#!/usr/bin/env python3
"""Small desktop control panel for the Mitsuko Phantom G1."""
from __future__ import annotations

import ast
import json
import math
import os
import signal
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import colorchooser, messagebox, simpledialog, ttk


ROOT = Path(__file__).resolve().parent
CONTROLLER = ROOT / "mitsuko_g1.py"
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "mitsuko-g1"
CONFIG = CONFIG_HOME / "mitsuko_g1.json"
GUI_CONFIG = CONFIG_HOME / "mitsuko_g1_gui.json"
PHONE_PAIRING = CONFIG_HOME / "phone_pairing.json"
CONFIG_HOME.mkdir(parents=True, exist_ok=True)
for target, legacy in ((CONFIG, ROOT / "mitsuko_g1.json"), (GUI_CONFIG, ROOT / "mitsuko_g1_gui.json")):
    if not target.exists() and legacy.exists():
        try:
            shutil.copy2(legacy, target)
        except OSError:
            pass
CAPTURE_HELPER = Path.home() / ".local/share/wayland-screenshot-venv/bin/wayland-screenshot"
BG = "#07100e"
PANEL = "#111f1b"
PANEL_ALT = "#172823"
TEXT = "#f2f7f4"
MUTED = "#9bb1a6"
ACCENT = "#46d6aa"
GREEN = "#46d6aa"
RED = "#ff8a70"
BORDER = "#263b33"
FONT = "Noto Sans"


