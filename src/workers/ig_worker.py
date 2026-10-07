"""Background daemon consumer for the Instagram saved-posts queue.

Migrated from minimax-video-factory's `minimax_mcp/ig_worker.py`.

ESTRUTURA (após o fatiamento do audit F1/SRP): este módulo é só a
ORQUESTRAÇÃO — montagem dos documentos, injeção dos defaults (GPU/LLM) e o
laço de consumo AMQP. A lógica de cada etapa mora nos irmãos:

- `workers.text`          — remoção de CTA e limpeza de título;
- `workers.media_download` — instagrapi/yt-dlp, cache em disco, classificação;
- `workers.screen`         — ffmpeg/ffprobe, OCR e fusão do texto de tela;
- `workers.consumer`       — comandos start/stop, ack, DLQ, progresso, ritmo.

Os nomes movidos são re-exportados aqui (ver `__all__`) para que os
scripts (`ig_reprocessar.py`, `ig_pendentes.py`), os testes e o
`python -m workers.ig_worker` continuem importando deste caminho.

Consumes ig.saved one message at a time (prefetch=1), downloads the media,
transcribes (video) or describes (image) it, ingests it into the knowledge
base, optionally deletes the downloaded file, and acks. Failures go through
infra.queue.queue.handle_failure (attempts -> DLQ).

The per-item logic (process_message/classify_file/apply_command) is pure and
injected with download/transcribe/describe/ingest callables so it can be unit
tested without RabbitMQ, GPU or network.

Config reads (IG_DOWNLOADS_DIR, IG_DELETE_AFTER_INGEST, WHISPER_MODEL,
IG_WORKER_MIN_INTERVAL, WHISPER_DEVICE, IG_SCREEN_FRAMES, IG_STATE_FILE) come
from `core.settings.config.settings` instead of `os.environ` directly.

`_default_download`'s final fallback path uses `infra.downloader.VideoDownloader`
(yt-dlp wrapper) inside `workers.media_download`. The instagrapi-authenticated
path (the primary one for saved/private posts) does not need it; the yt-dlp
fallback is only hit for public posts without IG_SESSIONID, and the import
stays lazy so importing this module never pulls yt-dlp in for the common case.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from core.contracts import (
    Describer,
    DocBuilt,
    DocBuiltErr,
    Downloader,
    Ingester,
    ProcessResult,
    ScreenReader,
    Transcriber,
)
from core.knowledge import knowledge
from core.settings.config import settings
from infra import db
from infra.instagram import ig_sync
from infra.llm import client as llm
from infra.queue import queue as ig_queue
from workers.consumer import (
    apply_command,
    pace_after,
    pace_sleep_seconds,
    record_progress,
    requeue_or_dead_letter,
    safe_ack,
)
from workers.media_download import (
    VIDEO_EXTS,
    classify_file,
    default_download,
    download_targets,
    existing_media,
    post_media_dir,
)
from workers.media_download import (
    default_download as _default_download,
)
from workers.media_download import (
    targets_from_info as _targets_from_info,
)
from workers.screen import (
    default_read_screen,
    extract_frames,
    guardar_capa,
    merge_screen_text,
)
from workers.screen import (
    default_read_screen as _default_read_screen,
)
from workers.text import clean_title, strip_cta

# Re-exported surface kept stable for scripts/tests (see module docstring).
__all__ = [
    "VIDEO_EXTS",
    "_default_download",
    "_default_read_screen",
    "_targets_from_info",
    "apply_command",
    "clean_title",
    "classify_file",
    "default_download",
    "default_read_screen",
    "download_targets",
    "existing_media",
    "extract_frames",
    "guardar_capa",
    "merge_screen_text",
    "pace_sleep_seconds",
    "post_media_dir",
    "process_message",
    "run",
    "strip_cta",
]

logger = logging.getLogger(__name__)

# Guardar a mídia é o padrão: uma requisição ao Instagram é escassa e
# punível, enquanto reprocessar na GPU é lento mas local, repetível e sem
# consequência externa.
IG_DELETE_AFTER_INGEST = settings.IG_DELETE_AFTER_INGEST
WHISPER_MODEL = settings.WHISPER_MODEL
WHISPER_DEVICE = settings.WHISPER_DEVICE


def _build_video_document(
    message: dict[str, Any],
    dl: dict[str, Any],
    filepaths: list[str],
    *,
    transcribe: Transcriber | None,
    read_screen: ScreenReader | None,
) -> DocBuilt | DocBuiltErr:
    """Transcribe+read the first video file and assemble the ingest text.

    Split out of `process_message` (which was tripping PLR0911/PLR0912/
    PLR0915) -- this is the whole "video" branch, pure and self-contained.

    Returns {"ok": True, "text", "lang", "doc_type": "video", "categoria":
    None} or {"ok": False, "error", "filepaths", ["permanent"]}.
    """
    if transcribe is None:
        return {
            "ok": False,
            "error": "no transcribe provided for video",
            "filepaths": filepaths,
        }
    tr = transcribe(filepaths[0])
    if not tr.get("ok"):
        return {
            "ok": False,
            "error": tr.get("error", "transcribe failed"),
            "filepaths": filepaths,
        }
    text, lang = tr["text"], tr.get("language", "pt")

    tela = ""
    descricao_video = ""
    if read_screen is not None:
        rs = read_screen(filepaths[0])
        if rs.get("ok"):
            tela = (rs.get("text") or "").strip()
            descricao_video = (rs.get("descricao") or "").strip()
        else:
            logger.warning("leitura de tela falhou: %s", rs.get("error"))

    legenda = (
        dl.get("caption") or message.get("caption") or message.get("title") or ""
    ).strip()

    fala = (text or "").strip()
    partes: list[str] = []
    if fala:
        partes.append(fala)
    if tela:
        partes.append(f"--- texto na tela ---\n{tela}")
    if descricao_video:
        partes.append(f"--- descrição do vídeo ---\n{descricao_video}")
    if legenda:
        partes.append(f"--- legenda ---\n{legenda}")
    text = "\n\n".join(partes)

    if not fala:
        lang = "pt"
    logger.info(
        "ig_pk=%s: fala=%d tela=%d descrição=%d legenda=%d caracteres",
        message.get("ig_pk"),
        len(fala),
        len(tela),
        len(descricao_video),
        len(legenda),
    )
    if not text.strip():
        return {
            "ok": False,
            "error": "sem fala, sem tela e sem legenda",
            "permanent": True,
            "filepaths": filepaths,
        }
    if not fala:
        logger.info(
            "ig_pk=%s sem fala; documento montado com %d caracteres de "
            "tela/descrição/legenda",
            message.get("ig_pk"),
            len(text),
        )
    return {
        "ok": True,
        "text": text,
        "lang": lang,
        "doc_type": "video",
        "categoria": None,
    }


def _describe_one_image(
    fp: str,
    *,
    describe: Describer,
    read_screen: ScreenReader | None,
) -> dict[str, Any]:
    """Describe+read_screen for a single carousel image. Split out of
    `_build_image_document` to keep its branch count under control
    (PLR0912).

    Returns {"ok": True, "piece", "categoria", "screen_text"} or
    {"ok": False, "error"}.
    """
    de = describe(fp)
    if not de.get("ok"):
        return {"ok": False, "error": str(de.get("error", "describe failed"))}
    screen_text = ""
    if read_screen is not None:
        rs = read_screen(fp)
        if rs.get("ok") and (rs.get("text") or "").strip():
            screen_text = rs["text"].strip()
        elif not rs.get("ok"):
            logger.warning("leitura de texto da imagem falhou: %s", rs.get("error"))
    return {
        "ok": True,
        "piece": de.get("conteudo_principal") or de.get("text") or "",
        "categoria": de.get("categoria"),
        "screen_text": screen_text,
    }


def _build_image_document(
    message: dict[str, Any],
    dl: dict[str, Any],
    filepaths: list[str],
    *,
    describe: Describer | None,
    read_screen: ScreenReader | None,
) -> DocBuilt | DocBuiltErr:
    """Describe each image file (carousels included) and assemble the
    ingest text. Same split rationale as `_build_video_document`.

    Returns {"ok": True, "text", "lang": "pt", "doc_type": "image",
    "categoria"} or {"ok": False, "error", "filepaths"}.
    """
    if describe is None:
        return {
            "ok": False,
            "error": "no describe provided for image",
            "filepaths": filepaths,
        }
    pieces: list[str] = []
    escritos: list[str] = []
    categoria = None
    imagens = [fp for fp in filepaths if classify_file(fp) == "image"]
    if not imagens:
        return {
            "ok": False,
            "error": "carrossel sem nenhuma imagem para descrever",
            "filepaths": filepaths,
        }
    if len(imagens) < len(filepaths):
        logger.info(
            "ig_pk=%s: carrossel misto, descrevendo %d imagem(ns) e "
            "ignorando %d vídeo(s)",
            message.get("ig_pk"),
            len(imagens),
            len(filepaths) - len(imagens),
        )
    falhas: list[str] = []
    for fp in imagens:
        r = _describe_one_image(fp, describe=describe, read_screen=read_screen)
        if not r["ok"]:
            falhas.append(r["error"])
            logger.warning("descrição falhou em %s: %s", Path(fp).name, r["error"])
            continue
        pieces.append(r["piece"])
        if categoria is None:
            categoria = r["categoria"]
        if r["screen_text"]:
            escritos.append(r["screen_text"])
    if falhas and not pieces:
        return {
            "ok": False,
            "error": (
                f"describe falhou em todas as {len(falhas)} imagem(ns): {falhas[0]}"
            ),
            "filepaths": filepaths,
        }
    descricao = "\n\n".join(p for p in pieces if p)
    lido = merge_screen_text(escritos)
    partes: list[str] = []
    if lido:
        partes.append(f"--- texto na imagem ---\n{lido}")
    if descricao:
        partes.append(f"--- descrição da imagem ---\n{descricao}")
    legenda = (
        dl.get("caption") or message.get("caption") or message.get("title") or ""
    ).strip()
    if legenda:
        partes.append(f"--- legenda ---\n{legenda}")
    text = "\n\n".join(partes)
    return {
        "ok": True,
        "text": text,
        "lang": "pt",
        "doc_type": "image",
        "categoria": categoria,
    }


def process_message(  # noqa: PLR0913 -- 5 callables de IO ja sao Protocol (F4b)
    message: dict[str, Any],
    *,
    download: Downloader,
    transcribe: Transcriber | None,
    describe: Describer | None,
    read_screen: ScreenReader | None = None,
    ingest: Ingester,
    categories: list[str] | None = None,
) -> ProcessResult:
    """Download -> transcribe/describe -> ingest. Pure; all IO injected.

    Videos transcribe the first downloaded file. Images/carousels describe
    cada arquivo de IMAGEM e juntam os `conteudo_principal` num texto só. A
    categoria da primeira foto vira a tag `categoria:<nome>`.
    """
    if not message:
        return {"status": "error", "error": "empty message"}

    dl = download(message)
    if not dl.get("ok"):
        return {"status": "error", "error": dl.get("error", "download failed")}
    filepaths = [f for f in (dl.get("filepaths") or [dl.get("filepath")]) if f]
    if not filepaths:
        return {"status": "error", "error": "no file downloaded"}

    kind = classify_file(filepaths[0])
    if kind == "video":
        built = _build_video_document(
            message, dl, filepaths, transcribe=transcribe, read_screen=read_screen
        )
    else:
        built = _build_image_document(
            message, dl, filepaths, describe=describe, read_screen=read_screen
        )
    if built["ok"] is False:
        # Comprehension filtra `ok` dinamicamente; pyright nao fecha o
        # TypedDict a partir dele -- o cast documenta essa fronteira.
        return cast(
            ProcessResult,
            {"status": "error", **{k: v for k, v in built.items() if k != "ok"}},
        )

    text, lang, doc_type, categoria = (
        built["text"],
        built["lang"],
        built["doc_type"],
        built["categoria"],
    )

    extra_tags = (
        [f"colecao:{message['collection_name']}"]
        if message.get("collection_name")
        else None
    )
    classified = bool(categoria) and categoria != "outros"
    if classified:
        extra_tags = (extra_tags or []) + [f"categoria:{categoria}"]
    text = strip_cta(text)
    title = clean_title(message.get("title"))
    ing = ingest(
        text,
        source_url=ig_sync.post_url(message.get("ig_pk"))
        if message.get("ig_pk")
        else message.get("url"),
        title=title,
        platform="instagram",
        doc_type=doc_type,
        language=lang,
        ig_pk=message.get("ig_pk"),
        extra_tags=extra_tags,
        raw_file_path=filepaths[0],
        categories=None if classified else categories,
    )
    if not ing.get("ok"):
        return {
            "status": "error",
            "error": ing.get("error", "ingest failed"),
            "filepaths": filepaths,
        }

    return {
        "status": "done",
        "document_id": ing.get("document_id"),
        "kind": kind,
        "filepath": filepaths[0],
        "filepaths": filepaths,
    }


_categories_cache: list[str] | None = None


def _discard_media(res: Mapping[str, Any]) -> None:
    """Apaga a mídia baixada, tenha a ingestão dado certo ou não.

    Só roda quando `IG_DELETE_AFTER_INGEST` é ligado explicitamente.
    """
    if not IG_DELETE_AFTER_INGEST:
        return
    for fp in res.get("filepaths") or [res.get("filepath")]:
        if not fp:
            continue
        try:
            Path(fp).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("could not delete %s: %s", fp, exc)


def _category_vocabulary() -> list[str]:
    """The user's collection names, fetched once per process."""
    # `global` (PLW0603) is deliberate: the whole point is a process-wide
    # memoization cache (one Instagram API round-trip per run, not one per
    # message), not per-call state. A class singleton would hide the same
    # module-level mutable behind an extra layer for no real gain here.
    global _categories_cache  # noqa: PLW0603
    if _categories_cache is None:
        try:
            _categories_cache = ig_sync.list_categories(ig_sync.make_client())
        except Exception as exc:
            logger.warning("could not read collections, using defaults: %s", exc)
            _categories_cache = ig_sync.list_categories(None)
    return _categories_cache


