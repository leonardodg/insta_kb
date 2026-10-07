"""Shared GPU mutex for this machine's single 12 GB GPU.

Not specific to insta_kb: the same lock file (path from `GPU_LOCK_DIR`,
default `~/.gpu-lock`, bind-mounted from the host into whichever container
needs it -- see `.devcontainer/docker-compose.yml`) is also used by
minimax-video-factory's ComfyUI renders, via an identical module duplicated
there on purpose (see docs/HANDOFF.md, 2026-10-07) -- neither project
imports the other; both just agree on one file and one locking protocol
(`fcntl.flock`, advisory, exclusive). `scripts/gpu-lock.sh` (outside either
repo, in the user's home) offers the same three operations from a shell.

Call `acquire()` before starting GPU work (Whisper transcription here;
ComfyUI render submission on the other side) and `release()` in a
`finally` block after. `status()` is read-only, safe to poll from a
healthcheck or a REST endpoint.
"""

from __future__ import annotations

import fcntl
import os
import time
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

_POLL_INTERVAL_SECONDS = 0.5


class GpuStatus(TypedDict):
    free: bool
    holder: str | None


# Open file descriptors this process currently holds the flock on, keyed by
# the opaque token handed back from acquire(). A single uvicorn worker
# process is assumed (see docker-compose.yml: no `--workers` flag) -- flock
# is per-fd, so release() must run in the same process that acquired it.
_held_fds: dict[str, int] = {}


def _lock_dir() -> Path:
    return Path(os.environ.get("GPU_LOCK_DIR", "~/.gpu-lock")).expanduser()


def _lock_path() -> Path:
    return _lock_dir() / "gpu.lock"


def _holder_path() -> Path:
    return _lock_dir() / "gpu.holder"


def acquire(holder: str, timeout: float = 300) -> str | None:
    """Blocks (polling) up to `timeout` seconds for the exclusive lock.
    Returns an opaque token to pass to `release()`, or None on timeout."""
    _lock_dir().mkdir(parents=True, exist_ok=True)
    fd = os.open(_lock_path(), os.O_CREAT | os.O_RDWR, 0o644)

    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                return None
            time.sleep(_POLL_INTERVAL_SECONDS)

    token = uuid.uuid4().hex
    _held_fds[token] = fd
    _holder_path().write_text(
        f"{holder} token={token} pid={os.getpid()} "
        f"since={datetime.now(timezone.utc).isoformat()}\n"
    )
    return token


def release(token: str) -> None:
    """Fail-soft on an unknown/already-released token -- a caller that
    already got token=None from a timed-out acquire() should be able to
    call release() unconditionally in a `finally` block."""
    fd = _held_fds.pop(token, None)
    if fd is None:
        return
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)
    _holder_path().unlink(missing_ok=True)


class GpuLockTimeout(Exception):
    """Raised by `held()` when the lock isn't free within `timeout`."""

    def __init__(self, holder: str, held_by: str | None):
        self.holder = holder
        self.held_by = held_by
        super().__init__(
            f"timed out acquiring GPU lock for {holder!r} (held by: {held_by})"
        )


@contextmanager
def held(holder: str, timeout: float = 300) -> Generator[None]:
    """The single-process shape most callers want: acquire, run one block
    of GPU work, always release -- no manual token/try/finally bookkeeping.
    Raises GpuLockTimeout instead of returning a sentinel, so a caller that
    forgets to check a return value fails loudly instead of racing ahead
    without the lock."""
    token = acquire(holder, timeout=timeout)
    if token is None:
        raise GpuLockTimeout(holder, status()["holder"])
    try:
        yield
    finally:
        release(token)


def status() -> GpuStatus:
    """Read-only, non-blocking. Does not affect any held lock."""
    _lock_dir().mkdir(parents=True, exist_ok=True)
    fd = os.open(_lock_path(), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return {"free": True, "holder": None}
    except BlockingIOError:
        holder = (
            _holder_path().read_text().strip() if _holder_path().exists() else "unknown"
        )
        return {"free": False, "holder": holder}
    finally:
        os.close(fd)
