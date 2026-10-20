"""One Pi-owned primary encoder and a closed authenticated onboard viewing boundary.

The encoder consumes the camera owner's existing latest JPEG stores. It never
opens a camera. Browser decode progress and source freshness remain independent.
"""

import asyncio
import base64
import ipaddress
import math
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

ROLES = ("forward", "backward", "chest", "wrist_left", "wrist_right")
SDP_LIMIT = 65536


class SharedPrimaryEncoder:
    """Single selected source, single process and at most one in-flight JPEG.

    A slow pipe blocks only this optional feeder. Capture publication and required
    JPEG observation never take this lock or wait for its process. Switching reaps
    that process before creating another; browsers share the same RTSP producer.
    """

    def __init__(self, stores, ffmpeg, *, process_factory=subprocess.Popen):
        self.stores = stores
        self.ffmpeg = str(ffmpeg)
        self.process_factory = process_factory
        self.lock = threading.RLock()
        self.process = self.worker = self.stop = None
        self.role = self.capture_generation = self.encode_generation = None
        self.frames = self.dropped = self.sequence = 0
        self.failure = None
        self.first_failure = None
        self.write_started = None
        self.closed = False

    def command(self):
        return [
            self.ffmpeg,
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
            "-threads",
            "1",
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
            "rtsp://127.0.0.1:8554/am1_primary",
        ]

    def select(self, role):
        if role not in ROLES or role not in self.stores:
            raise ValueError("configured semantic camera role required")
        sample = self.stores[role].metadata_snapshot()
        if sample is None:
            raise ValueError("selected camera source is not fresh")
        generation = sample[1]["capture_generation"]
        with self.lock:
            if self.closed:
                raise ValueError("camera owner has stopped its primary encoder")
            if (
                role == self.role
                and generation == self.capture_generation
                and self.process is not None
                and self.process.poll() is None
                and self.worker.is_alive()
                and self.status()["failure"] is None
            ):
                return self.status()
            self._close()
            self.role, self.capture_generation = role, generation
            self.encode_generation = str(uuid.uuid4())
            self.frames = self.dropped = self.sequence = 0
            self.failure = None
            self.write_started = None
            self.stop = threading.Event()
            self.process = self.process_factory(
                self.command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                bufsize=0,
                **({"creationflags": 0x08000000} if os.name == "nt" else {}),
            )
            self.worker = threading.Thread(
                target=self._feed,
                args=(self.process, self.stop, role, generation),
                name="am1-primary-encoder",
                daemon=True,
            )
            self.worker.start()
            return self.status()

    def _feed(self, process, stop, role, generation):
        last = 0
        next_frame_at = time.monotonic()
        try:
            while not stop.is_set() and process.poll() is None:
                sample = self.stores[role].metadata_snapshot()
                if sample is not None:
                    jpeg, metadata = sample
                    if metadata["capture_generation"] != generation:
                        self.failure = "capture-generation-changed"
                        return
                    sequence = metadata["sequence"]
                    if sequence > last:
                        # No store lock is held across optional encoding or a pipe write.
                        view = memoryview(jpeg)
                        self.write_started = time.monotonic()
                        while view and not stop.is_set():
                            written = process.stdin.write(view)
                            if not written:
                                raise BrokenPipeError()
                            view = view[written:]
                        if stop.is_set():
                            return
                        self.dropped += max(0, sequence - last - 1) if last else 0
                        last = self.sequence = sequence
                        self.frames += 1
                        self.write_started = None
                next_frame_at += 0.1
                now = time.monotonic()
                if next_frame_at < now:
                    # Skip missed slots; slow writes must not trigger catch-up bursts.
                    next_frame_at += (math.floor((now - next_frame_at) / 0.1) + 1) * 0.1
                stop.wait(max(0.0, next_frame_at - now))
        except (OSError, ValueError):
            if not stop.is_set():
                self.failure = "encoder-pipe-unavailable"

    def status(self):
        sample = self.stores[self.role].metadata_snapshot() if self.role else None
        current = sample is not None and sample[1]["capture_generation"] == self.capture_generation
        running = self.process is not None and self.process.poll() is None
        failure = self.failure
        if self.write_started is not None and time.monotonic() - self.write_started > 0.2:
            failure = "encoder-write-stall"
        if failure and self.first_failure is None:
            self.first_failure = {
                "cause": failure,
                "role": self.role,
                "encode_generation": self.encode_generation,
            }
        return {
            "enabled": True,
            "transport": "webrtc",
            "role": self.role,
            "encode_generation": self.encode_generation,
            "capture_generation": self.capture_generation,
            "state": "running" if running and not failure else "unavailable",
            "source_current": current,
            "frames_submitted": self.frames,
            "source_sequence": self.sequence,
            "dropped_frames": self.dropped,
            "failure": failure,
            "first_failure": self.first_failure,
            "width": 640,
            "height": 480,
            "fps_limit": 10,
            "codec": "H264 baseline",
            "shared_primary": True,
        }

    def _close(self):
        if self.stop:
            self.stop.set()
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
            # Terminating the child first releases even a blocked pipe writer.
            if self.worker:
                self.worker.join(timeout=1)
                if self.worker.is_alive():
                    raise RuntimeError("primary encoder feeder did not stop")
            if self.process.stdin:
                self.process.stdin.close()
        self.process = self.worker = self.stop = None

    def close(self):
        with self.lock:
            self.closed = True
            self._close()


