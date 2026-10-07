"""Local, partial type stub for `instagrapi.utils` -- see
`instagrapi/__init__.pyi` for why this exists. Only `InstagramIdCodec.encode`
is used (by `infra.instagram.ig_sync.post_url`). The real module is a
package (`instagrapi/utils/__init__.py`); a single `.pyi` file stands in for
it fine -- pyright resolves stubs by dotted name, not by file/directory
shape."""

class InstagramIdCodec:
    ENCODING_CHARS: str

    @staticmethod
    def encode(num: int, alphabet: str = ...) -> str: ...
    @staticmethod
    def decode(shortcode: str, alphabet: str = ...) -> int: ...
