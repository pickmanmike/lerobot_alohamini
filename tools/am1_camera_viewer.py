#!/usr/bin/env python3
"""AM1 camera-only LAN viewer. No LeRobot, motor, camera-decoder or ZMQ imports."""

from collections import deque
from email.message import Message
import argparse
import base64
import getpass
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit


ROLES = ("forward", "backward", "chest", "wrist_left", "wrist_right")
MAX_JPEG = 1_000_000
FRESH_SECONDS = 0.5
BINARY_SHA256 = "359fabade8a7a51e81a55fe6df6b0ef81764a5e1d63179577534eaaa71904b50"


def validate_config(config):
    if not isinstance(config, dict) or set(config) != {"version", "bind", "port", "cameras"}:
        raise ValueError("Expected version, bind, port and cameras only")
    if type(config["version"]) is not int or config["version"] != 1:
        raise ValueError("Unsupported camera config version")
    address = ipaddress.IPv4Address(config["bind"])
    if not address.is_private or address.is_loopback or address.is_unspecified or address.is_multicast:
        raise ValueError("Bind must be one explicit private LAN IPv4 address")
    if type(config["port"]) is not int or config["port"] != 1984:
        raise ValueError("Camera viewer uses LAN port 1984 only")
    cameras = config["cameras"]
    if not isinstance(cameras, dict) or not cameras or set(cameras) - set(ROLES):
        raise ValueError("Map at least one approved camera role; never invent missing roles")
    for role, path in cameras.items():
        if not isinstance(path, str) or not (
            path == f"/dev/am_camera_{role}"
            or re.fullmatch(r"/dev/v4l/by-path/[A-Za-z0-9_.:-]+-video-index0", path)
        ):
            raise ValueError("Only the role's camera alias or capture-index0 by-path identity is allowed")
    if len(set(cameras.values())) != len(cameras):
        raise ValueError("Camera paths must be unique")
    return {**config, "cameras": dict(cameras)}


def backend_config(config, password):
    return {
        "app": {"modules": ["api", "mjpeg", "v4l2"]},
        "api": {
            "listen": "127.0.0.1:1985", "username": "camera_backend", "password": password,
            "local_auth": True, "allow_paths": ["/api/stream.mjpeg"],
        },
        "rtsp": {"listen": ""}, "webrtc": {"listen": ""},
        "streams": {
            role: f"v4l2:device?video={path}&input_format=mjpeg&video_size=640x480&framerate=30"
            for role, path in config["cameras"].items()
        },
        "log": {"level": "warn"},
    }


