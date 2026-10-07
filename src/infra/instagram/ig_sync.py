"""Enumerate the user's Instagram saved posts and publish them to the queue.

Migrated from minimax-video-factory's `minimax_mcp/ig_sync.py`. instagrapi is
used only here (enumeration) — the worker downloads by pk via yt-dlp/
instagrapi directly, so IG_SESSIONID is only needed for enumeration.
Business logic (to_messages/split_new) is pure and unit-tested with
duck-typed Media objects.

Config reads (IG_SESSIONID, IG_DESCARTADOS_FILE) now come from
`core.settings.config.settings` instead of `os.environ` directly.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.settings.config import settings

logger = logging.getLogger(__name__)

IG_SESSIONID = settings.IG_SESSIONID
SESSIONID_MISSING = "IG_SESSIONID not configured"

# Onde ficam os posts que a listagem não conseguiu converter. Um por linha, com
# o payload cru: é a única cópia que sobra deles, e sem ela "descartei 3" é uma
# afirmação que não dá para conferir depois.
DESCARTADOS_FILE = settings.IG_DESCARTADOS_FILE

# Os campos que o `Media` do instagrapi exige. Registrar QUAL faltou é o que
# transforma o descarte em diagnóstico -- em 2026-08-10 era o `code`.
CAMPOS_OBRIGATORIOS = ("pk", "id", "code", "taken_at", "media_type", "user")

# instagrapi MediaType values: 1 = image, 2 = video, 8 = album/carousel
MEDIA_TYPES = {1: "image", 2: "video", 8: "carousel"}

# Auto-collections that are not real categories; kept out of the vocabulary.
AUTO_COLLECTION_NAMES = {"all posts", "todos os posts", "all", "todos"}

# Static fallback used when collections are unavailable (offline, API error).
FALLBACK_CATEGORIES = [
    "receita",
    "dica",
    "tutorial",
    "tech",
    "curso",
    "estudo",
    "inglês",
    "viagem",
    "house",
    "bitcoin",
    "treino",
    "car",
    "dog",
    "livro",
    "notícia",
    "outros",
]


def list_categories(client: Any) -> list[str]:
    """Normalized unique collection names — the category vocabulary for vision.

    Auto-collections ("All posts"/"Todos os posts") are excluded. Names are
    stripped, inner whitespace collapsed, and deduplicated case-insensitively
    keeping the first spelling. Any failure (or a None client) returns the
    static fallback list, never raises.
    """
    if client is None:
        return list(FALLBACK_CATEGORIES)
    try:
        names: list[str] = []
        seen: set[str] = set()
        for col in client.collections():
            raw = (getattr(col, "name", "") or "").strip()
            key = " ".join(raw.lower().split())
            if not key or key in AUTO_COLLECTION_NAMES or key in seen:
                continue
            seen.add(key)
            names.append(" ".join(raw.split()))
        return names or list(FALLBACK_CATEGORIES)
    except Exception:
        return list(FALLBACK_CATEGORIES)


def post_url(pk: Any, code: str | None = None) -> str:
    """O link do post. `/p/` quer o SHORTCODE, não o pk numérico.

    Estava montado como `/p/{pk}/`, e isso quebrava duas coisas ao mesmo tempo:
    o `source_url` de todo documento apontava para um link que não abre, e o
    fallback de yt-dlp -- que recebe essa URL quando o download autenticado
    falha -- levava HTTP 400 e nunca poderia funcionar.

    Sem `code` à mão, o shortcode se CALCULA a partir do pk: é o mesmo número
    noutra base, e o instagrapi traz o codec. Nenhuma requisição de rede -- o
    que importa porque isso corrige em massa documentos já gravados.
    """
    if code:
        return f"https://www.instagram.com/p/{code}/"
    try:
        # Lazy on purpose (PLC0415): instagrapi is only needed for this one
        # fallback path, and most callers of this module never hit it.
        from instagrapi.utils import InstagramIdCodec  # noqa: PLC0415

        return f"https://www.instagram.com/p/{InstagramIdCodec.encode(int(pk))}/"
    except Exception:
        # Um link torto é melhor que uma exceção no meio da listagem.
        return f"https://www.instagram.com/p/{pk}/"


def parse_items_tolerant(
    raw_items: list[dict[str, Any]],
    extract: Callable[[dict[str, Any]], Any],
    on_discard: Callable[[dict[str, Any], Exception], None] | None = None,
) -> list[Any]:
    """Converte item a item, e um item podre não leva os outros junto.

    Um try/except por item só funciona no ponto em que o item ainda existe --
    quando a exceção sobe por um laço de paginação externo, a lista inteira já
    foi perdida. O `except Exception` é largo de propósito: o que se sabe é
    que um payload degradado não deve custar a coleção; nada é engolido em
    silêncio -- todo descarte vira WARNING e vira linha no `DESCARTADOS_FILE`.
    """
    parsed: list[Any] = []
    for raw in raw_items:
        try:
            parsed.append(extract(raw))
        except Exception as exc:
            faltando = [c for c in CAMPOS_OBRIGATORIOS if c not in (raw or {})]
            logger.warning(
                "post descartado na listagem (pk=%s, faltando=%s): %s",
                (raw or {}).get("pk") or (raw or {}).get("id"),
                faltando or "-",
                exc,
            )
            if on_discard is not None:
                try:
                    on_discard(raw, exc)
                except Exception as reg:
                    # Falhar ao ARQUIVAR o descarte não pode custar a listagem
                    # -- seria trocar 500 posts por um arquivo de log.
                    logger.warning("não consegui registrar o descarte: %s", reg)
    return parsed


def registrar_descarte(
    raw: dict[str, Any], exc: Exception, caminho: str | None = None
) -> None:
    """Grava o post que não converteu, em JSONL, para inspeção depois.

    Escreve na hora (modo append), não no fim: uma varredura que morre no meio
    ainda deixa a evidência do que já descartou.
    """
    destino = Path(caminho or DESCARTADOS_FILE)
    registro = {
        "quando": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ig_pk": str((raw or {}).get("pk") or (raw or {}).get("id") or ""),
        "erro": f"{type(exc).__name__}: {exc}",
        "faltando": [c for c in CAMPOS_OBRIGATORIOS if c not in (raw or {})],
        "raw": raw,
    }
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(registro, ensure_ascii=False, default=str) + "\n")


def tolerant_client_class() -> type:
    """Subclasse do `Client` que sobrescreve SÓ a montagem de cada página.

    A paginação, o `amount=0` e o resto continuam sendo código do instagrapi;
    o único ponto trocado é a list comprehension que não tolera item ruim.
    A classe é criada aqui dentro, e não no topo do módulo, porque o import do
    instagrapi é preguiçoso de propósito -- os testes puros rodam sem ela.
    """
    # Lazy on purpose (PLC0415): same reasoning as the docstring above --
    # kept out of the pure, instagrapi-free unit tests' import path.
    from instagrapi import Client  # noqa: PLC0415
    from instagrapi.extractors import extract_media_v1  # noqa: PLC0415

    class TolerantClient(Client):
        """`collection_medias_v1_chunk` que pula o post malformado."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.descartados: list[dict[str, Any]] = []

        def collection_medias_v1_chunk(
            self, collection_pk: str, max_id: str = ""
        ) -> tuple[list[Any], str]:
            # Mesma escolha de endpoint do instagrapi (mixins/collection.py:120).
            if isinstance(collection_pk, int) or collection_pk.isdigit():
                endpoint = f"feed/collection/{collection_pk}/"
            elif collection_pk.lower() == "liked":
                endpoint = "feed/liked/"
            else:
                endpoint = "feed/saved/posts/"

            params = {"include_igtv_preview": "false"}
            if max_id:
                params["max_id"] = max_id
            # Fora do try de propósito: erro de REQUISIÇÃO (429, sessão morta,
            # rede) não é item podre, e engolir isso aqui esconderia justamente
            # a punição do Instagram que a operação precisa enxergar.
            result = self.private_request(endpoint, params=params)

            def anotar(raw: dict[str, Any], exc: Exception) -> None:
                self.descartados.append(
                    {
                        "ig_pk": str(
                            (raw or {}).get("pk") or (raw or {}).get("id") or ""
                        ),
                        "colecao_pk": str(collection_pk),
                        "erro": f"{type(exc).__name__}: {exc}",
                    }
                )
                registrar_descarte(raw, exc)

            items = parse_items_tolerant(
                [m.get("media", m) for m in result["items"]],
                extract_media_v1,
                on_discard=anotar,
            )
            return items, result.get("next_max_id", "") or result.get("max_id", "")

    return TolerantClient


