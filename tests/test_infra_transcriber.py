"""Unit tests for infra.transcriber.transcriber. No real GPU/Whisper model is
loaded: AudioTranscriber._get_model and tem_faixa_de_audio are monkeypatched."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from infra.transcriber import transcriber as mod


def _sem_audio(_path: str | Path) -> bool:
    return False


def _tem_audio(_path: str | Path) -> bool:
    return True


def test_transcribe_missing_file_returns_error(tmp_path: Path):
    at = mod.AudioTranscriber(model_size="small", device="cpu")
    result = at.transcribe(tmp_path / "does-not-exist.mp4")
    assert result["ok"] is False
    assert "File not found" in result["error"]


def test_transcribe_without_audio_track_returns_empty_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"not a real video, just needs to exist")
    monkeypatch.setattr(mod, "tem_faixa_de_audio", _sem_audio)

    at = mod.AudioTranscriber(model_size="small", device="cpu")
    result = at.transcribe(video)
    assert result["ok"] is True
    assert result["text"] == ""
    assert result["sem_audio"] is True


def test_transcribe_happy_path_builds_text_with_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")
    monkeypatch.setattr(mod, "tem_faixa_de_audio", _tem_audio)

    seg1 = SimpleNamespace(start=0.0, end=1.5, text=" olá ")
    seg2 = SimpleNamespace(start=1.5, end=3.0, text=" mundo ")
    info = SimpleNamespace(language="pt", language_probability=0.99, duration=3.0)

    class FakeModel:
        def transcribe(self, path: str | Path, **kwargs: Any):
            return [seg1, seg2], info

    expected_duration = 3.0
    at = mod.AudioTranscriber(model_size="small", device="cpu")
    monkeypatch.setattr(at, "_get_model", FakeModel)

    result = at.transcribe(video)
    assert result["ok"] is True
    assert "[0.00s - 1.50s] olá" in result["text"]
    assert "[1.50s - 3.00s] mundo" in result["text"]
    assert result["language"] == "pt"
    assert result["duration"] == expected_duration


def test_transcribe_exception_returns_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")
    monkeypatch.setattr(mod, "tem_faixa_de_audio", _tem_audio)

    at = mod.AudioTranscriber(model_size="small", device="cpu")

    def boom():
        raise RuntimeError("model broke")

    monkeypatch.setattr(at, "_get_model", boom)
    result = at.transcribe(video)
    assert result["ok"] is False
    assert "Transcription failed" in result["error"]


def test_free_drops_cache_entry():
    at = mod.AudioTranscriber(model_size="small", device="cpu")
    cache_key = "small-cpu-float16"
    mod.AudioTranscriber._model_cache[cache_key] = object()  # pyright: ignore[reportPrivateUsage] -- the class-level cache dict is the thing under test
    at.free()
    assert cache_key not in mod.AudioTranscriber._model_cache  # pyright: ignore[reportPrivateUsage]


def test_tem_faixa_de_audio_returns_none_on_unreadable_file(tmp_path: Path):
    bogus = tmp_path / "bogus.mp4"
    bogus.write_bytes(b"not a real container")
    assert mod.tem_faixa_de_audio(bogus) is None
