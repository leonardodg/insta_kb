"""Local, partial type stub for `instagrapi.extractors` -- see
`instagrapi/__init__.pyi` for why this exists. Only `extract_media_v1` is
used (by `infra.instagram.ig_sync.tolerant_client_class`)."""

from typing import Any

def extract_media_v1(data: dict[str, Any]) -> Any: ...
