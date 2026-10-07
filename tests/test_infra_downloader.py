"""Unit tests for infra.downloader.downloader. No real network/yt-dlp call:
YoutubeDL is monkeypatched with a fake context manager."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from infra.downloader import downloader as mod

EXPECTED_DURATION_SECONDS = 12.3


class FakeYoutubeDL:
    """Stands in for yt_dlp.YoutubeDL. `script` controls per-call behaviour."""

    instances: list["FakeYoutubeDL"] = []

    def __init__(self, opts: dict[str, Any]):
        self.opts = opts
        FakeYoutubeDL.instances.append(self)

    def __enter__(self) -> "FakeYoutubeDL":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def extract_info(self, url: str, download: bool = True) -> dict[str, Any]:
        return {
            "title": "a video",
            "duration": 12.3,
            "uploader": "someone",
            "webpage_url": url,
        }

    def prepare_filename(self, info: dict[str, Any]) -> str:
        return str(Path(self.opts["outtmpl"]).parent / "a video.mp4")


@pytest.fixture(autouse=True)
def _reset_instances():  # pyright: ignore[reportUnusedFunction] -- autouse pytest fixture, never called directly
    FakeYoutubeDL.instances.clear()
    yield
    FakeYoutubeDL.instances.clear()


def test_download_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mod, "YoutubeDL", FakeYoutubeDL)

    dl = mod.VideoDownloader(output_dir=tmp_path, browser="chrome")
    expected_file = tmp_path / "a video.mp4"
    expected_file.write_bytes(b"fake video bytes")

    result = dl.download("https://instagram.com/p/xyz/")

    assert result["ok"] is True
    assert result["filepath"] == str(expected_file)
    assert result["title"] == "a video"
    assert result["duration"] == EXPECTED_DURATION_SECONDS
    assert result["uploader"] == "someone"
    assert result["webpage_url"] == "https://instagram.com/p/xyz/"
    # First attempt uses browser cookies.
    assert "cookiesfrombrowser" in FakeYoutubeDL.instances[0].opts


def test_download_falls_back_without_cookies_on_cookie_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    attempts: list[bool] = []

    class FlakyYoutubeDL(FakeYoutubeDL):
        def extract_info(self, url: str, download: bool = True) -> dict[str, Any]:
            use_cookies = "cookiesfrombrowser" in self.opts or "cookiefile" in self.opts
            attempts.append(use_cookies)
            if use_cookies:
                raise RuntimeError("could not find browser keyring")
            return super().extract_info(url, download=download)

    monkeypatch.setattr(mod, "YoutubeDL", FlakyYoutubeDL)

    dl = mod.VideoDownloader(output_dir=tmp_path, browser="chrome")
    expected_file = tmp_path / "a video.mp4"
    expected_file.write_bytes(b"fake video bytes")

    result = dl.download("https://instagram.com/p/xyz/")

    assert result["ok"] is True
    assert attempts == [True, False]


def test_download_returns_error_on_non_cookie_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    class BoomingYoutubeDL(FakeYoutubeDL):
        def extract_info(self, url: str, download: bool = True) -> dict[str, Any]:
            raise RuntimeError("HTTP 404: video unavailable")

    monkeypatch.setattr(mod, "YoutubeDL", BoomingYoutubeDL)

    dl = mod.VideoDownloader(output_dir=tmp_path, browser="chrome")
    result = dl.download("https://instagram.com/p/xyz/")

    assert result["ok"] is False
    assert "404" in result["error"]


def test_download_returns_error_when_file_never_materializes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(mod, "YoutubeDL", FakeYoutubeDL)

    dl = mod.VideoDownloader(output_dir=tmp_path, browser="chrome")
    # Deliberately never writing the expected file to disk.
    result = dl.download("https://instagram.com/p/xyz/")

    assert result["ok"] is False
    assert "not found" in result["error"].lower()


def test_cookies_file_prefers_ig_cookies_file_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("IG_COOKIES_FILE", "/tmp/cookies.txt")
    monkeypatch.delenv("COOKIES_FILE", raising=False)
    dl = mod.VideoDownloader(output_dir=tmp_path)
    assert dl.cookies_file == "/tmp/cookies.txt"


def test_module_level_download_video_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(mod, "YoutubeDL", FakeYoutubeDL)
    (tmp_path / "a video.mp4").write_bytes(b"fake")

    result = mod.download_video("https://instagram.com/p/xyz/", output_dir=tmp_path)
    assert result["ok"] is True
