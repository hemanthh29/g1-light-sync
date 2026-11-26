#!/usr/bin/env python3
"""
Linux controller for the Mitsuko Phantom G1 monitor backlight (USB HID 1a86:fe07).

The protocol was read from the vendor's own desktop app (Mitsuko Smart Connect,
an Electron app by Robobloq, internal name "QuikLight"). It is NOT officially
documented. This script has been checked against the app's own packet builder
(byte-for-byte), but it has NOT been tested on real hardware yet.

Packet format (all values are bytes)
------------------------------------
Normal frame : 'R' 'B' | total_len | id | action | payload... | checksum
Screen frame : 'S' 'C' | total_len (2 bytes, big endian) | id | 0x80 | groups... | checksum
checksum     : sum of every byte in the frame (checksum byte counted as 0) mod 256
id           : counter that cycles 2..254
HID transport: frames are cut into 64-byte pieces, each written with a leading
               0x00 report ID.

Actions
-------
0x82 read info          0x85 effect(effect_id, param)   0x86 LED groups
0x87 brightness(v)      0x89 auto-off(a, b)             0x8A effect speed(v)
0x8B sound sensitivity  0x80 screen-sync frame (fw > 1.0.2)

LED group = 5 bytes: [first_led, R, G, B, last_led]   (1-based, inclusive;
254 means "to the end of the strip")

Usage examples
--------------
  python3 mitsuko_g1.py info
  python3 mitsuko_g1.py color 255 0 80
  python3 mitsuko_g1.py brightness 60
  python3 mitsuko_g1.py chase            # find out how your LEDs are laid out
  python3 mitsuko_g1.py screen --counts left=15,top=15,right=15,bottom=15
"""
import argparse
import colorsys
import json
import math
import os
import queue
import shutil
import subprocess
import sys
import threading
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config"))
CONFIG_DIR = os.path.join(CONFIG_HOME, "mitsuko-g1")
CONFIG = os.path.join(CONFIG_DIR, "mitsuko_g1.json")
STATE_FILE = os.path.join(CONFIG_DIR, "device_state.json")
LEGACY_CONFIG = os.path.join(APP_DIR, "mitsuko_g1.json")


def load_config():
    for path in (CONFIG, LEGACY_CONFIG):
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    return {}


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG, "w") as f:
        json.dump(cfg, f, indent=2)
    uid = os.environ.get("SUDO_UID")  # keep the file owned by the real user when run with sudo
    if uid:
        try:
            os.chown(CONFIG, int(uid), int(os.environ.get("SUDO_GID", uid)))
        except OSError:
            pass


def save_device_state(mode=None, **fields):
    """Persist the last lighting state so the desktop and phone UIs agree."""
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    if mode is not None and state.get("mode") != mode:
        brightness = state.get("brightness")
        state = {"mode": mode}
        if isinstance(brightness, int):
            state["brightness"] = brightness
    elif mode is not None:
        state["mode"] = mode
    state.update(fields)
    state["updatedAt"] = int(time.time())
    temp = f"{STATE_FILE}.{os.getpid()}.tmp"
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(temp, "w") as f:
            json.dump(state, f)
        os.chmod(temp, 0o600)
        os.replace(temp, STATE_FILE)
    except OSError:
        try:
            os.unlink(temp)
        except OSError:
            pass

VID, PID = 0x1A86, 0xFE07

ACT = {
    "sync_screen": 0x80, "read_info": 0x82, "effect": 0x85, "section": 0x86,
    "brightness": 0x87, "auto_off": 0x89, "speed": 0x8A, "sound": 0x8B,
    "turn_off": 0x97,
}


# ----------------------------------------------------------------- packets --
class Framer:
    """Builds protocol frames; mirrors the vendor app's packet builder."""

    def __init__(self):
        self._id = 1

    def next_id(self):
        self._id += 1
        if self._id >= 255:
            self._id = 1
        return self._id

    @staticmethod
    def _sum(buf):
        return sum(buf) & 0xFF

    def frame(self, action, payload=b"", pid=None):
        pid = self.next_id() if pid is None else pid
        buf = bytearray(b"RB" + bytes([6 + len(payload), pid, action]) + bytes(payload) + b"\x00")
        buf[-1] = self._sum(buf)
        return bytes(buf)

    def screen_frame(self, groups, pid=None):
        pid = self.next_id() if pid is None else pid
        total = 7 + len(groups)
        buf = bytearray(b"SC" + total.to_bytes(2, "big") + bytes([pid, ACT["sync_screen"]])
                        + bytes(groups) + b"\x00")
        buf[-1] = self._sum(buf)
        return bytes(buf)


def colors_to_groups(colors, to_end=False):
    """[(r,g,b), ...] one per LED  ->  flat list of 5-byte groups (adjacent equal colors merged).
    to_end=True makes the last group end at 254 ("to the end of the strip"), like the
    vendor app's solid-color frames do."""
    out, start = [], 0
    for i in range(1, len(colors) + 1):
        if i == len(colors) or colors[i] != colors[start]:
            r, g, b = colors[start]
            out += [start + 1, r, g, b, i]
            start = i
    if to_end and out:
        out[-1] = 254
    return out


def parse_edge_counts(spec):
    """Parse the perimeter in controller order: bottom, left, top, right."""
    counts = {"bottom": 30, "left": 17, "top": 30, "right": 17}
    try:
        for item in spec.split(","):
            key, value = item.split("=", 1)
            if key.strip() not in counts:
                raise ValueError
            counts[key.strip()] = int(value.strip())
        if any(value < 0 for value in counts.values()):
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError("--counts must look like bottom=30,left=17,top=30,right=17")
    return counts


