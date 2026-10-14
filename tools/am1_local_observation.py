"""Robot-local camera evidence. The owner mailbox has no network, decoder or file IO."""

import argparse
import asyncio
import hashlib
import io
import ipaddress
import json
import math
import re
import socket
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from tools.am1_observation import SOURCE_KEYS, source_identity

LOCAL_SOURCE_KEYS = (*SOURCE_KEYS, "role")
LOCAL_BINDING_KEYS = ("run_id", "generation", *LOCAL_SOURCE_KEYS)
TIMING_BASIS = "pi-upstream-arrival-monotonic"
PROVENANCE = "real-local-camera/pi-decoded-arrival"
MAX_JPEG = 1_000_000


def local_source_identity(value):
    if not isinstance(value, dict) or set(value) != set(LOCAL_SOURCE_KEYS):
        raise ValueError("exact configured local camera identity required")
    identity = source_identity({k: value[k] for k in SOURCE_KEYS})
    role = value["role"]
    if role not in ("forward", "backward", "chest", "wrist_left", "wrist_right"):
        raise ValueError("configured semantic camera role required")
    device = identity["device"]
    if device != f"/dev/am_camera_{role}" and not re.fullmatch(
        r"/dev/v4l/by-path/[A-Za-z0-9_.:-]+-video-index0", device
    ):
        raise ValueError("existing role camera identity required")
    return dict(identity, role=role)


