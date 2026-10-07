#!/usr/bin/env python3
"""Reprocessa documentos de IG com o pipeline atual, a partir da mídia no disco.

Sem `--aplicar` só compara e nada muda no banco -- é a validação que o usuário
pediu antes de autorizar o replace.

Nunca toca o Instagram: a mídia está em downloads/ig/<ig_pk>/, que é a razão de
ela ter deixado de ser apagada. Também não precisa da pausa de 90 s do worker:
ela existe para espaçar requisições que aqui não existem.

Migrado de minimax-video-factory/scripts/ig_reprocessar.py em 2026-10-06 (o
KB e o pipeline de Instagram passaram a viver só aqui) -- só os imports e o
nome da env var do banco mudaram (KB_DATABASE_URL -> DATABASE_URL, o nome que
este projeto usa). Script histórico de uma campanha de reprocessamento
pontual (2026-08-10/11); não tem teste automatizado, e não devia precisar --
roda contra dados reais sob --aplicar, de propósito, com backup antes de
qualquer escrita.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import create_engine, text

from core.contracts import DocumentDraft
from core.knowledge import knowledge
from infra import db
from infra.instagram import ig_sync
from infra.llm import client as llm
from workers import ig_worker

BACKUP = Path("output/kb-backup-reprocess")
BACKUP.mkdir(parents=True, exist_ok=True)
APLICAR = "--aplicar" in sys.argv
IDS = [int(a) for a in sys.argv[1:] if a.isdigit()]

_CLIENTE: list[Any] = []


def _cliente() -> Any:
    """Um cliente do instagrapi, criado na primeira vez que fizer falta.

    Preguiçoso porque a maioria dos reprocessamentos não precisa da rede, e
    fazer login no início cobraria requisição de quem não vai usá-la.

    **A falha é lembrada.** Com o IG_SESSIONID expirado cada tentativa custa
    trinta redirecionamentos até morrer em `TooManyRedirects`; repetir isso uma
    vez por documento transformaria um lote de 76 numa tempestade de milhares
    de requisições contra a página de login -- que é exatamente o padrão que
    faz a conta ser bloqueada de vez. Uma tentativa, e o resto do lote segue
    com o título como reserva.
    """
    if _CLIENTE:
        if _CLIENTE[0] is None:
            raise RuntimeError("sessão do Instagram indisponível (falhou antes)")
        return _CLIENTE[0]
    try:
        _CLIENTE.append(ig_sync.make_client())
    except Exception:
        _CLIENTE.append(None)
        raise
    return _CLIENTE[0]


e = create_engine(os.environ["DATABASE_URL"])
COLS = (
    "id, ig_pk, type, title, summary, tutorial, objectives, tags, "
    "transcription_text, source_url, language, raw_file_path"
)


def carregar(did: int) -> dict[str, Any] | None:
    with e.connect() as c:
        # nosec B608 -- COLS is a fixed literal above, not user input; :i is bound.
        query = text(f"SELECT {COLS} FROM documents WHERE id=:i")  # nosec B608
        r = c.execute(query, {"i": did}).fetchone()
    return dict(zip(COLS.replace(" ", "").split(","), r)) if r else None


def colecao_de(tags: list[Any] | None) -> str | None:
    for t in tags or []:
        if str(t).startswith("colecao:"):
            return str(t).split(":", 1)[1]
    return None


# A comparação completa, para leitura humana. A rodada anterior só imprimiu
# trechos cortados em 150-220 caracteres, e o usuário precisava do conteúdo
# inteiro para julgar -- que é a única coisa que decide se o lote vale a pena.
COMPARACAO: list[dict[str, Any]] = []

ok_n = falha_n = 0
for did in IDS:
    d = carregar(did)
    if not d:
        print(f"doc {did}: não existe")
        continue
    pk = d["ig_pk"]
    print("=" * 78)
    print(f"DOC {did}  pk={pk}")

    # Backup ANTES de qualquer coisa, mesmo em modo comparação: se o --aplicar
    # vier depois, o snapshot já está lá e não depende de eu lembrar.
    (BACKUP / f"{did}.json").write_text(
        json.dumps(d, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    midia = ig_worker.existing_media(pk)
    video = next(
        (m for m in midia if m.lower().endswith((".mp4", ".mkv", ".webm"))), None
    )
    fotos = [
        m
        for m in midia
        if m.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        and not m.endswith("capa.jpg")
    ]
    if not video and not fotos:
        print("  SEM MÍDIA no disco -- pulando (seria preciso baixar de novo)")
        falha_n += 1
        continue

    t0 = time.time()
    if video:
        tr = ig_worker._default_transcribe(video)  # pyright: ignore[reportPrivateUsage] -- no public equivalent; this is the pipeline's default injection point
        fala = (tr.get("text") or "").strip() if tr.get("ok") else ""
        rs = ig_worker._default_read_screen(video)  # pyright: ignore[reportPrivateUsage] -- same reasoning
        tela = (rs.get("text") or "").strip() if rs.get("ok") else ""
        if fala and tela:
            conteudo = f"{fala}\n\n--- texto na tela ---\n{tela}"
        elif tela:
            conteudo = f"--- texto na tela ---\n{tela}"
        else:
            conteudo = fala
        doc_type, lang = "video", (tr.get("language") or "pt")
    else:
        # Espelha o ramo de imagem do process_message: DESCREVER e LER são
        # coisas diferentes. Uma versão anterior deste script só descrevia, e
        # por isso a validação do post 3678218579439466538 não testou nada do
        # conserto que ela deveria testar -- reportou "tela lida: 0 chars" e eu
        # quase li isso como fracasso do worker, quando era defeito do medidor.
        pedacos: list[str] = []
        escritos: list[str] = []
        for f in fotos:
            de = llm.describe_image(f)
            if de.get("ok"):
                pedacos.append(de.get("conteudo_principal") or de.get("text") or "")
            rs = ig_worker._default_read_screen(f)  # pyright: ignore[reportPrivateUsage] -- same reasoning
            if rs.get("ok") and (rs.get("text") or "").strip():
                escritos.append(rs["text"].strip())
        conteudo = "\n\n".join(p for p in pedacos if p)
        tela = ig_worker.merge_screen_text(escritos)
        if tela:
            conteudo = f"{conteudo}\n\n--- texto na imagem ---\n{tela}".strip()
        doc_type, lang = "image", "pt"

    # A LEGENDA. Sem ela este script reproduzia o defeito que o reprocessamento
    # existe para consertar: a mídia vem do disco, não há `media_info`, e o
    # texto ia para o modelo sem o que o autor escreveu. Medido em 2026-08-19:
    # em 21 de 24 documentos de vídeo mudo a legenda nunca chegou ao LLM.
    #
    # Uma requisição por post, e só aqui -- o `media_info` é a chamada
    # autenticada que o resto do reprocessamento faz questão de evitar. Vale
    # porque é exatamente o material que falta; falhar nela não pode custar o
    # documento, então o título do banco fica de reserva.
    legenda = ""
    try:
        legenda = (getattr(_cliente(), "media_info")(pk).caption_text or "").strip()
    except Exception as exc:
        print(f"  media_info falhou ({exc}); usando o título como reserva")
        legenda = (d.get("title") or "").strip()
    if legenda:
        conteudo = f"{conteudo}\n\n--- legenda ---\n{legenda}".strip()

    conteudo = ig_worker.strip_cta(conteudo)
    if not conteudo.strip():
        print("  conteúdo vazio depois do processamento -- pulando")
        falha_n += 1
        continue

    gen = llm.generate_structured(
        conteudo, is_image=(doc_type == "image"), categories=None
    )
    dur = time.time() - t0
    if not gen.get("ok"):
        print(f"  FALHOU: {gen.get('error')}")
        falha_n += 1
        continue

    # Quanto de invenção havia ANTES, medido com a mesma régua.
    _, inv_antes = llm.ancorar_codigo(
        d["tutorial"] or "", d["transcription_text"] or ""
    )
    inv_depois = gen.get("codigo_removido", 0)
    titulo_novo = ig_worker.clean_title(d["title"])

    print(
        f"  {dur:.0f}s · tela lida: {len(tela)} chars · "
        f"trechos removidos agora: {inv_depois}"
    )
    print(f"  invenção ANTES (mesma régua): {inv_antes} trecho(s)")
    print(f"  título: {str(d['title'])[:60]!r}")
    print(f"       -> {str(titulo_novo)[:60]!r}")
    print(f"  resumo ANTES : {str(d['summary'])[:150]}")
    print(f"  resumo DEPOIS: {str(gen.get('resumo'))[:150]}")
    tut = gen.get("tutorial") or ""
    print(f"  tutorial DEPOIS ({len(tut)} chars): {tut[:220]!r}")

    COMPARACAO.append(
        {
            "doc": did,
            "ig_pk": pk,
            "pasta": f"downloads/ig/{pk}/",
            "link": ig_sync.post_url(pk),
            "tipo": doc_type,
            "titulo_antes": d["title"],
            "titulo_depois": titulo_novo,
            "invencao_antes": inv_antes,
            "invencao_depois": inv_depois,
            "tela_lida_chars": len(tela),
            "resumo_antes": d["summary"],
            "resumo_depois": gen.get("resumo"),
            "tutorial_antes": d["tutorial"],
            "tutorial_depois": tut,
            "tags_antes": d["tags"],
            "tags_depois": gen.get("tags"),
            "material": conteudo,
        }
    )

    if APLICAR:
        # Só o documento: as FKs são ON DELETE CASCADE (documents -> chunks ->
        # embeddings), verificado no information_schema. Apagar chunks à mão
        # antes seria redundante e mascararia uma mudança futura de esquema.
        with e.begin() as c:
            c.execute(text("DELETE FROM documents WHERE id=:i"), {"i": did})
        col = colecao_de(d["tags"])
        novo = knowledge.ingest_text(
            conteudo,
            source_url=ig_sync.post_url(pk),
            title=titulo_novo,
            platform="instagram",
            doc_type=doc_type,
            language=lang,
            ig_pk=pk,
            extra_tags=[f"colecao:{col}"] if col else None,
            raw_file_path=video or (fotos[0] if fotos else None),
        )
        if novo.get("ok"):
            print(f"  APLICADO -> novo document_id={novo['document_id']}")
            ok_n += 1
        else:
            # RESTAURA pelo ORM, não por INSERT cru. A primeira versão disto usava
            # SQL direto e APAGOU um documento de verdade ao ser testada: `tags` é
            # coluna JSON, uma lista Python vira array do Postgres (`{a,b}`) e o
            # INSERT falha com "invalid input syntax for type json". O documento
            # já estava deletado, e só havia backup por sorte.
            #
            # `save_document` trata os tipos e ainda reconstrói chunks e
            # embeddings, que o INSERT cru deixaria de fora -- o documento
            # voltaria invisível para a busca.
            s2 = db.get_session()
            try:
                draft: DocumentDraft = {
                    "type": d["type"],
                    "source_url": d["source_url"],
                    "platform": "instagram",
                    "title": d["title"],
                    "language": d.get("language") or "pt",
                    "transcription_text": d["transcription_text"],
                    "summary": d["summary"],
                    "tutorial": d["tutorial"],
                    "objectives": d["objectives"],
                    "tags": d["tags"],
                    "raw_file_path": d.get("raw_file_path"),
                    "llm_provider": None,
                    "llm_model": None,
                    "ig_pk": pk,
                }
                rec = db.save_document(
                    s2,
                    draft,
                    embed_fn=llm.embed,
                    embedding_model=llm.EMBEDDING_MODEL,
                )
                print(
                    f"  *** FALHA AO REGRAVAR: {novo.get('error')} — "
                    f"RESTAURADO como id={rec.id}"
                )
            except Exception as exc:
                print(
                    f"  *** FALHA AO REGRAVAR E AO RESTAURAR: {exc} — "
                    f"veja {BACKUP}/{did}.json"
                )
            finally:
                s2.close()
            falha_n += 1
    else:
        ok_n += 1

# Grava a comparação inteira: JSON para reprocessar, markdown para ler.
if COMPARACAO:
    Path("output/comparacao-10.json").write_text(
        json.dumps(COMPARACAO, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    linhas = ["# Comparação — os 10 documentos, antes e depois", ""]
    for c in COMPARACAO:
        linhas += [
            f"## doc {c['doc']} — {c['tipo']}",
            "",
            f"- **pasta local**: `{c['pasta']}`",
            f"- **link**: {c['link']}",
            f"- **título antes**: {c['titulo_antes']}",
            f"- **título depois**: {c['titulo_depois']}",
            f"- **código inventado**: {c['invencao_antes']} → {c['invencao_depois']}",
            f"- **texto lido da tela/imagem**: {c['tela_lida_chars']} chars",
            f"- **tags antes**: {c['tags_antes']}",
            f"- **tags depois**: {c['tags_depois']}",
            "",
            "### O que é o conteúdo (resumo)",
            "",
            f"**ANTES**\n\n{c['resumo_antes']}",
            "",
            f"**DEPOIS**\n\n{c['resumo_depois']}",
            "",
            "### Conteúdo principal (tutorial)",
            "",
            f"**ANTES**\n\n```\n{c['tutorial_antes']}\n```",
            "",
            f"**DEPOIS**\n\n```\n{c['tutorial_depois']}\n```",
            "",
            "---",
            "",
        ]
    Path("output/comparacao-10.md").write_text("\n".join(linhas), encoding="utf-8")
    print(
        f"comparação completa: output/comparacao-10.md e .json "
        f"({len(COMPARACAO)} documentos)"
    )

print("=" * 78)
print(f"{ok_n} ok, {falha_n} com problema · backups em {BACKUP}")
print("MODO APLICAR" if APLICAR else "modo comparação: NADA foi gravado")
