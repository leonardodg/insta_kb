"""Audio transcriber using faster-whisper (local, GPU-accelerated).

Migrated verbatim from minimax-video-factory's `minimax_mcp/transcriber.py`.
This is a standalone copy by design -- the task explicitly calls for insta_kb
to own its own transcriber instead of depending on the video-factory repo.
No config reads here (model_size/device/compute_type are constructor params,
as in the original); defaults match the source module 1:1.
"""

from __future__ import annotations

import gc
import logging
from pathlib import Path
from typing import Any, ClassVar

logger = logging.getLogger(__name__)

try:
    from faster_whisper import WhisperModel  # pyright: ignore[reportMissingTypeStubs]
except ImportError:
    WhisperModel = None

try:
    import av
except ImportError:
    av = None


def tem_faixa_de_audio(video_path: str | Path) -> bool | None:
    """O arquivo tem faixa de áudio? `None` quando não deu para saber.

    Três estados, e o terceiro importa: "não tem áudio" e "não consegui abrir
    o arquivo" pedem reações opostas -- a primeira segue sem transcrever, a
    segunda deixa o Whisper tentar e falhar com o erro dele, que é informativo.
    Colapsar as duas em `False` faria um arquivo corrompido virar,
    silenciosamente, um post "sem fala".

    `av` vem junto com o faster-whisper, então não é dependência nova.
    """
    if av is None:
        logger.debug("av não está instalado; não dá para inspecionar %s", video_path)
        return None
    try:
        with av.open(str(video_path)) as container:
            return bool(container.streams.audio)
    except Exception as exc:
        logger.debug("não consegui inspecionar as faixas de %s: %s", video_path, exc)
        return None