class FrameStore:
    """Only a completed upstream multipart frame advances freshness or sequence."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.condition = threading.Condition()
        self.frame = None
        self.arrived = None
        self.sequence = 0
        self.connected = False
        self.received = 0
        self.max_gap = 0.0
        self.arrivals = deque(maxlen=151)

    def publish(self, jpeg):
        # These UVC cameras pad native JPEG payloads to an 8-byte boundary.
        # Accept only EOI plus at most seven zero bytes; preserve the entire payload.
        if not 4 <= len(jpeg) <= MAX_JPEG or not jpeg.startswith(b"\xff\xd8") or not jpeg[-9:].rstrip(b"\0").endswith(b"\xff\xd9"):
            raise ValueError("Invalid or oversized JPEG frame")
        now = self.clock()
        with self.condition:
            if self.arrived is not None:
                self.max_gap = max(self.max_gap, now - self.arrived)
            self.frame, self.arrived = jpeg, now
            self.sequence += 1
            self.received += len(jpeg)
            self.connected = True
            self.arrivals.append(now)
            self.condition.notify_all()

    def disconnected(self):
        with self.condition:
            self.connected = False
            self.condition.notify_all()

    def _fresh(self, now):
        return self.connected and self.arrived is not None and 0 <= now - self.arrived <= FRESH_SECONDS

    def snapshot(self):
        with self.condition:
            now = self.clock()
            if not self._fresh(now):
                return None
            return self.frame, self.sequence, (now - self.arrived) * 1000

    def status(self):
        with self.condition:
            now = self.clock()
            state = "fresh" if self._fresh(now) else "unavailable" if self.arrived is None else "stale"
            recent = [t for t in self.arrivals if now - t <= 5]
            span = recent[-1] - recent[0] if len(recent) > 1 else 0
            return {
                "state": state, "sequence": self.sequence,
                "age_ms": None if self.arrived is None else round((now - self.arrived) * 1000, 3),
                "fps": round((len(recent) - 1) / span, 3) if span > 0 and state == "fresh" else 0,
                "bytes_received": self.received, "max_gap_ms": round(self.max_gap * 1000, 3),
            }


def iter_mjpeg(stream, content_type):
    message = Message()
    message["content-type"] = content_type
    boundary = message.get_param("boundary")
    if message.get_content_type() != "multipart/x-mixed-replace" or not boundary or len(boundary) > 100:
        raise ValueError("Expected a bounded MJPEG multipart boundary")
    boundary = b"--" + boundary.encode("ascii")

    def line():
        value = stream.readline(8193)
        if not value:
            raise EOFError("MJPEG connection ended")
        if len(value) > 8192 or not value.endswith(b"\n"):
            raise ValueError("Invalid MJPEG header")
        return value.rstrip(b"\r\n")

    while True:
        part = line()
        if not part:
            part = line()
        if part != boundary:
            raise ValueError("Unexpected MJPEG boundary")
        headers = {}
        size = 0
        while value := line():
            size += len(value)
            if size > 8192 or b":" not in value:
                raise ValueError("Invalid MJPEG headers")
            name, value = value.split(b":", 1)
            name = name.lower()
            if name in headers:
                raise ValueError("Duplicate MJPEG header")
            headers[name] = value.strip()
        length = int(headers.get(b"content-length", b"0"))
        if not 4 <= length <= MAX_JPEG or headers.get(b"content-type") != b"image/jpeg":
            raise ValueError("Invalid MJPEG frame type or length")
        jpeg = stream.read(length)
        if len(jpeg) != length:
            raise EOFError("Truncated MJPEG frame")
        yield jpeg


class ViewerServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = 16

    def __init__(self, address, stores, configured, credentials):
        self.stores = stores
        self.configured = frozenset(configured)
        self.stop_event = threading.Event()
        self.slots = threading.BoundedSemaphore(24)
        value = f"{credentials['username']}:{credentials['password']}".encode()
        self.authorization = b"Basic " + base64.b64encode(value)
        super().__init__(address, ViewerHandler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        request.settimeout(2)
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        # Never echo request targets/auth into a traceback or public log.
        print('CAMERA_HTTP_ERROR', flush=True)


class ViewerHandler(BaseHTTPRequestHandler):
    server_version = "AM1Camera/1"
    sys_version = ""

    def log_message(self, format, *args):
        pass  # URLs, headers and credentials are not access-log data.

    def send_view_headers(self, status, mime, length=None, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; img-src 'self' blob:; "
                         "script-src 'self'; style-src 'self'; connect-src 'self'; "
                         "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.send_header("Connection", "close")
        if length is not None:
            self.send_header("Content-Length", str(length))
        for name, value in (extra or {}).items():
            self.send_header(name, str(value))
        self.end_headers()

    def reply(self, status, body=b"", mime="text/plain; charset=utf-8", extra=None):
        self.send_view_headers(status, mime, len(body), extra)
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        try:
            self.get_view()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    def get_view(self):
        supplied = self.headers.get_all("Authorization", [])
        if len(supplied) != 1 or not hmac.compare_digest(supplied[0].encode(), self.server.authorization):
            self.reply(401, b"Authentication required", extra={"WWW-Authenticate": 'Basic realm="AM1 cameras"'})
            return
        if self.headers.get("Upgrade") or self.headers.get("Transfer-Encoding"):
            self.reply(403)
            return
        target = urlsplit(self.path)
        if target.scheme or target.netloc or target.fragment:
            self.reply(400)
            return
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/freshness.js": ("freshness.js", "text/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8")}
        if target.path in assets:
            if target.query:
                self.reply(400)
                return
            name, mime = assets[target.path]
            self.reply(200, (Path(__file__).parent / "am1_camera" / name).read_bytes(), mime)
            return
        if target.path == "/status.json":
            if target.query:
                self.reply(400)
                return
            report = {"format": "MJPG", "width": 640, "height": 480, "requested_fps": 30,
                      "freshness": "complete upstream frame arrival; not HTTP/cache time",
                      "cameras": {role: {**store.status(), "configured": role in self.server.configured}
                                  for role, store in self.server.stores.items()}}
            self.reply(200, json.dumps(report).encode(), "application/json")
            return
        if target.path not in ("/api/frame.jpeg", "/api/stream.mjpeg"):
            self.reply(404)
            return
        try:
            query = parse_qs(target.query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
        except ValueError:
            self.reply(400)
            return
        allowed = {"src", "cache"} if target.path == "/api/frame.jpeg" else {"src"}
        if not query or set(query) - allowed or any(len(v) != 1 for v in query.values()):
            self.reply(400)
            return
        if "cache" in query and query["cache"] != ["500ms"]:
            self.reply(400)
            return
        role = query.get("src", [""])[0]
        if role not in self.server.configured:
            self.reply(404)
            return
        store = self.server.stores[role]
        sample = store.snapshot()
        if sample is None:
            self.reply(503, b"Camera frame unavailable or stale")
            return
        if target.path == "/api/frame.jpeg":
            jpeg, sequence, age = sample
            self.reply(200, jpeg, "image/jpeg", {"X-Frame-Sequence": sequence, "X-Frame-Age-Ms": round(age, 3)})
            return
        self.send_view_headers(200, "multipart/x-mixed-replace; boundary=frame")
        last = 0
        while not self.server.stop_event.is_set():
            sample = store.snapshot()
            if sample is None:
                break
            jpeg, sequence, age = sample
            if sequence != last:
                self.wfile.write((f"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n"
                                  f"X-Frame-Sequence: {sequence}\r\nX-Frame-Age-Ms: {age:.3f}\r\n\r\n").encode())
                self.wfile.write(jpeg + b"\r\n")
                self.wfile.flush()
                last = sequence
            with store.condition:
                store.condition.wait(timeout=0.1)

    def refuse(self):
        self.reply(403)

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = do_TRACE = do_CONNECT = refuse


def make_server(address, stores, configured, credentials):
    return ViewerServer(address, stores, configured, credentials)


def read_connection(connection, role, authorization, store, stop):
    try:
        connection.request("GET", f"/api/stream.mjpeg?src={role}", headers={"Authorization": authorization})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("Camera backend unavailable")
        for jpeg in iter_mjpeg(response, response.getheader("Content-Type", "")):
            if stop.is_set():
                break
            store.publish(jpeg)
    finally:
        store.disconnected()
        connection.close()


class CameraReader(threading.Thread):
    """One continuous bounded loopback reader per source; never opens a device."""

    def __init__(self, role, authorization, store, stop, connection_factory=http.client.HTTPConnection):
        super().__init__(name=f"camera-{role}", daemon=True)
        self.role, self.authorization, self.store, self.stop = role, authorization, store, stop
        self.connection_factory = connection_factory

    def run(self):
        while not self.stop.is_set():
            try:
                connection = self.connection_factory("127.0.0.1", 1985, timeout=1)
                read_connection(connection, self.role, self.authorization, self.store, self.stop)
            except (OSError, ValueError, EOFError, http.client.HTTPException):
                self.store.disconnected()
            self.stop.wait(0.5)


def cleanup(stop, workers, server, child):
    """Stop only our readers/child; collect failures without hiding a primary exception."""
    stop.set()
    errors = []
    if server is not None:
        try:
            server.stop_event.set()
            server.server_close()
        except Exception as exc:
            errors.append(f"http-close:{type(exc).__name__}")
    if child is not None and child.poll() is None:
        try:
            child.terminate()
            try:
                child.wait(timeout=4)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=2)
                errors.append("backend-required-kill")
        except Exception as exc:
            errors.append(f"backend-stop:{type(exc).__name__}")
    for worker in workers:
        try:
            worker.join(timeout=2)
            if worker.is_alive():
                errors.append(f"reader-not-stopped:{worker.name}")
        except Exception as exc:
            errors.append(f"reader-join:{type(exc).__name__}")
    return errors


def write_private(path, value):
    """Create, never overwrite, owner-only configuration/credentials."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def load_private(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, encoding="utf-8") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 16384:
            raise ValueError("Private config must be a small regular file")
        if os.name == "posix" and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise ValueError("Private config must be owned by this user with mode 0600")
        return json.load(stream)


