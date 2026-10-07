"""Unit tests for infra.llm.client. No real Ollama server required: HTTP
calls are mocked via monkeypatching httpx.post."""

from __future__ import annotations

import base64
import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from infra.llm import client


def _fake_response(json_body: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: json_body,
    )


def test_parse_llm_json_plain():
    assert client.parse_llm_json('{"a": 1}') == {"a": 1}


def test_parse_llm_json_strips_markdown_fence():
    raw = '```json\n{"a": 1}\n```'
    assert client.parse_llm_json(raw) == {"a": 1}


def test_cortar_material_no_cut_when_fits():
    text, perdidos = client.cortar_material("abc", 100)
    assert text == "abc"
    assert perdidos == 0


def test_cortar_material_cuts_at_word_boundary():
    text = "uma frase razoavelmente longa para cortar no meio"
    cortado, perdidos = client.cortar_material(text, 20)
    assert perdidos > 0
    assert not cortado.endswith(" ")
    assert text.startswith(cortado)


def test_cortar_material_without_whitespace_cuts_at_limit_not_zero():
    # Base64/URL-like text with no spaces must not collapse to an empty
    # string when rfind(" ") can't find a boundary.
    text = "x" * 5000
    limite = 1000
    cortado, _perdidos = client.cortar_material(text, limite)
    assert len(cortado) == limite


def test_orcamento_de_material_is_zero_when_reply_budget_exceeds_ctx():
    # The budget must subtract the mold and the reply before handing out
    # characters, or the Ollama cutoff just moves somewhere else.
    orcamento = client.orcamento_de_material(num_ctx=2048, num_predict=3072, molde="x")
    assert orcamento == 0


def test_num_ctx_declared_in_summary_payload():
    # Private on purpose (reportPrivateUsage, suppressed below): this is the
    # exact helper every public call builds its payload with, and there is
    # no public seam that exposes the built dict otherwise.
    payload = client._ollama_payload(  # pyright: ignore[reportPrivateUsage]
        "oi", "m", force_json=True
    )
    assert payload["options"]["num_ctx"] == client.LLM_NUM_CTX


def test_orcamento_de_material_is_positive_for_reasonable_ctx():
    orcamento = client.orcamento_de_material(
        num_ctx=8192, num_predict=3072, molde="x" * 300
    )
    assert orcamento > 0


def test_coerce_categoria_accent_and_case_insensitive():
    cats = ["Inglês", "Receita"]
    assert client.coerce_categoria("ingles", cats) == "Inglês"
    assert client.coerce_categoria("RECEITA", cats) == "Receita"


def test_coerce_categoria_unknown_falls_back_to_outros():
    cats = ["Inglês", "Receita"]
    assert client.coerce_categoria("Saúde", cats) == "outros"


def test_coerce_categoria_no_vocabulary_passthrough():
    assert client.coerce_categoria("qualquer coisa", None) == "qualquer coisa"
    assert client.coerce_categoria(None, None) == "outros"


def test_ancorar_codigo_removes_unanchored_fenced_block():
    fonte = "o comando certo é pip install algo"
    tutorial = "Rode:\n```\npip install outracoisa\n```\nFeito."
    novo, removidos = client.ancorar_codigo(tutorial, fonte)
    assert removidos == 1
    assert client.NAO_VERIFICADO in novo
    assert "pip install outracoisa" in novo  # content kept, just unmarked


def test_ancorar_codigo_keeps_anchored_inline():
    fonte = "use o comando `ls -la` no terminal"
    tutorial = "Rode `ls -la` para ver tudo."
    novo, removidos = client.ancorar_codigo(tutorial, fonte)
    assert removidos == 0
    assert "`ls -la`" in novo


def test_ancorar_codigo_strips_unanchored_inline():
    fonte = "não existe nada parecido aqui"
    tutorial = "Use `comando_inventado` agora."
    novo, removidos = client.ancorar_codigo(tutorial, fonte)
    assert removidos == 1
    assert "`comando_inventado`" not in novo
    assert "comando_inventado" in novo  # words kept, backticks dropped


