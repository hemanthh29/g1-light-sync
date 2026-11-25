"""Long-lived screencast daemon.

This is the main workhorse: opens a portal ScreenCast session, runs an
in-process GStreamer pipeline, and continuously writes the latest frame
to the shm state directory for fast `snap` reads.

Public entry point: `run_daemon(source_type, persist, force_chooser)`.

The agent contract (see DESIGN.md) is enforced here:

* Persistence uses the XDG portal's restore_token mechanism
  (per the ScreenCast spec, version 4):
  - `persist_mode=2` is passed in `SelectSources` so the portal stores
    a persistent grant keyed by our app_id.
  - The `restore_token` returned in the `Start` Response is saved to
    `tokens.json` (via `stream_state.save_token`).
  - The next `SelectSources` call passes that token back as the
    `restore_token` option, which makes the portal skip the chooser.
  - Each restore consumes the token; the new `Start` Response yields
    the next token to save. So the round-trip is self-perpetuating.
  - The token is the ONLY mechanism for unattended restarts. There is
    no app_id-based shortcut.

* If no token is stored for the requested `source_type` and we're not
  in `force_chooser` mode, we fail fast (DaemonError) with a clear
  "run `screenshot cast enroll --window|--monitor` first" message.
  The agent path never blocks on a chooser.

* `force_chooser=True` (the enroll path) deliberately skips the token
  load so the operator sees the chooser dialog. The token returned by
  Start is still saved, so future `cast start` runs are unattended.

* The daemon's stdout/stderr are quiet. Logs go to
  `~/.local/share/wayland-screenshot/daemon.log`.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import dbus
import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstApp", "1.0")
from gi.repository import GLib, Gst, GstApp  # noqa: E402

from . import stream_state
from .portal import (
    PORTAL_PATH,
    SCREENCAST_IFACE,
    call_with_response,
    check_environment,
    get_interface,
)


Gst.init(None)

LOG_PATH = Path(
    os.environ.get(
        "XDG_DATA_HOME", str(Path.home() / ".local" / "share")
    )
) / "wayland-screenshot" / "daemon.log"


# Source-type bitmasks for the portal's ScreenCast.types option.
SOURCE_MONITOR = 1
SOURCE_WINDOW = 2

# persist_mode values for the portal (per the XDG ScreenCast spec):
#   0 = Do not persist
#   1 = Permissions persist as long as the application is running (transient)
#   2 = Permissions persist until explicitly revoked (persistent)
# We use 2 so the restore_token survives across `cast start` invocations.
PERSIST_NONE = 0
PERSIST_PERMANENT = 2


# --- logging ---------------------------------------------------------------


_log = logging.getLogger("wayland-screenshot.daemon")


def _setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_PATH)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    _log.addHandler(handler)
    _log.setLevel(logging.INFO)
    _log.propagate = False  # don't bubble to the root logger


# --- portal helpers ---------------------------------------------------------


def _create_session() -> tuple[int, dict[str, Any]]:
    """Create a ScreenCast session. Returns (code, results)."""
    opts = {"session_handle_token": f"ws_{uuid.uuid4().hex}"}
    iface = get_interface(SCREENCAST_IFACE)
    code, results, _ = call_with_response(iface, "CreateSession", opts)
    return code, results


def _select_sources(
    session_path: str,
    source_type: int,
    persist: bool,
    restore_token: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Select sources (monitor/window) and request persistent grant.

    When `restore_token` is provided, the portal uses it to skip the
    chooser (per the ScreenCast spec, version 4). The token is
    single-use: the next Start() response yields a new one to save.

    When `restore_token` is None, the portal will present a chooser
    dialog. The caller (`run_daemon`) gates this on `force_chooser`
    so the agent path never blocks.
    """
    opts: dict[str, Any] = {
        "types": dbus.UInt32(source_type),
        "persist_mode": dbus.UInt32(PERSIST_PERMANENT if persist else PERSIST_NONE),
        "cursor_mode": dbus.UInt32(1),
    }
    if restore_token:
        # The spec requires the key be a string. dbus-python's a{sv}
        # signature wraps plain strs in dbus.String automatically; we
        # pass the native to keep with the rest of the call style.
        opts["restore_token"] = restore_token
    iface = get_interface(SCREENCAST_IFACE)
    code, results, _ = call_with_response(
        iface, "SelectSources", dbus.ObjectPath(session_path), opts
    )
    return code, results


