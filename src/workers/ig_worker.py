"""Background daemon consumer for the Instagram saved-posts queue.

Migrated from minimax-video-factory's `minimax_mcp/ig_worker.py`.

LOCATION AMBIGUITY (flagged, not decided silently): the task offered two
candidate locations -- `src/workers/ig_worker.py` or somewhere under
`src/app/`. This file was placed under `src/workers/` because it is a
long-running daemon distinct from the FastAPI app entrypoint
(`src/app/main.py`, today just "Hello from app!") and distinct from the
`core`/`infra` layering (it is an orchestrator that imports from both). If
the intended convention is different, move the file; nothing else in this
migration depends on this exact path.

Consumes ig.saved one message at a time (prefetch=1), downloads the media,
transcribes (video) or describes (image) it, ingests it into the knowledge
base, optionally deletes the downloaded file, and acks. Failures go through
infra.queue.queue.handle_failure (attempts -> DLQ).

The per-item logic (process_message/classify_file/apply_command) is pure and
injected with download/transcribe/describe/ingest callables so it can be unit
tested without RabbitMQ, GPU or network.

Config reads (IG_DOWNLOADS_DIR, IG_DELETE_AFTER_INGEST, WHISPER_MODEL,
IG_WORKER_MIN_INTERVAL, WHISPER_DEVICE, IG_SCREEN_FRAMES, IG_STATE_FILE) now
come from `core.settings.config.settings` instead of `os.environ` directly.

`_default_download`'s final fallback path uses `infra.downloader.VideoDownloader`
(yt-dlp wrapper, migrated alongside this file). The instagrapi-authenticated
path (the primary one for saved/private posts) does not need it; the yt-dlp
fallback is only hit for public posts without IG_SESSIONID, and the import
stays lazy so importing this module never pulls yt-dlp in for the common case.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess  # nosec B404 -- used below with a fixed argv, no shell
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.knowledge import knowledge
from core.settings.config import settings
from infra import db
from infra.instagram import ig_sync
from infra.llm import client as llm
from infra.queue import queue as ig_queue

logger = logging.getLogger(__name__)

_CTA_PATTERNS = (
    r"segue(?:-me| me)?(?: aqui)? (?:para|pra)(?: não| nao)? perder",
    r"j[aá] me segue",
    r"siga para mais",
    r"salva(?: esse| este| o) vídeo",
    r"salv(e|a) para fazer depois",
    r"compartilh(a|e) com (?:seus|teus|os) amigos",
    r"link na bio",
    r"curte e compartilha",
    r"ativa o sininho",
    r"coment(?:a|e)[^.!?]*que eu te mando",
    # Inglês. Metade do que o usuário salva é de conta gringa, e o vocabulário
    # só existia em português.
    #
    # Os padrões são estreitos de propósito. `follow` sozinho apagaria "follow
    # the steps below" e "follow this pattern", que é exatamente o conteúdo que
    # esta base existe para guardar: um CTA que sobrevive é ruído, uma instrução
    # apagada é perda.
    r"follow(?:ing)? (?:me|us)(?=\W*(?:on\b|for\b|@|$|[.!?]))",
    r"follow @",
    r"follow (?:for|to get) more",
    r"still not following",
    r"save (?:this|the) (?:post|video|reel|one)",
    r"share (?:this|it) with (?:a|your|ur)",
    r"tag (?:a|your) (?:friend|buddy)",
    r"link in (?:the )?bio",
    r"(?:double.?tap|smash that)",
    r"(?:comment|drop a comment)[^.!?]*(?:below|and i(?:'|’)?ll|to get)",
    r"turn on (?:the )?notifications",
)
# Fim de frase é `.!?` SEGUIDO de espaço ou fim do texto -- não qualquer ponto.
_TERM = r"[.!?](?=\s|$)"
_NAO_TERM = r"(?:(?!" + _TERM + r").)"

_CTA_SENTENCE_RE = re.compile(
    _NAO_TERM + r"*(?:" + "|".join(_CTA_PATTERNS) + r")" + _NAO_TERM + r"*" + _TERM,
    re.IGNORECASE | re.DOTALL,
)


def strip_cta(text: str) -> str:
    """Remove sentences containing Instagram call-to-action phrases.

    A sentence is delimited by `.`, `!` or `?`. Only the sentence that
    contains the CTA is removed; surrounding content is preserved. Never
    raises and returns input unchanged when no CTA pattern matches.
    """
    if not text:
        return text
    return _CTA_SENTENCE_RE.sub("", text).strip()


# O mesmo vocabulário, mas casando até o fim da string em vez de exigir `.!?`.
_CTA_TAIL_RE = re.compile(
    _NAO_TERM + r"*(?:" + "|".join(_CTA_PATTERNS) + r")" + _NAO_TERM + r"*$",
    re.IGNORECASE | re.DOTALL,
)
_CTA_QUALQUER_RE = re.compile("|".join(_CTA_PATTERNS), re.IGNORECASE)
_HASHTAG_RE = re.compile(r"#\S+")
_WORD_RE = re.compile(r"\w{2,}", re.UNICODE)
# Abaixo disto não é título, é pontuação/lixo sobrando depois de tirar CTA e
# hashtags -- ver clean_title.
_MIN_WORDS_FOR_TITLE = 2


def clean_title(raw: str | None) -> str | None:
    """Limpa o título vindo da legenda do Instagram, ou devolve None.

    CTA na PRIMEIRA frase condena o título inteiro; depois dela, basta aparar.
    Se o que sobrar tiver menos de duas palavras, não é título -- devolve
    None, e o `ingest` cai no fallback que já existe
    (`title or resumo[:80]`), deixando a primeira linha do resumo assumir.
    """
    if not raw:
        return None
    if _CTA_QUALQUER_RE.search(re.split(_TERM, raw, maxsplit=1)[0]):
        return None
    t = _CTA_TAIL_RE.sub("", strip_cta(raw))
    t = _HASHTAG_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip(" -–—|·•,;:")
    return t if len(_WORD_RE.findall(t)) >= _MIN_WORDS_FOR_TITLE else None


IG_DOWNLOADS_DIR = Path(settings.IG_DOWNLOADS_DIR)
# Guardar a mídia é o padrão: uma requisição ao Instagram é escassa e
# punível, enquanto reprocessar na GPU é lento mas local, repetível e sem
# consequência externa.
IG_DELETE_AFTER_INGEST = settings.IG_DELETE_AFTER_INGEST
WHISPER_MODEL = settings.WHISPER_MODEL

# Ritmo mínimo entre mensagens, em segundos. 0 = comportamento antigo,
# consumir o mais rápido que a máquina aguentar.
IG_WORKER_MIN_INTERVAL = float(settings.IG_WORKER_MIN_INTERVAL or 0)
WHISPER_DEVICE = settings.WHISPER_DEVICE

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov"}


def classify_file(filepath: str) -> str:
    return "video" if Path(filepath).suffix.lower() in VIDEO_EXTS else "image"


def pace_sleep_seconds(
    elapsed: float, interval: float = IG_WORKER_MIN_INTERVAL
) -> float:
    """Quanto ainda falta dormir para a mensagem ter levado `interval` no
    total.

    Conta do INÍCIO da mensagem, não do fim. Pura de propósito -- dá para
    testar o ritmo sem fila, sem rede e sem GPU.
    """
    if interval <= 0:
        return 0.0
    return max(0.0, interval - max(0.0, elapsed))


def _build_video_document(
    message: dict[str, Any],
    dl: dict[str, Any],
    filepaths: list[str],
    *,
    transcribe: Callable[[str], dict[str, Any]] | None,
    read_screen: Callable[[str], dict[str, Any]] | None,
) -> dict[str, Any]:
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
    describe: Callable[[str], dict[str, Any]],
    read_screen: Callable[[str], dict[str, Any]] | None,
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
    describe: Callable[[str], dict[str, Any]] | None,
    read_screen: Callable[[str], dict[str, Any]] | None,
) -> dict[str, Any]:
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


def process_message(
    message: dict[str, Any],
    *,
    download: Callable[[dict[str, Any]], dict[str, Any]],
    transcribe: Callable[[str], dict[str, Any]] | None,
    describe: Callable[[str], dict[str, Any]] | None,
    read_screen: Callable[[str], dict[str, Any]] | None = None,
    ingest: Callable[..., dict[str, Any]],
    categories: list[str] | None = None,
) -> dict[str, Any]:
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
    if not built["ok"]:
        return {"status": "error", **{k: v for k, v in built.items() if k != "ok"}}

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


def apply_command(state: dict[str, Any], command: str) -> str:
    """Trata start/stop, mutando `state` E a assinatura da fila de trabalho.

    `basic_cancel` faz o broker parar de entregar; `basic_consume` volta a
    receber. `state["canal"]` e `state["tag"]` são preenchidos pelo laço de
    consumo a cada conexão, e ficam None nos testes, onde só o booleano
    importa.
    """
    canal, tag = state.get("canal"), state.get("tag")
    if command == "start":
        ja_rodando = not state["paused"]
        state["paused"] = False
        if canal is not None and not ja_rodando:
            try:
                state["tag"] = canal.basic_consume(
                    queue=ig_queue.QUEUE, on_message_callback=state["on_work"]
                )
            except Exception as exc:
                logger.warning("não consegui retomar o consumo: %s", exc)
        return "resumed"
    if command == "stop":
        ja_parado = state["paused"]
        state["paused"] = True
        if canal is not None and tag is not None and not ja_parado:
            try:
                canal.basic_cancel(tag)
                state["tag"] = None
            except Exception as exc:
                logger.warning("não consegui cancelar o consumo: %s", exc)
        return "paused"
    return "unknown"


def _download_targets(  # pyright: ignore[reportUnusedFunction]
    client: Any, pk: str
) -> list[tuple[str, str]]:
    """All (method, argument) download pairs for a post.

    Carousels (media_type 8) have no clip of their own, so one pair comes
    back per resource and the whole album is captured. Single media returns
    the pk.

    NOTE: unused in this module (as in the original minimax-video-factory
    source -- `_default_download` calls `_targets_from_info` directly with an
    already-fetched `media_info`). Kept for parity with the source; not a
    migration decision.
    """
    return _targets_from_info(client.media_info(pk), pk)


# instagrapi MediaType values -- same vocabulary as infra.instagram.ig_sync's
# MEDIA_TYPES, repeated here (not imported) because this module only needs
# the raw ints to branch on, not the string labels.
_IG_MEDIA_TYPE_IMAGE = 1
_IG_MEDIA_TYPE_VIDEO = 2
_IG_MEDIA_TYPE_CAROUSEL = 8


def _targets_from_info(info: Any, pk: str) -> list[tuple[str, str]]:
    """A parte pura de `_download_targets`: dado o `media_info`, quais
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