def rounded_points(x1, y1, x2, y2, radius):
    radius = min(radius, max(1, (x2 - x1) / 2), max(1, (y2 - y1) / 2))
    points = []
    for cx, cy, start in ((x2-radius, y1+radius, -90), (x2-radius, y2-radius, 0),
                          (x1+radius, y2-radius, 90), (x1+radius, y1+radius, 180)):
        for step in range(9):
            angle = math.radians(start + step * 90 / 8)
            points.extend((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return points


class RoundedButton(tk.Canvas):
    """A small, dependency-free rounded button for a consistent Tk theme."""
    def __init__(self, parent, text="", command=None, style="", **kwargs):
        self.label = text
        self.command = command
        self.variant = style
        self.state = kwargs.pop("state", "normal")
        font = tkfont.Font(family=FONT, size=10, weight="bold" if style in {"Accent.TButton", "NavActive.TButton", "ModeActive.TButton"} else "normal")
        width = max(84, font.measure(text) + 34)
        try:
            parent_style = parent.cget("style")
        except tk.TclError:
            parent_style = ""
        surface = PANEL if parent_style in {"Header.TFrame", "Sidebar.TFrame", "Card.TFrame", "CardAlt.TFrame"} else BG
        super().__init__(parent, width=width, height=44, bg=kwargs.pop("bg", surface),
                         highlightthickness=0, bd=0, cursor="hand2")
        self.font = font
        self.bind("<Configure>", lambda _event: self._draw())
        self.bind("<Enter>", lambda _event: self._hover(True))
        self.bind("<Leave>", lambda _event: self._hover(False))
        self.bind("<Button-1>", self._click)
        self.bind("<space>", self._click)
        self._hovered = False
        self._draw()

    def configure(self, cnf=None, **kwargs):
        # Keep the familiar small subset of ttk.Button.configure used here.
        if isinstance(cnf, dict):
            kwargs.update(cnf)
        if "text" in kwargs:
            self.label = kwargs.pop("text")
            self.configure(width=max(84, self.font.measure(self.label) + 34))
        if "command" in kwargs:
            self.command = kwargs.pop("command")
        if "state" in kwargs:
            self.state = kwargs.pop("state")
        if "style" in kwargs:
            self.variant = kwargs.pop("style")
        result = super().configure(**kwargs) if kwargs else None
        self._draw()
        return result

    config = configure

    def _click(self, _event=None):
        if self.state != "disabled" and self.command:
            self.command()

    def _hover(self, value):
        self._hovered = value
        self._draw()

    def _draw(self):
        if not hasattr(self, "font"):
            return
        self.delete("all")
        width, height = max(1, self.winfo_width()), max(1, self.winfo_height())
        disabled = self.state == "disabled"
        if self.variant == "Accent.TButton":
            fill, fg = ACCENT, BG
            if self._hovered and not disabled:
                fill = "#68e3bb"
        elif self.variant == "Stop.TButton":
            fill, fg = ("#33231f" if not self._hovered else "#49302a"), "#ffb39e"
        elif self.variant in {"NavActive.TButton", "ModeActive.TButton"}:
            fill, fg = PANEL_ALT, ACCENT
            if self.variant == "ModeActive.TButton":
                fill, fg = ACCENT, BG
        elif self.variant == "Mode.TButton":
            fill, fg = ("#20372f" if self._hovered else PANEL_ALT), TEXT
        elif self.variant == "Nav.TButton":
            fill, fg = (PANEL_ALT if self._hovered else PANEL), (TEXT if self._hovered else MUTED)
        else:
            fill, fg = ("#20372f" if self._hovered else PANEL_ALT), TEXT
        if disabled:
            fill, fg = "#17211e", "#6f8178"
        self._round_rect(2, 2, width - 2, height - 2, 10, fill)
        anchor = "w" if self.variant in {"Nav.TButton", "NavActive.TButton"} else "center"
        x = 14 if anchor == "w" else width / 2
        self.create_text(x, height / 2, text=self.label, fill=fg, font=self.font, anchor=anchor)

    def _round_rect(self, x1, y1, x2, y2, radius, fill):
        r = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
        self.create_polygon(rounded_points(x1, y1, x2, y2, radius), fill=fill, outline="")


class RoundedCard(ttk.Frame):
    """Card with a subtle border, drawn behind its regular ttk contents."""
    def __init__(self, parent, padding=16):
        super().__init__(parent, style="Root.TFrame", padding=padding)
        self.surface = tk.Canvas(self, bg=BG, highlightthickness=0, bd=0)
        self.surface.place(x=0, y=0, relwidth=1, relheight=1)
        self.surface.tk.call("lower", self.surface._w)
        self.bind("<Configure>", self._draw_surface)

    def _draw_surface(self, _event=None):
        self.surface.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if width < 8 or height < 8:
            return
        x1, y1, x2, y2, radius = 1, 1, width - 1, height - 1, 19
        self.surface.create_polygon(rounded_points(x1, y1, x2, y2, radius), fill=PANEL, outline=BORDER, width=1)


def read_led_count() -> int:
    try:
        n = int(json.loads(CONFIG.read_text()).get("leds", 94))
        return n if n > 0 else 94
    except (OSError, ValueError, TypeError):
        return 94


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        # Keep Tk's point-sized fonts proportional to the selected 1220px mockup
        # on high-DPI 4K desktops where the system default is visibly oversized.
        self.tk.call("tk", "scaling", 1.0)
        self.title("Mitsuko G1 · Light Sync")
        self.logo_image = None
        self.brand_image = None
        try:
            self.logo_image = tk.PhotoImage(file=str(ROOT / "mitsuko-g1.png"))
            self.iconphoto(True, self.logo_image)
            self.brand_image = self.logo_image.subsample(6, 6)
        except (tk.TclError, OSError):
            self.logo_image = None
        self.geometry("1220x1040")
        self.after(1000, self._use_mockup_window_size)
        self.minsize(1080, 940)
        self.configure(bg=BG)
        self.proc: subprocess.Popen | None = None
        self.active_mode = ""
        self.last_process_error = ""
        self.external_pid = None
        self.portal_proc = None
        self._closing_after_stop = False
        self.effect_buttons = []
        self.manual_widgets = []
        self.nav_buttons = {}
        self.pages = {}
        self.messages: queue.Queue[tuple[str, str]] = queue.Queue()
        self.notification_monitor = None
        self.notification_flash_running = False
        self.pending_device_effect = None
        self.color = "#46d6aa"
        try:
            stored_color = json.loads(CONFIG.read_text()).get("color")
            if isinstance(stored_color, list) and len(stored_color) == 3:
                self.color = "#" + "".join(f"{max(0, min(255, int(channel))):02x}" for channel in stored_color)
        except (OSError, ValueError, TypeError):
            pass
        self._style()
        self._vars()
        self._layout()
        self._init_tray_indicator()
        self.after(700, self._restore_notification_monitor)
        self.after(100, self._poll)
        existing_pid = self._external_sync_pid()
        if existing_pid is not None:
            self.external_pid = existing_pid
            self.active_mode = "External live mode"
            self._clear_notification_state()
            self.status.set("Another live mode is controlling the strip")
            self.status_dot.itemconfigure(1, fill=GREEN)
            self.details.set("Stop the current mode to switch to music reactive or other effects")
            self.stop_btn.configure(command=self.stop_external_mode, state="normal")
            self._set_manual_controls(False)
        else:
            self.after(350, self.read_info)
        self.after(600, self.refresh_diagnostics)
        self.protocol("WM_DELETE_WINDOW", self._close_to_tray)
        self.bind_all("<Control-q>", lambda _event: self._on_close())

    def _style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Root.TFrame", background=BG)
        style.configure("Header.TFrame", background=PANEL)
        style.configure("Sidebar.TFrame", background=PANEL)
        style.configure("Card.TFrame", background=PANEL)
        style.configure("CardAlt.TFrame", background=PANEL_ALT)
        style.configure("TLabel", background=BG, foreground=TEXT, font=(FONT, 10))
        style.configure("Card.TLabel", background=PANEL, foreground=TEXT, font=(FONT, 10))
        style.configure("Muted.Card.TLabel", background=PANEL, foreground=MUTED, font=(FONT, 9))
        style.configure("Title.TLabel", background=PANEL, foreground=TEXT, font=(FONT, 14, "bold"))
        style.configure("Sub.TLabel", background=PANEL, foreground=MUTED, font=(FONT, 9))
        style.configure("PageTitle.TLabel", background=BG, foreground=TEXT, font=(FONT, 20, "bold"))
        style.configure("PageSub.TLabel", background=BG, foreground=MUTED, font=(FONT, 10))
        style.configure("Section.TLabel", background=PANEL, foreground=TEXT, font=(FONT, 12, "bold"))
        style.configure("Status.TLabel", background=PANEL, foreground=GREEN, font=(FONT, 10, "bold"))
        style.configure("TButton", font=(FONT, 10), padding=(12, 9), background=PANEL_ALT, foreground=TEXT, borderwidth=0)
        style.map("TButton", background=[("active", "#20372f"), ("disabled", "#17211e")], foreground=[("disabled", MUTED)])
        style.configure("Accent.TButton", background=ACCENT, foreground=BG, font=(FONT, 10, "bold"), padding=(15, 10))
        style.map("Accent.TButton", background=[("active", "#68e3bb"), ("disabled", "#29463b")])
        style.configure("Stop.TButton", background="#33231f", foreground="#ffb39e", font=(FONT, 10, "bold"), padding=(13, 9))
        style.map("Stop.TButton", background=[("active", "#49302a"), ("disabled", PANEL_ALT)], foreground=[("disabled", MUTED)])
        style.configure("Nav.TButton", background=PANEL, foreground=MUTED, anchor="w", padding=(12, 11), font=(FONT, 10))
        style.map("Nav.TButton", background=[("active", PANEL_ALT)], foreground=[("active", TEXT)])
        style.configure("NavActive.TButton", background=PANEL_ALT, foreground=ACCENT, anchor="w", padding=(12, 11), font=(FONT, 10, "bold"))
        style.map("NavActive.TButton", background=[("active", PANEL_ALT)])
        style.configure("Mode.TRadiobutton", background=PANEL, foreground=TEXT, padding=(8, 8), font=(FONT, 10))
        style.map("Mode.TRadiobutton", background=[("selected", PANEL_ALT), ("active", PANEL_ALT)], foreground=[("selected", ACCENT), ("disabled", MUTED)])
        style.configure("TEntry", fieldbackground="#0b1713", foreground=TEXT, insertcolor=TEXT, bordercolor=BORDER, padding=7, font=(FONT, 10))
        style.configure("TSpinbox", fieldbackground="#0b1713", foreground=TEXT, arrowcolor=TEXT, bordercolor=BORDER, padding=6, font=(FONT, 10))
        style.configure("TCombobox", fieldbackground="#0b1713", background=PANEL_ALT, foreground=TEXT,
                        arrowcolor=ACCENT, bordercolor=BORDER, padding=7, font=(FONT, 10))
        style.map("TCombobox", fieldbackground=[("readonly", "#0b1713")],
                  foreground=[("readonly", TEXT)], selectbackground=[("readonly", PANEL_ALT)],
                  selectforeground=[("readonly", TEXT)])
        style.configure("Horizontal.TScale", background=PANEL, troughcolor="#263b33")
        style.configure("TSeparator", background=BORDER)

    def _use_mockup_window_size(self):
        # GNOME can restore the previous maximized state for Tk windows.
        # The chosen layout is designed around a compact, resizable window.
        try:
            self.wm_attributes("-zoomed", False)
        except tk.TclError:
            pass

        try:
            self.state("normal")
            import ctypes
            x11 = ctypes.CDLL("libX11.so.6")
            x11.XOpenDisplay.restype = ctypes.c_void_p
            x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
            x11.XDefaultRootWindow.restype = ctypes.c_ulong
            x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
            x11.XInternAtom.restype = ctypes.c_ulong
            x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
            x11.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.c_void_p]
            x11.XFlush.argtypes = [ctypes.c_void_p]
            x11.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
                ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)), ctypes.POINTER(ctypes.c_uint)]
            x11.XFree.argtypes = [ctypes.c_void_p]
            display = x11.XOpenDisplay(None)
            if display:
                root = x11.XDefaultRootWindow(display)
                state_atom = x11.XInternAtom(display, b"_NET_WM_STATE", 0)
                horz = x11.XInternAtom(display, b"_NET_WM_STATE_MAXIMIZED_HORZ", 0)
                vert = x11.XInternAtom(display, b"_NET_WM_STATE_MAXIMIZED_VERT", 0)
                class ClientMessage(ctypes.Structure):
                    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong),
                                ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
                                ("window", ctypes.c_ulong), ("message_type", ctypes.c_ulong),
                                ("format", ctypes.c_int), ("data", ctypes.c_long * 5)]
                storage = ctypes.create_string_buffer(192)
                event = ctypes.cast(storage, ctypes.POINTER(ClientMessage)).contents
                event.type, event.send_event, event.display = 33, 1, display
                frame = int(self.tk.call("wm", "frame", self._w), 0)
                tree_root, parent = ctypes.c_ulong(), ctypes.c_ulong()
                children, count = ctypes.POINTER(ctypes.c_ulong)(), ctypes.c_uint()
                x11.XQueryTree(display, frame, ctypes.byref(tree_root), ctypes.byref(parent),
                               ctypes.byref(children), ctypes.byref(count))
                window = children[0] if count.value else frame
                event.window, event.message_type, event.format = window, state_atom, 32
                event.data[:] = (0, horz, vert, 1, 0)
                x11.XSendEvent(display, root, 0, (1 << 19) | (1 << 20), storage)
                x11.XFlush(display)
                if count.value:
                    x11.XFree(children)
            self.after(350, lambda: self.geometry("1220x1040"))
        except (tk.TclError, OSError, TypeError, ValueError, AttributeError):
            pass

    def _vars(self):
        settings = {}
        try:
            settings = json.loads(GUI_CONFIG.read_text())
        except (OSError, ValueError):
            pass
        counts = settings.get("counts", {"bottom": 30, "left": 17, "top": 30, "right": 17})
        self.leds = tk.StringVar(value=str(settings.get("leds", read_led_count())))
        self.edge = {side: tk.StringVar(value=str(counts.get(side, n))) for side, n in
                     (("bottom", 30), ("left", 17), ("top", 30), ("right", 17))}
        self.fps = tk.StringVar(value=str(settings.get("fps", 20)))
        self.smooth = tk.DoubleVar(value=float(settings.get("smooth", 0.7)))
        self.depth = tk.DoubleVar(value=float(settings.get("depth", 0.2)))
        self.brightness = tk.IntVar(value=max(5, min(255, int(settings.get("brightness", 255)))))
        self.monitor = tk.StringVar(value=str(settings.get("monitor", 1)))
        self.profiles = settings.get("profiles", {}) if isinstance(settings.get("profiles", {}), dict) else {}
        self.profile_name = tk.StringVar(value="")
        self.diagnostic = tk.StringVar(value="Run a connection check to see USB and capture status.")
        self.status = tk.StringVar(value="Checking for the G1…")
        self.details = tk.StringVar(value="USB device status")
        self.smooth_value = tk.StringVar(value=f"{self.smooth.get():.2f}")
        self.depth_value = tk.StringVar(value=f"{self.depth.get():.2f}")
        self.audio_sensitivity = tk.DoubleVar(value=float(settings.get("audio_sensitivity", 1.5)))
        self.audio_sensitivity_value = tk.StringVar(value=f"{self.audio_sensitivity.get():.1f}×")
        self.notification_flash_enabled = tk.BooleanVar(value=bool(settings.get("notification_flash", False)))
        self.notification_restore_state = self._valid_notification_state(settings.get("notification_restore_state"))
        saved_mode = settings.get("audio_mode", "combined")
        self.audio_mode = tk.StringVar(value=saved_mode if saved_mode in {"combined", "bass", "treble", "voice"} else "combined")

    @staticmethod
    def _valid_notification_state(value):
        if not isinstance(value, list) or not value:
            return None
        if value[0] == "off" and len(value) == 1:
            return ("off",)
        if value[0] == "color" and len(value) == 4:
            try:
                channels = tuple(max(0, min(255, int(item))) for item in value[1:])
                return ("color", *channels)
            except (TypeError, ValueError):
                return None
        if value[0] == "effect" and len(value) == 3:
            try:
                return ("effect", int(value[1]), int(value[2]))
            except (TypeError, ValueError):
                return None
        return None

    def _save_notification_preference(self):
        try:
            settings = json.loads(GUI_CONFIG.read_text())
        except (OSError, ValueError):
            settings = {}
        settings["notification_flash"] = bool(self.notification_flash_enabled.get())
        GUI_CONFIG.write_text(json.dumps(settings, indent=2) + "\n")

    def _toggle_notification_flash(self):
        enabled = bool(self.notification_flash_enabled.get())
        self._save_notification_preference()
        if enabled:
            if not self._start_notification_monitor():
                self.notification_flash_enabled.set(False)
                self._save_notification_preference()
                return
            self.details.set("Notification flash enabled · set a color or built-in effect to establish a restore state")
        else:
            self._stop_notification_monitor()
            self.details.set("Notification flash disabled")

    def _restore_notification_monitor(self):
        if self.notification_flash_enabled.get():
            self._start_notification_monitor()

    def _start_notification_monitor(self):
        if self.notification_monitor is not None and self.notification_monitor.poll() is None:
            return True
        monitor = shutil.which("dbus-monitor")
        if not monitor:
            messagebox.showerror("Notification flash unavailable",
                "dbus-monitor is missing. Install the latest Mitsuko G1 package, which includes the required D-Bus tools.",
                parent=self)
            return False
        match = "type='method_call',interface='org.freedesktop.Notifications',member='Notify'"
        try:
            process = subprocess.Popen([monitor, "--session", match], stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1, start_new_session=True)
        except OSError as exc:
            messagebox.showerror("Notification flash unavailable", str(exc), parent=self)
            return False
        self.notification_monitor = process
        threading.Thread(target=self._read_notification_monitor, args=(process,), daemon=True).start()
        return True

    def _read_notification_monitor(self, process):
        if process.stdout is not None:
            for line in process.stdout:
                if "org.freedesktop.Notifications" in line and "member=Notify" in line:
                    self.messages.put(("notification", ""))
        code = process.wait()
        if self.notification_monitor is process and code != 0:
            self.messages.put(("notification_monitor_error", "D-Bus notification monitoring stopped."))

    def _stop_notification_monitor(self):
        process, self.notification_monitor = self.notification_monitor, None
        if process is not None and process.poll() is None:
            process.terminate()

    def _remember_notification_state(self, state):
        self.notification_restore_state = tuple(state)
        try:
            settings = json.loads(GUI_CONFIG.read_text())
        except (OSError, ValueError):
            settings = {}
        settings["notification_restore_state"] = list(state)
        GUI_CONFIG.write_text(json.dumps(settings, indent=2) + "\n")

    def _clear_notification_state(self):
        self.notification_restore_state = None
        try:
            settings = json.loads(GUI_CONFIG.read_text())
        except (OSError, ValueError):
            settings = {}
        settings.pop("notification_restore_state", None)
        GUI_CONFIG.write_text(json.dumps(settings, indent=2) + "\n")

    def _notification_flash(self):
        if (not self.notification_flash_enabled.get() or self.notification_flash_running
                or self.proc is not None or self.pending_device_effect is not None
                or self._external_sync_pid() is not None):
            return
        restore = self.notification_restore_state
        if restore is None:
            return
        flash_rgb = tuple(int(self.color[index:index + 2], 16) for index in (1, 3, 5))
        flash_command = self._base_cmd() + ["color", *(str(channel) for channel in flash_rgb)]
        if restore[0] == "color":
            restore_command = self._base_cmd() + ["color", *(str(channel) for channel in restore[1:])]
        elif restore[0] == "effect":
            restore_command = self._base_cmd() + ["effect", str(restore[1]), str(restore[2])]
        else:
            restore_command = self._base_cmd() + ["off"]
        self.notification_flash_running = True
        self._set_manual_controls(False)

        def worker():
            error = ""
            try:
                result = subprocess.run(flash_command, cwd=ROOT, env=self._env(),
                                         capture_output=True, text=True, timeout=5)
                if result.returncode != 0:
                    raise RuntimeError((result.stderr or result.stdout).strip() or "Could not flash the strip.")
                time.sleep(0.45)
                if self.proc is None and self._external_sync_pid() is None:
                    result = subprocess.run(restore_command, cwd=ROOT, env=self._env(),
                                            capture_output=True, text=True, timeout=5)
                    if result.returncode != 0:
                        raise RuntimeError((result.stderr or result.stdout).strip() or "Could not restore the previous light state.")
            except Exception as exc:
                error = str(exc)
            self.messages.put(("notification_flash_complete", error))

        threading.Thread(target=worker, daemon=True).start()

    def _card(self, parent, padding=16):
        return RoundedCard(parent, padding=padding)

    def _layout(self):
        shell = RoundedCard(self, padding=0)
        shell.pack(fill="both", expand=True, padx=22, pady=14)

        header = ttk.Frame(shell, style="Header.TFrame", padding=(20, 13))
        header.pack(fill="x", padx=1, pady=(1, 0))
        brand_mark = (tk.Label(header, image=self.brand_image, bg=PANEL, bd=0)
                      if self.brand_image is not None else
                      tk.Label(header, text="G1", bg=ACCENT, fg=BG, font=(FONT, 10, "bold"),
                               width=4, height=2, relief="flat"))
        brand_mark.pack(side="left", padx=(0, 11))
        title_box = ttk.Frame(header, style="Header.TFrame")
        title_box.pack(side="left")
        ttk.Label(title_box, text="Mitsuko G1 / Light Sync", style="Title.TLabel").pack(anchor="w")

        status_box = ttk.Frame(header, style="Header.TFrame")
        status_box.pack(side="left", fill="x", expand=True, padx=(28, 12))
        self.status_dot = tk.Canvas(status_box, width=12, height=12, bg=PANEL, highlightthickness=0)
        self.status_dot.create_oval(2, 2, 10, 10, fill=MUTED, outline="")
        self.status_dot.pack(side="left", padx=(0, 9))
        status_labels = ttk.Frame(status_box, style="Header.TFrame")
        status_labels.pack(side="left", fill="x", expand=True)
        ttk.Label(status_labels, textvariable=self.status, style="Status.TLabel").pack(anchor="w")
        ttk.Label(status_labels, textvariable=self.details, style="Muted.Card.TLabel").pack(anchor="w", pady=(2, 0))
        RoundedButton(header, text="Phone Wi-Fi setup", command=self.show_phone_pairing).pack(side="right", padx=(8, 0))
        RoundedButton(header, text="Refresh", command=self.read_info).pack(side="right", padx=(8, 0))

        body = ttk.Frame(shell, style="Root.TFrame")
        body.pack(fill="both", expand=True, padx=1, pady=(0, 1))
        sidebar = ttk.Frame(body, style="Sidebar.TFrame", padding=(11, 17))
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        sidebar.configure(width=220)
        ttk.Label(sidebar, text="DEVICE", style="Muted.Card.TLabel").pack(anchor="w", padx=9, pady=(0, 9))
        nav_items = (("Music", "Music reactive"), ("Screen", "Screen sync"),
                     ("Effects", "Lighting effects"), ("Controls", "Quick controls"))
        for key, label in nav_items:
            button = RoundedButton(sidebar, text=label, style="Nav.TButton",
                                command=lambda name=key: self._show_page(name))
            button.pack(fill="x", pady=2)
            self.nav_buttons[key] = button
        ttk.Separator(sidebar).pack(fill="x", pady=(15, 12))
        ttk.Label(sidebar, text="94 LEDS  ·  USB HID", style="Muted.Card.TLabel").pack(anchor="w", padx=9)
        ttk.Label(sidebar, text="Music uses PipeWire playback", style="Muted.Card.TLabel", wraplength=145).pack(anchor="w", padx=9, pady=(7, 0))

        content = ttk.Frame(body, style="Root.TFrame", padding=(22, 18, 22, 15))
        content.pack(side="left", fill="both", expand=True)
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)
        for key, builder in (("Music", self._build_music_page), ("Screen", self._build_screen_page),
                             ("Effects", self._build_effects_page), ("Controls", self._build_controls_page)):
            page = ttk.Frame(content, style="Root.TFrame")
            page.grid(row=0, column=0, sticky="nsew")
            self.pages[key] = page
            builder(page)
        sidebar.pack_forget()
        self._show_page("Music")

    def show_phone_pairing(self):
        try:
            pairing = json.loads(PHONE_PAIRING.read_text())
            pin = str(pairing.get("pin", ""))
        except (OSError, ValueError):
            pin = ""
        addresses = []
        try:
            result = subprocess.run(["ip", "-4", "-o", "addr", "show", "scope", "global"],
                                    capture_output=True, text=True, timeout=3)
            for line in result.stdout.splitlines():
                fields = line.split()
                if len(fields) >= 4:
                    address = fields[3].split("/", 1)[0]
                    if not address.startswith(("127.", "169.254.")):
                        addresses.append(address)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if not pin:
            messagebox.showerror("Phone Wi-Fi setup", "The phone relay is not running yet. Open Mitsuko G1 and try again.", parent=self)
            return
        host = ", ".join(dict.fromkeys(addresses)) or "No Wi-Fi address found"
        messagebox.showinfo("Connect phone over Wi-Fi",
            "Connect your phone and laptop to the same Wi-Fi network.\n\n"
            f"Laptop address: {host}\nPairing code: {pin}\n\n"
            "In the Mitsuko G1 phone app, choose Wi-Fi setup and enter the laptop address and pairing code.\n"
            "The code is for pairing only; the app saves a private access token afterward.", parent=self)

    def _show_page(self, name):
        self.pages[name].tkraise()
        for key, button in self.nav_buttons.items():
            button.configure(style="NavActive.TButton" if key == name else "Nav.TButton")

    def _page_heading(self, parent, title, subtitle):
        heading = ttk.Frame(parent, style="Root.TFrame")
        heading.pack(fill="x", pady=(0, 12))
        title_row = ttk.Frame(heading, style="Root.TFrame")
        title_row.pack(fill="x")
        ttk.Label(title_row, text=title, style="PageTitle.TLabel").pack(side="left")
        if title != "Music ambience":
            RoundedButton(title_row, text="← Music ambience", style="Nav.TButton",
                          command=lambda: self._show_page("Music")).pack(side="right")
        ttk.Label(heading, text=subtitle, style="PageSub.TLabel").pack(anchor="w", pady=(3, 0))

    def _build_music_page(self, page):
        self._page_heading(page, "Music ambience", "A clear preview of how each part of the song maps around your monitor.")
        columns = ttk.Frame(page, style="Root.TFrame")
        columns.pack(fill="both", expand=True)
        columns.columnconfigure(0, weight=7, uniform="music")
        columns.columnconfigure(1, weight=5, uniform="music")
        columns.rowconfigure(0, weight=1)

        preview = self._card(columns, padding=15)
        preview.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        ttk.Label(preview, text="PERIMETER MAP", style="Muted.Card.TLabel").pack(anchor="w")
        self.preview_mode_label = ttk.Label(preview, text=f"{self.audio_mode.get().title()} mode", style="Section.TLabel")
        self.preview_mode_label.pack(anchor="w", pady=(4, 0))
        ttk.Label(preview, text="Bass on the bottom · voice and mids on the sides · treble on top",
                  style="Muted.Card.TLabel", wraplength=410).pack(anchor="w", pady=(4, 5))
        self.preview_canvas = tk.Canvas(preview, width=330, height=220, bg=PANEL, highlightthickness=0)
        self.preview_canvas.pack(fill="both", expand=True, pady=(4, 0))
        self.preview_canvas.bind("<Configure>", self._update_perimeter_preview)
        for color, label, detail in (("#ffbd66", "Bass · bottom", "Beat pulse"),
                                     ("#c99be8", "Voice / mids · sides", "Color body"),
                                     ("#9580ff", "Treble · top", "Transient accents")):
            row = tk.Frame(preview, bg=PANEL_ALT, padx=12, pady=8)
            row.pack(fill="x", pady=(5, 0))
            dot = tk.Canvas(row, width=12, height=12, bg=PANEL_ALT, highlightthickness=0)
            dot.create_oval(1, 1, 11, 11, fill=color, outline="")
            dot.pack(side="left", padx=(0, 8))
            tk.Label(row, text=label, bg=PANEL_ALT, fg=TEXT, font=(FONT, 9)).pack(side="left")
            tk.Label(row, text=detail, bg=PANEL_ALT, fg=MUTED, font=(FONT, 9)).pack(side="right")

        controls_column = ttk.Frame(columns, style="Root.TFrame")
        controls_column.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        controls = self._card(controls_column, padding=14)
        controls.pack(fill="x")
        ttk.Label(controls, text="Sync mode", style="Section.TLabel").pack(anchor="w", pady=(0, 8))

        mode_grid = ttk.Frame(controls, style="Card.TFrame")
        mode_grid.pack(fill="x")
        self.music_mode_buttons = []
        for index, (label, value) in enumerate((("Combined", "combined"), ("Bass", "bass"),
                                                 ("Treble", "treble"), ("Voice · approx.", "voice"))):
            button = RoundedButton(mode_grid, text=label, style="Mode.TButton",
                                   command=lambda v=value: self._select_music_mode(v))
            button.grid(row=index // 2, column=index % 2, sticky="ew", padx=3, pady=3)
            mode_grid.columnconfigure(index % 2, weight=1)
            button.mode_value = value
            self.music_mode_buttons.append(button)
            self.manual_widgets.append(button)
        self._refresh_music_modes()

        sensitivity_header = ttk.Frame(controls, style="Card.TFrame")
        sensitivity_header.pack(fill="x", pady=(13, 0))
        ttk.Label(sensitivity_header, text="Sensitivity", style="Muted.Card.TLabel").pack(side="left")
        ttk.Label(sensitivity_header, textvariable=self.audio_sensitivity_value, style="Card.TLabel").pack(side="right")
        self.audio_sensitivity_scale = ttk.Scale(controls, from_=0.2, to=5.0, variable=self.audio_sensitivity,
                                                  command=lambda value: self.audio_sensitivity_value.set(f"{float(value):.1f}×"))
        self.audio_sensitivity_scale.pack(fill="x", pady=(5, 14))
        self.manual_widgets.append(self.audio_sensitivity_scale)
        self.music_btn = RoundedButton(controls, text="Start music reactive", style="Accent.TButton", command=self.start_music)
        self.music_btn.pack(fill="x")
        self.manual_widgets.append(self.music_btn)
        color_card = self._card(controls_column, padding=14)
        color_card.pack(fill="x", pady=(10, 0))
        ttk.Label(color_card, text="LED COLOR", style="Muted.Card.TLabel").pack(anchor="w")
        color_row = ttk.Frame(color_card, style="Card.TFrame")
        color_row.pack(fill="x", pady=(8, 0))
        ttk.Label(color_row, text="Palette anchor", style="Card.TLabel").pack(side="left")
        self.music_color_swatch = tk.Canvas(color_row, width=34, height=34, bg=PANEL,
                                            highlightthickness=0, cursor="hand2")
        self.music_color_swatch.pack(side="right")
        self.music_color_swatch.bind("<Button-1>", lambda _e: self.choose_color())
        self.manual_widgets.append(self.music_color_swatch)
        self._update_music_color_swatch()
        bright_header = ttk.Frame(color_card, style="Card.TFrame")
        bright_header.pack(fill="x", pady=(8, 0))
        ttk.Label(bright_header, text="Brightness", style="Muted.Card.TLabel").pack(side="left")
        self.music_brightness_label = tk.StringVar(value=str(self.brightness.get()))
        ttk.Label(bright_header, textvariable=self.music_brightness_label, style="Card.TLabel").pack(side="right")
        self.music_apply_brightness_btn = RoundedButton(bright_header, text="Apply", command=self.apply_brightness)
        self.music_apply_brightness_btn.pack(side="right", padx=(0, 8))
        self.manual_widgets.append(self.music_apply_brightness_btn)
        self.music_brightness_scale = ttk.Scale(color_card, from_=5, to=255, variable=self.brightness,
            command=lambda value: self.music_brightness_label.set(str(round(float(value)))))
        self.music_brightness_scale.pack(fill="x", pady=(4, 0))
        self.manual_widgets.append(self.music_brightness_scale)

        self.stop_btn = RoundedButton(controls_column, text="Stop live mode", style="Accent.TButton",
            command=lambda: self.stop_sync() if self.proc is not None else self.stop_external_mode(), state="disabled")
        self.stop_btn.pack(fill="x", pady=(10, 0))

        other = self._card(controls_column, padding=14)
        other.pack(fill="x", pady=(10, 0))
        ttk.Label(other, text="OTHER SECTIONS", style="Muted.Card.TLabel").pack(anchor="w", pady=(0, 6))
        for label, target in (("Screen sync", "Screen"), ("Lighting effects", "Effects"), ("Quick controls", "Controls")):
            link = RoundedButton(other, text=label, style="Nav.TButton",
                                 command=lambda name=target: self._show_page(name))
            link.pack(fill="x", pady=2)

    def _update_perimeter_preview(self, _event=None):
        canvas = getattr(self, "preview_canvas", None)
        if canvas is None or not canvas.winfo_exists():
            return
        canvas.delete("all")
        width = max(300, canvas.winfo_width())
        height = max(190, canvas.winfo_height())
        monitor_width = min(width - 150, (height - 55) * 1.65)
        monitor_width = max(190, monitor_width)
        monitor_height = monitor_width / 1.65
        left, right = (width - monitor_width) / 2, (width + monitor_width) / 2
        top, bottom = (height - monitor_height) / 2, (height + monitor_height) / 2
        mode = self.audio_mode.get()
        active = {"combined": {"bass", "voice", "treble"}, "bass": {"bass"},
                  "voice": {"voice"}, "treble": {"treble"}}.get(mode, {"bass", "voice", "treble"})
        muted = "#354151"
        bass = "#f0b15e" if "bass" in active else muted
        voice = "#a892ef" if "voice" in active else muted
        treble = "#76b9f4" if "treble" in active else muted
        canvas.create_rectangle(left, top, right, bottom, fill=BG, outline=BORDER, width=2)
        canvas.create_line(left + 2, top, right - 2, top, fill=treble, width=7, capstyle=tk.ROUND)
        canvas.create_line(left + 2, bottom, right - 2, bottom, fill=bass, width=7, capstyle=tk.ROUND)
        canvas.create_line(left, top + 2, left, bottom - 2, fill=voice, width=7, capstyle=tk.ROUND)
        canvas.create_line(right, top + 2, right, bottom - 2, fill="#8cbcff" if "voice" in active else muted, width=7, capstyle=tk.ROUND)
        canvas.create_text(width / 2, top - 19, text="TREBLE  ·  TOP", fill=treble, font=(FONT, 9, "bold"))
        canvas.create_text(width / 2, bottom + 20, text="BASS  ·  BOTTOM", fill=bass, font=(FONT, 9, "bold"))
        canvas.create_text(width / 2, height / 2 - 6, text=mode.title(), fill=TEXT, font=(FONT, 10, "bold"))
        canvas.create_text(width / 2, height / 2 + 15, text="Music reactive", fill=MUTED, font=(FONT, 9))

    def _select_music_mode(self, value):
        self.audio_mode.set(value)
        self.preview_mode_label.configure(text=f"{value.title()} mode")
        self._refresh_music_modes()
        self._update_perimeter_preview()

    def _refresh_music_modes(self):
        for button in getattr(self, "music_mode_buttons", ()):
            button.configure(style="ModeActive.TButton" if button.mode_value == self.audio_mode.get() else "Mode.TButton")

    def _update_music_color_swatch(self):
        canvas = getattr(self, "music_color_swatch", None)
        if canvas is not None and canvas.winfo_exists():
            canvas.delete("all")
            canvas.create_oval(2, 2, 32, 32, fill=self.color, outline=ACCENT, width=2)

    def _refresh_palette_swatches(self):
        for swatch in getattr(self, "palette_swatches", ()):
            if not swatch.winfo_exists():
                continue
            swatch.delete("all")
            selected = swatch.palette_color.lower() == self.color.lower()
            swatch.create_oval(4, 4, 36, 36, fill=swatch.palette_color,
                               outline=ACCENT if selected else BORDER,
                               width=3 if selected else 1)

    def _set_color_preview(self, color):
        self.color = color
        color_button = getattr(self, "color_btn", None)
        if color_button is not None and color_button.winfo_exists():
            color_button.configure(bg=color, fg=BG, activebackground=color, activeforeground=BG)
        color_value = getattr(self, "color_value", None)
        if color_value is not None:
            color_value.set(color.upper())
        self._update_music_color_swatch()
        self._refresh_palette_swatches()

    def select_palette_color(self, color):
        self._set_color_preview(color)
        self.set_color()

    def _build_screen_page(self, page):
        self._page_heading(page, "Screen sync", "Map monitor-edge colors to the LED strip.")
        profiles = self._card(page, padding=12)
        profiles.pack(fill="x", pady=(0, 10))
        ttk.Label(profiles, text="SAVED PROFILES", style="Muted.Card.TLabel").pack(side="left", padx=(0, 10))
        self.profile_combo = ttk.Combobox(profiles, textvariable=self.profile_name, state="readonly", width=24,
                                          values=sorted(self.profiles))
        self.profile_combo.pack(side="left", padx=(0, 8))
        self.profile_combo.bind("<<ComboboxSelected>>", self._load_profile)
        RoundedButton(profiles, text="Save profile…", command=self._save_profile).pack(side="left")
        layout = self._card(page, padding=16)
        layout.pack(fill="x", pady=(0, 12))
        ttk.Label(layout, text="STRIP LAYOUT", style="Muted.Card.TLabel").pack(anchor="w")
        ttk.Label(layout, text="LED count and edge allocation", style="Section.TLabel").pack(anchor="w", pady=(4, 12))
        general = ttk.Frame(layout, style="Card.TFrame")
        general.pack(fill="x", pady=(0, 12))
        for index, (label, variable, width) in enumerate((("Total LEDs", self.leds, 9), ("Target FPS", self.fps, 9),
                                                          ("Monitor # (X11)", self.monitor, 9))):
            cell = ttk.Frame(general, style="Card.TFrame")
            cell.grid(row=0, column=index, sticky="w", padx=(0, 20))
            ttk.Label(cell, text=label, style="Muted.Card.TLabel").pack(anchor="w", pady=(0, 5))
            entry = ttk.Entry(cell, textvariable=variable, width=width)
            entry.pack(anchor="w")
            self.manual_widgets.append(entry)
        edges = ttk.Frame(layout, style="Card.TFrame")
        edges.pack(fill="x")
        for index, side in enumerate(("bottom", "left", "top", "right")):
            cell = ttk.Frame(edges, style="Card.TFrame")
            cell.grid(row=0, column=index, sticky="ew", padx=(0, 8))
            edges.columnconfigure(index, weight=1)
            ttk.Label(cell, text=side.title(), style="Muted.Card.TLabel").pack(anchor="w", pady=(0, 5))
            entry = ttk.Entry(cell, textvariable=self.edge[side], width=8)
            entry.pack(fill="x")
            self.manual_widgets.append(entry)
        ttk.Label(layout, text="Counts must add up to the total. Strip order follows the monitor perimeter.",
                  style="Muted.Card.TLabel").pack(anchor="w", pady=(10, 0))

        tuning = self._card(page, padding=16)
        tuning.pack(fill="x", pady=(0, 12))
        ttk.Label(tuning, text="CAPTURE TUNING", style="Muted.Card.TLabel").pack(anchor="w", pady=(0, 10))
        sliders = ttk.Frame(tuning, style="Card.TFrame")
        sliders.pack(fill="x")
        self._slider(sliders, "Smoothing", self.smooth, self.smooth_value, 0, 0.95, 0)
        self._slider(sliders, "Edge sampling depth", self.depth, self.depth_value, 0.05, 0.5, 1)
        diagnostics = self._card(page, padding=12)
        diagnostics.pack(fill="x", pady=(0, 10))
        diag_row = ttk.Frame(diagnostics, style="Card.TFrame")
        diag_row.pack(fill="x")
        ttk.Label(diag_row, textvariable=self.diagnostic, style="Muted.Card.TLabel", wraplength=620).pack(side="left", fill="x", expand=True)
        RoundedButton(diag_row, text="Check connections", command=self.refresh_diagnostics).pack(side="right")
        actions = ttk.Frame(page, style="Root.TFrame")
        actions.pack(fill="x", pady=(2, 0))
        self.start_btn = RoundedButton(actions, text="Start screen sync", style="Accent.TButton", command=self.start_sync)
        self.start_btn.pack(side="left")
        self.choose_monitor_btn = RoundedButton(actions, text="Choose monitor…", command=self.choose_monitor)
        self.choose_monitor_btn.pack(side="left", padx=(8, 0))
        self.manual_widgets.append(self.start_btn)
        ttk.Label(actions, text="On Wayland, use Choose monitor to select a screen in the portal.",
                  style="PageSub.TLabel").pack(side="left", padx=(14, 0))

    def _build_effects_page(self, page):
        self._page_heading(page, "Lighting effects", "Firmware presets run on the G1; app effects stream from your computer.")
        device = self._card(page, padding=16)
        device.pack(fill="x", pady=(0, 12))
        ttk.Label(device, text="DEVICE PRESETS", style="Muted.Card.TLabel").pack(anchor="w", pady=(0, 8))
        self._effect_group(device, "Dynamic · built in", [(f"Dynamic {i}", 2, i - 1) for i in range(1, 8)])
        ttk.Separator(device).pack(fill="x", pady=12)
        self._effect_group(device, "Pickup · sound reactive, built in", [(f"Pickup {i}", 3, i - 1) for i in range(1, 8)])
        software = self._card(page, padding=16)
        software.pack(fill="x")
        ttk.Label(software, text="APP EFFECTS", style="Muted.Card.TLabel").pack(anchor="w", pady=(0, 8))
        self._software_effect_group(software)

    def _build_controls_page(self, page):
        self._page_heading(page, "Quick controls", "Set a steady color, adjust brightness, or switch the strip off.")
        color_card = self._card(page, padding=17)
        color_card.pack(fill="x", pady=(0, 12))
        ttk.Label(color_card, text="COLOR", style="Muted.Card.TLabel").pack(anchor="w")
        ttk.Label(color_card, text="Choose a palette anchor or apply a solid color", style="Section.TLabel").pack(anchor="w", pady=(4, 13))
        swatches = ttk.Frame(color_card, style="Card.TFrame")
        swatches.pack(fill="x", pady=(0, 12))
        self.palette_swatches = []
        for name, color in (("Mint", "#46d6aa"), ("Blue", "#7ea7ff"),
                            ("Violet", "#c49bff"), ("Coral", "#ff8a70"),
                            ("Amber", "#ffd36d"), ("Soft white", "#f4f4f0")):
            swatch = tk.Canvas(swatches, width=40, height=40, bg=PANEL, highlightthickness=0,
                               cursor="hand2")
            swatch.pack(side="left", padx=(0, 8))
            swatch.bind("<Button-1>", lambda _event, value=color: self.select_palette_color(value))
            swatch.bind("<Enter>", lambda _event, widget=swatch: widget.configure(cursor="hand2"))
            swatch.palette_color = color
            self.palette_swatches.append(swatch)
            self.manual_widgets.append(swatch)
        self._refresh_palette_swatches()
        color_row = ttk.Frame(color_card, style="Card.TFrame")
        color_row.pack(fill="x")
        self.color_btn = tk.Button(color_row, text="Choose color", command=self.choose_color,
                                   bg=PANEL_ALT, fg=TEXT, activebackground="#20372f",
                                   activeforeground=TEXT, relief="flat", bd=0,
                                   font=(FONT, 10, "bold"), padx=14, pady=10)
        self.color_btn.pack(side="left")
        self.manual_widgets.append(self.color_btn)
        self.color_value = tk.StringVar(value=self.color.upper())
        ttk.Label(color_row, textvariable=self.color_value, style="Card.TLabel").pack(side="left", padx=(11, 0))
        self.set_color_btn = RoundedButton(color_row, text="Apply solid color", command=self.set_color)
        self.set_color_btn.pack(side="right")
        self.manual_widgets.append(self.set_color_btn)

        brightness_card = self._card(page, padding=17)
        brightness_card.pack(fill="x", pady=(0, 12))
        bright_header = ttk.Frame(brightness_card, style="Card.TFrame")
        bright_header.pack(fill="x")
        ttk.Label(bright_header, text="BRIGHTNESS", style="Muted.Card.TLabel").pack(side="left")
        self.brightness_label = tk.StringVar(value=str(self.brightness.get()))
        ttk.Label(bright_header, textvariable=self.brightness_label, style="Card.TLabel").pack(side="right")
        self.brightness_scale = ttk.Scale(brightness_card, from_=5, to=255, variable=self.brightness,
                                          command=lambda value: self.brightness_label.set(str(round(float(value)))))
        self.brightness_scale.pack(fill="x", pady=(11, 12))
        self.manual_widgets.append(self.brightness_scale)
        self.apply_brightness_btn = RoundedButton(brightness_card, text="Apply brightness", command=self.apply_brightness)
        self.apply_brightness_btn.pack(anchor="w")
        self.manual_widgets.append(self.apply_brightness_btn)

        power = self._card(page, padding=17)
        power.pack(fill="x")
        ttk.Label(power, text="POWER", style="Muted.Card.TLabel").pack(anchor="w")
        ttk.Label(power, text="Turn the LED strip off", style="Section.TLabel").pack(anchor="w", pady=(4, 12))
        self.off_btn = RoundedButton(power, text="Turn LEDs off", style="Stop.TButton", command=self.turn_off)
        self.off_btn.pack(anchor="w")
        self.manual_widgets.append(self.off_btn)

        notification = self._card(page, padding=17)
        notification.pack(fill="x", pady=(12, 0))
        notification_header = ttk.Frame(notification, style="Card.TFrame")
        notification_header.pack(fill="x")
        ttk.Label(notification_header, text="NOTIFICATION FLASH", style="Muted.Card.TLabel").pack(side="left")
        self.notification_toggle = ttk.Checkbutton(
            notification_header, text="Enable", variable=self.notification_flash_enabled,
            command=self._toggle_notification_flash)
        self.notification_toggle.pack(side="right")
        ttk.Label(notification, text="Briefly flash the current palette color when a desktop notification arrives.",
                  style="Section.TLabel", wraplength=700).pack(anchor="w", pady=(8, 5))
        ttk.Label(notification,
                  text="Only runs while the strip is in a known steady state; screen, music, and app effects are left alone. Notification text is not saved.",
                  style="Muted.Card.TLabel", wraplength=700).pack(anchor="w")

    def _slider(self, parent, label, var, value_var, low, high, column):
        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 20, 12))
        parent.columnconfigure(column, weight=1)
        header = ttk.Frame(frame, style="Card.TFrame")
        header.pack(fill="x")
        ttk.Label(header, text=label, style="Muted.Card.TLabel").pack(side="left")
        ttk.Label(header, textvariable=value_var, style="Card.TLabel").pack(side="right")
        scale = ttk.Scale(frame, from_=low, to=high, variable=var, command=lambda value: value_var.set(f"{float(value):.2f}"))
        scale.pack(fill="x", pady=(5, 0))
        self.manual_widgets.append(scale)
        return scale

    def _effect_group(self, parent, title, entries):
        group = ttk.Frame(parent, style="Card.TFrame")
        group.pack(fill="x", pady=(4, 5))
        ttk.Label(group, text=title, style="Muted.Card.TLabel").pack(anchor="w", pady=(0, 6))
        row = ttk.Frame(group, style="Card.TFrame")
        row.pack(fill="x")
        for index, (label, effect_type, effect_index) in enumerate(entries):
            button = RoundedButton(row, text=label,
                                command=lambda t=effect_type, i=effect_index, n=label: self.apply_device_effect(t, i, n))
            column_count = 4
            button.grid(row=index // column_count, column=index % column_count, sticky="ew",
                        padx=(0, 6), pady=3)
            row.columnconfigure(index % column_count, weight=1)
            self.effect_buttons.append(button)
            self.manual_widgets.append(button)

    def _software_effect_group(self, parent):
        group = ttk.Frame(parent, style="Card.TFrame")
        group.pack(fill="x", pady=(5, 0))
        ttk.Label(group, text="App generated · Color Flow, Aurora, Breathe, Comet, Ember", style="Muted.Card.TLabel").pack(anchor="w", pady=(0, 6))
        row = ttk.Frame(group, style="Card.TFrame")
        row.pack(fill="x")
        for index, (label, name) in enumerate((("Color Flow", "color_flow"), ("Aurora", "aurora"),
                                                ("Breathe", "breathe"), ("Comet", "comet"), ("Ember", "ember"))):
            button = RoundedButton(row, text=label, command=lambda n=name: self.start_animation(n))
            button.grid(row=index // 3, column=index % 3, sticky="ew", padx=(0, 6), pady=3)
            row.columnconfigure(index % 3, weight=1)
            self.effect_buttons.append(button)
            self.manual_widgets.append(button)

    def _base_cmd(self):
        if not CONTROLLER.exists():
            raise RuntimeError(f"Controller script not found:\n{CONTROLLER}")
        return [sys.executable, str(CONTROLLER)]

    def _set_manual_controls(self, enabled):
        state = "normal" if enabled else "disabled"
        for widget in self.manual_widgets:
            widget.configure(state=state)

    def _external_sync_pid(self):
        # Also find a live mode started by an older GUI instance, even when its
        # pid file is stale or it is an app-generated animation.
        try:
            for entry in Path("/proc").iterdir():
                if not entry.name.isdigit():
                    continue
                try:
                    cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="ignore")
                    if "mitsuko_g1.py" in cmdline and any(f" {mode} " in f" {cmdline} " for mode in ("screen", "animate", "music")):
                        return int(entry.name)
                except OSError:
                    continue
        except OSError:
            pass
        return None

    def _env(self):
        env = os.environ.copy()
        env["WAYLAND_SCREENSHOT_BIN"] = self._capture_helper()
        return env

    def _run_async(self, args, callback=None):
        def worker():
            try:
                result = subprocess.run(args, cwd=ROOT, env=self._env(), capture_output=True, text=True, timeout=12)
                self.messages.put(("command", json.dumps({"code": result.returncode, "out": result.stdout, "err": result.stderr, "callback": callback or ""})))
            except Exception as exc:
                self.messages.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def read_info(self):
        self.status.set("Checking for the G1…")
        self._run_async(self._base_cmd() + ["info"], "info")

    def _poll(self):
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "notification":
                    self._notification_flash()
                elif kind == "notification_monitor_error":
                    self.notification_monitor = None
                    self.notification_flash_enabled.set(False)
                    self._save_notification_preference()
                    self.details.set(payload)
                elif kind == "notification_flash_complete":
                    self.notification_flash_running = False
                    if self.proc is None and self._external_sync_pid() is None and self.pending_device_effect is None:
                        self._set_manual_controls(True)
                    if payload:
                        self.details.set(f"Notification flash error · {payload}")
                elif kind == "error":
                    self.status.set("Could not check the device")
                    self.status_dot.itemconfigure(1, fill=RED)
                    self.details.set(payload)
                elif kind == "command":
                    result = json.loads(payload)
                    if result["callback"] == "info":
                        if result["code"] == 0:
                            try:
                                info = ast.literal_eval(result["out"].strip())
                                self.status.set("G1 connected")
                                self.status_dot.itemconfigure(1, fill=GREEN)
                                self.details.set(f"Firmware {info.get('firmware', 'unknown')}  ·  {self.leds.get()} configured LEDs")
                            except Exception:
                                self.status.set("G1 responded")
                                self.status_dot.itemconfigure(1, fill=GREEN)
                                self.details.set(result["out"].strip() or "USB device is available")
                        else:
                            self.status.set("G1 not detected")
                            self.status_dot.itemconfigure(1, fill=RED)
                            self.details.set((result["err"] or result["out"]).strip().splitlines()[-1:][0] if (result["err"] or result["out"]).strip() else "Check USB cable and udev permissions")
                    elif result["callback"] == "brightness":
                        output = (result["out"] or result["err"]).strip()
                        self.details.set(output or "Brightness command completed")
                    elif result["callback"] == "device_effect":
                        self._set_manual_controls(True)
                        output = (result["out"] or result["err"]).strip()
                        self.details.set(output or "Device effect command completed")
                        if result["code"] != 0:
                            messagebox.showerror("Effect failed", output or "The device rejected the effect command.", parent=self)
                        elif self.pending_device_effect is not None:
                            self._remember_notification_state(("effect", *self.pending_device_effect))
                        self.pending_device_effect = None
                    elif result["callback"] == "solid_color" and result["code"] == 0:
                        self._remember_notification_state(("color", *(int(self.color[index:index + 2], 16) for index in (1, 3, 5))))
                    elif result["callback"] == "lights_off" and result["code"] == 0:
                        self._remember_notification_state(("off",))
                elif kind == "diagnostics":
                    self.diagnostic.set(payload)
                elif kind == "log":
                    if payload.startswith("Audio levels"):
                        self.details.set(payload[:180])
                    elif any(word in payload.lower() for word in ("stopped", "error", "could not", "cannot", "not found")):
                        self.last_process_error = payload[:180]
                        self.details.set(self.last_process_error)
        except queue.Empty:
            pass
        if self.proc is not None and self.proc.poll() is not None:
            mode = self.active_mode or "Live mode"
            self.proc = None
            self.active_mode = ""
            self._clear_notification_state()
            self.start_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")
            self.stop_btn.configure(text="Stop live mode")
            self._set_manual_controls(True)
            self.details.set(self.last_process_error or f"{mode} stopped")
            self.last_process_error = ""
            if self._closing_after_stop:
                self._stop_notification_monitor()
                self.destroy()
                return
        elif self.external_pid is not None and self._external_sync_pid() is None:
            self.external_pid = None
            self.active_mode = ""
            self.status.set("Live mode stopped")
            self.status_dot.itemconfigure(1, fill=MUTED)
            self._external_mode_stopped()
        self.after(120, self._poll)

    def _save(self, values):
        values["brightness"] = self.brightness.get()
        try:
            gui_settings = json.loads(GUI_CONFIG.read_text())
        except (OSError, ValueError):
            gui_settings = {}
        gui_settings.update(values)
        gui_settings["profiles"] = self.profiles
        GUI_CONFIG.write_text(json.dumps(gui_settings, indent=2) + "\n")
        try:
            config = json.loads(CONFIG.read_text())
        except (OSError, ValueError):
            config = {}
        config["leds"] = values["leds"]
        if "color" in values:
            config["color"] = values["color"]
        CONFIG.write_text(json.dumps(config, indent=2) + "\n")

    def _settings(self):
        try:
            leds = int(self.leds.get())
            counts = {side: int(var.get()) for side, var in self.edge.items()}
            fps = int(self.fps.get())
            monitor = int(self.monitor.get())
        except ValueError:
            raise ValueError("LED counts, FPS, and monitor index must be whole numbers.")
        if not 1 <= leds <= 254 or any(v < 0 for v in counts.values()):
            raise ValueError("Use 1–254 LEDs and non-negative edge counts.")
        if sum(counts.values()) != leds:
            raise ValueError(f"The edge counts add up to {sum(counts.values())}, but LEDs is {leds}.")
        if not 1 <= fps <= 60:
            raise ValueError("Target FPS must be between 1 and 60.")
        if monitor < 1:
            raise ValueError("Monitor index must be 1 or greater.")
        return {"leds": leds, "counts": counts, "fps": fps,
                "monitor": monitor,
                "smooth": round(self.smooth.get(), 2), "depth": round(self.depth.get(), 2),
                "audio_sensitivity": round(self.audio_sensitivity.get(), 1),
                "audio_mode": self.audio_mode.get(),
                "color": [int(self.color[index:index + 2], 16) for index in (1, 3, 5)]}

    def start_sync(self):
        if self.proc is not None:
            return
        try:
            old_pid = self._external_sync_pid()
            if old_pid is not None:
                if not messagebox.askyesno("Take over screen sync?",
                        "A command-line sync is already controlling the strip. Stop it and let this window take over?",
                        parent=self):
                    return
                os.kill(old_pid, signal.SIGINT)
                self.start_btn.configure(state="disabled")
                self.details.set("Switching control to this window…")
                self.after(250, self._wait_for_external_sync)
                return
            self._launch_sync()
        except Exception as exc:
            messagebox.showerror("Could not start sync", str(exc), parent=self)

    def _wait_for_external_sync(self, attempts=0):
        if self._external_sync_pid() is not None and attempts < 20:
            self.after(250, lambda: self._wait_for_external_sync(attempts + 1))
            return
        if self._external_sync_pid() is not None:
            self.start_btn.configure(state="normal")
            self.details.set("Existing sync did not stop; use Ctrl+C in its terminal")
            return
        self._launch_sync()

    def stop_external_mode(self, attempts=0):
        if attempts == 0:
            pid = self._external_sync_pid()
            if pid is None:
                self._external_mode_stopped()
                return
            try:
                os.kill(pid, signal.SIGINT)
            except ProcessLookupError:
                self._external_mode_stopped()
                return
            except OSError as exc:
                messagebox.showerror("Could not stop live mode", str(exc), parent=self)
                return
            self.start_btn.configure(state="disabled")
            self.details.set("Stopping current live mode…")
        if self._external_sync_pid() is not None and attempts < 20:
            if attempts == 4:
                pid = self._external_sync_pid()
                if pid is not None:
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            self.after(250, lambda: self.stop_external_mode(attempts + 1))
        elif self._external_sync_pid() is not None:
            self.start_btn.configure(state="normal")
            self.details.set("Live mode did not stop; stop its process from the terminal")
        else:
            self._external_mode_stopped()

    def _external_mode_stopped(self):
        self.external_pid = None
        self.active_mode = ""
        self._clear_notification_state()
        self.stop_btn.configure(text="Stop live mode", command=self.stop_sync, state="disabled")
        self._set_manual_controls(True)
        self.details.set("Live mode stopped · choose Music Reactive or another effect")
        if self._closing_after_stop:
            self._stop_notification_monitor()
            self.destroy()

    def _launch_sync(self):
        try:
            values = self._settings()
            self._save(values)
            counts = ",".join(f"{side}={values['counts'][side]}" for side in ("bottom", "left", "top", "right"))
            args = self._base_cmd() + ["--leds", str(values["leds"]), "screen", "--counts", counts,
                   "--fps", str(values["fps"]), "--smooth", str(values["smooth"]), "--depth", str(values["depth"]),
                   "--monitor", str(values["monitor"])]
            self.proc = subprocess.Popen(args, cwd=ROOT, env=self._env(), stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
            threading.Thread(target=self._read_process, args=(self.proc,), daemon=True).start()
            self.start_btn.configure(state="disabled")
            self.stop_btn.configure(state="normal")
            self.active_mode = "Screen sync"
            self._clear_notification_state()
            self.last_process_error = ""
            self._set_manual_controls(False)
            self.details.set(f"Screen sync running · target {values['fps']} FPS")
        except Exception as exc:
            messagebox.showerror("Could not start sync", str(exc), parent=self)

    def _read_process(self, proc):
        if proc.stdout:
            for line in proc.stdout:
                self.messages.put(("log", line.rstrip()))

    def _capture_helper(self):
        # Prefer the user's patched helper so portal grants survive reboots.
        return (str(CAPTURE_HELPER) if CAPTURE_HELPER.exists()
                else (shutil.which("wayland-screenshot") or "wayland-screenshot"))

    def refresh_diagnostics(self):
        self.diagnostic.set("Checking USB controller and monitor capture…")

        def worker():
            try:
                usb = subprocess.run(["lsusb", "-d", "1a86:fe07"], capture_output=True, text=True, timeout=4)
                helper = self._capture_helper()
                capture = subprocess.run([helper, "cast", "status", "--json"], capture_output=True,
                                         text=True, timeout=4)
                state = json.loads(capture.stdout) if capture.returncode == 0 else {}
                if state.get("running"):
                    size = f"{state.get('width', '?')}×{state.get('height', '?')}"
                    cap_text = f"capture running ({size})"
                else:
                    cap_text = "capture stopped; choose a monitor to grant or restore access"
                usb_text = "G1 connected" if usb.returncode == 0 and usb.stdout.strip() else "G1 not detected; check USB cable"
                self.messages.put(("diagnostics", f"{usb_text} · {cap_text}"))
            except Exception as exc:
                self.messages.put(("diagnostics", f"Diagnostics unavailable: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _profile_values(self):
        values = self._settings()
        values["brightness"] = self.brightness.get()
        values["color"] = [int(self.color[index:index + 2], 16) for index in (1, 3, 5)]
        return values

    def _save_profile(self):
        try:
            values = self._profile_values()
        except ValueError as exc:
            messagebox.showerror("Cannot save profile", str(exc), parent=self)
            return
        name = simpledialog.askstring("Save lighting profile", "Profile name:", parent=self)
        if not name:
            return
        name = name.strip()
        if not name:
            return
        self.profiles[name] = values
        self.profile_name.set(name)
        self.profile_combo.configure(values=sorted(self.profiles))
        self._save(values)
        self.diagnostic.set(f"Saved profile: {name}")

    def _load_profile(self, _event=None):
        values = self.profiles.get(self.profile_name.get())
        if not isinstance(values, dict):
            return
        try:
            self.leds.set(str(values.get("leds", self.leds.get())))
            for side, variable in self.edge.items():
                variable.set(str(values.get("counts", {}).get(side, variable.get())))
            self.fps.set(str(values.get("fps", self.fps.get())))
            self.smooth.set(float(values.get("smooth", self.smooth.get())))
            self.depth.set(float(values.get("depth", self.depth.get())))
            self.monitor.set(str(values.get("monitor", self.monitor.get())))
            self.brightness.set(int(values.get("brightness", self.brightness.get())))
            self.audio_sensitivity.set(float(values.get("audio_sensitivity", self.audio_sensitivity.get())))
            self.audio_mode.set(values.get("audio_mode", self.audio_mode.get()))
            color = values.get("color")
            if isinstance(color, list) and len(color) == 3:
                self._set_color_preview("#" + "".join(f"{max(0, min(255, int(channel))):02x}" for channel in color))
            self._refresh_music_modes()
            self._update_music_color_swatch()
            self._save(self._settings())
            self.diagnostic.set(f"Loaded profile: {self.profile_name.get()}")
        except (TypeError, ValueError) as exc:
            messagebox.showerror("Could not load profile", str(exc), parent=self)

    def choose_monitor(self):
        if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"):
            self.diagnostic.set("On X11, set Monitor # above (1 is usually the primary screen).")
            return
        active = self.proc is not None or self._external_sync_pid() is not None
        if active and not messagebox.askyesno("Change captured monitor?",
                "Screen sync will pause while you choose a monitor. Continue?", parent=self):
            return
        if self.proc is not None:
            self.stop_sync()
        elif self._external_sync_pid() is not None:
            self.stop_external_mode()
        self._wait_sync_before_monitor()

    def _wait_sync_before_monitor(self, attempts=0):
        if self.proc is not None or self._external_sync_pid() is not None:
            if attempts >= 40:
                self.diagnostic.set("Could not stop the active sync. Stop it before choosing a monitor.")
                return
            self.after(250, lambda: self._wait_sync_before_monitor(attempts + 1))
            return
        self._begin_monitor_enrollment()

    def _begin_monitor_enrollment(self):
        helper = self._capture_helper()
        try:
            subprocess.run([helper, "cast", "stop"], capture_output=True, text=True, timeout=8)
            self.portal_proc = subprocess.Popen([helper, "cast", "enroll", "--monitor"],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, start_new_session=True)
            self.diagnostic.set("Choose a monitor in the system sharing dialog…")
            self.after(700, self._watch_monitor_enrollment)
        except Exception as exc:
            self.diagnostic.set(f"Could not open monitor chooser: {exc}")

    def _watch_monitor_enrollment(self, attempts=0):
        helper = self._capture_helper()
        try:
            result = subprocess.run([helper, "cast", "status", "--json"], capture_output=True,
                                    text=True, timeout=4)
            state = json.loads(result.stdout) if result.returncode == 0 else {}
            if state.get("running"):
                self.diagnostic.set(f"Monitor selected · capture ready ({state.get('width', '?')}×{state.get('height', '?')}); start screen sync.")
                return
        except Exception:
            pass
        if self.portal_proc is not None and self.portal_proc.poll() is not None:
            err = self.portal_proc.stderr.read().strip() if self.portal_proc.stderr else ""
            self.portal_proc = None
            self.diagnostic.set(err or "Monitor selection ended before capture started.")
            return
        if attempts >= 75:
            self.diagnostic.set("Monitor selection timed out. Try Choose monitor again.")
            return
        self.after(600, lambda: self._watch_monitor_enrollment(attempts + 1))

    def stop_sync(self):
        if self.proc is None:
            return
        proc = self.proc
        threading.Thread(target=self._stop_process_safely, args=(proc,), daemon=True).start()
        self.details.set("Stopping sync…")

    @staticmethod
    def _stop_process_safely(proc):
        if proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGINT)
        except OSError:
            try:
                proc.send_signal(signal.SIGINT)
            except OSError:
                return
        try:
            proc.wait(timeout=2.0)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            try:
                proc.terminate()
            except OSError:
                pass
        try:
            proc.wait(timeout=1.0)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            try:
                proc.kill()
            except OSError:
                pass

    def apply_device_effect(self, effect_type, effect_index, label):
        if self.proc is not None:
            return
        self.pending_device_effect = (effect_type, effect_index)
        self._set_manual_controls(False)
        self.details.set(f"Applying {label}…")
        self._run_async(self._base_cmd() + ["effect", str(effect_type), str(effect_index)], "device_effect")

    def start_animation(self, name):
        if self.proc is not None:
            return
        try:
            leds = int(self.leds.get())
            fps = int(self.fps.get())
            if not 1 <= leds <= 254 or not 1 <= fps <= 60:
                raise ValueError("LED count must be 1–254 and FPS 1–60.")
            color = self.color.lstrip("#")
            args = self._base_cmd() + ["--proto", "sync", "--leds", str(leds), "animate", name,
                                      "--fps", str(fps), "--color", color]
            self.proc = subprocess.Popen(args, cwd=ROOT, env=self._env(), stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
            threading.Thread(target=self._read_process, args=(self.proc,), daemon=True).start()
            self.active_mode = name.replace("_", " ").title()
            self._clear_notification_state()
            self.last_process_error = ""
            self.start_btn.configure(state="disabled")
            self.stop_btn.configure(state="normal")
            self._set_manual_controls(False)
            self.details.set(f"{self.active_mode} running · target {fps} FPS · Stop live mode to return to controls")
        except Exception as exc:
            self.proc = None
            messagebox.showerror("Could not start effect", str(exc), parent=self)

    def start_music(self):
        if self.proc is not None:
            return
        try:
            leds = int(self.leds.get())
            fps = int(self.fps.get())
            sensitivity = float(self.audio_sensitivity.get())
            counts = {side: int(var.get()) for side, var in self.edge.items()}
            if not 1 <= leds <= 254 or not 1 <= fps <= 60:
                raise ValueError("LED count must be 1–254 and FPS 1–60.")
            if any(value < 0 for value in counts.values()) or sum(counts.values()) != leds:
                raise ValueError(f"Perimeter counts must add up to {leds} LEDs.")
            if not 0.2 <= sensitivity <= 5.0:
                raise ValueError("Audio sensitivity must be between 0.2 and 5.0.")
            settings = json.loads(GUI_CONFIG.read_text()) if GUI_CONFIG.exists() else {}
            settings["audio_sensitivity"] = sensitivity
            settings["audio_mode"] = self.audio_mode.get()
            settings["counts"] = counts
            GUI_CONFIG.write_text(json.dumps(settings, indent=2) + "\n")
            color = self.color.lstrip("#")
            count_arg = ",".join(f"{side}={counts[side]}" for side in ("bottom", "left", "top", "right"))
            args = self._base_cmd() + ["--leds", str(leds), "music", "--fps", str(fps),
                                      "--sensitivity", f"{sensitivity:.2f}",
                                      "--mode", self.audio_mode.get(), "--color", color,
                                      "--counts", count_arg]
            self.proc = subprocess.Popen(args, cwd=ROOT, env=self._env(), stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
            threading.Thread(target=self._read_process, args=(self.proc,), daemon=True).start()
            self.active_mode = f"Music · {self.audio_mode.get().title()}"
            self._clear_notification_state()
            self.last_process_error = ""
            self.start_btn.configure(state="disabled")
            self.stop_btn.configure(state="normal")
            self._set_manual_controls(False)
            self.details.set(f"Music reactive running · PipeWire system audio · target {fps} FPS")
        except Exception as exc:
            self.proc = None
            messagebox.showerror("Could not start music sync", str(exc), parent=self)

    def choose_color(self):
        selected = colorchooser.askcolor(self.color, parent=self, title="Choose LED color")[1]
        if selected:
            self._set_color_preview(selected)

    def set_color(self):
        r, g, b = (int(self.color[i:i + 2], 16) for i in (1, 3, 5))
        self._run_async(self._base_cmd() + ["color", str(r), str(g), str(b)], "solid_color")

    def turn_off(self):
        if self.proc is not None:
            self.stop_sync()
            return
        self._run_async(self._base_cmd() + ["off"], "lights_off")

    def apply_brightness(self):
        value = self.brightness.get()
        try:
            settings = json.loads(GUI_CONFIG.read_text())
        except OSError:
            settings = {}
        except ValueError:
            settings = {}
        settings["brightness"] = value
        GUI_CONFIG.write_text(json.dumps(settings, indent=2) + "\n")
        self._run_async(self._base_cmd() + ["brightness", str(value)], "brightness")

    def _init_tray_indicator(self):
        self._gtk = None
        self._indicator = None
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import Gtk, AyatanaAppIndicator3

            indicator = AyatanaAppIndicator3.Indicator.new(
                "mitsuko-g1", "mitsuko-g1",
                AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS)
            indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
            indicator.set_icon_theme_path(str(ROOT))
            indicator.set_icon_full("mitsuko-g1", "Mitsuko G1")
            menu = Gtk.Menu()
            show_item = Gtk.MenuItem(label="Open Mitsuko G1")
            show_item.connect("activate", lambda *_args: self._restore_from_tray())
            menu.append(show_item)
            menu.append(Gtk.SeparatorMenuItem())
            quit_item = Gtk.MenuItem(label="Quit Mitsuko G1")
            quit_item.connect("activate", lambda *_args: self._on_close())
            menu.append(quit_item)
            menu.show_all()
            indicator.set_menu(menu)
            self._gtk = Gtk
            self._indicator = indicator
            self.after(50, self._pump_tray_events)
        except (ImportError, ValueError, AttributeError, RuntimeError) as exc:
            self._tray_error = str(exc)

    def _pump_tray_events(self):
        if self._gtk is None:
            return
        while self._gtk.events_pending():
            self._gtk.main_iteration_do(False)
        self.after(50, self._pump_tray_events)

    def _close_to_tray(self):
        # Withdraw from the window list; the AppIndicator remains in GNOME's top panel.
        if self._indicator is not None:
            self.withdraw()
        else:
            # Never silently send the app to the bottom dock: the requested
            # behavior is to remain available from the top-panel indicator.
            messagebox.showerror(
                "Top-panel indicator unavailable",
                "Mitsuko G1 could not start its top-panel indicator, so the window will stay open. "
                "Install the latest Mitsuko G1 package to add the required indicator support.\n\n"
                f"Details: {getattr(self, '_tray_error', 'unknown error')}",
                parent=self,
            )

    def _restore_from_tray(self):
        self.deiconify()
        self.state("normal")
        self.lift()
        self.focus_force()

    def _on_close(self):
        external_pid = self._external_sync_pid()
        if self.proc is not None or external_pid is not None:
            if not messagebox.askyesno("Stop live mode?", f"{self.active_mode or 'Live mode'} is running. Stop it and close the app?", parent=self):
                return
            self._closing_after_stop = True
            if self.proc is not None:
                self.stop_sync()
            else:
                self.external_pid = external_pid
                self.stop_external_mode()
        else:
            self._stop_notification_monitor()
            self.destroy()


if __name__ == "__main__":
    App().mainloop()
