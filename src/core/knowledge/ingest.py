"""Ingestão na base de conhecimento: texto, mídia e markdown.

Extraído de `core.knowledge.knowledge` (audit F2/SRP, domínios 1–3 do
facade). Dono de: `ingest_text` (LLM → DB → vault), os pipelines de mídia
(`ingest_video`/`ingest_audio` com download+transcrição+sniff de
plataforma) e a importação de markdown (`ingest_markdown` com frontmatter
YAML, seção `## Summary` e fallback via LLM).

Os helpers de frontmatter (`_parse_frontmatter`, `_extract_section`) moram
aqui porque só a importação de markdown os usa. yt-dlp/faster-whisper
seguem com import lazy (PLC0415): quem só consulta não paga o custo.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import yaml

from core.settings.config import settings
from infra import db, vault
from infra.llm import client as llm

VAULT_PATH = settings.VAULT_PATH or None

# `"---\nmeta\n---\nbody"`.split("---", 2) -- 3 parts means the leading and
# closing frontmatter fences were both found.
_FRONTMATTER_PARTS_WITH_BODY = 3


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split YAML frontmatter (`---` delimited, if present) from the markdown
    body.

    Returns (meta, body). Tolerates missing/broken frontmatter.
    """
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < _FRONTMATTER_PARTS_WITH_BODY:
        return {}, text
    meta_raw, body = parts[1], parts[2]
    try:
        meta: Any = yaml.safe_load(meta_raw) or {}
    except Exception:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return cast(dict[str, Any], meta), body.lstrip("\n")


def _extract_section(body: str, heading: str = "Summary") -> str | None:
    """Extract the text under a `## <heading>` markdown section
    (case-insensitive), e.g. the `## Summary` block found in Claude Chat
    transcripts."""
    pat = re.compile(rf"^##+\s*{re.escape(heading)}\s*$", re.MULTILINE | re.IGNORECASE)
    m = pat.search(body)
    if not m:
        return None
    start = m.end()
    # section ends at the next top-level (## or lower) heading
    nxt = re.search(r"^##+\s+\S", body[start:], re.MULTILINE)
    end = start + nxt.start() if nxt else len(body)
    text = body[start:end].strip()
    return text or None


def ingest_text(
    text: str,
    *,
    source_url: str | None = None,
    title: str | None = None,
    platform: str = "manual",
    doc_type: str = "text",
    language: str = "pt",
    ig_pk: str | None = None,
    extra_tags: list[str] | None = None,
    categories: list[str] | None = None,
    raw_file_path: str | None = None,
) -> dict[str, Any]:
    """Summarize+document `text` via the LLM and store it in the knowledge base.

    `categories` asks the summary model to classify the content into that
    vocabulary; the result lands as a `categoria:<name>` tag. Callers that
    already know the category -- the Instagram worker does, for images, from
    the vision model -- should leave it None instead of asking twice.

    `raw_file_path` é o arquivo de onde este texto saiu, quando ele foi
    preservado. Quem guarda a mídia precisa dela para achar o que
    re-transcrever depois.
    """
    if not text or not text.strip():
        return {"ok": False, "error": "empty text"}

    gen = llm.generate_structured(
        text, is_image=(doc_type == "image"), categories=categories
    )
    if not gen.get("ok"):
        return {"ok": False, "stage": "llm", "error": gen.get("error")}

    generated_tags = list(gen.get("tags") or [])
    categoria = (gen.get("categoria") or "").strip()
    if categoria and categoria.lower() != "outros":
        generated_tags.append(f"categoria:{categoria}")

    tags = list(dict.fromkeys([t for t in generated_tags + (extra_tags or []) if t]))

    session = db.get_session()
    try:
        doc = db.save_document(
            session,
            type=doc_type,
            source_url=source_url,
            platform=platform,
            title=title or (gen["resumo"][:80] if gen.get("resumo") else "sem título"),
            language=language,
            transcription_text=text,
            summary=gen.get("resumo"),
            tutorial=gen.get("tutorial"),
            objectives="\n".join(gen.get("objetivos") or []),
            tags=tags,
            raw_file_path=raw_file_path,
            llm_provider=gen.get("provider"),
            llm_model=gen.get("model"),
            embed_fn=llm.embed,
            embedding_model=llm.EMBEDDING_MODEL,
            ig_pk=ig_pk,
        )
        doc_dict = {
            "id": doc.id,
            "title": doc.title,
            "summary": doc.summary,
            "tutorial": doc.tutorial,
            "tags": doc.tags,
            "source_url": doc.source_url,
            "platform": doc.platform,
            "type": doc.type,
            "transcription_text": doc.transcription_text,
        }
    except Exception as e:
        session.rollback()
        return {"ok": False, "stage": "db", "error": str(e)}
    finally:
        session.close()

    vault_result = vault.write_markdown_copy(doc_dict, VAULT_PATH)
    return {
        "ok": True,
        "document_id": doc_dict["id"],
        "title": doc_dict["title"],
        "summary": doc_dict["summary"],
        "tutorial": doc_dict["tutorial"],
        "tags": doc_dict["tags"],
        "vault": vault_result,
    }


