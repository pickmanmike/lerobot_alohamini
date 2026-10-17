"""Authenticated PC leader producer for the existing persistent AM1 owner.

USB is the ordinary source. Virtual input enters before the identical mapping,
clutch, serialization, grant and native-expiry boundaries. No motor transport is
created here. An enrolled viewer is independent and never claims this input.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import ssl
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from examples.alohamini.am1_live_teleop import (
    TELEOP_ARM_KEYS,
    TELEOP_RECIPE,
    ZERO_BODY,
    map_leader_action,
    normalized_raw,
    validate_body,
    validate_source,
    validate_target,
    validate_workspace,
)


class LeaderClutch:
    """Explicit acquisition reference, separate from unchanged motor calibration."""

    def __init__(self):
        self.reference = self.cursor = None
        self.last_clamps = {}

    def acquire(self, leader, cursor):
        self.reference, self.cursor = dict(leader), dict(cursor)

    def map(self, leader, workspace):
        if self.reference is None:
            raise ValueError("qualified current leader/cursor acquisition required")
        requested = {k: self.cursor[k] + leader[k] - self.reference[k] for k in TELEOP_ARM_KEYS}
        result = {
            k: max(workspace["bounds"][k]["min"], min(workspace["bounds"][k]["max"], requested[k]))
            for k in TELEOP_ARM_KEYS
        }
        self.last_clamps = {
            k: dict(requested=requested[k], applied=result[k], **workspace["bounds"][k])
            for k in TELEOP_ARM_KEYS
            if result[k] != requested[k]
        }
        return result


class FrameProducer:
    def __init__(self, source, *, clock=time.perf_counter, source_id=None):
        self.input = source
        self.clock = clock
        self.source_id = source_id or str(uuid4())
        self.sequence = 0
        self.previous = None
        self.workspace = self.native_cursor = None
        self.clutch = LeaderClutch()
        self.reacquire = True
        self.last_clutch = None

    def capture(self, grant, *, body=None, grant_received_at=None):
        received = self.clock() if grant_received_at is None else grant_received_at
        acquired = self.clock()  # Earliest read start, before either leader bus.
        leader = map_leader_action(self.input.get_action())
        target = leader
        if self.workspace is not None:
            if self.reacquire:
                self.clutch.acquire(leader, self.native_cursor)
                self.last_clutch = {
                    "event": "leader_clutch_acquired",
                    "leader_reference": leader,
                    "native_cursor": dict(self.native_cursor),
                    "meaning": "explicit relative acquisition; calibration unchanged",
                }
                self.reacquire = False
            target = self.clutch.map(leader, self.workspace)
        self.sequence += 1
        source = {
            "kind": self.input.kind,
            "source_id": self.source_id,
            "sample_seq": self.sequence,
            "acquired_at_monotonic_s": acquired,
            "grant_received_at_monotonic_s": received,
            "sent_at_monotonic_s": self.clock(),
            "grant_id": grant["grant_id"],
        }
        validate_source(source, grant_id=grant["grant_id"], previous=self.previous)
        self.previous = dict(source)
        return {
            "target": validate_target(target, workspace=self.workspace),
            "body": validate_body(body or ZERO_BODY),
            "source": source,
        }

    def serialize(self, frame):
        if not 0 <= self.clock() - frame["source"]["grant_received_at_monotonic_s"] < 0.25:
            raise ValueError("original capture/grant expired before serialization")
        return json.loads(json.dumps(frame, allow_nan=False))


class USBLeaderSource:
    kind = "usb-leaders"

    def __init__(self, *, left_port, right_port, leader_id, calibration_dir):
        self.options = {
            "left_port": left_port,
            "right_port": right_port,
            "leader_id": leader_id,
            "calibration_dir": calibration_dir,
        }
        self.leader = None
        self.connected = []
        self.last_raw = None

    def open(self):
        from examples.alohamini.leader_client_utils import make_normalized_bi_leader_config
        from lerobot.teleoperators.bi_so_leader import BiSOLeader

        config = make_normalized_bi_leader_config(
            left_port=self.options["left_port"],
            right_port=self.options["right_port"],
            leader_id=self.options["leader_id"],
            arm_profile="so-arm-5dof",
        )
        config.calibration_dir = Path(self.options["calibration_dir"])
        self.leader = BiSOLeader(config)
        try:
            for side in ("left", "right"):
                arm = getattr(self.leader, side + "_arm")
                if not arm.calibration or set(arm.calibration) != set(arm.bus.motors):
                    raise ValueError("existing complete " + side + " leader calibration required")
                arm.bus.connect(handshake=False)
                self.connected.append(arm.bus)
                if not arm.bus.is_calibrated:
                    raise ValueError(
                        side + " leader hardware calibration differs; recalibration is outside this adapter"
                    )
                torque = arm.bus.sync_read("Torque_Enable", normalize=False, num_retry=0)
                if set(torque) != set(arm.bus.motors) or any(v != 0 for v in torque.values()):
                    raise ValueError("leader sampling requires existing torque-off state")
        except BaseException:
            self.close()
            raise

    def get_action(self):
        if self.leader is None:
            raise ValueError("USB leaders are not connected")
        result, raw_frame = {}, {}
        # Same BiSOLeader/SOLeader named sync-read acquisition, retaining unclipped
        # raw values instead of SOLeader's legacy missing-calibration fallback.
        for side in ("left", "right"):
            bus = getattr(self.leader, side + "_arm").bus
            raw = bus.sync_read("Present_Position", normalize=False, num_retry=0)
            if set(raw) != set(bus.motors):
                raise ValueError("incomplete " + side + " leader frame")
            raw_frame[side] = dict(raw)
            for joint, value in raw.items():
                cal = bus.calibration[joint]
                identity = (cal.id, cal.drive_mode, cal.homing_offset, cal.range_min, cal.range_max)
                if type(value) is not int or not 0 <= value <= 4095 or cal.id != bus.motors[joint].id:
                    raise ValueError("invalid original leader encoder/mapping")
                result[side + "_" + joint + ".pos"] = normalized_raw(
                    value, identity, gripper=joint == "gripper"
                )
        self.last_raw = raw_frame
        return result

    def close(self):
        for bus in reversed(self.connected):
            bus.disconnect(disable_torque=False)
        self.connected.clear()


class SessionClient:
    def __init__(self, origin, ca, storage_state):
        import aiohttp
        from yarl import URL

        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.username
        ):
            raise ValueError("exact trusted HTTPS origin required")
        self.origin = origin.rstrip("/")
        self.tls = ssl.create_default_context(cafile=str(ca))
        self.tls.minimum_version = ssl.TLSVersion.TLSv1_2
        state = json.loads(Path(storage_state).read_text(encoding="utf-8"))
        cookies = [
            c
            for c in state.get("cookies", [])
            if c.get("name") == "__Host-am1-device"
            and c.get("domain", "").lstrip(".") == parsed.hostname
            and c.get("path") == "/"
            and c.get("secure")
        ]
        if len(cookies) != 1:
            raise ValueError("one existing enrolled device cookie for exact trusted origin required")
        # Exact secure origin/domain validation above precedes private LAN IP use.
        jar = aiohttp.CookieJar(unsafe=True)
        jar.update_cookies({"__Host-am1-device": cookies[0]["value"]}, response_url=URL(self.origin))
        self.http = aiohttp.ClientSession(
            cookie_jar=jar,
            headers={"Origin": self.origin},
            timeout=aiohttp.ClientTimeout(total=3),
            connector=aiohttp.TCPConnector(ssl=self.tls),
        )
        self.ws = self.reader = None
        self.pending = {}
        self.snapshot = None
        self.csrf = self.device = self.generation = self.connection = None
        self.sequence = 0
        self.changed = asyncio.Event()
        self.transport_error = None
        self.transport_close = None
        self._detaching = False

    async def get(self, path):
        async with self.http.get(self.origin + "/api/" + path, allow_redirects=False) as response:
            result = await response.json()
            if response.status != 200:
                raise RuntimeError(result.get("reason", "authenticated session read refused"))
            return result

    async def post(self, path, body):
        async with self.http.post(
            self.origin + "/api/" + path,
            json=body,
            headers={"X-AM1-CSRF": self.csrf or ""},
            allow_redirects=False,
        ) as response:
            result = await response.json()
            if response.status != 200 or result.get("accepted") is False:
                raise RuntimeError(result.get("reason", "authenticated session operation refused"))
            return result

    async def attach(self):
        self.transport_error = None
        self.transport_close = None
        self._detaching = False
        device = await self.get("session")
        if device.get("control") is not True:
            raise ValueError("existing enrolled PC control capability required")
        self.csrf, self.device = device["csrf"], device["device_id"]
        attached = await self.post("attach", {})
        self.ws = await self.http.ws_connect(
            self.origin.replace("https:", "wss:") + "/api/ws",
            ssl=self.tls,
            heartbeat=1,
            max_msg_size=65536,
            compress=0,
        )
        await self.ws.send_json({"ticket": attached["ticket"]})
        self.reader = asyncio.create_task(self._read())
        await self.wait(lambda: self.snapshot is not None, 3)

    async def _read(self):
        import aiohttp

        reason, reader_error = "stream-ended", None
        try:
            async for message in self.ws:
                if message.type != aiohttp.WSMsgType.TEXT:
                    reason = "message-" + message.type.name.lower()
                    break
                value = json.loads(message.data)
                if value.get("kind") == "snapshot":
                    self.snapshot = value["snapshot"]
                    if "revision" in value:
                        await self.ws.send_json({"ack": value["revision"]})
                    self.changed.set()
                elif value.get("kind") == "result":
                    future = self.pending.pop(value["id"], None)
                    if future and not future.done():
                        future.set_result(value)
                elif value.get("kind") == "unavailable":
                    raise RuntimeError("owner reporting unavailable")
        except asyncio.CancelledError:
            reason = "reader-cancelled"
            raise
        except Exception as error:
            reason = "reader-error"
            reader_error = {"error_type": type(error).__name__, "message": str(error)}
            self.transport_error = str(error)
        finally:
            exception = self.ws.exception()
            self.transport_close = {
                "reason": reason,
                "expected": self._detaching,
                "close_code": self.ws.close_code,
                "exception_type": type(exception).__name__ if exception else None,
                "exception": str(exception) if exception else None,
                "reader_error": reader_error,
                "observed_at_monotonic_s": time.monotonic(),
            }
            if not self._detaching and self.transport_error is None:
                self.transport_error = "current WSS connection ended: " + reason
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("current WSS connection lost"))
            self.pending.clear()
            self.changed.set()

    async def command(self, command):
        if (
            self.transport_error
            or self.ws is None
            or self.ws.closed
            or self.reader is not None
            and self.reader.done()
            or len(self.pending) >= 4
        ):
            raise ConnectionError("bounded current WSS connection required")
        identity = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self.pending[identity] = future
        try:
            await self.ws.send_json({"id": identity, "command": command})
            result = await asyncio.wait_for(future, 1.0)
            if result.get("accepted") is not True:
                raise RuntimeError(result.get("reason", "session command refused"))
            return result
        finally:
            self.pending.pop(identity, None)

    async def wait(self, predicate, timeout):
        deadline = time.monotonic() + timeout
        while True:
            if self.transport_error:
                raise ConnectionError(self.transport_error)
            if self.reader is not None and self.reader.done() or self.ws is not None and self.ws.closed:
                raise ConnectionError("current WSS connection is closed")
            if predicate():
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("qualified current native state did not arrive")
            self.changed.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.changed.wait(), 0.1)

    def identities(self):
        run = self.snapshot["run"]
        return {
            "run_id": run["run_id"],
            "service_incarnation": self.snapshot["service_incarnation"],
            "controller_generation": self.generation,
            "connection_generation": self.connection,
            "host_incarnation": run["backend_incarnation"],
            "intent_revision": run["intent_revision"],
        }

    async def claim(self):
        result = await self.command({"op": "claim"})
        self.generation = result["controller_generation"]

    async def renew(self):
        while True:
            await asyncio.sleep(0.45)
            await self.post("claim", {"controller_generation": self.generation})

    async def resume(self):
        connected = await self.command(dict(op="connect", **self.identities()))
        self.connection = connected["connection_generation"]
        self.snapshot = connected["snapshot"]
        self.sequence = 0
        await self.command(dict(op="release_input", **self.identities()))

        def qualified():
            evidence, run = self.snapshot.get("evidence", {}), self.snapshot.get("run", {})
            live = run.get("live_input") or evidence.get("live_input") or {}
            return (
                all(
                    evidence.get(k) is True
                    for k in (
                        "feedback",
                        "required_observation",
                        "hold_acknowledged",
                        "pose_aligned",
                        "native_ack",
                    )
                )
                and live.get("cursor_intent_revision") == run.get("intent_revision")
                and live.get("required_view", {}).get("qualified") is True
            )

        await self.wait(qualified, 8)
        await self.command(
            dict(
                op="resume",
                **self.identities(),
                expected_intent_revision=self.snapshot["run"]["intent_revision"],
                operation_id=str(uuid4()),
            )
        )
        await self.wait(
            lambda: (
                self.snapshot["run"]["status"] == "running"
                and self.snapshot["evidence"].get("active_acknowledged") is True
            ),
            5,
        )

    async def detach(self):
        self._detaching = True
        if self.ws is not None:
            await self.ws.close()
        if self.reader is not None:
            with contextlib.suppress(asyncio.CancelledError, ConnectionError, RuntimeError):
                await self.reader
        self.ws = self.reader = None
        self.snapshot = None

    async def close(self):
        try:
            await self.detach()
        finally:
            await self.http.close()


def _live(snapshot):
    return (
        (snapshot.get("run") or {}).get("live_input") or snapshot.get("evidence", {}).get("live_input") or {}
    )


def native_frame_ack(snapshot, sequence, source, *, body_zero=False):
    live, evidence = _live(snapshot), snapshot.get("evidence", {})
    actual = live.get("source") or {}
    if (
        live.get("last_input_seq") != sequence
        or any(actual.get(k) != source.get(k) for k in ("source_id", "sample_seq", "grant_id"))
        or not all(evidence.get(k) is True for k in ("active_acknowledged", "feedback", "native_ack"))
    ):
        return False
    if body_zero:
        body, measured = live.get("body") or {}, evidence.get("measured_body") or {}
        return (
            set(body) == set(ZERO_BODY)
            and all(body[k] == 0 for k in ZERO_BODY)
            and all(type(measured.get(k)) in (int, float) and measured[k] == 0 for k in ZERO_BODY)
        )
    return True


async def run(args):
    from examples.alohamini.am1_teleop_workload import TeleopWorkload

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    log = (output / "frames.jsonl").open("x", encoding="utf-8")

    def record(value):
        log.write(
            json.dumps(dict(local_monotonic_s=time.monotonic(), **value), sort_keys=True, allow_nan=False)
            + "\n"
        )
        log.flush()

    client = None
    source = producer = workload = renewal = keyboard = None
    started = False
    owned_run_id = None
    first_error = None
    cleanup_errors = []
    summary = {
        "source": args.source,
        "producer_program_complete": False,
        "native_execution_complete": False,
        "physical_human_movement_validated": False,
    }
    try:
        client = SessionClient(args.url, args.ca, args.storage_state)
        # Missing/mismatched USB inputs refuse before creating a robot motor task.
        if args.source == "usb-leaders":
            source = USBLeaderSource(
                left_port=args.left_port,
                right_port=args.right_port,
                leader_id=args.leader_id,
                calibration_dir=args.calibration_dir,
            )
            await asyncio.to_thread(source.open)
        from lerobot.robots.alohamini.alohamini_client import AlohaMiniClient
        from lerobot.robots.alohamini.config_alohamini import AlohaMiniClientConfig

        body_mapper = AlohaMiniClient(
            AlohaMiniClientConfig(remote_ip="127.0.0.1", robot_model="alohamini1", cameras={})
        )
        from tools.am1_physical_executor import body_recipe_action

        if args.source == "usb-leaders" and not args.no_keyboard:
            from lerobot.teleoperators.keyboard.teleop_keyboard import KeyboardTeleop, KeyboardTeleopConfig

            keyboard = KeyboardTeleop(KeyboardTeleopConfig(id="am1-persistent-leader-keyboard"))
            keyboard.connect()
            if not keyboard.is_connected:
                raise RuntimeError("ordinary body keyboard failed to connect")
        # Cold mapper imports and local source setup must finish before the
        # current WSS reader/heartbeat starts on this asyncio thread.
        await client.attach()
        await client.claim()
        renewal = asyncio.create_task(client.renew())
        import aiohttp

        start_body = {
            "operation_id": str(uuid4()),
            "recipe": TELEOP_RECIPE,
            "controller_generation": client.generation,
        }
        summary.update(
            start_operation_id=start_body["operation_id"],
            start_outcome_uncertain=True,
            native_cleanup_uncertain=True,
            start_ambiguous_attempts=[],
        )
        record(
            {"event": "start_requested", "operation_id": start_body["operation_id"], "recipe": TELEOP_RECIPE}
        )
        try:
            start_receipt = await client.post("start", start_body)
        except (OSError, TimeoutError, aiohttp.ClientError) as original:
            attempt = {"attempt": 1, "error_type": type(original).__name__, "message": str(original)}
            summary["start_ambiguous_attempts"].append(attempt)
            record(dict(event="start_outcome_ambiguous", operation_id=start_body["operation_id"], **attempt))
            try:
                # Existing durable operation identity is retried once unchanged.
                start_receipt = await client.post("start", start_body)
            except (OSError, TimeoutError, aiohttp.ClientError) as retry_error:
                summary["start_ambiguous_attempts"].append(
                    {"attempt": 2, "error_type": type(retry_error).__name__, "message": str(retry_error)}
                )
                raise original from retry_error
        except RuntimeError:
            summary.update(start_outcome_uncertain=False, native_cleanup_uncertain=False)
            raise
        owned_run_id = start_receipt["run_id"]
        summary["owned_run_id"] = owned_run_id
        summary["start_outcome_uncertain"] = False
        started = True

        def native_admitted():
            state = client.snapshot or {}
            run = state.get("run") or {}
            if run.get("run_id") != owned_run_id or run.get("recipe") != TELEOP_RECIPE:
                return False
            if run.get("status") in {"faulted", "interrupted", "stopped", "completed"}:
                # Freeze the actual owned-run failure before ordinary Stop and
                # cleanup reporting can replace the current snapshot.
                summary["native_admission_terminal"] = json.loads(json.dumps(state, allow_nan=False))
                record({"event": "native_admission_terminal", "snapshot": state})
                cause = (
                    run.get("native_first_cause")
                    or run.get("first_cause")
                    or state.get("evidence", {}).get("fault")
                    or "terminal before native Live"
                )
                raise RuntimeError("native admission " + run["status"] + ": " + str(cause))
            return run.get("native_live_at") is not None

        await client.wait(native_admitted, 180)
        workspace = validate_workspace(_live(client.snapshot)["workspace"])
        run_id, deadline = client.snapshot["run"]["run_id"], client.snapshot["run"]["deadline"]
        summary.update(run_id=run_id, original_native_deadline=deadline, workspace=workspace)
        record({"event": "native_admitted", "snapshot": client.snapshot})
        if args.source == "virtual-leaders":
            workload = source = TeleopWorkload(workspace)
        producer = FrameProducer(source)
        producer.workspace = workspace
        producer.native_cursor = _live(client.snapshot)["cursor"]
        await client.resume()
        if workload and not args.next_start_seconds:
            workload.admit(time.monotonic())
        active_at, interrupted, frame_count, recoveries = time.monotonic(), False, 0, 0
        last_phase = None
        while True:
            if renewal.done():
                renewal.result()
            state = client.snapshot
            if state["run"]["run_id"] != run_id or state["run"]["deadline"] != deadline:
                raise RuntimeError("original run identity/deadline changed")
            if state["run"]["status"] != "running":
                cause = next(
                    (
                        v.get("kind")
                        for v in reversed(state.get("events", []))
                        if v.get("kind")
                        in {"required_view_expired", "input_expired", "native_input_expired", "pause", "stop"}
                    ),
                    None,
                )
                if (
                    state["run"]["status"] != "paused"
                    or cause not in {"required_view_expired", "input_expired", "native_input_expired"}
                    or state["run"].get("first_cause")
                    or state["run"].get("uncertain")
                    or recoveries >= 3
                ):
                    raise RuntimeError(
                        "input held: "
                        + str(state["run"].get("first_cause") or cause or _live(state).get("state"))
                    )
                recoveries += 1
                if workload:
                    workload.freeze()
                record({"event": "cause_specific_hold", "cause": cause, "snapshot": state})
                await client.resume()
                producer.native_cursor = _live(client.snapshot)["cursor"]
                producer.reacquire = True
                if workload and not args.next_start_seconds:
                    workload.admit(time.monotonic())
                record({"event": "qualified_hold_recovery", "cause": cause, "snapshot": client.snapshot})
            if (
                args.interrupt_at_seconds is not None
                and workload
                and not interrupted
                and workload.elapsed_s >= args.interrupt_at_seconds
            ):
                interrupted = True
                workload.freeze()
                renewal.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await renewal
                record({"event": "controlled_input_interruption", "progress_s": workload.elapsed_s})
                await client.detach()
                await asyncio.sleep(args.interruption_seconds)
                await client.attach()
                await client.claim()
                renewal = asyncio.create_task(client.renew())
                await client.resume()
                producer.native_cursor = _live(client.snapshot)["cursor"]
                producer.reacquire = True
                if not args.next_start_seconds:
                    workload.admit(time.monotonic())
                record(
                    {
                        "event": "qualified_reattachment",
                        "progress_s": workload.elapsed_s,
                        "snapshot": client.snapshot,
                    }
                )
            phase = workload.phase if workload else "usb"
            if phase != last_phase:
                record(
                    {
                        "event": "workload_phase",
                        "phase": phase,
                        "progress_s": workload.elapsed_s if workload else None,
                    }
                )
                last_phase = phase
            body = dict(ZERO_BODY)
            if workload and workload.body_phase_elapsed is not None:
                measured = client.snapshot["evidence"].get("measured_body", {})
                body_mapper.last_remote_state.update(measured)
                body = body_recipe_action(body_mapper, workload.body_phase_elapsed)
            elif keyboard is not None:
                keys = keyboard.get_action()
                if body_mapper.teleop_keys.get("quit", "q") in keys:
                    break
                body_mapper.last_remote_state.update(client.snapshot["evidence"].get("measured_body", {}))
                # The reviewed profile uses the ordinary lowest body speed.
                keys = {k: v for k, v in keys.items() if k not in {"t", "g"}}
                body_mapper.speed_index = 0
                body = {
                    **body_mapper._from_keyboard_to_base_action(keys),
                    **body_mapper._from_keyboard_to_lift_action(keys),
                }
            if "lift_axis.vel" in body and "lift_axis.height_mm" in body:
                # LiftAxis gives explicit velocity precedence, including stopped
                # zero. Retain that behavior if a legacy mapper emits both.
                original_body = dict(body)
                body.pop("lift_axis.height_mm")
                record({"event": "body_velocity_precedence", "original": original_body, "sent": body})
            grant = await client.command(dict(op="grant", **client.identities()))
            received = time.perf_counter()
            frame = await asyncio.to_thread(producer.capture, grant, body=body, grant_received_at=received)
            frame = producer.serialize(frame)
            client.sequence += 1
            result = await client.command(
                dict(
                    op="input",
                    **client.identities(),
                    grant_id=grant["grant_id"],
                    seq=client.sequence,
                    **frame,
                )
            )
            if producer.last_clutch:
                record(producer.last_clutch)
                producer.last_clutch = None
            record(
                {
                    "event": "leader_frame",
                    "network_seq": client.sequence,
                    "grant": grant,
                    "frame": frame,
                    "receipt": result,
                    "progress_s": workload.elapsed_s if workload else None,
                    "phase": phase,
                    "raw_leaders": getattr(source, "last_raw", None),
                    "command_clamps": producer.clutch.last_clamps,
                    "native": client.snapshot,
                }
            )
            frame_count += 1
            if workload:
                if not args.next_start_seconds:
                    workload.advance(time.monotonic())
                if (
                    workload.complete
                    or args.next_start_seconds
                    and time.monotonic() - active_at >= args.next_start_seconds
                    or not args.next_start_seconds
                    and workload.elapsed_s >= args.duration_seconds
                ):
                    break
            elif time.monotonic() - active_at >= args.duration_seconds:
                break
            await asyncio.sleep(0.06)
        # Qualification time is separate from active program progress. Keep new
        # acquisitions alive while actual body feedback settles; never refresh
        # one retained zero sample with another grant.
        qualification_at = time.monotonic()
        while True:
            grant = await client.command(dict(op="grant", **client.identities()))
            frame = producer.serialize(
                await asyncio.to_thread(
                    producer.capture, grant, body=ZERO_BODY, grant_received_at=time.perf_counter()
                )
            )
            client.sequence += 1
            await client.command(
                dict(
                    op="input",
                    **client.identities(),
                    grant_id=grant["grant_id"],
                    seq=client.sequence,
                    **frame,
                )
            )
            await client.wait(
                lambda frame=frame: native_frame_ack(client.snapshot, client.sequence, frame["source"]), 0.18
            )
            final = client.snapshot
            record({"event": "terminal_zero_qualification", "frame": frame, "native": final})
            if native_frame_ack(final, client.sequence, frame["source"], body_zero=True):
                break
            if time.monotonic() - qualification_at >= 2:
                raise RuntimeError("terminal current native body-zero feedback did not qualify")
            await asyncio.sleep(0.03)
        summary.update(
            frame_count=frame_count,
            active_progress_s=workload.elapsed_s if workload else None,
            phases_completed=sorted(workload.phases_completed) if workload else [],
            producer_program_complete=bool(workload and workload.complete),
            final_native_ack=final,
            final_body_zero=True,
            interrupted=interrupted,
            qualified_recoveries=recoveries,
            terminal_qualification_seconds=time.monotonic() - qualification_at,
            endpoint_error_normalized={
                k: final["evidence"]["measured_positions"][k] - frame["target"][k] for k in TELEOP_ARM_KEYS
            },
        )
    except BaseException as error:
        first_error = error
        summary["failure"] = str(error)
        summary["failure_type"] = type(error).__name__
        summary["last_raw_leaders"] = getattr(source, "last_raw", None)
        if workload:
            workload.freeze()
    finally:

        async def cleanup_step(name, operation, timeout):
            try:
                await asyncio.wait_for(operation(), timeout)
            except BaseException as error:
                cleanup_errors.append(
                    {"step": name, "error_type": type(error).__name__, "message": str(error)}
                )

        if renewal:

            async def stop_renewal():
                if not renewal.done():
                    renewal.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await renewal

            await cleanup_step("controller_renewal", stop_renewal, 2.0)
        if started and owned_run_id and client is not None:
            summary["native_cleanup_uncertain"] = True

            async def stop_owned_run():
                def terminal_qualified(terminal):
                    if (terminal.get("run") or {}).get("run_id") != owned_run_id:
                        raise RuntimeError("cleanup status belongs to another run")
                    if terminal["run"].get("status") in {"stopped", "faulted", "interrupted"}:
                        summary["native_terminal"] = terminal
                        terminal_run = terminal["run"]
                        summary["native_cleanup_uncertain"] = (
                            terminal_run.get("uncertain") is not False
                            or terminal_run.get("cleanup") != "physical_readback_verified"
                        )
                        if terminal_run["status"] in {"faulted", "interrupted"}:
                            summary["terminal_failure"] = {
                                "status": terminal_run["status"],
                                "cause": terminal_run.get("native_first_cause")
                                or terminal_run.get("first_cause")
                                or terminal.get("evidence", {}).get("fault")
                                or "native terminal cause unavailable",
                            }
                        if summary["native_cleanup_uncertain"]:
                            raise RuntimeError("native cleanup remains uncertain")
                        return True
                    return False

                try:
                    before_stop = (await client.get("state"))["snapshot"]
                except (OSError, TimeoutError, aiohttp.ClientError, RuntimeError) as error:
                    summary["pre_stop_state_error"] = {
                        "error_type": type(error).__name__,
                        "message": str(error),
                    }
                else:
                    if terminal_qualified(before_stop):
                        return
                try:
                    stopped = await client.post(
                        "stop", {"run_id": owned_run_id, "operation_id": str(uuid4())}
                    )
                    summary["stop_receipt"] = stopped
                except (OSError, TimeoutError, aiohttp.ClientError, RuntimeError) as error:
                    try:
                        terminal = (await client.get("state"))["snapshot"]
                        qualified = terminal_qualified(terminal)
                    except BaseException as qualification_error:
                        summary["stop_qualification_error"] = {
                            "error_type": type(qualification_error).__name__,
                            "message": str(qualification_error),
                        }
                        raise error from qualification_error
                    if not qualified:
                        raise
                    summary["stop_request_error"] = {
                        "error_type": type(error).__name__,
                        "message": str(error),
                    }
                    return
                until = time.monotonic() + 85
                while time.monotonic() < until:
                    if terminal_qualified((await client.get("state"))["snapshot"]):
                        return
                    await asyncio.sleep(0.1)
                raise TimeoutError("native cleanup did not qualify")

            await cleanup_step("native_stop", stop_owned_run, 89.0)
        if isinstance(source, USBLeaderSource):
            await cleanup_step("usb_leader_close", lambda: asyncio.to_thread(source.close), 3.0)
        if keyboard is not None:
            await cleanup_step("keyboard_close", lambda: asyncio.to_thread(keyboard.disconnect), 2.0)
        if client is not None:
            await cleanup_step("session_close", client.close, 3.0)
            summary["transport_error"] = getattr(client, "transport_error", None)
            summary["transport_close"] = getattr(client, "transport_close", None)
        await cleanup_step("frame_log_close", lambda: asyncio.to_thread(log.close), 2.0)
        summary["cleanup_errors"] = cleanup_errors
        if summary.get("terminal_failure") and first_error is None:
            terminal_failure = summary["terminal_failure"]
            first_error = RuntimeError(
                "native terminal " + terminal_failure["status"] + ": " + str(terminal_failure["cause"])
            )
            summary.update(failure=str(first_error), failure_type=type(first_error).__name__)
        (output / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8"
        )
    if first_error is not None:
        raise first_error
    if cleanup_errors:
        raise RuntimeError("cleanup failed; inspect durable summary: " + str(output / "summary.json"))
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("usb-leaders", "virtual-leaders"), default="usb-leaders")
    for name in ("url", "ca", "storage-state", "output"):
        parser.add_argument("--" + name, required=True)
    for name in ("left-port", "right-port", "leader-id", "calibration-dir"):
        parser.add_argument("--" + name)
    parser.add_argument("--duration-seconds", type=float, default=180.0)
    parser.add_argument("--next-start-seconds", type=float)
    parser.add_argument("--interrupt-at-seconds", type=float)
    parser.add_argument("--interruption-seconds", type=float, default=0.65)
    parser.add_argument(
        "--no-keyboard", action="store_true", help="USB stationary probe/arm-only operation; body stays zero"
    )
    args = parser.parse_args(argv)
    if args.source == "usb-leaders" and not all(
        (args.left_port, args.right_port, args.leader_id, args.calibration_dir)
    ):
        parser.error("USB requires existing explicit ports, leader ID and calibration directory")
    if not 0 < args.duration_seconds <= 180 or not 0.3 <= args.interruption_seconds <= 2:
        parser.error("bounded duration <=180s and interruption .3..2s required")
    if args.next_start_seconds is not None and not 0 < args.next_start_seconds <= 5:
        parser.error("next Start check must be greater than zero through5s")
    print(json.dumps(asyncio.run(run(args)), sort_keys=True))


if __name__ == "__main__":
    main()
