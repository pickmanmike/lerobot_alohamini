"""Leader-format acquisition, clutch and finite workload without devices."""

import importlib
from uuid import uuid4

import pytest

from tests.robots.test_am1_live_teleop_contract import KEYS, workspace


def adapter():
    name = "tools.am1_leader_adapter"
    assert importlib.util.find_spec(name), "authenticated PC leader adapter is missing"
    return importlib.import_module(name)


def test_acquisition_follows_grant_and_expired_capture_is_never_regranted():
    m = adapter()
    clock = [20.0]

    class Input:
        kind = "virtual-leaders"

        def get_action(self):
            clock[0] += 0.02
            return {k.removeprefix("arm_"): 0.0 for k in KEYS}

    producer = m.FrameProducer(Input(), clock=lambda: clock[0])
    frame = producer.capture({"grant_id": str(uuid4()), "expires": 999.0})
    assert frame["source"]["grant_received_at_monotonic_s"] == 20.0
    assert frame["source"]["acquired_at_monotonic_s"] == 20.0
    assert frame["source"]["sent_at_monotonic_s"] == 20.02
    assert set(frame["target"]) == set(KEYS)
    clock[0] += 0.5
    with pytest.raises(ValueError):
        producer.serialize(frame)


def test_expired_input_tolerance_requires_exact_structured_original_refusal():
    m = adapter()
    exact = {"accepted": False, "status": "refused", "reason": "expired grant", "effect_admitted": False}
    assert m.expired_input_refusal(m.CommandRefusedError(exact))
    assert not m.expired_input_refusal(RuntimeError("expired grant"))
    for change in (
        {"accepted": None},
        {"status": "unknown"},
        {"reason": "required selected view expired"},
        {"effect_admitted": None},
    ):
        assert not m.expired_input_refusal(m.CommandRefusedError(dict(exact, **change)))


def test_recovery_refusal_requires_original_grant_operation_and_exact_structured_receipt():
    m = adapter()
    receipt = {
        "accepted": False,
        "status": "refused",
        "reason": "interactive running required",
        "effect_admitted": False,
    }
    error = m.CommandRefusedError(receipt)
    assert not m.recovery_grant_refusal(error)
    error.operation = "input"
    assert not m.recovery_grant_refusal(error)
    error.operation = "grant"
    assert m.recovery_grant_refusal(error)
    for key in receipt:
        bad = m.CommandRefusedError({**receipt, key: None})
        bad.operation = "grant"
        assert not m.recovery_grant_refusal(bad)


@pytest.mark.parametrize(
    "change", [{"reason": "required selected view expired"}, {"accepted": None}, {"effect_admitted": None}]
)
def test_sample_sender_preserves_all_other_refusals_without_regrant(change):
    import asyncio

    m = adapter()
    calls, records = [], []

    class Input:
        kind = "virtual-leaders"

        def get_action(self):
            return {key.removeprefix("arm_"): 0.0 for key in KEYS}

    class Client:
        sequence = 0

        def identities(self):
            return {"run_id": "original"}

        async def command(self, command):
            calls.append(command)
            if command["op"] == "grant":
                return {"grant_id": str(uuid4())}
            raise m.CommandRefusedError(
                {
                    "accepted": False,
                    "status": "refused",
                    "reason": "expired grant",
                    "effect_admitted": False,
                    **change,
                }
            )

    with pytest.raises(m.CommandRefusedError):
        asyncio.run(
            m.send_leader_sample(
                Client(),
                m.FrameProducer(Input()),
                m.ZERO_BODY,
                records.append,
                phase="individual",
                progress_s=0.0,
            )
        )
    assert [row["op"] for row in calls] == ["grant", "input"]
    assert records[-1]["fresh_acquisition_allowed"] is False


def test_sample_sender_never_sends_under_an_identity_changed_during_capture():
    import asyncio

    m = adapter()
    calls, records = [], []
    current = ["original"]

    class Input:
        kind = "virtual-leaders"

        def get_action(self):
            current[0] = "another-run"
            return {key.removeprefix("arm_"): 0.0 for key in KEYS}

    class Client:
        sequence = 0

        def identities(self):
            return {"run_id": current[0]}

        async def command(self, command):
            calls.append(command)
            return {"grant_id": str(uuid4())}

    with pytest.raises(RuntimeError, match="identity changed"):
        asyncio.run(
            m.send_leader_sample(
                Client(),
                m.FrameProducer(Input()),
                m.ZERO_BODY,
                records.append,
                phase="individual",
                progress_s=0.0,
            )
        )
    assert [row["op"] for row in calls] == ["grant"]
    assert records[-1]["identity"]["run_id"] == "original"


def test_real_core_wss_expired_grant_keeps_original_refusal_and_allows_fresh_grant(tmp_path):
    import asyncio
    import json

    from tests.robots.am1_session_fixture import Cluster
    from tests.robots.test_am1_session_api import enrolled

    m = adapter()
    cluster = Cluster(tmp_path / "cluster")

    async def scenario():
        session = await enrolled(cluster)
        token = next(cookie.value for cookie in session.cookie_jar if cookie.key == "__Host-am1-device")
        await session.close()
        storage = tmp_path / "state.json"
        storage.write_text(
            json.dumps(
                {
                    "cookies": [
                        {
                            "name": "__Host-am1-device",
                            "value": token,
                            "domain": "127.0.0.1",
                            "path": "/",
                            "secure": True,
                        }
                    ]
                }
            )
        )
        client = m.SessionClient(cluster.url, cluster.cert, storage)
        try:
            await client.attach()
            await client.claim()
            start = await client.post(
                "start",
                {
                    "operation_id": str(uuid4()),
                    "recipe": "fake-interactive",
                    "controller_generation": client.generation,
                },
            )
            await client.wait(lambda: (client.snapshot.get("run") or {}).get("run_id") == start["run_id"], 2)
            base = {
                "run_id": start["run_id"],
                "service_incarnation": client.snapshot["service_incarnation"],
                "controller_generation": client.generation,
            }
            connected = await client.command(dict(op="connect", **base))
            base["connection_generation"] = connected["connection_generation"]
            await client.command(dict(op="release_input", **base))
            current = (await client.get("state"))["snapshot"]
            await client.command(
                dict(
                    op="resume",
                    **base,
                    expected_intent_revision=current["run"]["intent_revision"],
                    operation_id=str(uuid4()),
                )
            )
            old = await client.command(dict(op="grant", **base))
            await asyncio.sleep(0.3)
            with pytest.raises(m.CommandRefusedError) as caught:
                await client.command(dict(op="input", **base, grant_id=old["grant_id"], seq=1, target=[0.0]))
            assert m.expired_input_refusal(caught.value)
            assert caught.value.response["reason"] == "expired grant"
            assert (
                caught.value.timing["send_completed_at_monotonic_s"]
                <= caught.value.timing["response_observed_at_monotonic_s"]
            )
            fresh = await client.command(dict(op="grant", **base))
            assert fresh["grant_id"] != old["grant_id"]
            assert (
                await client.command(
                    dict(op="input", **base, grant_id=fresh["grant_id"], seq=2, target=[0.0])
                )
            )["accepted"]
            # The owner ticks before handling grant; its reply can precede the
            # WSS snapshot reporting the newly entered recovery episode.
            cluster.flags.write_text(json.dumps({"feedback": False}))
            records = []
            client.identities = lambda: dict(base)
            with pytest.raises(m.CommandRefusedError, match="interactive running required"):
                await m.send_leader_sample(
                    client, None, m.ZERO_BODY, records.append, phase="combined", progress_s=2.0
                )
            assert records[-1]["event"] == "leader_grant_rejected"
            assert records[-1]["identity"] == base
            assert records[-1]["receipt"]["effect_admitted"] is False
            assert (
                records[-1]["grant_timing"]["response_observed_at_monotonic_s"]
                >= records[-1]["grant_request_started_at_monotonic_s"]
            )
            authoritative = (await client.get("state"))["snapshot"]
            assert authoritative["run"]["status"] == "recovering"
            assert set(authoritative["run"]["recovery"]) == {"started", "ceiling", "revision"}
        finally:
            await client.close()

    try:
        asyncio.run(scenario())
    finally:
        cluster.close()