class OnboardMediaProxy:
    """Fixed same-Pi camera owner routes; no caller-supplied URL or device open."""

    def __init__(self, config, ipc, *, clock=time.monotonic):
        from aiohttp import web

        from tools.am1_camera_viewer import load_private, validate_credentials

        if not isinstance(config, dict) or set(config) != {"url", "credentials"}:
            raise ValueError("onboard camera requires existing url and credentials path only")
        endpoint = urlsplit(config["url"])
        try:
            address = ipaddress.IPv4Address(endpoint.hostname)
        except (ValueError, TypeError) as exc:
            raise ValueError("explicit private Pi camera owner IPv4 required") from exc
        if (
            endpoint.scheme != "http"
            or not address.is_private
            or address.is_unspecified
            or address.is_multicast
            or not endpoint.port
            or not 1024 <= endpoint.port <= 65535
            or endpoint.username
            or endpoint.password
            or endpoint.path not in ("", "/")
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("one exact existing private camera owner origin required")
        credentials = validate_credentials(load_private(Path(config["credentials"])))
        self.authorization = (
            "Basic "
            + base64.b64encode((credentials["username"] + ":" + credentials["password"]).encode()).decode()
        )
        self.url = config["url"].rstrip("/")
        self.host = endpoint.hostname
        self.ipc, self.clock, self.web = ipc, clock, web
        self.session = None
        self.lane = asyncio.Semaphore(4)
        self.streams = asyncio.Semaphore(2)
        self.offer_lock = asyncio.Lock()
        self.viewers = {}

    def validate_origin(self, origin):
        if self.host not in ("127.0.0.1", urlsplit(origin).hostname):
            raise ValueError("onboard camera must be on the same Pi as the trusted gateway")

    async def start(self):
        from aiohttp import ClientSession, ClientTimeout

        self.session = ClientSession(
            timeout=ClientTimeout(total=4), headers={"Authorization": self.authorization}
        )

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

    def role(self, role):
        if role not in ROLES:
            raise self.web.HTTPBadRequest(reason="configured semantic camera role required")
        return role

    async def upstream(self, method, path, body=None, *, limit=16384, mime=None):
        from aiohttp import ClientError

        try:
            async with asyncio.timeout(4):
                async with self.lane:
                    async with self.session.request(
                        method,
                        self.url + path,
                        data=body,
                        headers={"Content-Type": mime} if mime else {},
                        allow_redirects=False,
                    ) as response:
                        chunks, size = [], 0
                        async for chunk in response.content.iter_chunked(8192):
                            size += len(chunk)
                            if size > limit:
                                raise self.web.HTTPBadGateway(
                                    reason="bounded onboard media response exceeded"
                                )
                            chunks.append(chunk)
                        if response.status not in (200, 201):
                            raise self.web.HTTPServiceUnavailable(reason="onboard view unavailable")
                        return response.status, b"".join(chunks), dict(response.headers)
        except (ClientError, OSError, TimeoutError):
            raise self.web.HTTPServiceUnavailable(reason="onboard camera owner unavailable") from None

    async def status(self):
        import json

        _, data, _ = await self.upstream("GET", "/status.json")
        try:
            result = json.loads(data)
            if not isinstance(result, dict) or not isinstance(result.get("cameras"), dict):
                raise ValueError()
            return result
        except (ValueError, UnicodeError):
            raise self.web.HTTPBadGateway(reason="invalid onboard camera status") from None

    async def snapshot(self, role):
        return await self.upstream("GET", "/api/frame.jpeg?src=" + self.role(role), limit=1_000_000)

    async def mjpeg(self, role):
        """Explicit prior viewer only; preserve multipart ages, bound slow consumers."""
        from aiohttp import ClientError, ClientTimeout

        role = self.role(role)
        if self.streams.locked():
            raise self.web.HTTPTooManyRequests(reason="two explicit JPEG rollback viewers maximum")
        await self.streams.acquire()
        try:
            async with self.session.get(
                self.url + "/api/stream.mjpeg?src=" + role,
                timeout=ClientTimeout(total=60, sock_read=1),
                allow_redirects=False,
            ) as response:
                if (
                    response.status != 200
                    or response.headers.get("Content-Type") != "multipart/x-mixed-replace; boundary=frame"
                ):
                    raise self.web.HTTPServiceUnavailable(reason="prior JPEG view unavailable")
                async for chunk in response.content.iter_chunked(65536):
                    yield chunk
        except (ClientError, OSError, TimeoutError):
            raise self.web.HTTPServiceUnavailable(reason="prior JPEG view ended or stalled") from None
        finally:
            self.streams.release()

    async def owner_progress(self, report, role, frames, media_time, receipt, *, qualified, delivery=None):
        if delivery is not None:
            delivery["owner_snapshot_started_at_monotonic_s"] = self.clock()
        result = await asyncio.wait_for(self.ipc.request({"op": "snapshot"}, "gateway-view"), 1)
        if delivery is not None:
            delivery["owner_snapshot_completed_at_monotonic_s"] = self.clock()
        run = result.get("snapshot", {}).get("run") or {}
        if run.get("recipe") != "physical-live-teleop":
            return {"accepted": False, "reason": "no required interactive view"}
        if delivery is not None:
            delivery["run_id"] = run["run_id"]
        source, video = report.get("cameras", {}).get(role, {}), report.get("video", {})
        arrived = source.get("arrived_at_monotonic_s")
        original = (
            receipt
            if frames == 0 or not qualified
            else min(receipt, arrived)
            if type(arrived) in (int, float) and math.isfinite(arrived)
            else receipt - 1
        )
        command = {
            "op": "view_progress",
            "run_id": run["run_id"],
            "role": role,
            "owner_generation": str(report.get("owner_generation") or "unavailable"),
            "capture_generation": str(source.get("capture_generation") or "unavailable"),
            "encode_generation": str(video.get("encode_generation") or "unavailable"),
            "decoded_frames": frames,
            "media_time": media_time,
            "source_sequence": source.get("sequence", 0),
            "source_fresh": bool(qualified),
            # Same-Pi monotonic floor retains both original source and decode receipt expiry.
            "received_at_monotonic_s": original,
        }
        if delivery is not None:
            delivery["original_floor_at_monotonic_s"] = original
            delivery["owner_progress_started_at_monotonic_s"] = self.clock()
        result = await asyncio.wait_for(self.ipc.request(command, "gateway-view"), 1)
        if delivery is not None:
            delivery["owner_progress_completed_at_monotonic_s"] = self.clock()
            result = dict(result)
            delivery["core"] = result.pop("view_timing", None)
            delivery["owner_result"] = {key: result[key] for key in
                                       ("accepted", "status", "reason", "required_view") if key in result}
        return result

    async def offer(self, role, sdp, device):
        self.role(role)
        if len(sdp) > SDP_LIMIT:
            raise self.web.HTTPRequestEntityTooLarge(max_size=SDP_LIMIT, actual_size=len(sdp))
        if not sdp.startswith(b"v=0") or not sdp:
            raise self.web.HTTPBadRequest(reason="SDP offer required")
        async with self.offer_lock:
            now = self.clock()
            self.viewers = {key: value for key, value in self.viewers.items() if now - value["at"] < 2}
            old = self.viewers.get(device)
            if old and now - old.get("offered_at", 0) < 1:
                raise self.web.HTTPTooManyRequests(reason="bounded view retry delay")
            if device not in self.viewers and len(self.viewers) >= 2:
                raise self.web.HTTPTooManyRequests(reason="two shared primary viewers maximum")
            # Retire the previous browser attachment before status/IPC/upstream
            # work can yield. A failed offer must not restore its old authority.
            viewer = {"at": now, "offered_at": now, "frames": 0, "media_time": -1,
                      "role": role, "view_generation": str(uuid.uuid4()), "ready": False}
            self.viewers[device] = viewer
            report = await self.status()
            await self.owner_progress(report, role, 0, 0, now, qualified=False)
            response = await self.upstream(
                "POST", "/api/webrtc/" + role, sdp, limit=SDP_LIMIT, mime="application/sdp"
            )
            if (
                response[0] != 201
                or response[2].get("Content-Type", "").split(";")[0] != "application/sdp"
                or not response[1].startswith(b"v=0")
            ):
                raise self.web.HTTPBadGateway(reason="invalid onboard SDP answer")
            viewer.update(at=self.clock(), ready=True)
            return response[0], response[1], {**response[2], "X-Camera-View-Generation": viewer["view_generation"]}

    async def progress(self, device, body, received_at):
        keys = {
            "role",
            "owner_generation",
            "capture_generation",
            "encode_generation",
            "view_generation",
            "decoded_frames",
            "media_time",
        }
        if not isinstance(body, dict) or set(body) != keys:
            raise self.web.HTTPBadRequest(reason="closed decoded view progress required")
        role = self.role(body["role"])
        frames, media_time = body["decoded_frames"], body["media_time"]
        if (
            type(frames) is not int
            or not 0 <= frames < 2**53
            or type(media_time) not in (int, float)
            or not math.isfinite(media_time)
            or media_time < 0
            or any(
                not isinstance(body[key], str) or not 1 <= len(body[key]) <= 128
                for key in ("owner_generation", "capture_generation", "encode_generation", "view_generation")
            )
        ):
            raise self.web.HTTPBadRequest(reason="bounded decoded view progress required")
        delivery = {
            "version": 1, "role": role, "run_id": None,
            "decoded_frames": frames, "media_time": media_time, "source_sequence": None,
            "request_received_at_monotonic_s": received_at,
            "request_body_completed_at_monotonic_s": None,
            "status_started_at_monotonic_s": None, "status_completed_at_monotonic_s": None,
            "source_arrived_at_monotonic_s": None, "original_floor_at_monotonic_s": None,
            "owner_snapshot_started_at_monotonic_s": None, "owner_snapshot_completed_at_monotonic_s": None,
            "owner_progress_started_at_monotonic_s": None, "owner_progress_completed_at_monotonic_s": None,
            "owner_enqueued_at_monotonic_s": None,  # Not observed by this API.
            "core": None, "owner_result": None, "http_response_ready_at_monotonic_s": None,
        }
        def receipt(result):
            return {**result, "view_delivery": delivery}

        viewer = self.viewers.get(device)
        if (viewer is None or viewer.get("ready") is not True or viewer.get("role") != role
                or viewer.get("view_generation") != body["view_generation"]):
            return receipt({"accepted": False, "reason": "current view attachment required"})
        delivery["status_started_at_monotonic_s"] = self.clock()
        report = await self.status()
        delivery["status_completed_at_monotonic_s"] = self.clock()
        if (self.viewers.get(device) is not viewer or viewer.get("ready") is not True
                or viewer.get("role") != role or viewer.get("view_generation") != body["view_generation"]):
            return receipt({"accepted": False, "reason": "current view attachment required"})
        source, video = report.get("cameras", {}).get(role, {}), report.get("video", {})
        arrived = source.get("arrived_at_monotonic_s")
        delivery["source_arrived_at_monotonic_s"] = (
            arrived if type(arrived) in (int, float) and math.isfinite(arrived) else None)
        delivery["source_sequence"] = source.get("sequence")
        identities = (
            body["owner_generation"] == report.get("owner_generation")
            and body["capture_generation"] == source.get("capture_generation")
            and body["encode_generation"] == video.get("encode_generation")
            and video.get("capture_generation") == source.get("capture_generation")
            and video.get("role") == role
        )
        if not identities:
            return receipt({"accepted": False, "reason": "view source generation changed"})
        advanced = frames > viewer["frames"] and media_time > viewer["media_time"]
        if not advanced:
            return receipt({"accepted": False, "reason": "decoded video has not advanced"})
        viewer.update(frames=frames, media_time=media_time, at=self.clock())
        arrived = source.get("arrived_at_monotonic_s")
        fresh = (
            source.get("state") == "fresh"
            and source.get("configured") is True
            and video.get("state") == "running"
            and type(arrived) in (int, float)
            and math.isfinite(arrived)
            and 0 <= self.clock() - arrived < 0.5
        )
        result = await self.owner_progress(report, role, frames, media_time, received_at, qualified=fresh,
                                           delivery=delivery)
        return receipt(result if fresh else {"accepted": False, "reason": "selected source is not fresh"})