class AudioTranscriber:
    """Transcribes audio from video files using faster-whisper (local, GPU)."""

    _model_cache: ClassVar[dict[str, Any]] = {}

    def __init__(
        self,
        model_size: str = "small",
        device: str = "cuda",
        compute_type: str = "float16",
        download_root: str | Path | None = None,
    ):
        """
        Args:
            model_size: Whisper model size (tiny, base, small, medium, large-v3)
            device: cuda or cpu
            compute_type: float16 (GPU), int8 (CPU), float32
            download_root: Where to cache downloaded models
        """
        if WhisperModel is None:
            raise RuntimeError(
                "faster-whisper not installed. Run: pip install faster-whisper"
            )

        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.download_root = Path(download_root) if download_root else None

    def _get_model(self) -> Any:
        """Get or create cached WhisperModel instance."""
        if WhisperModel is None:
            raise RuntimeError(
                "faster-whisper not installed. Run: pip install faster-whisper"
            )
        cache_key = f"{self.model_size}-{self.device}-{self.compute_type}"
        if cache_key not in self._model_cache:
            logger.info(
                "Loading Whisper model: %s on %s (%s)",
                self.model_size,
                self.device,
                self.compute_type,
            )
            kwargs: dict[str, Any] = {
                "device": self.device,
                "compute_type": self.compute_type,
            }
            if self.download_root:
                kwargs["download_root"] = str(self.download_root)
            try:
                self._model_cache[cache_key] = WhisperModel(self.model_size, **kwargs)
            except RuntimeError as e:
                # A busy card is not a reason to refuse to transcribe. The GPU
                # is routinely occupied here -- by a render, or by Ollama, which
                # holds ~10 GB after any knowledge-base call -- and the CPU can
                # do this job, just slower. Only fall back for memory, since
                # other RuntimeErrors mean something actually broke.
                if self.device == "cpu" or "out of memory" not in str(e).lower():
                    raise
                logger.warning(
                    "Whisper could not load on %s (%s). Falling back to "
                    "CPU/int8 -- slower, but the GPU is occupied.",
                    self.device,
                    e,
                )
                kwargs["device"] = "cpu"
                kwargs["compute_type"] = "int8"
                self._model_cache[cache_key] = WhisperModel(self.model_size, **kwargs)
        return self._model_cache[cache_key]

    def transcribe(
        self,
        video_path: str | Path,
        language: str = "pt",
        beam_size: int = 5,
        vad_filter: bool = True,
        word_timestamps: bool = False,
    ) -> dict[str, Any]:
        """
        Transcribe audio from a video file.

        Args:
            video_path: Path to video/audio file
            language: Language code (pt for Portuguese)
            beam_size: Beam size for decoding
            vad_filter: Use voice activity detection
            word_timestamps: Include word-level timestamps

        Returns:
            Dict with transcription text, segments, and metadata.
        """
        video_path = Path(video_path)
        if not video_path.exists():
            return {"ok": False, "error": f"File not found: {video_path}"}

        # Vídeo SEM faixa de áudio não é falha, é ausência de fala -- e a
        # diferença decidia se o post existia. O faster-whisper pede a faixa de
        # áudio pelo índice, e num arquivo que só tem vídeo o PyAV levanta
        # `IndexError: tuple index out of range` (av/container/streams.py:118).
        # Isso subia como "Transcription failed", e o `process_message` aborta
        # em transcrição falha -- ANTES da cadeia que já existe logo abaixo
        # dele, que faz um vídeo mudo virar documento pela tela e pela
        # legenda.
        #
        # Devolver ok=True com texto vazio é o que entrega o post a essa
        # cadeia: é o mesmo estado de um reel de música, que ela já trata.
        if tem_faixa_de_audio(video_path) is False:
            logger.info("%s não tem faixa de áudio; sem fala a transcrever", video_path)
            return {
                "ok": True,
                "text": "",
                "segments": [],
                "language": language,
                "language_probability": 0.0,
                "duration": 0.0,
                "sem_audio": True,
            }

        logger.info("Transcribing %s with %s model", video_path, self.model_size)

        try:
            model = self._get_model()
            segments, info = model.transcribe(
                str(video_path),
                language=language,
                beam_size=beam_size,
                vad_filter=vad_filter,
                word_timestamps=word_timestamps,
            )

            segments_list: list[dict[str, Any]] = []
            full_text: list[str] = []
            for seg in segments:
                segment_data = {
                    "start": seg.start,
                    "end": seg.end,
                    "text": seg.text.strip(),
                }
                if word_timestamps and seg.words:
                    segment_data["words"] = [
                        {
                            "word": w.word,
                            "start": w.start,
                            "end": w.end,
                            "probability": w.probability,
                        }
                        for w in seg.words
                    ]
                segments_list.append(segment_data)
                full_text.append(
                    f"[{seg.start:.2f}s - {seg.end:.2f}s] {seg.text.strip()}"
                )

            return {
                "ok": True,
                "text": "\n".join(full_text),
                "segments": segments_list,
                "language": info.language,
                "language_probability": info.language_probability,
                "duration": info.duration,
            }

        except Exception as e:
            logger.exception("Transcription failed for %s", video_path)
            return {"ok": False, "error": f"Transcription failed: {e!s}"}

    def free(self) -> None:
        """Unload the cached Whisper model and release the VRAM it occupies.

        The model lives in a class-level cache so repeated transcriptions skip
        the load; but a loaded whisper keeps its weights resident on the GPU,
        which can starve Ollama (lfm2:24b needs ~6 GB) on the same 12 GB card.
        Call this between the Whisper step and the LLM step in a long pipeline.

        Dropping the cache entry is what actually frees the memory. The model
        is a CTranslate2 object that owns its CUDA allocation and releases it
        from its destructor, so the VRAM comes back when the last reference
        goes away; `gc.collect()` covers the case where a reference cycle keeps
        it alive past the `del`.

        Deliberately no `torch.cuda.empty_cache()` here. faster-whisper does
        not allocate through PyTorch, so that call frees nothing it holds --
        and torch is imported nowhere else in this project, so importing it
        just to make the call would initialise a CUDA context worth a few
        hundred MB on the very card this method exists to free.
        """
        cache_key = f"{self.model_size}-{self.device}-{self.compute_type}"
        self._model_cache.pop(cache_key, None)
        gc.collect()


def transcribe_video(
    video_path: str | Path,
    model_size: str = "small",
    device: str = "cuda",
    language: str = "pt",
) -> dict[str, Any]:
    """Convenience function for single transcription."""
    transcriber = AudioTranscriber(model_size=model_size, device=device)
    return transcriber.transcribe(video_path, language=language)
