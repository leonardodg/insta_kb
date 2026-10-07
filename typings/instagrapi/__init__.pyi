"""Local, partial type stub for `instagrapi`.

instagrapi ships no `py.typed` marker, so pyright treats every member as
Unknown regardless of its (real) inline annotations -- `reportMissingTypeStubs`
plus a cascade of `reportUnknown*` wherever a value derived from the library
flows into our code. Reproducing the whole library's surface here would be a
maintenance trap; this covers only what `infra.instagram.ig_sync` actually
calls on `Client` (directly, not through `getattr`/duck-typing, which already
resolves to `Any` and needs no stub).
"""

from typing import Any

class Client:
    delay_range: list[Any]

    def __init__(
        self,
        settings: dict[str, Any] | None = ...,
        proxy: str | None = ...,
        delay_range: list[Any] | None = ...,
        logger: Any = ...,
        override_app_version: bool = ...,
        **kwargs: Any,
    ) -> None: ...
    def collection_medias_v1_chunk(
        self, collection_pk: str, max_id: str = ...
    ) -> tuple[list[Any], str]: ...
    def private_request(
        self,
        endpoint: str,
        data: Any = ...,
        params: dict[str, Any] | None = ...,
        login: bool = ...,
        with_signature: bool = ...,
        headers: dict[str, Any] | None = ...,
        extra_sig: Any = ...,
        domain: str | None = ...,
    ) -> dict[str, Any]: ...
    def login_by_sessionid(self, sessionid: str) -> bool: ...
    def set_sessionid(self, sessionid: str) -> None: ...
    def media_info(self, media_pk: str, use_cache: bool = ...) -> Any: ...
    def collections(self) -> list[Any]: ...
    def collection_medias(
        self, collection_pk: str, amount: int = ..., last_media_pk: int = ...
    ) -> list[Any]: ...
    def saved_posts(self) -> list[Any]: ...