# ------------------------------------------------------------------ device --
class G1:
    def __init__(self, path=None, order="RGB", verbose=False, proto="section", chunk=11):
        try:
            import hid
        except ImportError:
            sys.exit("Missing HID binding. Install the package dependencies with `sudo apt install python3-hid` or reinstall the G1 Light Sync package.")
        self.hid, self.order, self.verbose = hid, order.upper(), verbose
        self.no_end = False         # True = do not force the last group to end at 254
        self.proto = proto          # "section" (0x86, known to work) or "sync" (0x80 'SC' frames)
        self.chunk = max(1, min(chunk, 49))  # LED groups per 0x86 frame
        self.framer = Framer()
        self.dev = hid.device()
        if path is None:
            cands = [d for d in hid.enumerate(VID, PID) if d.get("interface_number", 0) in (0, -1)]
            if not cands:
                sys.exit("Phantom G1 (1a86:fe07) not found. Check `lsusb` and the udev rule.")
            path = cands[0]["path"]
        elif isinstance(path, str):
            path = path.encode()
        self.device_path = path
        try:
            self.dev.open_path(path)
        except OSError as e:
            sys.exit(f"Cannot open the device ({e}). Install the udev rule and replug the strip.")
        self.dev.set_nonblocking(1)
        self.fw = (0, 0, 0)
        self.lamps = load_config().get("leds", 60)  # LED count used for chase / screen sync
        self.lamps_reported = 0    # raw value from the strip (254 = "not configured")

    def reconnect(self):
        """Reopen the controller after a transient USB disconnect."""
        try:
            self.dev.close()
        except (OSError, AttributeError):
            pass
        for item in self.hid.enumerate(VID, PID):
            if item.get("interface_number", 0) not in (0, -1):
                continue
            device = self.hid.device()
            try:
                device.open_path(item["path"])
                device.set_nonblocking(1)
            except OSError:
                try:
                    device.close()
                except OSError:
                    pass
                continue
            self.dev = device
            self.device_path = item["path"]
            return True
        return False

    # -- low level
    def write(self, data):
        if self.verbose:
            print("TX", data.hex(" "))
        for i in range(0, len(data), 64):
            self.dev.write(b"\x00" + data[i:i + 64])

    def read_reply(self, pid, timeout=0.3):
        end = time.time() + timeout
        while time.time() < end:
            r = bytes(self.dev.read(64))
            if r:
                if self.verbose:
                    print("RX", r.hex(" "))
                if r[:2] == b"RB" and len(r) > 3 and r[3] == pid:
                    return r
            else:
                time.sleep(0.005)
        return None

    def drain(self):
        while self.dev.read(64):
            pass

    def _rgb(self, r, g, b):
        ch = {"R": r, "G": g, "B": b}
        return tuple(ch[c] for c in self.order)

    # -- commands
    def info(self):
        pid = self.framer.next_id()
        self.write(self.framer.frame(ACT["read_info"], pid=pid))
        r = self.read_reply(pid)
        if not r or len(r) < 24:
            return None
        self.fw = (r[21], r[22], r[23])
        self.lamps_reported = r[11]
        if 0 < r[11] < 254:        # 254 means "to the end", i.e. count not set; keep default 60
            self.lamps = r[11]
        return {"raw": r.hex(" "), "lamps": r[11], "display_size": r[8],
                "firmware": ".".join(map(str, self.fw)), "uuid": r[12:20].hex()}

    def brightness(self, value):
        # Smart Connect maps its alpha slider to 5..255; zero is not a valid
        # dimming level on the manufacturer UI (the strip has a separate off command).
        value = max(5, value)
        pid = self.framer.next_id()
        self.write(self.framer.frame(ACT["brightness"], bytes([value]), pid=pid))
        return self.read_reply(pid)

    def effect(self, effect_id, param=0):
        pid = self.framer.next_id()
        self.write(self.framer.frame(ACT["effect"], bytes([effect_id, param]), pid=pid))
        return self.read_reply(pid)

    def turn_off(self):
        pid = self.framer.next_id()
        self.write(self.framer.frame(ACT["turn_off"], pid=pid))
        return self.read_reply(pid)

    def speed(self, value):
        self.write(self.framer.frame(ACT["speed"], bytes([value])))

    def sound_sensitivity(self, value):
        self.write(self.framer.frame(ACT["sound"], bytes([value])))

    def groups(self, flat):
        """Send LED groups using the 0x86 command (works on every firmware)."""
        step = 5 * self.chunk  # 11 groups (55 bytes) per frame is what the vendor app uses
        for i in range(0, len(flat), step):
            self.write(self.framer.frame(ACT["section"], bytes(flat[i:i + step])))
            if len(flat) > step:
                time.sleep(0.02)

    def solid(self, r, g, b):
        c = self._rgb(r, g, b)
        n = self.lamps_reported
        flat = [1, *c, 254] if not (0 < n < 254) else [1, *c, n, n + 1, 0, 0, 0, 254]
        self.groups(flat)

    def frame_colors(self, colors):
        """Send one color per LED (list of (r,g,b)); this is the screen-sync path."""
        colors = [self._rgb(*c) for c in colors]
        flat = colors_to_groups(colors, to_end=(self.proto == "section" and not self.no_end))
        if self.proto == "sync" and self.fw > (1, 0, 2):
            self.write(self.framer.screen_frame(flat))
        else:
            self.groups(flat)


def send_frame_resilient(g, colors):
    """Retry streamed frames after a USB disconnect, waiting for the G1 to return."""
    while True:
        try:
            g.frame_colors(colors)
            g.drain()
            return
        except OSError as exc:
            print(f"G1 USB connection lost ({exc}); waiting to reconnect…", file=sys.stderr, flush=True)
            while not g.reconnect():
                time.sleep(1.0)
            print("G1 reconnected; resuming live mode.", flush=True)


