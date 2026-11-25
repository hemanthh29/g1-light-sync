"""State files for the long-lived screencast daemon.

The daemon and the fast `snap` reader share state via files in a
shared directory (default `/dev/shm/wayland-screenshot/`):

    state.json    daemon state (pid, source, dims, started_at, status)
    frame.json    last-frame metadata (width, height, stride, format, sequence, captured_at)
    buffer.raw    last-frame pixels (raw BGRA bytes)
    tokens.json   portal restore_tokens keyed by source_type ("monitor" or "window")

Atomicity: every JSON write is `write tmp -> fsync -> rename`, so readers
never see a partial JSON document. `buffer.raw` is written before the
matching `frame.json`, so a reader that reads `frame.json` first is
guaranteed to see the corresponding bytes (a happens-before via the
filesystem). Sequence numbers in `frame.json` let the reader detect
mid-write races and retry once if a torn read is observed.

Portal restore tokens (per the XDG Desktop Portal ScreenCast spec):
    The portal returns a `restore_token` in the Start() Response when
    `persist_mode=2` is granted. We MUST save it client-side; the next
    SelectSources() call passes it back as `restore_token` to skip the
    chooser. The token is single-use (consumed on restore), so the
    response of each Start() yields the next token to save. This is the
    ONLY way to make the agent path unattended across invocations.

Env-var override: `WAYLAND_SCREENSHOT_STATE_DIR` lets tests point at a
temp directory.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


DEFAULT_DIRNAME = "wayland-screenshot"
ENV_OVERRIDE = "WAYLAND_SCREENSHOT_STATE_DIR"

STATE_FILENAME = "state.json"
FRAME_FILENAME = "frame.json"
BUFFER_FILENAME = "buffer.raw"
LOCK_FILENAME = "lock"
TOKENS_FILENAME = "tokens.json"


VALID_SOURCE_TYPES = ("monitor", "window")


def get_state_dir() -> Path:
    """Return the directory holding the daemon's state files.

    Resolution order: $WAYLAND_SCREENSHOT_STATE_DIR > /dev/shm/<name> >
    $XDG_RUNTIME_DIR/<name>. The first two are real tmpfs; the third is a
    disk-backed tmpfs (still fast on most systems, but slower under memory
    pressure).
    """
    override = os.environ.get(ENV_OVERRIDE)
    if override:
        return Path(override)
    shm = Path("/dev/shm") / DEFAULT_DIRNAME
    if shm.parent.exists() and os.access("/dev/shm", os.W_OK):
        return shm
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / DEFAULT_DIRNAME
    return runtime


def state_path() -> Path:
    return get_state_dir() / STATE_FILENAME


def frame_path() -> Path:
    return get_state_dir() / FRAME_FILENAME


def buffer_path() -> Path:
    return get_state_dir() / BUFFER_FILENAME


def lock_path() -> Path:
    return get_state_dir() / LOCK_FILENAME


def tokens_path() -> Path:
    # Keep portal restore grants across reboots while frame/state data stays
    # in the runtime directory (normally /dev/shm). An explicit state-dir
    # override remains fully isolated for tests and custom deployments.
    if os.environ.get(ENV_OVERRIDE):
        return get_state_dir() / TOKENS_FILENAME
    data_home = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return data_home / DEFAULT_DIRNAME / TOKENS_FILENAME


# --- low-level atomic JSON write -------------------------------------------


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        payload = json.dumps(data, indent=2, sort_keys=True).encode("utf-8")
        os.write(fd, payload)
        os.fsync(fd)
        os.close(fd)
        os.replace(tmp, path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        return None
    return None


# --- daemon state ----------------------------------------------------------


def is_running() -> bool:
    """True iff a live daemon has written a state.json we can confirm.

    The daemon's atomic write uses tmp+rename, which is atomic on POSIX.
    A reader can still see a transient miss (state.json briefly absent,
    or read returning empty) if it races with the rename. We retry briefly
    so callers don't get a spurious "no daemon" during that window.
    """
    sp = state_path()
    state: dict[str, Any] | None = None
    for _ in range(5):
        if not sp.exists():
            time.sleep(0.005)
            continue
        state = _read_json(sp)
        if state is not None:
            break
        # File exists but read returned None (race during write, OR
        # the file is genuinely garbage). On a fresh retry the
        # previous race has resolved; if it still reads empty the
        # caller really should treat it as "no daemon".
        time.sleep(0.005)
    if not state:
        return False
    pid = state.get("pid")
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        # ESRCH: no such process.
        return False
    except PermissionError:
        # EPERM: process exists but we can't signal it (different uid).
        # Treat as running — the daemon is alive, just not ours to signal.
        return True
    except OSError:
        # Other OSError: be conservative and say "not running" so callers
        # don't try to interact with a state file whose pid is questionable.
        return False


def write_state(
    pid: int,
    *,
    source_type: str,
    width: int,
    height: int,
    stride: int,
    fmt: str = "BGRA",
    started_at: float | None = None,
    status: str = "starting",
    node_id: int | None = None,
) -> None:
    """Write state.json. Called by the daemon during startup and shutdown.

    state.json holds only transient daemon runtime state (pid, source,
    dimensions, status). Portal restore_tokens are NOT stored here — they
    live in a separate `tokens.json` (see `save_token` / `load_token`),
    persisted across daemon restarts so a follow-up `cast start` can
    skip the chooser by handing the saved token back to SelectSources.
    """
    payload: dict[str, Any] = {
        "pid": pid,
        "source_type": source_type,
        "width": width,
        "height": height,
        "stride": stride,
        "format": fmt,
        "started_at": started_at if started_at is not None else time.time(),
        "status": status,
    }
    if node_id is not None:
        payload["node_id"] = node_id
    _atomic_write_json(state_path(), payload)


def update_status(status: str) -> None:
    """Mutate the `status` field of state.json without touching the rest."""
    state = _read_json(state_path())
    if not state:
        return
    state["status"] = status
    _atomic_write_json(state_path(), state)


def update_field(key: str, value: Any) -> None:
    """Mutate a single top-level field of state.json atomically.

    Used by the daemon to flip transient flags (e.g. `repick_in_progress`)
    without overwriting the rest of the state. Silently no-ops if
    state.json doesn't exist yet (the daemon hasn't written it).
    """
    state = _read_json(state_path())
    if not state:
        return
    state[key] = value
    _atomic_write_json(state_path(), state)


def read_state() -> dict[str, Any] | None:
    """Read state.json. Returns None if missing or invalid."""
    return _read_json(state_path())


# --- frame buffer ----------------------------------------------------------


def write_frame(
    bgra_bytes: bytes,
    *,
    width: int,
    height: int,
    stride: int,
    fmt: str = "BGRA",
    sequence: int,
    captured_at: float | None = None,
) -> None:
    """Write buffer.raw atomically, then frame.json.

    Reader order: frame.json, buffer.raw. Both files are written via
    tmp+rename so the reader never sees a torn buffer.raw. Earlier
    versions wrote buffer.raw in place; an 8 MB BGRA buffer at 1920×1080
    takes long enough to write that `snap` regularly raced with the
    write and read_frame detected a torn buffer. Atomic rename makes
    that race window effectively invisible.

    The happens-before chain still holds: the frame.json rename implies
    a barrier, so a reader that has just observed frame.json with
    sequence N sees buffer.raw with at least sequence N (it might be
    one frame stale because buffer.raw is renamed before frame.json,
    but the data is intact). The reader uses `size_bytes` from
    frame.json as the canonical size; if the buffer doesn't match that
    size, something else (filesystem corruption, manual edit) is going
    on and we report None.
    """
    bp = buffer_path()
    bp.parent.mkdir(parents=True, exist_ok=True)
    tmp = bp.with_suffix(bp.suffix + f".tmp.{os.getpid()}")
    try:
        with open(tmp, "wb") as fh:
            fh.write(bgra_bytes)
            fh.flush()
            os.fsync(fh.fileno())
        os.rename(tmp, bp)
    finally:
        # Cleanup if the rename failed for any reason.
        try:
            os.unlink(tmp)
        except OSError:
            pass
    _atomic_write_json(
        frame_path(),
        {
            "width": width,
            "height": height,
            "stride": stride,
            "format": fmt,
            "sequence": sequence,
            "captured_at": captured_at if captured_at is not None else time.time(),
            "size_bytes": len(bgra_bytes),
        },
    )


def read_frame() -> tuple[bytes, dict[str, Any]] | None:
    """Read frame.json + buffer.raw. Returns None if no frame is available.

    Both files are written atomically (see `write_frame`), so the reader
    either sees the previous complete frame or the new complete frame
    — never a torn buffer.raw. The only transient failure modes are:

    - frame.json doesn't exist (daemon is starting up, or it just
      crashed mid-write)
    - frame.json or buffer.raw briefly absent during the atomic rename

    We retry briefly (5 × 5 ms = 25 ms max) to ride out the rename
    gap. A persistent miss means there's no frame yet — caller
    surfaces "daemon is up but no frame" or treats it as a snapshot
    miss.

    The `size_bytes` check is still in place as a defensive assertion
    against filesystem corruption or someone hand-editing the file;
    it should never trigger under normal operation.
    """
    bp = buffer_path()
    fp = frame_path()
    for _ in range(5):
        meta = _read_json(fp)
        if not meta:
            time.sleep(0.005)
            continue
        expected = int(meta.get("size_bytes", 0))
        if not bp.exists():
            time.sleep(0.005)
            continue
        try:
            with bp.open("rb") as fh:
                data = fh.read()
        except OSError:
            time.sleep(0.005)
            continue
        if expected and len(data) != expected:
            # Should not happen with atomic writes. Defensive only.
            return None
        return data, meta
    return None


# --- lifecycle -------------------------------------------------------------


def clear() -> None:
    """Remove all state files. Called by `cast stop` and on daemon exit."""
    for p in (state_path(), frame_path(), buffer_path(), lock_path()):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


# --- portal restore tokens -------------------------------------------------


def _read_tokens() -> dict[str, str]:
    """Read tokens.json. Returns {} if missing or invalid."""
    data = _read_json(tokens_path())
    if not data:
        return {}
    return {k: str(v) for k, v in data.items() if isinstance(v, str) and v}


def load_token(source_type: str) -> str | None:
    """Return the saved restore_token for a source_type, or None.

    Per the XDG ScreenCast spec, a token is single-use: it gets consumed
    when the portal restores the session and returns a new one in the
    Start() response. We save the new one, so the next call still has a
    valid token. This function just reads what the previous Start() saved.
    """
    if source_type not in VALID_SOURCE_TYPES:
        raise ValueError(f"invalid source_type {source_type!r}")
    return _read_tokens().get(source_type) or None


def save_token(source_type: str, token: str) -> None:
    """Persist a restore_token from the Start() response for next time.

    Atomic rename of tokens.json. Overwrites any prior token for this
    source_type (each Start() yields a new token; the old one is now
    consumed).
    """
    if source_type not in VALID_SOURCE_TYPES:
        raise ValueError(f"invalid source_type {source_type!r}")
    if not token:
        # Don't persist empty tokens — they'd just cause the next call
        # to fail with "Restore token is not valid".
        return
    current = _read_tokens()
    current[source_type] = token
    _atomic_write_json(tokens_path(), current)


def has_token(source_type: str) -> bool:
    """True iff a non-empty token is stored for this source_type."""
    return load_token(source_type) is not None


def clear_tokens() -> None:
    """Drop all stored tokens. Used by `cast stop --purge-tokens` and tests."""
    try:
        tokens_path().unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass
