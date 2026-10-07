"""Leitura de tela da mídia: ffmpeg/ffprobe + OCR + fusão do texto.

Extraído de `workers.ig_worker` (audit F1/SRP, responsabilidade do grupo
ffmpeg+OCR+merge). Dono de: extrair quadros do vídeo, guardar a capa,
mesclar o texto dos quadros sem repetição e o `default_read_screen`
(imagem → OCR direto; vídeo → quadros → OCR → descrição da capa).

ffmpeg/ffprobe rodam com argv fixo, sem shell (nosec B603/B607).
"""

from __future__ import annotations

import logging
import shutil
import subprocess  # nosec B404 -- used below with a fixed argv, no shell
import tempfile
from pathlib import Path
from typing import Any

from core.settings.config import settings
from infra.llm import client as llm
from workers.media_download import classify_file

logger = logging.getLogger(__name__)

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


def default_read_screen(video_path: str) -> dict[str, Any]:
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
