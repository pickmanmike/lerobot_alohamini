#!/usr/bin/env python
"""Loopback-only AM1 console shell; it does not start a robot on page load."""

from __future__ import annotations

import base64
import hmac
import http.client
import json
import secrets
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


MAX_POST_BYTES = 4096
CAMERA_ROLES = frozenset({"forward", "backward", "chest", "wrist_left", "wrist_right"})
CAMERA_ASSETS = frozenset({"app.js", "freshness.js", "mjpeg.js", "style.css"})
CONSOLE_ASSETS = frozenset({"app.js", "style.css"})
ALLOWED_OPERATIONS = frozenset({"Start", "Pause", "Resume", "Stop", "Approve"})
ALLOWED_BODY_KEYS = frozenset("WSZXADUJT G".replace(" ", ""))
COOKIE_NAME = "am1_console"


def _camera_target(path: str) -> str | None:
    """Accept only the narrow viewer routes, never arbitrary backend URLs."""
    target = urlsplit(path)
    if target.scheme or target.netloc or target.fragment:
        return None
    if target.path == "/camera/status.json" and not target.query:
        return "/status.json"
    if target.path not in {"/camera/api/frame.jpeg", "/camera/api/stream.mjpeg"}:
        return None
    try:
        query = parse_qs(target.query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
    except ValueError:
        return None
    allowed = {"src", "cache"} if target.path.endswith("frame.jpeg") else {"src"}
    if not query or set(query) - allowed or any(len(values) != 1 for values in query.values()):
        return None
    if query.get("src", [None])[0] not in CAMERA_ROLES:
        return None
    if "cache" in query and query["cache"] != ["500ms"]:
        return None
    return target.path.removeprefix("/camera") + ("?" + target.query if target.query else "")


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = 32

    def __init__(self, address, config, camera_auth_file: Path, session_adapter):
        if address[0] != "127.0.0.1":
            raise ValueError("AM1 console must bind to 127.0.0.1 only")
        parsed = urlsplit(config.browser_url)
        if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {"", "/"}:
            raise ValueError("Camera browser URL must identify one HTTP viewer origin")
        credentials = json.loads(camera_auth_file.read_text(encoding="utf-8"))
        if (
            not isinstance(credentials, dict) or not isinstance(credentials.get("username"), str)
            or not isinstance(credentials.get("password"), str) or not credentials["username"]
            or not credentials["password"]
        ):
            raise ValueError("Private camera auth file needs username and password")
        self.camera_host = parsed.hostname
        self.camera_port = parsed.port or 80
        self.camera_authorization = "Basic " + base64.b64encode(
            f"{credentials['username']}:{credentials['password']}".encode()
        ).decode()
        self.session_adapter = session_adapter
        self.cookie_token = secrets.token_hex(32)
        self.csrf_token = secrets.token_hex(32)
        self.stop_event = threading.Event()
        self.camera_slots = threading.BoundedSemaphore(8)
        self.stream_slots = threading.BoundedSemaphore(5)
        self.event_slots = threading.BoundedSemaphore(4)
        self.events = deque(maxlen=128)
        self.event_sequence = 0
        self.event_condition = threading.Condition()
        super().__init__(address, ConsoleHandler)

    def emit(self, event: dict) -> None:
        if not isinstance(event, dict):
            return
        with self.event_condition:
            self.event_sequence += 1
            self.events.append((self.event_sequence, dict(event)))
            self.event_condition.notify_all()

    def server_close(self) -> None:
        self.stop_event.set()
        with self.event_condition:
            self.event_condition.notify_all()
        super().server_close()

    def handle_error(self, request, client_address):
        # Never leak camera credentials, request targets or private session data.
        pass


class ConsoleHandler(BaseHTTPRequestHandler):
    server_version = "AM1Console/1"
    sys_version = ""

    def log_message(self, *_):
        pass

    def _headers(self, status: int, mime: str, length: int | None = None, *, cookie: bool = False, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; "
                         "img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        if length is not None:
            self.send_header("Content-Length", str(length))
        if cookie:
            self.send_header("Set-Cookie", f"{COOKIE_NAME}={self.server.cookie_token}; HttpOnly; SameSite=Strict; Path=/")
        for name, value in (extra or {}).items():
            self.send_header(name, str(value))
        self.end_headers()

    def _reply(self, status: int, body=b"", mime="text/plain; charset=utf-8", *, cookie=False):
        self._headers(status, mime, len(body), cookie=cookie)
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, status: int, payload: dict):
        self._reply(status, json.dumps(payload, separators=(",", ":")).encode(), "application/json")

    def _origin(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def _valid_host(self):
        hosts = self.headers.get_all("Host", [])
        return len(hosts) == 1 and hosts[0] == self._origin().removeprefix("http://")

    def _valid_cookie(self):
        supplied = self.headers.get_all("Cookie", [])
        if len(supplied) != 1:
            return False
        parts = [part.strip() for part in supplied[0].split(";")]
        tokens = [part.partition("=")[2] for part in parts if part.partition("=")[0] == COOKIE_NAME]
        return len(tokens) == 1 and hmac.compare_digest(tokens[0], self.server.cookie_token)

    def _base_check(self):
        if not self._valid_host() or self.headers.get("Upgrade") or self.headers.get("Transfer-Encoding"):
            self._reply(403)
            return False
        target = urlsplit(self.path)
        if target.scheme or target.netloc or target.fragment or ".." in target.path.split("/"):
            self._reply(403)
            return False
        return True

    def do_GET(self):
        if not self._base_check():
            return
        target = urlsplit(self.path)
        if target.path == "/" and not target.query:
            page = (Path(__file__).parent / "am1_console_ui" / "index.html").read_bytes()
            page = page.replace(b"AM1_CSRF_TOKEN", self.server.csrf_token.encode())
            self._reply(200, page, "text/html; charset=utf-8", cookie=True)
            return
        if not self._valid_cookie():
            self._reply(403)
            return
        if target.path.startswith("/assets/") and not target.query:
            name = target.path.removeprefix("/assets/")
            if name in CONSOLE_ASSETS:
                body = (Path(__file__).parent / "am1_console_ui" / name).read_bytes()
                mime = "text/css" if name.endswith(".css") else "text/javascript"
                self._reply(200, body, mime)
                return
        if target.path.startswith("/camera/assets/") and not target.query:
            name = target.path.removeprefix("/camera/assets/")
            if name in CAMERA_ASSETS:
                body = (Path(__file__).parent / "am1_camera" / name).read_bytes()
                mime = "text/css" if name.endswith(".css") else "text/javascript"
                self._reply(200, body, mime)
                return
        if target.path == "/api/state" and not target.query:
            self._json(200, self.server.session_adapter.state())
            return
        if target.path == "/api/events" and not target.query:
            self._events()
            return
        if target.path.startswith("/camera/"):
            camera_path = _camera_target(self.path)
            if camera_path is None:
                self._reply(404)
                return
            self._proxy_camera(camera_path)
            return
        self._reply(404)

    def _events(self):
        if not self.server.event_slots.acquire(blocking=False):
            self._reply(503)
            return
        try:
            self._headers(200, "text/event-stream; charset=utf-8", extra={"Connection": "close"})
            cursor = self.server.event_sequence
            self.wfile.write(b": connected\n\n")
            while not self.server.stop_event.is_set():
                with self.server.event_condition:
                    self.server.event_condition.wait_for(
                        lambda: self.server.stop_event.is_set() or self.server.event_sequence > cursor,
                        timeout=10,
                    )
                    batch = [(seq, event) for seq, event in self.server.events if seq > cursor]
                    oldest = self.server.events[0][0] if self.server.events else cursor
                if batch and cursor + 1 < oldest:
                    self.wfile.write(b"event: dropped\ndata: {}\n\n")
                for seq, event in batch:
                    self.wfile.write(f"id: {seq}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n".encode())
                    cursor = seq
                if not batch:
                    self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            pass
        finally:
            self.server.event_slots.release()

    def _proxy_camera(self, camera_path: str):
        streaming = camera_path.startswith("/api/stream.mjpeg")
        if not self.server.camera_slots.acquire(blocking=False):
            self._reply(503)
            return
        if streaming and not self.server.stream_slots.acquire(blocking=False):
            self.server.camera_slots.release()
            self._reply(503)
            return
        connection = None
        try:
            connection = http.client.HTTPConnection(self.server.camera_host, self.server.camera_port, timeout=2)
            connection.request("GET", camera_path, headers={"Authorization": self.server.camera_authorization})
            response = connection.getresponse()
            if response.status != 200:
                self._reply(503)
                return
            if streaming:
                mime = response.getheader("Content-Type", "")
                if not mime.startswith("multipart/x-mixed-replace; boundary=frame"):
                    self._reply(502)
                    return
                self._headers(200, mime, extra={"Connection": "close"})
                while not self.server.stop_event.is_set():
                    # read(32768) waits to fill its buffer; a sparse MJPEG source
                    # must deliver each available part without that delay.
                    block = response.read1(32768)
                    if not block:
                        break
                    self.wfile.write(block)
                    self.wfile.flush()
            else:
                length = response.getheader("Content-Length")
                if length is None or not length.isdecimal() or int(length) > 1_000_000:
                    self._reply(502)
                    return
                body = response.read(int(length))
                if len(body) != int(length):
                    self._reply(502)
                    return
                mime = "application/json" if camera_path == "/status.json" else "image/jpeg"
                extra = {}
                if mime == "image/jpeg":
                    for name in ("X-Frame-Sequence", "X-Frame-Age-Ms"):
                        value = response.getheader(name)
                        if value is not None:
                            extra[name] = value
                self._headers(200, mime, len(body), extra=extra)
                self.wfile.write(body)
        except (OSError, ValueError, http.client.HTTPException, BrokenPipeError, ConnectionResetError):
            try:
                self._reply(503)
            except (OSError, ValueError):
                pass
        finally:
            if connection is not None:
                connection.close()
            if streaming:
                self.server.stream_slots.release()
            self.server.camera_slots.release()

    def do_POST(self):
        if not self._base_check():
            return
        target = urlsplit(self.path)
        if target.query or target.path not in {"/api/operation", "/api/body"}:
            self._reply(404)
            return
        if (
            not self._valid_cookie() or self.headers.get_all("Origin", []) != [self._origin()]
            or self.headers.get_all("X-AM1-CSRF", []) != [self.server.csrf_token]
            or self.headers.get("Content-Type") != "application/json"
        ):
            self._reply(403)
            return
        length = self.headers.get("Content-Length", "")
        if not length.isdecimal() or int(length) > MAX_POST_BYTES:
            # Consume only a small already-sent body before refusing, so the
            # Windows TCP stack does not reset this ordinary 413 response.
            if length.isdecimal() and int(length) <= MAX_POST_BYTES * 2:
                self.rfile.read(int(length))
            self._reply(413)
            return
        try:
            payload = json.loads(self.rfile.read(int(length)))
        except (ValueError, UnicodeDecodeError):
            self._reply(400)
            return
        if not isinstance(payload, dict):
            self._reply(400)
            return
        if target.path == "/api/operation":
            if payload.get("kind") not in ALLOWED_OPERATIONS:
                self._reply(400)
                return
            result = self.server.session_adapter.operation(payload)
        else:
            if not isinstance(payload.get("keys"), list) or any(key not in ALLOWED_BODY_KEYS for key in payload["keys"]):
                self._reply(400)
                return
            result = self.server.session_adapter.body_input(payload)
        self._json(200, result)

    do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = do_CONNECT = do_HEAD = lambda self: self._reply(403)