def _default_describe(filepath: str) -> dict[str, Any]:
    """Describe an image with the category vocabulary threaded in."""
    return llm.describe_image(filepath, categories=_category_vocabulary())


def _default_transcribe(filepath: str) -> dict[str, Any]:
    # Deliberately lazy (PLC0415): faster-whisper is a heavy, GPU-touching
    # dependency; importing it only when a video actually needs transcribing
    # keeps unit tests (which inject fakes for this callable) free of it.
    from infra.gpu_lock import GpuLockTimeout, held  # noqa: PLC0415
    from infra.transcriber import AudioTranscriber  # noqa: PLC0415

    # This machine has one 12 GB GPU, shared with minimax-video-factory's
    # ComfyUI renders (same lock file, see infra/gpu_lock/gpu_lock.py).
    # Timeout is generous: a render can legitimately run for ~20 minutes
    # (see that repo's CLAUDE.md pixel-frame budget table).
    try:
        with held(f"ig-worker transcribe {filepath}", timeout=1800):
            transcriber = AudioTranscriber(
                model_size=WHISPER_MODEL, device=WHISPER_DEVICE
            )
            try:
                return transcriber.transcribe(filepath)
            finally:
                try:
                    transcriber.free()
                except Exception:
                    logger.warning("could not release Whisper VRAM", exc_info=True)
    except GpuLockTimeout as exc:
        return {"ok": False, "error": str(exc)}