def _start_stream(session_path: str) -> tuple[int, dict[str, Any], str | None]:
    """Start the screencast stream. Returns (code, results, restore_token).

    The portal returns a new `restore_token` in the Start() Response when
    a persistent grant is in effect. Per the spec, this token replaces
    the one we passed in to SelectSources; the next call uses it.
    """
    iface = get_interface(SCREENCAST_IFACE)
    code, results, _ = call_with_response(
        iface, "Start", dbus.ObjectPath(session_path), "", {}
    )
    token: str | None = None
    if code == 0 and isinstance(results, dict):
        rt = results.get("restore_token")
        if isinstance(rt, str) and rt:
            token = rt
    return code, results, token


def _open_pipewire_remote(session_path: str) -> int:
    """Open the portal's private PipeWire remote. Returns the fd as an int.

    `OpenPipeWireRemote` is synchronous (no Response signal). The return
    shape varies by portal backend: GNOME sometimes returns a
    `dbus.UnixFd` wrapper, sometimes the raw int. Handle both.
    """
    bus = dbus.SessionBus()
    proxy = bus.get_object(
        "org.freedesktop.portal.Desktop", PORTAL_PATH
    )
    sc_iface = dbus.Interface(proxy, SCREENCAST_IFACE)
    pw_fd = sc_iface.OpenPipeWireRemote(
        dbus.ObjectPath(session_path), dbus.Dictionary({})
    )
    if hasattr(pw_fd, "take"):
        return int(pw_fd.take())
    return int(pw_fd)


def _node_id_from_start_results(results: dict[str, Any]) -> int | None:
    """Pull the first node_id from a Start() response.

    The response looks like:
        {"streams": [(dbus.UInt32(42), {position: ..., size: ...})]}

    The portal's signature is `a(ua{sv})` — an array of struct tuples
    `(node_id, properties_dict)`. dbus-python may wrap as `dbus.Struct`
    or `_ResultTuple`; some backends return a list of dicts. Try all.
    """
    streams = results.get("streams")
    if not streams:
        return None
    first = streams[0]
    if isinstance(first, (dbus.Struct, tuple, list)):
        nid = first[0]
    else:
        # dict-like path
        nid = first.get("node_id")
    if nid is None:
        return None
    return int(nid)


# --- frame extraction -------------------------------------------------------


def _build_pipeline_string(pw_fd: int, node_id: int) -> str:
    """Build the GStreamer pipeline string. Pure function — unit-testable.

    pipewiresrc pulls from the portal's PipeWire remote. videoconvert
    normalizes the format. appsink drops after the first sample per
    `drop=true` while `max-buffers=1` keeps the buffer pool small.
    `emit-signals=true` is REQUIRED — `Gst.parse_launch` does not default
    it on, and without it the `new-sample` callback never fires.
    """
    return (
        f"pipewiresrc name=pw_src fd={pw_fd} path={node_id} do-timestamp=false "
        f"! videoconvert ! video/x-raw,format=BGRA "
        f"! appsink name=sink emit-signals=true max-buffers=1 drop=true sync=false"
    )