def _default_download(message: dict[str, Any]) -> dict[str, Any]:
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
            for method, target in _targets_from_info(info, pk):
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


IG_SCREEN_FRAMES = int(settings.IG_SCREEN_FRAMES or 4)


def extract_frames(video_path: str, n: int = IG_SCREEN_FRAMES) -> list[str]:
    """`n` quadros espalhados pelo vídeo, num diretório temporário.

    Os quadros são descartáveis: o que se guarda é o vídeo, e refazer custa
    pouco.
    """
    try:
        dur = float(
            subprocess.run(  # nosec B603 B607 -- fixed argv, no shell, no user-controlled executable path
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "csv=p=0",
                    video_path,
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            ).stdout.strip()
        )
    except Exception as exc:
        logger.warning("ffprobe falhou em %s: %s", video_path, exc)
        return []
    if dur <= 0:
        return []

    d = Path(tempfile.mkdtemp(prefix="igframes_"))
    try:
        subprocess.run(  # nosec B603 B607 -- fixed argv, no shell, no user-controlled executable path
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-i",
                video_path,
                "-vf",
                f"fps={n}/{dur},scale=1080:-1",
                "-frames:v",
                str(n),
                "-q:v",
                "2",
                str(d / "f_%02d.jpg"),
            ],
            capture_output=True,
            timeout=120,
            check=True,
        )
    except Exception as exc:
        logger.warning("ffmpeg falhou em %s: %s", video_path, exc)
        return []
    return sorted(str(p) for p in d.glob("*.jpg"))