def turn_off_if_connected(g):
    try:
        g.turn_off()
        g.solid(0, 0, 0)
        save_device_state("off", power=False)
    except OSError:
        print("G1 is disconnected; skipped the final off frame.", file=sys.stderr, flush=True)


# ------------------------------------------------------------ LED counting --
def single_led(g, k):
    """Light only LED number k (1-based) white; everything else off."""
    flat = []
    if k > 1:
        flat += [1, 0, 0, 0, k - 1]
    flat += [k, 255, 255, 255, k, k + 1, 0, 0, 0, 254]
    g.groups(flat)


def find_count(g, ask=input, lo=1, hi=253):
    """Binary-search the number of LEDs. LED k is visible iff k <= real count."""
    print("I will light ONE white LED at a time. Look along the whole strip.")
    print("Answer y if you can see a lit white LED, n if the whole strip is dark,")
    print("r to show it again, q to quit.\n")
    while lo < hi:
        mid = (lo + hi + 1) // 2
        while True:
            single_led(g, mid)
            ans = ask(f"Testing LED {mid}: is a white LED lit? [y/n/r/q] ").strip().lower()
            if ans in ("y", "n"):
                break
            if ans == "q":
                g.solid(0, 0, 0)
                return None
        if ans == "y":
            lo = mid
        else:
            hi = mid - 1
    return lo


def auto_counts(n, width, height):
    """Split n LEDs over the four edges in proportion to the screen's aspect ratio."""
    per = 2 * (width + height)
    horiz = round(n * width / per)
    vert = (n - 2 * horiz) // 2
    extra = n - 2 * horiz - 2 * vert
    return {"top": horiz + extra, "bottom": horiz, "left": vert, "right": vert}


# ------------------------------------------------------------- screen sync --
def parse_counts(s):
    d = {"left": 0, "top": 0, "right": 0, "bottom": 0}
    for part in s.split(","):
        k, v = part.split("=")
        if k not in d:
            raise ValueError(f"unknown edge {k!r}")
        d[k] = int(v)
    return d


def parse_path(s):
    spec = []
    for part in s.split(","):
        edge, direction = part.split(":")
        ok = {"top": ("left", "right"), "bottom": ("left", "right"),
              "left": ("up", "down"), "right": ("up", "down")}
        if edge not in ok or direction not in ok[edge]:
            raise ValueError(f"bad path element {part!r}")
        spec.append((edge, direction))
    return spec


class WaylandCapture:
    """Read whole-monitor BGRA frames from the authorized portal helper."""

    def __init__(self):
        if os.geteuid() == 0:
            sys.exit("Run screen sync as your desktop user, not with sudo, so it can use Wayland capture.")
        exe = (os.environ.get("WAYLAND_SCREENSHOT_BIN") or shutil.which("wayland-screenshot")
               or os.path.expanduser("~/.local/share/wayland-screenshot-venv/bin/wayland-screenshot"))
        if not os.path.isfile(exe) or not os.access(exe, os.X_OK):
            sys.exit("Wayland capture helper is missing. Install wayland-screenshot or set WAYLAND_SCREENSHOT_BIN.")
        self.state_dir = os.environ.get("WAYLAND_SCREENSHOT_STATE_DIR", "/dev/shm/wayland-screenshot")
        self.meta_path = os.path.join(self.state_dir, "frame.json")
        self.buffer_path = os.path.join(self.state_dir, "buffer.raw")
        self.exe = exe
        self.proc = None
        self.attached = False
        try:
            status = subprocess.run([exe, "cast", "status", "--json"], capture_output=True,
                                    text=True, timeout=3)
            self.attached = status.returncode == 0 and json.loads(status.stdout).get("running", False)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            self.attached = False
        if not self.attached:
            self.proc = subprocess.Popen([exe, "cast", "start"], stdout=subprocess.DEVNULL,
                                         stderr=subprocess.PIPE, text=True)
        deadline = time.time() + 20
        while time.time() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                _, err = self.proc.communicate()
                sys.exit("Wayland capture helper stopped: " + (err.strip() or "unknown error"))
            try:
                with open(self.meta_path) as f:
                    meta = json.load(f)
                if meta.get("width", 0) and meta.get("height", 0) and os.path.exists(self.buffer_path):
                    self.width, self.height = int(meta["width"]), int(meta["height"])
                    break
            except (OSError, ValueError, KeyError):
                pass
            time.sleep(0.1)
        else:
            self.close()
            sys.exit("Timed out waiting for Wayland monitor frames. Re-authorize with "
                     "`wayland-screenshot cast enroll --monitor`.")

    def grab(self, np):
        if self.proc is not None and self.proc.poll() is not None:
            _, err = self.proc.communicate()
            raise RuntimeError("Wayland capture helper stopped: " + (err.strip() or "unknown error"))
        with open(self.meta_path) as f:
            meta = json.load(f)
        width, height = int(meta["width"]), int(meta["height"])
        stride = int(meta.get("stride") or width * 4)
        raw = np.fromfile(self.buffer_path, dtype=np.uint8)
        if raw.size < stride * height:
            return None
        return raw[:stride * height].reshape(height, stride)[:, :width * 4].reshape(height, width, 4)

    def close(self):
        if getattr(self, "proc", None) is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        elif getattr(self, "attached", False):
            subprocess.run([self.exe, "cast", "stop"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5)


def screen_sync(g, counts, path, fps, smooth, depth, monitor):
    try:
        import numpy as np
    except ImportError:
        sys.exit("Missing dependency: install numpy in the g1env virtual environment.")
    wayland = os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))
    sct = None
    portal = None
    if wayland:
        portal = WaylandCapture()
        width, height = portal.width, portal.height
    else:
        try:
            import mss
        except ImportError:
            sys.exit("Missing dependency: install mss in the g1env virtual environment.")
        sct = mss.MSS()
        mon = sct.monitors[monitor]
        width, height = mon["width"], mon["height"]
    if counts is None:
        counts = auto_counts(g.lamps, width, height)
        print("Using estimated edge counts (override with --counts):", counts)
    prev, interval = None, 1.0 / fps
    print(f"Screen sync running at ~{fps} fps. Ctrl+C to stop.", flush=True)
    try:
        while True:
            t0 = time.time()
            if portal:
                frame = portal.grab(np)
                if frame is None:
                    time.sleep(0.01)
                    continue
                # Portal/helper frames are BGRA; the sampling math expects RGB.
                img = frame[::4, ::4, :3][:, :, ::-1].astype(np.float32)
            else:
                img = np.asarray(sct.grab(mon))[::4, ::4, :3][:, :, ::-1].astype(np.float32)
            h, w = img.shape[:2]
            dh, dw = max(1, int(h * depth)), max(1, int(w * depth))
            leds = []
            for edge, direction in path:
                n = counts[edge]
                if not n:
                    continue
                band = {"top": lambda: img[:dh].mean(axis=0), "bottom": lambda: img[-dh:].mean(axis=0),
                        "left": lambda: img[:, :dw].mean(axis=1), "right": lambda: img[:, -dw:].mean(axis=1)}[edge]()
                cols = [seg.mean(axis=0) for seg in np.array_split(band, n)]
                if direction in ("left", "up"):  # natural order is left->right / top->bottom
                    cols.reverse()
                leds += cols
            cur = np.array(leds)
            prev = cur if prev is None else prev * smooth + cur * (1 - smooth)
            colors = [tuple(int(v) for v in c) for c in prev]
            while True:
                try:
                    g.frame_colors(colors)
                    g.drain()
                    break
                except OSError as exc:
                    print(f"G1 USB connection lost ({exc}); waiting to reconnect…", file=sys.stderr, flush=True)
                    while not g.reconnect():
                        time.sleep(1.0)
                    print("G1 reconnected; resuming screen sync.", flush=True)
            time.sleep(max(0.0, interval - (time.time() - t0)))
    finally:
        if portal:
            portal.close()


