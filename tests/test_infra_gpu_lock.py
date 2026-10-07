"""Tests for infra.gpu_lock: the shared-GPU mutex used to serialize this
machine's single 12 GB GPU between insta_kb's ig-worker (Whisper) and
minimax-video-factory's ComfyUI renders.

The lock file lives outside both git repos (bind-mounted from the host,
see .devcontainer/docker-compose.yml) -- these tests point GPU_LOCK_DIR at
a temp dir so they never touch the real shared lock.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from infra.gpu_lock.gpu_lock import GpuLockTimeout, acquire, held, release, status

_TIMEOUT_GUARD_SECONDS = 2  # generous ceiling to catch a hang, not a precise bound


@pytest.fixture
def lock_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GPU_LOCK_DIR", str(tmp_path))
    return tmp_path


def test_status_free_when_nobody_holds_it(lock_dir: Path) -> None:
    result = status()
    assert result == {"free": True, "holder": None}


def test_acquire_then_status_reports_held(lock_dir: Path) -> None:
    token = acquire("test-holder", timeout=1)
    assert token is not None

    result = status()
    assert result["free"] is False
    holder = result["holder"]
    assert holder is not None
    assert "test-holder" in holder


def test_release_frees_it_again(lock_dir: Path) -> None:
    token = acquire("test-holder", timeout=1)
    assert token is not None

    release(token)

    assert status() == {"free": True, "holder": None}


def test_second_acquire_times_out_while_held(lock_dir: Path) -> None:
    token = acquire("first", timeout=1)
    assert token is not None

    start = time.monotonic()
    second = acquire("second", timeout=0.3)
    elapsed = time.monotonic() - start

    assert second is None
    assert elapsed < _TIMEOUT_GUARD_SECONDS

    release(token)


def test_second_acquire_succeeds_after_release(lock_dir: Path) -> None:
    token = acquire("first", timeout=1)
    assert token is not None
    release(token)

    second = acquire("second", timeout=1)
    assert second is not None
    release(second)


def test_release_unknown_token_is_a_noop_not_a_raise(lock_dir: Path) -> None:
    # No prior acquire in this test -- release must fail soft, not raise,
    # so a caller that already timed out (got token=None) can still safely
    # call release() in a `finally` block without an extra `if token:` guard.
    release("does-not-exist")


def test_held_releases_automatically_on_exit(lock_dir: Path) -> None:
    with held("test-holder", timeout=1):
        assert status()["free"] is False

    assert status() == {"free": True, "holder": None}


def test_held_releases_on_exception(lock_dir: Path) -> None:
    with pytest.raises(ValueError), held("test-holder", timeout=1):
        raise ValueError("boom")

    assert status() == {"free": True, "holder": None}


def test_held_raises_gpu_lock_timeout_when_busy(lock_dir: Path) -> None:
    with held("first", timeout=1), pytest.raises(GpuLockTimeout) as exc_info:
        with held("second", timeout=0.2):
            pass

    assert exc_info.value.holder == "second"
    assert "first" in str(exc_info.value.held_by)