def _on_new_sample(
    sink: GstApp.AppSink,
    frame_info: dict[str, Any],
) -> Gst.FlowReturn:
    """appsink callback. Pulls the sample, writes to shm, increments seq.

    On the first sample, also updates state.json with the real width /
    height / stride. The pipewiresrc element's `notify::caps` signal is
    unreliable on GStreamer 1.28, so we discover the dimensions from the
    first sample instead — which always has caps baked into the buffer.
    """
    sample = sink.emit("pull-sample")
    if sample is None:
        return Gst.FlowReturn.OK
    buffer = sample.get_buffer()
    if buffer is None:
        return Gst.FlowReturn.OK
    caps = sample.get_caps()
    if caps is None:
        return Gst.FlowReturn.OK
    struct = caps.get_structure(0)
    width = struct.get_value("width")
    height = struct.get_value("height")
    if not width or not height:
        return Gst.FlowReturn.OK

    success, map_info = buffer.map(Gst.MapFlags.READ)
    if not success:
        return Gst.FlowReturn.OK
    try:
        data = bytes(map_info.data)
        frame_info["sequence"] += 1
        try:
            stream_state.write_frame(
                data,
                width=width,
                height=height,
                stride=width * 4,
                sequence=frame_info["sequence"],
                captured_at=time.time(),
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("write_frame failed: %s", exc)
        # First-frame dimension discovery: state.json is written with
        # zeros at pipeline start (caps aren't known yet). Bounce the
        # real dimensions in here so `cast status` reports them honestly.
        if frame_info["sequence"] == 1:
            try:
                stream_state.update_field("width", width)
                stream_state.update_field("height", height)
                stream_state.update_field("stride", width * 4)
            except Exception as exc:  # noqa: BLE001
                _log.warning("update_field for dimensions failed: %s", exc)
    finally:
        buffer.unmap(map_info)
    return Gst.FlowReturn.OK


# --- daemon entry point -----------------------------------------------------


class DaemonError(Exception):
    """Raised when the daemon cannot come up. Maps to EXIT_PORTAL or EXIT_ENV."""


# Module-level state for the running daemon. Used by `repick()` to swap
# pipeline / pipewire fd without re-running CreateSession. Cleared on
# shutdown.
_daemon_context: dict[str, Any] = {}


def run_daemon(
    source_type: str = "monitor",
    persist: bool = True,
    force_chooser: bool = False,
) -> int:
    """Run the long-lived screencast daemon. Returns process exit code.

    Token model (per the XDG ScreenCast spec):
      - `force_chooser=False` (the default for `cast start`): if no
        token is stored for `source_type`, raise DaemonError immediately
        with a clear "run `screenshot cast enroll` first" message. If a
        token IS stored, pass it to SelectSources so the portal skips
        the chooser. The token returned in Start is saved for next time.
      - `force_chooser=True` (the `cast enroll` path): always call
        SelectSources without a token, so the portal shows the chooser
        and the operator can grant the persistent permission. The
        token returned in Start is still saved, so future `cast start`
        runs are unattended.

    The agent-first contract: when called via `cast start` with
    `force_chooser=False`, this function never blocks on a chooser. It
    either has a valid token and runs, or it errors with a clear
    message.

    Signal handling: SIGTERM/SIGINT trigger a clean shutdown that stops
    the GStreamer pipeline, clears state files, and exits 0.
    """
    _setup_logging()
    _log.info(
        "daemon starting: source=%s persist=%s force_chooser=%s",
        source_type, persist, force_chooser,
    )

    issues = check_environment(SCREENCAST_IFACE)
    if issues:
        for line in issues:
            _log.error("env check: %s", line)
        raise DaemonError("; ".join(issues))

    if source_type == "monitor":
        st = SOURCE_MONITOR
    elif source_type == "window":
        st = SOURCE_WINDOW
    else:
        raise DaemonError(f"unknown source_type {source_type!r}")

    # Resolve the restore_token to pass to SelectSources. In the agent
    # path (force_chooser=False) we MUST have a token or fail fast —
    # we never present a chooser to an unattended caller. In the enroll
    # path (force_chooser=True) we deliberately skip the token so the
    # operator sees the chooser and can grant the permission.
    restore_token: str | None
    if force_chooser:
        restore_token = None
        _log.info("enroll mode: forcing chooser dialog")
    else:
        restore_token = stream_state.load_token(source_type)
        if not restore_token:
            raise DaemonError(
                f"no restore_token for source_type={source_type!r}; "
                f"run 'screenshot cast enroll --{source_type}' once to grant "
                f"the permission. The agent path requires a stored token and "
                f"never presents a chooser."
            )
        _log.info("loaded restore_token from tokens.json")

    # Portal negotiation.
    code, results = _create_session()
    if code != 0:
        raise DaemonError(f"CreateSession failed: code {code}, {results}")
    session_path = str(results.get("session_handle", ""))
    if not session_path:
        raise DaemonError(f"CreateSession returned no session_handle: {results}")
    _log.info("session created: %s", session_path)

    code, results = _select_sources(session_path, st, persist, restore_token)
    if code != 0:
        raise DaemonError(f"SelectSources failed: code {code}, {results}")
    _log.info("sources selected (restore_token=%s)", "yes" if restore_token else "no")

    code, results, new_token = _start_stream(session_path)
    if code != 0:
        raise DaemonError(f"Start failed: code {code}, {results}")
    node_id = _node_id_from_start_results(results)
    if node_id is None:
        raise DaemonError(f"Start returned no node_id: {results}")

    # Per the spec, the new token replaces the one we passed in. Save it
    # for the next call. If Start returned no token (e.g. persist=False),
    # the existing token (if any) stays in place — but the next call
    # without a token will just trigger a chooser, which is correct.
    if new_token:
        stream_state.save_token(source_type, new_token)
        _log.info("saved new restore_token (length=%d)", len(new_token))
    else:
        _log.warning("Start returned no restore_token; future calls will re-prompt")

    _log.info("stream started: node_id=%d", node_id)

    pw_fd_int = _open_pipewire_remote(session_path)
    # We pass pw_fd_int to pipewiresrc; it will close it on its own.
    pipeline_str = _build_pipeline_string(pw_fd_int, node_id)
    _log.info("pipeline: %s", pipeline_str)

    pipeline = Gst.parse_launch(pipeline_str)
    appsink = pipeline.get_by_name("sink")
    if appsink is None:
        raise DaemonError("pipeline did not produce an appsink named 'sink'")
    frame_info: dict[str, Any] = {"sequence": 0}
    appsink.connect("new-sample", _on_new_sample, frame_info)

    bus = pipeline.get_bus()
    bus.add_signal_watch()

    def on_bus_message(_bus, message):
        if message.type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            _log.error("gstreamer ERROR: %s (%s)", err, debug)
            # A real GStreamer ERROR is fatal — the pipeline can't
            # recover on its own. Tear down via `shutdown` so the agent
            # sees a clean exit rather than a stall. Defer to the next
            # mainloop iteration to avoid re-entrancy on the bus callback.
            # `shutdown` is defined further down in this function; the
            # closure lookup happens at call time, by which point it's
            # defined.
            GLib.idle_add(lambda: shutdown("gstreamer error"))
        elif message.type == Gst.MessageType.WARNING:
            warn, debug = message.parse_warning()
            _log.warning("gstreamer WARNING: %s (%s)", warn, debug)
        elif message.type == Gst.MessageType.EOS:
            _log.info("gstreamer EOS")

    bus.connect("message", on_bus_message)

    stream_state.write_state(
        pid=os.getpid(),
        source_type=source_type,
        width=0,  # populated on the first frame (see _on_new_sample)
        height=0,
        stride=0,
        status="starting",
    )

    main_loop = GLib.MainLoop()

    # Stash the negotiation context for repick() to reuse. The signal
    # handler (installed later) calls repick() to rebuild the stream
    # without re-running CreateSession or the env check.
    _daemon_context["session_path"] = session_path
    _daemon_context["source_type"] = source_type
    _daemon_context["persist"] = persist
    _daemon_context["node_id"] = node_id

    def shutdown(reason: str) -> None:
        _log.info("shutting down: %s", reason)
        stream_state.update_status("stopping")
        pipeline.set_state(Gst.State.NULL)
        try:
            os.close(pw_fd_int)
        except OSError:
            pass
        _daemon_context.clear()
        stream_state.clear()
        main_loop.quit()

    def repick() -> bool:
        """Tear down the stream and re-pick the source on a fresh session.

        Per the XDG ScreenCast spec, `SelectSources` can only be called
        before `Start`. To change the source after Start, the client
        must create a new session, call SelectSources + Start on it,
        then swap the GStreamer pipeline. This is what `cast repick`
        does.

        In window mode, this re-shows the portal's chooser if the
        previous surface is gone (the user closed the window). The
        operator picks a new one. In monitor mode, the same monitor
        is usually restored without a chooser.

        Returns True on success, False on failure. The agent can poll
        `state.json` (`repick_in_progress` flips False on completion).
        """
        nonlocal pipeline, pw_fd_int
        _log.info("repick: tearing down current stream")
        try:
            stream_state.update_field("repick_in_progress", True)
        except Exception as exc:  # noqa: BLE001
            _log.warning("could not write repick_in_progress=true: %s", exc)
        # Stop the old GStreamer pipeline first. The old fd is closed
        # when we NULL the pipeline below, but the python-level fd
        # also needs explicit cleanup.
        pipeline.set_state(Gst.State.NULL)
        # The portal's spec says SelectSources is only valid before
        # Start on the same session. We have to create a fresh session.
        code, results = _create_session()
        if code != 0:
            _log.error("repick: CreateSession failed: code %d, %s", code, results)
            stream_state.update_field("repick_in_progress", False)
            return False
        new_session_path = str(results.get("session_handle", ""))
        if not new_session_path:
            _log.error("repick: CreateSession returned no session_handle: %s", results)
            stream_state.update_field("repick_in_progress", False)
            return False
        _log.info("repick: new session: %s", new_session_path)
        st = SOURCE_MONITOR if source_type == "monitor" else SOURCE_WINDOW
        # Pass the saved token so the portal may skip the chooser when
        # the source is still grantable. Per spec, even with a token,
        # the chooser appears if the portal can't auto-restore the
        # specific surface (e.g. window was closed).
        token_for_pick = stream_state.load_token(source_type) or ""
        code, results = _select_sources(new_session_path, st, persist, token_for_pick)
        if code != 0:
            _log.error("repick: SelectSources failed: code %d, %s", code, results)
            stream_state.update_field("repick_in_progress", False)
            return False
        code, results, new_token = _start_stream(new_session_path)
        if code != 0:
            _log.error("repick: Start failed: code %d, %s", code, results)
            stream_state.update_field("repick_in_progress", False)
            return False
        new_node_id = _node_id_from_start_results(results)
        if new_node_id is None:
            _log.error("repick: Start returned no node_id: %s", results)
            stream_state.update_field("repick_in_progress", False)
            return False
        if new_token:
            stream_state.save_token(source_type, new_token)
        # Close the old PipeWire fd (we own it; the pipeline is gone).
        try:
            os.close(pw_fd_int)
        except OSError:
            pass
        # Open a new PipeWire remote for the new session.
        try:
            new_pw_fd = _open_pipewire_remote(new_session_path)
        except Exception as exc:  # noqa: BLE001
            _log.error("repick: OpenPipeWireRemote failed: %s", exc)
            stream_state.update_field("repick_in_progress", False)
            return False
        new_pipeline_str = _build_pipeline_string(new_pw_fd, new_node_id)
        _log.info("repick: new pipeline: %s", new_pipeline_str)
        new_pipeline_obj = Gst.parse_launch(new_pipeline_str)
        new_appsink = new_pipeline_obj.get_by_name("sink")
        if new_appsink is None:
            _log.error("repick: pipeline did not produce an appsink")
            stream_state.update_field("repick_in_progress", False)
            return False
        new_appsink.connect("new-sample", _on_new_sample, frame_info)
        new_bus = new_pipeline_obj.get_bus()
        new_bus.add_signal_watch()
        new_bus.connect("message", on_bus_message)
        # Dimensions will be discovered from the first frame in
        # _on_new_sample; the notify::caps signal is unreliable, so we
        # don't try to wire it here.
        ret = new_pipeline_obj.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            _log.error("repick: failed to set new pipeline to PLAYING")
            stream_state.update_field("repick_in_progress", False)
            return False
        # Hot-swap: reassign the closure locals so the main loop
        # keeps referring to the new pipeline / fd.
        pipeline = new_pipeline_obj
        pw_fd_int = new_pw_fd
        _daemon_context["pipeline"] = new_pipeline_obj
        _daemon_context["pw_fd_int"] = new_pw_fd
        _daemon_context["session_path"] = new_session_path
        _daemon_context["node_id"] = new_node_id
        stream_state.write_state(
            pid=os.getpid(),
            source_type=source_type,
            width=0, height=0, stride=0,
            status="running",
            node_id=new_node_id,
        )
        stream_state.update_field("repick_in_progress", False)
        _log.info("repick: success, new node_id=%d session=%s",
                  new_node_id, new_session_path)
        return True

    _daemon_context["pipeline"] = pipeline
    _daemon_context["pw_fd_int"] = pw_fd_int
    _daemon_context["frame_info"] = frame_info
    _daemon_context["main_loop"] = main_loop
    _daemon_context["shutdown"] = shutdown
    _daemon_context["repick"] = repick

    def handle_sigint(_sig, _frame):
        shutdown("SIGINT")

    def handle_sigterm(_sig, _frame):
        shutdown("SIGTERM")

    def handle_sigusr2(_sig, _frame):
        # SIGUSR2 triggers a repick. The handler is intentionally simple:
        # we just call the stored repick() function. If it fails, the
        # state file's `running` flag stays True but the frame stream
        # will stop producing frames; the agent will notice via a
        # stalled `sequence` counter.
        repick()

    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGUSR2, handle_sigusr2)

    ret = pipeline.set_state(Gst.State.PLAYING)
    if ret == Gst.StateChangeReturn.FAILURE:
        raise DaemonError("failed to set pipeline to PLAYING")
    stream_state.update_status("running")

    _log.info("daemon running: pid=%d", os.getpid())
    main_loop.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI shim for direct `python -m wayland_screenshot.portal_screencast` use."""
    parser = argparse.ArgumentParser(prog="screenshot cast (daemon)")
    parser.add_argument("source", choices=["monitor", "window"], default="monitor", nargs="?")
    parser.add_argument("--no-persist", dest="persist", action="store_false", default=True)
    parser.add_argument(
        "--force-chooser", dest="force_chooser", action="store_true",
        help="skip the stored token; force the portal chooser (operator-only, for enroll)",
    )
    args = parser.parse_args(argv)
    try:
        return run_daemon(
            source_type=args.source,
            persist=args.persist,
            force_chooser=args.force_chooser,
        )
    except DaemonError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
