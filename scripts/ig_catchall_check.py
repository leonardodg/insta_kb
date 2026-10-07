#!/usr/bin/env python3
"""Enumera SO' a catch-all ("All posts") e mede se a tolerancia por item pegou.

Por que so' a catch-all: e' a unica colecao que lista TODOS os salvos, e e' a
unica que morria. As 51 nomeadas ja foram varridas hoje as 11:13 e nao tem nada
de novo a dizer -- repeti-las seria pagar ~350 requisicoes ao Instagram de
graca, no mesmo dia, que e' o padrao culpado pelo 429 de 2026-08-09.

**Nao publica nada por padrao.** A enumeracao inteira e' gravada em JSON, entao
a recuperacao depois nao custa nenhuma requisicao nova. Publicar so' com
`--publicar`, e ai vale a nota do HANDOFF: `existing_pks` conhece o banco, nao
a fila, entao o que ainda esta' pendente seria republicado.

Migrado de minimax-video-factory/scripts/ig_catchall_check.py em 2026-10-06 --
só os imports mudaram (minimax_mcp.{db,ig_sync,ig_queue} -> infra.db,
infra.instagram.ig_sync, infra.queue.queue).
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("catchall")

from infra import db  # noqa: E402
from infra.instagram import ig_sync  # noqa: E402

PUBLICAR = "--publicar" in sys.argv
DESTINO = Path("output/ig-sync-2026-08-18/catchall.json")

if not ig_sync.IG_SESSIONID:
    log.error(ig_sync.SESSIONID_MISSING)
    raise SystemExit(2)

session = db.get_session()
try:
    existing = db.list_ig_pks(session)
finally:
    session.close()
log.info("ja no banco: %d ig_pk", len(existing))

client = ig_sync.make_client()

cols = list(client.collections())
catch_all = [c for c in cols if getattr(c, "type", "") == "ALL_MEDIA_AUTO_COLLECTION"]
if not catch_all:
    log.error("nenhuma colecao ALL_MEDIA_AUTO_COLLECTION entre as %d", len(cols))
    raise SystemExit(3)
col = catch_all[0]
log.info(
    "catch-all id=%s nome=%r -- enumerando TODAS as paginas",
    col.id,
    getattr(col, "name", ""),
)

# O ponto do teste: antes, um item sem `code` levava daqui uma excecao e a
# colecao inteira sumia. Agora o item ruim vira uma linha no descartados.jsonl.
medias = list(client.collection_medias(col.id, amount=0))
log.info("ENUMERADOS %d posts; descartados %d", len(medias), len(client.descartados))

mensagens = ig_sync.dedupe_by_pk(
    ig_sync.to_messages([{"media": m, "collection_name": None} for m in medias])
)
novos = [m for m in mensagens if m["ig_pk"] not in existing]

DESTINO.parent.mkdir(parents=True, exist_ok=True)
DESTINO.write_text(
    json.dumps(mensagens, ensure_ascii=False, indent=2), encoding="utf-8"
)

log.info("=" * 60)
log.info("enumerados na catch-all : %d", len(medias))
log.info("mensagens (dedup)       : %d", len(mensagens))
log.info("ja no banco             : %d", len(mensagens) - len(novos))
log.info("NOVOS (nao no banco)    : %d", len(novos))
log.info("DESCARTADOS             : %d", len(client.descartados))
for d in client.descartados:
    log.info("  descartado ig_pk=%s: %s", d["ig_pk"], d["erro"])
log.info("enumeracao salva em     : %s", DESTINO)
log.info("=" * 60)

if not PUBLICAR:
    log.info("nada publicado (rode com --publicar para enfileirar os novos)")
    raise SystemExit(0)

from infra.queue import queue as ig_queue  # noqa: E402

conn = ig_queue.connect()
try:
    channel = conn.channel()
    ig_queue.declare(channel)
    for msg in novos:
        ig_queue.publish(channel, msg)
    log.info("PUBLICADOS %d na fila", len(novos))
finally:
    ig_queue.close(conn)