def test_clutch_records_reference_and_rejoin_clears_outage_motion_without_calibration_change():
    m = adapter()
    origin = dict.fromkeys(KEYS, 4.0)
    current = dict.fromkeys(KEYS, 7.0)
    clutch = m.LeaderClutch()
    clutch.acquire(origin, current)
    assert clutch.map(origin, workspace()) == current
    moved = dict(origin, **{KEYS[0]: 5.0})
    assert clutch.map(moved, workspace())[KEYS[0]] == 8.0
    clutch.acquire(dict.fromkeys(KEYS, 90.0), dict.fromkeys(KEYS, 3.0))
    expected = dict.fromkeys(KEYS, 3.0)
    expected[KEYS[8]] = 4.7
    assert clutch.map(dict.fromkeys(KEYS, 90.0), workspace()) == expected
    assert clutch.last_clamps[KEYS[8]]["requested"] == 3.0


def test_read_only_usb_sampling_retains_original_raw_and_unclipped_calibrated_values():
    from types import SimpleNamespace

    m = adapter()
    names = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")

    class Bus:
        def __init__(self, direction):
            self.motors = {name: SimpleNamespace(id=i) for i, name in enumerate(names, 1)}
            self.calibration = {
                name: SimpleNamespace(
                    id=i, drive_mode=direction, homing_offset=0, range_min=1000, range_max=3000
                )
                for i, name in enumerate(names, 1)
            }

        def sync_read(self, register, *, normalize, num_retry):
            assert register == "Present_Position" and normalize is False
            return {name: 3001 if name == "shoulder_lift" else 2000 for name in names}

    source = m.USBLeaderSource(
        left_port="unused", right_port="unused", leader_id="test", calibration_dir="unused"
    )
    source.leader = SimpleNamespace(
        left_arm=SimpleNamespace(bus=Bus(0)), right_arm=SimpleNamespace(bus=Bus(1))
    )
    values = source.get_action()
    assert values["left_shoulder_lift.pos"] > 100
    assert values["right_shoulder_lift.pos"] < -100
    assert source.last_raw["left"]["shoulder_lift"] == 3001
    with pytest.raises(ValueError):
        m.FrameProducer(source).capture({"grant_id": str(uuid4())})


def test_workload_emits_leader_format_freezes_progress_and_finishes_all_active_phases():
    name = "examples.alohamini.am1_teleop_workload"
    assert importlib.util.find_spec(name), "representative virtual leader workload is missing"
    m = importlib.import_module(name)
    task = m.TeleopWorkload(workspace())
    task.admit(10.0)
    task.advance(10.1)
    before = task.elapsed_s
    task.freeze()
    task.advance(50.0)
    assert task.elapsed_s == before
    task.admit(50.0)
    for i in range(1801):
        task.advance(50.1 + i * 0.1)
    assert task.complete and task.elapsed_s == 172.5
    assert task.phases_completed == {"individual", "combined", "body"}
    assert set(task.get_action()) == {k.removeprefix("arm_") for k in KEYS}
    assert task.body_phase_elapsed is None


