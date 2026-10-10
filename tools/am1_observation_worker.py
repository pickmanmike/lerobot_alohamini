"""Independent direct WSS receiver/decoder. All network and disk work stays outside the owner tick."""

import argparse
import asyncio
import secrets
import ssl
import time
from pathlib import Path
from urllib.parse import urlsplit

from tools.am1_observation import BINDING_KEYS, SOURCE_KEYS, ObservationMailbox  # noqa: F401
from tools.am1_observer import ObserverReceiver
from tools.am1_session_ipc import IPCClient, private_directory, private_json, strict_json


class BoundReceiver(ObserverReceiver):
    def __init__(self, output_dir, binding, clock=time.monotonic):
        self.binding = {k: binding[k] for k in BINDING_KEYS}
        self.clock = clock
        self.decoded_at = None
        super().__init__(output_dir, binding["generation"])

    def accept_bound(self, record, received_ms):
        if not isinstance(record, dict) or record.get("binding") != self.binding:
            self._unqualify("exact source/run/framing binding mismatch")
            return False
        accepted = self.accept(
            record, received_wall_time_ms=int(time.time() * 1000), received_monotonic_ms=received_ms
        )
        self.decoded_at = self.clock()
        # Includes decode, bounded image writes and health publication, never just socket receipt.
        self.expire(self.decoded_at * 1000)
        return accepted and self.state.get("running") is True

    def evidence(self, now):
        self.expire(now * 1000)
        qualified = (
            self.state.get("running") is True
            and self.current_contiguous_frames >= 3
            and self.current_contiguous_span_ms >= 200 - 1e-6
        )
        return dict(
            self.binding,
            qualified=qualified,
            reason=self.state.get("reason"),
            sequence=self.last_sequence,
            received_at=(self.last_received_monotonic_ms or 0) / 1000,
            decoded_at=self.decoded_at or 0,
            source_age_s=self.last_effective_capture_age_ms / 1000,
            frame_sha256=self.state.get("frame_sha256"),
        )


class ObservationWorker:
    def __init__(self, config):
        self.config = config
        parsed = urlsplit(config["url"])
        if (
            parsed.scheme != "wss"
            or parsed.path != "/observer"
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("exact private WSS observer endpoint required")
        self.ipc = IPCClient(config["owner_state"])
        self.directory = private_directory(config["state"])
        self.tls = ssl.create_default_context(cafile=config["ca"])
        self.tls.minimum_version = ssl.TLSVersion.TLSv1_2
        self.current = None
        self.receiver = None
        self.ws = None
        self.sent_sequence = 0
        self.next_challenge = 0
        self.last_command = 0
        self.receiving = None
        self.last_logged = None
        self.log_bytes = self.log_records = 0

    async def sync(self, evidence=None):
        result = await self.ipc.request(
            {"op": "observation_sync", "evidence": evidence}, "observation-worker"
        )
        if evidence and self.receiver:
            key = (evidence["sequence"], evidence["qualified"])
            if key != self.last_logged:
                self.last_logged = key
                from tools.am1_session_ipc import encode

                row = encode({"evidence": evidence, "consumption": result.get("consumption")}) + b"\n"
                if self.log_records < 8192 and self.log_bytes + len(row) <= 16 * 1024 * 1024:
                    with (self.receiver.output_dir / "delivery.jsonl").open("ab") as stream:
                        stream.write(row)
                    self.log_records += 1
                    self.log_bytes += len(row)
        return result["request"]

    async def exchange(self, session, request):
        if self.ws is None or self.ws.closed:
            from aiohttp import ClientWSTimeout

            self.ws = await session.ws_connect(
                self.config["url"],
                ssl=self.tls,
                headers={"Authorization": "Bearer " + self.config["token"]},
                max_msg_size=1024 * 1024,
                heartbeat=5,
                timeout=ClientWSTimeout(ws_receive=2, ws_close=1),
            )
            self.last_command = 0
            self.receiving = asyncio.create_task(self.ws.receive())
        now = time.monotonic()
        if now - self.last_command >= 1:
            await self.ws.send_json({"op": "start" if request["active"] else "stop", "request": request})
            self.last_command = now
        if request["active"] and now >= self.next_challenge:
            nonce = secrets.token_hex(16)
            self.receiver.issue_challenge(nonce, now * 1000)
            await self.ws.send_json({"op": "challenge", "generation": request["generation"], "nonce": nonce})
            self.next_challenge = now + 0.125
        if self.receiving.done():
            from aiohttp import WSMsgType

            message = self.receiving.result()
            if message.type != WSMsgType.TEXT:
                raise ConnectionError("observer channel closed")
            received = time.monotonic() * 1000
            value = strict_json(message.data)
            if value.get("event") == "frame":
                # Await exactly one decode; websocket reader queue is bounded by aiohttp.
                # Private application-boundary acceptance fault; never exposed by HTTP/API.
                if not (self.directory / "pause-delivery").exists():
                    await asyncio.to_thread(self.receiver.accept_bound, value, received)
            elif value.get("event") == "status":
                private_json(self.directory / "source-status.json", value)
            self.receiving = asyncio.create_task(self.ws.receive())

    async def disconnect(self):
        if self.receiving:
            self.receiving.cancel()
            await asyncio.gather(self.receiving, return_exceptions=True)
            self.receiving = None
        if self.ws:
            await self.ws.close()
            self.ws = None

    async def run(self):
        from aiohttp import ClientSession, ClientTimeout

        async with ClientSession(timeout=ClientTimeout(total=2)) as session:
            try:
                await self.loop(session)
            finally:
                await self.disconnect()

    async def loop(self, session):
        from aiohttp import ClientError

        while True:
            try:
                evidence = None
                if self.receiver:
                    e = self.receiver.evidence(time.monotonic())
                    if not e["qualified"] or e["sequence"] != self.sent_sequence:
                        evidence = e
                        self.sent_sequence = e["sequence"]
                request = await self.sync(evidence)
                if request is None:
                    if self.current:
                        await self.exchange(session, dict(self.current, active=False))
                    await asyncio.sleep(0.1)
                    continue
                if not self.current or request["generation"] != self.current["generation"]:
                    if self.current and self.ws and not self.ws.closed:
                        await self.ws.send_json({"op": "stop", "request": dict(self.current, active=False)})
                    await self.disconnect()
                    self.current = request
                    self.receiver = BoundReceiver(self.directory / request["generation"], request)
                    self.sent_sequence = 0
                    self.last_logged = None
                    self.log_bytes = self.log_records = 0
                await self.exchange(session, request)
                await asyncio.sleep(0.025)
            except (ClientError, OSError, ValueError, KeyError, TimeoutError, ConnectionError) as error:
                if self.receiver:
                    self.receiver._unqualify("worker transport unavailable: " + type(error).__name__)
                await self.disconnect()
                await asyncio.sleep(0.25)


def main():
    parser = argparse.ArgumentParser(description="Private independent current-image observation worker")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    worker = ObservationWorker(strict_json(Path(args.config).read_bytes()))
    asyncio.run(worker.run())


if __name__ == "__main__":
    main()
