#!/usr/bin/env python3
"""Authenticated local-network bridge for the G1 Light Sync companions."""
from __future__ import annotations
import hmac, ipaddress, json, os, re, secrets, signal, shutil, socket, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOME = Path.home()
ROOT = Path(__file__).resolve().parent
CONTROLLER = ROOT / "mitsuko_g1.py"
GUI_CONFIG = HOME / ".config/mitsuko-g1/mitsuko_g1_gui.json"
CONFIG = HOME / ".config/mitsuko-g1/mitsuko_g1.json"
HELPER = HOME / ".local/share/wayland-screenshot-venv/bin/wayland-screenshot"
PORT = 8765
DISCOVERY_PORT = 8766
DISCOVERY_REQUEST = b'MITSUKO_G1_DISCOVER_V1'
ADB = HOME / '.local/bin/adb'
PAIR_FILE = HOME / '.config/mitsuko-g1/phone_pairing.json'
TOKEN_FILE = HOME / '.config/mitsuko-g1/phone_access_token'
STATE_FILE = Path(os.environ.get('XDG_CONFIG_HOME', HOME / '.config')) / 'mitsuko-g1/device_state.json'
_lock = threading.RLock()
_client_lock = threading.RLock()
_pair_attempts = {}
_connected_clients = {}
CLIENT_TTL = 20


def private_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    os.chmod(path, 0o600)


def load_or_create_token():
    try:
        token = TOKEN_FILE.read_text().strip()
        if len(token) >= 40:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(48)
    private_write(TOKEN_FILE, token)
    return token


def rotate_pair_pin():
    global PAIR_PIN
    PAIR_PIN = f'{secrets.randbelow(100_000_000):08d}'
    private_write(PAIR_FILE, json.dumps({'pin': PAIR_PIN}))
    return PAIR_PIN


ACCESS_TOKEN = load_or_create_token()
try:
    PAIR_PIN = str(json.loads(PAIR_FILE.read_text()).get('pin', ''))
    if len(PAIR_PIN) != 8 or not PAIR_PIN.isdigit(): raise ValueError('Invalid pairing code')
except (OSError, ValueError, TypeError):
    PAIR_PIN = f'{secrets.randbelow(100_000_000):08d}'
    private_write(PAIR_FILE, json.dumps({'pin': PAIR_PIN}))


def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def register_client(handler):
    client_id = handler.headers.get('X-G1-Client-Id', '')
    if not re.fullmatch(r'[A-Za-z0-9-]{8,64}', client_id):
        return
    raw_name = handler.headers.get('X-G1-Client-Name', 'Phone')
    name = ''.join(ch for ch in raw_name if ch.isprintable()).strip()[:48] or 'Phone'
    with _client_lock:
        _connected_clients[client_id] = {'name': name, 'seen': time.monotonic()}
        cutoff = time.monotonic() - CLIENT_TTL
        for key in [key for key, client in _connected_clients.items() if client['seen'] < cutoff]:
            del _connected_clients[key]


def connected_clients():
    cutoff = time.monotonic() - CLIENT_TTL
    with _client_lock:
        for key in [key for key, client in _connected_clients.items() if client['seen'] < cutoff]:
            del _connected_clients[key]
        return sorted(({'name': client['name']} for client in _connected_clients.values()),
                      key=lambda client: client['name'].casefold())


def active_mode():
    try:
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit():
                continue
            try:
                args = [a for a in (entry / 'cmdline').read_bytes().decode(errors='ignore').split('\0') if a]
            except OSError:
                continue
            joined = ' '.join(args)
            if 'mitsuko_g1.py' in joined:
                for mode in ('screen', 'music', 'animate'):
                    if mode in args:
                        return mode
    except OSError:
        pass
    return 'idle'


def capture_status():
    helper = str(HELPER) if HELPER.exists() else 'wayland-screenshot'
    try:
        result = subprocess.run([helper, 'cast', 'status', '--json'], capture_output=True, text=True, timeout=3)
        data = json.loads(result.stdout) if result.returncode == 0 else {}
        return {'running': bool(data.get('running')), 'width': data.get('width'), 'height': data.get('height')}
    except Exception:
        return {'running': False}