def make_client() -> Any:
    """Build an authenticated instagrapi Client from IG_SESSIONID."""
    Client = tolerant_client_class()

    client = Client()
    client.delay_range = [1, 3]  # avoid Instagram checkpoint/rate-limit
    # instagrapi renamed set_sessionid -> login_by_sessionid (v2+); the old
    # name was removed, not just deprecated.
    login = getattr(client, "login_by_sessionid", None)
    if login is None:
        login = client.set_sessionid
    login(IG_SESSIONID)
    return client


def saved_posts(client: Any, max_per_collection: int = 0) -> list[dict[str, Any]]:
    """Enumerate saved posts across the "All posts" collection + named ones.

    `max_per_collection=0` means "todas as páginas" -- qualquer teto trunca
    ESTA sincronização em silêncio.

    Returns a flat list of {"media": Media, "collection_name": str} dicts.
    """
    return [
        entry
        for _, entries in saved_posts_by_collection(client, max_per_collection)
        for entry in entries
    ]


def saved_posts_by_collection(client: Any, max_per_collection: int = 0):
    """Igual a `saved_posts`, mas rende UMA coleção por vez.

    Existe para que a sincronização publique conforme enumera, em vez de
    acumular tudo e publicar no fim.

    ⚠️ **A ordem importa, e não é a que o Instagram devolve.** As coleções
    NOMEADAS vêm primeiro e a catch-all por último -- a catch-all guarda TODOS
    os posts com `collection_name=None`, então enumerá-la primeiro faria cada
    post ser publicado sem coleção.

    Rende `(collection_name, [{"media": ..., "collection_name": ...}, ...])`.
    """
    if not hasattr(client, "collection_medias"):
        # Legacy API: uma "coleção" só, sem nome.
        yield (
            None,
            [{"media": m, "collection_name": None} for m in client.saved_posts()],
        )
        return

    cols = list(client.collections())

    def eh_catch_all(col: Any) -> bool:
        return getattr(col, "type", "") == "ALL_MEDIA_AUTO_COLLECTION"

    # nomeadas primeiro, catch-all por último
    for col in sorted(cols, key=eh_catch_all):
        if eh_catch_all(col):
            # The catch-all holds every saved post, so its name carries no
            # information -- None means "no collection".
            name = None
        else:
            name = getattr(col, "name", "") or None
        try:
            medias = list(client.collection_medias(col.id, amount=max_per_collection))
        except Exception as exc:
            # One unreadable collection must not abort the whole sync, but
            # silence here means a collection can go missing from every run
            # with nothing to show for it.
            logger.error(
                "coleção %r PERDIDA INTEIRA, nenhum post listado: %s", name, exc
            )
            continue
        yield name, [{"media": m, "collection_name": name} for m in medias]