def validate_credentials(value):
    if not isinstance(value, dict) or set(value) != {"username", "password"}:
        raise ValueError("Expected private username and password only")
    if not isinstance(value["username"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value["username"]):
        raise ValueError("Username must contain 1–64 ASCII letters, digits, underscore or dash")
    password = value["password"]
    if not isinstance(password, str) or not 12 <= len(password) <= 256 or any(ord(c) < 32 or ord(c) > 126 for c in password):
        raise ValueError("Use 12–256 printable ASCII characters for the private viewing password")
    return value


def preflight(config, binary):
    """Read-only checks. No device open, USB trigger or motor import."""
    if sys.platform != "linux":
        raise ValueError("Camera capture is Pi/Linux-only; --check is hardware-free")
    if hashlib.sha256(binary.read_bytes()).hexdigest() != BINARY_SHA256:
        raise ValueError("Expected the approved official go2rtc v1.9.14 ARM64 binary")
    result = subprocess.run(["pgrep", "-f", "[l]erobot.robots.alohamini.alohamini_host"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
    if result.returncode != 1:
        raise ValueError("Motor host must be stopped (or host process check failed)")
    resolved = []
    for path in config["cameras"].values():
        device = Path(path).resolve(strict=True)
        if not re.fullmatch(r"/dev/video[0-9]+", str(device)) or not stat.S_ISCHR(device.stat().st_mode):
            raise ValueError("Camera identity did not resolve to a V4L2 character device")
        if (Path("/sys/class/video4linux") / device.name / "index").read_text().strip() != "0":
            raise ValueError("Camera identity is not a capture-index0 device")
        resolved.append(str(device))
    if len(set(resolved)) != len(resolved):
        raise ValueError("Two roles resolve to the same capture device")
    result = subprocess.run(["fuser", *resolved], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=3)
    if result.returncode != 1:
        raise ValueError("Camera already owned (or device-owner check failed); stop the other owner first")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 1985))  # Refuse a pre-existing backend; never kill it.


def run_viewer(config, credentials, binary, state_dir, duration=None):
    preflight(config, binary)
    stores = {role: FrameStore() for role in ROLES}
    stop = threading.Event()
    workers, server, child, backend_path = [], None, None, None
    primary_failure = False
    try:
        server = make_server((config["bind"], config["port"]), stores, config["cameras"], credentials)
        server.timeout = 0.2
        password = secrets.token_urlsafe(32)
        fd, name = tempfile.mkstemp(prefix="backend-", suffix=".json", dir=state_dir)
        backend_path = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(backend_config(config, password), stream)
        # Isolate child from terminal Ctrl+C. No credentials on argv or in logs.
        child = subprocess.Popen([str(binary), "-config", str(backend_path)], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        authorization = "Basic " + base64.b64encode(f"camera_backend:{password}".encode()).decode()
        for role in config["cameras"]:
            worker = CameraReader(role, authorization, stores[role], stop)
            workers.append(worker)
            worker.start()
        print(f"CAMERA_VIEW_URL=http://{config['bind']}:{config['port']}", flush=True)
        print("CAMERA_CONFIGURED_ROLES=" + ",".join(config["cameras"]), flush=True)
        print("CAMERA_REQUEST=MJPG 640x480 30fps; delivered rate follows below", flush=True)
        started = last_report = time.monotonic()
        while duration is None or time.monotonic() - started < duration:
            if child.poll() is not None:
                raise RuntimeError("Camera backend exited unexpectedly")
            server.handle_request()
            now = time.monotonic()
            if now - last_report >= 1:
                print("CAMERA_STATUS " + json.dumps({"elapsed_s": round(now - started, 3),
                      "cameras": {role: stores[role].status() for role in config["cameras"]}},
                      separators=(",", ":")), flush=True)
                last_report = now
    except KeyboardInterrupt:
        print("CAMERA_STOP_REQUESTED", flush=True)
    except BaseException:
        primary_failure = True
        raise
    finally:
        errors = cleanup(stop, workers, server, child)
        if backend_path is not None:
            try:
                backend_path.unlink()
            except OSError as exc:
                errors.append(f"private-runtime-cleanup:{type(exc).__name__}")
        print("CAMERA_CLEANUP_ERRORS=" + json.dumps(errors), flush=True)
        if errors and not primary_failure:
            raise RuntimeError("Camera cleanup failed; inspect the private log")
    return 0


def main(argv=None):
    home = Path.home()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=home / ".config/am1-camera/cameras.json")
    parser.add_argument("--credentials", type=Path, default=home / ".config/am1-camera/viewer.json")
    parser.add_argument("--binary", type=Path, default=home / ".local/lib/am1-camera/go2rtc-v1.9.14")
    parser.add_argument("--state-dir", type=Path, default=home / ".local/state/am1-camera")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="Validate config only; never open cameras")
    modes.add_argument("--configure", action="store_true", help="Create private role map; no hardware access")
    modes.add_argument("--init-auth", action="store_true", help="Enter private browser credentials locally")
    parser.add_argument("--bind", help="Explicit LAN IPv4 address for --configure")
    parser.add_argument("--camera", action="append", default=[], metavar="ROLE=CAPTURE_PATH")
    parser.add_argument("--duration", type=float, help="Optional bounded camera-only check, 1–120 seconds")
    args = parser.parse_args(argv)
    if args.duration is not None and not 1 <= args.duration <= 120:
        parser.error("--duration must be finite and within 1–120 seconds")
    if not args.configure and (args.bind or args.camera):
        parser.error("--bind and --camera are only for --configure")
    try:
        if args.configure:
            pairs = [item.split("=", 1) for item in args.camera]
            if any(len(pair) != 2 for pair in pairs) or len({p[0] for p in pairs}) != len(pairs):
                raise ValueError("Use each role once as ROLE=CAPTURE_PATH")
            config = validate_config({"version": 1, "bind": args.bind, "port": 1984, "cameras": dict(pairs)})
            write_private(args.config, config)
            print(f"CAMERA_CONFIG_CREATED={args.config}")
            return 0
        config = validate_config(load_private(args.config))
        if args.check:
            print("CAMERA_CONFIG_OK roles=" + ",".join(config["cameras"]) + "; no device access")
            return 0
        if args.init_auth:
            if not sys.stdin.isatty():
                raise ValueError("Enter credentials in your local interactive terminal, not a pipe")
            username = input("Viewing username [viewer]: ").strip() or "viewer"
            password = getpass.getpass("Viewing password (12+ characters, hidden): ")
            if password != getpass.getpass("Confirm password (hidden): "):
                raise ValueError("Passwords did not match")
            write_private(args.credentials, validate_credentials({"username": username, "password": password}))
            print("CAMERA_PRIVATE_AUTH_CREATED; password was not logged")
            return 0
        credentials = validate_credentials(load_private(args.credentials))
        args.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = args.state_dir.stat()
        if os.name != "posix" or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("Run on Pi with an owner-only mode-0700 camera state directory")
        import fcntl  # Pi-only; hardware-free help/check/import works on Windows.
        fd = os.open(args.state_dir / "viewer.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            previous = signal.signal(signal.SIGTERM, lambda signum, frame: (_ for _ in ()).throw(KeyboardInterrupt()))
            try:
                return run_viewer(config, credentials, args.binary, args.state_dir, args.duration)
            finally:
                signal.signal(signal.SIGTERM, previous)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        # Never print user values, response bodies, URLs or credentials from an exception.
        print(f"CAMERA_REFUSAL {type(exc).__name__}: {exc}" if isinstance(exc, (ValueError, RuntimeError))
              else f"CAMERA_REFUSAL {type(exc).__name__}: check private paths, permissions and owner/port availability",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
