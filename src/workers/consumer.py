"""Fila de trabalho do worker: comandos AMQP, estado, progresso e ritmo.

Extraído de `workers.ig_worker` (audit F1/SRP, responsabilidades de
ritmo/throttling e comandos de fila). Dono de: `apply_command`
(start/stop com basic_consume/basic_cancel), ack tolerante a falha,
requeue/DLQ de entrega falha, o arquivo de progresso `IG_STATE_FILE` e a
pausa de ritmo entre mensagens.

`pace_sleep_seconds` é puro de propósito — dá para testar o ritmo sem
fila, sem rede e sem GPU.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from core.settings.config import settings
from infra.queue import queue as ig_queue

logger = logging.getLogger(__name__)

# Ritmo mínimo entre mensagens, em segundos. 0 = comportamento antigo,
# consumir o mais rápido que a máquina aguentar.
IG_WORKER_MIN_INTERVAL = float(settings.IG_WORKER_MIN_INTERVAL or 0)


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


def safe_ack(ch: Any, method: Any) -> None:
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


def pace_after(started: float, pk: str | None) -> None:
    """Sleep out the rest of IG_WORKER_MIN_INTERVAL, if any is left."""
    pausa = pace_sleep_seconds(time.monotonic() - started)
    if pausa > 0:
        logger.info("pace: aguardando %.1fs antes do próximo (ig_pk=%s)", pausa, pk)
        time.sleep(pausa)


def record_progress(message: dict[str, Any], res: dict[str, Any]) -> None:
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


def requeue_or_dead_letter(
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