def run_animation(g, name, fps, base_color):
    """Generate a lightweight color animation locally and stream it to the strip."""
    n = max(1, g.lamps)
    interval = 1.0 / max(1, fps)
    started = time.monotonic()
    r0, g0, b0 = base_color
    print(f"{name.replace('_', ' ').title()} running at up to {fps} FPS. Ctrl+C to stop.", flush=True)
    while True:
        frame_start = time.monotonic()
        t = frame_start - started
        colors = []
        for i in range(n):
            x = i / max(1, n)
            if name == "color_flow":
                rgb = colorsys.hsv_to_rgb((x * 1.35 + t * 0.16) % 1.0, 0.92, 1.0)
                color = tuple(round(c * 255) for c in rgb)
            elif name == "aurora":
                wave = math.sin(2 * math.pi * x * 1.5 - t * 0.75) + 0.45 * math.sin(2 * math.pi * x * 3.1 + t * 0.58)
                hue = (0.50 + 0.16 * wave) % 1.0
                value = 0.45 + 0.5 * (0.5 + 0.5 * math.sin(2 * math.pi * x * 2.2 - t * 0.9))
                rgb = colorsys.hsv_to_rgb(hue, 0.78, value)
                color = tuple(round(c * 255) for c in rgb)
            elif name == "breathe":
                level = 0.06 + 0.94 * (0.5 + 0.5 * math.sin(2 * math.pi * t / 4.2))
                color = (round(r0 * level), round(g0 * level), round(b0 * level))
            elif name == "comet":
                head = (t * n / 2.2) % n
                distance = (head - i) % n
                level = 0.012 + 0.988 * math.exp(-distance / max(2.0, n * 0.07))
                if distance > n * 0.55:
                    level = 0.012
                color = (round(r0 * level), round(g0 * level), round(b0 * level))
            else:  # ember
                flicker = 0.52 + 0.29 * math.sin(i * 1.77 + t * 4.1 + math.sin(i * 0.31 - t * 1.8))
                flicker += 0.17 * math.sin(i * 0.53 - t * 2.7)
                heat = max(0.12, min(1.0, flicker))
                color = (255, round(52 + 126 * heat), round(4 + 22 * heat * heat))
            colors.append(color)
        send_frame_resilient(g, colors)
        time.sleep(max(0.0, interval - (time.monotonic() - frame_start)))


