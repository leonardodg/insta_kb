"""Unit tests for infra.llm.client. No real Ollama server required: HTTP
calls are mocked via monkeypatching httpx.post."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

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
