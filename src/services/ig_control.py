"""Instagram worker control plane: queue status, control commands, progress.

Moved out of `mcp_server.server` (SOLID audit #2): the REST API and the MCP
server both expose these operations, so the logic lives in the application
layer and both adapters call it directly -- no adapter-to-adapter import.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.settings.config import settings
from infra import db
from infra.queue import queue as ig_queue

logger = logging.getLogger(__name__)


def queue_status() -> dict[str, Any]:
    """Tamanho da fila ig.saved (ready/dead) e quantos consumidores ativos."""
    conn = None
    try:
        conn = ig_queue.connect()
        return ig_queue.queue_status(conn.channel())
    except Exception as e:
        return {"ok": False, "error": f"ig_queue_status failed: {e}"}
    finally:
        if conn is not None:
            ig_queue.close(conn)


def publish_control_command(command: str) -> dict[str, Any]:
    """Publish a start/stop command to CONTROL_QUEUE for the ig-worker."""
    conn = None
    try:
        conn = ig_queue.connect()
        channel = conn.channel()
        ig_queue.declare(channel)
        channel.basic_publish(
            exchange="",
            routing_key=ig_queue.CONTROL_QUEUE,
            body=json.dumps({"command": command}),
            properties=None,
        )
        return {"ok": True, "command": command}
    except Exception as e:
        return {"ok": False, "error": f"ig_worker_{command} failed: {e}"}
    finally:
        if conn is not None:
            ig_queue.close(conn)


def get_progress(last_n: int = 10) -> dict[str, Any]:
    """Últimos N posts processados pelo ig-worker (state file) + contagem."""
    entries: list[dict[str, Any]] = []
    try:
        state_path = Path(settings.IG_STATE_FILE)
        if state_path.exists():
            entries = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("could not read %s: %s", settings.IG_STATE_FILE, exc)
        entries = []
    try:
        session = db.get_session()
        try:
            total_ig = len(db.list_ig_pks(session))
        finally:
            session.close()
    except Exception as e:
        return {"ok": False, "error": f"ig_get_progress failed: {e}"}
    return {"ok": True, "last": entries[-last_n:], "documents_with_ig_pk": total_ig}