def test_parse_vision_reply_valid_json():
    raw = '{"tipo": "receita", "categoria": "Receita", "conteudo_principal": "bolo"}'
    parsed = client.parse_vision_reply(raw)
    assert parsed["conteudo_principal"] == "bolo"
    assert parsed["tipo"] == "receita"


def test_parse_vision_reply_non_json_becomes_conteudo_principal():
    parsed = client.parse_vision_reply("texto livre sem json")
    assert parsed["conteudo_principal"] == "texto livre sem json"
    assert parsed["tipo"] == "outros"


def test_embed_calls_ollama_and_returns_vector(monkeypatch: pytest.MonkeyPatch):
    captured = {}

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> SimpleNamespace:
        captured["url"] = url
        captured["json"] = json
        return _fake_response({"embedding": [0.1, 0.2, 0.3]})

    monkeypatch.setattr(client.httpx, "post", fake_post)
    vec = client.embed("hello", model="mxbai-embed-large")
    assert vec == [0.1, 0.2, 0.3]
    assert captured["json"]["model"] == "mxbai-embed-large"
    assert "/api/embeddings" in captured["url"]


def test_chat_ollama_provider(monkeypatch: pytest.MonkeyPatch):
    def fake_post(url: str, json: dict[str, Any], timeout: float) -> SimpleNamespace:
        assert "/api/chat" in url
        return _fake_response({"message": {"content": "resposta"}})

    monkeypatch.setattr(client.httpx, "post", fake_post)
    assert client.chat("pergunta", provider="ollama", model="lfm2:24b") == "resposta"


def test_chat_unknown_provider_raises():
    with pytest.raises(RuntimeError):
        client.chat("x", provider="bogus")


def test_generate_structured_happy_path(monkeypatch: pytest.MonkeyPatch):
    body = (
        '{"resumo": "um resumo", "tutorial": "um tutorial", '
        '"objetivos": ["a"], "tags": ["t1"]}'
    )

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> SimpleNamespace:
        return _fake_response({"message": {"content": body}})

    monkeypatch.setattr(client.httpx, "post", fake_post)
    result = client.generate_structured("conteúdo de teste")
    assert result["ok"] is True
    assert result["resumo"] == "um resumo"
    assert result["tags"] == ["t1"]


def test_generate_structured_retries_then_fails_on_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
):
    expected_attempts = 2  # documented retry policy: two attempts, no more
    calls = {"n": 0}

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> SimpleNamespace:
        calls["n"] += 1
        return _fake_response({"message": {"content": "not json"}})

    monkeypatch.setattr(client.httpx, "post", fake_post)
    result = client.generate_structured("conteúdo")
    assert result["ok"] is False
    assert calls["n"] == expected_attempts


def test_generate_structured_retries_on_valid_json_wrong_schema(
    monkeypatch: pytest.MonkeyPatch,
):
    # The model can return perfectly valid JSON that mirrors the post's own
    # content instead of the requested {resumo, tutorial, ...} shape --
    # observed on screenshot posts where the input already looks structured
    # and the model anchors on it. This is the same stochastic failure as
    # invalid JSON, so it gets the same one retry, not an immediate failure.
    wrong_schema = (
        '{"transacao": {"status": "Pendente", "prazo_estimado": "15-60 min"}}'
    )
    good = '{"resumo": "r", "tutorial": "t", "objetivos": ["o"], "tags": ["a"]}'
    bodies = [wrong_schema, good]

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> SimpleNamespace:
        return _fake_response({"message": {"content": bodies.pop(0)}})

    monkeypatch.setattr(client.httpx, "post", fake_post)
    result = client.generate_structured("conteúdo")
    assert result["ok"] is True
    assert result["resumo"] == "r"


def test_generate_structured_fails_after_two_wrong_schema_attempts(
    monkeypatch: pytest.MonkeyPatch,
):
    expected_attempts = 2
    wrong_schema = '{"transacao": {"status": "Pendente"}}'
    calls = {"n": 0}

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> SimpleNamespace:
        calls["n"] += 1
        return _fake_response({"message": {"content": wrong_schema}})

    monkeypatch.setattr(client.httpx, "post", fake_post)
    result = client.generate_structured("conteúdo")
    assert result["ok"] is False
    assert "resumo" in result["error"]
    assert calls["n"] == expected_attempts