def ingest_video(
    url: str,
    *,
    browser: str = "chrome",
    downloads_dir: str | Path = "downloads",
    whisper_model: str = "small",
    whisper_device: str = "cuda",
) -> dict[str, Any]:
    """Download a video, transcribe it, and document it in the knowledge base."""
    # Deliberately lazy (PLC0415): yt-dlp and faster-whisper are heavy,
    # optional dependencies needed only by this function. `search`/`ask`/
    # `ingest_text` are called far more often and must not pay their import
    # cost (or require them installed) just by importing this module.
    from infra.downloader import VideoDownloader  # noqa: PLC0415
    from infra.transcriber import AudioTranscriber  # noqa: PLC0415

    downloader = VideoDownloader(output_dir=downloads_dir, browser=browser)
    dl: dict[str, Any] = downloader.download(url)
    if not dl.get("ok"):
        return {"ok": False, "stage": "download", "error": dl.get("error")}

    transcriber = AudioTranscriber(model_size=whisper_model, device=whisper_device)
    tr = transcriber.transcribe(dl["filepath"])
    if not tr.get("ok"):
        return {"ok": False, "stage": "transcribe", "error": tr.get("error")}

    if "instagram" in url:
        platform = "instagram"
    elif "youtu" in url:
        platform = "youtube"
    else:
        platform = "video"

    result = ingest_text(
        tr["text"],
        source_url=dl.get("webpage_url", url),
        title=dl.get("title"),
        platform=platform,
        doc_type="video",
        language=tr.get("language", "pt"),
    )
    if result.get("ok"):
        result["downloaded_file"] = dl["filepath"]
    return result


def ingest_audio(
    path_or_url: str,
    *,
    browser: str = "chrome",
    downloads_dir: str | Path = "downloads",
    whisper_model: str = "small",
    whisper_device: str = "cuda",
) -> dict[str, Any]:
    """Transcribe a local audio file (or download it first if given a URL)
    and document it in the knowledge base.
    """
    # Same reasoning as ingest_video's lazy imports above.
    from infra.transcriber import AudioTranscriber  # noqa: PLC0415

    if path_or_url.startswith(("http://", "https://")):
        from infra.downloader import VideoDownloader  # noqa: PLC0415

        downloader = VideoDownloader(output_dir=downloads_dir, browser=browser)
        dl: dict[str, Any] = downloader.download(path_or_url)
        if not dl.get("ok"):
            return {"ok": False, "stage": "download", "error": dl.get("error")}
        filepath: str = dl["filepath"]
        title: str | None = dl.get("title")
        source_url: str | None = dl.get("webpage_url", path_or_url)
    else:
        filepath = path_or_url
        title = Path(path_or_url).stem
        source_url = None

    transcriber = AudioTranscriber(model_size=whisper_model, device=whisper_device)
    tr = transcriber.transcribe(filepath)
    if not tr.get("ok"):
        return {"ok": False, "stage": "transcribe", "error": tr.get("error")}

    result = ingest_text(
        tr["text"],
        source_url=source_url,
        title=title,
        platform="podcast",
        doc_type="audio",
        language=tr.get("language", "pt"),
    )
    if result.get("ok"):
        result["source_file"] = filepath
    return result


