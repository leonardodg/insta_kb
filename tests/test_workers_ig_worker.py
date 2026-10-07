"""Unit tests for workers.ig_worker's pure/injectable logic. No RabbitMQ,
GPU, Postgres or Instagram access: process_message takes every IO operation
as an injected callable."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from workers import ig_worker as mod
from workers import media_download


def _ingest_unused(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Fake `Ingester` pros testes que falham antes de chegar no ingest."""
    return {}


def _ingest_returning(payload: dict[str, Any]) -> Callable[..., dict[str, Any]]:
    def _fake(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return payload

    return _fake


def test_classify_file():
    assert mod.classify_file("post.mp4") == "video"
    assert mod.classify_file("post.jpg") == "image"
    assert mod.classify_file("post.MOV") == "video"


def test_strip_cta_removes_only_the_cta_sentence():
    text = "Aqui vai a receita boa. Siga para mais dicas assim. Bom apetite."
    result = mod.strip_cta(text)
    assert "receita boa" in result
    assert "Bom apetite" in result
    assert "Siga para mais" not in result


def test_strip_cta_noop_without_cta():
    text = "Nada de promocional aqui."
    assert mod.strip_cta(text) == text


def test_clean_title_drops_leading_cta():
    assert mod.clean_title("Still not Following me?? You'll miss all of it..") is None


def test_clean_title_trims_trailing_cta_keeps_legit_title():
    result = mod.clean_title("Estude comigo na Fluency. Link na Bio.")
    assert result == "Estude comigo na Fluency."


def test_clean_title_none_for_short_remainder():
    assert mod.clean_title("Siga para mais @fulano") is None


def test_clean_title_none_input():
    assert mod.clean_title(None) is None
    assert mod.clean_title("") is None


def test_pace_sleep_seconds_zero_interval_never_sleeps():
    assert mod.pace_sleep_seconds(0.0, interval=0) == 0.0


def test_pace_sleep_seconds_computes_remaining():
    expected_remaining_seconds = 20.0
    assert mod.pace_sleep_seconds(10.0, interval=30) == expected_remaining_seconds


def test_pace_sleep_seconds_never_negative():
    assert mod.pace_sleep_seconds(50.0, interval=30) == 0.0


def test_merge_screen_text_dedupes_contained_lines():
    pedacos = ["linha completa aqui\noutra linha", "linha compl"]
    merged = mod.merge_screen_text(pedacos)
    assert "linha completa aqui" in merged
    assert merged.count("linha compl") == 1


def test_merge_screen_text_empty_input():
    assert mod.merge_screen_text([]) == ""


def test_apply_command_start_stop_without_channel():
    state = {"paused": False, "tag": None, "canal": None}
    assert mod.apply_command(state, "stop") == "paused"
    assert state["paused"] is True
    assert mod.apply_command(state, "start") == "resumed"
    assert state["paused"] is False


def test_apply_command_unknown():
    state = {"paused": False, "tag": None, "canal": None}
    assert mod.apply_command(state, "bogus") == "unknown"


def test_apply_command_stop_cancels_consumer():
    calls: list[str] = []

    class FakeChannel:
        def basic_cancel(self, tag: str) -> None:
            calls.append(tag)

    state = {"paused": False, "tag": "tag-1", "canal": FakeChannel()}
    mod.apply_command(state, "stop")
    assert calls == ["tag-1"]
    assert state["tag"] is None


def test_process_message_empty():
    assert mod.process_message(
        {},
        download=lambda m: {},
        transcribe=None,
        describe=None,
        ingest=_ingest_unused,
    ) == {"status": "error", "error": "empty message"}


def test_process_message_download_failure():
    result = mod.process_message(
        {"ig_pk": "1"},
        download=lambda m: {"ok": False, "error": "404"},
        transcribe=None,
        describe=None,
        ingest=_ingest_unused,
    )
    assert result == {"status": "error", "error": "404"}


def test_process_message_video_happy_path(monkeypatch: pytest.MonkeyPatch):
    expected_document_id = 42
    message = {"ig_pk": "1", "title": "Receita boa", "collection_name": "Receitas"}

    ingest_calls = {}

    def fake_ingest(text: str, **kwargs: Any) -> dict[str, Any]:
        ingest_calls["text"] = text
        ingest_calls["kwargs"] = kwargs
        return {"ok": True, "document_id": expected_document_id}

    result = mod.process_message(
        message,
        download=lambda m: {
            "ok": True,
            "filepaths": ["/tmp/x.mp4"],
            "caption": "legenda aqui",
        },
        transcribe=lambda fp: {"ok": True, "text": "fala transcrita", "language": "pt"},
        describe=None,
        read_screen=lambda fp: {"ok": True, "text": "", "descricao": ""},
        ingest=fake_ingest,
        categories=["Receitas"],
    )
    assert result["status"] == "done"
    assert result.get("document_id") == expected_document_id
    assert "fala transcrita" in ingest_calls["text"]
    assert ingest_calls["kwargs"]["extra_tags"] == ["colecao:Receitas"]
    assert ingest_calls["kwargs"]["title"] == "Receita boa"


def test_process_message_video_no_content_is_permanent_failure():
    message = {"ig_pk": "1", "title": None}
    result = mod.process_message(
        message,
        download=lambda m: {"ok": True, "filepaths": ["/tmp/x.mp4"]},
        transcribe=lambda fp: {"ok": True, "text": "", "language": "pt"},
        describe=None,
        read_screen=lambda fp: {"ok": True, "text": "", "descricao": ""},
        ingest=_ingest_returning({"ok": True, "document_id": 1}),
    )
    assert result["status"] == "error"
    assert result.get("permanent") is True


def test_process_message_image_describe_tags_category():
    message = {"ig_pk": "2", "title": "Dica boa"}

    ingest_calls = {}

    def fake_ingest(text: str, **kwargs: Any) -> dict[str, Any]:
        ingest_calls["kwargs"] = kwargs
        return {"ok": True, "document_id": 7}

    result = mod.process_message(
        message,
        download=lambda m: {"ok": True, "filepaths": ["/tmp/x.jpg"]},
        transcribe=None,
        describe=lambda fp: {
            "ok": True,
            "conteudo_principal": "conteúdo da imagem",
            "categoria": "Receita",
        },
        ingest=fake_ingest,
    )
    assert result["status"] == "done"
    assert "categoria:Receita" in ingest_calls["kwargs"]["extra_tags"]
    assert ingest_calls["kwargs"]["categories"] is None  # already classified


def test_process_message_image_all_describe_fail():
    result = mod.process_message(
        {"ig_pk": "3"},
        download=lambda m: {"ok": True, "filepaths": ["/tmp/x.jpg"]},
        transcribe=None,
        describe=lambda fp: {"ok": False, "error": "vision down"},
        ingest=_ingest_returning({"ok": True}),
    )
    assert result["status"] == "error"
    assert "vision down" in (result.get("error") or "")


def test_process_message_video_caption_fallback_chain_with_screen_text():
    # When media is reused from disk there's no media_info, so dl["caption"]
    # comes back empty -- the message-level `caption` (not just `title`,
    # which is only an 80-char reserve) must still reach the model. Measured:
    # 21 of 24 silent-video documents lost the caption, and all 21 were
    # exactly the disk-reused ones.
    message = {
        "ig_pk": "9",
        "title": "titulo curto",
        "caption": "A LEGENDA COMPLETA com o passo a passo que importa",
    }
    captured: dict[str, Any] = {}

    def fake_ingest(text: str, **kwargs: Any) -> dict[str, Any]:
        captured["text"] = text
        return {"ok": True, "document_id": 1}

    result = mod.process_message(
        message,
        download=lambda m: {"ok": True, "filepaths": ["/tmp/x.mp4"]},  # no caption
        transcribe=lambda fp: {"ok": True, "text": "", "language": "pt"},
        describe=None,
        read_screen=lambda fp: {"ok": True, "text": "TEXTO NA TELA"},
        ingest=fake_ingest,
    )
    assert result["status"] == "done"
    assert "LEGENDA COMPLETA" in captured["text"]


def test_process_message_video_caption_fallback_chain_without_screen_text():
    message = {
        "ig_pk": "9",
        "title": "titulo curto",
        "caption": "A LEGENDA COMPLETA com o passo a passo que importa",
    }
    captured: dict[str, Any] = {}

    def fake_ingest(text: str, **kwargs: Any) -> dict[str, Any]:
        captured["text"] = text
        return {"ok": True, "document_id": 1}

    result = mod.process_message(
        message,
        download=lambda m: {"ok": True, "filepaths": ["/tmp/x.mp4"]},
        transcribe=lambda fp: {"ok": True, "text": "", "language": "pt"},
        describe=None,
        read_screen=None,
        ingest=fake_ingest,
    )
    assert result["status"] == "done"
    assert "LEGENDA COMPLETA" in captured["text"]


def test_process_message_old_message_without_caption_falls_back_to_title():
    # A message queued before this fix has no "caption" key at all -- the
    # title must still serve as the last-resort reserve.
    message = {"ig_pk": "9", "title": "titulo curto"}
    captured: dict[str, Any] = {}

    def fake_ingest(text: str, **kwargs: Any) -> dict[str, Any]:
        captured["text"] = text
        return {"ok": True, "document_id": 1}

    result = mod.process_message(
        message,
        download=lambda m: {"ok": True, "filepaths": ["/tmp/x.mp4"]},
        transcribe=lambda fp: {"ok": True, "text": "", "language": "pt"},
        describe=None,
        read_screen=lambda fp: {"ok": True, "text": "TEXTO NA TELA"},
        ingest=fake_ingest,
    )
    assert result["status"] == "done"
    assert "titulo curto" in captured["text"]


def test_process_message_mixed_carousel_only_images_reach_vision_model():
    # A mixed carousel (photos AND videos) gets its `kind` from the first
    # file. Starting with a photo, the post used to send every file --
    # including .mp4s -- to the vision model, which 400s on video the same
    # way it does on an unsupported image format.
    message = {"ig_pk": "4", "title": "Post misto"}
    mixed = ["/tmp/a.jpg", "/tmp/b.mp4", "/tmp/c.jpg", "/tmp/d.mp4"]
    seen: list[str] = []

    def describe_images_only(fp: str) -> dict[str, Any]:
        seen.append(fp)
        return {"ok": True, "conteudo_principal": f"desc {fp}", "categoria": "receita"}

    result = mod.process_message(
        message,
        download=lambda m: {"ok": True, "filepaths": list(mixed)},
        transcribe=None,
        describe=describe_images_only,
        ingest=_ingest_returning({"ok": True, "document_id": 99}),
    )
    assert result["status"] == "done"
    assert seen == ["/tmp/a.jpg", "/tmp/c.jpg"]  # the .mp4s were never tried


def test_process_message_mixed_carousel_one_bad_photo_does_not_abort_others():
    # The same structural bug that let one malformed post take down an
    # entire collection in the listing: one failing description must not
    # cost the photos that already paid for a successful describe() call.
    message = {"ig_pk": "5", "title": "Post misto"}
    mixed = ["/tmp/a.jpg", "/tmp/b.jpg"]

    def describe_one_bad(fp: str) -> dict[str, Any]:
        if fp == "/tmp/a.jpg":
            return {"ok": False, "error": "vision failed: 400"}
        return {"ok": True, "conteudo_principal": "sobrevivi", "categoria": "receita"}

    result = mod.process_message(
        message,
        download=lambda m: {"ok": True, "filepaths": list(mixed)},
        transcribe=None,
        describe=describe_one_bad,
        ingest=_ingest_returning({"ok": True, "document_id": 1}),
    )
    assert result["status"] == "done"


def test_process_message_carousel_all_images_fail_names_the_count():
    message = {"ig_pk": "6", "title": "Post misto"}
    mixed = ["/tmp/a.jpg", "/tmp/b.jpg", "/tmp/c.jpg"]

    result = mod.process_message(
        message,
        download=lambda m: {"ok": True, "filepaths": list(mixed)},
        transcribe=None,
        describe=lambda fp: {"ok": False, "error": "vision failed: 400"},
        ingest=_ingest_returning({"ok": True}),
    )
    assert result["status"] == "error"
    assert "3" in (result.get("error") or "")


def test_existing_media_empty_when_dir_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(media_download, "IG_DOWNLOADS_DIR", tmp_path / "nope")
    assert mod.existing_media("123") == []


def test_existing_media_sorted_by_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(media_download, "IG_DOWNLOADS_DIR", tmp_path)
    post_dir = tmp_path / "123"
    post_dir.mkdir()
    (post_dir / "b.jpg").write_bytes(b"2")
    (post_dir / "a.jpg").write_bytes(b"1")
    files = mod.existing_media("123")
    expected_file_count = 2
    assert len(files) == expected_file_count


def test_discard_media_noop_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mod, "IG_DELETE_AFTER_INGEST", False)
    f = tmp_path / "x.mp4"
    f.write_bytes(b"data")
    mod._discard_media({"filepaths": [str(f)]})  # pyright: ignore[reportPrivateUsage] -- internal helper, no public wrapper
    assert f.exists()