def test_generate_structured_happy_path_costs_one_attempt(
    monkeypatch: pytest.MonkeyPatch,
):
    # The retry must not tax the happy path: a good response on the first
    # try costs exactly one generation.
    good = '{"resumo": "r", "tutorial": "t", "objetivos": ["o"], "tags": ["a"]}'
    calls = {"n": 0}

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> SimpleNamespace:
        calls["n"] += 1
        return _fake_response({"message": {"content": good}})

    monkeypatch.setattr(client.httpx, "post", fake_post)
    result = client.generate_structured("conteúdo")
    assert result["ok"] is True
    assert calls["n"] == 1


def test_generate_structured_truncates_oversized_material(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    # Better to cut the material ourselves, with a log line, than let Ollama
    # discard from the FRONT (the mold/instructions) and reply confidently
    # with whatever fit.
    good = '{"resumo": "r", "tutorial": "t", "objetivos": ["o"], "tags": ["a"]}'
    seen_prompts: list[str] = []

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> SimpleNamespace:
        seen_prompts.append(json["messages"][-1]["content"])
        return _fake_response({"message": {"content": good}})

    monkeypatch.setattr(client, "LLM_NUM_CTX", 4096)
    monkeypatch.setattr(client.httpx, "post", fake_post)

    oversized = "palavra " * 50_000  # far more than num_ctx=4096 allows
    with caplog.at_level("WARNING"):
        result = client.generate_structured(oversized)

    assert result["ok"] is True
    assert len(seen_prompts[0]) < len(oversized)
    assert any("cortado" in rec.message for rec in caplog.records)


def test_imagem_para_b64_converts_webp_to_jpeg(tmp_path: Path):
    # Ollama's image decoder 400s on WebP, which is a common format Instagram
    # serves images in -- measured: same image, webp -> HTTP 400, jpeg -> 200.
    src = Image.new("RGB", (64, 48), (200, 30, 90))
    webp_path = tmp_path / "post.webp"
    src.save(webp_path, format="WEBP")

    b64 = client.imagem_para_b64(str(webp_path))
    converted = Image.open(io.BytesIO(base64.b64decode(b64)))

    assert converted.format == "JPEG"
    assert converted.size == (64, 48)


def test_imagem_para_b64_passes_jpeg_through_unchanged(tmp_path: Path):
    src = Image.new("RGB", (64, 48), (200, 30, 90))
    jpeg_path = tmp_path / "post.jpg"
    src.save(jpeg_path, format="JPEG", quality=90)

    b64 = client.imagem_para_b64(str(jpeg_path))
    assert base64.b64decode(b64) == jpeg_path.read_bytes()


def test_imagem_para_b64_handles_alpha_channel(tmp_path: Path):
    # save(format="JPEG") raises OSError on RGBA/P without convert("RGB") first.
    png_path = tmp_path / "transparent.png"
    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(png_path, format="PNG")

    client.imagem_para_b64(str(png_path))  # must not raise


def test_imagem_para_b64_decides_by_content_not_extension(tmp_path: Path):
    # A lying extension (.heic whose bytes are actually JPEG) must not force
    # a re-encode -- PIL detects the real format from the bytes.
    src = Image.new("RGB", (64, 48), (10, 20, 30))
    jpeg_path = tmp_path / "real.jpg"
    src.save(jpeg_path, format="JPEG", quality=90)
    lying_path = tmp_path / "lying.heic"
    lying_path.write_bytes(jpeg_path.read_bytes())

    b64 = client.imagem_para_b64(str(lying_path))
    assert base64.b64decode(b64) == jpeg_path.read_bytes()


def test_imagem_para_b64_unreadable_file_passes_through(tmp_path: Path):
    # Better to send the original bytes and let Ollama decide than to invent
    # a new error here -- if the format already worked, nothing changes.
    garbage_path = tmp_path / "garbage.jpg"
    garbage_path.write_bytes(b"not an image")

    b64 = client.imagem_para_b64(str(garbage_path))
    assert base64.b64decode(b64) == b"not an image"
