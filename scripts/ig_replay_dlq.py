#!/usr/bin/env python3
"""Devolve as mensagens da DLQ para a fila de trabalho, uma vez.

Ordem de propósito: **grava em disco, publica, e só então dá o ack** na morta.
Um ack antes da publicação transforma qualquer soluço de rede em post perdido
sem cópia -- e a DLQ é justamente onde estão os que já falharam uma vez.

Deduplica por `ig_pk`: a DLQ tinha 95 mensagens para 66 posts distintos, porque
uma mensagem tentada N vezes chega lá N vezes. O worker pularia as repetidas
pelo `document_exists`, mas só depois de a primeira virar documento -- então
deduplicar aqui poupa um ciclo de fila por cópia.

    uv run python scripts/ig_replay_dlq.py            # relatório, sem mexer
    uv run python scripts/ig_replay_dlq.py --aplicar  # move de verdade

Migrado de minimax-video-factory/scripts/ig_replay_dlq.py em 2026-10-06 --
só o import mudou (minimax_mcp.ig_queue -> infra.queue.queue).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("replay")

from pika.adapters.blocking_connection import BlockingChannel  # noqa: E402
from pika.spec import Basic  # noqa: E402

from infra.queue import queue as ig_queue  # noqa: E402

APLICAR = "--aplicar" in sys.argv
MORTA = f"{ig_queue.QUEUE}.dead"
COPIA = Path(f"output/dlq-replay-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.jsonl")

conn = ig_queue.connect()
try:
    canal: BlockingChannel = conn.channel()
    ig_queue.declare(canal)

    vistos: set[str] = set()
    lidas = republicadas = repetidas = 0
    COPIA.parent.mkdir(parents=True, exist_ok=True)

    # **Nada de nack DENTRO do laço.** `nack(requeue=True)` devolve a mensagem
    # na hora e o `basic_get` seguinte pega a mesma: laço infinito, medido em
    # 2026-08-19 -- rodou 2 min e escreveu 341 MB da mesma dúzia de posts.
    # Segurando tudo sem ack até o fim, a fila esvazia do ponto de vista deste
    # canal, o laço termina, e aí se decide o destino de uma vez.
    Pendente = tuple[int, dict[str, Any] | str | None]
    pendentes: list[Pendente] = []

    with COPIA.open("a", encoding="utf-8") as arquivo:
        while True:
            # pika's basic_get stub is incomplete on some pyright pins (the
            # pre-commit hook's 1.1.409 vs. the 1.1.411+ this was written
            # against); cast() pins the real runtime type regardless of which
            # stub pyright loads. Same gap as basic_consume in ig_worker.py.
            metodo_raw, _props, corpo_raw = canal.basic_get(  # pyright: ignore
                queue=MORTA, auto_ack=False
            )
            # Redundant on newer pyright (which already infers these from the
            # stub), necessary on 1.1.409 (the pre-commit hook's pin) which
            # can't -- bare ignore covers whichever side flags it.
            metodo = cast(Basic.GetOk | None, metodo_raw)  # pyright: ignore
            corpo = cast(bytes | None, corpo_raw)  # pyright: ignore
            if metodo is None or corpo is None:
                break
            delivery_tag: int = metodo.delivery_tag
            lidas += 1
            arquivo.write(corpo.decode("utf-8", "replace") + "\n")

            try:
                msg: dict[str, Any] = json.loads(corpo)
            except ValueError:
                log.warning("mensagem ilegível na DLQ; será devolvida intacta")
                pendentes.append((delivery_tag, None))
                continue

            pk = str(msg.get("ig_pk") or "")
            if pk in vistos:
                repetidas += 1
                destino: str | None = "descartar" if APLICAR else None
                pendentes.append((delivery_tag, destino))
                continue
            vistos.add(pk)
            pendentes.append((delivery_tag, msg if APLICAR else None))

    if APLICAR:
        for tag, acao in pendentes:
            if acao is None:
                # Ilegível: fica na morta, para alguém olhar.
                canal.basic_nack(tag, requeue=True)
            elif isinstance(acao, str):  # == "descartar"
                canal.basic_ack(tag)  # cópia repetida do mesmo post
            else:
                # Publica ANTES do ack. Nesta ordem, uma falha aqui deixa a
                # mensagem na morta; na ordem inversa, sumiria.
                ig_queue.publish(canal, acao)
                canal.basic_ack(tag)
                republicadas += 1
    else:
        for tag, _ in pendentes:
            canal.basic_nack(tag, requeue=True)

    log.info("lidas da DLQ      : %d", lidas)
    log.info("posts distintos   : %d", len(vistos))
    log.info("repetidas          : %d", repetidas)
    log.info("republicadas      : %d", republicadas)
    log.info("cópia em          : %s", COPIA)
    if not APLICAR:
        log.info("ENSAIO -- nada foi movido. Rode com --aplicar.")
finally:
    ig_queue.close(conn)