def finite_time(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


class LocalObservationMailbox:
    """Latest decoded evidence, retaining upstream arrival through actual consumption."""

    def __init__(self, source, clock=time.monotonic):
        self.source = local_source_identity(source)
        self.clock = clock
        self.request = self.record = None
        self.reason = "no current decoded local image"
        self.current_source_generation = None
        self.retired_generations = set()
        self.last_sequence = 0

    def begin(self, run_id, live_s):
        uuid.UUID(run_id)
        self.request = dict(
            self.source,
            run_id=run_id,
            generation=str(uuid.uuid4()),
            active=True,
            started_at=self.clock(),
            timing_basis=TIMING_BASIS,
        )
        self.record = None
        self.last_sequence = 0
        self.current_source_generation = None
        self.reason = "no current decoded local image"

    def stop(self):
        if self.request:
            self.request = dict(self.request, active=False)
        self.record = None

    def publish(self, record):
        # A delayed optional/P1/previous-run delivery has no authority over this run.
        if not self.request or not self.request["active"] or not isinstance(record, dict):
            return
        if any(record.get(k) != self.request[k] for k in LOCAL_BINDING_KEYS):
            return
        self.record = None
        self.reason = str(record.get("reason", "invalid or unqualified local evidence"))[:160]
        try:
            source_generation = (
                str(uuid.UUID(record["owner_generation"])),
                str(uuid.UUID(record["capture_generation"])),
            )
        except (KeyError, ValueError, TypeError, AttributeError):
            return
        if source_generation in self.retired_generations:
            self.reason = "retired camera generation replay"
            return
        if record.get("timing_basis") != TIMING_BASIS:
            return
        if not all(finite_time(record.get(k)) for k in ("arrived_at", "decoded_at")):
            return
        if not self.request["started_at"] <= record["arrived_at"] <= record["decoded_at"] <= self.clock():
            return
        if self.clock() - record["arrived_at"] > 0.5:
            self.reason = "original local arrival expired at owner consumption"
            return
        sequence = record.get("sequence")
        if type(sequence) is not int or sequence <= 0:
            return
        if self.current_source_generation != source_generation:
            if len(self.retired_generations) >= 64:
                self.reason = "camera generation history limit"
                return
            if self.current_source_generation is not None:
                self.retired_generations.add(self.current_source_generation)
            self.current_source_generation = source_generation
            self.last_sequence = 0
        if record.get("qualified") is not True:
            self.reason = str(record.get("reason", self.reason))[:160]
            return
        if sequence <= self.last_sequence or not re.fullmatch(
            "[a-f0-9]{64}", str(record.get("frame_sha256", ""))
        ):
            return
        self.last_sequence = sequence
        self.record = dict(record)
        self.reason = None

    def evidence(self):
        record = self.record
        age = self.clock() - record["arrived_at"] if record else None
        qualified = record is not None and 0 <= age <= 0.5
        return {
            "qualified": qualified,
            "age_s": age,
            "reason": self.reason
            if record is None
            else (None if qualified else "original local arrival expired at owner consumption"),
            "sequence": record["sequence"] if record else None,
            "generation": self.request["generation"] if self.request else None,
            "owner_generation": record["owner_generation"] if record else None,
            "capture_generation": record["capture_generation"] if record else None,
            "arrived_at": record["arrived_at"] if record else None,
            "decoded_at": record["decoded_at"] if record else None,
            "frame_sha256": record["frame_sha256"] if record else None,
            "role": self.source["role"],
            "timing_basis": TIMING_BASIS,
        }


class LocalReceiver:
    """Bounded single JPEG slot; real decode is run off the owner thread."""

    def __init__(self, directory, binding, clock=time.monotonic):
        from tools.am1_session_ipc import private_directory

        self.directory = private_directory(directory)
        self.binding = {k: binding[k] for k in LOCAL_BINDING_KEYS}
        self.started_at = binding["started_at"]
        self.clock = clock
        self.current_generation = None
        self.retired_generations = set()
        self.last_sequence = 0
        self.record = None
        self.arrivals = []
        self.reason = "no current decoded local image"

    def unqualify(self, reason):
        self.record = None
        self.arrivals.clear()
        self.reason = reason

    def accept(self, jpeg, headers):
        from PIL import Image

        try:
            for key, header in (
                ("machine", "X-Camera-Machine"),
                ("device", "X-Camera-Device"),
                ("role", "X-Camera-Role"),
            ):
                if headers.get(header) != self.binding[key]:
                    raise ValueError("exact local camera identity mismatch")
            if headers.get("X-Frame-Timing") != TIMING_BASIS:
                raise ValueError("local original-arrival timing required")
            generation = (
                str(uuid.UUID(headers["X-Camera-Owner-Generation"])),
                str(uuid.UUID(headers["X-Camera-Capture-Generation"])),
            )
            if generation in self.retired_generations:
                raise ValueError("retired camera generation replay")
            arrived = float(headers["X-Frame-Arrived-Monotonic-S"])
            sequence = int(headers["X-Frame-Sequence"])
            if (
                not finite_time(arrived)
                or not self.started_at <= arrived <= self.clock()
                or self.clock() - arrived > 0.5
            ):
                raise ValueError("original local arrival expired")
            if generation != self.current_generation:
                if len(self.retired_generations) >= 64:
                    raise ValueError("camera generation history limit")
                if self.current_generation:
                    self.retired_generations.add(self.current_generation)
                self.current_generation = generation
                self.last_sequence = 0
                self.unqualify("camera generation requires current qualification")
            if (
                self.record
                and generation == self.current_generation
                and sequence == self.last_sequence
                and arrived == self.record["arrived_at"]
                and isinstance(jpeg, bytes)
                and len(jpeg) <= MAX_JPEG
                and hashlib.sha256(jpeg).hexdigest() == self.record["frame_sha256"]
            ):
                # A getter can return the same latest frame. Keep its original age
                # and qualification; no decode/arrival count/freshness renewal.
                return True
            if sequence <= self.last_sequence or (self.record and arrived <= self.record["arrived_at"]):
                raise ValueError("local camera sequence replay")
            if not isinstance(jpeg, bytes) or not 4 <= len(jpeg) <= MAX_JPEG:
                raise ValueError("bounded actual JPEG required")
            with Image.open(io.BytesIO(jpeg)) as image:
                if image.format != "JPEG" or image.size != (640, 480):
                    raise ValueError("configured native JPEG dimensions required")
                image.load()
            decoded = self.clock()
            if not arrived <= decoded or decoded - arrived > 0.5:
                raise ValueError("original local arrival expired during decode")
            from tools.am1_session_ipc import private_json

            # Atomic bounded latest pixels; no recording queue and no getter-time freshness.
            temporary = self.directory / "latest.tmp"
            temporary.write_bytes(jpeg)
            temporary.chmod(0o600)
            temporary.replace(self.directory / "latest.jpeg")
            self.last_sequence = sequence
            if self.arrivals and arrived - self.arrivals[-1] > 0.5:
                self.arrivals.clear()
            self.arrivals.append(arrived)
            self.arrivals = self.arrivals[-5:]
            self.record = dict(
                self.binding,
                owner_generation=generation[0],
                capture_generation=generation[1],
                arrived_at=arrived,
                decoded_at=decoded,
                sequence=sequence,
                timing_basis=TIMING_BASIS,
                frame_sha256=hashlib.sha256(jpeg).hexdigest(),
            )
            self.reason = None
            private_json(self.directory / "latest.json", self.record)
            return True
        except (ValueError, KeyError, TypeError, OSError, Image.DecompressionBombError) as exc:
            self.unqualify(str(exc)[:160])
            return False

    def evidence(self):
        record = self.record
        fresh = record is not None and 0 <= self.clock() - record["arrived_at"] <= 0.5
        qualified = fresh and len(self.arrivals) >= 3 and self.arrivals[-1] - self.arrivals[0] >= 0.2 - 1e-9
        if not fresh:
            self.arrivals.clear()
        return dict(
            record or self.binding,
            qualified=qualified,
            reason=self.reason
            if record is None
            else (None if qualified else "local image freshness/qualification unavailable"),
        )


def validate_local_endpoint(url, source):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.port != 1984
        or parsed.path != "/api/frame.jpeg"
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parse_qs(parsed.query) != {"src": [source["role"]]}
    ):
        raise ValueError("exact existing same-Pi authenticated JPEG route required")
    address = ipaddress.IPv4Address(parsed.hostname)
    if not address.is_private or address.is_unspecified or address.is_multicast:
        raise ValueError("private same-Pi IPv4 address required")
    # This route verification is preparation only; never a per-frame/owner network wait.
    result = subprocess.run(
        ["ip", "-j", "route", "get", str(address)], check=True, capture_output=True, timeout=2, text=True
    )
    routes = json.loads(result.stdout)
    if (
        len(routes) != 1
        or routes[0].get("type") != "local"
        or routes[0].get("dev") != "lo"
        or routes[0].get("dst") != str(address)
    ):
        raise ValueError("camera endpoint must route locally on this Pi")
    if source["machine"] != socket.gethostname():
        raise ValueError("exact local camera machine identity required")