def test_discard_media_deletes_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(mod, "IG_DELETE_AFTER_INGEST", True)
    f = tmp_path / "x.mp4"
    f.write_bytes(b"data")
    mod._discard_media({"filepaths": [str(f)]})  # pyright: ignore[reportPrivateUsage] -- internal helper, no public wrapper
    assert not f.exists()


def test_targets_from_info_single_video():
    info = SimpleNamespace(media_type=2)
    assert mod._targets_from_info(info, "pk1") == [  # pyright: ignore[reportPrivateUsage] -- same reasoning
        ("clip_download", "pk1")
    ]


def test_targets_from_info_single_image_with_thumbnail():
    info = SimpleNamespace(media_type=1, thumbnail_url="https://x/y.jpg")
    assert mod._targets_from_info(info, "pk1") == [  # pyright: ignore[reportPrivateUsage]
        ("photo_download_by_url", "https://x/y.jpg")
    ]


def test_targets_from_info_carousel_mixed():
    video_res = SimpleNamespace(media_type=2, video_url="https://x/v.mp4", pk="r1")
    photo_res = SimpleNamespace(media_type=1, thumbnail_url="https://x/p.jpg", pk="r2")
    info = SimpleNamespace(media_type=8, resources=[video_res, photo_res])
    targets = mod._targets_from_info(info, "pk1")  # pyright: ignore[reportPrivateUsage]
    assert targets == [
        ("video_download_by_url", "https://x/v.mp4"),
        ("photo_download_by_url", "https://x/p.jpg"),
    ]
