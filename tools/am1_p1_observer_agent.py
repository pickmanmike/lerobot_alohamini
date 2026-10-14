"""Finite P1 user-session capture adapter. No remote shell, device selector or arbitrary arguments API."""

import argparse
import asyncio
import base64
import contextlib
import os
import re
import secrets
import ssl
import time
from pathlib import Path
from urllib.parse import urlsplit

from tools.am1_observation import BINDING_KEYS, SOURCE_KEYS, source_identity
from tools.am1_session_core import OwnerLock
from tools.am1_session_ipc import private_directory, private_json, strict_json

REQUEST_KEYS = {*BINDING_KEYS, "active", "duration_seconds", "max_recording_bytes"}


def validate_request(value):
    if not isinstance(value, dict) or set(value) != REQUEST_KEYS:
        raise ValueError("finite source request schema")
    import uuid

    uuid.UUID(value["run_id"])
    uuid.UUID(value["generation"])
    source_identity({k: value[k] for k in SOURCE_KEYS})
    if (
        type(value["active"]) is not bool
        or type(value["duration_seconds"]) is not int
        or not 20 <= value["duration_seconds"] <= 660
    ):
        raise ValueError("finite capture duration")
    if (
        type(value["max_recording_bytes"]) is not int
        or not 1048576 <= value["max_recording_bytes"] <= 140 * 1024 * 1024
    ):
        raise ValueError("finite capture storage cap")
    return {k: v for k, v in value.items() if k != "active"}


class CaptureLedger:
    """Durable dedup and one source reservation; reconnect cannot renew the original budget."""

    def __init__(self, directory, clock=time.monotonic):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "capture-ledger.json"
        self.clock = clock
        if self.path.exists() and self.path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("source ledger byte cap")
        self.rows = strict_json(self.path.read_bytes()) if self.path.exists() else []
        self.current = self.rows[-1] if self.rows else None

    def start(self, request):
        canonical = validate_request(request)
        for row in self.rows:
            if (
                row["request"]["run_id"] == canonical["run_id"]
                or row["request"]["generation"] == canonical["generation"]
            ):
                if row["request"] != canonical:
                    raise ValueError("source identity/budget conflict")
                return row
        if self.current and not (self.current.get("terminal") or {}).get("camera_released"):
            raise ValueError("previous source has not confirmed release")
        if len(self.rows) >= 128:
            raise ValueError("source ledger retention cap; private maintenance required")
        self.current = {
            "request": canonical,
            "started_at": self.clock(),
            "deadline": self.clock() + canonical["duration_seconds"],
            "terminal": None,
        }
        self.rows.append(self.current)
        self.save()
        return self.current

    def save(self):
        private_json(self.path, self.rows, limit=2 * 1024 * 1024)

    def terminal(self, value):
        if self.current:
            # Full diagnostics remain in the original capture metadata/output. Keep status and
            # dedup history bounded even when 128 source phase records accompanied the result.
            fields = (
                "event",
                "generation",
                "success",
                "camera_released",
                "clip_bytes",
                "clip_sha256",
                "stop_reason",
                "record_hold_seconds",
                "frames_delivered",
                "observed_source_frames",
                "recording_started_utc",
                "recording_stopped_utc",
                "error",
                "error_type",
            )
            self.current["terminal"] = {
                key: value[key][:512] if isinstance(value[key], str) else value[key]
                for key in fields
                if key in value
            }
            self.save()


class LatestEncoder:
    """Optional single-slot JPEG encoder; failure never awaits or invalidates required proof."""

    def __init__(self, config):
        self.config = config
        self.queue = asyncio.Queue(maxsize=1)
        self.process = None
        self.task = None
        self.frames = 0
        self.dropped = 0
        self.failure = None
        endpoint = urlsplit(config["rtsp_url"])
        if (
            endpoint.scheme != "rtsp"
            or endpoint.hostname != "127.0.0.1"
            or not endpoint.username
            or not endpoint.password
            or endpoint.path != "/p1-observer"
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("private authenticated local named RTSP endpoint required")

    def offer(self, jpeg):
        if self.queue.full():
            self.queue.get_nowait()
            self.dropped += 1
        self.queue.put_nowait(jpeg)

    def command(self):
        return [
            self.config["ffmpeg"],
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-probesize",
            "32768",
            "-analyzeduration",
            "100000",
            "-f",
            "image2pipe",
            "-framerate",
            "10",
            "-vcodec",
            "mjpeg",
            "-i",
            "pipe:0",
            "-an",
            "-vf",
            "scale=640:360,fps=10",
            "-c:v",
            "libx264",
            "-profile:v",
            "baseline",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-bf",
            "0",
            "-g",
            "10",
            "-keyint_min",
            "10",
            "-b:v",
            "600k",
            "-maxrate",
            "600k",
            "-bufsize",
            "600k",
            "-f",
            "rtsp",
            "-rtsp_transport",
            "tcp",
            self.config["rtsp_url"],
        ]

    async def run(self):
        try:
            while True:
                try:
                    if self.process is None or self.process.returncode is not None:
                        self.process = await asyncio.create_subprocess_exec(
                            *self.command(),
                            stdin=asyncio.subprocess.PIPE,
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                            **({"creationflags": 0x08000000} if os.name == "nt" else {}),
                        )
                    jpeg = await self.queue.get()
                    self.process.stdin.write(jpeg)
                    await asyncio.wait_for(self.process.stdin.drain(), 0.2)
                    self.frames += 1
                    self.failure = None
                except (OSError, TimeoutError, ConnectionError):
                    self.failure = "optional encoder unavailable"
                    await self.stop_process()
                    await asyncio.sleep(1)
        finally:
            await self.stop_process()

    async def stop_process(self):
        if self.process and self.process.returncode is None:
            self.process.terminate()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.process.wait(), 2)
            if self.process.returncode is None:
                self.process.kill()
                await self.process.wait()
        self.process = None

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None