def ingest_markdown(
    path: str | Path,
    *,
    recursive: bool = False,
    doc_type: str = "document",
    platform: str = "obsidian",
    language: str = "pt",
    reindex_if_exists: bool = False,
) -> dict[str, Any]:
    """Import one or more markdown files (e.g. Obsidian tutorials) into the KB.

    Accepts a file path or a directory (recursive optional). YAML frontmatter
    is used for title/tags/source_url; a `## Summary` section is reused when
    present, otherwise the LLM generates summary+tutorial+objectives+tags.
    Skips `.trash`, `.obsidian` and other dot-directories. Returns per-file
    results.
    """
    p = Path(path).expanduser()
    if p.is_file():
        files = [p]
    elif p.is_dir():
        files = sorted(
            f
            for f in (p.rglob("*.md") if recursive else p.glob("*.md"))
            if not any(part.startswith(".") for part in f.relative_to(p).parts)
        )
    else:
        return {"ok": False, "error": f"path not found: {path}"}
    if not files:
        return {"ok": False, "error": f"no .md files found in {path}"}

    results: list[dict[str, Any]] = []
    imported = 0
    for f in files:
        res = _ingest_markdown_file(
            f,
            doc_type=doc_type,
            platform=platform,
            language=language,
            reindex_if_exists=reindex_if_exists,
        )
        results.append({"file": str(f), **res})
        if res.get("ok"):
            imported += 1
    failed = len(files) - imported

    # ok reflects whether anything was actually imported. Returning ok=True
    # with imported=0 reads as success and hides the common causes -- an
    # unreachable database, Ollama down -- behind a green result.
    first_error = next(
        (r.get("error") for r in results if not r.get("ok") and r.get("error")), None
    )
    out: dict[str, Any] = {
        "ok": imported > 0,
        "imported": imported,
        "failed": failed,
        "total": len(files),
        "results": results,
    }
    if failed:
        out["error"] = f"{failed} of {len(files)} file(s) failed" + (
            f": {first_error}" if first_error else ""
        )
    return out


def _ingest_markdown_file(
    f: Path,
    *,
    doc_type: str,
    platform: str,
    language: str,
    reindex_if_exists: bool,
) -> dict[str, Any]:
    try:
        content = f.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"ok": False, "error": f"read failed: {e}"}
    if not content.strip():
        return {"ok": False, "error": "empty file"}

    meta, body = _parse_frontmatter(content)
    title = str(meta.get("title") or f.stem).strip()
    source_url = meta.get("url") or None
    fm_tags: list[Any] = meta.get("tags") or []
    if isinstance(fm_tags, str):
        fm_tags = [t.strip() for t in fm_tags.split(",") if t.strip()]
    aliases: list[Any] = meta.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]

    summary = _extract_section(body, "Summary")
    if summary:
        return _save_document_with(
            f,
            doc_type=doc_type,
            platform=platform,
            language=language,
            title=title,
            source_url=source_url,
            text=body,
            summary=summary,
            tutorial=None,
            objectives=None,
            tags=fm_tags,
            llm_provider="frontmatter",
            llm_model="none",
        )

    # O `generate_structured` mede o orçamento, corta só o que não cabe e
    # REGISTRA quanto perdeu -- um corte fixo aqui só jogaria fora nota boa.
    gen = llm.generate_structured(body)
    if not gen.get("ok"):
        return {"ok": False, "stage": "llm", "error": gen.get("error")}

    tags = list(dict.fromkeys([t for t in (fm_tags + (gen.get("tags") or [])) if t]))
    return _save_document_with(
        f,
        doc_type=doc_type,
        platform=platform,
        language=language,
        title=title,
        source_url=source_url,
        text=body,
        summary=gen.get("resumo"),
        tutorial=gen.get("tutorial"),
        objectives="\n".join(gen.get("objetivos") or []),
        tags=tags,
        llm_provider=gen.get("provider"),
        llm_model=gen.get("model"),
    )


def _save_document_with(
    f: Path,
    *,
    doc_type: str,
    platform: str,
    language: str,
    title: str,
    source_url: str | None,
    text: str,
    summary: str | None,
    tutorial: str | None,
    objectives: str | None,
    tags: list[str],
    llm_provider: str | None,
    llm_model: str | None,
) -> dict[str, Any]:
    session = db.get_session()
    try:
        doc = db.save_document(
            session,
            type=doc_type,
            source_url=source_url,
            platform=platform,
            title=title,
            language=language,
            transcription_text=text,
            summary=summary,
            tutorial=tutorial,
            objectives=objectives,
            tags=tags,
            raw_file_path=str(f),
            llm_provider=llm_provider,
            llm_model=llm_model,
            embed_fn=llm.embed,
            embedding_model=llm.EMBEDDING_MODEL,
        )
        doc_dict = {
            "id": doc.id,
            "title": doc.title,
            "summary": doc.summary,
            "tutorial": doc.tutorial,
            "tags": doc.tags,
            "source_url": doc.source_url,
            "platform": doc.platform,
            "type": doc.type,
            "transcription_text": doc.transcription_text,
        }
    except Exception as e:
        session.rollback()
        return {"ok": False, "stage": "db", "error": str(e)}
    finally:
        session.close()

    vault_result = vault.write_markdown_copy(doc_dict, VAULT_PATH)
    return {
        "ok": True,
        "document_id": doc_dict["id"],
        "title": doc_dict["title"],
        "summary": doc_dict["summary"],
        "tags": doc_dict["tags"],
        "vault": vault_result,
    }
