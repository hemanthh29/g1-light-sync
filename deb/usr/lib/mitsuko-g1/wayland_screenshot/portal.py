"""Low-level helpers for talking to the XDG Desktop Portal.

Both the Screenshot and ScreenCast interfaces live here so the CLI layer
doesn't have to repeat D-Bus boilerplate. Set the GLib mainloop as the
default once at import so every SessionBus() inherits it without each
caller having to remember.
"""

from __future__ import annotations

import os
from typing import Any

import dbus
import dbus.mainloop.glib


PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST_IFACE = "org.freedesktop.portal.ScreenCast"
REQUEST_IFACE = "org.freedesktop.portal.Request"

dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)


def check_environment(*interfaces: str) -> list[str]:
    """Return a list of human-readable issues; empty if env is OK.

    `interfaces` are portal interface names (e.g. 'org.freedesktop.portal.Screenshot')
    that must be present on the portal service. If none are passed, only the
    base environment (Wayland session + portal bus reachable) is checked.
    """
    issues: list[str] = []
    session = os.environ.get("XDG_SESSION_TYPE")
    if session != "wayland":
        issues.append(
            f"XDG_SESSION_TYPE={session!r} (expected 'wayland'). "
            "The portal path requires Wayland; X11 is not supported."
        )
    try:
        bus = dbus.SessionBus()
    except Exception as exc:  # noqa: BLE001 — env check, want all info
        return [f"could not connect to session D-Bus: {exc}"]
    proxy = bus.get_object(PORTAL_BUS, PORTAL_PATH)
    for iface_name in interfaces:
        try:
            dbus.Interface(proxy, iface_name)
        except dbus.exceptions.DBusException as exc:
            issues.append(
                f"{PORTAL_BUS} does not expose {iface_name}: {exc}. "
                "Is xdg-desktop-portal-gnome installed and running?"
            )
    return issues


def get_interface(name: str) -> dbus.Interface:
    """Return a proxy for the named portal interface."""
    bus = dbus.SessionBus()
    proxy = bus.get_object(PORTAL_BUS, PORTAL_PATH)
    return dbus.Interface(proxy, name)


def call_with_response(
    iface: dbus.Interface,
    method: str,
    *args: Any,
    out_count: int = 1,
) -> tuple[int, dict[str, Any], tuple[Any, ...]]:
    """Call a portal method, wait for the Response signal, return (code, results, extras).

    `code` 0 = success. 1 = user cancelled. Anything else is a real error.
    `extras` holds any extra out-args the method returned (e.g. the unix
    fd returned by `OpenPipeWireRemote`). The request object path is
    always the first return value and is used to subscribe to the
    Response signal — it's stripped from `extras`.

    Variable `*args` so the same helper handles methods with different
    signatures: `(parent_window, options)` (Screenshot) and
    `(options,)` (CreateSession, OpenPipeWireRemote).
    """
    from gi.repository import GLib  # local import — heavier dep, only here

    bus = dbus.SessionBus()
    done: dict[str, object] = {}

    def on_response(response: dbus.UInt32, results: dict) -> None:
        if int(response) == 0:
            done["results"] = dict(results)
        else:
            done["err"] = int(response)
        loop.quit()

    ret = getattr(iface, method)(*args)
    # The portal method returns (o request_path, [extra out-args...]).
    # If out_count > 1, ret is a tuple; otherwise it's just the path.
    if out_count <= 1:
        req_path = ret
        extras: tuple[Any, ...] = ()
    else:
        req_path = ret[0]
        extras = tuple(ret[1:out_count])
    bus.add_signal_receiver(
        on_response,
        signal_name="Response",
        dbus_interface=REQUEST_IFACE,
        path=req_path,
    )
    loop = GLib.MainLoop()
    loop.run()

    if "err" in done:
        return int(done["err"]), {}, extras
    return 0, done.get("results", {}), extras  # type: ignore[return-value]
