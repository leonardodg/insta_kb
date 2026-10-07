"""Download da mídia do Instagram: instagrapi (autenticado), disco e yt-dlp.

Extraído de `workers.ig_worker` (audit F1/SRP, responsabilidades 2 e do
grupo instagrapi+yt-dlp+disco). Dono de: classificar arquivo por extensão,
mapear `media_info` → pares de download, cache em disco por `ig_pk` e o
`default_download` (único lugar que fala com o Instagram/yt-dlp).

O fallback yt-dlp importa `infra.downloader` de propósito e de forma lazy
(PLC0415): só é alcançado em post público sem IG_SESSIONID — o caminho
comum autenticado nunca puxa yt-dlp.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from core.settings.config import settings
from infra.instagram import ig_sync

logger = logging.getLogger(__name__)

IG_DOWNLOADS_DIR = Path(settings.IG_DOWNLOADS_DIR)

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov"}


def classify_file(filepath: str) -> str:
    return "video" if Path(filepath).suffix.lower() in VIDEO_EXTS else "image"


def download_targets(  # pyright: ignore[reportUnusedFunction]
    client: Any, pk: str
) -> list[tuple[str, str]]:
    """All (method, argument) download pairs for a post.

    Carousels (media_type 8) have no clip of their own, so one pair comes
    back per resource and the whole album is captured. Single media returns
    the pk.

    NOTE: unused in this module (as in the original minimax-video-factory
    source -- `default_download` calls `targets_from_info` directly with an
    already-fetched `media_info`). Kept for parity with the source; not a
    migration decision.
    """
    return targets_from_info(client.media_info(pk), pk)


# instagrapi MediaType values -- same vocabulary as infra.instagram.ig_sync's
# MEDIA_TYPES, repeated here (not imported) because this module only needs
# the raw ints to branch on, not the string labels.
_IG_MEDIA_TYPE_IMAGE = 1
_IG_MEDIA_TYPE_VIDEO = 2
_IG_MEDIA_TYPE_CAROUSEL = 8


def targets_from_info(info: Any, pk: str) -> list[tuple[str, str]]:
    """A parte pura de `download_targets`: dado o `media_info`, quais
    downloads.

    Separado para que quem já tem o `info` na mão não peça de novo.
    """
    mtype = int(getattr(info, "media_type", 0) or 0)
    if mtype == _IG_MEDIA_TYPE_CAROUSEL:
        resources = list(getattr(info, "resources", None) or [])
        if not resources:
            return [("photo_download", pk)]
        pairs: list[tuple[str, str]] = []
        for r in resources:
            rm = int(getattr(r, "media_type", 0) or 0)
            is_video = rm == _IG_MEDIA_TYPE_VIDEO
            url = (
                getattr(r, "video_url", None)
                if is_video
                else getattr(r, "thumbnail_url", None)
            )
            if url:
                method = (
                    "video_download_by_url" if is_video else "photo_download_by_url"
                )
                pairs.append((method, str(url)))
                continue
            logger.warning(
                "carousel %s: resource %s has no url, falling back to pk download",
                pk,
                getattr(r, "pk", "?"),
            )
            pairs.append(("clip_download" if is_video else "photo_download", str(r.pk)))
        return pairs
    if mtype == _IG_MEDIA_TYPE_IMAGE:
        url = getattr(info, "thumbnail_url", None)
        if url:
            return [("photo_download_by_url", str(url))]
        logger.warning("imagem %s sem thumbnail_url, caindo no download por pk", pk)
        return [("photo_download", pk)]
    return [("clip_download", pk)]


def post_media_dir(pk: str) -> Path:
    """Onde a mídia de um post vive: uma pasta por `ig_pk`."""
    return IG_DOWNLOADS_DIR / str(pk)


def existing_media(pk: str) -> list[str]:
    """Arquivos já baixados deste post, em ordem de download."""
    d = post_media_dir(pk)
    if not d.is_dir():
        return []
    files = [p for p in d.iterdir() if p.is_file()]
    files.sort(key=lambda p: (p.stat().st_mtime, p.name))
    return [str(p) for p in files]


def default_download(message: dict[str, Any]) -> dict[str, Any]:
    """Download the media for a saved post — ou reaproveita o que já está no
    disco.

    Prefers the authenticated instagrapi client, falling back to yt-dlp
    (public posts, or when IG_SESSIONID is unset) via `infra.downloader`.
    """
    url = message.get("url", "")
    pk = message.get("ig_pk", "")
    if pk:
        cached = existing_media(pk)
        if cached:
            logger.info(
                "post %s: reaproveitando %d arquivo(s) do disco, sem tocar o Instagram",
                pk,
                len(cached),
            )
            return {
                "ok": True,
                "filepath": cached[0],
                "filepaths": cached,
                "reused": True,
            }
    if pk:
        try:
            dest = post_media_dir(pk)
            dest.mkdir(parents=True, exist_ok=True)
            client = ig_sync.make_client()
            client.delay_range = [0.5, 1.0]
            info = client.media_info(pk)
            legenda = (getattr(info, "caption_text", "") or "").strip()
            filepaths: list[str] = []
            for method, target in targets_from_info(info, pk):
                out = getattr(client, method)(target, folder=str(dest))
                if out and Path(out).exists():
                    filepaths.append(str(Path(out)))
            if filepaths:
                return {
                    "ok": True,
                    "filepath": filepaths[0],
                    "filepaths": filepaths,
                    "caption": legenda,
                }
        except Exception as e:
            logger.warning("instagrapi download failed for %s: %s", pk, e)

    # Deliberately lazy (PLC0415): yt-dlp is only needed for this fallback
    # path (public posts, or no IG_SESSIONID) -- the common, authenticated
    # path above never reaches here.
    from infra.downloader import VideoDownloader  # noqa: PLC0415

    dest = post_media_dir(pk) if pk else IG_DOWNLOADS_DIR
    dest.mkdir(parents=True, exist_ok=True)
    downloader = VideoDownloader(output_dir=dest, browser="chrome")
    dl: dict[str, Any] = downloader.download(url)
    if dl.get("ok") and dl.get("filepath"):
        dl["filepaths"] = [dl["filepath"]]
    return dl