def _on_work(ch: Any, method: Any, properties: Any, body: bytes) -> None:
    """pika on_message_callback for the ig.saved work queue.

    Top-level (not nested in `run()`) because it never needs `run()`'s
    `state` -- keeping it here is also what let `run()` itself drop under
    PLR0915's statement-count ceiling.
    """
    started = time.monotonic()
    parsed = ig_queue.parse_message(body)
    if not parsed["ok"]:
        logger.warning("corrupted message -> DLQ: %s", parsed["error"])
        try:
            ig_queue.dead_letter(ch, properties, body)
        except Exception as exc:
            logger.warning("dead_letter failed: %s", exc)
        safe_ack(ch, method)
        return

    message = parsed["message"]
    session = db.get_session()
    try:
        already = db.document_exists(session, ig_pk=message.get("ig_pk"))
    finally:
        session.close()
    if already:
        logger.info("duplicate ig_pk=%s -> ack without processing", message["ig_pk"])
        safe_ack(ch, method)
        return

    res = process_message(
        message,
        download=_default_download,
        transcribe=_default_transcribe,
        describe=_default_describe,
        read_screen=_default_read_screen,
        ingest=knowledge.ingest_text,
        categories=_category_vocabulary(),
    )
    _discard_media(res)

    if res["status"] == "done":
        logger.info(
            "ingested ig_pk=%s document_id=%s", message["ig_pk"], res.get("document_id")
        )
        record_progress(message, res)
    else:
        permanente = bool(res.get("permanent"))
        logger.warning(
            "processing failed ig_pk=%s: %s%s",
            message["ig_pk"],
            res.get("error"),
            " (permanente)" if permanente else "",
        )
        requeue_or_dead_letter(ch, properties, body, permanente=permanente)
    pace_after(started, message.get("ig_pk"))
    safe_ack(ch, method)