def test_real_verified_https_wss_client_reuses_exact_ip_cookie_and_generation(tmp_path):
    import asyncio
    import json

    from tests.robots.am1_session_fixture import Cluster
    from tests.robots.test_am1_session_api import enrolled

    m = adapter()
    cluster = Cluster(tmp_path / "cluster")

    async def scenario():
        session = await enrolled(cluster)
        try:
            token = next(c.value for c in session.cookie_jar if c.key == "__Host-am1-device")
        finally:
            await session.close()
        storage = tmp_path / "storage.json"
        storage.write_text(
            json.dumps(
                {
                    "cookies": [
                        {
                            "name": "__Host-am1-device",
                            "value": token,
                            "domain": "127.0.0.1",
                            "path": "/",
                            "secure": True,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        client = m.SessionClient(cluster.url, cluster.cert, storage)
        try:
            await client.attach()
            await asyncio.sleep(0.25)  # Publisher snapshots omit an already-ACKed revision.
            await client.claim()
            assert client.generation
            assert client.snapshot["service_incarnation"]
        finally:
            await client.close()
        assert client.transport_error is None
        assert client.transport_close["expected"] is True

    try:
        asyncio.run(scenario())
    finally:
        cluster.close()


def test_terminal_ack_requires_current_source_and_actual_body_zero():
    import copy

    m = adapter()
    source = {"source_id": str(uuid4()), "sample_seq": 9, "grant_id": str(uuid4())}
    zero = {"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 0}
    snapshot = {
        "run": {"live_input": {"last_input_seq": 7, "source": source, "body": zero}},
        "evidence": {
            "active_acknowledged": True,
            "feedback": True,
            "native_ack": True,
            "measured_body": zero,
        },
    }
    assert m.native_frame_ack(snapshot, 7, source, body_zero=True)
    for mutation in ("old_source", "moving", "missing_feedback", "requested_motion"):
        bad = copy.deepcopy(snapshot)
        if mutation == "old_source":
            bad["run"]["live_input"]["source"]["sample_seq"] -= 1
        elif mutation == "moving":
            bad["evidence"]["measured_body"]["x.vel"] = 0.1
        elif mutation == "missing_feedback":
            bad["evidence"]["measured_body"].pop("lift_axis.vel")
        else:
            bad["run"]["live_input"]["body"]["theta.vel"] = 1
        assert not m.native_frame_ack(bad, 7, source, body_zero=True)


def test_real_wss_clean_close_ends_qualified_wait_before_timeout(tmp_path):
    import asyncio
    import json

    from tests.robots.am1_session_fixture import Cluster
    from tests.robots.test_am1_session_api import enrolled

    m = adapter()
    cluster = Cluster(tmp_path / "cluster")

    async def scenario():
        session = await enrolled(cluster)
        try:
            token = next(cookie.value for cookie in session.cookie_jar if cookie.key == "__Host-am1-device")
        finally:
            await session.close()
        storage = tmp_path / "storage.json"
        storage.write_text(
            json.dumps(
                {
                    "cookies": [
                        {
                            "name": "__Host-am1-device",
                            "value": token,
                            "domain": "127.0.0.1",
                            "path": "/",
                            "secure": True,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        client = m.SessionClient(cluster.url, cluster.cert, storage)
        try:
            await client.attach()
            await client.ws.close()
            await asyncio.wait_for(client.reader, 1)
            with pytest.raises(ConnectionError, match="WSS|connection"):
                await client.wait(lambda: False, 0.15)
            assert client.transport_error and client.transport_close["expected"] is False
            assert "close_code" in client.transport_close  # Handshake may complete after reader exit.
            await client.attach()
            assert client.transport_error is None and client.transport_close is None
        finally:
            await client.close()

    try:
        asyncio.run(scenario())
    finally:
        cluster.close()


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "stop",
        "renew",
        "start_lost",
        "start_unknown",
        "terminal_fault",
        "admission_fault",
        "admission_terminal",
        "terminal_stop_race",
        "foreign_terminal",
        "expired_first",
        "expired_zero",
        "expired_zero_timeout",
        "expired_next_budget",
        "expired_usb_budget",
        "expired_foreign_budget",
        "recovery",
        "recovery_fault",
        "recovery_wall",
        "zero_deadline_rounding",
        "zero_inner_timeout",
    ],
)
def test_real_runner_waits_for_exact_start_from_idle_null_run_and_stops_owned_id(
    tmp_path, monkeypatch, failure
):
    import asyncio
    import copy
    from types import SimpleNamespace

    m = adapter()
    prelive = {"admission_fault", "admission_terminal", "terminal_stop_race", "foreign_terminal"}
    dropped = failure in {
        "expired_first",
        "expired_zero",
        "expired_zero_timeout",
        "expired_next_budget",
        "expired_usb_budget",
        "expired_foreign_budget",
    }
    wall_budget = failure in {"expired_next_budget", "expired_usb_budget"}
    foreign_boundary = failure == "expired_foreign_budget"
    recovery_case = failure in {"recovery", "recovery_fault", "recovery_wall"}
    pc_clock = [10.0]
    if recovery_case or failure in {"zero_deadline_rounding", "zero_inner_timeout"}:
        import time

        # Advance only the PC's program clock, leaving asyncio scheduling and
        # original perf_counter source acquisition clocks untouched. Held reads
        # cost .05s each; accepted inputs cost .01s. A missing freeze/rebaseline
        # therefore credits the held interval and fails the exact progress test.
        monkeypatch.setattr(
            m, "time", SimpleNamespace(monotonic=lambda: pc_clock[0], perf_counter=time.perf_counter)
        )
    owned_id, foreign_id = str(uuid4()), str(uuid4())
    instances = []

    class Session:
        def __init__(self, *args):
            self.snapshot = {"run": None, "evidence": {}}
            self.sequence = 0
            self.generation = "control"
            self.device, self.connection = "device", "connection"
            self.starts = self.closed = self.saw_foreign = 0
            self.stops = []
            self.start_requests = []
            self.cleanup_reads = 0
            self.inputs = []
            self.recovery_reads = 0
            self.authoritative = None
            self.resumes = 0
            instances.append(self)

        async def attach(self):
            pass

        async def claim(self):
            pass

        async def renew(self):
            if failure == "renew":
                from aiohttp import ServerDisconnectedError

                raise ServerDisconnectedError("injected renewal transport failure")
            await asyncio.Event().wait()

        def state(self, run_id):
            w = workspace()
            return {
                "service_incarnation": "service",
                "controller": {"device_id": "device", "controller_generation": "control"},
                "run": {
                    "run_id": run_id,
                    "recipe": "physical-live-teleop",
                    "native_live_at": 1.0,
                    "deadline": 301.0,
                    "status": "running",
                    "uncertain": False,
                    "cleanup": None,
                    "backend_incarnation": "host",
                    "intent_revision": 2,
                    "recovery_episodes": 0,
                    "first_cause": None,
                    "live_input": {
                        "workspace": w,
                        "reference": copy.deepcopy(w["reference"]),
                        "cursor": copy.deepcopy(w["reference"]),
                        "required_view": {"qualified": True},
                        "cursor_intent_revision": 2,
                    },
                },
                "evidence": {
                    "active_acknowledged": True,
                    "feedback": True,
                    "native_ack": True,
                    "required_observation": True,
                    "hold_acknowledged": True,
                    "pose_aligned": True,
                    "acknowledged_intent_revision": 2,
                    "accepted_host_action": {
                        "run_id": run_id,
                        "host_incarnation": "host",
                        "intent_revision": 2,
                        "state": "paused",
                    },
                    "measured_positions": w["reference"],
                    "measured_body": {"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 0},
                },
            }

        async def post(self, op, payload):
            if op == "start":
                self.starts = 1  # Simulated durable server deduplicates this operation.
                self.start_requests.append(copy.deepcopy(payload))
                if failure == "start_unknown" or failure == "start_lost" and len(self.start_requests) == 1:
                    from aiohttp import ServerDisconnectedError

                    raise ServerDisconnectedError("injected Start accepted but response lost")
                return {"accepted": True, "run_id": owned_id}
            assert op == "stop"
            self.stops.append(payload["run_id"])
            if failure == "stop":
                from aiohttp import ClientOSError

                raise ClientOSError(5, "injected Stop transport failure")
            self.snapshot = self.state(owned_id)
            self.snapshot["run"].update(
                status="faulted" if failure in {"terminal_fault", *prelive} else "stopped",
                cleanup="unknown" if failure == "admission_fault" else "physical_readback_verified",
                uncertain=failure == "admission_fault",
                first_cause="retained genuine native fault"
                if failure in {"terminal_fault", *prelive}
                else None,
            )
            if failure in prelive:
                self.snapshot["run"].update(
                    native_live_at=None, native_first_cause="injected original pre-Live native fault"
                )
            if failure in {"admission_terminal", "terminal_stop_race"}:
                from tools.am1_session_core import SessionAuthority

                authority = SessionAuthority(tmp_path / "stop-authority")
                try:
                    authority.run = copy.deepcopy(self.snapshot["run"])
                    result = authority._handle({"op": "stop", **payload}, "owner")
                    assert result["accepted"] is False and result["reason"] == "terminal"
                    raise RuntimeError(result["reason"])
                finally:
                    authority.close()
            return {"accepted": True, "status": "stopped"}

        async def wait(self, predicate, timeout):
            if self.snapshot["run"] is None:
                assert not predicate()
                self.snapshot = self.state(foreign_id)
                if failure in prelive:
                    self.snapshot["run"].update(
                        status="faulted",
                        native_live_at=None,
                        native_first_cause="another run's historical fault",
                    )
                assert not predicate(), "same recipe on another run is not this accepted Start"
                self.saw_foreign += 1
                self.snapshot = self.state(owned_id)
                if failure in prelive:
                    self.snapshot["run"].update(
                        status="faulted",
                        native_live_at=None,
                        first_cause="generic owning layer failure",
                        native_first_cause="injected original pre-Live native fault",
                        cleanup="unknown" if failure == "admission_fault" else "physical_readback_verified",
                        uncertain=failure == "admission_fault",
                    )
            assert predicate()

        def identities(self):
            return {"run_id": self.snapshot["run"]["run_id"] if foreign_boundary else owned_id}

        async def resume(self, *, check=None):
            assert self.snapshot["run"]["run_id"] == owned_id
            if check:
                check(self.snapshot)
            if recovery_case:
                assert self.snapshot["run"]["status"] != "recovering"
                self.resumes += 1
                self.snapshot["run"]["status"] = "running"
                self.snapshot["run"]["intent_revision"] += 2
                self.snapshot["run"]["live_input"]["cursor_intent_revision"] = self.snapshot["run"][
                    "intent_revision"
                ]
                self.connection = str(uuid4())
                self.sequence = 0

        async def command(self, payload):
            if payload["op"] == "grant":
                if (
                    recovery_case
                    and self.authoritative is None
                    and (self.inputs or failure in {"recovery", "recovery_wall"})
                ):
                    self.authoritative = copy.deepcopy(self.snapshot)
                    self.authoritative["run"].update(
                        status="recovering",
                        first_cause="feedback_loss",
                        recovery_episodes=1,
                        remaining_s=296.0,
                        recovery={
                            "started": 5.0,
                            "ceiling": 5.4,
                            "revision": self.snapshot["run"]["intent_revision"],
                        },
                    )
                    raise m.CommandRefusedError(
                        {
                            "accepted": False,
                            "status": "refused",
                            "reason": "interactive running required",
                            "effect_admitted": False,
                        }
                    )
                return {"accepted": True, "grant_id": str(uuid4()), "expires": 99.0}
            assert payload["op"] == "input"
            self.inputs.append(copy.deepcopy(payload))
            if len(self.inputs) >= 2 and failure == "zero_deadline_rounding":
                await asyncio.sleep(10)  # Owned2s timer expires while coarse PC clock has not ticked.
            if len(self.inputs) >= 2 and failure == "zero_inner_timeout":
                raise TimeoutError("injected inner command timeout before qualification budget")
            expired_at = 1 if failure == "expired_first" or foreign_boundary else 2
            if dropped and (
                len(self.inputs) == expired_at
                or failure == "expired_zero_timeout"
                and len(self.inputs) >= 2
                or wall_budget
            ):
                await asyncio.sleep(0.04)
                if foreign_boundary:
                    self.snapshot = self.state(foreign_id)
                raise m.CommandRefusedError(
                    {
                        "accepted": False,
                        "status": "refused",
                        "reason": "expired grant",
                        "effect_admitted": False,
                    }
                )
            if dropped or recovery_case:
                await asyncio.sleep(0.01)
            if recovery_case or failure in {"zero_deadline_rounding", "zero_inner_timeout"}:
                pc_clock[0] += 0.01
            self.snapshot["run"]["live_input"].update(
                last_input_seq=payload["seq"], source=payload["source"], body=payload["body"]
            )
            self.snapshot["evidence"]["measured_positions"] = payload["target"]
            return {"accepted": True}

        async def get(self, op):
            assert op == "state"
            self.cleanup_reads += 1
            if recovery_case and self.authoritative is not None and not self.stops:
                pc_clock[0] += 0.05
                self.recovery_reads += 1
                self.snapshot = copy.deepcopy(self.authoritative)
                if self.recovery_reads > 1 and failure != "recovery_wall":
                    revision = self.snapshot["run"]["intent_revision"] + 1
                    self.snapshot["run"].update(status="paused", recovery=None, intent_revision=revision)
                    self.snapshot["run"]["live_input"]["cursor_intent_revision"] = revision
                    self.snapshot["evidence"]["acknowledged_intent_revision"] = revision
                    self.snapshot["evidence"]["accepted_host_action"]["intent_revision"] = revision
                    self.snapshot["events"] = [
                        {
                            "kind": "live_sensing_requalified",
                            "at": 5.1,
                            "run_id": owned_id,
                            "service_incarnation": "service",
                        }
                    ]
                    if failure == "recovery_fault":
                        self.snapshot["evidence"]["fault"] = "injected genuine native fault"
                return {"snapshot": self.snapshot}
            if failure == "foreign_terminal":
                foreign = copy.deepcopy(self.snapshot)
                foreign["run"]["run_id"] = foreign_id
                return {"snapshot": foreign}
            if failure == "terminal_stop_race" and self.cleanup_reads == 1:
                prior = copy.deepcopy(self.snapshot)
                prior["run"].update(status="finishing", uncertain=True)
                return {"snapshot": prior}
            return {"snapshot": self.snapshot}

        async def close(self):
            self.closed += 1

    monkeypatch.setattr(m, "SessionClient", Session)
    sources = []

    class Input:
        kind = "usb-leaders"

        def __init__(self, **kwargs):
            self.closed = 0
            sources.append(self)

        def open(self):
            pass

        def get_action(self):
            return {k.removeprefix("arm_"): v for k, v in workspace()["reference"].items()}

        def close(self):
            self.closed += 1

    monkeypatch.setattr(m, "USBLeaderSource", Input)
    args = SimpleNamespace(
        source="usb-leaders"
        if failure and not recovery_case and (not dropped or failure == "expired_usb_budget")
        else "virtual-leaders",
        output=str(tmp_path / "evidence"),
        url="unused",
        ca="unused",
        storage_state="unused",
        left_port="unused",
        right_port="unused",
        leader_id="unused",
        calibration_dir="unused",
        no_keyboard=True,
        duration_seconds=180.0,
        next_start_seconds=0.001,
        interrupt_at_seconds=None,
        interruption_seconds=0.65,
    )
    if failure:
        args.duration_seconds = 0.001
    if dropped:
        args.next_start_seconds = None
    if failure == "expired_next_budget":
        args.next_start_seconds = 3.0
    if failure == "expired_usb_budget":
        args.duration_seconds = 0.03
    if foreign_boundary:
        args.next_start_seconds = 0.001
    if recovery_case:
        args.next_start_seconds = 0.03 if failure == "recovery_wall" else None
        args.duration_seconds = 0.15 if failure == "recovery_fault" else 0.015
        if failure == "recovery":
            summary = asyncio.run(m.run(args))
        else:
            with pytest.raises((RuntimeError, TimeoutError)):
                asyncio.run(asyncio.wait_for(m.run(args), 20.0))
            import json

            summary = json.loads((tmp_path / "evidence" / "summary.json").read_text())
        assert summary["producer_program_complete"] is False
        assert summary["active_progress_s"] == pytest.approx(
            0.02 if failure == "recovery" else 0.01 if failure == "recovery_fault" else 0
        )
        assert summary["frame_count"] == (
            2 if failure == "recovery" else 0 if failure == "recovery_wall" else 1
        )
        assert instances[0].resumes == (2 if failure == "recovery" else 1)
        assert instances[0].stops == [owned_id]
        if failure == "recovery":
            assert summary["qualified_native_recovery_episodes"] == 1
            assert len({item["source"]["source_id"] for item in instances[0].inputs}) == 1
            assert [item["source"]["sample_seq"] for item in instances[0].inputs] == [1, 2, 3]
        return
    elif (
        failure
        and failure != "start_lost"
        and (not dropped or failure == "expired_zero_timeout" or wall_budget or foreign_boundary)
    ):
        with pytest.raises(
            Exception, match="injected|cleanup failed|native terminal|body-zero|identity/deadline"
        ):
            asyncio.run(
                # Includes local cold imports (measured7.632s), the selected
                # wall budget, fixed2s zero attempts and bounded cleanup.
                asyncio.wait_for(m.run(args), 20.0) if wall_budget else m.run(args)
            )
        import json

        summary = json.loads((tmp_path / "evidence" / "summary.json").read_text())
        if args.source == "usb-leaders":
            assert sources[0].closed == 1
        if failure != "start_unknown":
            assert summary["owned_run_id"] == owned_id
        assert (
            "injected" in str(summary)
            or failure in {"terminal_fault", "expired_zero_timeout", "zero_deadline_rounding"}
            or wall_budget
            or foreign_boundary
        )
        if failure == "stop":
            assert summary["native_cleanup_uncertain"] is True
        if failure == "start_unknown":
            assert summary["start_outcome_uncertain"] is True and summary["start_operation_id"]
        if failure == "terminal_fault":
            assert summary["native_cleanup_uncertain"] is False and summary["cleanup_errors"] == []
            assert summary["terminal_failure"]["cause"] == "retained genuine native fault"
        if failure == "zero_deadline_rounding":
            assert summary["failure_type"] == "RuntimeError" and "body-zero" in summary["failure"]
            assert summary["native_cleanup_uncertain"] is False
        if failure == "zero_inner_timeout":
            assert summary["failure_type"] == "TimeoutError" and "injected inner" in summary["failure"]
        if failure in prelive:
            assert summary["native_admission_terminal"]["run"]["run_id"] == owned_id
            assert summary["failure"] == "native admission faulted: injected original pre-Live native fault"
            assert summary["native_cleanup_uncertain"] is (failure in {"admission_fault", "foreign_terminal"})
            if failure in {"admission_fault", "foreign_terminal"}:
                assert summary["cleanup_errors"][0]["step"] == "native_stop"
                if failure == "foreign_terminal":
                    assert "another run" in summary["cleanup_errors"][0]["message"]
                    assert "native_terminal" not in summary
            else:
                assert summary["cleanup_errors"] == []
                assert (
                    summary["native_terminal"]["run"]["native_first_cause"]
                    == "injected original pre-Live native fault"
                )
            assert "run_id" not in summary and "final_native_ack" not in summary
    else:
        summary = asyncio.run(m.run(args))
    session = instances[0]
    assert (
        session.starts == 1
        and session.stops
        == (
            []
            if failure in {"start_unknown", "admission_fault", "admission_terminal", "foreign_terminal"}
            or foreign_boundary
            else [owned_id]
        )
        and session.closed == 1
    )
    assert session.saw_foreign == (0 if failure == "start_unknown" else 1)
    if failure not in {"start_unknown", *prelive}:
        assert summary["run_id"] == owned_id
    if failure not in {"stop", "start_unknown", "terminal_fault", *prelive} and not foreign_boundary:
        assert summary["native_terminal"]["run"]["status"] == "stopped"
    if failure in {"start_lost", "start_unknown"}:
        assert len(session.start_requests) == 2 and session.start_requests[0] == session.start_requests[1]
        assert summary["start_operation_id"] == session.start_requests[0]["operation_id"]
    assert summary["producer_program_complete"] is False
    if not failure:
        assert summary["active_progress_s"] == 0
    if dropped:
        import json

        records = [
            json.loads(line) for line in (tmp_path / "evidence" / "frames.jsonl").read_text().splitlines()
        ]
        rejected = [row for row in records if row["event"] == "leader_frame_rejected"]
        assert len(rejected) >= 1 and all(row["discarded"] is True for row in rejected)
        if failure != "expired_zero_timeout" and not wall_budget:
            assert len(rejected) == 1
        assert rejected[0]["receipt"]["reason"] == "expired grant"
        assert len({item["source"]["source_id"] for item in session.inputs}) == 1
        assert [item["source"]["sample_seq"] for item in session.inputs] == list(
            range(1, len(session.inputs) + 1)
        )
        assert len({item["grant_id"] for item in session.inputs}) == len(session.inputs)
        assert all(
            item["source"]["grant_received_at_monotonic_s"] <= item["source"]["acquired_at_monotonic_s"]
            for item in session.inputs
        )
        if foreign_boundary:
            assert len(session.inputs) == 1 and session.inputs[0]["run_id"] == owned_id
            assert "identity/deadline changed" in summary["failure"]
            assert "native_terminal" not in summary and summary["native_cleanup_uncertain"] is True
            assert "another run" in summary["cleanup_errors"][0]["message"]
            return
        if wall_budget:
            attempts = [row for row in records if row["event"] == "leader_frame_attempt"]
            zero = [row for row in attempts if row["phase"] == "terminal_zero_qualification"]
            assert zero and zero[0]["local_monotonic_s"] - attempts[0]["local_monotonic_s"] < (
                3.2 if failure == "expired_next_budget" else 0.2
            )
            assert records[-1]["local_monotonic_s"] - zero[0]["local_monotonic_s"] < 2.15
            assert not [row for row in records if row["event"] == "leader_frame"]
            assert summary["producer_program_complete"] is False and summary["cleanup_errors"] == []
            assert "body-zero" in summary["failure"]
            return
        if failure == "expired_zero_timeout":
            assert all(row["phase"] == "terminal_zero_qualification" for row in rejected)
            assert 1.8 < records[-1]["local_monotonic_s"] - rejected[0]["local_monotonic_s"] < 2.1
            assert "body-zero" in summary["failure"]
            assert summary["cleanup_errors"] == []
            return
        assert 0 < summary["active_progress_s"] < 0.04
        assert summary["original_native_deadline"] == 301.0
        assert summary["cleanup_errors"] == [] and summary["final_body_zero"] is True


def test_runner_constructor_failure_still_saves_first_cause_without_start(tmp_path, monkeypatch):
    import asyncio
    import json
    from types import SimpleNamespace

    m = adapter()

    def refused(*args):
        raise ValueError("injected existing CA/cookie refusal")

    monkeypatch.setattr(m, "SessionClient", refused)
    args = SimpleNamespace(
        source="virtual-leaders",
        output=str(tmp_path / "evidence"),
        url="unused",
        ca="unused",
        storage_state="unused",
    )
    with pytest.raises(ValueError, match="injected existing CA"):
        asyncio.run(m.run(args))
    summary = json.loads((tmp_path / "evidence" / "summary.json").read_text())
    assert summary["failure"] == "injected existing CA/cookie refusal"
    assert not summary.get("owned_run_id") and not summary.get("stop_receipt")


@pytest.mark.parametrize("kind", ["virtual-leaders", "usb-leaders"])
def test_cold_local_initialization_finishes_before_live_wss_is_attached(tmp_path, monkeypatch, kind):
    import asyncio
    import builtins
    import json
    import time
    from types import SimpleNamespace

    m = adapter()
    state = {
        "attached": False,
        "cold_imported": False,
        "source_opened": False,
        "source_closed": False,
        "client_closed": False,
        "claim_calls": 0,
    }
    actual_import = builtins.__import__

    def cold_import(name, *args, **kwargs):
        if name == "lerobot.robots.alohamini.alohamini_client":
            if state["attached"]:
                raise RuntimeError("cold mapper import blocked the active WSS reader")
            time.sleep(0.03)  # Deterministic stand-in for the measured7.632s cold import.
            state["cold_imported"] = True
        return actual_import(name, *args, **kwargs)

    class Session:
        def __init__(self, *args):
            pass

        async def attach(self):
            state["attached"] = True

        async def claim(self):
            state["claim_calls"] += 1
            assert state["cold_imported"]
            assert kind != "usb-leaders" or state["source_opened"]
            raise RuntimeError("injected stop at first current claim before Start")

        async def close(self):
            state["client_closed"] = True

    class Input:
        def __init__(self, **kwargs):
            pass

        def open(self):
            assert not state["attached"]
            state["source_opened"] = True

        def close(self):
            state["source_closed"] = True

    monkeypatch.setattr(m, "SessionClient", Session)
    monkeypatch.setattr(m, "USBLeaderSource", Input)
    monkeypatch.setattr(builtins, "__import__", cold_import)
    args = SimpleNamespace(
        source=kind,
        output=str(tmp_path / "evidence"),
        url="unused",
        ca="unused",
        storage_state="unused",
        left_port="unused",
        right_port="unused",
        leader_id="unused",
        calibration_dir="unused",
        no_keyboard=True,
    )
    with pytest.raises(RuntimeError, match="injected stop at first current claim"):
        asyncio.run(m.run(args))
    summary = json.loads((tmp_path / "evidence" / "summary.json").read_text())
    assert state["cold_imported"] and state["attached"] and state["client_closed"]
    assert state["claim_calls"] == 1
    assert state["source_closed"] is (kind == "usb-leaders")
    assert "start_operation_id" not in summary and "owned_run_id" not in summary
    assert summary["cleanup_errors"] == []


def recovery_state():
    """Actual public fields; Pi clocks remain separate from the PC clock."""
    import copy

    w = workspace()
    return {
        "service_incarnation": "service",
        "controller": {"device_id": "device", "controller_generation": "control"},
        "events": [],
        "run": {
            "run_id": "owned",
            "recipe": "physical-live-teleop",
            "backend_incarnation": "host",
            "native_live_at": 100.0,
            "deadline": 400.0,
            "remaining_s": 299.99,
            "intent_revision": 2,
            "status": "running",
            "uncertain": False,
            "first_cause": None,
            "native_first_cause": None,
            "recovery": None,
            "recovery_episodes": 0,
            "live_input": {
                "workspace": w,
                "reference": copy.deepcopy(w["reference"]),
                "cursor": copy.deepcopy(w["reference"]),
                "cursor_intent_revision": 2,
                "required_view": {"qualified": True},
            },
        },
        "evidence": {
            "feedback": True,
            "required_observation": True,
            "hold_acknowledged": True,
            "pose_aligned": True,
            "native_ack": True,
            "active_acknowledged": True,
            "fault": None,
            "acknowledged_intent_revision": 2,
            "accepted_host_action": {
                "run_id": "owned",
                "host_incarnation": "host",
                "intent_revision": 2,
                "state": "paused",
            },
            "measured_positions": copy.deepcopy(w["reference"]),
            "measured_body": dict(adapter().ZERO_BODY),
        },
    }


def recovering_state():
    state = recovery_state()
    state["run"].update(
        status="recovering",
        first_cause="feedback_loss",
        recovery_episodes=1,
        recovery={"started": 100.0, "ceiling": 100.4, "revision": 2},
    )
    state["evidence"].update(feedback=False, hold_acknowledged=False)
    return state


def requalified_state():
    state = recovery_state()
    state["run"].update(
        status="paused",
        first_cause="feedback_loss",
        recovery_episodes=1,
        intent_revision=3,
        remaining_s=299.9,
    )
    state["run"]["live_input"]["cursor_intent_revision"] = 3
    state["evidence"]["acknowledged_intent_revision"] = 3
    state["evidence"]["accepted_host_action"]["intent_revision"] = 3
    state["events"] = [
        {"kind": "live_sensing_requalified", "at": 100.1, "run_id": "owned", "service_incarnation": "service"}
    ]
    return state


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "fault",
        "uncertain",
        "foreign",
        "reference",
        "workspace",
        "deadline",
        "live",
        "ownership",
        "missing_descriptor",
        "ceiling",
        "revision",
        "quota",
        "old_event",
        "old_ack",
        "changed_ceiling",
    ],
)
def test_same_run_recovery_waits_original_episode_then_current_held_proof(mutation):
    import asyncio
    import copy

    m = adapter()
    records, commands = [], []
    first, last = recovering_state(), requalified_state()
    if mutation == "fault":
        first["evidence"]["fault"] = "real native fault"
    elif mutation == "uncertain":
        first["run"]["uncertain"] = True
    elif mutation == "foreign":
        first["run"]["run_id"] = "foreign"
    elif mutation == "reference":
        last["run"]["live_input"]["reference"][KEYS[0]] += 1
    elif mutation == "workspace":
        last["run"]["live_input"]["workspace"]["reference"][KEYS[0]] += 1
    elif mutation == "deadline":
        first["run"]["deadline"] += 1
    elif mutation == "live":
        first["run"]["native_live_at"] += 1
    elif mutation == "ownership":
        first["controller"]["controller_generation"] = "other"
    elif mutation == "missing_descriptor":
        first = last
    elif mutation == "ceiling":
        first["run"]["remaining_s"] = 299.5
    elif mutation == "revision":
        first["run"]["recovery"]["revision"] = 1
    elif mutation == "quota":
        first["run"]["recovery_episodes"] = 4
    elif mutation == "old_event":
        last["events"][0]["at"] = 99.0
    elif mutation == "old_ack":
        last["evidence"]["accepted_host_action"]["intent_revision"] = 2
    elif mutation == "changed_ceiling":
        last = copy.deepcopy(first)
        last["run"]["recovery"]["ceiling"] += 1

    class Client:
        snapshot = recovery_state()
        device, generation, connection = "device", "control", "connection"
        reads = 0

        async def get(self, path):
            assert path == "state"
            self.reads += 1
            self.snapshot = copy.deepcopy(first if self.reads == 1 else last)
            return {"snapshot": self.snapshot}

        async def resume(self, *, check=None):
            assert self.reads >= 2 and self.snapshot["run"]["status"] == "paused"
            assert check is not None
            check(self.snapshot)
            commands.append("connect/release/resume")
            self.snapshot["run"]["status"] = "running"

    async def scenario():
        client = Client()
        guard = m.SameRunRecovery(client, records.append)
        if mutation:
            with pytest.raises((RuntimeError, TimeoutError, ValueError)):
                await guard.resume()
            assert commands == []
        else:
            assert await guard.resume() is True
            assert commands == ["connect/release/resume"]
            assert guard.history_qualified(client.snapshot)
            assert guard.episodes == 1
            assert records[-1]["event"] == "qualified_native_recovery"
            assert records[-1]["original_recovery"] == first["run"]["recovery"]
            # Sticky history permits a later ordinary view/input pause only
            # after this exact episode has qualified, without resetting quota.
            client.snapshot["run"]["status"] = "paused"
            assert guard.history_qualified(client.snapshot)
            client.snapshot["run"]["recovery_episodes"] = 0
            assert not guard.history_qualified(client.snapshot)

    asyncio.run(asyncio.wait_for(scenario(), 0.8))


@pytest.mark.parametrize("mutation", ["manual", "foreign_event", "old_revision", "episode_jump", "quota"])
def test_grant_race_only_rejoins_current_ordinary_pause_with_unchanged_qualified_history(mutation):
    import asyncio
    import copy

    m = adapter()
    paused = recovery_state()
    paused["run"].update(status="paused", intent_revision=3)
    paused["events"] = [
        {"kind": "required_view_expired", "at": 100.01, "run_id": "owned", "service_incarnation": "service"}
    ]
    if mutation == "manual":
        paused["events"][0]["kind"] = "pause"
    elif mutation == "foreign_event":
        paused["events"][0]["run_id"] = "foreign"
    elif mutation == "old_revision":
        paused["run"]["intent_revision"] = 2
    elif mutation == "episode_jump":
        paused["run"].update(recovery_episodes=1, first_cause="feedback_loss")

    class Client:
        snapshot = recovery_state()
        device, generation, connection = "device", "control", "connection"

        async def get(self, path):
            return {"snapshot": copy.deepcopy(paused)}

        async def resume(self, **kwargs):
            pytest.fail("an unsupported paused state must never connect/release/resume")

    async def scenario():
        guard = m.SameRunRecovery(Client(), lambda value: None)
        if mutation == "quota":
            guard.pause_recoveries = 3
        with pytest.raises(RuntimeError, match="cause-specific pause"):
            await guard.resume(require_episode=True, refusal_identity={"intent_revision": 2})

    asyncio.run(scenario())


def test_original_recovery_ceiling_includes_authenticated_read_time_without_reanchoring():
    import asyncio

    m = adapter()

    class Client:
        snapshot = recovery_state()
        device, generation, connection = "device", "control", "connection"

        async def get(self, path):
            await asyncio.sleep(0.08)
            state = recovering_state()
            state["run"].update(
                remaining_s=299.9, recovery={"started": 100.0, "ceiling": 100.15, "revision": 2}
            )
            return {"snapshot": state}

        async def resume(self, **kwargs):
            pytest.fail("the original episode budget elapsed during the authenticated read")

    async def scenario():
        guard = m.SameRunRecovery(Client(), lambda value: None)
        with pytest.raises(TimeoutError, match="original native recovery ceiling"):
            await guard.resume()

    asyncio.run(asyncio.wait_for(scenario(), 0.3))


@pytest.mark.parametrize("cause", ["feedback", "view", "server_connection_replaced"])
def test_real_core_ipc_tls_wss_recovery_requires_qualified_pause_before_explicit_resume(tmp_path, cause):
    import asyncio
    import copy
    import json
    import ssl
    import subprocess

    from aiohttp import web

    from tests.robots.am1_session_fixture import OPENSSL
    from tests.robots.test_am1_live_teleop_executor import authority_lane
    from tools.am1_session_ipc import OwnerServer
    from tools.am1_session_service import Gateway

    m = adapter()
    a, e, ids = authority_lane(tmp_path / "authority")
    a.run.update(backend_incarnation=e.host_incarnation, recovery_episodes=0, status="paused")
    a._teleop_view = {"run_id": e.run_id, "qualified_until": 10.5, "role": "chest"}
    healthy, epoch, commands = [True], [0], []

    def evidence():
        value = recovery_state()["evidence"]
        revision = a.run["intent_revision"]
        active = e.desired == "active"
        live = copy.deepcopy(recovery_state()["run"]["live_input"])
        live.update(
            state="active" if active else "paused",
            cursor_intent_revision=revision,
            live_admitted_at_monotonic_s=9.0,
            live_deadline_monotonic_s=309.0,
        )
        if e._live_target:
            live.update(
                last_input_seq=e._live_target["seq"],
                source=e._live_target["source"],
                body=e._live_target["body"],
            )
        value.update(
            feedback=healthy[0],
            hold_acknowledged=not active,
            active_acknowledged=active,
            acknowledged_intent_revision=revision,
            live_input=live,
            accepted_host_action={
                "run_id": e.run_id,
                "host_incarnation": e.host_incarnation,
                "intent_revision": revision,
                "state": live["state"],
                "epoch": epoch[0],
            },
        )
        return value

    e.evidence = evidence  # Explicit fake native sensing/ACK; Core/IPC/gateway/client are production.

    def dispatch(recipe):
        e.desired = "active"
        epoch[0] += 1
        return True

    e.dispatch = dispatch
    real_handle = a.handle

    def handle(command, device):
        commands.append((command["op"], a.run["status"]))
        if command["op"] == "connect":
            assert a.run["status"] == "paused", "connect must not short-circuit Core recovery"
            if a.run["recovery_episodes"]:
                assert any(v["kind"] == "live_sensing_requalified" for v in a.events)
        return real_handle(command, device)

    a.handle = handle
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            OPENSSL,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=IP:127.0.0.1,DNS:localhost",
        ],
        check=True,
        capture_output=True,
    )

    async def scenario():
        owner = OwnerServer(tmp_path / "owner")
        owner.authority.close()
        owner.authority = a
        owner.stopping.set()  # Test explicitly advances the real Core, without any device worker.
        gateway = Gateway(owner.directory, tmp_path / "auth")
        enrolled = gateway.auth.enroll(gateway.auth.pair(control=True))
        a.controller["device_id"] = a.connection["device_id"] = enrolled["device_id"]
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        runner = web.AppRunner(gateway.app, access_log=None, shutdown_timeout=1)
        client, qualification = None, None
        records, entered = [], asyncio.Event()

        def record(value):
            records.append(copy.deepcopy(value))
            if value["event"] == "native_recovery_wait":
                entered.set()

        try:
            await owner.start()
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=tls)
            await site.start()
            gateway.origin = f"https://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
            storage = tmp_path / "storage.json"
            storage.write_text(
                json.dumps(
                    {
                        "cookies": [
                            {
                                "name": "__Host-am1-device",
                                "value": enrolled["token"],
                                "domain": "127.0.0.1",
                                "path": "/",
                                "secure": True,
                            }
                        ]
                    }
                )
            )
            client = m.SessionClient(gateway.origin, cert, storage)
            await client.attach()
            client.generation, client.connection = ids["controller_generation"], ids["connection_generation"]
            guard = m.SameRunRecovery(client, record)
            await guard.resume()
            initial_connects = sum(op == "connect" for op, _ in commands)

            class Input:
                kind = "virtual-leaders"

                def get_action(self):
                    return {k.removeprefix("arm_"): v for k, v in workspace()["reference"].items()}

            producer = m.FrameProducer(Input())
            producer.workspace, producer.native_cursor = workspace(), workspace()["reference"]
            first = await m.send_leader_sample(
                client, producer, m.ZERO_BODY, record, phase="individual", progress_s=0.02
            )
            assert first[2]["accepted"] is True and producer.sequence == 1
            stale = copy.deepcopy(client.snapshot)
            assert stale["run"]["status"] == "running"
            if cause == "view":
                a._teleop_view["qualified_until"] = 9.0
            else:
                healthy[0] = False  # Core tick inside grant, before any newer published snapshot.
            with pytest.raises(m.CommandRefusedError) as caught:
                await m.send_leader_sample(
                    client, producer, m.ZERO_BODY, record, phase="individual", progress_s=0.02
                )
            assert m.recovery_grant_refusal(caught.value)
            assert a.run["status"] == ("paused" if cause == "view" else "recovering")
            assert producer.sequence == 1
            assert records[-1]["event"] == "leader_grant_rejected"

            async def qualify():
                await entered.wait()
                await asyncio.sleep(0.06)
                assert a.run["status"] == "recovering"
                assert sum(op == "connect" for op, _ in commands) == initial_connects
                if cause == "server_connection_replaced":
                    result = real_handle(dict(op="connect", **client.identities()), enrolled["device_id"])
                    assert result["accepted"]
                    return
                healthy[0] = True
                a.tick()
                assert a.run["status"] == "paused"

            if cause == "view":
                a._teleop_view["qualified_until"] = 10.5  # Explicit fresh selected-view proof.
                assert not await guard.resume(require_episode=True, refusal_identity=caught.value.identity)
                assert guard.pause_recoveries == 1 and guard.episodes == 0
            else:
                qualification = asyncio.create_task(qualify())
                if cause == "server_connection_replaced":
                    with pytest.raises(RuntimeError, match="qualification transition"):
                        await guard.resume(require_episode=True)
                    await qualification
                    assert sum(op == "connect" for op, _ in commands) == initial_connects
                    return
                assert await guard.resume(require_episode=True)
                await qualification
            assert a.run["deadline"] == 309.0 and a.run["native_live_at"] == 9.0
            assert a.run["first_cause"] == (None if cause == "view" else "feedback_loss")
            assert a.run["recovery_episodes"] == (0 if cause == "view" else 1)
            producer.native_cursor = m._live(client.snapshot)["cursor"]
            producer.reacquire = True
            fresh = await m.send_leader_sample(
                client, producer, m.ZERO_BODY, record, phase="individual", progress_s=0.02
            )
            assert fresh[2]["accepted"] is True
            assert first[1]["source"]["source_id"] == fresh[1]["source"]["source_id"]
            assert fresh[1]["source"]["sample_seq"] == 2 and first[0]["grant_id"] != fresh[0]["grant_id"]
            assert (
                fresh[1]["source"]["acquired_at_monotonic_s"]
                >= fresh[1]["source"]["grant_received_at_monotonic_s"]
            )
            assert m._live(client.snapshot)["workspace"] == workspace()
            assert [(op, status) for op, status in commands if op == "connect"] == [("connect", "paused")] * 2
        finally:
            if qualification:
                qualification.cancel()
            if client:
                await client.close()
            await runner.cleanup()
            a.run = None
            await owner.close()

    asyncio.run(asyncio.wait_for(scenario(), 5))