def run_notification_effect(g, name, alert_color, base_color=None):
    """Play one short alert pattern and finish at its steady background color."""
    n = max(1, g.lamps)
    base = tuple(base_color) if base_color is not None else (0, 0, 0)
    alert = tuple(max(0, min(255, int(channel))) for channel in alert_color)

    def send(colors):
        send_frame_resilient(g, colors)

    def hold(color, duration):
        send([color] * n)
        time.sleep(duration)

    def blend(level):
        level = max(0.0, min(1.0, level))
        return tuple(round(old + (new - old) * level) for old, new in zip(base, alert))

    if name == "single":
        hold(alert, 0.24)
        hold(base, 0.10)
    elif name in {"double", "urgent"}:
        count = 2 if name == "double" else 3
        for _ in range(count):
            hold(alert, 0.12 if name == "urgent" else 0.17)
            hold(base, 0.10)
    elif name == "breathe":
        steps = 12
        for index in range(steps + 1):
            send([blend((index / steps) ** 2)] * n)
            time.sleep(0.045)
        for index in range(steps - 1, -1, -1):
            send([blend((index / steps) ** 2)] * n)
            time.sleep(0.045)
    elif name == "ping":
        width = max(4, min(10, round(n * 0.09)))
        frames = max(12, min(32, n // 3))
        for frame in range(frames):
            head = round(frame * n / frames)
            colors = [base] * n
            for tail in range(width):
                led = (head - tail) % n
                colors[led] = blend(1.0 - tail / width)
            send(colors)
            time.sleep(0.045)
        hold(base, 0.08)


def run_music(g, fps, sensitivity, mode, base_color, edge_counts, zone_colors=None):
    """Capture PipeWire playback and render a responsive, palette-based music mode."""
    import numpy as np

    zone_colors = zone_colors or {"bass": base_color, "voice": base_color, "treble": base_color}

    # Keep the proven 0x86 section transport for live audio frames. Combined
    # uses a handful of color regions, which fit in a single small report.
    g.proto = "section"
    if sum(edge_counts.values()) != g.lamps:
        raise RuntimeError(f"Music layout counts total {sum(edge_counts.values())}; expected {g.lamps} LEDs.")

    pw_cat = shutil.which("pw-cat")
    if not pw_cat:
        raise RuntimeError("pw-cat is missing. Install PipeWire tools (pipewire-bin) and try again.")
    rate = 44100
    samples_per_block = 1024
    window_size = 4096
    command = [pw_cat, "--record", "--raw", "--format", "s16", "--rate", str(rate),
               "--channels", "1", "--target", "auto", "--latency", "60ms",
               "--properties", '{"stream.capture.sink": true}', "-"]
    audio = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
    errors = []
    blocks = queue.Queue(maxsize=4)
    reader_errors = []
    stop_reader = threading.Event()

    def drain_errors():
        if audio.stderr:
            for line in iter(audio.stderr.readline, b""):
                errors.append(line.decode(errors="replace").strip())

    threading.Thread(target=drain_errors, daemon=True).start()

    def capture_audio():
        needed = samples_per_block * 2
        while not stop_reader.is_set():
            chunk = bytearray()
            while len(chunk) < needed and not stop_reader.is_set():
                part = audio.stdout.read(needed - len(chunk))
                if not part:
                    reader_errors.append("PipeWire audio stream ended.")
                    return
                chunk.extend(part)
            samples = np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0
            try:
                blocks.put_nowait(samples)
            except queue.Full:
                try:
                    blocks.get_nowait()  # discard stale audio so LEDs stay in sync with live playback
                except queue.Empty:
                    pass
                try:
                    blocks.put_nowait(samples)
                except queue.Full:
                    pass

    reader = threading.Thread(target=capture_audio, daemon=True)
    reader.start()

    try:
        labels = {"combined": "Full mix", "bass": "Bass", "treble": "Treble", "voice": "Voice emphasis (approx.)"}
        print(f"{labels[mode]} music mode running at up to {fps} FPS; capturing PipeWire output. Ctrl+C to stop.", flush=True)
        levels = np.zeros(4, dtype=np.float32)
        hits = np.zeros(3, dtype=np.float32)
        beat_level = 0.0
        last_beat = -1e9
        song_reference_db = None
        song_level = 0.5
        beat_reference = None
        audio_history = np.zeros(window_size, dtype=np.float32)
        previous_spectrum = np.zeros(window_size // 2 + 1, dtype=np.float32)
        zone_hsv = {name: colorsys.rgb_to_hsv(*(channel / 255 for channel in zone_colors[name]))
                    for name in ("bass", "voice", "treble")}
        last_report = time.monotonic()
        previous_frame_time = time.monotonic()
        if mode != "combined":
            frequencies = np.fft.rfftfreq(window_size, 1 / rate)
            bands = ((35, 150), (150, 500), (500, 3500), (3500, 16000))
            masks = [(frequencies >= low) & (frequencies < high) for low, high in bands]
            window = np.hanning(window_size).astype(np.float32)
        floor_db = (-24.0, -29.0, -28.0, -33.0)
        band_range_db = max(18.0, 36.0 - 3.0 * sensitivity)
        sensitivity_shift = (sensitivity - 1.5) * 1.1

        def onset_strength(current_band, previous_band):
            total = float(current_band.sum())
            if total < 1e-7:
                return 0.0
            flux = float(np.maximum(current_band - previous_band, 0).sum())
            relative_flux = flux / total
            return min(1.0, max(0.0, (relative_flux - 0.035) / 0.22))

        def rgb_for_zone(name, value):
            hue, saturation, base_value = zone_hsv[name]
            value = min(1.0, max(0.0, value * gate * base_value))
            rgb = colorsys.hsv_to_rgb(hue, saturation, value)
            return tuple(round(channel * 255) for channel in rgb)

        while True:
            frame_start = time.monotonic()
            dt = min(0.15, max(0.001, frame_start - previous_frame_time))
            previous_frame_time = frame_start
            try:
                samples = blocks.get(timeout=0.5)
            except queue.Empty:
                if audio.poll() is not None or reader_errors:
                    detail = "; ".join((errors + reader_errors)[-3:]) or "PipeWire ended the audio stream."
                    raise RuntimeError(f"Could not capture system audio: {detail}")
                continue
            audio_history[:-samples_per_block] = audio_history[samples_per_block:]
            audio_history[-samples_per_block:] = samples
            while True:
                try:
                    samples = blocks.get_nowait()
                except queue.Empty:
                    break
                audio_history[:-samples_per_block] = audio_history[samples_per_block:]
                audio_history[-samples_per_block:] = samples

            rms = float(np.sqrt(np.mean(audio_history * audio_history)))
            gate = min(1.0, max(0.0, (rms - 0.002) / 0.025))

            if mode == "combined":
                # Full-mix response: long-term relative loudness gives the
                # song's verse/build/chorus arc, while a broadband short-block
                # rise creates a separate beat pulse. This path intentionally
                # does not calculate FFT bands or classify instruments.
                rms_db = 20.0 * math.log10(max(rms, 1e-6))
                if rms > 0.002:
                    if song_reference_db is None:
                        song_reference_db = rms_db
                    else:
                        reference_alpha = 1.0 - math.exp(-dt / 24.0)
                        song_reference_db += (rms_db - song_reference_db) * reference_alpha
                    loudness_range_db = max(10.0, 22.0 - 2.4 * sensitivity)
                    song_target = min(1.0, max(0.0, 0.5 + (rms_db - song_reference_db) / loudness_range_db))
                else:
                    song_target = 0.0
                song_tau = 0.20 if song_target > song_level else 0.75
                song_level += (song_target - song_level) * (1.0 - math.exp(-dt / song_tau))

                block_rms = float(np.sqrt(np.mean(samples * samples)))
                if beat_reference is None:
                    beat_reference = block_rms
                beat_ratio = block_rms / max(beat_reference, 0.0015)
                beat_threshold = max(1.04, 1.18 - 0.025 * sensitivity)
                beat_range = max(0.48, 0.90 - 0.08 * sensitivity)
                beat_strength = min(1.0, max(0.0, (beat_ratio - beat_threshold) / beat_range)) * gate
                if beat_strength >= 0.20 and frame_start - last_beat >= 0.20:
                    beat_level = max(beat_level, beat_strength)
                    last_beat = frame_start
                else:
                    beat_level *= math.exp(-dt / 0.19)
                if rms > 0.002:
                    reference_alpha = 1.0 - math.exp(-dt / 0.85)
                    beat_reference += (block_rms - beat_reference) * reference_alpha
                else:
                    beat_reference *= math.exp(-dt / 0.85)

                # Keep the slow musical envelope visible between beats, then
                # let each hit lift the same palette zones together.
                shared_value = 0.08 + 0.64 * song_level + 0.95 * beat_level
                bottom, left, top, right = (edge_counts[key] for key in ("bottom", "left", "top", "right"))
                colors = ([rgb_for_zone("bass", shared_value)] * bottom
                          + [rgb_for_zone("voice", shared_value)] * left
                          + [rgb_for_zone("treble", shared_value)] * top
                          + [rgb_for_zone("voice", shared_value)] * right)
                if time.monotonic() - last_report >= 1.0:
                    print("Audio levels · song {:.2f} · beat {:.2f}".format(song_level, beat_level), flush=True)
                    last_report = time.monotonic()
                send_frame_resilient(g, colors)
                time.sleep(max(0.0, 1.0 / max(1, fps) - (time.monotonic() - frame_start)))
                continue

            spectrum = np.abs(np.fft.rfft(audio_history * window)) * (2.0 / max(1.0, float(window.sum())))
            band_energy = np.asarray([
                float(np.sqrt(np.sum(spectrum[mask] ** 2) * 0.5)) if np.any(mask) else 0.0
                for mask in masks
            ], dtype=np.float32)
            rms_db = 20.0 * math.log10(max(rms, 1e-6))

            # Track the song's own loud/quiet movement against one slow global
            # reference. Band balance remains a separate, same-frame signal.
            if rms > 0.002:
                if song_reference_db is None:
                    song_reference_db = rms_db
                else:
                    alpha = 1.0 - math.exp(-dt / 24.0)
                    song_reference_db += (rms_db - song_reference_db) * alpha
                loudness_range_db = max(12.0, 24.0 - 2.0 * sensitivity)
                song_level = min(1.0, max(0.0, 0.5 + (rms_db - song_reference_db) / loudness_range_db))
            else:
                song_level = 0.0
            song_gain = 0.18 + 0.82 * song_level

            # Band activity is its share of the current full mix, not a
            # per-band auto-gain. This keeps tone response separate from the
            # global in-song loudness envelope above.
            relative_db = np.asarray([
                20.0 * math.log10(max(float(energy), 1e-7) / max(rms, 1e-6))
                for energy in band_energy
            ], dtype=np.float32)
            band_targets = np.asarray([
                min(1.0, max(0.0, (float(value) - (floor_db[index] - sensitivity_shift)) / band_range_db))
                for index, value in enumerate(relative_db)
            ], dtype=np.float32)
            for index, target in enumerate(band_targets):
                alpha = 1.0 - math.exp(-dt / (0.075 if target > levels[index] else 0.22))
                levels[index] += (float(target) - float(levels[index])) * alpha

            onsets = [onset_strength(spectrum[mask], previous_spectrum[mask]) for mask in masks]
            previous_spectrum = spectrum.copy()
            hits[0] = max(float(hits[0]) * math.exp(-dt / 0.16), onsets[0] * gate)
            hits[1] = max(float(hits[1]) * math.exp(-dt / 0.24), max(onsets[1], 0.55 * onsets[2]) * gate)
            hits[2] = max(float(hits[2]) * math.exp(-dt / 0.10), onsets[3] * gate)

            beat_candidate = max(onsets[0], 0.72 * onsets[1]) * gate
            if beat_candidate > 0.48 and frame_start - last_beat >= 0.22:
                beat_level = 1.0
                last_beat = frame_start
            else:
                beat_level *= math.exp(-dt / 0.18)

            mid_level = min(1.0, 0.35 * float(levels[1]) + 0.65 * float(levels[2]))
            if time.monotonic() - last_report >= 1.0:
                print("Audio levels · song {:.2f} · bass {:.2f} · mids {:.2f} · treble {:.2f} · beat {:.2f}".format(
                    song_level, float(levels[0]), mid_level, float(levels[3]), beat_level), flush=True)
                last_report = time.monotonic()

            if mode == "bass":
                value = song_gain * (0.06 + 0.40 * float(levels[0])) + 0.68 * float(hits[0]) + 0.16 * beat_level
                colors = [rgb_for_zone("bass", value)] * g.lamps
            elif mode == "treble":
                value = song_gain * (0.05 + 0.36 * float(levels[3])) + 0.55 * float(hits[2]) + 0.12 * beat_level
                colors = [rgb_for_zone("treble", value)] * g.lamps
            elif mode == "voice":
                value = song_gain * (0.06 + 0.38 * mid_level) + 0.36 * float(hits[1]) + 0.10 * beat_level
                colors = [rgb_for_zone("voice", value)] * g.lamps
            else:
                # All frequency-analysis modes are handled above.
                colors = [rgb_for_zone("voice", 0.0)] * g.lamps
            send_frame_resilient(g, colors)
            time.sleep(max(0.0, 1.0 / max(1, fps) - (time.monotonic() - frame_start)))
    finally:
        stop_reader.set()
        if audio.poll() is None:
            audio.terminate()
            try:
                audio.wait(timeout=1)
            except subprocess.TimeoutExpired:
                audio.kill()
        reader.join(timeout=0.5)
        if audio.stdout:
            audio.stdout.close()
        if audio.stderr:
            audio.stderr.close()


# --------------------------------------------------------------------- CLI --
def main():
    ap = argparse.ArgumentParser(description="G1 Light Sync unofficial controller for Mitsuko Phantom G1")
    ap.add_argument("--path", help="hidraw path (default: auto-detect 1a86:fe07)")
    ap.add_argument("--order", default="RGB", help="channel order if colors look wrong, e.g. GRB")
    ap.add_argument("-v", "--verbose", action="store_true", help="print raw packets")
    ap.add_argument("--leds", type=int, default=0, help="LED count (default: saved value from `count`, else 60)")
    ap.add_argument("--proto", choices=["section", "sync"], default="section",
                    help="how per-LED frames are sent: section (0x86, default) or sync (0x80)")
    ap.add_argument("--chunk", type=int, default=11, help="LED groups per 0x86 frame (max 49)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info")
    c = sub.add_parser("color"); [c.add_argument(x, type=int) for x in ("r", "g", "b")]
    sub.add_parser("off")
    sub.add_parser("count", help="find how many LEDs the strip has (interactive) and save it")
    gp = sub.add_parser("groups", help="send raw LED groups: first R G B last [first R G B last ...]")
    gp.add_argument("values", type=int, nargs="+")
    sub.add_parser("rgbtest", help="show pure red, green, blue, white to check the channel order")
    b = sub.add_parser("brightness"); b.add_argument("value", type=int)
    e = sub.add_parser("effect"); e.add_argument("id", type=int); e.add_argument("param", type=int, nargs="?", default=0)
    sp = sub.add_parser("speed"); sp.add_argument("value", type=int)
    ch = sub.add_parser("chase", help="walk one white LED along the strip to learn the layout")
    ch.add_argument("--start", type=int, default=1, help="first LED number to light")
    ch.add_argument("--delay", type=float, default=0.15, help="seconds per LED")
    ch.add_argument("--no-end", action="store_true", help="do not force the last group to end at 254")
    s = sub.add_parser("screen", help="screen sync (X11)")
    s.add_argument("--counts", default=None,
                   help="LEDs per edge, e.g. bottom=30,left=20,top=30,right=20 (default: estimated)")
    s.add_argument("--path-order", default="bottom:left,left:up,top:right,right:down",
                   help="order the strip runs around the screen, as seen from the FRONT")
    s.add_argument("--fps", type=int, default=20)
    s.add_argument("--smooth", type=float, default=0.5, help="0 = none, 0.9 = very smooth")
    s.add_argument("--depth", type=float, default=0.12, help="fraction of screen edge to sample")
    s.add_argument("--monitor", type=int, default=1)
    an = sub.add_parser("animate", help="run a software-generated lighting effect")
    an.add_argument("name", choices=["color_flow", "aurora", "breathe", "comet", "ember"])
    an.add_argument("--fps", type=int, default=20)
    an.add_argument("--color", default="4c8dff", help="base color for Breathe and Comet, hex RGB")
    mu = sub.add_parser("music", help="react to live system audio via PipeWire")
    mu.add_argument("--fps", type=int, default=20)
    mu.add_argument("--sensitivity", type=float, default=1.5, help="audio response gain, 0.2–5")
    mu.add_argument("--mode", choices=["combined", "bass", "treble", "voice"], default="combined")
    mu.add_argument("--color", default="4c8dff", help="palette anchor color in hex RGB")
    mu.add_argument("--bass-color", default=None, help="custom bass zone color in hex RGB")
    mu.add_argument("--voice-color", default=None, help="custom voice/mids zone color in hex RGB")
    mu.add_argument("--treble-color", default=None, help="custom treble zone color in hex RGB")
    mu.add_argument("--counts", default="bottom=30,left=17,top=30,right=17",
                    help="perimeter LED counts in strip order, used by Combined mode")
    notify = sub.add_parser("notification", help="play one desktop notification light pattern")
    notify.add_argument("pattern", choices=["single", "double", "breathe", "ping", "urgent"])
    notify.add_argument("r", type=int)
    notify.add_argument("g", type=int)
    notify.add_argument("b", type=int)
    notify.add_argument("--base-color", nargs=3, type=int,
                        help="steady RGB color between flashes; omit when the strip was off")
    a = ap.parse_args()

    if a.cmd == "music":
        ap.error("Music reactive is temporarily unavailable while it is under redesign (work in progress).")
    if a.cmd == "effect" and a.id == 3:
        ap.error("Pickup sound-reactive presets are temporarily unavailable while Music Reactive is under redesign.")

    g = G1(a.path, a.order, a.verbose, a.proto, a.chunk)
    inf = g.info()
    if a.leds:
        g.lamps = a.leds
    if a.cmd == "info":
        print(inf or "No reply to info request (device may still work; try -v).")
        return
    if a.cmd == "color":
        g.solid(a.r, a.g, a.b)
        save_device_state("solid", power=True, color=[a.r, a.g, a.b])
        cfg = load_config()
        cfg["color"] = [a.r, a.g, a.b]
        cfg.pop("effect", None)
        save_config(cfg)
    elif a.cmd == "off":
        reply = g.turn_off()
        g.solid(0, 0, 0)
        save_device_state("off", power=False)
        cfg = load_config()
        cfg.pop("effect", None)
        save_config(cfg)
        print("Turn-off acknowledged." if reply else "Turn-off command sent.")
    elif a.cmd == "count":
        n = find_count(g)
        if n:
            print(f"\nThe strip has {n} LEDs. Lighting all of them green to confirm...")
            g.groups([1, 0, 255, 0, n, n + 1, 0, 0, 0, 254])
            time.sleep(4)
            g.solid(0, 0, 0)
            cfg = load_config()
            cfg["leds"] = n
            save_config(cfg)
            print(f"Saved leds={n} to {CONFIG}")
    elif a.cmd == "groups":
        if len(a.values) % 5:
            sys.exit("groups needs a multiple of 5 numbers: first R G B last ...")
        g.groups(a.values)
    elif a.cmd == "rgbtest":
        for name, col in (("RED", (255, 0, 0)), ("GREEN", (0, 255, 0)), ("BLUE", (0, 0, 255)), ("WHITE", (255, 255, 255))):
            print("Now showing:", name)
            g.solid(*col)
            time.sleep(2)
        g.solid(0, 0, 0)
    elif a.cmd == "brightness":
        reply = g.brightness(a.value)
        if reply:
            print(f"Brightness acknowledged: {reply[5]}/255")
            time.sleep(0.02)
            color = load_config().get("color")
            if isinstance(color, list) and len(color) == 3 and all(isinstance(c, int) and 0 <= c <= 255 for c in color):
                if load_config().get("effect"):
                    print("Left the selected device effect running.")
                else:
                    g.solid(*color)
                    print("Reapplied the saved static color.")
            else:
                print("No saved static color; set a color once, then brightness can be reapplied.")
        else:
            print("Brightness command sent, but the device did not acknowledge it.")
        save_device_state(brightness=max(5, a.value))
    elif a.cmd == "effect":
        reply = g.effect(a.id, a.param)
        save_device_state("effect", power=True, effect_id=a.id, effect_param=a.param)
        cfg = load_config()
        cfg["effect"] = [a.id, a.param]
        save_config(cfg)
        print("Effect acknowledged." if reply else "Effect command sent.")
    elif a.cmd == "speed":
        g.speed(a.value)
    elif a.cmd == "chase":
        n = a.leds or g.lamps
        g.no_end = a.no_end
        for i in range(a.start - 1, n):
            g.frame_colors([(255, 255, 255) if j == i else (0, 0, 0) for j in range(n)])
            print(f"LED {i + 1}/{n}", end="\r", flush=True)
            time.sleep(a.delay)
        print()
    elif a.cmd == "screen":
        counts = parse_counts(a.counts) if a.counts else None
        if counts and sum(counts.values()) != g.lamps:
            print(f"Warning: counts add up to {sum(counts.values())} but the LED count is {g.lamps}.")
        save_device_state("screen", power=True, fps=a.fps, monitor=a.monitor)
        try:
            screen_sync(g, counts, parse_path(a.path_order), a.fps, a.smooth, a.depth, a.monitor)
        except KeyboardInterrupt:
            pass
        finally:
            turn_off_if_connected(g)
    elif a.cmd == "animate":
        try:
            color = a.color.lstrip("#")
            if len(color) != 6:
                raise ValueError
            base_color = tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            sys.exit("--color must be a six-digit hex RGB value, such as 4c8dff.")
        cfg = load_config()
        cfg.pop("effect", None)
        save_config(cfg)
        save_device_state("animate", power=True, animation=a.name, color=list(base_color))
        try:
            run_animation(g, a.name, a.fps, base_color)
        except KeyboardInterrupt:
            pass
        finally:
            turn_off_if_connected(g)
    elif a.cmd == "music":
        if not 1 <= a.fps <= 60:
            sys.exit("--fps must be between 1 and 60.")
        if not 0.2 <= a.sensitivity <= 5:
            sys.exit("--sensitivity must be between 0.2 and 5.")
        try:
            color = a.color.lstrip("#")
            if len(color) != 6:
                raise ValueError
            base_color = tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
            zone_colors = {}
            for zone in ("bass", "voice", "treble"):
                raw_color = getattr(a, f"{zone}_color")
                raw_color = raw_color.lstrip("#") if raw_color is not None else color
                if len(raw_color) != 6:
                    raise ValueError
                zone_colors[zone] = tuple(int(raw_color[i:i + 2], 16) for i in (0, 2, 4))
            edge_counts = parse_edge_counts(a.counts)
        except ValueError:
            sys.exit("Music colors must be six-digit hex RGB values and --counts must use bottom,left,top,right.")
        cfg = load_config()
        cfg.pop("effect", None)
        save_config(cfg)
        save_device_state("music", power=True, audio_mode=a.mode)
        try:
            run_music(g, a.fps, a.sensitivity, a.mode, base_color, edge_counts, zone_colors)
        except KeyboardInterrupt:
            pass
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)
        finally:
            turn_off_if_connected(g)
    elif a.cmd == "notification":
        alert = tuple(max(0, min(255, channel)) for channel in (a.r, a.g, a.b))
        base = tuple(max(0, min(255, channel)) for channel in a.base_color) if a.base_color else None
        run_notification_effect(g, a.pattern, alert, base)


if __name__ == "__main__":
    main()