class LocalObservationWorker:
    def __init__(self, config):
        from tools.am1_camera_viewer import load_private, validate_credentials
        from tools.am1_session_ipc import IPCClient, private_directory

        if set(config) != {"owner_state", "state", "url", "credentials", "source"}:
            raise ValueError("exact local worker configuration required")
        self.source = local_source_identity(config["source"])
        validate_local_endpoint(config["url"], self.source)
        credentials = validate_credentials(load_private(config["credentials"]))
        self.credentials = credentials
        self.config = config
        self.directory = private_directory(config["state"])
        self.ipc = IPCClient(config["owner_state"])
        self.receiver = None
        self.current = None
        self.last_logged = self.last_sent = None
        self.log_records = self.log_bytes = 0

    async def sync(self, evidence):
        from tools.am1_session_ipc import encode

        key = (
            None
            if evidence is None
            else (evidence.get("capture_generation"), evidence.get("sequence"), evidence["qualified"])
        )
        delivery = evidence if key != self.last_sent or (evidence and not evidence["qualified"]) else None
        result = await self.ipc.request(
            {"op": "observation_sync", "evidence": delivery}, "observation-worker"
        )
        self.last_sent = key
        if evidence is not None:
            key = (evidence.get("capture_generation"), evidence.get("sequence"), evidence["qualified"])
            if key != self.last_logged:
                self.last_logged = key
                row = encode({"evidence": evidence, "consumption": result.get("consumption")}) + b"\n"
                if self.log_records < 8192 and self.log_bytes + len(row) <= 16 * 1024 * 1024:
                    with (self.receiver.directory / "delivery.jsonl").open("ab") as stream:
                        stream.write(row)
                    self.log_records += 1
                    self.log_bytes += len(row)
        return result["request"]

    async def run(self):
        import base64

        from aiohttp import ClientError, ClientSession, ClientTimeout

        value = f"{self.credentials['username']}:{self.credentials['password']}".encode()
        authorization = "Basic " + base64.b64encode(value).decode()
        async with ClientSession(timeout=ClientTimeout(total=0.35), trust_env=False) as session:
            while True:
                try:
                    request = await self.sync(self.receiver.evidence() if self.receiver else None)
                    if not request or not request["active"]:
                        await asyncio.sleep(0.1)
                        continue
                    if any(request.get(k) != self.source[k] for k in LOCAL_SOURCE_KEYS):
                        raise ValueError("owner local source configuration mismatch")
                    if not self.current or self.current["generation"] != request["generation"]:
                        self.current = request
                        self.receiver = LocalReceiver(self.directory / request["generation"], request)
                        self.last_logged = self.last_sent = None
                        self.log_records = self.log_bytes = 0
                    if not (self.directory / "pause-delivery").exists():
                        async with session.get(
                            self.config["url"],
                            headers={"Authorization": authorization},
                            allow_redirects=False,
                        ) as response:
                            if response.status != 200 or response.headers.get("Content-Type") != "image/jpeg":
                                raise ValueError("local current JPEG unavailable")
                            jpeg = await response.content.read(MAX_JPEG + 1)
                            # HTTP stream chunks may be partial; read exactly the bounded full body.
                            while not response.content.at_eof() and len(jpeg) <= MAX_JPEG:
                                jpeg += await response.content.read(MAX_JPEG + 1 - len(jpeg))
                            await asyncio.to_thread(self.receiver.accept, jpeg, response.headers)
                    await asyncio.sleep(0.075)
                except (ClientError, ValueError, KeyError, OSError, TimeoutError) as exc:
                    if self.receiver:
                        self.receiver.unqualify("local worker unavailable: " + type(exc).__name__)
                    await asyncio.sleep(0.1)


def main():
    from tools.am1_session_ipc import strict_json

    parser = argparse.ArgumentParser(
        description="Independent robot-local JPEG observation worker; no devices"
    )
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    asyncio.run(LocalObservationWorker(strict_json(Path(args.config).read_bytes())).run())


if __name__ == "__main__":
    main()
