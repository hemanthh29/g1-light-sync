"""Fast frame reader: pulls the latest frame from the screencast daemon's shm.

The daemon (see portal_screencast.run_daemon) writes each frame to
`/dev/shm/wayland-screenshot/{buffer.raw, frame.json}`. This module reads
those files and reconstructs a PIL Image. It is the entire read path for
`screenshot snap` and the fallback for `screenshot` when a daemon is
running.
"""

from __future__ import annotations

from typing import Any

from PIL import Image

from . import stream_state


def is_daemon_running() -> bool:
    """True iff a live daemon has published a state.json we can read."""
    return stream_state.is_running()


# Alias for the `snap` CLI which needs to distinguish "no daemon" from
# "daemon is up, no frame yet". Both cases map to the same exit code (3),
# but the error message on stderr is different and that distinction is
# useful for both humans and agent state machines.
has_daemon = is_daemon_running


def read_latest_frame() -> tuple[Image.Image, dict[str, Any]] | None:
    """Read the most recent frame. Returns None when no daemon or no frame.

    Returns (PIL.Image.Image, metadata) on success. Metadata contains the
    frame.json fields plus the daemon's source_type from state.json.

    The agent contract: never raises on a missing daemon. Returns None
    so the caller can decide whether to fall back to a one-shot capture
    (`screenshot`) or surface an error (`snap`).
    """
    if not stream_state.is_running():
        return None
    state = stream_state.read_state() or {}
    result = stream_state.read_frame()
    if result is None:
        return None
    data, meta = result
    width = int(meta.get("width", 0))
    height = int(meta.get("height", 0))
    if not width or not height:
        return None
    fmt = str(meta.get("format", "BGRA"))
    if fmt == "BGRA":
        img = Image.frombytes("RGBA", (width, height), data, "raw", "BGRA")
    elif fmt == "BGRx":
        img = Image.frombytes("RGBa", (width, height), data, "raw", "BGRx")
    else:
        # Unknown format — best-effort decode as BGRA.
        img = Image.frombytes("RGBA", (width, height), data, "raw", "BGRA")
    combined_meta = {**meta, "source_type": state.get("source_type", "unknown")}
    return img, combined_meta