def run() -> None:
    """Daemon main: connect, declare, consume ig.saved + control queue."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )

    state: dict[str, Any] = {"paused": False, "tag": None, "canal": None}

    def on_control(ch: Any, method: Any, properties: Any, body: bytes) -> None:
        try:
            command = json.loads(body).get("command", "")
        except ValueError, TypeError:
            command = ""
        logger.info("control command: %s", apply_command(state, command))
        safe_ack(ch, method)

    def consume_once() -> None:
        """Connect and consume until the connection dies; returns on error."""
        connection = ig_queue.connect()
        try:
            channel = connection.channel()
            ig_queue.declare(channel)
            channel.basic_qos(prefetch_count=1)
            state["canal"], state["on_work"] = channel, _on_work
            state["tag"] = (
                None
                if state["paused"]
                else channel.basic_consume(  # pyright: ignore[reportUnknownMemberType] -- pika's stub for basic_consume is incomplete on some pyright/typeshed pins
                    queue=ig_queue.QUEUE, on_message_callback=_on_work
                )
            )
            channel.basic_consume(  # pyright: ignore[reportUnknownMemberType]
                queue=ig_queue.CONTROL_QUEUE, on_message_callback=on_control
            )
            logger.info(
                "ig-worker consuming %s (paused=%s)", ig_queue.QUEUE, state["paused"]
            )
            channel.start_consuming()
        except KeyboardInterrupt:
            raise
        except KeyError as exc:
            logger.error(
                "variável de ambiente ausente: %s — o worker NÃO vai se recuperar "
                "sozinho, isto não é queda de conexão. Carregue o .env antes de "
                "subir o daemon: `set -a; . ./.env; set +a`",
                exc,
            )
        except Exception as exc:
            logger.warning("consumer connection dropped: %s", exc)
        finally:
            ig_queue.close(connection)

    backoff = 1
    while True:
        try:
            consume_once()
        except KeyboardInterrupt:
            break
        time.sleep(backoff)
        backoff = min(backoff * 2, 30)
        logger.info("reconnecting in %ss...", backoff)


if __name__ == "__main__":
    run()