def usb_connected():
    try:
        if subprocess.run(['lsusb', '-d', '1a86:fe07'], capture_output=True, timeout=2).stdout.strip():
            return True
    except (OSError, subprocess.TimeoutExpired):
        pass
    return any('00001A86:0000FE07' in p.read_text(errors='ignore')
               for p in Path('/sys/bus/hid/devices').glob('*/uevent'))


def status():
    return {'ok': True, 'deviceConnected': usb_connected(), 'mode': active_mode(),
            'lightState': read_json(STATE_FILE, {}),
            'connectedDevices': connected_clients(), 'capture': capture_status()}


def keep_usb_reverse():
    """Recreate ADB's reverse tunnel after USB reconnects or ADB restarts."""
    adb = str(ADB) if ADB.is_file() else shutil.which('adb')
    if not adb:
        return
    while True:
        try:
            devices = subprocess.run([adb, 'devices'], capture_output=True, text=True, timeout=4)
            serial = next((line.split()[0] for line in devices.stdout.splitlines()[1:]
                           if '\tdevice' in line and 'usb:' in line), None)
            if serial:
                subprocess.run([adb, '-s', serial, 'reverse', f'tcp:{PORT}', f'tcp:{PORT}'],
                               capture_output=True, timeout=4)
        except (OSError, subprocess.TimeoutExpired):
            pass
        time.sleep(3)


