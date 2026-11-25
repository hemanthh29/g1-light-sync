"""CLI entry point for the wayland-screenshot tool.

The contract is agent-first (see DESIGN.md): no interactive prompts, stable
exit codes, `--json` on every state-reporting subcommand, single-line stdout
on success, errors prefixed `error:` on stderr.

Exit codes (stable, agents branch on these):
    0   success
    1   cancelled
    2   env-error
    3   no-daemon
    4   portal-error
    5   internal-error
    124 timeout

The tool talks to the user's desktop via the XDG Desktop Portal's ScreenCast
interface (PipeWire-backed). There is no legacy one-shot mode: every
capture comes from the long-lived `cast` daemon or errors out cleanly.

Subcommands:
    wayland-screenshot                  snap from the running daemon (exit 3 if none)
    wayland-screenshot --help
    wayland-screenshot doctor [--json]
    wayland-screenshot cast start [-w] [--no-persist]
    wayland-screenshot cast stop [--purge-tokens]
    wayland-screenshot cast status [--json]
    wayland-screenshot cast enroll {-w|-m}    (operator-only: seeds a token)
    wayland-screenshot cast repick [--timeout N] [--json]
    wayland-screenshot snap [-o PATH]         read latest frame from the daemon
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from .portal import (
    PORTAL_PATH,
    SCREENCAST_IFACE,
    check_environment,
)
from .portal_screencast import DaemonError, run_daemon


EXIT_OK = 0
EXIT_CANCELLED = 1
EXIT_ENV = 2
EXIT_NO_DAEMON = 3
EXIT_PORTAL = 4
EXIT_INTERNAL = 5
EXIT_TIMEOUT = 124


# --- doctor ----------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace) -> int:
    info = {
        "xdg_session_type": os.environ.get("XDG_SESSION_TYPE"),
        "portal_service": f"org.freedesktop.portal.Desktop @ {PORTAL_PATH}",
        "screencast_iface": SCREENCAST_IFACE,
    }
    issues = check_environment(SCREENCAST_IFACE)
    info["issues"] = issues
    info["ok"] = not issues
    if getattr(args, "json", False):
        print(json.dumps(info, indent=2))
    else:
        print("wayland-screenshot doctor")
        print("-" * 40)
        for k, v in info.items():
            if k == "issues":
                if v:
                    print()
                    print("  ISSUES:")
                    for line in issues:
                        print(f"    - {line}")
            elif k == "ok":
                continue
            else:
                print(f"  {k:20s}: {v}")
        if not issues:
            print()
            print("  all checks passed.")
    return EXIT_OK if not issues else EXIT_ENV


# --- output helpers --------------------------------------------------------


def _emit_image(img, out: Path | None, prefix: str) -> int:
    from PIL import Image  # noqa
    assert isinstance(img, Image.Image)
    # compress_level=1 is a 4-5x speedup over PIL's default 6; the
    # resulting PNGs are 10-15% larger and visually identical for
    # full-screen captures (the eye can't tell). This matters for
    # agent-driven capture loops, where the dominant cost of `snap`
    # is the PNG encode. Reference: PIL docs, save() compress_level.
    save_kwargs = {"format": "PNG", "compress_level": 1}
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out, **save_kwargs)
        dest = out
    else:
        dest_dir = (
            Path(os.environ.get("XDG_PICTURES_DIR", str(Path.home() / "Pictures")))
            / "Screenshots"
        )
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
        img.save(dest, **save_kwargs)
    print(dest)
    return EXIT_OK


# --- dead code, kept for reference -----------------------------------------


def _daemonize() -> None:
    """Standard double-fork. **DO NOT USE** — left for reference.

    The CLI process *is* the daemon. Forking sets the daemon's PPid to
    init, and the portal's PID-attribution check then rejects
    `OpenPipeWireRemote` with `AccessDenied: Unable to open /proc/<pid>/root`.
    See VERIFICATION.md Bug 1 for the full story.
    """
    if os.fork() != 0:
        os._exit(EXIT_OK)  # parent exits
    os.setsid()
    if os.fork() != 0:
        os._exit(EXIT_OK)  # intermediate exits
    sys.stdin = open(os.devnull, "r")
    sys.stdout = open(os.devnull, "w")
    sys.stderr = open(os.devnull, "w")
    os.chdir("/")


# --- cast subcommands -------------------------------------------------------


def cmd_cast_start(args: argparse.Namespace) -> int:
    """`cast start [-w] [--no-persist]` — run the screencast daemon in this process.

    The CLI process IS the daemon. We do NOT fork or double-fork, because
    the XDG portal rejects calls from processes whose parent is `init`
    (PPid=1). The portal's PID-attribution check needs a real ancestor
    in the user session. Detaching via fork breaks that.

    The agent contract:
      - `cast start` writes its own pid to state.json, prints "started"
        to stdout, then enters the GLib mainloop. It does not return
        until SIGTERM, SIGINT, or an unrecoverable error.
      - `cast stop` reads the pid from state.json and sends SIGTERM.
      - `cast status --json` returns the daemon's pid and stream state.
      - `cast snap` reads the latest frame from the shm buffer.

    Exit codes:
        0  clean shutdown (SIGTERM/SIGINT after a successful start)
        2  env error
        4  portal error
        5  internal error
    """
    source_type = "window" if args.window else "monitor"
    persist = args.persist

    # Block double-start: if state.json already has a live pid, refuse.
    from . import stream_state
    if stream_state.is_running():
        existing = stream_state.read_state() or {}
        print(
            f"error: daemon already running (pid {existing.get('pid')})",
            file=sys.stderr,
        )
        return EXIT_INTERNAL

    # Validate the env + token BEFORE we print "started" so the agent
    # never sees a "started" followed by an error (which would be a
    # contradictory state). `run_daemon` is the one that does these
    # checks; we just need to call it with the right args. It raises
    # DaemonError on a hard failure (which we map to EXIT_PORTAL).
    # On success, the daemon is running and the "started" line will
    # appear when the mainloop exits cleanly with code 0.
    try:
        # The env check happens inside run_daemon. We do a pre-check
        # for the token here so we can print "started" only after we
        # know we have what we need.
        if not stream_state.has_token(source_type):
            from .portal_screencast import check_environment as _env_check
            env_issues = _env_check(SCREENCAST_IFACE)
            if env_issues:
                for line in env_issues:
                    print(f"error: {line}", file=sys.stderr)
                return EXIT_PORTAL
            print(
                f"error: no restore_token for source_type={source_type!r}; "
                f"run 'wayland-screenshot cast enroll --{source_type}' once to grant "
                f"the permission. The agent path requires a stored token and "
                f"never presents a chooser.",
                file=sys.stderr,
            )
            return EXIT_PORTAL
        # We have a token. Print "started" so the agent knows we're up.
        print("started", flush=True)
        return run_daemon(source_type=source_type, persist=persist, force_chooser=False)
    except DaemonError as exc:
        from . import portal_screencast as pc
        pc._log.error("daemon error: %s", exc)
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_PORTAL
    except Exception as exc:
        from . import portal_screencast as pc
        pc._log.error("unexpected error: %s", exc)
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INTERNAL


def cmd_cast_stop(args: argparse.Namespace) -> int:
    """`cast stop [--purge-tokens]` — SIGTERM the running daemon.

    --purge-tokens also clears the stored portal restore tokens so the
    next `cast start` will re-prompt. Use this if the underlying source
    is no longer available (window closed, monitor unplugged) and you
    want to re-grant.
    """
    from . import stream_state

    if not stream_state.is_running():
        # If the user asked to clear tokens, do that even without a daemon.
        if args.purge_tokens:
            stream_state.clear_tokens()
            print("tokens cleared")
            return EXIT_OK
        print("not running", file=sys.stderr)
        return EXIT_NO_DAEMON
    state = stream_state.read_state() or {}
    pid = int(state.get("pid", 0))
    if not pid:
        print("error: no pid in state.json", file=sys.stderr)
        return EXIT_INTERNAL
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        stream_state.clear()
        if args.purge_tokens:
            stream_state.clear_tokens()
            print("tokens cleared")
        print("not running")
        return EXIT_OK
    except PermissionError:
        print(f"error: cannot signal pid {pid} (different uid?)", file=sys.stderr)
        return EXIT_INTERNAL
    # Wait for clean exit.
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if not stream_state.is_running():
            if args.purge_tokens:
                stream_state.clear_tokens()
                print("tokens cleared")
            print("stopped")
            return EXIT_OK
        time.sleep(0.1)
    # Escalate.
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    # Give it one more grace period to clean up.
    time.sleep(0.2)
    stream_state.clear()
    if args.purge_tokens:
        stream_state.clear_tokens()
        print("tokens cleared")
    print("stopped (killed)")
    return EXIT_OK


def cmd_cast_status(args: argparse.Namespace) -> int:
    """`cast status [--json]` — print daemon state. Exit 3 if not running."""
    from . import stream_state
    if not stream_state.is_running():
        if getattr(args, "json", False):
            print(json.dumps({"running": False}))
        else:
            print("not running")
        return EXIT_NO_DAEMON
    state = stream_state.read_state() or {}
    frame = stream_state.read_frame()
    info = {
        "running": True,
        "pid": state.get("pid"),
        "source_type": state.get("source_type"),
        # Width/height come from frame.json (the source of truth for
        # what the latest capture is) rather than state.json, which is
        # only populated once a frame arrives.
        "width": (frame[1].get("width") if frame is not None else state.get("width", 0)),
        "height": (frame[1].get("height") if frame is not None else state.get("height", 0)),
        "stride": (frame[1].get("stride") if frame is not None else state.get("stride", 0)),
        "format": state.get("format"),
        "status": state.get("status"),
        "started_at": state.get("started_at"),
    }
    if frame is not None:
        _, meta = frame
        info["sequence"] = meta.get("sequence")
        info["frame_captured_at"] = meta.get("captured_at")
    if getattr(args, "json", False):
        print(json.dumps(info, indent=2))
    else:
        for k, v in info.items():
            print(f"  {k:20s}: {v}")
    return EXIT_OK


def cmd_cast_enroll(args: argparse.Namespace) -> int:
    """`cast enroll --window|--monitor` — operator-only.

    Runs the daemon in foreground with `force_chooser=True` so the
    chooser dialog appears. The operator clicks the source they want
    to grant and (in newer portal versions) chooses "Allow" + "Persist".
    The `restore_token` returned in the Start() response is saved to
    `tokens.json`, so future `cast start` calls are unattended.

    This is the ONLY step that requires a human at the box. Once
    enrolled, the source works for any number of agent invocations
    until the user revokes the permission via GNOME Settings -> Privacy.
    """
    if not args.window and not args.monitor:
        print("error: --window or --monitor required", file=sys.stderr)
        return EXIT_INTERNAL
    source_type = "window" if args.window else "monitor"
    print(
        f"A chooser dialog will appear — select the {source_type} you want "
        f"to enroll. The grant will be persisted so future agent runs are "
        f"unattended. This is a one-time setup per source type.",
        file=sys.stderr,
    )
    try:
        return run_daemon(
            source_type=source_type, persist=True, force_chooser=True
        )
    except DaemonError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_PORTAL


def cmd_cast_repick(args: argparse.Namespace) -> int:
    """`cast repick [--timeout N] [--json]` — ask the daemon to re-pick the source.

    The daemon tears down its GStreamer pipeline, re-runs the portal's
    `SelectSources` + `Start` with the saved `restore_token`, and
    rebuilds the pipeline. In window mode this re-shows the chooser
    if the previous surface is gone (the user closed the window), so
    the operator picks a new one. In monitor mode the same monitor
    is usually restored without a chooser, but the call is harmless.

    This is the only way to recover from "window closed" without
    running `cast stop` + `cast enroll` + `cast start`. Designed for
    the agent loop: send the signal, poll `state.json` until the
    `repick_in_progress` flag clears, check the new node_id / status.

    Exit codes:
        0  repick succeeded; new stream is up
        3  no daemon running, or repick timed out
        4  repick timed out (the daemon is still trying)
    """
    from . import stream_state
    state = stream_state.read_state()
    if not state or state.get("status") != "running" or not stream_state.is_running():
        print("error: no daemon running", file=sys.stderr)
        return EXIT_NO_DAEMON
    pid = state.get("pid")
    if not pid:
        print("error: no daemon running (no pid in state.json)", file=sys.stderr)
        return EXIT_NO_DAEMON
    # Sanity: repick is meaningless for monitor mode unless the user
    # explicitly asked for it. The portal will just re-pick the same
    # monitor. Don't fail, but warn.
    if state.get("source_type") == "monitor":
        print(
            "warning: repick in monitor mode is usually a no-op "
            "(the portal restores the same monitor). Use --window "
            "in cast start for a window-mode daemon.",
            file=sys.stderr,
        )

    # Flip the flag in case the signal races; main check is via SIGUSR2.
    stream_state.update_field("repick_in_progress", True)
    try:
        os.kill(int(pid), signal.SIGUSR2)
    except ProcessLookupError:
        print("error: daemon died before signal could be sent", file=sys.stderr)
        return EXIT_NO_DAEMON

    # Poll for completion. The daemon flips repick_in_progress back to
    # False on success or failure. If the daemon dies, is_running() fails.
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if not stream_state.is_running():
            print("error: daemon died during repick", file=sys.stderr)
            return EXIT_NO_DAEMON
        state = stream_state.read_state() or {}
        if not state.get("repick_in_progress", False):
            # Done. Report the new state.
            new_node = state.get("node_id")
            if args.json:
                print(json.dumps({
                    "ok": True,
                    "pid": pid,
                    "node_id": new_node,
                    "source_type": state.get("source_type"),
                    "status": state.get("status"),
                }))
            else:
                print(f"repick ok (pid={pid}, node_id={new_node})")
            return EXIT_OK
        time.sleep(0.1)
    print(f"error: repick timed out after {args.timeout}s", file=sys.stderr)
    return EXIT_PORTAL


def cmd_snap(args: argparse.Namespace) -> int:
    """`wayland-screenshot snap [-o PATH]` — read latest frame from shm.

    Errors out cleanly if no daemon is running, or if the daemon is up
    but hasn't produced a frame yet (commonly the first ~1s after
    `cast start`). Both map to EXIT_NO_DAEMON (3) so the agent's
    branch stays simple; the error message distinguishes.
    """
    from .snap import read_latest_frame, has_daemon
    if not has_daemon():
        print(
            "error: no daemon running; start one with 'wayland-screenshot cast start'",
            file=sys.stderr,
        )
        return EXIT_NO_DAEMON
    frame = read_latest_frame()
    if frame is None:
        # Daemon is alive but no frame yet. Common during the first
        # second after `cast start` while portal negotiation completes,
        # or if every read raced with a torn buffer.raw write despite
        # the brief retry in stream_state.read_frame. Treat as no-daemon
        # to preserve the agent contract (one error class).
        print(
            "error: daemon is up but has not produced a frame yet; "
            "wait ~1s after 'cast start' or retry once",
            file=sys.stderr,
        )
        return EXIT_NO_DAEMON
    img, _meta = frame
    return _emit_image(img, args.output, prefix="snap")


# --- entry point ------------------------------------------------------------


def build_cast_parser() -> argparse.ArgumentParser:
    """Parser for `wayland-screenshot cast <subcommand>`."""
    parser = argparse.ArgumentParser(
        prog="wayland-screenshot cast",
        description="Manage the long-lived screencast daemon.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_start = sub.add_parser("start", help="start the daemon (foreground; prints 'started' on success)")
    p_start.add_argument("-w", "--window", action="store_true", help="capture a specific window")
    p_start.add_argument("--no-persist", dest="persist", action="store_false", default=True)
    p_start.set_defaults(func=cmd_cast_start)

    p_stop = sub.add_parser("stop", help="stop the running daemon")
    p_stop.add_argument(
        "--purge-tokens", action="store_true",
        help="also clear stored portal restore tokens (next start will re-prompt)",
    )
    p_stop.set_defaults(func=cmd_cast_stop)

    p_status = sub.add_parser("status", help="show daemon status")
    p_status.add_argument("--json", action="store_true", help="emit JSON")
    p_status.set_defaults(func=cmd_cast_status)

    p_enroll = sub.add_parser(
        "enroll",
        help="operator-only: click the chooser to seed a portal grant (interactive)",
    )
    grp = p_enroll.add_mutually_exclusive_group(required=True)
    grp.add_argument("-w", "--window", action="store_true", help="enroll a window")
    grp.add_argument("-m", "--monitor", action="store_true", help="enroll a monitor")
    p_enroll.set_defaults(func=cmd_cast_enroll)

    p_repick = sub.add_parser(
        "repick",
        help="re-pick the source (window-mode). Useful after the captured window closed.",
    )
    p_repick.add_argument(
        "--timeout", type=float, default=10.0,
        help="seconds to wait for the daemon to finish repick (default 10)",
    )
    p_repick.add_argument(
        "--json", action="store_true",
        help="emit JSON status on completion",
    )
    p_repick.set_defaults(func=cmd_cast_repick)

    return parser


def build_snap_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wayland-screenshot snap",
        description="Read the latest frame from the running screencast daemon.",
    )
    parser.add_argument("-o", "--output", type=Path)
    return parser


def _print_help() -> None:
    print("Usage: wayland-screenshot [command] [args]")
    print()
    print("Commands:")
    print("  wayland-screenshot                snap from the running daemon (exit 3 if none)")
    print("  wayland-screenshot snap [-o PATH] read latest frame from the daemon")
    print("  wayland-screenshot doctor [--json] environment check")
    print("  wayland-screenshot cast start [-w] [--no-persist] start the screencast daemon")
    print("  wayland-screenshot cast stop [--purge-tokens]      stop the daemon")
    print("  wayland-screenshot cast status [--json]            show daemon status")
    print("  wayland-screenshot cast enroll {-w|-m}             operator-only: seed a portal token")
    print("  wayland-screenshot cast repick [--timeout N] [--json] re-pick the source")
    print()
    print("Run 'wayland-screenshot <command> --help' for details on a command.")


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)

    try:
        return _main_inner(raw)
    except SystemExit as exc:
        # argparse --help raises SystemExit(0); the test contract is
        # that this returns EXIT_OK rather than propagating.
        return EXIT_OK if exc.code in (None, 0) else int(exc.code)


def _main_inner(raw: list[str]) -> int:
    if raw and raw[0] in ("-h", "--help"):
        _print_help()
        return EXIT_OK

    if not raw:
        # No-args: snap from the daemon. Errors out if no daemon is running.
        return cmd_snap(build_snap_parser().parse_args([]))

    if raw[0] == "doctor":
        parser = argparse.ArgumentParser(prog="wayland-screenshot doctor", add_help=True)
        parser.add_argument("--json", action="store_true")
        return cmd_doctor(parser.parse_args(raw[1:]))

    if raw[0] == "snap":
        return cmd_snap(build_snap_parser().parse_args(raw[1:]))

    if raw[0] == "cast":
        parser = build_cast_parser()
        args = parser.parse_args(raw[1:])
        return args.func(args)

    # Unknown top-level command. The legacy one-shot `screenshot [flags]`
    # form is gone — every capture goes through the daemon.
    print(f"error: unknown command: {' '.join(raw)!r}", file=sys.stderr)
    print("Run 'wayland-screenshot --help' for usage.", file=sys.stderr)
    return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
