"""Video downloader using yt-dlp with browser cookie support.

Migrated verbatim (behaviour unchanged) from minimax-video-factory's
`minimax_mcp/downloader.py`. This closes the gap left by the earlier
migration: `core.knowledge.knowledge.ingest_video`/`ingest_audio` and
`workers.media_download.default_download` lazily import
`infra.downloader.VideoDownloader`, which did not exist until now.

No config reads here (output_dir/browser/format_spec are constructor
params, as in the original) except the two env vars read directly below,
reproduced unchanged rather than moved to `core.settings.config.settings`
-- see the module docstring in the original source; IG_COOKIES_FILE/
COOKIES_FILE are host-path overrides set ad hoc per environment, not part
of the Settings schema the rest of this project uses.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL

logger = logging.getLogger(__name__)


class VideoDownloader:
    """Downloads videos from Instagram, YouTube, and other platforms using yt-dlp."""

    def __init__(
        self,
        output_dir: str | Path,
        browser: str = "chrome",
        format_spec: str = "best[ext=mp4]/best",
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.browser = browser
        self.format_spec = format_spec

    @property
    def cookies_file(self) -> str | None:
        """Path to a Netscape cookies.txt, from IG_COOKIES_FILE or COOKIES_FILE.

        Preferred over browser cookies when set: inside the Docker container
        there is no Chrome profile, and Chrome 127+ app-bound encryption makes
        `cookiesfrombrowser` unable to read the sessionid anyway. A cookies.txt
        exported from the logged-in browser is the reliable source for private
        Instagram content.
        """
        return (
            os.environ.get("IG_COOKIES_FILE") or os.environ.get("COOKIES_FILE") or None
        )

    def _build_ydl_opts(self, use_cookies: bool = True) -> dict[str, Any]:
        """Build yt-dlp options, optionally with cookie support."""
        opts: dict[str, Any] = {
            "outtmpl": str(self.output_dir / "%(title)s.%(ext)s"),
            "format": self.format_spec,
            "ignoreerrors": True,
            "no_warnings": False,
            "quiet": False,
            "noprogress": False,
            # Allow fallback to other formats if preferred not available
            "format_sort": ["ext:mp4", "res:1080", "res:720", "res:480"],
        }
        if use_cookies:
            cf = self.cookies_file
            if cf:
                opts["cookiefile"] = cf
            else:
                opts["cookiesfrombrowser"] = (self.browser,)
        return opts

    def download(self, url: str) -> dict[str, Any]:
        """
        Download a video from the given URL.

        Tries browser cookies (or cookies.txt if IG_COOKIES_FILE is set) first,
        then falls back to an anonymous download when the cookie source is
        unavailable (e.g. container without the browser profile, browser
        running/locked, or no keyring to decrypt the cookies).

        Args:
            url: Video URL (Instagram Reel, YouTube, etc.)

        Returns:
            Dict with success status, file path, and metadata.
        """
        for use_cookies in (True, False):
            mode = f"browser cookies ({self.browser})" if use_cookies else "no cookies"
            logger.info("Downloading video from %s using %s", url, mode)

            try:
                # yt-dlp types YoutubeDL's constructor param as its own strict
                # `_Params` TypedDict; `_build_ydl_opts` builds a plain
                # `dict[str, Any]` (it mixes in `cookiefile`/`cookiesfrombrowser`
                # dynamically), so pyright sees a structural mismatch even
                # though every key here is one yt-dlp actually accepts.
                opts = self._build_ydl_opts(use_cookies=use_cookies)
                with YoutubeDL(opts) as ydl:  # pyright: ignore[reportArgumentType]
                    info = ydl.extract_info(url, download=True)
                    if not info:
                        return {"ok": False, "error": "Failed to extract video info"}

                    filepath = ydl.prepare_filename(info)
                    if not os.path.exists(filepath):
                        # Try to find the actual file (yt-dlp sometimes changes
                        # extension)
                        base = os.path.splitext(filepath)[0]
                        for ext in [".mp4", ".mkv", ".webm", ".mov"]:
                            cand = base + ext
                            if os.path.exists(cand):
                                filepath = cand
                                break

                    if not os.path.exists(filepath):
                        return {
                            "ok": False,
                            "error": f"Downloaded file not found: {filepath}",
                        }

                    return {
                        "ok": True,
                        "filepath": filepath,
                        "title": info.get("title", "unknown"),
                        "duration": info.get("duration"),
                        "uploader": info.get("uploader"),
                        "webpage_url": info.get("webpage_url", url),
                    }

            except Exception as e:
                msg = str(e)
                logger.exception("Download attempt failed for %s (%s)", url, mode)
                if use_cookies and self._is_cookie_error(msg):
                    logger.warning(
                        "Cookie loading failed (%s); retrying without cookies", msg
                    )
                    continue
                return {"ok": False, "error": f"Download failed: {msg}"}

        return {"ok": False, "error": "Download failed: no usable download path"}

    @staticmethod
    def _is_cookie_error(msg: str) -> bool:
        """Heuristic: does this error mean the browser cookie source was the problem?"""
        lowered = msg.lower()
        return any(
            kw in lowered
            for kw in (
                "cookie",
                "database",
                "could not find browser",
                "keyring",
                "dbus",
            )
        )


def download_video(
    url: str,
    output_dir: str | Path,
    browser: str = "chrome",
) -> dict[str, Any]:
    """Convenience function for single video download."""
    downloader = VideoDownloader(output_dir=output_dir, browser=browser)
    return downloader.download(url)