def serve_discovery():
    """Reply to phone broadcasts with the service port; no address entry needed."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('', DISCOVERY_PORT))
            while True:
                message, peer = sock.recvfrom(512)
                try:
                    is_private = ipaddress.ip_address(peer[0]).is_private
                except ValueError:
                    is_private = False
                if message == DISCOVERY_REQUEST and is_private:
                    answer = json.dumps({'service':'mitsuko-g1','port':PORT,'version':1}).encode()
                    sock.sendto(answer, peer)
    except OSError:
        pass


def publish_bonjour():
    """Advertise the HTTP control service for native iOS Bonjour discovery."""
    try:
        import dbus
        bus = dbus.SystemBus()
        server = bus.get_object('org.freedesktop.Avahi', '/')
        path = server.EntryGroupNew(dbus_interface='org.freedesktop.Avahi.Server')
        group = bus.get_object('org.freedesktop.Avahi', path)
        group.AddService(dbus.Int32(-1), dbus.Int32(-1), dbus.UInt32(0),
                         dbus.String('G1 Light Sync'), dbus.String('_mitsukog1._tcp'),
                         dbus.String(''), dbus.String(''), dbus.UInt16(PORT),
                         dbus.Array([], signature='s'),
                         dbus_interface='org.freedesktop.Avahi.EntryGroup')
        group.Commit(dbus_interface='org.freedesktop.Avahi.EntryGroup')
        while True:
            time.sleep(60)
    except Exception:
        pass


def terminate_active():
    pids = []
    commands = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            args = [a for a in (entry / 'cmdline').read_bytes().decode(errors='ignore').split('\0') if a]
            if 'mitsuko_g1.py' in ' '.join(args) and any(m in args for m in ('screen', 'music', 'animate')):
                pids.append(int(entry.name))
                commands.append(args)
        except OSError:
            continue
    for pid in pids:
        try:
            os.killpg(pid, signal.SIGINT)
        except OSError:
            try: os.kill(pid, signal.SIGINT)
            except OSError: pass
    deadline = time.monotonic() + 2.5
    while time.monotonic() < deadline and any(Path(f'/proc/{p}').exists() for p in pids):
        time.sleep(.1)
    for pid in pids:
        if Path(f'/proc/{pid}').exists():
            try: os.killpg(pid, signal.SIGTERM)
            except OSError:
                try: os.kill(pid, signal.SIGTERM)
                except OSError: pass
    return commands[0] if commands else None


def restart_command(args):
    if args and Path(args[1]).is_file():
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, env={**os.environ, 'WAYLAND_SCREENSHOT_BIN': str(HELPER)})


def cli(*args):
    if not CONTROLLER.is_file():
        raise RuntimeError('G1 Light Sync controller is missing from the per-user app install.')
    return [os.environ.get('PYTHON', '/usr/bin/python3'), str(CONTROLLER), *map(str, args)]


def run_command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=12,
                            env={**os.environ, 'WAYLAND_SCREENSHOT_BIN': str(HELPER)})
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or 'Controller command failed').strip()[-500:])
    return (result.stdout or 'Command sent').strip()


def action(data):
    command = str(data.get('action', ''))
    with _lock:
        if command == 'stop':
            terminate_active()
            return {'ok': True, 'message': 'Live mode stopped', 'status': status()}
        if command == 'screen':
            settings = read_json(GUI_CONFIG, {})
            profile_name = str(data.get('profile', ''))
            profile = settings.get('profiles', {}).get(profile_name, {}) if profile_name else settings
            counts = profile.get('counts', settings.get('counts', {'bottom':31,'left':16,'top':31,'right':16}))
            leds = int(profile.get('leds', settings.get('leds', 94)))
            fps = int(profile.get('fps', settings.get('fps', 20)))
            smooth = float(profile.get('smooth', settings.get('smooth', .5)))
            depth = float(profile.get('depth', settings.get('depth', .2)))
            monitor = int(profile.get('monitor', settings.get('monitor', 1)))
            terminate_active()
            proc = subprocess.Popen(cli('--leds', leds, 'screen', '--counts', ','.join(f'{k}={int(counts.get(k,0))}' for k in ('bottom','left','top','right')),
                                        '--fps', fps, '--smooth', smooth, '--depth', depth, '--monitor', monitor),
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    start_new_session=True, env={**os.environ, 'WAYLAND_SCREENSHOT_BIN': str(HELPER)})
            return {'ok': True, 'message': 'Screen sync started', 'pid': proc.pid, 'status': status()}
        if command == 'music':
            raise ValueError('Music reactive is temporarily unavailable while it is under redesign (work in progress).')
        if command == 'animate':
            name = str(data.get('name', ''))
            if name not in ('color_flow', 'aurora', 'breathe', 'comet', 'ember'):
                raise ValueError('Unknown app lighting effect')
            color = str(data.get('color', '4c8dff')).strip().lstrip('#')
            if not re.fullmatch(r'[0-9a-fA-F]{6}', color):
                raise ValueError('Effect color must be a six-digit RGB value')
            settings = read_json(GUI_CONFIG, {})
            leds = int(settings.get('leds', 94))
            fps = int(settings.get('fps', 20))
            if not 1 <= leds <= 254 or not 1 <= fps <= 60:
                raise ValueError('Invalid LED count or effect frame rate in desktop settings')
            terminate_active()
            proc = subprocess.Popen(cli('--proto', 'sync', '--leds', leds, 'animate', name,
                                        '--fps', fps, '--color', color.lower()),
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    start_new_session=True, env={**os.environ, 'WAYLAND_SCREENSHOT_BIN': str(HELPER)})
            label = name.replace('_', ' ').title()
            return {'ok': True, 'message': f'{label} started', 'pid': proc.pid, 'status': status()}
        if command == 'brightness':
            value = max(5, min(255, int(data.get('value', 180))))
            previous = terminate_active()
            message = run_command(cli('brightness', value))
            restart_command(previous)
            return {'ok': True, 'message': message, 'status': status()}
        if command == 'color':
            rgb = data.get('rgb', [70, 190, 155])
            if not isinstance(rgb, list) or len(rgb) != 3:
                raise ValueError('Expected three RGB values')
            rgb = [max(0, min(255, int(v))) for v in rgb]
            terminate_active()
            return {'ok': True, 'message': run_command(cli('color', *rgb)), 'status': status()}
        if command == 'effect':
            effect_id = int(data.get('id', 2)); param = int(data.get('param', 0))
            if effect_id not in (2,3) or not 0 <= param <= 6:
                raise ValueError('Unsupported effect')
            if effect_id == 3:
                raise ValueError('Sound-reactive Pickup presets are temporarily unavailable while Music Reactive is under redesign.')
            terminate_active()
            return {'ok': True, 'message': run_command(cli('effect', effect_id, param)), 'status': status()}
        if command == 'off':
            terminate_active()
            return {'ok': True, 'message': run_command(cli('off')), 'status': status()}
        raise ValueError('Unknown action')


class Handler(BaseHTTPRequestHandler):
    server_version = 'G1LightSyncPhoneBridge/1.0'
    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code); self.send_header('Content-Type','application/json')
        self.send_header('Content-Length', str(len(body))); self.send_header('Cache-Control','no-store')
        self.end_headers(); self.wfile.write(body)
    def _is_local(self):
        try:
            return ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            return False
    def _is_private_peer(self):
        try:
            return ipaddress.ip_address(self.client_address[0]).is_private
        except ValueError:
            return False
    def _authorized(self):
        if self._is_local():
            return True
        supplied = self.headers.get('Authorization', '')
        return supplied.startswith('Bearer ') and hmac.compare_digest(supplied[7:], ACCESS_TOKEN)
    def _read_json(self):
        size = int(self.headers.get('Content-Length', '0'))
        if size > 4096: raise ValueError('Request too large')
        return json.loads(self.rfile.read(size))
    def do_GET(self):
        if not self._is_local() and not self._is_private_peer():
            self._json(403, {'ok':False,'error':'G1 Light Sync phone access is limited to your local network'}); return
        if not self._authorized():
            self._json(401, {'ok':False,'error':'Pair this phone with the laptop first'}); return
        if self.path == '/api/status':
            register_client(self)
            self._json(200, status())
        elif self.path == '/api/profiles':
            settings = read_json(GUI_CONFIG, {})
            self._json(200, {'profiles': sorted(settings.get('profiles', {}).keys())})
        elif self.path == '/health': self._json(200, {'ok': True})
        else: self._json(404, {'ok':False,'error':'Not found'})
    def do_POST(self):
        try:
            data = self._read_json()
            if self.path == '/api/pair':
                if not self._is_private_peer():
                    self._json(403, {'ok':False,'error':'Pairing is limited to your local network'}); return
                if self._is_local():
                    self._json(400, {'ok':False,'error':'Use the Wi-Fi address to pair'}); return
                now = time.monotonic(); remote = self.client_address[0]
                recent = [t for t in _pair_attempts.get(remote, []) if now - t < 300]
                if len(recent) >= 5:
                    _pair_attempts[remote] = recent
                    self._json(429, {'ok':False,'error':'Too many pairing attempts; try again in five minutes'}); return
                recent.append(now); _pair_attempts[remote] = recent
                if not hmac.compare_digest(str(data.get('pin', '')), PAIR_PIN):
                    self._json(403, {'ok':False,'error':'Pairing code did not match'}); return
                self._json(200, {'ok':True,'token':ACCESS_TOKEN,'message':'Paired with G1 Light Sync'})
                rotate_pair_pin()
                return
            if self.path != '/api/action': self._json(404, {'ok':False,'error':'Not found'}); return
            if not self._is_local() and not self._is_private_peer():
                self._json(403, {'ok':False,'error':'G1 Light Sync phone access is limited to your local network'}); return
            if not self._authorized():
                self._json(401, {'ok':False,'error':'Pair this phone with the laptop first'}); return
            register_client(self)
            self._json(200, action(data))
        except Exception as exc:
            self._json(400, {'ok':False,'error':str(exc)[:500]})
    def log_message(self, *_args): pass

if __name__ == '__main__':
    threading.Thread(target=keep_usb_reverse, daemon=True, name='adb-reverse-watch').start()
    threading.Thread(target=serve_discovery, daemon=True, name='wifi-discovery').start()
    threading.Thread(target=publish_bonjour, daemon=True, name='bonjour-advertiser').start()
    try:
        ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
    except OSError as exc:
        if exc.errno != 98: raise