def merge_screen_text(pedacos: list[str]) -> str:
    """Junta o texto dos quadros sem repetir o que já apareceu.

    Deduplica por LINHA e preserva a ordem.
    """
    saida: list[str] = []
    chaves: list[str] = []
    for p in pedacos:
        for linha in (p or "").splitlines():
            chave = " ".join(linha.split())
            if not chave:
                continue
            contida = next((i for i, k in enumerate(chaves) if chave in k), None)
            if contida is not None:
                continue
            contem = next((i for i, k in enumerate(chaves) if k in chave), None)
            if contem is not None:
                saida[contem] = linha.rstrip()
                chaves[contem] = chave
                continue
            chaves.append(chave)
            saida.append(linha.rstrip())
    return "\n".join(saida).strip()


def guardar_capa(quadros: list[str], video_path: str) -> str | None:
    """Guarda UM quadro como capa do post, ao lado do vídeo."""
    if not quadros:
        return None
    escolhido = quadros[1] if len(quadros) > 1 else quadros[0]
    destino = Path(video_path).parent / "capa.jpg"
    try:
        destino.write_bytes(Path(escolhido).read_bytes())
        return str(destino)
    except OSError as exc:
        logger.warning("não consegui guardar a capa de %s: %s", video_path, exc)
        return None


