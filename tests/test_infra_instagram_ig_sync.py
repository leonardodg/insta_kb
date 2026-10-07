"""Unit tests for infra.instagram.ig_sync's pure business logic (no
instagrapi/network/Postgres required)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from infra.instagram import ig_sync


def _media(
    pk: int | None,
    media_type: int = 2,
    caption_text: str = "",
    username: str = "user",
    code: str | None = None,
):
    user = SimpleNamespace(username=username)
    return SimpleNamespace(
        pk=pk, media_type=media_type, caption_text=caption_text, user=user, code=code
    )


def test_to_messages_maps_media_to_queue_message():
    items = [
        {
            "media": _media(1, caption_text="Receita boa\nresto"),
            "collection_name": "Receitas",
        }
    ]
    messages = ig_sync.to_messages(items)
    assert len(messages) == 1
    msg = messages[0]
    assert msg["ig_pk"] == "1"
    assert msg["media_type"] == "video"
    assert msg["title"] == "Receita boa"
    assert msg["caption"] == "Receita boa\nresto"
    assert msg["owner_username"] == "user"
    assert msg["collection_name"] == "Receitas"
    assert msg["status"] == "queued"


def test_to_messages_skips_missing_pk_or_unknown_type():
    items = [
        {"media": _media(None), "collection_name": None},
        {"media": _media(2, media_type=999), "collection_name": None},
    ]
    assert ig_sync.to_messages(items) == []


def test_to_messages_no_caption_gives_none_title():
    items = [{"media": _media(5, caption_text=""), "collection_name": None}]
    msg = ig_sync.to_messages(items)[0]
    assert msg["title"] is None
    assert msg["caption"] is None


def test_dedupe_by_pk_prefers_message_with_collection_name():
    messages = [
        {"ig_pk": "1", "collection_name": None},
        {"ig_pk": "1", "collection_name": "Dev"},
    ]
    result = ig_sync.dedupe_by_pk(messages)
    assert len(result) == 1
    assert result[0]["collection_name"] == "Dev"


def test_dedupe_by_pk_preserves_order():
    messages = [{"ig_pk": "1"}, {"ig_pk": "2"}, {"ig_pk": "1"}]
    result = ig_sync.dedupe_by_pk(messages)
    assert [m["ig_pk"] for m in result] == ["1", "2"]


def test_split_new_separates_existing_from_new():
    messages = [
        {"ig_pk": "1", "collection_name": None},
        {"ig_pk": "2", "collection_name": None},
    ]
    new, skipped = ig_sync.split_new(messages, existing_pks={"1"})
    assert [m["ig_pk"] for m in new] == ["2"]
    assert skipped == 1


def test_split_new_dedupes_within_batch_before_checking_existing():
    messages = [
        {"ig_pk": "1", "collection_name": None},
        {"ig_pk": "1", "collection_name": "Dev"},
    ]
    new, skipped = ig_sync.split_new(messages, existing_pks=set())
    assert len(new) == 1
    assert new[0]["collection_name"] == "Dev"
    assert skipped == 0


def test_post_url_with_code():
    assert ig_sync.post_url(123, code="ABC123") == "https://www.instagram.com/p/ABC123/"


def test_post_url_without_code_falls_back_to_pk_on_import_error():
    # instagrapi.utils.InstagramIdCodec may not be importable/usable in a
    # pure unit test context for arbitrary pk values; the function must
    # never raise either way.
    url = ig_sync.post_url(123)
    assert url.startswith("https://www.instagram.com/p/")


def test_list_categories_none_client_returns_fallback():
    assert ig_sync.list_categories(None) == ig_sync.FALLBACK_CATEGORIES


def test_list_categories_dedupes_and_excludes_catch_all():
    class FakeClient:
        def collections(self):
            return [
                SimpleNamespace(name="All posts"),
                SimpleNamespace(name="Receitas"),
                SimpleNamespace(name="receitas"),  # dup, case-insensitive
                SimpleNamespace(name="Dev"),
            ]

    cats = ig_sync.list_categories(FakeClient())
    assert cats == ["Receitas", "Dev"]


def test_list_categories_exception_falls_back():
    class BrokenClient:
        def collections(self):
            raise RuntimeError("API down")

    assert ig_sync.list_categories(BrokenClient()) == ig_sync.FALLBACK_CATEGORIES


def test_parse_items_tolerant_skips_bad_item_keeps_others():
    def extract(raw: dict[str, Any]) -> Any:
        if raw.get("bad"):
            raise ValueError("broken item")
        return raw["pk"]

    discarded: list[dict[str, Any]] = []

    def _on_discard(raw: dict[str, Any], _exc: Exception) -> None:
        discarded.append(raw)

    items = ig_sync.parse_items_tolerant(
        [{"pk": 1}, {"bad": True, "pk": 2}, {"pk": 3}],
        extract,
        on_discard=_on_discard,
    )
    assert items == [1, 3]
    assert discarded == [{"bad": True, "pk": 2}]


def test_registrar_descarte_writes_jsonl(tmp_path: Path):
    destino = tmp_path / "descartados.jsonl"
    ig_sync.registrar_descarte({"pk": 7}, ValueError("boom"), caminho=str(destino))
    assert destino.exists()
    content = destino.read_text(encoding="utf-8")
    assert '"ig_pk": "7"' in content
    assert "boom" in content


def test_sync_saved_posts_publishes_and_counts():
    total_media_items = 2

    class FakeClient:
        descartados: list[Any] = []

        def saved_posts(self) -> list[Any]:
            return [_media(1), _media(2)]

    published: list[dict[str, Any]] = []
    result = ig_sync.sync_saved_posts(
        FakeClient(),
        existing_pks={"2"},
        publish_fn=published.append,
    )
    assert result["ok"] is True
    assert result["published"] == 1
    assert result["skipped_existing"] == 1
    assert result["total"] == total_media_items
    assert [m["ig_pk"] for m in published] == ["1"]