class P1Agent:
    def __init__(self, config):
        self.config = config
        self.identity = source_identity(config["source"])
        if (
            os.name != "nt"
            or os.environ.get("COMPUTERNAME", "").casefold() != self.identity["machine"].casefold()
        ):
            raise ValueError("P1 capture requires exact configured Windows machine/user context")
        if not re.fullmatch("[a-f0-9]{64}", config["token"]):
            raise ValueError("private machine token required")
        self.directory = private_directory(config["state"])
        self.lock = OwnerLock(self.directory / "source.lock")
        self.ledger = CaptureLedger(self.directory)
        if self.ledger.current and self.ledger.current["terminal"] is None:
            # An old independently finite child may have finalized after its agent exited.
            generation = self.ledger.current["request"]["generation"]
            metadata = self.directory / generation / "capture-metadata.json"
            if metadata.exists():
                result = strict_json(metadata.read_text(encoding="utf-8-sig"))
                if result.get("generation") == generation and result.get("camera_released") is True:
                    self.ledger.terminal(result)
        self.process = None
        self.bridge = None
        self.capture_task = None
        self.peer = None
        self.latest = asyncio.Queue(maxsize=1)
        self.encoder = None
        if config.get("media"):
            self.encoder = LatestEncoder(config["media"])
        self.capture_token = None
        self.actual_source = None

    def status(self):
        row = self.ledger.current
        return {
            "event": "status",
            "source": self.identity,
            "generation": row["request"]["generation"] if row else None,
            "run_id": row["request"]["run_id"] if row else None,
            "terminal": row["terminal"] if row else None,
            "active": bool(self.process and self.process.returncode is None),
            "media": {
                "optional": True,
                "frames": self.encoder.frames,
                "dropped": self.encoder.dropped,
                "failure": self.encoder.failure,
            }
            if self.encoder
            else {"optional": True, "enabled": False},
        }

    async def start_capture(self, request):
        if request.get("active") is not True:
            raise ValueError("active finite request required")
        if any(request.get(k) != self.identity[k] for k in SOURCE_KEYS):
            raise ValueError("exact source binding required")
        previous = len(self.ledger.rows)
        row = self.ledger.start(request)
        if len(self.ledger.rows) == previous:
            return  # Includes terminal and uncertain-after-agent-restart; never silently reopen.
        generation = row["request"]["generation"]
        directory = self.directory / generation
        directory.mkdir()
        while not self.latest.empty():
            self.latest.get_nowait()
        self.capture_token = secrets.token_hex(32)
        capture = {
            "expected_host": self.identity["machine"],
            "video_device_id": self.identity["device"],
            "generation": generation,
            "duration_seconds": request["duration_seconds"],
            "max_recording_bytes": request["max_recording_bytes"],
            "output_dir": str(directory),
            "capture_port": self.config["capture_port"],
            "token": self.capture_token,
            "delivery_jpeg_quality_percent": 15,
        }
        private_json(directory / "capture.private.json", capture)
        script = Path(__file__).with_name("am1_observer_capture.ps1")
        self.process = await asyncio.create_subprocess_exec(
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(script),
            "-ConfigPath",
            str(directory / "capture.private.json"),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=0x08000000,
            limit=1024 * 1024,
        )
        self.capture_task = asyncio.create_task(self.supervise(row, directory))
        if self.encoder:
            self.encoder.task = asyncio.create_task(self.encoder.run())

    async def supervise(self, row, directory):
        terminal = None

        async def stdout():
            nonlocal terminal
            retained = 0
            with (directory / "capture-output.log").open("wb") as log:
                while line := await self.process.stdout.readline():
                    if retained + len(line) <= 512 * 1024:
                        log.write(line)
                        log.flush()
                        retained += len(line)
                    try:
                        event = strict_json(line.decode("utf-8-sig"))
                        if event.get("event") in ("complete", "failed"):
                            terminal = event
                    except (ValueError, UnicodeError):
                        pass

        output = asyncio.create_task(stdout())

        async def finite_deadline():
            while self.process.returncode is None:
                if self.ledger.clock() >= row["deadline"]:
                    with contextlib.suppress(OSError, TimeoutError):
                        await self.local_command("stop")
                await asyncio.sleep(0.1)

        deadline_task = asyncio.create_task(finite_deadline())
        try:
            while self.process.returncode is None:
                try:
                    reader, writer = await asyncio.wait_for(
                        asyncio.open_connection("127.0.0.1", self.config["capture_port"], limit=1024 * 1024),
                        0.5,
                    )
                    self.bridge = writer
                    while line := await reader.readline():
                        value = strict_json(line)
                        if (
                            value.get("event") != "frame"
                            or value.get("generation") != row["request"]["generation"]
                        ):
                            continue
                        value["binding"] = {k: row["request"][k] for k in BINDING_KEYS}
                        if self.latest.full():
                            self.latest.get_nowait()
                        self.latest.put_nowait(value)
                        if self.encoder:
                            pixels = base64.b64decode(value["jpeg_base64"], validate=True)
                            if 0 < len(pixels) <= 512 * 1024:
                                self.encoder.offer(pixels)
                except (OSError, ValueError, TimeoutError):
                    await asyncio.sleep(0.1)
                finally:
                    if self.bridge:
                        self.bridge.close()
                        self.bridge = None
            await self.process.wait()
            await output
        finally:
            deadline_task.cancel()
            await asyncio.gather(deadline_task, return_exceptions=True)
            if self.encoder:
                await self.encoder.close()
            self.ledger.terminal(
                terminal or {"event": "capture_process_lost", "success": False, "camera_released": False}
            )
            private_json(directory / "source-result.json", self.status())

    async def local_command(self, event, nonce=None):
        if not self.bridge:
            return
        command = {
            "event": event,
            "token": self.capture_token,
            "generation": self.ledger.current["request"]["generation"],
        }
        if nonce:
            command["nonce"] = nonce
        self.bridge.write((__import__("json").dumps(command, separators=(",", ":")) + "\n").encode())
        await asyncio.wait_for(self.bridge.drain(), 0.15)

    async def websocket(self, request):
        from aiohttp import WSMsgType, web

        if request.query_string or not secrets.compare_digest(
            request.headers.get("Authorization", ""), "Bearer " + self.config["token"]
        ):
            raise web.HTTPUnauthorized()
        if self.peer is not None:
            raise web.HTTPConflict(reason="one machine observation reader")
        ws = web.WebSocketResponse(max_msg_size=8192, heartbeat=5)
        self.peer = ws
        try:
            await ws.prepare(request)

            async def sender():
                while True:
                    value = await self.latest.get()
                    await asyncio.wait_for(ws.send_json(value), 0.25)

            sending = asyncio.create_task(sender())
            try:
                async for message in ws:
                    if message.type != WSMsgType.TEXT:
                        break
                    value = strict_json(message.data)
                    op = value.get("op")
                    if op == "start" and set(value) == {"op", "request"}:
                        await self.start_capture(value["request"])
                        await ws.send_json(self.status())
                    elif op == "stop" and set(value) == {"op", "request"}:
                        canonical = validate_request(value["request"])
                        if self.ledger.current and canonical == self.ledger.current["request"]:
                            await self.local_command("stop")
                        await ws.send_json(self.status())
                    elif op == "status" and set(value) == {"op"}:
                        await ws.send_json(self.status())
                    elif op == "challenge" and set(value) == {"op", "generation", "nonce"}:
                        if not re.fullmatch("[a-zA-Z0-9_-]{1,80}", value["nonce"]):
                            raise ValueError("nonce")
                        if (
                            self.ledger.current
                            and value["generation"] == self.ledger.current["request"]["generation"]
                        ):
                            await self.local_command("frame", value["nonce"])
                    else:
                        raise ValueError("allowlisted source operation required")
                    if sending.done():
                        sending.result()
            finally:
                sending.cancel()
                await asyncio.gather(sending, return_exceptions=True)
        except (ValueError, KeyError, TypeError, TimeoutError, ConnectionError):
            await ws.close(code=1008)
        finally:
            self.peer = None
        return ws

    async def close(self):
        # A normal service stop requests graceful recording finalization; no forced capture kill.
        if self.process and self.process.returncode is None:
            await self.local_command("stop")
            if self.capture_task:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(self.capture_task), 35)
        if self.encoder:
            await self.encoder.close()
        self.lock.close()


async def run(config):
    from aiohttp import web

    from tools.am1_session_service import validate_listener

    validate_listener(config["host"], config["port"], config["origin"])
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(config["cert"], config["key"])
    agent = P1Agent(config)
    app = web.Application(client_max_size=8192)
    app.router.add_get("/observer", agent.websocket)
    runner = web.AppRunner(app, access_log=None, shutdown_timeout=1)
    await runner.setup()
    await web.TCPSite(runner, config["host"], config["port"], ssl_context=tls).start()
    private_json(
        agent.directory / "ready.json", {"pid": os.getpid(), "source": agent.identity, "active": False}
    )
    try:
        await asyncio.Event().wait()
    finally:
        await agent.close()
        await runner.cleanup()


def main():
    parser = argparse.ArgumentParser(
        description="Independent bounded P1 capture service; start explicitly in camera-capable user session"
    )
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    asyncio.run(run(strict_json(Path(args.config).read_bytes())))


if __name__ == "__main__":
    main()