def _default_read_screen(video_path: str) -> dict[str, Any]:
    """Lê o texto que está escrito na mídia. Aceita vídeo e imagem."""
    if classify_file(video_path) == "image":
        return llm.read_screen(video_path)

    quadros = extract_frames(video_path)
    if not quadros:
        return {"ok": True, "text": ""}
    pai = str(Path(quadros[0]).parent)
    try:
        capa = guardar_capa(quadros, video_path)
        pedacos: list[str] = []
        for q in quadros:
            r = llm.read_screen(q)
            if r.get("ok") and r.get("text"):
                pedacos.append(r["text"])
            elif not r.get("ok"):
                logger.warning("leitura de tela falhou em %s: %s", q, r.get("error"))

        descricao = ""
        try:
            de = llm.describe_image(quadros[len(quadros) // 2])
            if de.get("ok"):
                descricao = (
                    de.get("conteudo_principal") or de.get("text") or ""
                ).strip()
        except Exception as exc:
            logger.warning("descrição do vídeo falhou: %s", exc)

        return {
            "ok": True,
            "text": merge_screen_text(pedacos),
            "descricao": descricao,
            "capa": capa,
        }
    finally:
        shutil.rmtree(pai, ignore_errors=True)


_categories_cache: list[str] | None = None


def _discard_media(res: dict[str, Any]) -> None:
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


def _safe_ack(ch: Any, method: Any) -> None:
    """Ack a delivery, swallowing channel/connection errors."""
    try:
        ch.basic_ack(method.delivery_tag)
    except Exception as exc:
        logger.warning(
            "ack failed for delivery_tag=%s (%s); will redeliver",
            method.delivery_tag,
            exc,
        )


# Cap on IG_STATE_FILE's history: enough for `ig_get_progress` to show a
# useful tail without the file growing without bound over a long-running
# daemon.
_MAX_PROGRESS_ENTRIES = 500


def _pace_after(started: float, pk: str | None) -> None:
    """Sleep out the rest of IG_WORKER_MIN_INTERVAL, if any is left."""
    pausa = pace_sleep_seconds(time.monotonic() - started)
    if pausa > 0:
        logger.info("pace: aguardando %.1fs antes do próximo (ig_pk=%s)", pausa, pk)
        time.sleep(pausa)


def _record_progress(message: dict[str, Any], res: dict[str, Any]) -> None:
    """Append a `{"status": "done", ...}` entry to IG_STATE_FILE, capped at
    `_MAX_PROGRESS_ENTRIES`. Best-effort: a failure here must not re-queue
    or re-process a post that already ingested fine.
    """
    state_path = Path(settings.IG_STATE_FILE)
    try:
        existing: list[dict[str, Any]] = (
            json.loads(state_path.read_text(encoding="utf-8"))
            if state_path.exists()
            else []
        )
        existing.append(
            {
                "ig_pk": message["ig_pk"],
                "status": "done",
                "document_id": res["document_id"],
                "title": message.get("title"),
            }
        )
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(existing[-_MAX_PROGRESS_ENTRIES:], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("could not update the progress file: %s", exc)


def _requeue_or_dead_letter(
    ch: Any, properties: Any, body: bytes, *, permanente: bool
) -> None:
    """Send a failed delivery's COPY to the retry queue or straight to the
    DLQ. The caller still acks the original delivery afterwards."""
    try:
        if permanente:
            ig_queue.dead_letter(ch, properties, body)
        else:
            ig_queue.handle_failure(ch, properties, body)
    except Exception as exc:
        logger.warning("handle_failure failed: %s", exc)


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
        _safe_ack(ch, method)
        return

    message = parsed["message"]
    session = db.get_session()
    try:
        already = db.document_exists(session, ig_pk=message.get("ig_pk"))
    finally:
        session.close()
    if already:
        logger.info("duplicate ig_pk=%s -> ack without processing", message["ig_pk"])
        _safe_ack(ch, method)
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
            "ingested ig_pk=%s document_id=%s", message["ig_pk"], res["document_id"]
        )
        _record_progress(message, res)
    else:
        permanente = bool(res.get("permanent"))
        logger.warning(
            "processing failed ig_pk=%s: %s%s",
            message["ig_pk"],
            res["error"],
            " (permanente)" if permanente else "",
        )
        _requeue_or_dead_letter(ch, properties, body, permanente=permanente)
    _pace_after(started, message.get("ig_pk"))
    _safe_ack(ch, method)


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
        _safe_ack(ch, method)

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