def to_messages(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map saved-post entries (from saved_posts) to queue message dicts.

    `items` is a list of {"media": Media-like, "collection_name": str}. The
    Media is duck-typed on .pk/.media_type/.caption_text/.user.username so
    tests can pass simple stand-ins. Skips items with no pk or unknown
    media_type.
    """
    messages: list[dict[str, Any]] = []
    for entry in items:
        m = entry["media"]
        pk = getattr(m, "pk", None)
        media_type_raw = getattr(m, "media_type", None)
        media_type = (
            MEDIA_TYPES.get(media_type_raw) if isinstance(media_type_raw, int) else None
        )
        if not pk or not media_type:
            continue
        caption = (getattr(m, "caption_text", "") or "").strip()
        # None, not "sem título": a placeholder here is truthy enough to block
        # the title-derived-from-summary fallback downstream.
        title = caption.splitlines()[0][:80] if caption else None
        user = getattr(m, "user", None)
        messages.append(
            {
                "ig_pk": str(pk),
                "media_type": media_type,
                "url": post_url(pk, getattr(m, "code", None)),
                "title": title,
                # A legenda INTEIRA, não só a primeira linha.
                "caption": caption or None,
                "owner_username": getattr(user, "username", None),
                "collection_name": entry.get("collection_name"),
                "status": "queued",
            }
        )
    return messages


def dedupe_by_pk(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One message per ig_pk, preferring the one that names a collection.

    `saved_posts` yields one entry per (post, collection) pair, so a post
    saved in three collections becomes three messages, and whichever is
    consumed first decides the document's collection tag.

    Order is preserved so the priority ordering a caller applies still holds.
    """
    best: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for msg in messages:
        pk = msg["ig_pk"]
        if pk not in best:
            best[pk] = msg
            order.append(pk)
        elif not best[pk].get("collection_name") and msg.get("collection_name"):
            best[pk] = msg
    return [best[pk] for pk in order]


def split_new(
    messages: list[dict[str, Any]], existing_pks: set[str]
) -> tuple[list[dict[str, Any]], int]:
    """Partition messages into not-yet-ingested vs already-known ig_pks.

    Deduplicates within the batch first: `existing_pks` only knows what is
    already in the database, so without this a post saved in three
    collections is published three times on the very first sync.
    """
    new: list[dict[str, Any]] = []
    skipped = 0
    for msg in dedupe_by_pk(messages):
        if msg["ig_pk"] in existing_pks:
            skipped += 1
        else:
            new.append(msg)
    return new, skipped


def sync_saved_posts(
    client: Any,
    *,
    existing_pks: set[str],
    publish_fn: Callable[[dict[str, Any]], None],
    progress_fn: Callable[[dict[str, Any]], None] | None = None,
    reprocessar: bool = False,
) -> dict[str, Any]:
    """Enumera os salvos e publica CONFORME enumera. Devolve as contagens.

    `publish_fn` é injetado para a tool ligá-lo ao `infra.queue.queue.publish`
    com um canal real enquanto os testes passam um gravador.

    **Publica por coleção, não no fim.** Publicando incrementalmente: o
    worker começa a consumir enquanto a enumeração ainda corre, uma
    interrupção no meio preserva o que já foi enfileirado, e `progress_fn`
    recebe um resumo por coleção.

    `reprocessar=True` ignora `existing_pks` e reenfileira tudo. O worker
    ainda pula o que já está no banco (`document_exists`), então isso só faz
    sentido junto com uma limpeza -- ou para reprocessar de propósito.

    **`descartados` existe para a perda não ser invisível.** O contador vem do
    cliente tolerante (`tolerant_client_class`); com um cliente qualquer -- os
    testes, o caminho legado -- ele é 0 e nada muda.
    """
    publicados = 0
    pulados = 0
    total = 0
    vistos: set[str] = set()
    descartados_antes = len(getattr(client, "descartados", []))

    for nome, entradas in saved_posts_by_collection(client):
        mensagens = to_messages(entradas)
        total += len(mensagens)

        # `vistos` faz o papel que o `dedupe_by_pk` fazia dentro de um lote só:
        # sem ele, um post salvo em três coleções seria publicado três vezes,
        # agora que os lotes são separados.
        conhecidos = vistos if reprocessar else (vistos | existing_pks)
        novas, pulou = split_new(mensagens, conhecidos)

        for msg in novas:
            publish_fn(msg)
            vistos.add(msg["ig_pk"])

        publicados += len(novas)
        pulados += pulou
        if progress_fn:
            progress_fn(
                {
                    "collection": nome or "(todos os salvos)",
                    "in_collection": len(mensagens),
                    "published": len(novas),
                    "skipped": pulou,
                    "published_total": publicados,
                    "discarded_total": len(getattr(client, "descartados", []))
                    - descartados_antes,
                }
            )

    descartados = len(getattr(client, "descartados", [])) - descartados_antes
    if descartados:
        logger.warning(
            "%d post(s) descartado(s) na listagem; o payload cru está em %s",
            descartados,
            DESCARTADOS_FILE,
        )

    return {
        "ok": True,
        "published": publicados,
        "skipped_existing": pulados,
        "descartados": descartados,
        "total": total,
    }
