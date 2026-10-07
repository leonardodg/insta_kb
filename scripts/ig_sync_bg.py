#!/usr/bin/env python3
"""Roda a enumeracao dos salvos e publica na fila, com log por colecao.

Existe separado da tool MCP porque a tool morre no corte de 1800 s: a catch-all
tinha 3618 posts na medicao de 2026-08-10 e a varredura leva ~30 min. Aqui o
processo e' de background e o log e' o sinal de vida.

Migrado de minimax-video-factory/scripts/ig_sync_bg.py em 2026-10-06 -- só os
imports mudaram (minimax_mcp.{db,ig_queue,ig_sync} -> infra.db,
infra.queue.queue, infra.instagram.ig_sync).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("ig_sync_bg")

from infra import db  # noqa: E402
from infra.instagram import ig_sync  # noqa: E402
from infra.queue import queue as ig_queue  # noqa: E402

if not ig_sync.IG_SESSIONID:
    log.error(ig_sync.SESSIONID_MISSING)
    raise SystemExit(2)

conn = ig_queue.connect()
try:
    channel = conn.channel()
    ig_queue.declare(channel)
    session = db.get_session()
    try:
        existing = db.list_ig_pks(session)
    finally:
        session.close()
    log.info("ja no banco: %d ig_pk", len(existing))

    client = ig_sync.make_client()

    def progresso(ev: dict[str, Any]) -> None:
        log.info(
            "colecao %s -> %d posts, %d publicados, %d pulados (acumulado %d)",
            ev["collection"],
            ev["in_collection"],
            ev["published"],
            ev["skipped"],
            ev["published_total"],
        )

    res = ig_sync.sync_saved_posts(
        client,
        existing_pks=existing,
        publish_fn=lambda msg: ig_queue.publish(channel, msg),
        progress_fn=progresso,
    )
    log.info("RESULTADO %s", res)
finally:
    ig_queue.close(conn)
