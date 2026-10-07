#!/usr/bin/env python3
"""Quais documentos ainda faltam reprocessar. Escreve a lista e a imprime.

Distinguir "já refeito" de "ainda antigo" precisa de DOIS sinais, e usar só um
dá resposta errada nas duas direções -- aconteceu comigo em 2026-08-11:

  só `id > 369`        -> conta como pronto quem falhou e foi RESTAURADO com o
                          conteúdo antigo (o restauro grava id novo)
  só `llm_provider`    -> conta como pronto todo mundo, porque a ingestão
                          ORIGINAL também passou pelo ingest_text e preencheu
                          esse campo

O critério certo combina os dois: tocado pelo lote (id novo) E vindo do
ingest_text (llm_provider preenchido). O restauro usa save_document com
llm_provider=None justamente para deixar esse rastro.

Migrado de minimax-video-factory/scripts/ig_pendentes.py em 2026-10-06 --
só o import de `ig_worker` e o nome da env var do banco mudaram.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import create_engine, text

from workers import ig_worker

# Maior id existente antes do lote de reprocessamento começar.
CORTE = int(os.environ.get("IG_ID_CORTE", "369"))
LISTA = Path("output/ig-sync-2026-08-10/ids_restam.txt")

e = create_engine(os.environ["DATABASE_URL"])
with e.connect() as c:
    linhas = c.execute(
        text(
            "SELECT id, ig_pk, llm_provider FROM documents "
            "WHERE ig_pk IS NOT NULL ORDER BY id"
        )
    ).fetchall()

pk_de = {i: pk for i, pk, _ in linhas}
refeitos = [i for i, _, prov in linhas if i > CORTE and prov]
restaurados = [i for i, _, prov in linhas if i > CORTE and not prov]
intocados = [i for i, _, _prov in linhas if i <= CORTE]

candidatos = intocados + restaurados
faltam = sorted(i for i in candidatos if ig_worker.existing_media(pk_de[i]))
sem_midia = sorted(i for i in candidatos if not ig_worker.existing_media(pk_de[i]))

print(f"total de documentos de IG      : {len(linhas)}")
print(f"  reprocessados com sucesso    : {len(refeitos)}")
print(f"  restaurados após falha       : {len(restaurados)} {restaurados}")
print(f"  ainda intocados              : {len(intocados)}")
print(f"  SEM mídia (não dá sem baixar): {len(sem_midia)}")
print(f"  A REPROCESSAR                : {len(faltam)}")

LISTA.parent.mkdir(parents=True, exist_ok=True)
LISTA.write_text(" ".join(map(str, faltam)), encoding="utf-8")
print(f"lista gravada em {LISTA}")
