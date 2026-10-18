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
        "ordinary_pauses",
        "ordinary_pauses_no_ack",
        "http_start_recovered",
        "http_start_unknown200",
        "http_start_unknown",
        "http_start_refused",
        "http_start_retry_refused",
        "http_start_retry_lost",
        "http_start_terminal_uncertain",
        "http_start_pending",
        "http_start_malformed",
        "http_start_foreign_receipt",
        "http_start_bad_runid",
    ],
)
def test_real_runner_waits_for_exact_start_from_idle_null_run_and_stops_owned_id(
    tmp_path, monkeypatch, failure
):
    import asyncio
    import copy
    from types import SimpleNamespace

    m = adapter()
    production_post = m.SessionClient.post
    initial_pause_case = failure == "initial_resume_pause"
    http_start = failure is not None and failure.startswith("http_start_")
    prelive = {
        "admission_fault",
        "admission_terminal",
        "terminal_stop_race",
        "foreign_terminal",
        "http_start_terminal_uncertain",
    }
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
    ordinary_case = failure in {"ordinary_pauses", "ordinary_pauses_no_ack"}
    mailbox_case = isinstance(failure, str) and failure.startswith("mailbox_")
    timed_expiry = failure in {"expired_first", "expired_zero", "expired_zero_timeout"}
    pc_clock = [10.0]
    if (
        recovery_case
        or ordinary_case
        or mailbox_case
        or timed_expiry
        or initial_pause_case
        or failure in {"zero_deadline_rounding", "zero_inner_timeout"}
    ):
        import time

        # Advance only the PC's program clock, leaving asyncio scheduling and
        # original perf_counter source acquisition clocks untouched. Held reads
        # cost .05s each; accepted inputs cost .01s. A missing freeze/rebaseline
        # therefore credits the held interval and fails the exact progress test.
        # The three expiry fixtures also need explicit elapsed transaction time:
        # a sub-ms accepted frame must not depend on a Windows monotonic tick to
        # reach their .001s program boundary before all later frames are refused.
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
            if http_start:
                from aiohttp import ClientSession, web

                from tools.am1_session_service import unknown_outcome

                async def start(request):
                    body = await request.json()
                    self.start_requests.append(body)
                    attempt = len(self.start_requests)
                    self.starts = len({x["operation_id"] for x in self.start_requests})
                    refusal = {
                        "accepted": False,
                        "status": "refused",
                        "effect_admitted": False,
                        "reason": "known nonadmission",
                    }
                    if (
                        failure == "http_start_refused"
                        or failure == "http_start_retry_refused"
                        and attempt == 2
                    ):
                        return web.json_response(refusal, status=403)
                    if failure == "http_start_retry_lost" and attempt == 2:
                        request.transport.close()
                        return web.json_response(unknown_outcome(), status=503)
                    if failure == "http_start_terminal_uncertain":
                        return web.json_response(
                            {
                                "accepted": True,
                                "status": "faulted",
                                "run_id": owned_id,
                                "operation_id": body["operation_id"],
                                "uncertain": True,
                                "effect_admitted": False,
                            }
                        )
                    if attempt == 1 or failure in {"http_start_unknown", "http_start_malformed"}:
                        if failure not in {
                            "http_start_recovered",
                            "http_start_unknown200",
                            "http_start_pending",
                        }:
                            self.snapshot = self.state(foreign_id)
                        if failure == "http_start_malformed" and attempt == 1:
                            return web.Response(text="not JSON", content_type="application/json")
                        return web.json_response(
                            unknown_outcome(), status=200 if failure == "http_start_unknown200" else 503
                        )
                    return web.json_response(
                        {
                            "accepted": True,
                            "status": "starting" if failure == "http_start_pending" else "accepted",
                            "uncertain": False,
                            "effect_admitted": failure != "http_start_pending",
                            "run_id": foreign_id
                            if failure == "http_start_foreign_receipt"
                            else "invalid"
                            if failure == "http_start_bad_runid"
                            else owned_id,
                            "operation_id": str(uuid4())
                            if failure == "http_start_foreign_receipt"
                            else body["operation_id"],
                        }
                    )

                app = web.Application()
                app.router.add_post("/api/start", start)
                self.http_runner = web.AppRunner(app, access_log=None)
                await self.http_runner.setup()
                site = web.TCPSite(self.http_runner, "127.0.0.1", 0)
                await site.start()
                self.origin = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
                self.http, self.csrf = ClientSession(), "existing-test-csrf"

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
                    "remaining_s": 300.0,  # Actual Core publishes the native clock on owner reads.
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
                if http_start:
                    return await production_post(self, op, payload)
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
                        cleanup="unknown"
                        if failure in {"admission_fault", "http_start_terminal_uncertain"}
                        else "physical_readback_verified",
                        uncertain=failure in {"admission_fault", "http_start_terminal_uncertain"},
                    )
            assert predicate()

        def identities(self):
            if mailbox_case or initial_pause_case:
                return dict(run_id=owned_id, service_incarnation="service",
                            controller_generation=self.generation, connection_generation=self.connection,
                            host_incarnation="host", intent_revision=self.snapshot["run"]["intent_revision"])
            return {"run_id": self.snapshot["run"]["run_id"] if foreign_boundary else owned_id}

        async def resume(self, *, check=None, refusal_record=None):
            assert self.snapshot["run"]["run_id"] == owned_id
            if check:
                check(self.snapshot)
            if initial_pause_case:
                self.resumes += 1
                assert not self.inputs, "initial interrupted Resume cannot admit a source frame"
                if self.resumes == 1:
                    self.connection = "accepted-initial-connect"
                    identity = dict(self.identities(), intent_revision=3)
                    self.snapshot["run"].update(status="paused", intent_revision=5, remaining_s=299.)
                    self.snapshot["evidence"]["active_acknowledged"] = False
                    event = {"kind": "required_view_expired", "at": 2.,
                             "run_id": owned_id, "service_incarnation": "service"}
                    self.snapshot["events"] = [event]
                    raise m._ResumePausedError(identity, event)
                self.snapshot["run"]["status"] = "running"
                self.snapshot["evidence"]["active_acknowledged"] = True
                return
            if recovery_case or ordinary_case or mailbox_case:
                assert self.snapshot["run"]["status"] != "recovering"
                self.resumes += 1
                if failure == "ordinary_pauses_no_ack" and self.resumes == 5:
                    raise TimeoutError("unqualified ordinary held ACK")
                self.snapshot["run"]["status"] = "running"
                self.snapshot["run"]["intent_revision"] += 2
                self.snapshot["run"]["live_input"]["cursor_intent_revision"] = self.snapshot["run"][
                    "intent_revision"
                ]
                self.connection = str(uuid4())
                self.sequence = 0

        async def command(self, payload):
            if initial_pause_case:
                assert self.resumes == 2, "grant/input cannot precede initial true active ACK"
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
            if mailbox_case and self.authoritative is None and len(self.inputs) == (
                2 if failure in {"mailbox_zero", "mailbox_zero_budget"} else 1
            ):
                self.authoritative = copy.deepcopy(self.snapshot)
                current = self.authoritative
                revision = current["run"]["intent_revision"] + 1
                current["run"].update(status="paused", intent_revision=revision, recovery=None)
                current["run"]["live_input"]["cursor_intent_revision"] = revision
                current["evidence"]["acknowledged_intent_revision"] = revision
                current["evidence"]["accepted_host_action"].update(state="paused", intent_revision=revision)
                current["events"] = [{"kind": "native_input_expired", "at": 2.0,
                                      "run_id": owned_id, "service_incarnation": "service"}]
                if failure == "mailbox_running":
                    current["run"]["status"] = "running"
                elif failure == "mailbox_recovering":
                    current["run"].update(status="recovering", recovery={"started": 2., "ceiling": 3., "revision": revision})
                elif failure == "mailbox_uncertain":
                    current["run"]["uncertain"] = True
                elif failure == "mailbox_fault":
                    current["evidence"]["fault"] = "retained genuine native fault"
                elif failure == "mailbox_run":
                    current["run"]["run_id"] = foreign_id
                elif failure == "mailbox_revision":
                    current["run"]["intent_revision"] -= 1
                elif failure == "mailbox_manual":
                    current["events"][0]["kind"] = "pause"
                elif failure == "mailbox_deadline":
                    current["run"]["deadline"] += 1
                elif failure == "mailbox_workspace":
                    current["run"]["live_input"]["workspace"]["reference"][KEYS[0]] += 1
                raise m.CommandRefusedError({"accepted": False, "status": "refused",
                                            "reason": "physical input mailbox refused",
                                            "effect_admitted": failure == "mailbox_effect"})
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
                if timed_expiry:
                    pc_clock[0] += 0.04
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
            if (
                recovery_case
                or ordinary_case
                or mailbox_case
                or timed_expiry
                or initial_pause_case
                or failure in {"zero_deadline_rounding", "zero_inner_timeout"}
            ):
                pc_clock[0] += 0.01
            self.snapshot["run"]["live_input"].update(
                last_input_seq=payload["seq"], source=payload["source"], body=payload["body"]
            )
            self.snapshot["evidence"]["measured_positions"] = payload["target"]
            if ordinary_case and len(self.inputs) <= 4:
                self.snapshot["run"].update(
                    status="paused", intent_revision=self.snapshot["run"]["intent_revision"] + 1
                )
                self.snapshot["events"] = [
                    {"kind": "input_expired", "at": 2.0, "run_id": owned_id, "service_incarnation": "service"}
                ]
            return {"accepted": True}

        async def get(self, op):
            assert op == "state"
            self.cleanup_reads += 1
            if mailbox_case and self.authoritative is not None and not self.stops:
                pc_clock[0] += 2.01 if failure == "mailbox_zero_budget" else 0.05
                self.snapshot = copy.deepcopy(self.authoritative)
                if failure == "mailbox_connection":
                    self.connection = "replacement-connection"
                return {"snapshot": self.snapshot}
            if (ordinary_case or initial_pause_case) and not self.stops:
                pc_clock[0] += 0.05  # Held reads must not add active workload time.
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
            if http_start:
                await self.http.close()
                await self.http_runner.cleanup()

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
        if failure
        and not recovery_case
        and not ordinary_case
        and not mailbox_case
        and (not dropped or failure == "expired_usb_budget")
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
    if initial_pause_case:
        import json

        args.source = "virtual-leaders"  # This assertion concerns the scripted-progress clock.
        summary = asyncio.run(asyncio.wait_for(m.run(args), 5))
        session = instances[0]
        records = [json.loads(line) for line in (tmp_path / "evidence" / "frames.jsonl").read_text().splitlines()]
        assert session.resumes == 2 and session.starts == 1 and session.stops == [owned_id]
        assert sum(r["event"] == "accepted_resume_superseded" for r in records) == 1
        assert next(i for i, r in enumerate(records) if r["event"] == "accepted_resume_superseded") < next(
            i for i, r in enumerate(records) if r["event"] == "leader_frame_attempt")
        first_frame = next(r for r in records if r["event"] == "leader_frame_attempt")
        assert 0 <= first_frame["progress_s"] < .04  # Held initial work is outside the active clock.
        assert summary["active_progress_s"] == 0 and summary["frame_count"] == 1
        assert summary["original_native_deadline"] == 301. and summary["cleanup_errors"] == []
        return
    if http_start:
        import json

        resolved = failure in {"http_start_recovered", "http_start_unknown200", "http_start_pending"}
        if failure == "http_start_terminal_uncertain":
            with pytest.raises(RuntimeError, match="native admission faulted"):
                asyncio.run(asyncio.wait_for(m.run(args), 20))
            summary = json.loads((tmp_path / "evidence" / "summary.json").read_text())
        elif resolved:
            summary = asyncio.run(asyncio.wait_for(m.run(args), 20))
        else:
            with pytest.raises(m.HTTPOutcomeError):
                asyncio.run(asyncio.wait_for(m.run(args), 20))
            summary = json.loads((tmp_path / "evidence" / "summary.json").read_text())
            assert summary["failure_type"] == "HTTPOutcomeError"
        session = instances[0]
        requests = session.start_requests
        assert len(requests) == (
            1 if failure in {"http_start_refused", "http_start_terminal_uncertain"} else 2
        )
        assert requests[0]["operation_id"] == summary["start_operation_id"]
        assert all(value == requests[0] for value in requests)
        assert session.starts == 1 and session.closed == 1 and sources[0].closed == 1
        records = [
            json.loads(line) for line in (tmp_path / "evidence" / "frames.jsonl").read_text().splitlines()
        ]
        assert records[0]["event"] == "start_requested"
        assert records[0]["operation_id"] == summary["start_operation_id"]
        assert all(
            x["operation_id"] == summary["start_operation_id"]
            for x in records
            if x["event"] in {"start_outcome_ambiguous", "start_refused"}
        )
        assert summary["producer_program_complete"] is False
        if failure == "http_start_terminal_uncertain":
            assert summary["owned_run_id"] == owned_id and summary["start_outcome_uncertain"] is False
            assert summary["native_cleanup_uncertain"] is True and session.stops == []
            assert summary["start_ambiguous_attempts"] == []
            assert summary["native_admission_terminal"]["run"]["run_id"] == owned_id
            assert summary["terminal_failure"]["cause"] == "injected original pre-Live native fault"
            assert summary["cleanup_errors"][0]["step"] == "native_stop"
            return
        assert summary["cleanup_errors"] == []
        if failure == "http_start_refused":
            assert (
                summary["start_outcome_uncertain"] is False and summary["native_cleanup_uncertain"] is False
            )
            assert summary["start_refusal"]["response"]["effect_admitted"] is False
            assert summary["start_ambiguous_attempts"] == [] and session.stops == []
        else:
            attempts = summary["start_ambiguous_attempts"]
            assert len(attempts) == (1 if resolved else 2)
            assert attempts[0]["http_status"] == (
                200 if failure in {"http_start_unknown200", "http_start_malformed"} else 503
            )
            if failure != "http_start_malformed":
                assert attempts[0]["response"]["accepted"] is None
                assert attempts[0]["response"]["uncertain"] is True
            if resolved:
                assert summary["owned_run_id"] == owned_id and session.stops == [owned_id]
                assert (
                    summary["start_outcome_uncertain"] is False
                    and summary["native_cleanup_uncertain"] is False
                )
            else:
                assert "owned_run_id" not in summary and session.stops == []
                assert (
                    summary["start_outcome_uncertain"] is True and summary["native_cleanup_uncertain"] is True
                )
                assert summary["failure"] == attempts[0]["message"]
        return
    if mailbox_case:
        import json

        args.next_start_seconds, args.duration_seconds = (
            (0.001, 180.) if failure in {"mailbox_zero", "mailbox_zero_budget"} else (None, 0.02)
        )
        succeeds = failure in {"mailbox_main", "mailbox_zero"}
        if succeeds:
            summary = asyncio.run(asyncio.wait_for(m.run(args), 5))
        else:
            with pytest.raises((RuntimeError, TimeoutError)):
                asyncio.run(asyncio.wait_for(m.run(args), 5))
            summary = json.loads((tmp_path / "evidence" / "summary.json").read_text())
        session = instances[0]
        records = [json.loads(line) for line in (tmp_path / "evidence" / "frames.jsonl").read_text().splitlines()]
        rejected = [row for row in records if row["event"] == "leader_frame_rejected"]
        assert len(rejected) == 1 and rejected[0]["discarded"] is True
        assert rejected[0]["fresh_acquisition_allowed"] is False
        assert rejected[0]["receipt"]["reason"] == "physical input mailbox refused"
        assert session.starts == 1 and session.stops == ([] if failure == "mailbox_run" else [owned_id])
        assert session.closed == 1
        if failure == "mailbox_run":
            assert summary["native_cleanup_uncertain"] is True  # Never Stop a foreign current run.
        assert summary["original_native_deadline"] == 301. and summary["qualified_native_recovery_episodes"] == 0
        assert [item["source"]["sample_seq"] for item in session.inputs] == list(range(1, len(session.inputs) + 1))
        assert len({item["grant_id"] for item in session.inputs}) == len(session.inputs)
        assert len({item["source"]["source_id"] for item in session.inputs}) == 1
        if succeeds:
            assert session.resumes == 2 and summary["qualified_recoveries"] == 1
            assert summary["final_body_zero"] is True
            assert len(session.inputs) > (2 if failure == "mailbox_zero" else 1)
            if failure == "mailbox_main":
                assert summary["frame_count"] == 2 and summary["active_progress_s"] == pytest.approx(0.02)
        else:
            assert session.resumes == 1
            assert len(session.inputs) == (2 if failure == "mailbox_zero_budget" else 1)
        return
    if ordinary_case:
        args.next_start_seconds, args.duration_seconds = None, 0.05
        if failure == "ordinary_pauses_no_ack":
            with pytest.raises(TimeoutError, match="unqualified ordinary held ACK"):
                asyncio.run(asyncio.wait_for(m.run(args), 20))
            import json

            summary = json.loads((tmp_path / "evidence" / "summary.json").read_text())
            assert summary["ordinary_pauses_observed"] == 4
            assert "qualified_recoveries" not in summary
            assert summary["failure_type"] == "TimeoutError"
        else:
            summary = asyncio.run(asyncio.wait_for(m.run(args), 20))
            assert summary["qualified_recoveries"] == 4
        assert summary["qualified_native_recovery_episodes"] == 0
        assert summary["original_native_deadline"] == 301.0
        assert summary["active_progress_s"] == pytest.approx(
            0.04 if failure == "ordinary_pauses_no_ack" else 0.05
        )
        assert summary["frame_count"] == (4 if failure == "ordinary_pauses_no_ack" else 5)
        assert summary["producer_program_complete"] is False
        assert instances[0].resumes == 5 and instances[0].stops == [owned_id]
        assert len({item["source"]["source_id"] for item in instances[0].inputs}) == 1
        assert summary["cleanup_errors"] == []
        return
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

        async def resume(self, *, check=None, refusal_record=None):
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


@pytest.mark.parametrize("mutation", ["manual", "foreign_event", "old_revision", "episode_jump"])
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
        with pytest.raises(RuntimeError, match="cause-specific pause"):
            await guard.resume(require_episode=True, refusal_identity={"intent_revision": 2})

    asyncio.run(scenario())


@pytest.mark.parametrize("terminal_uncertain", [False, True])
def test_actual_core_start_receipt_owns_pending_or_terminal_uncertain_run(tmp_path, terminal_uncertain):
    import asyncio
    from types import SimpleNamespace

    from tests.robots.test_am1_live_teleop_executor import admitted_executor
    from tools.am1_session_core import SessionAuthority

    m = adapter()
    e, ids = admitted_executor()
    e.hold = lambda: True
    e.evidence = lambda: {
        "feedback": True,
        "native_ack": True,
        "fault": None,
        "required_observation": True,
        "lifecycle": {},
    }
    e.begin = (
        (lambda _: (_ for _ in ()).throw(RuntimeError("injected startup fault")))
        if terminal_uncertain
        else lambda _: True
    )
    e.finish = lambda _: {"cleanup": "unknown", "uncertain": True}
    authority = SessionAuthority(tmp_path / "authority", clock=lambda: 10.0, executor=e)
    body = {
        "operation_id": str(uuid4()),
        "recipe": "physical-live-teleop",
        "controller_generation": ids["controller_generation"],
    }
    try:
        authority.controller = {
            "device_id": "pc",
            "generation": ids["controller_generation"],
            "expires": 11.5,
        }
        result = authority._handle(dict(body, op="start"), "pc")
        assert result["accepted"] is True and result["operation_id"] == body["operation_id"]
        assert result["status"] == ("faulted" if terminal_uncertain else "starting")
        assert result["uncertain"] is terminal_uncertain and result["effect_admitted"] is False

        class Response:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def json(self):
                return result

        client = SimpleNamespace(
            origin="https://fixture",
            csrf="csrf",
            http=SimpleNamespace(post=lambda *args, **kwargs: Response()),
        )
        assert asyncio.run(m.SessionClient.post(client, "start", body)) == result
    finally:
        authority.close()


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


@pytest.mark.parametrize("cause", ["feedback", "view", "server_connection_replaced", "connect_loss_pre", "connect_loss_post", "connect_attach_loss_pre", "connect_attach_loss_post", "release_loss_pre", "release_loss_post", "release_connection_loss", "resume_view_pause", "resume_view_twice", "resume_view_refusal", "resume_view_refused_twice", "resume_pong_pause", "grant_loss_pre", "grant_loss_post", "grant_connection_loss", "grant_view_reconnect", "grant_view_switch", "qualification_episode_pending", "qualification_episode_cleared", "qualification_same_episode", "resume_alignment_refusal", "resume_alignment_refused_twice"])
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
                "acquired_at_monotonic_s": a.clock(),
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
        delayed, unblock_release = [], asyncio.Event()

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
            if cause in {"qualification_episode_pending", "qualification_episode_cleared", "qualification_same_episode"}:
                # Synthetic50: a NEW actual Core episode opens during the
                # already-connected empty-release/held qualification phase.
                pi_now, injected, thermal = [10.], [False], [None]
                repeated = [False]
                a.clock = e.clock = lambda: pi_now[0]
                native_evidence = e.evidence

                def thermal_evidence():
                    value = native_evidence()
                    if thermal[0] is not None:
                        proof = copy.deepcopy(thermal[0])
                        if proof["peaks_ready"] and e.desired == "active":
                            proof.update(deficit_started_at_monotonic_s=None, deficit_ceiling_monotonic_s=None,
                                         hold_sample_at_monotonic_s=None, zero_read_started_at_monotonic_s=None,
                                         zero_read_completed_at_monotonic_s=None)
                        value["thermal_history"] = proof
                        value["accepted_host_action"]["thermal_history"] = copy.deepcopy(proof)
                    return value

                def restore_native():
                    pi_now[0] += .125
                    healthy[0] = True
                    if cause == "qualification_same_episode":
                        a._teleop_view["qualified_until"] = pi_now[0] + .5
                    thermal[0].update(peaks_ready=True, raw_sample_at_monotonic_s=pi_now[0],
                                      hold_sample_at_monotonic_s=pi_now[0], zero_read_started_at_monotonic_s=pi_now[0],
                                      zero_read_completed_at_monotonic_s=pi_now[0])
                    a.tick()

                e.evidence = thermal_evidence
                a._pause_live_input("input_expired")
                original_wait, original_record = client.wait, guard.record
                native_wait = asyncio.Event()
                descriptors, envelopes = [], []
                send = client.ws.send_json

                async def capture(value, **kwargs):
                    if "command" in value:
                        envelopes.append(copy.deepcopy(value))
                    return await send(value, **kwargs)

                def record_handoff(value):
                    original_record(value)
                    if value["event"] == "native_recovery_wait":
                        native_wait.set()

                async def interrupted_qualification(predicate, timeout):
                    if not injected[0] and timeout != 5:
                        injected[0] = True
                        assert sum(v["command"]["op"] == "connect" for v in envelopes) == 1
                        assert sum(v["command"]["op"] == "release_input" for v in envelopes) == 1
                        assert not any(v["command"]["op"] in {"resume", "grant", "input"} for v in envelopes)
                        pi_now[0] += .125
                        healthy[0] = False
                        thermal[0] = {"version": 1, "raw_sample_at_monotonic_s": pi_now[0], "peaks_ready": False,
                                      "deficit_started_at_monotonic_s": pi_now[0],
                                      "deficit_ceiling_monotonic_s": pi_now[0] + 10,
                                      "hold_sample_at_monotonic_s": None, "zero_read_started_at_monotonic_s": None,
                                      "zero_read_completed_at_monotonic_s": None}
                        a.tick()  # Real Core thermal-history transition also supports paused runs.
                        descriptors.append(copy.deepcopy(a.run["recovery"]))
                        assert set(descriptors[0]) == {"started", "ceiling", "revision"}
                        if cause == "qualification_episode_cleared":
                            restore_native()  # Real Core clears only its public descriptor.
                            assert a.run["status"] == "paused" and a.run["recovery"] is None
                        await original_wait(lambda: client.snapshot["run"]["recovery_episodes"] == 1, 1)
                    elif cause == "qualification_same_episode" and not repeated[0] and timeout != 5:
                        # Synthetic53: held qualification did not close the episode.
                        # The acknowledged replacement connect advances revision,
                        # then the SAME original deficit returns before Resume.
                        repeated[0] = True
                        assert guard.episodes == 1
                        assert not any(v["command"]["op"] == "resume" for v in envelopes)
                        pi_now[0] += .125
                        healthy[0] = False
                        thermal[0].update(peaks_ready=False, raw_sample_at_monotonic_s=pi_now[0])
                        a.tick()
                        descriptors.append(copy.deepcopy(a.run["recovery"]))
                        assert a.run["recovery_episodes"] == 1
                        assert all(descriptors[1][key] == descriptors[0][key] for key in ("started", "ceiling"))
                        await original_wait(lambda: client.snapshot["run"]["status"] == "recovering", 1)
                    return await original_wait(predicate, timeout)

                async def qualify_native():
                    for _ in range(2 if cause == "qualification_same_episode" else 1):
                        await native_wait.wait()  # Independent of authenticated GET.
                        native_wait.clear()
                        assert producer.sequence == client.sequence == 0
                        restore_native()

                client.ws.send_json, client.wait, guard.record = capture, interrupted_qualification, record_handoff
                renewal = asyncio.create_task(client.renew())
                qualification = asyncio.create_task(qualify_native())
                def renewal_current():
                    if renewal.done():
                        renewal.result()
                        raise RuntimeError("original renewal ended")
                try:
                    assert await guard.resume(expected_pause="input_expired", renewal_check=renewal_current)
                    await qualification
                    assert guard.episodes == 1 and a.run["recovery_episodes"] == 1
                    assert client.snapshot["evidence"]["active_acknowledged"] is True
                    assert sum(v["command"]["op"] == "connect" for v in envelopes) == (3 if cause == "qualification_same_episode" else 2)
                    assert guard.completed_episodes == 1
                    assert sum(v["command"]["op"] == "resume" for v in envelopes) == 1
                    assert not any(v["command"]["op"] in {"claim", "start", "input", "grant"} for v in envelopes)
                    closed = [v for v in records if v["event"] == "qualified_native_recovery"]
                    assert len(closed) == 1 and closed[0]["original_recovery"] == descriptors[0]
                    holds = [json.loads(row[0]) for row in a.db.execute("SELECT data FROM transitions")
                             if json.loads(row[0])["kind"] == "required_hold"]
                    assert len(holds) == (2 if cause == "qualification_same_episode" else 1)
                    assert holds[0]["recovery"] == descriptors[0]
                    assert a.run["native_live_at"] == 9. and a.run["deadline"] == 309.
                    producer.native_cursor, producer.reacquire = m._live(client.snapshot)["cursor"], True
                    fresh = await m.send_leader_sample(client, producer, m.ZERO_BODY, record, phase="individual", progress_s=.02)
                    assert fresh[2]["accepted"] is True and producer.sequence == 1
                finally:
                    qualification.cancel()
                    renewal.cancel()
                    await asyncio.gather(qualification, renewal, return_exceptions=True)
                return
            if cause in {"grant_view_reconnect", "grant_view_switch"}:
                # Synthetic48: actual Core/IPC/TLS/Gateway view reset, then an
                # independent viewer task publishes genuine advancing progress.
                from types import SimpleNamespace

                from tools import am1_session_service as service
                from tools.am1_onboard_media import OnboardMediaProxy

                pi_clock, old_time = [10.01], service.time
                a.clock = e.clock = lambda: pi_clock[0]
                service.time = SimpleNamespace(monotonic=lambda: pi_clock[0], time=old_time.time)
                auth = tmp_path / "view-auth.json"
                auth.write_bytes(json.dumps({"username": "fixture", "password": "fixture-password"}).encode())
                auth.chmod(0o600)
                proxy = OnboardMediaProxy({"url": "http://127.0.0.1:18889", "credentials": str(auth)},
                                          gateway.ipc, clock=lambda: pi_clock[0])
                generation = {name: str(uuid4()) for name in ("owner", "chest", "wrist_left", "encode")}
                report = {"owner_generation": generation["owner"], "cameras": {
                    role: {"state": "fresh", "configured": True, "sequence": 11,
                           "capture_generation": generation[role], "arrived_at_monotonic_s": 10.}
                    for role in ("chest", "wrist_left")},
                    "video": {"enabled": True, "role": "chest", "state": "running",
                              "capture_generation": generation["chest"], "encode_generation": generation["encode"]}}

                async def status():
                    return copy.deepcopy(report)

                async def upstream(method, path, body=None, **kwargs):
                    assert method == "POST" and path.startswith("/api/webrtc/")
                    return 201, b"v=0\r\n", {"Content-Type": "application/sdp"}

                proxy.status, proxy.upstream, gateway.onboard = status, upstream, proxy
                original_call, lost = gateway.call, [False]
                envelopes, observed, published = [], asyncio.Event(), asyncio.Event()
                original_record, original_attach = guard.record, client.attach

                def record_gap(value):
                    original_record(value)
                    if value["event"] == "owner_rejoin_state" and not m._live(value["snapshot"])["required_view"]["qualified"]:
                        observed.set()

                def capture(ws):
                    send = ws.send_json
                    async def sent(value, **kwargs):
                        if "command" in value:
                            envelopes.append(copy.deepcopy(value))
                        return await send(value, **kwargs)
                    ws.send_json = sent

                async def attached():
                    await original_attach()
                    capture(client.ws)

                async def lose_grant(command, device):
                    if command.get("op") == "grant" and not lost[0]:
                        lost[0] = True
                        a._pause_live_input("input_expired")
                        await proxy.offer("chest", b"v=0\r\n", client.device)
                        assert not a._teleop_view_qualified()
                        client.ws._response.connection.transport.abort()
                        return {"accepted": False, "status": "refused"}
                    return await original_call(command, device)

                async def fresh_view():
                    # No owner GET handler mutates the proof. A separate viewer
                    # request proceeds after the unqualified snapshot is retained.
                    await observed.wait()
                    await asyncio.sleep(.05)
                    assert not any(v["command"]["op"] in {"connect", "release_input", "resume", "input"} for v in envelopes)
                    assert producer.sequence == client.sequence == 0
                    role = "wrist_left" if cause == "grant_view_switch" else "chest"
                    pi_clock[0] += 1.05  # Existing source re-offer minimum remains enforced.
                    report["video"].update(role=role, capture_generation=generation[role])
                    report["cameras"][role].update(sequence=20, arrived_at_monotonic_s=pi_clock[0] - .02)
                    await proxy.offer(role, b"v=0\r\n", client.device)
                    progress = {"role": role, "owner_generation": generation["owner"],
                                "capture_generation": generation[role], "encode_generation": generation["encode"],
                                "decoded_frames": 9, "media_time": .692}
                    with pytest.raises(m.HTTPOutcomeError) as wrong_source:
                        await client.post("onboard/progress", dict(progress, capture_generation=str(uuid4())))
                    assert wrong_source.value.response["reason"] == "view source generation changed"
                    assert not a._teleop_view_qualified()
                    report["cameras"][role]["arrived_at_monotonic_s"] = pi_clock[0] - .6
                    with pytest.raises(m.HTTPOutcomeError) as stale_source:
                        await client.post("onboard/progress", progress)
                    assert stale_source.value.response["reason"] == "selected source is not fresh"
                    assert not a._teleop_view_qualified()
                    assert not any(v["command"]["op"] in {"connect", "release_input", "resume", "input"} for v in envelopes)
                    report["cameras"][role].update(sequence=21, arrived_at_monotonic_s=pi_clock[0] - .02)
                    receipt = await client.post("onboard/progress", dict(progress, decoded_frames=10, media_time=.8))
                    assert receipt["accepted"] is True and receipt["required_view"]["qualified"] is True
                    assert receipt["required_view"]["role"] == role
                    published.set()

                capture(client.ws)
                guard.record, client.attach, gateway.call = record_gap, attached, lose_grant
                renewal = asyncio.create_task(client.renew())
                qualification = asyncio.create_task(fresh_view())
                def view_renewal_healthy():
                    if renewal.done():
                        renewal.result()
                        raise RuntimeError("original renewal ended")
                try:
                    with pytest.raises(ConnectionError) as caught:
                        await m.send_leader_sample(client, producer, m.ZERO_BODY, record, phase="individual", progress_s=0)
                    assert not await guard.resume(grant_error=caught.value, renewal_check=view_renewal_healthy)
                    await qualification
                    assert published.is_set() and guard.episodes == 0
                    assert client.snapshot["evidence"]["active_acknowledged"] is True
                    assert m._live(client.snapshot)["required_view"]["role"] == ("wrist_left" if cause == "grant_view_switch" else "chest")
                    assert sum(v["command"]["op"] == "connect" for v in envelopes) == 1
                    assert sum(v["command"]["op"] == "release_input" for v in envelopes) == 1
                    assert sum(v["command"]["op"] == "resume" for v in envelopes) == 1
                    producer.native_cursor, producer.reacquire = m._live(client.snapshot)["cursor"], True
                    frame = await m.send_leader_sample(client, producer, m.ZERO_BODY, record, phase="individual", progress_s=.02)
                    assert frame[2]["accepted"] is True and producer.sequence == 1
                    assert a.run["deadline"] == 309. and a.run["native_live_at"] == 9.
                finally:
                    qualification.cancel()
                    renewal.cancel()
                    await asyncio.gather(qualification, renewal, return_exceptions=True)
                    service.time, gateway.onboard = old_time, None
                    await proxy.close()
                return
            if cause in {"grant_loss_pre", "grant_loss_post", "grant_connection_loss"}:
                # Synthetic47: real Core creates and qualifies an episode while
                # a grant result is unknown, before ANY producer capture.
                pi_clock = [10.01]
                a.clock = e.clock = lambda: pi_clock[0]
                original_call, lost = gateway.call, [False]
                old_results, envelopes = [], []
                original_attach = client.attach

                def capture(ws):
                    send = ws.send_json
                    async def sent(value, **kwargs):
                        if "command" in value:
                            envelopes.append(copy.deepcopy(value))
                        return await send(value, **kwargs)
                    ws.send_json = sent

                async def attached():
                    await original_attach()
                    capture(client.ws)

                async def delay_grant(command, device):
                    if command.get("op") == "grant" and not lost[0]:
                        lost[0] = True
                        old = copy.deepcopy(command)
                        if cause == "grant_loss_post":
                            old_results.append(await original_call(old, device))
                        pi_clock[0] += .01
                        healthy[0] = False
                        a.tick()  # Actual Core required_hold, original descriptor.
                        descriptor = copy.deepcopy(a.run["recovery"])
                        assert set(descriptor) == {"started", "ceiling", "revision"}
                        pi_clock[0] += .01
                        healthy[0] = True
                        a.tick()  # Actual public descriptor clearing after qualification.
                        assert a.run["status"] == "paused" and a.run["recovery"] is None
                        async def finish_old():
                            await unblock_release.wait()
                            if cause != "grant_loss_post":
                                old_results.append(await original_call(old, device))
                            return old_results[-1]
                        task = asyncio.create_task(finish_old())
                        delayed.append(task)
                        if cause == "grant_connection_loss":
                            client.ws._response.connection.transport.abort()
                        return await asyncio.shield(task)
                    return await original_call(command, device)

                capture(client.ws)
                client.attach, gateway.call = attached, delay_grant
                renewal = asyncio.create_task(client.renew())
                def healthy_renewal():
                    if renewal.done():
                        renewal.result()
                        raise RuntimeError("original renewal ended")
                cursor = copy.deepcopy(producer.native_cursor)
                try:
                    with pytest.raises((TimeoutError, ConnectionError)) as caught:
                        await m.send_leader_sample(client, producer, m.ZERO_BODY, record,
                                                   phase="individual", progress_s=0)
                    original = caught.value
                    assert producer.sequence == 0 and client.sequence == 0
                    assert producer.native_cursor == cursor
                    assert not any(op == "input" for op, _ in commands)
                    assert await guard.resume(grant_error=original, renewal_check=healthy_renewal)
                    assert a.run["status"] == "running" and guard.episodes == 1
                    assert client.snapshot["evidence"]["active_acknowledged"] is True
                    assert sum(op == "connect" for op, _ in commands) == initial_connects + 1
                    holds = [v for v in a.events if v["kind"] == "required_hold"]
                    assert len(holds) == 1 and set(holds[0]["recovery"]) == {"started", "ceiling", "revision"}
                    sql_holds = [json.loads(row[0]) for row in a.db.execute("SELECT data FROM transitions")
                                 if json.loads(row[0])["kind"] == "required_hold"]
                    assert sql_holds == holds
                    assert a.run["recovery"] is None  # No lifecycle/API change.
                    grants = [v for v in envelopes if v["command"]["op"] == "grant"]
                    assert len(grants) == 1  # Unknown permission was discarded.
                    unblock_release.set()
                    await asyncio.wait_for(asyncio.gather(*delayed), .5)
                    if cause == "grant_loss_post":
                        assert old_results[0]["accepted"] is True
                    else:
                        assert old_results == [{"accepted": False, "status": "refused", "reason": "stale control generation", "effect_admitted": False}]
                    assert not any(v["command"]["op"] in {"claim", "start", "input"} for v in envelopes)
                    assert not any(op == "input" for op, _ in commands)
                    assert client.generation == ids["controller_generation"]  # Existing fenced HTTP renewal only.
                    assert client.snapshot["status_projection"] == {"kind": "am1-essential-status", "version": 1}
                    assert a.run["deadline"] == 309. and a.run["native_live_at"] == 9.
                    producer.native_cursor = m._live(client.snapshot)["cursor"]
                    producer.reacquire = True
                    fresh = await m.send_leader_sample(client, producer, m.ZERO_BODY, record,
                                                       phase="individual", progress_s=.02)
                    assert fresh[2]["accepted"] is True and producer.sequence == client.sequence == 1
                    grants = [v for v in envelopes if v["command"]["op"] == "grant"]
                    assert len(grants) == 2 and grants[0]["id"] != grants[1]["id"]
                    assert grants[0]["command"]["connection_generation"] != grants[1]["command"]["connection_generation"]
                    assert len([r for r in records if r["event"] == "qualified_native_recovery"]) == 1
                finally:
                    renewal.cancel()
                    await asyncio.gather(renewal, return_exceptions=True)
                return
            first = await m.send_leader_sample(
                client, producer, m.ZERO_BODY, record, phase="individual", progress_s=0.02
            )
            assert first[2]["accepted"] is True and producer.sequence == 1
            stale = copy.deepcopy(client.snapshot)
            assert stale["run"]["status"] == "running"
            if cause == "resume_pong_pause":
                # Synthetic38 transition: real accepted Resume, pending native
                # active ACK, then installed aiohttp's actual NoPONG callback.
                pi_clock, pending_active = [10.01], [False]
                a.clock = e.clock = lambda: pi_clock[0]
                a._pause_live_input("input_expired")
                a._teleop_view["qualified_until"] = pi_clock[0] + .5
                original_call, original_wait, original_evidence = gateway.call, client.wait, e.evidence
                resumes, closes, interruptions = [], [], []

                def pending_ack():
                    value = original_evidence()
                    if pending_active[0] and e.desired == "active":
                        value["active_acknowledged"] = False
                    return value

                async def accepted_resume(command, device):
                    if command.get("op") == "resume":
                        resumes.append(copy.deepcopy(command))
                        if len(resumes) == 1:
                            pending_active[0] = True
                    response = await original_call(command, device)
                    if command.get("op") == "resume":
                        assert response["accepted"] is True
                    return response

                async def no_pong_during_active(predicate, timeout):
                    if timeout == 5 and len(resumes) == 1 and not closes:
                        assert sum(op == "input" for op, _ in commands) == 1
                        pi_clock[0] += .02
                        a._pause_live_input("input_expired")
                        pending_active[0] = False
                        await asyncio.sleep(.01)
                        assert client.ws._waiting
                        client.ws._pong_not_received()
                        await client.reader
                        closes.append(copy.deepcopy(client.transport_close))
                    try:
                        return await original_wait(predicate, timeout)
                    except ConnectionError as error:
                        interruptions.append(error)
                        raise

                e.evidence, gateway.call, client.wait = pending_ack, accepted_resume, no_pong_during_active
                renewal = asyncio.create_task(client.renew())
                def renewal_healthy():
                    assert not renewal.done()
                try:
                    ceiling = m.time.monotonic() + 1.5
                    assert not await guard.resume(expected_pause="input_expired", wall_deadline=ceiling,
                                                  renewal_check=renewal_healthy)
                    assert client.snapshot["run"]["status"] == "running"
                    assert client.snapshot["evidence"]["active_acknowledged"] is True
                    assert len(resumes) == 2 and len({c["operation_id"] for c in resumes}) == 2
                    assert len(interruptions) == 1
                    assert closes[0]["reason"] == "message-error" and closes[0]["close_code"] == 1006
                    assert closes[0]["exception_type"] == "ServerTimeoutError"
                    assert closes[0]["exception"] == "No PONG received after 0.5 seconds"
                    assert closes[0]["reader_error"] is None and closes[0]["expected"] is False
                    assert sum(op == "input" for op, _ in commands) == 1
                    assert sum(op == "claim" for op, _ in commands) == 0
                    assert guard.episodes == 0 and guard.pause_recoveries == 2
                    assert sum(r["event"] == "accepted_resume_superseded" for r in records) == 1
                    assert a.run["deadline"] == 309. and a.run["native_live_at"] == 9.
                    assert m.time.monotonic() < ceiling
                    producer.native_cursor, producer.reacquire = m._live(client.snapshot)["cursor"], True
                    fresh = await m.send_leader_sample(client, producer, m.ZERO_BODY, record,
                                                       phase="individual", progress_s=.04)
                    assert fresh[2]["accepted"] is True and producer.sequence == 2
                    assert fresh[0]["grant_id"] != first[0]["grant_id"]
                finally:
                    renewal.cancel()
                    await asyncio.gather(renewal, return_exceptions=True)
                return
            if cause in {"resume_alignment_refusal", "resume_alignment_refused_twice"}:
                # Synthetic52: PC observes qualified held proof; a later real
                # Core Resume evidence read sees alignment unavailable.
                pi_clock, denied = [10.], [False]
                a.clock = e.clock = lambda: pi_clock[0]
                a._pause_live_input("input_expired")
                a._teleop_view["qualified_until"] = pi_clock[0] + .5
                original_call, original_get, original_evidence = gateway.call, client.get, e.evidence
                count = 3 if cause == "resume_alignment_refused_twice" else 2
                resumes, refused, fresh_reads, envelopes = [], [], [], []
                awaiting_fresh = [False]
                own_event = copy.deepcopy(a.events[-1])
                send = client.ws.send_json

                def current_evidence():
                    proof = original_evidence()
                    if denied[0]:
                        proof["pose_aligned"] = False
                    return proof

                async def capture(value, **kwargs):
                    if "command" in value:
                        envelopes.append(copy.deepcopy(value))
                    return await send(value, **kwargs)

                async def refuse_after_qualified(command, device):
                    if command.get("op") == "resume":
                        resumes.append(copy.deepcopy(command))
                        revision = a.run["intent_revision"]
                        if len(resumes) < count:
                            denied[0] = True
                            response = await original_call(command, device)
                            assert response == {"accepted": False, "status": "refused", "effect_admitted": False,
                                                "reason": "qualified release/alignment required"}
                            assert a.run["intent_revision"] == revision and a.events[-1] == own_event
                            refused.append(copy.deepcopy(response))
                            awaiting_fresh[0] = True
                            denied[0] = False  # Independent native convergence, not a GET side effect.
                            return response
                        assert fresh_reads and all(fresh_reads[-1].values())
                    return await original_call(command, device)

                async def fresh_owner(path):
                    response = await original_get(path)
                    if awaiting_fresh[0]:
                        proof = response["snapshot"]["evidence"]
                        fresh_reads.append({key:proof[key] for key in
                                            ("feedback", "required_observation", "hold_acknowledged", "pose_aligned", "native_ack")})
                        assert all(fresh_reads[-1].values())
                        awaiting_fresh[0] = False
                    return response

                e.evidence, gateway.call, client.get = current_evidence, refuse_after_qualified, fresh_owner
                client.ws.send_json = capture
                ceiling = m.time.monotonic() + 1.5
                assert not await guard.resume(expected_pause="input_expired", wall_deadline=ceiling)
                assert len(resumes) == count and len(refused) == count - 1
                assert len({v["operation_id"] for v in resumes}) == count
                assert sum(v["command"]["op"] == "connect" for v in envelopes) == 1
                assert sum(v["command"]["op"] == "release_input" for v in envelopes) == 1
                assert not any(v["command"]["op"] in {"claim", "start", "grant", "input"} for v in envelopes)
                assert client.snapshot["evidence"]["active_acknowledged"] is True
                assert a.run["native_live_at"] == 9. and a.run["deadline"] == 309.
                assert guard.episodes == 0 and m.time.monotonic() < ceiling
                assert sum(r["event"] == "resume_qualification_refused" for r in records) == count - 1
                assert any(r["event"] == "resume_qualification_observed" for r in records)
                producer.native_cursor, producer.reacquire = m._live(client.snapshot)["cursor"], True
                fresh = await m.send_leader_sample(client, producer, m.ZERO_BODY, record, phase="individual", progress_s=.04)
                assert fresh[2]["accepted"] is True and producer.sequence == 2
                return
            if cause in {"resume_view_refusal", "resume_view_refused_twice"}:
                # Clearly synthetic Core/Gateway witness: expiry occurs AFTER
                # the PC held/view predicate, BEFORE the real Core Resume gate.
                pi_clock, refusals_left = [10.01], [2 if cause == "resume_view_refused_twice" else 1]
                a.clock = e.clock = lambda: pi_clock[0]
                a._pause_live_input("input_expired")
                a._teleop_view["qualified_until"] = pi_clock[0] + .5
                original_call, original_get = gateway.call, client.get
                resumes, refused, fresh_reads = [], [], []
                awaiting_fresh = [False]
                own_event = copy.deepcopy(a.events[-1])
                before_connects = sum(op == "connect" for op, _ in commands)
                before_releases = sum(op == "release_input" for op, _ in commands)

                async def refuse_after_pc_view(command, device):
                    if command.get("op") == "resume":
                        resumes.append(copy.deepcopy(command))
                        revision = a.run["intent_revision"]
                        assert sum(op == "input" for op, _ in commands) == 1
                        if refusals_left[0]:
                            refusals_left[0] -= 1
                            pi_clock[0] += .02
                            a._teleop_view["qualified_until"] = pi_clock[0] - .001
                            response = await original_call(command, device)
                            assert response == {"accepted": False, "status": "refused", "effect_admitted": False,
                                                "reason": "current required selected view must advance before Resume"}
                            assert a.run["intent_revision"] == revision and a.run["status"] == "paused"
                            assert a.events[-1] == own_event
                            refused.append(copy.deepcopy(response))
                            awaiting_fresh[0] = True
                            # An independent viewer advances real Core progress;
                            # its production cannot depend on the later owner GET.
                            pi_clock[0] += .01
                            progress = {"op": "view_progress", "run_id": e.run_id, "role": "chest",
                                        "owner_generation": "owner", "capture_generation": "capture",
                                        "encode_generation": "encode", "decoded_frames": len(resumes) + 1,
                                        "media_time": float(len(resumes) + 1), "source_sequence": len(resumes),
                                        "source_fresh": True, "received_at_monotonic_s": pi_clock[0]}
                            assert real_handle(progress, "gateway-view")["accepted"] is True
                            return response
                        assert fresh_reads and fresh_reads[-1]["qualified"] is True
                    return await original_call(command, device)

                async def fresh_view_owner(path):
                    response = await original_get(path)
                    if awaiting_fresh[0]:
                        snapshot = response["snapshot"]
                        view = m._live(snapshot)["required_view"]
                        fresh_reads.append(copy.deepcopy(view))
                        assert snapshot["events"][-1] == own_event and snapshot["run"]["status"] == "paused"
                        assert view["qualified"] is True  # Fresh HTTP proof, never a cached flag assignment.
                        awaiting_fresh[0] = False
                    return response

                gateway.call, client.get = refuse_after_pc_view, fresh_view_owner
                ceiling = m.time.monotonic() + 1.5
                assert not await guard.resume(expected_pause="input_expired", wall_deadline=ceiling)
                count = 3 if cause == "resume_view_refused_twice" else 2
                assert len(resumes) == count and len(refused) == count - 1
                assert len({command["operation_id"] for command in resumes}) == count
                assert sum(op == "connect" for op, _ in commands) == before_connects + 1
                assert sum(op == "release_input" for op, _ in commands) == before_releases + 1
                assert client.snapshot["run"]["status"] == "running"
                assert client.snapshot["evidence"]["active_acknowledged"] is True
                assert a.run["deadline"] == 309. and a.run["native_live_at"] == 9.
                assert guard.episodes == 0 and guard.pause_recoveries == 1
                assert not any(record["event"] in {"accepted_resume_superseded", "qualified_native_recovery"}
                               for record in records)
                assert sum(record["event"] == "resume_view_refused" for record in records) == count - 1
                assert m.time.monotonic() < ceiling
                producer.native_cursor = m._live(client.snapshot)["cursor"]
                producer.reacquire = True
                fresh = await m.send_leader_sample(client, producer, m.ZERO_BODY, record,
                                                   phase="individual", progress_s=.04)
                assert fresh[2]["accepted"] is True and producer.sequence == 2
                return
            if cause in {"resume_view_pause", "resume_view_twice"}:
                # Synthetic view expiry AFTER a positively accepted Resume,
                # before an active snapshot. Core/Gateway/client are real.
                pi_clock, remaining_pauses = [10.01], [2 if cause == "resume_view_twice" else 1]
                a.clock = e.clock = lambda: pi_clock[0]  # Same Pi clock for owner and mailbox expiry.
                a._pause_live_input("required_view_expired")
                a._teleop_view["qualified_until"] = pi_clock[0] + .5
                original_call, original_get = gateway.call, client.get
                original_evidence, defer_active = e.evidence, [False]
                def pending_active_evidence():
                    value = original_evidence()
                    if defer_active[0] and e.desired == "active":
                        # Fake native ACK has not arrived yet. Prevent a real
                        # publisher race from qualifying BEFORE the injected pause.
                        value["active_acknowledged"] = False
                        value["accepted_host_action"].update(state="paused", epoch=epoch[0] - 1)
                    return value
                e.evidence = pending_active_evidence
                resume_commands, new_pause_reads = [], []
                current_pause = [None]

                async def pause_accepted_resume(command, device):
                    if command.get("op") == "resume" and remaining_pauses[0]:
                        defer_active[0] = True
                    result = await original_call(command, device)
                    if command.get("op") == "resume":
                        assert result["accepted"] is True
                        resume_commands.append(copy.deepcopy(command))
                        assert sum(op == "input" for op, _ in commands) == 1
                        if remaining_pauses[0]:
                            remaining_pauses[0] -= 1
                            pi_clock[0] += .02
                            a._teleop_view["qualified_until"] = pi_clock[0] - .001
                            a.tick()  # Actual selected-view protection owns the new pause.
                            assert a.run["status"] == "paused"
                            current_pause[0] = copy.deepcopy(a.events[-1])
                            defer_active[0] = False
                    return result

                async def fresh_owner(path):
                    result = await original_get(path)
                    if current_pause[0] is not None:
                        assert result["snapshot"]["run"]["status"] == "paused"
                        assert result["snapshot"]["events"][-1] == current_pause[0]
                        new_pause_reads.append(current_pause[0])
                        current_pause[0] = None
                        pi_clock[0] += .01
                        a._teleop_view["qualified_until"] = pi_clock[0] + .5
                    return result

                gateway.call, client.get = pause_accepted_resume, fresh_owner
                ceiling = m.time.monotonic() + 1.5
                assert not await guard.resume(expected_pause="required_view_expired", wall_deadline=ceiling)
                count = 3 if cause == "resume_view_twice" else 2
                assert len(resume_commands) == count and len(new_pause_reads) == count - 1
                assert len({c["operation_id"] for c in resume_commands}) == count
                assert client.snapshot["run"]["status"] == "running"
                assert client.snapshot["evidence"]["active_acknowledged"] is True
                assert a.run["deadline"] == 309. and a.run["native_live_at"] == 9.
                assert guard.episodes == 0 and guard.pause_recoveries == count
                assert m.time.monotonic() < ceiling
                assert not any(r["event"] == "qualified_native_recovery" for r in records)
                producer.native_cursor = m._live(client.snapshot)["cursor"]
                producer.reacquire = True
                fresh = await m.send_leader_sample(client, producer, m.ZERO_BODY, record,
                                                   phase="individual", progress_s=.04)
                assert fresh[2]["accepted"] is True and producer.sequence == 2
                return
            if cause in {"release_loss_pre", "release_loss_post", "release_connection_loss"}:
                # Synthetic delayed release; real Core/IPC/Gateway concurrently
                # execute it only AFTER a new connection has resumed the run.
                a._pause_live_input("native_input_expired")
                original_call, lost = gateway.call, [False]
                envelopes, old_results = [], []
                original_attach = client.attach

                def capture(ws):
                    send = ws.send_json
                    async def sent(value, **kwargs):
                        if "command" in value:
                            envelopes.append(copy.deepcopy(value))
                        return await send(value, **kwargs)
                    ws.send_json = sent

                async def attached():
                    await original_attach()
                    capture(client.ws)

                async def delay_release(command, device):
                    if command.get("op") == "release_input" and "intent_revision" in command and not lost[0]:
                        lost[0] = True
                        old = copy.deepcopy(command)
                        if cause == "release_loss_post":
                            old_results.append(await original_call(old, device))
                        async def finish_old():
                            await unblock_release.wait()
                            if cause != "release_loss_post":
                                old_results.append(await original_call(old, device))
                            return old_results[-1]
                        task = asyncio.create_task(finish_old())
                        delayed.append(task)
                        if cause == "release_connection_loss":
                            client.ws._response.connection.transport.abort()
                        return await asyncio.shield(task)
                    return await original_call(command, device)

                capture(client.ws)
                client.attach, gateway.call = attached, delay_release
                renewal = asyncio.create_task(client.renew())
                def healthy_renewal():
                    if renewal.done():
                        renewal.result()
                        raise RuntimeError("original renewal ended")
                try:
                    assert not await guard.resume(expected_pause="native_input_expired", renewal_check=healthy_renewal)
                    assert a.run["status"] == "running"
                    assert len([v for v in envelopes if v["command"]["op"] == "connect"]) == 2
                    releases = [v for v in envelopes if v["command"]["op"] == "release_input"]
                    assert len(releases) == 2 and releases[0]["id"] != releases[1]["id"]
                    assert releases[0]["command"]["connection_generation"] != releases[1]["command"]["connection_generation"]
                    unblock_release.set()
                    await asyncio.wait_for(asyncio.gather(*delayed), 0.5)
                    if cause == "release_loss_post":
                        assert old_results[0]["accepted"] is True
                    else:
                        assert old_results == [{"accepted": False, "status": "refused", "reason": "stale control generation", "effect_admitted": False}]
                    assert a.run["status"] == "running"  # Late old release cannot pause the NEW binding.
                    assert not any(v["command"]["op"] in {"claim", "start"} for v in envelopes)
                    assert client.generation == ids["controller_generation"]  # HTTP renewals retain this lease.
                    assert a.run["deadline"] == 309.0 and a.run["native_live_at"] == 9.0
                    assert guard.episodes == 0 and guard.pause_recoveries == 1
                    producer.native_cursor = m._live(client.snapshot)["cursor"]
                    producer.reacquire = True
                    fresh = await m.send_leader_sample(client, producer, m.ZERO_BODY, record,
                                                       phase="individual", progress_s=0.04)
                    assert fresh[2]["accepted"] is True and producer.sequence == 2
                    assert fresh[0]["grant_id"] != first[0]["grant_id"]
                finally:
                    renewal.cancel()
                    await asyncio.gather(renewal, return_exceptions=True)
                return
            if cause in {"connect_loss_pre", "connect_loss_post", "connect_attach_loss_pre", "connect_attach_loss_post"}:
                # Synthetic transport failure at the demonstrated initial-connect
                # boundary. Core, IPC, TLS, gateway and SessionClient are real.
                a._pause_live_input("input_expired")
                sent_bindings, lost = [], [False]
                original_attach, original_call, original_wait = client.attach, gateway.call, client.wait
                attaching, attachment_losses = [False], [0]
                interrupt_attachment = cause.startswith("connect_attach_loss_")

                async def wait_initial_snapshot(predicate, timeout):
                    if attaching[0] and interrupt_attachment and not attachment_losses[0]:
                        assert timeout == 3 and client.snapshot is None
                        attachment_losses[0] += 1
                        # Exact37 seam: actual authenticated TLS/WSS attachment
                        # loses its fresh stream before its first snapshot.
                        client.ws._response.connection.transport.abort()
                        await client.reader
                    return await original_wait(predicate, timeout)

                def capture_binding(ws):
                    original_send = ws.send_json
                    async def send(value, **kwargs):
                        if (value.get("command") or {}).get("op") == "connect":
                            sent_bindings.append(copy.deepcopy(value))
                        return await original_send(value, **kwargs)
                    ws.send_json = send

                async def attached():
                    attaching[0] = True
                    try:
                        await original_attach()
                    finally:
                        attaching[0] = False
                    capture_binding(client.ws)

                async def lose_connect(command, device):
                    if command.get("op") == "connect" and not lost[0]:
                        lost[0] = True
                        if cause in {"connect_loss_post", "connect_attach_loss_post"}:
                            await original_call(command, device)
                        # Both before-effect and after-effect cases lose the
                        # pending result. Neither supplies nonadmission proof.
                        client.ws._response.connection.transport.abort()
                        return {"accepted": False, "status": "refused"}
                    return await original_call(command, device)

                capture_binding(client.ws)
                client.attach, client.wait, gateway.call = attached, wait_initial_snapshot, lose_connect
                assert guard.original["run"]["operation_id"] == a.run["operation_id"]
                if interrupt_attachment:
                    renewal = asyncio.create_task(client.renew())
                    def renewal_healthy():
                        if renewal.done():
                            renewal.result()
                            raise RuntimeError("original HTTP renewal ended")
                    try:
                        assert not await guard.resume(expected_pause="input_expired", renewal_check=renewal_healthy)
                    finally:
                        renewal.cancel()
                        await asyncio.gather(renewal, return_exceptions=True)
                    assert attachment_losses == [1]
                    assert any(row["event"] == "ordinary_attachment_interrupted" for row in records)
                else:
                    assert not await guard.resume(expected_pause="input_expired")
                assert len(sent_bindings) == 2
                assert sent_bindings[0]["id"] != sent_bindings[1]["id"]
                assert [op for op, _ in commands].count("claim") == 0
                assert sum(op == "connect" for op, _ in commands) == initial_connects + (
                    2 if cause in {"connect_loss_post", "connect_attach_loss_post"} else 1
                )
                assert a.run["deadline"] == 309.0 and a.run["native_live_at"] == 9.0
                assert a.run["first_cause"] is None and a.run["recovery_episodes"] == 0
                assert guard.pause_recoveries == 1
                producer.native_cursor = m._live(client.snapshot)["cursor"]
                producer.reacquire = True
                fresh = await m.send_leader_sample(
                    client, producer, m.ZERO_BODY, record, phase="individual", progress_s=0.02
                )
                assert fresh[2]["accepted"] is True and producer.sequence == 2
                assert fresh[0]["grant_id"] != first[0]["grant_id"]
                assert m._live(client.snapshot)["reference"] == workspace()["reference"]
                return
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
            if cause == "view":
                for pause in range(2, 5):
                    a._teleop_view["qualified_until"] = 9.0
                    with pytest.raises(m.CommandRefusedError) as later:
                        await m.send_leader_sample(
                            client, producer, m.ZERO_BODY, record, phase="individual", progress_s=0.02
                        )
                    assert m.recovery_grant_refusal(later.value)
                    a._teleop_view["qualified_until"] = 10.5
                    assert not await guard.resume(require_episode=True, refusal_identity=later.value.identity)
                    producer.native_cursor = m._live(client.snapshot)["cursor"]
                    producer.reacquire = True
                    current = await m.send_leader_sample(
                        client, producer, m.ZERO_BODY, record, phase="individual", progress_s=0.02
                    )
                    assert current[2]["accepted"] is True
                    assert guard.pause_recoveries == pause and guard.episodes == 0
                    assert a.run["deadline"] == 309.0 and a.run["native_live_at"] == 9.0
                    assert m._live(client.snapshot)["workspace"] == workspace()
                    assert m._live(client.snapshot)["reference"] == workspace()["reference"]
                    assert current[1]["source"]["source_id"] == first[1]["source"]["source_id"]
                assert producer.sequence == 5
                assert [(op, status) for op, status in commands if op == "connect"] == [
                    ("connect", "paused")
                ] * 5
            elif cause == "feedback":
                for count in (2, 3):
                    healthy[0] = False
                    with pytest.raises(m.CommandRefusedError):
                        await m.send_leader_sample(
                            client, producer, m.ZERO_BODY, record, phase="individual", progress_s=0.02
                        )
                    assert a.run["status"] == "recovering" and a.run["recovery_episodes"] == count
                    connects = sum(op == "connect" for op, _ in commands)

                    async def qualify_current_episode(expected_connects=connects):
                        await asyncio.sleep(0.06)
                        assert a.run["status"] == "recovering"
                        assert sum(op == "connect" for op, _ in commands) == expected_connects
                        healthy[0] = True
                        a.tick()

                    qualification = asyncio.create_task(qualify_current_episode())
                    assert await guard.resume(require_episode=True)
                    await qualification
                    assert guard.episodes == count and a.run["deadline"] == 309.0
                    producer.native_cursor = m._live(client.snapshot)["cursor"]
                    producer.reacquire = True
                    current = await m.send_leader_sample(
                        client, producer, m.ZERO_BODY, record, phase="individual", progress_s=0.02
                    )
                    assert current[2]["accepted"] is True
                connects = sum(op == "connect" for op, _ in commands)
                healthy[0] = False
                a.tick()
                assert a.run["recovery_episodes"] == 4 and a.run["requested_terminal"] == "faulted"
                assert a.run["status"] in {"finishing", "faulted"}
                with pytest.raises(RuntimeError, match="episode budget"):
                    await guard.resume(require_episode=True)
                assert sum(op == "connect" for op, _ in commands) == connects
        finally:
            unblock_release.set()
            await asyncio.gather(*delayed, return_exceptions=True)
            if qualification:
                qualification.cancel()
            if client:
                await client.close()
            await runner.cleanup()
            a.run = None
            await owner.close()

    asyncio.run(asyncio.wait_for(scenario(), 5))


@pytest.mark.parametrize("count", [3, 4])
def test_ordinary_pause_counter_does_not_change_native_episode_three_limit(count):
    m = adapter()
    client = type("Client", (), {"snapshot": recovery_state(), "device": "device", "generation": "control"})()
    guard = m.SameRunRecovery(client, lambda value: None)
    guard.pause_recoveries, guard.episodes, guard.cause = 12, 3, "feedback_loss"
    state = recovery_state()
    state["run"].update(recovery_episodes=count, first_cause="feedback_loss")
    if count == 3:
        guard.check(state)
    else:
        with pytest.raises(RuntimeError, match="episode budget"):
            guard.check(state)


def test_fourth_ordinary_pause_still_requires_qualified_held_ack_before_resume():
    import asyncio
    import copy

    m = adapter()
    paused = recovery_state()
    paused["run"].update(status="paused", intent_revision=3)
    paused["events"] = [
        {"kind": "input_expired", "at": 100.01, "run_id": "owned", "service_incarnation": "service"}
    ]
    paused["run"]["live_input"]["cursor_intent_revision"] = 3
    paused["evidence"]["acknowledged_intent_revision"] = 3
    paused["evidence"]["accepted_host_action"]["intent_revision"] = 3
    paused["evidence"]["hold_acknowledged"] = False
    commands = []

    class Client:
        snapshot = recovery_state()
        device, generation, connection = "device", "control", "connection"
        resume, identities = m.SessionClient.resume, m.SessionClient.identities

        async def get(self, path):
            return {"snapshot": copy.deepcopy(paused)}

        async def command(self, payload):
            commands.append(payload["op"])
            assert payload["op"] != "resume"
            return {"connection_generation": "connection", "snapshot": self.snapshot}

        async def wait(self, predicate, timeout):
            assert timeout == 8 and not predicate()
            raise TimeoutError("held ACK remains unqualified")

    async def scenario():
        guard = m.SameRunRecovery(Client(), lambda value: None)
        guard.pause_recoveries = 3
        with pytest.raises(TimeoutError, match="held ACK"):
            await guard.resume(require_episode=True, refusal_identity={"intent_revision": 2})
        assert commands == ["connect", "release_input"]
        assert guard.pause_recoveries == 4 and guard.episodes == 0

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change",
    [
        None,
        "run",
        "operation",
        "recipe",
        "service",
        "device",
        "controller",
        "accepted_at",
        "startup_deadline",
        "terminal",
        "uncertain",
        "native_fault",
        "reporting_error",
        "policy_close",
        "reader_reason",
        "expected_close",
        "missing_anchor",
        "expired_before_attach",
        "expired_during_attach",
        "second_loss",
        "stale_attach",
        "already_live",
        "later_run",
        "later_operation",
        "later_recipe",
        "later_service",
        "later_device",
        "later_controller",
        "later_accepted_at",
        "later_startup_deadline",
        "later_terminal",
        "later_uncertain",
        "later_native_fault",
    ],
)
def test_runner_starting_transport_reattaches_once_to_exact_owned_admission(tmp_path, monkeypatch, change):
    """Synthetic WSS loss at actual run()/wait(), before any native Live or input."""
    import asyncio
    import copy
    import json
    import time
    from types import SimpleNamespace

    m = adapter()
    actual_wait = m.SessionClient.wait
    clock, instances, live_entries = [20.0], [], []
    monkeypatch.setattr(
        m, "time", SimpleNamespace(monotonic=lambda: clock[0], perf_counter=time.perf_counter)
    )
    owned = str(uuid4())

    class Session:
        wait = actual_wait

        def __init__(self, *args):
            self.device, self.generation, self.connection = "device", "control", None
            self.snapshot = recovery_state()
            self.snapshot["run"] = None
            self.transport_close = self.transport_error = self.reader = None
            self.ws = SimpleNamespace(closed=False)
            self.changed = asyncio.Event()
            self.starts, self.stops, self.claims, self.attaches = [], [], 0, 0
            self.renewing, self.closed = False, False
            self.pending = []
            instances.append(self)

        def starting(self):
            state = recovery_state()
            state["run"].update(
                run_id=owned,
                operation_id=self.starts[0]["operation_id"],
                accepted_at=1000.0,
                startup_deadline=1180.0,
                status="starting",
                native_live_at=None,
                deadline=None,
                backend_incarnation=None,
                live_input=None,
            )
            return state

        async def lose(self):
            await asyncio.sleep(0)
            clock[0] += 181.0 if change == "expired_before_attach" else 9.0
            if change == "already_live":
                self.snapshot["run"]["native_live_at"] = 1009.0
            self.transport_close = {
                "reason": "reader-error" if change == "reader_reason" else "stream-ended",
                "expected": change == "expected_close",
                "close_code": 1008 if change == "policy_close" else 1006,
                "reader_error": {"error_type": "RuntimeError", "message": "owner reporting unavailable"}
                if change == "reporting_error"
                else None,
                "exception_type": "ClientConnectionResetError",
                "exception": "Cannot write to closing transport",
                "observed_at_monotonic_s": clock[0],
            }
            self.transport_error = "current WSS connection ended: stream-ended"
            self.ws.closed = True
            self.changed.set()

        async def attach(self):
            self.attaches += 1
            if self.attaches == 1:
                return
            assert self.snapshot is None, "an old cached Live snapshot cannot satisfy fresh attachment"
            assert self.renewing, "HTTP renewal must keep the same claim alive during attachment"
            self.transport_close = self.transport_error = None
            self.ws.closed = False
            if change == "expired_during_attach":
                clock[0] += 180.0
            if change == "stale_attach":
                return
            self.snapshot = self.starting()
            if change is None or change.startswith("later_"):
                self.pending.append(asyncio.create_task(self.publish_live()))
            else:
                self.mutate(change)
            if change == "second_loss":
                self.pending.append(asyncio.create_task(self.lose()))
            self.changed.set()

        def mutate(self, mutation):
            if mutation in {"run", "operation", "recipe", "accepted_at", "startup_deadline"}:
                key = {"run": "run_id", "operation": "operation_id"}.get(mutation, mutation)
                self.snapshot["run"][key] = 1181.0 if mutation == "startup_deadline" else "changed"
            elif mutation == "service":
                self.snapshot["service_incarnation"] = "other-service"
            elif mutation in {"device", "controller"}:
                key = "device_id" if mutation == "device" else "controller_generation"
                self.snapshot["controller"][key] = "other-owner"
            elif mutation == "terminal":
                self.snapshot["run"].update(status="faulted", native_first_cause="original native fault")
            elif mutation == "uncertain":
                self.snapshot["run"]["uncertain"] = True
            elif mutation == "native_fault":
                self.snapshot["evidence"]["fault"] = "genuine native status fault"

        async def publish_live(self):
            await asyncio.sleep(0)
            self.snapshot["run"].update(
                native_live_at=1009.0,
                deadline=1309.0,
                status="preparing",
                backend_incarnation="host",
                live_input=recovery_state()["run"]["live_input"],
            )
            if change:
                self.mutate(change.removeprefix("later_"))
            self.changed.set()

        async def detach(self):
            self.snapshot = None

        async def claim(self):
            self.claims += 1

        async def renew(self):
            self.renewing = True
            try:
                await asyncio.Event().wait()
            finally:
                self.renewing = False

        async def post(self, path, body):
            if path == "start":
                self.starts.append(copy.deepcopy(body))
                self.snapshot = self.starting()
                if change == "missing_anchor":
                    del self.snapshot["run"]["accepted_at"]
                self.pending.append(asyncio.create_task(self.lose()))
                return {"accepted": True, "run_id": owned, "operation_id": body["operation_id"]}
            assert path == "stop"
            self.stops.append(copy.deepcopy(body))
            return {"accepted": True, "status": "finishing"}

        async def get(self, path):
            assert path == "state"
            state = self.starting()
            if self.stops:
                state["run"].update(status="stopped", cleanup="physical_readback_verified", uncertain=False)
            return {"snapshot": state}

        async def close(self):
            for task in self.pending:
                await task
            self.closed = True

    def live_boundary(value):
        live_entries.append(copy.deepcopy(instances[0].snapshot))
        raise RuntimeError("synthetic reached existing Live entrypoint")

    monkeypatch.setattr(m, "SessionClient", Session)
    monkeypatch.setattr(m, "validate_workspace", live_boundary)
    args = SimpleNamespace(
        source="virtual-leaders",
        output=str(tmp_path / "run"),
        url="unused",
        ca="unused",
        storage_state="unused",
    )
    with pytest.raises((ConnectionError, RuntimeError, TimeoutError)):
        asyncio.run(asyncio.wait_for(m.run(args), 10))
    session = instances[0]
    summary = json.loads((tmp_path / "run" / "summary.json").read_text())
    rows = [json.loads(row) for row in (tmp_path / "run" / "frames.jsonl").read_text().splitlines()]
    assert len(session.starts) == 1 and session.claims == 1
    assert [row["run_id"] for row in session.stops] == [owned]
    assert session.closed and not session.renewing
    assert summary["frame_count"] == 0 and summary["cleanup_errors"] == []
    refused_without_attach = change in {
        "reporting_error",
        "policy_close",
        "reader_reason",
        "expected_close",
        "missing_anchor",
        "expired_before_attach",
        "already_live",
    }
    assert session.attaches == (1 if refused_without_attach else 2)
    assert len(live_entries) == (1 if change is None else 0)
    assert not any(row["event"] == "native_admitted" for row in rows)
    if change is None:
        assert summary["failure"] == "synthetic reached existing Live entrypoint"
        assert live_entries[0]["run"]["operation_id"] == session.starts[0]["operation_id"]
        assert live_entries[0]["run"]["recovery_episodes"] == 0
        boundary = [row for row in rows if row["event"] == "native_admission_transport_closed"]
        assert len(boundary) == 1 and boundary[0]["transport_close"]["close_code"] == 1006
        assert boundary[0]["remaining_s"] == pytest.approx(171.0)
        assert len([row for row in rows if row["event"] == "native_admission_reattached"]) == 1
    elif change in {"reporting_error", "policy_close", "reader_reason", "expected_close", "second_loss"}:
        assert summary["failure_type"] == "ConnectionError"
        assert summary["failure"] == "current WSS connection ended: stream-ended"
    elif change in {"expired_before_attach", "expired_during_attach"}:
        assert summary["failure_type"] == "TimeoutError"
        assert summary["failure"] == "original native admission ceiling exhausted"
    else:
        assert summary["failure_type"] == "RuntimeError"
        assert "native admission" in summary["failure"]
        if change in {"terminal", "later_terminal"}:
            assert "original native fault" in summary["failure"]
            assert (
                summary["native_admission_terminal"]["run"]["native_first_cause"] == "original native fault"
            )



@pytest.mark.parametrize("failure", [
    "mailbox_main", "mailbox_zero", "mailbox_zero_budget", "mailbox_running",
    "mailbox_recovering", "mailbox_uncertain", "mailbox_fault", "mailbox_run",
    "mailbox_connection", "mailbox_revision", "mailbox_manual", "mailbox_deadline",
    "mailbox_workspace", "mailbox_effect",
])
def test_runner_opaque_mailbox_requires_fresh_exact_ordinary_pause(tmp_path, monkeypatch, failure):
    # Reuse the existing actual run()/source/grant/record/Stop harness unchanged
    # for every previous case; only this explicitly synthetic loss is injected.
    test_real_runner_waits_for_exact_start_from_idle_null_run_and_stops_owned_id(tmp_path, monkeypatch, failure)



@pytest.mark.parametrize("mutation", [None, "operation", "missing_identity", "bool_revision", "accepted_type",
                                     "effect_type", "status", "reason", "uncertain", "identity_type"])
def test_opaque_mailbox_candidate_preserves_exact_nonadmission_and_identity_types(mutation):
    m = adapter()
    error = m.CommandRefusedError({"accepted": False, "status": "refused", "effect_admitted": False,
                                   "reason": "physical input mailbox refused"})
    error.operation = "input"
    error.identity = dict(run_id="owned", service_incarnation="service", controller_generation="control",
                          connection_generation="connection", host_incarnation="host", intent_revision=2)
    if mutation == "operation":
        error.operation = "grant"
    elif mutation == "missing_identity":
        error.identity.pop("connection_generation")
    elif mutation == "bool_revision":
        error.identity["intent_revision"] = True
    elif mutation == "accepted_type":
        error.response["accepted"] = 0
    elif mutation == "effect_type":
        error.response["effect_admitted"] = 0
    elif mutation == "status":
        error.response["status"] = "unknown"
    elif mutation == "reason":
        error.response["reason"] = "another refusal"
    elif mutation == "uncertain":
        error.response["uncertain"] = True
    elif mutation == "identity_type":
        error.identity["host_incarnation"] = False
    assert m.mailbox_input_refusal(error) is (mutation is None)
    assert not m.expired_input_refusal(error)  # An opaque refusal never becomes lease expiry.


@pytest.mark.parametrize("missing", ["hold_acknowledged", "pose_aligned", "native_ack", "cursor_revision", "view"])
def test_opaque_mailbox_rejoin_still_waits_for_actual_current_held_proof(missing):
    import asyncio
    import copy

    m = adapter()
    paused = recovery_state()
    paused["run"].update(status="paused", intent_revision=3)
    paused["events"] = [{"kind": "native_input_expired", "at": 100.01,
                          "run_id": "owned", "service_incarnation": "service"}]
    paused["run"]["live_input"]["cursor_intent_revision"] = 3
    paused["evidence"]["acknowledged_intent_revision"] = 3
    paused["evidence"]["accepted_host_action"]["intent_revision"] = 3
    if missing == "cursor_revision":
        paused["run"]["live_input"]["cursor_intent_revision"] = 2
    elif missing == "view":
        paused["run"]["live_input"]["required_view"]["qualified"] = False
    else:
        paused["evidence"][missing] = False
    calls = []
    class Client:
        snapshot = recovery_state()
        device, generation, connection = "device", "control", "connection"
        resume, identities = m.SessionClient.resume, m.SessionClient.identities
        async def get(self, path):
            return {"snapshot": copy.deepcopy(paused)}
        async def command(self, payload):
            calls.append(payload["op"])
            assert payload["op"] != "resume"
            return {"connection_generation": "new-qualified-connection", "snapshot": self.snapshot}
        async def wait(self, predicate, timeout):
            assert timeout == 8 and not predicate()
            raise TimeoutError("current held feedback remains unqualified")
    async def scenario():
        client = Client()
        refused = client.identities()
        guard = m.SameRunRecovery(client, lambda value: None)
        with pytest.raises(TimeoutError, match="held feedback"):
            await guard.resume(require_episode=True, refusal_identity=refused, expected_pause="native_input_expired")
        assert calls == ["connect", "release_input"] and guard.episodes == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("change", [
    None, "two_losses", "run", "service", "host", "controller", "device", "operation", "reference", "deadline",
    "uncertain", "fault", "terminal", "running", "recovering", "revision", "bool_revision",
    "old_ack", "foreign_ack", "old_stamp", "future_stamp", "view", "active_ack",
    "native_expired", "http_delay", "wall_expired", "expired_fresh", "backward_clock",
    "reader_error", "second_loss", "renewal", "renewal_after_attach", "release_loss", "resume_loss",
    "manual_event", "changed_event_time", "nonordinary", "connection",
])
def test_pending_connect_replacement_preserves_identity_proof_budget_and_original_error(monkeypatch, change, inspect=None, attachment=None):
    import asyncio
    import copy
    import time
    from types import SimpleNamespace

    m = adapter()
    clock = [1000.0]
    monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: clock[0], perf_counter=time.perf_counter))
    initial = recovery_state()
    initial["run"]["operation_id"] = str(uuid4())  # Actual Core public Start operation field.
    initial["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"] = 100.0
    paused = copy.deepcopy(initial)
    paused["run"]["status"] = "paused"
    paused["evidence"]["active_acknowledged"] = False
    paused["events"] = [{"kind": "input_expired", "at": 100.0,
                         "run_id": "owned", "service_incarnation": "service"}]
    fresh = copy.deepcopy(paused)
    fresh["run"].update(intent_revision=3, remaining_s=299.95)
    fresh["run"]["live_input"]["cursor_intent_revision"] = 3
    fresh["evidence"]["acknowledged_intent_revision"] = 3
    fresh["evidence"]["accepted_host_action"].update(intent_revision=3, acquired_at_monotonic_s=100.04)
    for key, value in {"run": "foreign", "host": "foreign", "operation": str(uuid4()),
                       "deadline": 401.0, "uncertain": True, "terminal": "stopped",
                       "running": "running", "recovering": "recovering", "revision": 5,
                       "bool_revision": True}.items():
        if change == key:
            field = {"run": "run_id", "host": "backend_incarnation", "operation": "operation_id",
                     "terminal": "status", "running": "status", "recovering": "status",
                     "revision": "intent_revision", "bool_revision": "intent_revision"}.get(key, key)
            fresh["run"][field] = value
    if change == "service": fresh["service_incarnation"] = "foreign"
    if change == "controller": fresh["controller"]["controller_generation"] = "foreign"
    if change == "device": fresh["controller"]["device_id"] = "foreign"
    if change == "reference": fresh["run"]["live_input"]["reference"][KEYS[0]] += 1
    if change == "fault": fresh["evidence"]["fault"] = "retained native fault"
    if change == "old_ack": fresh["evidence"]["accepted_host_action"]["intent_revision"] = 2
    if change == "foreign_ack": fresh["evidence"]["accepted_host_action"]["host_incarnation"] = "foreign"
    if change == "old_stamp": fresh["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"] = 99.0
    if change == "future_stamp": fresh["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"] = 101.0
    if change == "view": fresh["run"]["live_input"]["required_view"]["qualified"] = False
    if change == "active_ack": fresh["evidence"]["active_acknowledged"] = True
    if change == "expired_fresh": fresh["run"]["remaining_s"] = 0.0
    if change == "backward_clock": fresh["run"]["remaining_s"] = 300.0
    if change == "manual_event": fresh["events"][-1]["kind"] = "pause"
    if change == "changed_event_time": fresh["events"][-1]["at"] += 0.01
    if change == "nonordinary": paused["events"][-1]["kind"] = "pause"
    if change == "native_expired": paused["run"]["remaining_s"] = 0.01
    if change == "http_delay": paused["run"]["remaining_s"] = 0.5
    if change == "attachment_native_budget":
        paused["run"]["remaining_s"] = 1.4
    original = ConnectionError("original pending WSS result lost")
    calls, records = [], []

    class Client:
        snapshot = copy.deepcopy(initial)
        device, generation, connection = "device", "control", "connection"
        resume, identities = m.SessionClient.resume, m.SessionClient.identities
        transport_close = {"expected": False, "reason": "stream-ended", "close_code": 1006, "reader_error": None}
        attaches = reads = connects = 0
        lost = False
        attachment_errors = []

        async def get(self, path):
            assert path == "state"
            self.reads += 1
            clock[0] += 0.7 if change == "http_delay" and self.reads == 1 else 0.01
            self.snapshot = copy.deepcopy(paused if self.reads % 2 else fresh)
            if attachment == "later" and self.reads == 2:
                raise ConnectionError("later authenticated proof read interrupted")
            if change == "connection" and self.reads == 2:
                self.connection = "foreign-connection"
            return {"snapshot": self.snapshot}

        async def detach(self):
            self.snapshot = None
            clock[0] += 0.01

        async def attach(self):
            assert self.snapshot is None  # Old cached state cannot satisfy attachment.
            self.attaches += 1
            clock[0] += (.6 if attachment == "repeat" else 2.01 if change == "wall_expired" else 0.01)
            if attachment and attachment != "later" and (self.attaches == 1 or attachment == "repeat"):
                close = {"expected": False, "reason": "stream-ended", "close_code": 1006,
                         "reader_error": None, "observed_at_monotonic_s": clock[0]}
                if attachment == "policy":
                    close["close_code"] = 1008
                if attachment == "reader":
                    close["reader_error"] = {"message": "owner reporting unavailable"}
                if attachment == "expected":
                    close["expected"] = True
                if attachment == "reason":
                    close["reason"] = "message-error"
                if attachment in {"oversized", "unicode"}:
                    close.update(exception=("\U0001f600" if attachment == "unicode" else "x") * 20000,
                                 exception_type=("\U0001f600" if attachment == "unicode" else "y") * 20000,
                                 observed_at_monotonic_s=float("nan"), unrelated_private="never record")
                error = (TimeoutError if attachment == "timeout" else ConnectionError)("initial snapshot interrupted")
                if attachment not in {"untagged", "http"}:
                    error.initial_snapshot_transport_close = copy.deepcopy(close)
                self.transport_close = close
                self.attachment_errors.append(error)
                raise error
            self.snapshot = copy.deepcopy(fresh)

        async def command(self, payload):
            calls.append(copy.deepcopy(payload))
            clock[0] += 0.02
            op = payload["op"]
            if op == "connect":
                self.connects += 1
                if (self.connects == 1 or change == "two_losses" and self.connects == 3) and change not in {"release_loss", "resume_loss"}:
                    self.lost = True
                    if change == "reader_error":
                        self.transport_close = dict(self.transport_close, reader_error={"message": "reporting failed"})
                    raise original if self.connects == 1 else ConnectionError("later distinct connect result lost")
                if change == "second_loss":
                    raise ConnectionError("distinct replacement result lost")
                self.snapshot["run"]["intent_revision"] += 1
                revision = self.snapshot["run"]["intent_revision"]
                self.snapshot["run"]["live_input"]["cursor_intent_revision"] = revision
                self.snapshot["evidence"]["acknowledged_intent_revision"] = revision
                self.snapshot["evidence"]["accepted_host_action"]["intent_revision"] = revision
                return {"connection_generation": "new-binding", "snapshot": self.snapshot}
            if op == "release_input":
                if change == "release_loss": raise original
                return {"accepted": True}
            assert op == "resume"
            if change == "resume_loss": raise original
            self.snapshot["run"]["status"] = "running"
            self.snapshot["evidence"]["active_acknowledged"] = True
            return {"accepted": True}

        async def wait(self, predicate, timeout):
            assert predicate(), "existing current held proof is required before Resume"

    async def scenario():
        client = Client()
        def record(value):
            records.append(value)
            if attachment == "record" and value["event"] == "ordinary_attachment_interrupted":
                raise ValueError("interrupted attachment record unavailable")
        guard = m.SameRunRecovery(client, record)
        def renewal_healthy():
            if change == "renewal" and client.lost or change == "renewal_after_attach" and client.attaches:
                raise RuntimeError("original HTTP renewal failed")
        if change in {"native_expired", "http_delay"}:
            with pytest.raises(TimeoutError):
                await guard.resume(wall_deadline=1002., renewal_check=renewal_healthy)
            assert calls == [] and client.attaches == 0  # Budget expired before pending transport I/O exists.
            return
        if change in {None, "two_losses"}:
            assert not await guard.resume(wall_deadline=1002.0, expected_pause="input_expired",
                                          renewal_check=renewal_healthy)
            assert client.attaches == (2 if attachment else 1) and client.connects == 2
            assert guard.pause_recoveries == 1 and guard.episodes == 0 and guard.connect_rebindings == 1
            if change == "two_losses":
                # A completed replacement does not spend a run-wide quota.
                # A later distinct ordinary pause owns its own single attempt.
                paused.clear()
                paused.update(copy.deepcopy(client.snapshot))
                paused["run"].update(status="paused", intent_revision=5, remaining_s=299.85)
                paused["run"]["live_input"]["cursor_intent_revision"] = 5
                paused["evidence"].update(active_acknowledged=False, acknowledged_intent_revision=5)
                paused["evidence"]["accepted_host_action"].update(state="paused", intent_revision=5,
                                                                    acquired_at_monotonic_s=100.14)
                paused["events"][-1]["at"] = 100.1
                fresh.clear()
                fresh.update(copy.deepcopy(paused))
                fresh["run"].update(intent_revision=6, remaining_s=299.80)
                fresh["run"]["live_input"]["cursor_intent_revision"] = 6
                fresh["evidence"]["acknowledged_intent_revision"] = 6
                fresh["evidence"]["accepted_host_action"].update(intent_revision=6, acquired_at_monotonic_s=100.19)
                assert not await guard.resume(wall_deadline=1002.0, expected_pause="input_expired",
                                              renewal_check=renewal_healthy)
                assert client.attaches == 2 and client.connects == 4
                assert guard.pause_recoveries == 2 and guard.episodes == 0 and guard.connect_rebindings == 2
        else:
            with pytest.raises(ConnectionError) as caught:
                await guard.resume(wall_deadline=1002.0,
                                   expected_pause=None if change == "nonordinary" else "input_expired",
                                   renewal_check=renewal_healthy)
            assert caught.value is original
            assert not any(call["op"] == "resume" for call in calls) or change == "resume_loss"
            assert client.connects == (2 if change == "second_loss" else 1)
            if change in {"reader_error", "release_loss", "resume_loss", "renewal", "native_expired", "http_delay"}:
                assert client.attaches == 0
            if change not in {"reader_error", "release_loss", "resume_loss"}:
                assert records[-1]["event"] == "ordinary_connect_requalification_failed"
        if inspect:
            inspect(records, calls, original, client)
        assert not any(call["op"] in {"claim", "start", "input"} for call in calls)
    asyncio.run(asyncio.wait_for(scenario(), 0.5))


def test_input_transport_ambiguity_remains_discarded_without_connect_reattachment():
    import asyncio

    m = adapter()
    calls, records = [], []
    original = ConnectionError("input outcome remains ambiguous")
    class Input:
        kind = "virtual-leaders"
        def get_action(self):
            return {key.removeprefix("arm_"): value for key, value in workspace()["reference"].items()}
    class Client:
        snapshot = recovery_state()
        device, generation, connection, sequence = "device", "control", "connection", 0
        identities = m.SessionClient.identities
        async def command(self, command):
            calls.append(command)
            if command["op"] == "grant":
                return {"accepted": True, "grant_id": str(uuid4()), "expires": 400.0}
            assert command["op"] == "input"
            raise original
    async def scenario():
        producer = m.FrameProducer(Input())
        producer.workspace = workspace()
        producer.native_cursor = workspace()["reference"]
        with pytest.raises(ConnectionError) as caught:
            await m.send_leader_sample(Client(), producer, m.ZERO_BODY, records.append,
                                       phase="individual", progress_s=0.1)
        assert caught.value is original and not hasattr(original, "connect_identity")
        assert [call["op"] for call in calls] == ["grant", "input"]
        assert [row["event"] for row in records] == ["leader_frame_attempt"]
    asyncio.run(scenario())


@pytest.mark.parametrize("missing", ["run_id", "recipe", "backend_incarnation", "native_live_at", "deadline"])
def test_same_run_recovery_retains_all_five_required_original_run_pins(missing):
    from types import SimpleNamespace

    m = adapter()
    state = recovery_state()
    state["run"].pop(missing)
    client = SimpleNamespace(snapshot=state, device="device", generation="control")
    with pytest.raises(KeyError, match=missing):
        m.SameRunRecovery(client, lambda value: None)


@pytest.mark.parametrize("change", [
    None, "connection_loss", "run", "service", "host", "controller", "device", "operation",
    "reference", "workspace", "deadline", "uncertain", "fault", "terminal", "running", "recovering",
    "revision", "bool_revision", "manual_event", "event_at", "old_ack", "foreign_ack", "old_stamp",
    "future_stamp", "cursor", "view", "active_ack", "renewal", "renewal_after_attach", "wall_expired",
    "native_expired", "http_delay", "remaining_increased", "connection", "replacement_same_generation",
    "replacement_revision", "replacement_loss", "nested_release_loss", "resume_loss", "release_refused",
    "initial_nonordinary", "new_cursor", "new_view", "new_ack", "reader_error",
])
def test_known_connect_empty_release_loss_requires_new_fenced_authority(monkeypatch, change):
    import asyncio
    import copy
    import time
    from types import SimpleNamespace

    m = adapter()
    clock = [1000.0]
    monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: clock[0], perf_counter=time.perf_counter))
    paused = recovery_state()
    paused["run"].update(status="paused", operation_id=str(uuid4()))
    paused["evidence"].update(active_acknowledged=False)
    paused["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"] = 100.0
    paused["events"] = [{"kind": "native_input_expired", "at": 100.0,
                         "run_id": "owned", "service_incarnation": "service"}]
    if change == "initial_nonordinary":
        paused["events"][-1]["kind"] = "pause"
    if change == "native_expired":
        paused["run"]["remaining_s"] = 0.01
    if change == "http_delay":
        paused["run"]["remaining_s"] = 0.5
    original = (ConnectionError if change == "connection_loss" else TimeoutError)("original empty release unknown")
    refused = m.CommandRefusedError({"accepted": False, "status": "refused", "reason": "unsafe", "effect_admitted": False})
    calls, records = [], []

    class Client:
        device, generation, connection = "device", "control", "connection"
        identities, resume = m.SessionClient.identities, m.SessionClient.resume
        snapshot = copy.deepcopy(paused)
        reads = connects = releases = attaches = 0
        transport_close = None

        def revision(self, value):
            self.snapshot["run"]["intent_revision"] = value
            self.snapshot["run"]["live_input"]["cursor_intent_revision"] = value
            self.snapshot["evidence"]["acknowledged_intent_revision"] = value
            self.snapshot["evidence"]["accepted_host_action"].update(intent_revision=value, acquired_at_monotonic_s=100.08)

        async def get(self, path):
            assert path == "state"
            self.reads += 1
            clock[0] += 0.7 if change == "http_delay" and self.reads == 1 else 0.01
            if self.reads == 1:
                self.snapshot = copy.deepcopy(paused)
            else:
                self.snapshot["run"]["remaining_s"] = 299.9
                if change in {"run", "host", "operation", "deadline", "uncertain", "terminal", "running", "recovering", "revision", "bool_revision"}:
                    key, value = {
                        "run": ("run_id", "foreign"), "host": ("backend_incarnation", "foreign"),
                        "operation": ("operation_id", str(uuid4())), "deadline": ("deadline", 401.0),
                        "uncertain": ("uncertain", True), "terminal": ("status", "stopped"),
                        "running": ("status", "running"), "recovering": ("status", "recovering"),
                        "revision": ("intent_revision", 4), "bool_revision": ("intent_revision", True),
                    }[change]
                    self.snapshot["run"][key] = value
                if change == "service":
                    self.snapshot["service_incarnation"] = "foreign"
                if change == "controller":
                    self.snapshot["controller"]["controller_generation"] = "foreign"
                if change == "device":
                    self.snapshot["controller"]["device_id"] = "foreign"
                if change == "reference":
                    self.snapshot["run"]["live_input"]["reference"][KEYS[0]] += 1
                if change == "workspace":
                    self.snapshot["run"]["live_input"]["workspace"]["reference"][KEYS[0]] += 1
                if change == "fault":
                    self.snapshot["evidence"]["fault"] = "native fault"
                if change == "manual_event":
                    self.snapshot["events"][-1]["kind"] = "pause"
                if change == "event_at":
                    self.snapshot["events"][-1]["at"] += .01
                if change == "old_ack":
                    self.snapshot["evidence"]["accepted_host_action"]["intent_revision"] = 2
                if change == "foreign_ack":
                    self.snapshot["evidence"]["accepted_host_action"]["host_incarnation"] = "foreign"
                if change == "old_stamp":
                    self.snapshot["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"] = 99.0
                if change == "future_stamp":
                    self.snapshot["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"] = 101.0
                if change == "cursor":
                    self.snapshot["run"]["live_input"]["cursor_intent_revision"] = 2
                if change == "view":
                    self.snapshot["run"]["live_input"]["required_view"]["qualified"] = False
                if change == "active_ack":
                    self.snapshot["evidence"]["active_acknowledged"] = True
                if change == "remaining_increased":
                    self.snapshot["run"]["remaining_s"] = 300.0
                if change == "connection":
                    self.connection = "foreign"
            return {"snapshot": copy.deepcopy(self.snapshot)}

        async def detach(self):
            self.saved = copy.deepcopy(self.snapshot)
            self.snapshot = None
            clock[0] += .01

        async def attach(self):
            assert self.snapshot is None
            self.attaches += 1
            self.snapshot = copy.deepcopy(self.saved)
            clock[0] += 2.01 if change == "wall_expired" else .01

        async def command(self, payload):
            calls.append(copy.deepcopy(payload))
            clock[0] += .02
            if payload["op"] == "connect":
                self.connects += 1
                if self.connects == 2 and change == "replacement_loss":
                    raise ConnectionError("new connect unknown")
                self.snapshot = copy.deepcopy(self.snapshot)  # Core replies do not mutate the prior HTTP receipt.
                self.revision(self.snapshot["run"]["intent_revision"] + (2 if self.connects == 2 and change == "replacement_revision" else 1))
                gen = "accepted-1" if change == "replacement_same_generation" else "accepted-" + str(self.connects)
                return {"connection_generation": gen, "snapshot": copy.deepcopy(self.snapshot)}
            if payload["op"] == "release_input":
                self.releases += 1
                if self.releases == 1:
                    if change == "release_refused":
                        raise refused
                    if change == "reader_error":
                        self.transport_close = {"reader_error": {"message": "report failed"}}
                    raise original
                if change == "nested_release_loss":
                    raise TimeoutError("replacement empty release unknown")
                if change == "new_cursor":
                    self.snapshot["run"]["live_input"]["cursor_intent_revision"] -= 1
                if change == "new_view":
                    self.snapshot["run"]["live_input"]["required_view"]["qualified"] = False
                if change == "new_ack":
                    self.snapshot["evidence"]["accepted_host_action"]["intent_revision"] -= 1
                return {"accepted": True}
            assert payload["op"] == "resume"
            if change == "resume_loss":
                raise TimeoutError("Resume outcome unknown")
            self.snapshot["run"].update(status="running", intent_revision=self.snapshot["run"]["intent_revision"] + 1)
            self.snapshot["evidence"]["active_acknowledged"] = True
            return {"accepted": True}

        async def wait(self, predicate, timeout):
            if not predicate():
                raise TimeoutError("fresh current held cursor/view/ACK unavailable")

    async def scenario():
        client = Client()
        guard = m.SameRunRecovery(client, records.append)
        def healthy():
            if change == "renewal" and client.releases or change == "renewal_after_attach" and client.attaches:
                raise RuntimeError("original renewal failed")
        if change in {"native_expired", "http_delay"}:
            with pytest.raises(TimeoutError):
                await guard.resume(wall_deadline=1002., renewal_check=healthy)
            assert calls == [] and client.attaches == 0  # No original release was attempted after expiry.
            return
        if change in {None, "connection_loss"}:
            assert not await guard.resume(wall_deadline=1002., renewal_check=healthy)
            assert [v["op"] for v in calls] == ["connect", "release_input", "connect", "release_input", "resume"]
            assert calls[1]["connection_generation"] != calls[3]["connection_generation"]
            assert client.attaches == 1 and guard.episodes == guard.pause_recoveries == 0
            assert guard.original["run"]["deadline"] == 400. and client.generation == "control"
        else:
            with pytest.raises(type(refused) if change == "release_refused" else type(original)) as caught:
                await guard.resume(wall_deadline=1002., renewal_check=healthy)
            assert caught.value is (refused if change == "release_refused" else original)
            if change not in {"resume_loss"}:
                assert not any(v["op"] == "resume" for v in calls)
            assert client.connects <= 2 and client.releases <= 2 and client.attaches <= 1
        assert not any(v["op"] in {"claim", "start", "input", "grant"} for v in calls)
    asyncio.run(asyncio.wait_for(scenario(), .5))


@pytest.mark.parametrize("expired", [False, True])
def test_interrupted_resume_defers_episode_credit_and_preserves_original_episode_ceiling(monkeypatch, expired):
    import asyncio
    import copy
    import time
    from types import SimpleNamespace

    m = adapter()
    pc = [1000.0]
    monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: pc[0], perf_counter=time.perf_counter))
    first, ready = recovering_state(), requalified_state()
    records, resumes = [], []

    class Client:
        snapshot = recovery_state()
        device, generation, connection = "device", "control", "connection"
        identities = m.SessionClient.identities
        reads = 0

        async def get(self, path):
            self.reads += 1
            if self.reads == 1:
                self.snapshot = copy.deepcopy(first)
            elif self.reads == 2:
                pc[0] += .05
                self.snapshot = copy.deepcopy(ready)
            else:
                pc[0] += .5 if expired else .05
            return {"snapshot": copy.deepcopy(self.snapshot)}

        async def resume(self, *, check, refusal_record=None):
            check(self.snapshot)
            resumes.append(pc[0])
            assert not any(r["event"] == "qualified_native_recovery" for r in records)
            if len(resumes) == 1:
                # Model known connect(+1), accepted Resume(+1), ordinarypause(+1).
                identity = dict(self.identities(), intent_revision=self.snapshot["run"]["intent_revision"] + 1)
                self.snapshot["run"].update(intent_revision=6, remaining_s=299.8)
                event = {"kind": "required_view_expired", "at": 100.2,
                         "run_id": "owned", "service_incarnation": "service"}
                self.snapshot["events"].append(event)
                raise m._ResumePausedError(identity, event)
            assert pc[0] < 1000.39, "continued beyond the original episode ceiling"
            self.snapshot["run"]["status"] = "running"
            self.snapshot["evidence"]["active_acknowledged"] = True

    async def scenario():
        client = Client()
        guard = m.SameRunRecovery(client, records.append)
        if expired:
            with pytest.raises(TimeoutError):
                await guard.resume(wall_deadline=1002.)
            assert len(resumes) == 1
            assert not any(r["event"] == "qualified_native_recovery" for r in records)
        else:
            assert await guard.resume(wall_deadline=1002.) is True
            assert len(resumes) == 2
            qualified = [r for r in records if r["event"] == "qualified_native_recovery"]
            assert len(qualified) == 1 and qualified[0]["original_recovery"] == first["run"]["recovery"]
        assert guard.episodes == 1 and guard.cause == "feedback_loss"
    asyncio.run(asyncio.wait_for(scenario(), .7))


@pytest.mark.parametrize("mutation", [
    None, "twice", "manual", "foreign_event", "event_extra", "old_event", "before_request",
    "revision_plus1", "revision_plus3", "bool_revision", "connection", "foreign_run", "foreign_host",
    "reference", "workspace", "deadline", "uncertain", "fault", "terminal", "recovering",
    "remaining_increased", "remaining_invalid", "wall_budget", "native_budget", "missing_ack",
    "false_ack", "wrong_ack_status", "resume_timeout", "resume_connection", "fresh_event_changed",
    "fresh_revision", "renewal", "renewal_initial",
])
def test_accepted_resume_superseding_pause_preserves_identity_proof_and_one_clock(monkeypatch, mutation):
    import asyncio
    import copy
    import time
    from types import SimpleNamespace

    m = adapter()
    pc = [1000.0]
    monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: pc[0], perf_counter=time.perf_counter))
    original = TimeoutError("original Resume outcome unknown") if mutation == "resume_timeout" else ConnectionError("original Resume result lost")
    state = recovery_state()
    state["run"].update(status="paused", operation_id=str(uuid4()), remaining_s=.15 if mutation == "native_budget" else 299.99)
    state["evidence"]["active_acknowledged"] = False
    pi_start = state["run"]["deadline"] - state["run"]["remaining_s"]
    state["events"] = [{"kind": "required_view_expired", "at": pi_start - .01,
                         "run_id": "owned", "service_incarnation": "service"}]
    calls, records = [], []

    class Client:
        snapshot = copy.deepcopy(state)
        device, generation, connection = "device", "control", "connection"
        identities, resume = m.SessionClient.identities, m.SessionClient.resume
        reads = connects = resumes = 0

        async def get(self, path):
            self.reads += 1
            pc[0] += .8 if mutation == "wall_budget" and self.reads > 1 else .01
            if self.reads > 1:
                self.snapshot["run"]["remaining_s"] = state["run"]["remaining_s"] - self.resumes * .01
                if mutation == "remaining_increased":
                    self.snapshot["run"]["remaining_s"] = 300.
                if mutation == "remaining_invalid":
                    self.snapshot["run"]["remaining_s"] = True
                if mutation == "fresh_event_changed":
                    self.snapshot["events"][-1]["at"] += .001
                if mutation == "fresh_revision":
                    self.snapshot["run"]["intent_revision"] += 1
            return {"snapshot": copy.deepcopy(self.snapshot)}

        async def command(self, payload):
            calls.append(copy.deepcopy(payload))
            pc[0] += .02
            if payload["op"] == "connect":
                self.connects += 1
                self.snapshot = copy.deepcopy(self.snapshot)
                revision = self.snapshot["run"]["intent_revision"] + 1
                self.snapshot["run"].update(intent_revision=revision, status="paused")
                self.snapshot["run"]["live_input"]["cursor_intent_revision"] = revision
                self.snapshot["evidence"].update(hold_acknowledged=True, pose_aligned=True, native_ack=True,
                                                active_acknowledged=False, acknowledged_intent_revision=revision)
                self.snapshot["evidence"]["accepted_host_action"].update(intent_revision=revision, state="paused")
                return {"connection_generation": "connection-" + str(self.connects), "snapshot": self.snapshot}
            if payload["op"] == "release_input":
                return {"accepted": True}
            assert payload["op"] == "resume"
            self.resumes += 1
            if mutation in {"resume_timeout", "resume_connection"}:
                raise original
            if self.resumes <= (2 if mutation == "twice" else 1) or mutation == "wall_budget":
                revision = payload["intent_revision"] + 2
                if mutation == "revision_plus1":
                    revision -= 1
                if mutation == "revision_plus3":
                    revision += 1
                if mutation == "bool_revision":
                    revision = True
                self.snapshot["run"].update(status="paused", intent_revision=revision)
                event = {"kind": "required_view_expired", "at": pi_start + self.resumes * .01,
                         "run_id": "owned", "service_incarnation": "service"}
                if mutation == "manual":
                    event["kind"] = "pause"
                if mutation == "foreign_event":
                    event["run_id"] = "foreign"
                if mutation == "event_extra":
                    event["extra"] = True
                if mutation == "old_event":
                    event["at"] = self.snapshot["events"][-1]["at"]
                if mutation == "before_request":
                    event["at"] = pi_start - .001
                self.snapshot["events"].append(event)
                if mutation == "connection":
                    self.connection = "foreign"
                if mutation in {"foreign_run", "foreign_host", "deadline", "uncertain", "terminal", "recovering"}:
                    key, value = {"foreign_run": ("run_id", "foreign"), "foreign_host": ("backend_incarnation", "foreign"),
                                  "deadline": ("deadline", 401.), "uncertain": ("uncertain", True),
                                  "terminal": ("status", "stopped"), "recovering": ("status", "recovering")}[mutation]
                    self.snapshot["run"][key] = value
                if mutation == "reference":
                    self.snapshot["run"]["live_input"]["reference"][KEYS[0]] += 1
                if mutation == "workspace":
                    self.snapshot["run"]["live_input"]["workspace"]["reference"][KEYS[0]] += 1
                if mutation == "fault":
                    self.snapshot["evidence"]["fault"] = "real fault"
            else:
                self.snapshot["run"].update(status="running", intent_revision=payload["intent_revision"] + 1)
                self.snapshot["evidence"]["active_acknowledged"] = True
            if mutation == "native_budget":
                pc[0] += .2
            result = {"accepted": True, "status": "resuming", "effect_admitted": False}
            if mutation == "missing_ack":
                result.pop("accepted")
            if mutation == "false_ack":
                result["accepted"] = False
            if mutation == "wrong_ack_status":
                result["status"] = "unknown"
            return result

        async def wait(self, predicate, timeout):
            if not predicate():
                raise TimeoutError("actual active ACK unavailable")

    async def scenario():
        client = Client()
        guard = m.SameRunRecovery(client, records.append)
        def healthy():
            if mutation == "renewal_initial" or mutation == "renewal" and client.resumes:
                raise RuntimeError("original renewal failed")
        if mutation in {None, "twice"}:
            assert await guard.resume(wall_deadline=1002., renewal_check=healthy) is False
            assert client.resumes == (3 if mutation == "twice" else 2)
            assert client.snapshot["evidence"]["active_acknowledged"] is True
            assert len({c["operation_id"] for c in calls if c["op"] == "resume"}) == client.resumes
        else:
            with pytest.raises((RuntimeError, TimeoutError, ConnectionError)) as caught:
                await guard.resume(wall_deadline=1002., renewal_check=healthy)
            if mutation in {"resume_timeout", "resume_connection"}:
                assert caught.value is original
            if mutation == "renewal_initial":
                assert not calls
            elif mutation == "wall_budget":
                assert pc[0] >= 1002. and 1 < client.resumes < 5
            else:
                assert client.resumes == 1
            assert not any(r["event"] == "qualified_native_recovery" for r in records)
        assert guard.episodes == 0 and guard.original["run"]["deadline"] == 400.
        assert not any(c["op"] in {"grant", "input", "claim", "start"} for c in calls)
    asyncio.run(asyncio.wait_for(scenario(), .6))


def test_initial_runner_waits_through_superseding_pause_before_grant_and_progress(tmp_path, monkeypatch):
    test_real_runner_waits_for_exact_start_from_idle_null_run_and_stops_owned_id(
        tmp_path, monkeypatch, "initial_resume_pause")


@pytest.mark.parametrize("mutation", [
    None, "receipt_accepted", "receipt_effect", "receipt_reason", "receipt_uncertain", "manual_origin",
    "connection", "revision", "bool_revision", "foreign_run", "owner", "manual_event", "foreign_event",
    "recovering", "uncertain", "fault", "reference", "remaining_increased", "stale_hold", "cursor",
    "cached_view_only", "caller_budget", "native_budget", "phase_budget", "renewal", "later_refusal",
    "later_timeout", "active_missing",
])
def test_nonadmitted_view_resume_requires_fresh_owner_proof_and_original_phase(monkeypatch, mutation):
    import asyncio
    import copy
    import time
    from types import SimpleNamespace

    m = adapter()
    pc = [1000.]
    monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: pc[0], perf_counter=time.perf_counter))
    state = recovery_state()
    state["run"].update(status="paused", operation_id=str(uuid4()), remaining_s=.15 if mutation == "native_budget" else 299.)
    state["evidence"]["active_acknowledged"] = False
    state["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"] = 101.
    state["events"] = [{"kind": "pause" if mutation == "manual_origin" else "input_expired", "at": 100.9,
                            "run_id": "owned", "service_incarnation": "service"}]
    receipt = {"accepted": False, "status": "refused", "effect_admitted": False,
                   "reason": "current required selected view must advance before Resume"}
    for name, update in {"receipt_accepted": {"accepted": None}, "receipt_effect": {"effect_admitted": None},
                         "receipt_reason": {"reason": "qualified release/alignment required"},
                         "receipt_uncertain": {"uncertain": True}}.items():
        if mutation == name:
            receipt.update(update)
    original = m.CommandRefusedError(receipt, timing={"request_started_at_monotonic_s": 42.})
    later = TimeoutError("later Resume outcome unknown")
    calls, records, waits = [], [], []

    class Client:
        snapshot = copy.deepcopy(state)
        device, generation, connection = "device", "control", "connection"
        identities, resume = m.SessionClient.identities, m.SessionClient.resume
        reads = resumes = 0

        async def get(self, path):
            self.reads += 1
            pc[0] += .01
            fresh = copy.deepcopy(self.snapshot)
            if self.reads > 1:
                fresh["run"]["remaining_s"] -= .01
                fresh["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"] = fresh["run"]["deadline"] - fresh["run"]["remaining_s"]
                if mutation == "connection":
                    self.connection = "foreign"
                if mutation in {"revision", "bool_revision", "foreign_run", "recovering", "uncertain", "remaining_increased"}:
                    key, value = {"revision": ("intent_revision", 4), "bool_revision": ("intent_revision", True),
                                  "foreign_run": ("run_id", "foreign"), "recovering": ("status", "recovering"),
                                  "uncertain": ("uncertain", True), "remaining_increased": ("remaining_s", 300.)}[mutation]
                    fresh["run"][key] = value
                if mutation == "owner":
                    fresh["controller"]["device_id"] = "foreign"
                if mutation == "manual_event":
                    fresh["events"].append(dict(fresh["events"][-1], kind="pause", at=100.95))
                if mutation == "foreign_event":
                    fresh["events"][-1]["run_id"] = "foreign"
                if mutation == "fault":
                    fresh["evidence"]["fault"] = "actual hard fault"
                if mutation == "reference":
                    fresh["run"]["live_input"]["reference"][KEYS[0]] += 1
                if mutation == "stale_hold":
                    fresh["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"] = 100.
                if mutation == "cursor":
                    fresh["run"]["live_input"]["cursor_intent_revision"] -= 1
                if mutation == "cached_view_only":
                    fresh["run"]["live_input"]["required_view"]["qualified"] = False
                if mutation in {"caller_budget", "native_budget"}:
                    pc[0] += 2.
            return {"snapshot": fresh}

        async def command(self, payload):
            calls.append(copy.deepcopy(payload))
            pc[0] += .02
            if payload["op"] == "connect":
                self.snapshot = copy.deepcopy(self.snapshot)
                self.snapshot["run"]["intent_revision"] += 1
                revision = self.snapshot["run"]["intent_revision"]
                self.snapshot["run"]["live_input"]["cursor_intent_revision"] = revision
                self.snapshot["evidence"]["acknowledged_intent_revision"] = revision
                self.snapshot["evidence"]["accepted_host_action"]["intent_revision"] = revision
                return {"connection_generation": "new-connection", "snapshot": self.snapshot}
            if payload["op"] == "release_input":
                return {"accepted": True}
            assert payload["op"] == "resume"
            self.resumes += 1
            if self.resumes == 1 or mutation == "phase_budget":
                raise original
            if mutation == "later_timeout":
                raise later
            if mutation == "later_refusal":
                raise m.CommandRefusedError(dict(receipt, reason="stale or malformed intent revision"))
            self.snapshot["run"].update(status="running", intent_revision=payload["intent_revision"] + 1)
            self.snapshot["evidence"]["active_acknowledged"] = mutation != "active_missing"
            return {"accepted": True, "status": "resuming"}

        async def wait(self, predicate, timeout):
            waits.append(timeout)
            if self.resumes:
                pc[0] += .2
            if not predicate():
                if mutation in {"cached_view_only", "cursor"}:
                    # Even an old WSS True/current cursor cannot replace the
                    # following authenticated owner's missing proof.
                    self.snapshot["run"]["live_input"]["required_view"]["qualified"] = True
                    self.snapshot["run"]["live_input"]["cursor_intent_revision"] = self.snapshot["run"]["intent_revision"]
                    pc[0] += .2
                    assert predicate()
                else:
                    raise TimeoutError("actual active ACK unavailable")

    async def scenario():
        client = Client()
        guard = m.SameRunRecovery(client, records.append)
        def healthy():
            if mutation == "renewal" and client.resumes:
                raise RuntimeError("original renewal task failed")
        deadline = 1009. if mutation == "phase_budget" else 1002.
        if mutation is None:
            assert await guard.resume(expected_pause="input_expired", wall_deadline=deadline, renewal_check=healthy) is False
            assert client.resumes == 2 and client.reads == 2
            assert client.snapshot["evidence"]["active_acknowledged"] is True
            assert len({c["operation_id"] for c in calls if c["op"] == "resume"}) == 2
        else:
            with pytest.raises(m.CommandRefusedError) as caught:
                await guard.resume(expected_pause=None if mutation == "manual_origin" else "input_expired",
                                   wall_deadline=deadline, renewal_check=healthy)
            assert caught.value is original
            if mutation in {"later_refusal", "later_timeout", "active_missing"}:
                assert client.resumes == 2 and original.__cause__ is not None
                if mutation == "later_timeout":
                    assert original.__cause__ is later
            elif mutation == "phase_budget":
                assert client.resumes > 2 and 1008. <= pc[0] < 1009.
                assert min(waits) < max(waits) and original.__cause__ is not None
            else:
                assert client.resumes == 1
            if mutation.startswith("receipt_") or mutation == "manual_origin":
                assert client.reads == 1
            assert not any(r["event"] in {"qualified_native_recovery", "accepted_resume_superseded"} for r in records)
        assert sum(c["op"] == "connect" for c in calls) == 1
        assert sum(c["op"] == "release_input" for c in calls) == 1
        assert not any(c["op"] in {"claim", "start", "grant", "input"} for c in calls)
        assert guard.episodes == 0 and guard.original["run"]["deadline"] == 400.
        if not (mutation and (mutation.startswith("receipt_") or mutation == "manual_origin")):
            assert records[-1]["event"] == "resume_view_refused" or any(r["event"] == "resume_view_refused" for r in records)
            context = original.resume_view_context
            assert context["receipt"] == receipt and context["timing"] == original.timing
            assert context["operation_id"] == next(c["operation_id"] for c in calls if c["op"] == "resume")
    asyncio.run(asyncio.wait_for(scenario(), .8))


@pytest.mark.parametrize("change,label", [("view", "required_view"), ("old_ack", "ack_revision"),
                                          ("old_stamp", "ack_clock"), ("backward_clock", "remaining_clock"),
                                          ("revision", "intent_revision"), ("connection", "connection"),
                                          ("active_ack", "active_ack"), ("manual_event", "pause_event")])
def test_failed_connect_qualification_retains_compared_proof_without_changing_first_error(monkeypatch, change, label):
    import json

    def inspect(records, calls, original, client):
        failure = records[-1]
        assert failure["event"] == "ordinary_connect_requalification_failed"
        detail = failure["qualification"]
        assert label in detail["failed"]
        assert detail["authority"]["status"] == client.snapshot["run"]["status"]
        assert detail["proof"]["view"]["qualified"] == client.snapshot["run"]["live_input"]["required_view"]["qualified"]
        assert detail["clocks"]["original_remaining_s"] == 299.99
        assert detail["clocks"]["fresh_remaining_s"] == client.snapshot["run"]["remaining_s"]
        assert isinstance(original.__cause__, RuntimeError)
        assert str(original.__cause__) == "current ordinary paused binding/held feedback unavailable"
        assert original.__cause__.qualification == detail
        assert len(json.dumps(detail, allow_nan=False).encode()) <= 8192
        assert not any(name in json.dumps(detail) for name in ("workspace", "reference", "arm_feedback", "settings", "cookies"))
        assert client.connects == 1 and client.attaches == 1
    test_pending_connect_replacement_preserves_identity_proof_budget_and_original_error(monkeypatch, change, inspect)


@pytest.mark.parametrize("stage", ["success", "construction", "record"])
def test_connect_qualification_evidence_is_fault_only_and_preserves_first_error(monkeypatch, stage):
    m = adapter()
    constructed = []
    if stage in {"success", "construction"}:
        def unavailable(*args):
            constructed.append(True)
            raise ValueError("diagnostic-only failure containing private content")
        monkeypatch.setattr(m, "_paused_binding_diagnostic", unavailable)
    else:
        original_init = m.SameRunRecovery.__init__
        def initialized(self, client, record):
            def broken_record(value):
                record(value)
                if value["event"] == "ordinary_connect_requalification_failed":
                    raise ValueError("record-only failure")
            original_init(self, client, broken_record)
        monkeypatch.setattr(m.SameRunRecovery, "__init__", initialized)

    def inspect(records, calls, original, client):
        if stage == "success":
            assert not constructed and original.__cause__ is None
            assert [call["op"] for call in calls] == ["connect", "connect", "release_input", "resume"]
            assert not any("qualification" in row for row in records)
            assert not any(row["event"].endswith("_failed") for row in records)
        else:
            assert original.__cause__.__class__ is RuntimeError
            assert str(original.__cause__) == "current ordinary paused binding/held feedback unavailable"
            failure = records[-1]
            assert failure["message"] == str(original.__cause__)
            assert "private content" not in str(failure)
            assert ("qualification" in failure) is (stage == "record")
            assert client.connects == 1 and client.attaches == 1
            if stage == "construction":
                assert constructed == [True]
    test_pending_connect_replacement_preserves_identity_proof_budget_and_original_error(
        monkeypatch, None if stage == "success" else "view", inspect)


def test_failed_connect_qualification_projection_is_bounded_and_json_safe():
    import copy
    import json

    m = adapter()
    state = recovery_state()
    fresh = copy.deepcopy(state)
    identity = {"run_id": "owned", "service_incarnation": "service", "host_incarnation": "host",
                "controller_generation": "control", "connection_generation": "connection", "intent_revision": 2}
    event = {"kind": "input_expired", "at": 100., "run_id": "owned", "service_incarnation": "service"}
    fresh["run"].update(status="paused", remaining_s=True)
    fresh["events"] = [dict(event, kind="x" * 20000)]
    fresh["run"]["live_input"]["required_view"] = {"qualified": False, "cause": "x" * 20000, "role": {"private": "secret"}}
    fresh["evidence"]["accepted_host_action"].update(host_incarnation="x" * 20000, acquired_at_monotonic_s=float("nan"))
    detail = m._paused_binding_diagnostic(state, fresh, identity, event, False, "connection", 299.99, 1002.)
    encoded = json.dumps(detail, allow_nan=False)
    assert len(encoded.encode()) <= 8192
    assert "secret" not in encoded and "private" not in encoded
    assert detail["proof"]["view"]["role"] == "<invalid>"
    assert detail["clocks"]["fresh_ack_at"] == "<invalid>"
    assert detail["clocks"]["fresh_remaining_s"] is True  # Preserve the invalid bool's actual type.
    assert {"pause_event", "ack_host", "required_view", "clock_types"} <= set(detail["failed"])
    def strings(value):
        if isinstance(value, dict):
            return [item for child in value.values() for item in strings(child)]
        if isinstance(value, list):
            return [item for child in value for item in strings(child)]
        return [value] if isinstance(value, str) else []
    assert max(map(len, strings(detail))) <= 128


@pytest.mark.parametrize("change", [None, "run", "view", "old_ack", "revision", "manual_event", "renewal_after_attach"])
def test_initial_snapshot_transport_retry_still_requires_unchanged_current_proof(monkeypatch, change):
    def inspect(records, calls, original, client):
        attempts = [row for row in records if row["event"] == "ordinary_attachment_interrupted"]
        if change == "renewal_after_attach":
            assert attempts == [] and client.attaches == 1
        else:
            assert len(attempts) == 1 and attempts[0]["phase"] == "initial_snapshot"
            assert attempts[0]["remaining_s"] < 2 and client.attaches == 2
        assert client.connects == (2 if change is None else 1)
        if change is None:
            assert [call["op"] for call in calls] == ["connect", "connect", "release_input", "resume"]
        else:
            assert original.__cause__ is not None
            assert not any(call["op"] == "resume" for call in calls)
    test_pending_connect_replacement_preserves_identity_proof_budget_and_original_error(
        monkeypatch, change, inspect, attachment="retry")


@pytest.mark.parametrize("failure", ["policy", "reader", "reason", "expected", "untagged", "http", "timeout", "later"])
def test_initial_snapshot_retry_rejects_all_other_attachment_or_later_failures(monkeypatch, failure):
    def inspect(records, calls, original, client):
        assert client.attaches == 1 and client.connects == 1
        assert not any(row["event"] == "ordinary_attachment_interrupted" for row in records)
        if failure != "later":
            assert original.__cause__ is client.attachment_errors[0]
        assert [call["op"] for call in calls] == ["connect"]
    test_pending_connect_replacement_preserves_identity_proof_budget_and_original_error(
        monkeypatch, "attachment_error", inspect, attachment=failure)


@pytest.mark.parametrize("budget", ["caller", "native"])
def test_repeated_initial_snapshot_losses_spend_one_original_ceiling(monkeypatch, budget):
    def inspect(records, calls, original, client):
        attempts = [row for row in records if row["event"] == "ordinary_attachment_interrupted"]
        assert len(attempts) >= 2 and client.attaches == len(attempts) + 1
        assert all(later["remaining_s"] < earlier["remaining_s"] for earlier, later in zip(attempts, attempts[1:], strict=False))
        assert isinstance(original.__cause__, TimeoutError)
        assert str(original.__cause__) == "original rejoin ceiling exhausted"
        assert [call["op"] for call in calls] == ["connect"] and client.connects == 1
    test_pending_connect_replacement_preserves_identity_proof_budget_and_original_error(
        monkeypatch, "attachment_native_budget" if budget == "native" else "attachment_budget", inspect,
        attachment="repeat")


@pytest.mark.parametrize("stage", ["oversized", "unicode", "record"])
def test_initial_snapshot_interruption_record_is_bounded_and_keeps_first_error(monkeypatch, stage):
    import json

    def inspect(records, calls, original, client):
        row = next(row for row in records if row["event"] == "ordinary_attachment_interrupted")
        assert len(json.dumps(row, allow_nan=False).encode()) < 1024
        if stage == "record":
            assert isinstance(original.__cause__, ValueError) and client.attaches == 1
            assert [call["op"] for call in calls] == ["connect"]
        else:
            assert "unrelated_private" not in row["transport_close"]
            assert 0 < len(row["transport_close"]["exception"].encode("utf-8")) <= 32
            assert 0 < len(row["transport_close"]["exception_type"].encode("utf-8")) <= 32
            assert row["transport_close"]["observed_at_monotonic_s"] is None
            assert client.attaches == 2 and client.connects == 2
    test_pending_connect_replacement_preserves_identity_proof_budget_and_original_error(
        monkeypatch, "attachment_record" if stage == "record" else None, inspect, attachment=stage)


@pytest.mark.parametrize("change", [
    None, "attach_retry", "unicode", "active", "view", "hold", "hold_before_event", "cursor", "revision", "manual", "foreign",
    "remaining", "renewal", "caller_budget", "active_budget", "native_budget", "continuation_budget",
    "later_loss", "record", "later_record", "wrong_exception", "wrong_reason", "reader_error",
    "expected_close", "unknown_ack", "resume_loss", "attach_timeout", "nonordinary", "history",
])
def test_known_resume_no_pong_requires_new_ordinary_proof_and_original_clocks(monkeypatch, change):
    import asyncio
    import copy
    import json
    import time
    from types import SimpleNamespace

    from aiohttp import ServerTimeoutError

    m = adapter()
    pc = [1000.]
    monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: pc[0], perf_counter=time.perf_counter))
    state = recovery_state()
    state["run"].update(status="paused", operation_id=str(uuid4()),
                        remaining_s=.2 if change == "native_budget" else 299.)
    state["evidence"]["active_acknowledged"] = False
    pi_lower = state["run"]["deadline"] - state["run"]["remaining_s"]
    state["events"] = [{"kind": "pause" if change == "nonordinary" else "input_expired", "at": pi_lower - .01,
                        "run_id": "owned", "service_incarnation": "service"}]
    state["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"] = pi_lower
    if change == "unicode":
        state["service_incarnation"] = state["events"][-1]["service_incarnation"] = "\U0001f9ed" * 4000
    original = ConnectionError("current WSS connection ended: message-error")
    stream_error = ServerTimeoutError("No PONG received after 0.5 seconds")
    if change == "wrong_exception":
        stream_error = RuntimeError(str(stream_error))
    if change == "wrong_reason":
        stream_error = ServerTimeoutError("other server timeout")
    calls, records = [], []

    class Client:
        snapshot = copy.deepcopy(state)
        device, generation, connection = "device", "control", "connection"
        identities, resume = m.SessionClient.identities, m.SessionClient.resume
        ws = SimpleNamespace(exception=lambda: stream_error, _pong_heartbeat=.5)
        transport_close = {"expected": change == "expected_close", "reason": "message-error", "close_code": 1006,
                           "exception_type": type(stream_error).__name__, "exception": str(stream_error),
                           "reader_error": {"message": "reporting failed"} if change == "reader_error" else None}
        attaches = connects = resumes = reads = 0
        saved = None

        async def get(self, path):
            assert path == "state"
            self.reads += 1
            pc[0] += .01
            if self.reads > 1:
                if change == "continuation_budget" and self.reads > 2:
                    pc[0] += 5.1
                fresh = copy.deepcopy(self.saved)
                if change == "active":
                    fresh["run"]["status"] = "running"
                if change in {"view", "cursor"}:
                    fresh["run"]["live_input"]["required_view"]["qualified"] = change != "view"
                    if change == "cursor":
                        fresh["run"]["live_input"]["cursor_intent_revision"] -= 1
                if change == "hold":
                    fresh["evidence"]["accepted_host_action"]["intent_revision"] -= 1
                if change == "hold_before_event":
                    fresh["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"] = pi_lower + .005
                if change == "revision":
                    fresh["run"]["intent_revision"] += 1
                if change == "manual":
                    fresh["events"].append(dict(fresh["events"][-1], kind="pause", at=pi_lower + .02))
                if change == "foreign":
                    fresh["controller"]["device_id"] = "foreign"
                if change == "remaining":
                    fresh["run"]["remaining_s"] = 300.
                if change == "history":
                    fresh["run"].update(recovery_episodes=1, first_cause="feedback_loss", status="recovering")
                return {"snapshot": fresh}
            return {"snapshot": copy.deepcopy(self.snapshot)}

        async def detach(self):
            assert not any(c["op"] in {"input", "grant"} for c in calls)
            self.snapshot = None
            pc[0] += .01

        async def attach(self):
            assert self.snapshot is None
            self.attaches += 1
            pc[0] += 2.1 if change == "caller_budget" else 5.1 if change == "active_budget" else .3 if change == "native_budget" else .01
            if change == "attach_retry" and self.attaches == 1:
                interrupted = ConnectionError("fresh stream ended before snapshot")
                interrupted.initial_snapshot_transport_close = {
                    "expected": False, "reason": "stream-ended", "close_code": 1006, "reader_error": None}
                raise interrupted
            if change == "attach_timeout":
                raise TimeoutError("original three-second attachment timed out")
            self.snapshot = copy.deepcopy(self.saved)

        async def command(self, command):
            calls.append(copy.deepcopy(command))
            pc[0] += .01
            if command["op"] == "connect":
                self.connects += 1
                if self.connects > 1 and change == "later_loss":
                    raise ConnectionError("later replacement command lost")
                self.snapshot = copy.deepcopy(self.snapshot)
                revision = self.snapshot["run"]["intent_revision"] + 1
                self.snapshot["run"].update(intent_revision=revision, status="paused")
                self.snapshot["run"]["live_input"]["cursor_intent_revision"] = revision
                self.snapshot["evidence"].update(active_acknowledged=False, acknowledged_intent_revision=revision)
                self.snapshot["evidence"]["accepted_host_action"].update(intent_revision=revision, state="paused")
                return {"connection_generation": "known-" + str(self.connects), "snapshot": self.snapshot}
            if command["op"] == "release_input":
                return {"accepted": True}
            assert command["op"] == "resume"
            self.resumes += 1
            if change == "resume_loss":
                raise original  # Unknown Resume result is NEVER this known-ACK phase.
            if self.resumes == 1:
                self.saved = copy.deepcopy(self.snapshot)
                revision = command["intent_revision"] + 2
                self.saved["run"].update(status="paused", intent_revision=revision,
                                         remaining_s=state["run"]["remaining_s"] - .01)
                self.saved["events"].append({"kind": "required_view_expired", "at": pi_lower + .01,
                                             "run_id": "owned", "service_incarnation": state["service_incarnation"]})
                self.saved["run"]["live_input"]["cursor_intent_revision"] = revision
                self.saved["evidence"].update(active_acknowledged=False, acknowledged_intent_revision=revision)
                self.saved["evidence"]["accepted_host_action"].update(intent_revision=revision, state="paused",
                                                                      acquired_at_monotonic_s=pi_lower + .01)
                self.snapshot["run"].update(status="resuming", intent_revision=command["intent_revision"] + 1)
            else:
                self.snapshot["run"].update(status="running", intent_revision=command["intent_revision"] + 1)
                self.snapshot["evidence"]["active_acknowledged"] = True
            return {"accepted": True, "status": "unknown" if change == "unknown_ack" else "resuming"}

        async def wait(self, predicate, timeout):
            if timeout == 5 and self.resumes == 1:
                raise original
            assert predicate(), "fresh proof or actual active ACK required"

    async def scenario():
        client = Client()
        def record(row):
            records.append(copy.deepcopy(row))
            if change == "record" and row["event"] == "accepted_resume_transport_closed" or (
                    change == "later_record" and row["event"] == "accepted_resume_superseded"):
                raise ValueError("bounded record unavailable")
        def healthy():
            if change == "renewal" and client.resumes:
                raise RuntimeError("original renewal failed")
        guard = m.SameRunRecovery(client, record)
        deadline = None if change in {"active_budget", "continuation_budget"} else 1002.
        if change in {None, "attach_retry", "unicode"}:
            assert await guard.resume(wall_deadline=deadline, renewal_check=healthy) is False
            assert client.connects == client.resumes == 2
            assert client.attaches == (2 if change == "attach_retry" else 1)
            assert client.snapshot["run"]["status"] == "running"
            assert client.snapshot["evidence"]["active_acknowledged"] is True
            assert len({c["operation_id"] for c in calls if c["op"] == "resume"}) == 2
        else:
            with pytest.raises(ConnectionError) as caught:
                await guard.resume(wall_deadline=deadline, renewal_check=healthy)
            assert caught.value is original
            assert client.resumes == 1
            if change in {"wrong_exception", "wrong_reason", "reader_error", "expected_close", "unknown_ack", "resume_loss"}:
                assert not hasattr(original, "accepted_resume_context") and client.attaches == 0
            if change in {"active_budget", "continuation_budget"}:
                assert isinstance(original.__cause__, TimeoutError) and pc[0] < 1006.
            if change == "later_loss":
                assert client.connects == 2 and isinstance(original.__cause__, ConnectionError)
        assert not any(c["op"] in {"input", "grant", "claim", "start"} for c in calls)
        assert guard.original["run"]["deadline"] == 400. and guard.episodes == 0
        for row in records:
            if row["event"] == "accepted_resume_transport_closed":
                assert len(json.dumps(row, allow_nan=False).encode()) < 2048
                assert row["phase"] == "active_ack" and 0 < row["remaining_s"] <= 5
                assert row["operation_id"] == next(c["operation_id"] for c in calls if c["op"] == "resume")
        assert not any(row["event"] == "qualified_native_recovery" for row in records)
    asyncio.run(asyncio.wait_for(scenario(), .6))


@pytest.mark.parametrize("existing_owner", [False, True], ids=["new-owned-generation", "same-owned-generation"])
def test_initial_claim_lost_result_reuses_live_authority_without_replaying_claim(tmp_path, existing_owner):
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
        storage.write_text(json.dumps({"cookies": [{"name": "__Host-am1-device", "value": token,
            "domain": "127.0.0.1", "path": "/", "secure": True}]}))
        client = m.SessionClient(cluster.url, cluster.cert, storage)
        loss = None
        try:
            await client.attach()
            if existing_owner:
                prior = await client.post("claim", {})
                await client.wait(lambda: (client.snapshot.get("controller") or {}).get(
                    "controller_generation") == prior["controller_generation"], 1)
            assert client.snapshot["run"] is None and client.generation is None
            cluster.flags.write_text(json.dumps({"delay_reply": "claim"}))
            original_close, generation = {}, []

            async def lose_result_after_actual_core_effect():
                while not cluster.flags.with_suffix(".reply").exists():
                    await asyncio.sleep(.002)
                receipt = json.loads(cluster.flags.with_suffix(".reply").read_text())
                assert receipt["accepted"] is True
                generation.append(receipt["controller_generation"])
                assert (await client.get("state"))["snapshot"]["run"] is None
                client.ws._response.connection.transport.abort()
                while client.transport_close is None:
                    await asyncio.sleep(.002)
                original_close.update(client.transport_close)
                cluster.flags.with_suffix(".release").write_text("release old result only")

            loss = asyncio.create_task(lose_result_after_actual_core_effect())
            await client.claim()
            await loss
            assert original_close["expected"] is False and original_close["close_code"] == 1006
            assert original_close["reader_error"] is None and original_close["reason"] == "stream-ended"
            assert client.generation == generation[0]
            fresh = (await client.get("state"))["snapshot"]
            assert fresh["run"] is None
            assert fresh["controller"]["device_id"] == client.device
            assert fresh["controller"]["controller_generation"] == generation[0]
            # A supplied generation is renewal-only in real Core. No second
            # bare Claim can create authority, and no Start precedes fresh proof.
            assert sum(v["kind"] == "claim" for v in fresh["events"]) == 1
            start = await client.post("start", {"operation_id": str(uuid4()),
                "recipe": "fake-interactive", "controller_generation": client.generation})
            assert start["accepted"] is True
            assert (await client.get("state"))["snapshot"]["run"]["run_id"] == start["run_id"]
        finally:
            cluster.flags.with_suffix(".release").write_text("finish")
            if loss is not None and not loss.done():
                loss.cancel()
                await asyncio.gather(loss, return_exceptions=True)
            await client.close()

    try:
        asyncio.run(asyncio.wait_for(scenario(), 6))
    finally:
        cluster.close()


@pytest.mark.parametrize("change", [None, "terminal", "accepted", "no_effect", "expired", "foreign", "service", "device",
    "run", "unsafe", "cause", "clock", "missing_identity", "missing_clock", "complete", "generation_changed", "renew_refused", "renew_lost",
    "renew_generation", "renew_expiry", "attach_loss", "final_generation", "final_run", "final_lease",
    "initial_generation", "initial_connection", "reader_error", "timeout", "expected_close", "wrong_close",
    "wrong_reason", "old_close", "command_precheck", "budget_spent", "spent_09", "attach_budget",
    "missing_original_run", "missing_fresh_run", "missing_attached_run", "missing_final_run"])
def test_initial_claim_requalification_preserves_original_error_authority_and_one_second_budget(tmp_path, monkeypatch, change):
    import asyncio
    import copy
    from types import SimpleNamespace

    from tools.am1_fake_executor import FakeExecutor
    from tools.am1_session_core import SessionAuthority

    m = adapter()
    pc, pi, calls = [1000.], [100.], []
    monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: pc[0]))
    device, service = str(uuid4()), str(uuid4())
    authority = SessionAuthority(tmp_path / "authority", clock=lambda: pi[0], executor=FakeExecutor(clock=lambda: pi[0]))
    authority.service_incarnation = service
    original = ConnectionError("original pending initial Claim result lost")
    terminal = {"run_id": str(uuid4()), "operation_id": str(uuid4()), "recipe": "physical-live-teleop",
        "backend_incarnation": str(uuid4()), "status": "faulted", "uncertain": False, "cleanup": "physical_readback_verified",
        "first_cause": "feedback_loss", "native_first_cause": "retained actual first cause", "native_live_at": None,
        "deadline": None, "accepted_at": 90., "startup_deadline": 270., "intent_revision": 2, "execution": {"complete": False}}
    terminal_case = change in {"terminal", "unsafe", "cause", "clock", "missing_identity", "missing_clock", "complete"}
    if change == "unsafe":
        terminal["uncertain"] = True
    if change == "missing_identity":
        terminal.pop("run_id")
    if change == "missing_clock":
        terminal.pop("deadline")
    if change == "generation_changed":
        authority.handle({"op": "claim"}, device)

    def snapshot():
        value = authority.handle({"op": "snapshot"}, device)["snapshot"]
        if terminal_case:
            value["run"] = copy.deepcopy(terminal)  # Explicit synthetic known-cleanup last-run fields.
        return value

    client = m.SessionClient.__new__(m.SessionClient)
    client.snapshot = snapshot()
    if change == "missing_original_run":
        client.snapshot.pop("run")
    client.device, client.generation, client.connection = device, None, None
    client.transport_error = client.transport_close = None
    client.ws, client.reader = SimpleNamespace(closed=False), None
    if change == "initial_generation":
        client.generation = str(uuid4())
    if change == "initial_connection":
        client.connection = str(uuid4())
    if change == "command_precheck":
        client.transport_error = "already closed before command entry"
    generation, get_count = [], [0]

    async def command(command):
        calls.append(("command", copy.deepcopy(command)))
        assert command == {"op": "claim"}
        if change != "no_effect":
            receipt = authority.handle(command, device)
            generation.append(receipt["controller_generation"])
        if change == "accepted":
            return receipt
        pc[0] += 1.01 if change == "budget_spent" else .9 if change == "spent_09" else .02
        if change == "expired":
            pi[0] += 1.6
        if change == "foreign":
            authority.controller["device_id"] = str(uuid4())
        if change == "generation_changed":
            authority.controller["generation"] = str(uuid4())
        client.transport_error = "current WSS connection ended: stream-ended"
        client.transport_close = {"expected": False, "close_code": 1006, "reason": "stream-ended", "reader_error": None,
            "exception_type": "ClientConnectionResetError", "exception": "Cannot write to closing transport",
            "observed_at_monotonic_s": pc[0]}
        if change == "reader_error":
            client.transport_close["reader_error"] = {"error_type": "RuntimeError", "message": "reporting"}
        if change == "expected_close":
            client.transport_close["expected"] = True
        if change == "wrong_close":
            client.transport_close["close_code"] = 1008
        if change == "wrong_reason":
            client.transport_close["reason"] = "message-error"
        if change == "old_close":
            client.transport_close["observed_at_monotonic_s"] = 999.
        if change == "timeout":
            raise TimeoutError("initial Claim result timeout is not classified stream loss")
        raise original

    async def detach():
        calls.append(("detach", None))
        client.snapshot = None
        client.ws = client.reader = None

    async def get(path):
        calls.append(("get", path))
        assert path == "state"
        get_count[0] += 1
        pc[0] += .11 if change == "spent_09" else .01
        if change == "final_lease" and get_count[0] == 2:
            pi[0] += 1.6
        value = snapshot()
        if change == "service":
            value["service_incarnation"] = str(uuid4())
        if change == "device":
            client.device = str(uuid4())
        if change == "run" or change == "final_run" and get_count[0] == 2:
            value["run"] = dict(terminal, run_id=str(uuid4()), status="starting")
        if change == "cause":
            value["run"]["native_first_cause"] = "changed first cause"
        if change == "clock":
            value["run"]["startup_deadline"] += 1
        if change == "complete":
            value["run"]["execution"]["complete"] = True
        if change == "missing_fresh_run" or change == "missing_final_run" and get_count[0] == 2:
            value.pop("run")
        return {"snapshot": value}

    async def post(path, body):
        calls.append(("post", (path, copy.deepcopy(body))))
        assert path == "claim" and body == {"controller_generation": generation[0]}
        assert client.generation is None  # No authority/Start publication before the final fresh proof.
        pc[0] += .01
        if change == "renew_refused":
            pi[0] += 1.6
        if change == "renew_lost":
            raise TimeoutError("generation-fenced renewal outcome unavailable")
        result = authority.handle(dict(op="claim", **body), device)
        if result["accepted"] is not True:
            raise m.HTTPOutcomeError(200, result)
        if change == "renew_generation":
            result["controller_generation"] = str(uuid4())
        if change == "renew_expiry":
            result["presence_expires"] = float("nan")
        return result

    async def attach():
        calls.append(("attach", None))
        assert client.snapshot is None and client.generation is None
        if change == "attach_loss":
            raise ConnectionError("fresh initial stream interrupted")
        pc[0] += .95 if change == "attach_budget" else .01
        if change == "final_generation":
            authority.controller["generation"] = str(uuid4())
        client.transport_error = client.transport_close = None
        client.ws = SimpleNamespace(closed=False)
        client.snapshot = snapshot()
        if change == "missing_attached_run":
            client.snapshot.pop("run")

    client.command, client.detach, client.get, client.post, client.attach = command, detach, get, post, attach

    async def scenario():
        if change in {None, "terminal", "accepted"}:
            await client.claim()
            assert client.generation == generation[0] and pc[0] < 1001.
            assert [v[0] for v in calls] == (["command"] if change == "accepted" else
                ["command", "detach", "get", "post", "attach", "get"])
        elif change == "timeout":
            with pytest.raises(TimeoutError, match="initial Claim result timeout"):
                await client.claim()
            assert [v[0] for v in calls] == ["command"]
        else:
            with pytest.raises(ConnectionError) as caught:
                await client.claim()
            assert caught.value is original
            assert client.generation is None or change == "initial_generation"
            if change in {"initial_generation", "initial_connection", "reader_error", "expected_close",
                          "wrong_close", "wrong_reason", "old_close", "command_precheck"}:
                assert [v[0] for v in calls] == ["command"]
            if change == "budget_spent":
                assert isinstance(original.__cause__, TimeoutError) and len(calls) == 1
            if change == "spent_09":
                assert isinstance(original.__cause__, TimeoutError)
                assert [v[0] for v in calls] == ["command", "detach", "get"]
            if change == "attach_budget":
                assert isinstance(original.__cause__, TimeoutError) and pc[0] < 1001.02
        assert sum(op == "command" for op, _ in calls) == 1
        assert sum(op == "post" for op, _ in calls) <= 1
        assert authority.run is None  # No Start, Resume, input, or motor work at this entrypoint.

    try:
        asyncio.run(asyncio.wait_for(scenario(), .5))
    finally:
        authority.close()


@pytest.mark.parametrize("change", [None, "wrong_pong_type", "wrong_pong_message", "reader_error",
    "reference", "hash", "initial_clock", "fresh_clock", "fresh_controller", "fresh_terminal",
    "fresh_unsafe", "final_clock", "spent_details", "spent_attach", "spent_start",
    "closing_transport", "closing_wrong_type", "closing_wrong_message", "closing_reader",
    "closing_expected", "closing_code", "closing_after_live", "closing_fresh_controller", "closing_spent_attach"])
def test_runner_admission_no_pong_uses_original_real_core_detail_clocks(tmp_path, monkeypatch, change):
    """Synthetic45: real Core/Gateway projection/detail and installed NoPONG; no motor or network."""
    import asyncio
    import copy
    import json
    import time
    from types import SimpleNamespace
    from urllib.parse import parse_qsl, urlsplit

    from aiohttp import ClientConnectionResetError, WSMessage, WSMsgType
    from aiohttp._websocket.writer import WebSocketWriter
    from aiohttp.client_ws import ClientWebSocketResponse

    from tests.robots.test_am1_live_teleop_executor import authority_lane
    from tools.am1_session_service import Gateway

    m = adapter()
    actual_wait, actual_read = m.SessionClient.wait, m.SessionClient._read
    authority, executor, _ = authority_lane(tmp_path / "authority")
    pc, clients, entered, caught = [20.0], [], [], []
    monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: pc[0], perf_counter=time.perf_counter))
    owned = str(uuid4())
    authority.clock = lambda: 1000.0 + pc[0] - 20.0
    template = recovery_state()
    executor.evidence = lambda: dict(copy.deepcopy(template["evidence"]),
                                     live_input=copy.deepcopy(template["run"]["live_input"]))
    gateway = object.__new__(Gateway)

    async def call(command, _device):
        if "reference" in command:
            return authority.snapshot_detail(command)
        return {"snapshot": authority.snapshot()}

    gateway.call = call

    class Wire:
        _pong_heartbeat = .5

        def __init__(self):
            self.closed, self.close_code, self.error = False, None, None
            self.messages = asyncio.Queue()

        def _handle_ping_pong_exception(self, error):
            self.closed, self.close_code, self.error = True, 1006, error
            self.messages.put_nowait(WSMessage(WSMsgType.ERROR, error, None))

        def exception(self):
            return self.error

        def __aiter__(self):
            return self

        async def __anext__(self):
            return await self.messages.get()

        async def send_json(self, value):
            assert set(value) == {"ack"}

    class Session:
        _read = actual_read

        def __init__(self, *_args):
            self.device, self.generation, self.connection = "device", "control", None
            self.snapshot, self.pending = None, {}
            self.transport_close = self.transport_error = self.reader = self.ws = None
            self.changed, self.tasks = asyncio.Event(), []
            self.starts, self.stops, self.claims, self.attaches = [], [], 0, 0
            self.renewing = self._detaching = False
            self.detail_reads = 0
            clients.append(self)

        def current(self):
            value = authority.snapshot()
            assert "accepted_at" not in value["run"]
            assert value["status_projection"] == {"kind": "am1-essential-status", "version": 1}
            return value

        async def wait(self, predicate, timeout):
            try:
                await actual_wait(self, predicate, timeout)
            except ConnectionError as error:
                # Mutate negative close evidence at its consumption boundary,
                # before the runner can quarantine/replace the failed stream.
                if change == "closing_reader":
                    self.transport_close["reader_error"] = {"error_type": "RuntimeError", "message": "reporting unavailable"}
                elif change == "closing_expected":
                    self.transport_close["expected"] = True
                elif change == "closing_code":
                    self.transport_close["close_code"] = 1008
                caught.append(error)
                raise

        async def lose(self):
            await asyncio.sleep(0)
            pc[0] += 9
            if change == "reader_error":
                self.ws.messages.put_nowait(WSMessage(WSMsgType.TEXT, "invalid JSON", None))
                await self.reader
                return
            if change and change.startswith("closing_"):
                # Installed writer + ping completion callback produce ERROR;
                # no network or physical backend is involved in this replay.
                writer = WebSocketWriter(SimpleNamespace(_paused=False), SimpleNamespace(is_closing=lambda: True))
                ping = asyncio.create_task(writer.send_frame(b"", WSMsgType.PING))
                await asyncio.sleep(0)
                ClientWebSocketResponse._ping_task_done(self.ws, ping)
                assert type(self.ws.error) is ClientConnectionResetError
                assert str(self.ws.error) == "Cannot write to closing transport"
                if change == "closing_wrong_type":
                    self.ws.error = RuntimeError(str(self.ws.error))
                elif change == "closing_wrong_message":
                    self.ws.error = type(self.ws.error)("other connection reset")
                elif change == "closing_after_live":
                    authority.run.update(status="preparing", native_live_at=1009.0, deadline=1309.0,
                                         backend_incarnation=executor.host_incarnation)
                    self.snapshot = self.current()
            else:
                ClientWebSocketResponse._pong_not_received(self.ws)
            if change == "wrong_pong_type":
                self.ws.error = RuntimeError(str(self.ws.error))
            elif change == "wrong_pong_message":
                self.ws.error = type(self.ws.error)("other timeout")
            await self.reader
            if change == "reader_error":
                self.transport_close["reader_error"] = {"error_type": "RuntimeError", "message": "reporting unavailable"}

        async def attach(self):
            self.attaches += 1
            assert self.snapshot is None
            assert self.attaches == 1 or self.renewing
            self.ws, self.transport_close, self.transport_error = Wire(), None, None
            self._detaching = False
            self.reader = asyncio.create_task(self._read())
            if self.attaches == 1:
                self.snapshot = {"service_incarnation": authority.service_incarnation, "run": None}
            else:
                if change in {"spent_attach", "closing_spent_attach"}:
                    pc[0] += 180
                if change == "fresh_clock":
                    authority.run["accepted_at"] += .01
                elif change in {"fresh_controller", "closing_fresh_controller"}:
                    authority.controller["generation"] = "other"
                elif change == "fresh_terminal":
                    authority.run.update(status="faulted", native_first_cause="original native fault")
                elif change == "fresh_unsafe":
                    authority.run["uncertain"] = True
                self.snapshot = self.current()
                self.tasks.append(asyncio.create_task(self.live()))

        async def live(self):
            await asyncio.sleep(0)
            authority.run.update(status="preparing", native_live_at=1009.0, deadline=1309.0,
                                 backend_incarnation=executor.host_incarnation)
            if change == "final_clock":
                authority.run["accepted_at"] += .02
            self.snapshot = self.current()
            self.changed.set()

        async def detach(self):
            self._detaching = True
            if self.reader and not self.reader.done():
                self.reader.cancel()
                await asyncio.gather(self.reader, return_exceptions=True)
            self.ws = self.reader = self.snapshot = None

        async def claim(self):
            self.claims += 1
            authority.controller = {"device_id": self.device, "generation": self.generation, "expires": 1180.0}

        async def renew(self):
            self.renewing = True
            try:
                await asyncio.Event().wait()
            finally:
                self.renewing = False

        async def post(self, path, body):
            if path == "start":
                self.starts.append(copy.deepcopy(body))
                if change == "spent_start":
                    pc[0] += 181
                authority.run.update(run_id=owned, operation_id=body["operation_id"], accepted_at=1000.0,
                    startup_deadline=1180.0, status="starting", native_live_at=None, deadline=None,
                    backend_incarnation=None, first_cause=None, uncertain=False)
                if change == "initial_clock":
                    del authority.run["accepted_at"]
                self.snapshot = self.current()
                if change in {"reference", "hash"}:
                    self.snapshot["run"]["details_reference"]["field" if change == "reference" else "sha256"] = (
                        "evidence" if change == "reference" else "0" * 64)
                self.tasks.append(asyncio.create_task(self.lose()))
                return {"accepted": True, "run_id": owned, "operation_id": body["operation_id"]}
            assert path == "stop"
            self.stops.append(copy.deepcopy(body))
            authority.run.update(status="stopped", cleanup="physical_readback_verified", uncertain=False)
            return {"accepted": True}

        async def get(self, path):
            parsed = urlsplit(path)
            query = dict(parse_qsl(parsed.query))
            assert parsed.path == "state"
            if query:
                self.detail_reads += 1
                if change == "spent_details":
                    pc[0] += 181
            response = await Gateway.state(gateway, SimpleNamespace(query=query))
            value = json.loads(response.body)
            if not query and self.attaches == 1 and change in {"reference", "hash"}:
                value["snapshot"]["run"]["details_reference"]["field" if change == "reference" else "sha256"] = (
                    "evidence" if change == "reference" else "0" * 64)
            return value

        async def close(self):
            await self.detach()
            await asyncio.gather(*self.tasks, return_exceptions=True)

    def admitted_boundary(value):
        entered.append(copy.deepcopy(clients[0].snapshot))
        raise RuntimeError("synthetic qualified Live boundary")

    monkeypatch.setattr(m, "SessionClient", Session)
    monkeypatch.setattr(m, "validate_workspace", admitted_boundary)
    args = SimpleNamespace(source="virtual-leaders", output=str(tmp_path / "run"), url="unused", ca="unused",
                           storage_state="unused")
    try:
        with pytest.raises((RuntimeError, ConnectionError, TimeoutError, ValueError)) as result:
            asyncio.run(asyncio.wait_for(m.run(args), 10))
        client = clients[0]
        if change and change.startswith("closing_"):
            for task in client.tasks:
                if task.done() and not task.cancelled():
                    task.result()  # A fixture error must never masquerade as a qualified transport loss.
        if change in {None, "closing_transport"}:
            assert str(result.value) == "synthetic qualified Live boundary"
            assert len(entered) == 1 and client.attaches == 2 and client.detail_reads >= 3
        else:
            assert entered == []
            if caught:
                assert result.value is caught[0]
        assert len(client.starts) == 1 and client.claims == 1
        assert client.stops == [{"run_id": owned, "operation_id": client.stops[0]["operation_id"]}]
        rows = [json.loads(row) for row in (tmp_path / "run" / "frames.jsonl").read_text().splitlines()]
        assert not any(row["event"].startswith("leader_frame") for row in rows)
        assert "accepted_at" not in (client.current()["run"])
    finally:
        authority.close()


@pytest.mark.parametrize("change", [None, "offset", "length_bool", "total", "encoding", "truncated", "hash",
                                    "reference_bool", "budget", "refused"])
def test_startup_run_detail_reuses_real_core_gateway_bounded_chunk_contract(tmp_path, change):
    import asyncio
    import copy
    import json
    from types import SimpleNamespace
    from urllib.parse import parse_qsl, urlsplit

    from tests.robots.test_am1_live_teleop_executor import authority_lane
    from tools.am1_session_service import Gateway

    m = adapter()
    authority, executor, _ = authority_lane(tmp_path / "authority")
    authority.run.update(accepted_at=9.0, startup_deadline=189.0,
                         backend_incarnation=executor.host_incarnation, original_evidence="x" * 12000)
    state = authority.snapshot()
    before, requests, clock = copy.deepcopy(authority.run), [], [0.0]
    assert "accepted_at" not in state["run"]
    gateway = object.__new__(Gateway)

    async def call(command, _device):
        return authority.snapshot_detail(command)

    gateway.call = call

    class Client:
        async def get(self, path):
            query = dict(parse_qsl(urlsplit(path).query))
            requests.append(copy.deepcopy(query))
            if change == "refused":
                raise RuntimeError("authenticated read refused")
            response = await Gateway.state(gateway, SimpleNamespace(query=query))
            result = json.loads(response.body)
            if change == "budget":
                clock[0] += 2.1
            elif change == "offset":
                result["offset"] += 1
            elif change == "length_bool":
                result["length"] = True
            elif change == "total":
                result["total_bytes"] += 1
            elif change == "encoding":
                result["encoding"] = "hex"
            elif change == "truncated":
                result["data"] = result["data"][:-4]
            elif change == "hash":
                import base64

                altered = bytearray(base64.b64decode(result["data"]))
                altered[0] ^= 1
                result["data"] = base64.b64encode(altered).decode()
            elif change == "reference_bool":
                result["reference"]["version"] = True
            return result

    def remaining():
        if clock[0] >= 2.0:
            raise TimeoutError("original admission clock exhausted")
        return 2.0 - clock[0]

    try:
        if change is None:
            result = asyncio.run(m._startup_run_detail(Client(), state, remaining))
            assert result == before and result["accepted_at"] == 9.0
            assert [int(query["offset"]) for query in requests] == [0, 8192]
        else:
            with pytest.raises((RuntimeError, ValueError, TimeoutError)):
                asyncio.run(m._startup_run_detail(Client(), state, remaining))
        assert authority.run == before and "accepted_at" not in state["run"]
        assert all(query["limit"] == "8192" for query in requests)
    finally:
        authority.close()


@pytest.mark.parametrize("change", [None, "small", "matching_null", "mixed_null_projection",
    "mixed_absent_projection", "foreign_host", "live_null_host", "bound_null_host", "unsafe_status",
    "missing_live", "missing_deadline", "changed_deadline", "future_deadline", "bad_ref_shape", "detail_error"])
def test_startup_detail_uses_the_actual_core_start_pending_backend_shape(tmp_path, monkeypatch, change):
    import asyncio
    import copy
    import json
    from types import SimpleNamespace
    from urllib.parse import parse_qsl, urlsplit

    from tests.robots.test_am1_live_teleop_executor import authority_lane
    from tools.am1_session_service import Gateway

    m = adapter()
    authority, executor, _ = authority_lane(tmp_path / "authority")
    authority.run = None
    begins = []
    # Only the actual executor worker launch is suppressed. Core Start, bind_run,
    # original startup clocks and Gateway status/detail generation are unchanged.
    monkeypatch.setattr(executor, "begin", lambda recipe: begins.append(recipe.name) or True)
    receipt = authority.handle({"op": "start", "operation_id": str(uuid4()),
        "recipe": "physical-live-teleop", "controller_generation": authority.controller["generation"]}, "pc")
    assert receipt["accepted"] and receipt["effect_admitted"] is False
    assert begins == ["physical-live-teleop"]
    assert authority.run["status"] == "starting" and authority.run["accepted_at"] == 10.
    assert authority.run["startup_deadline"] == 190.
    assert authority.run["native_live_at"] is None and authority.run["deadline"] is None
    assert "backend_incarnation" not in authority.run  # The real production pending shape.
    if change in {"matching_null", "mixed_absent_projection", "live_null_host", "bound_null_host",
                  "unsafe_status", "missing_live", "missing_deadline"}:
        # Explicit synthetic compatibility/negative states; the primary case above
        # retains the unmodified Core-generated ABSENT field.
        authority.run["backend_incarnation"] = None
    if change == "live_null_host":
        authority.run.update(status="paused", native_live_at=10., deadline=310.)
    before, requests = copy.deepcopy(authority.run), []
    gateway = object.__new__(Gateway)
    async def call(command, _device):
        return authority.snapshot_detail(command) if "reference" in command else {"snapshot": authority.snapshot()}
    gateway.call = call
    read_error = RuntimeError("original authenticated detail unavailable")
    class Client:
        async def get(self, path):
            requests.append(path)
            if change == "detail_error":
                raise read_error
            response = await Gateway.state(gateway, SimpleNamespace(query=dict(parse_qsl(urlsplit(path).query))))
            return json.loads(response.body)
    async def scenario():
        response = await Gateway.state(gateway, SimpleNamespace(query={}))
        state = json.loads(response.body)["snapshot"]
        assert state["status_projection"] == {"kind": "am1-essential-status", "version": 1}
        assert "accepted_at" not in state["run"]
        assert state["run"]["details_reference"]["host_incarnation"] is None
        if change == "small":
            state = {"service_incarnation": state["service_incarnation"], "run": copy.deepcopy(before)}
        elif change == "mixed_null_projection":
            state["run"]["backend_incarnation"] = None
        elif change == "mixed_absent_projection":
            state["run"].pop("backend_incarnation")
        elif change == "foreign_host":
            state["run"]["details_reference"]["host_incarnation"] = str(uuid4())
        elif change == "bound_null_host":
            state["run"]["backend_incarnation"] = str(uuid4())
        elif change == "unsafe_status":
            state["run"]["status"] = "recovering"
        elif change == "missing_live":
            state["run"].pop("native_live_at")
        elif change == "missing_deadline":
            state["run"].pop("deadline")
        elif change in {"changed_deadline", "future_deadline"}:
            state["run"]["startup_deadline"] += .01 if change == "changed_deadline" else 1800.
        elif change == "bad_ref_shape":
            state["run"]["details_reference"]["bytes"] = True
        if change in {None, "small", "matching_null"}:
            result = await m._startup_run_detail(Client(), state, lambda: 1.)
            assert result == before and result["accepted_at"] == 10.
            assert result["startup_deadline"] == 190.
            assert ("backend_incarnation" in result) is (change == "matching_null")
            assert bool(requests) is (change != "small")
        else:
            with pytest.raises((RuntimeError, ValueError)) as caught:
                await m._startup_run_detail(Client(), state, lambda: 1.)
            if change == "detail_error":
                assert caught.value is read_error
        assert authority.run == before and begins == ["physical-live-teleop"]
    try:
        asyncio.run(scenario())
    finally:
        authority.run = None
        authority.close()


@pytest.mark.parametrize("change", [None, "still_recovering", "ordinary", "ordinary_held", "ordinary_view", "ordinary_then_episode", "manual_before_hold", "remaining_increased", "missing_source", "changed_source", "missing_descriptor", "extra_descriptor",
    "future_source", "future_qualified", "earlier_source", "manual_pause", "closed_episode", "later_distinct_episode", "limit",
    "foreign", "service", "controller", "reference", "deadline", "live", "fault", "uncertain", "native_fault",
    "running", "view", "held", "ack", "alignment", "caller_expired", "episode_expired", "http_delay",
    "renewal", "nested_connect", "nested_release", "nested_resume", "same_generation", "missing_tag"])
def test_unknown_grant_requires_source_episode_fresh_hold_new_fence_and_original_budget(change, monkeypatch):
    import asyncio
    import copy
    from types import SimpleNamespace

    m = adapter()
    now, commands, records = [1000.], [], []
    monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: now[0], perf_counter=m.time.perf_counter))
    before, fresh = recovery_state(), requalified_state()
    episode = {"started": 100.02, "ceiling": 100.42, "revision": 2}
    hold = {"kind": "required_hold", "at": 100.03, "run_id": "owned", "service_incarnation": "service",
            "recovery": copy.deepcopy(episode)}
    fresh["events"].insert(0, hold)
    fresh["evidence"]["active_acknowledged"] = False
    fresh["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"] = 100.1
    original = TimeoutError("original unknown permission")
    connection = str(uuid4())

    class Client:
        device, generation, sequence = "device", "control", 0
        identities = m.SessionClient.identities
        snapshot = copy.deepcopy(before)
        attaches = 0
        reads = 0

        async def detach(self):
            self.snapshot = None

        async def attach(self):
            self.attaches += 1
            self.snapshot = copy.deepcopy(fresh)

        async def get(self, path):
            assert path == "state"
            if change == "http_delay":
                now[0] += .4
            elif change in {"view", "held", "ack", "alignment"}:
                now[0] += .05  # Original episode/caller clock also advances while waiting.
                fresh["run"]["remaining_s"] -= .05
            self.reads += 1
            value = copy.deepcopy(fresh)
            if change == "still_recovering" and self.reads == 1:
                value["run"].update(status="recovering", recovery=copy.deepcopy(episode), intent_revision=2)
                value["events"] = [copy.deepcopy(hold)]
                value["evidence"]["feedback"] = False
            return {"snapshot": value}

        async def command(self, command):
            commands.append(copy.deepcopy(command))
            op = command["op"]
            if change == ("nested_release" if op == "release_input" else "nested_" + op):
                raise ConnectionError("new phase outcome unknown")
            if op == "connect":
                value = copy.deepcopy(self.snapshot)
                value["run"]["intent_revision"] += 1
                revision = value["run"]["intent_revision"]
                value["run"]["live_input"]["cursor_intent_revision"] = revision
                value["evidence"]["acknowledged_intent_revision"] = revision
                value["evidence"]["accepted_host_action"]["intent_revision"] = revision
                return {"accepted": True, "connection_generation": self.connection if change == "same_generation" else connection,
                        "snapshot": value}
            if op == "resume":
                self.snapshot["run"]["status"] = "running"
                self.snapshot["run"]["intent_revision"] += 1
                revision = self.snapshot["run"]["intent_revision"]
                self.snapshot["run"]["live_input"]["cursor_intent_revision"] = revision
                self.snapshot["evidence"].update(active_acknowledged=True, acknowledged_intent_revision=revision)
                self.snapshot["evidence"]["accepted_host_action"].update(state="active", intent_revision=revision)
            return {"accepted": True}

        async def wait(self, predicate, timeout):
            if not predicate():
                raise TimeoutError("current held/active proof unavailable")

    client = Client()
    client.connection = "connection"
    guard = m.SameRunRecovery(client, records.append)
    context = {"identity": client.identities(), "snapshot": copy.deepcopy(before), "requested": 1000.}
    original.grant_context = context
    if change in {"ordinary", "ordinary_held", "ordinary_view", "remaining_increased"}:
        fresh["run"].update(first_cause=None, recovery_episodes=0)
        fresh["events"] = [{"kind": "input_expired", "at": 100.1,
                            "run_id": "owned", "service_incarnation": "service"}]
        if change == "remaining_increased":
            fresh["run"]["remaining_s"] = 300.
        elif change == "ordinary_held":
            fresh["evidence"]["hold_acknowledged"] = False
        elif change == "ordinary_view":
            fresh["run"]["live_input"]["required_view"]["qualified"] = False
    elif change in {"ordinary_then_episode", "manual_before_hold"}:
        fresh["events"].insert(0, {"kind": "input_expired" if change == "ordinary_then_episode" else "pause",
                                    "at": 100.02, "run_id": "owned", "service_incarnation": "service"})
        hold["recovery"]["revision"] += 1
        episode["revision"] += 1
        fresh["run"]["intent_revision"] += 1
        fresh["run"]["live_input"]["cursor_intent_revision"] += 1
        fresh["evidence"]["acknowledged_intent_revision"] += 1
        fresh["evidence"]["accepted_host_action"]["intent_revision"] += 1
    elif change == "missing_source":
        fresh["events"].pop(0)
    elif change == "changed_source":
        hold["service_incarnation"] = "foreign"
    elif change == "missing_descriptor":
        hold.pop("recovery")
    elif change == "extra_descriptor":
        hold["recovery"]["count"] = 1
    elif change == "future_source":
        hold["at"] = 101.
    elif change == "future_qualified":
        fresh["events"][-1]["at"] = 100.2
    elif change == "earlier_source":
        hold["recovery"]["started"] = 99.
    elif change in {"manual_pause", "closed_episode"}:
        fresh["events"].append({"kind": "pause" if change == "manual_pause" else "resumed", "at": 100.11,
                                "run_id": "owned", "service_incarnation": "service"})
        fresh["run"]["intent_revision"] += 2
    elif change == "later_distinct_episode":
        fresh["run"]["recovery_episodes"] = 2
    elif change == "limit":
        guard.episodes, guard.cause = 3, "feedback_loss"
        context["snapshot"]["run"].update(recovery_episodes=3, first_cause="feedback_loss")
        fresh["run"]["recovery_episodes"] = 4
    elif change == "foreign":
        fresh["run"]["run_id"] = "other"
    elif change == "service":
        fresh["service_incarnation"] = "other"
    elif change == "controller":
        fresh["controller"]["controller_generation"] = "other"
    elif change == "reference":
        fresh["run"]["live_input"]["reference"][KEYS[0]] += 1
    elif change == "deadline":
        fresh["run"]["deadline"] += 1
    elif change == "live":
        fresh["run"]["native_live_at"] += 1
    elif change == "fault":
        fresh["evidence"]["fault"] = "device hard fault"
    elif change == "uncertain":
        fresh["run"]["uncertain"] = True
    elif change == "native_fault":
        fresh["run"]["native_first_cause"] = "device hard fault"
    elif change == "running":
        fresh["run"]["status"] = "running"
    elif change == "view":
        fresh["run"]["live_input"]["required_view"]["qualified"] = False
    elif change == "held":
        fresh["evidence"]["hold_acknowledged"] = False
    elif change == "ack":
        fresh["evidence"]["accepted_host_action"]["intent_revision"] -= 1
    elif change == "alignment":
        fresh["evidence"]["pose_aligned"] = False
    elif change == "episode_expired":
        fresh["run"]["remaining_s"] = 299.5
    elif change == "missing_tag":
        del original.grant_context

    def healthy():
        if change == "renewal":
            raise RuntimeError("original renewal failed")

    async def scenario():
        if change in {None, "still_recovering", "ordinary", "ordinary_then_episode"}:
            assert await guard.resume(grant_error=original, wall_deadline=1000.2, renewal_check=healthy) is (change != "ordinary")
            assert [command["op"] for command in commands] == ["connect", "release_input", "resume"]
            assert client.connection == connection and client.sequence == 0
            assert guard.episodes == (0 if change == "ordinary" else 1)
            if change != "ordinary":
                assert records[-1]["event"] == "qualified_native_recovery"
                assert records[-1]["original_recovery"] == episode
            else:
                assert not any(r["event"] == "qualified_native_recovery" for r in records)
            assert client.snapshot["evidence"]["active_acknowledged"] is True
        else:
            with pytest.raises(TimeoutError) as caught:
                await guard.resume(grant_error=original, wall_deadline=999.9 if change == "caller_expired" else 1000.2,
                                   renewal_check=healthy)
            assert caught.value is original
            assert not any(command["op"] in {"grant", "input", "claim", "start"} for command in commands)
            assert not any(r["event"] == "qualified_native_recovery" for r in records)
            if change not in {"nested_connect", "nested_release", "nested_resume", "same_generation"}:
                assert commands == []
        assert client.sequence == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["individual", "terminal_zero_qualification"])
def test_runner_routes_only_pre_capture_grant_ambiguity_and_preserves_terminal_ceiling(tmp_path, monkeypatch, phase):
    m = adapter()
    actual_send, actual_resume = m.send_leader_sample, m.SameRunRecovery.resume
    original, seen, samples = TimeoutError("unknown first permission"), [], []

    async def send(client, producer, body, record, *, phase: str, progress_s):
        if phase == selected and not seen:
            sequence, source_sequence = client.sequence, producer.sequence
            command = client.command
            async def lost(payload):
                assert payload["op"] == "grant"
                raise original
            client.command = lost
            try:
                return await actual_send(client, producer, body, record, phase=phase, progress_s=progress_s)
            finally:
                client.command = command
                samples.append((client.sequence == sequence, producer.sequence == source_sequence))
        return await actual_send(client, producer, body, record, phase=phase, progress_s=progress_s)

    async def resume(guard, **kwargs):
        if kwargs.get("grant_error") is original:
            assert samples == [(True, True)]
            assert original.grant_context["snapshot"]["run"]["status"] == "running"
            kwargs["renewal_check"]()
            if selected == "terminal_zero_qualification":
                assert m.time.monotonic() < kwargs["wall_deadline"] <= m.time.monotonic() + 2
            seen.append(kwargs)
            return False  # Routing seam only; real qualified Core/fence is tested above.
        return await actual_resume(guard, **kwargs)

    selected = phase
    monkeypatch.setattr(m, "send_leader_sample", send)
    monkeypatch.setattr(m.SameRunRecovery, "resume", resume)
    test_real_runner_waits_for_exact_start_from_idle_null_run_and_stops_owned_id(tmp_path, monkeypatch, None)
    assert len(seen) == 1 and samples == [(True, True)]


@pytest.mark.parametrize("teleop,kind", [(True, "required_hold"), (False, "required_hold"), (True, "pause")])
def test_required_hold_source_descriptor_is_copied_before_sql_and_only_for_teleop(tmp_path, teleop, kind):
    import copy
    import json

    from tests.robots.test_am1_live_teleop_executor import authority_lane

    authority, _, _ = authority_lane(tmp_path / "owner")
    try:
        descriptor = {"started": 10., "ceiling": 20., "revision": authority.run["intent_revision"]}
        authority.run["recovery"] = descriptor
        if not teleop:
            authority.run["recipe"] = "ordinary-non-teleop"
        authority._event(kind)
        original = copy.deepcopy(authority.events[-1])
        descriptor["started"] = 99.
        authority.run["recovery"] = None
        assert authority.events[-1] == original
        sql = json.loads(authority.db.execute("SELECT data FROM transitions ORDER BY rowid DESC LIMIT 1").fetchone()[0])
        assert sql == original
        if teleop and kind == "required_hold":
            assert original["recovery"] == {"started": 10., "ceiling": 20., "revision": original["recovery"]["revision"]}
        else:
            assert set(original) == {"kind", "at", "run_id", "service_incarnation"}
    finally:
        authority.run = None
        authority.close()


@pytest.mark.parametrize("change", [None, "closed_episode", "persistent", "caller", "terminal", "native", "episode", "episode_active_grace",
    "phase_not_reset", "fault", "uncertain", "feedback", "hold", "alignment", "native_ack", "cursor", "ack",
    "backwards_ack", "future_ack", "remaining", "invalid_remaining", "manual", "event_time", "revision", "history",
    "foreign", "controller", "connection", "reference", "deadline", "live", "malformed_view", "wrong_role", "view_cause",
    "renewal", "http_error", "nested_connect", "nested_release", "nested_resume", "active_ack"])
def test_grant_view_wait_keeps_current_authority_and_one_original_phase(change, monkeypatch):
    import asyncio
    import copy
    from types import SimpleNamespace

    m = adapter()
    now, commands, waits, records = [1000.], [], [], []
    monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: now[0], perf_counter=m.time.perf_counter))
    before, fresh = recovery_state(), requalified_state()
    fresh["run"].update(first_cause=None, recovery_episodes=0)
    fresh["run"]["live_input"]["required_view"] = {
        "required": True, "qualified": False, "role": "chest", "cause": "required_view_expired"}
    fresh["events"] = [{"kind": "input_expired", "at": 100.1,
                        "run_id": "owned", "service_incarnation": "service"}]
    fresh["evidence"].update(active_acknowledged=False)
    fresh["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"] = 100.1
    original = ConnectionError("original unknown pre-capture grant")
    generation = str(uuid4())
    if change == "closed_episode":
        before["run"].update(first_cause="feedback_loss", recovery_episodes=1)
        fresh["run"].update(first_cause="feedback_loss", recovery_episodes=1)
        fresh["events"].insert(0, {"kind": "required_hold", "at": 100., "run_id": "owned",
                                  "service_incarnation": "service",
                                  "recovery": {"started": 99., "ceiling": 99.9, "revision": 0}})
    if change == "native":
        before["run"]["remaining_s"] = .2
        fresh["events"][-1]["at"] = 399.9
    if change in {"episode", "episode_active_grace"}:
        fresh["run"].update(first_cause="feedback_loss", recovery_episodes=1)
        fresh["events"][-1]["kind"] = "live_sensing_requalified"
        fresh["events"].insert(0, {"kind": "required_hold", "at": 100.03, "run_id": "owned",
                                  "service_incarnation": "service",
                                  "recovery": {"started": 100.02, "ceiling": 110.02 if change == "episode_active_grace" else 100.42, "revision": 2}})

    class Client:
        device, generation, sequence = "device", "control", 0
        identities = m.SessionClient.identities
        snapshot = recovery_state() if change == "closed_episode" else copy.deepcopy(before)
        reads = 0

        async def detach(self):
            self.snapshot = None

        async def attach(self):
            self.snapshot = copy.deepcopy(fresh)

        async def get(self, path):
            assert path == "state"
            self.reads += 1
            assert commands == []  # No binding or authority mutation before a qualified view.
            now[0] += ((.1 if self.reads == 1 else 5.9) if change == "episode_active_grace"
                       else 3. if change == "phase_not_reset" else 1. if change == "persistent" else .15)
            value = copy.deepcopy(fresh)
            elapsed = now[0] - 1000.
            value["run"]["remaining_s"] = (.2 if change == "native" else 300.) - elapsed
            value["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"] = (
                399.8 + elapsed if change == "native" else 100. + elapsed)
            if self.reads > 1 and change not in {"persistent", "episode"}:
                view = value["run"]["live_input"]["required_view"]
                view.update(qualified=True, cause=None, role="wrist_left")
                if change in {"caller", "terminal"}:
                    now[0] = 1002.01
                elif change == "fault":
                    value["evidence"]["fault"] = "genuine hard fault"
                elif change == "uncertain":
                    value["run"]["uncertain"] = True
                elif change in {"feedback", "alignment", "native_ack", "hold"}:
                    key = {"alignment": "pose_aligned", "hold": "hold_acknowledged"}.get(change, change)
                    value["evidence"][key] = False
                elif change == "cursor":
                    value["run"]["live_input"]["cursor_intent_revision"] -= 1
                elif change == "ack":
                    value["evidence"]["accepted_host_action"]["intent_revision"] -= 1
                elif change == "backwards_ack":
                    value["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"] = 100.1
                elif change == "future_ack":
                    value["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"] = 401.
                elif change in {"remaining", "invalid_remaining"}:
                    value["run"]["remaining_s"] = 299.99 if change == "remaining" else True
                elif change in {"manual", "event_time"}:
                    value["events"][-1]["kind" if change == "manual" else "at"] = "pause" if change == "manual" else 100.11
                elif change == "revision":
                    value["run"]["intent_revision"] += 1
                elif change == "history":
                    value["run"]["recovery_episodes"] += 1
                elif change == "foreign":
                    value["run"]["run_id"] = "foreign"
                elif change == "controller":
                    value["controller"]["controller_generation"] = "foreign"
                elif change == "connection":
                    self.connection = "foreign"
                elif change == "reference":
                    value["run"]["live_input"]["reference"][KEYS[0]] += 1
                elif change in {"deadline", "live"}:
                    value["run"]["deadline" if change == "deadline" else "native_live_at"] += 1
                elif change == "malformed_view":
                    view.pop("required")
                elif change == "wrong_role":
                    view["role"] = "unconfigured"
                elif change == "view_cause":
                    view["cause"] = "required_view_expired"
                elif change == "http_error":
                    raise ConnectionError("later owner HTTP unavailable")
            return {"snapshot": value}

        async def command(self, payload):
            assert self.snapshot["run"]["live_input"]["required_view"]["qualified"] is True
            commands.append(copy.deepcopy(payload))
            op = payload["op"]
            if change == "nested_" + ("release" if op == "release_input" else op):
                raise ConnectionError("later ambiguous command")
            if op == "connect":
                value = copy.deepcopy(self.snapshot)
                value["run"]["intent_revision"] += 1
                revision = value["run"]["intent_revision"]
                value["run"]["live_input"]["cursor_intent_revision"] = revision
                value["evidence"]["acknowledged_intent_revision"] = revision
                value["evidence"]["accepted_host_action"]["intent_revision"] = revision
                return {"accepted": True, "connection_generation": generation, "snapshot": value}
            if op == "resume":
                self.snapshot["run"].update(status="running", intent_revision=self.snapshot["run"]["intent_revision"] + 1)
                revision = self.snapshot["run"]["intent_revision"]
                self.snapshot["run"]["live_input"]["cursor_intent_revision"] = revision
                self.snapshot["evidence"].update(active_acknowledged=change != "active_ack", acknowledged_intent_revision=revision)
                self.snapshot["evidence"]["accepted_host_action"].update(state="active", intent_revision=revision)
            return {"accepted": True}

        async def wait(self, predicate, timeout):
            waits.append(timeout)
            if change == "phase_not_reset" and len(waits) == 1:
                assert 0 < timeout < 2.01  # Six seconds were already spent before connecting.
                now[0] += timeout + .001
                raise TimeoutError("original held/view phase exhausted")
            if change == "episode_active_grace" and len(waits) == 2:
                assert timeout == 5  # Active ACK keeps its existing distinct phase.
                now[0] += 2.4  # Beyond held/view+8, inside original episode and active+5.
            if not predicate():
                raise TimeoutError("current proof unavailable")

    client = Client()
    client.connection = "connection"
    guard = m.SameRunRecovery(client, records.append)
    if change == "closed_episode":
        guard.episodes, guard.cause = 1, "feedback_loss"
        client.snapshot = copy.deepcopy(before)
    original.grant_context = {"identity": client.identities(), "snapshot": copy.deepcopy(before), "requested": 1000.}

    def healthy():
        if change == "renewal" and client.reads >= 2:
            raise RuntimeError("original HTTP renewal failed")

    async def scenario():
        if change in {None, "closed_episode", "episode_active_grace"}:
            assert await guard.resume(grant_error=original, renewal_check=healthy) is (change == "episode_active_grace")
            assert [v["op"] for v in commands] == ["connect", "release_input", "resume"]
            assert client.snapshot["evidence"]["active_acknowledged"] is True
            assert client.snapshot["run"]["live_input"]["required_view"]["role"] == "wrist_left"
            assert guard.episodes == (1 if change in {"closed_episode", "episode_active_grace"} else 0)
        else:
            with pytest.raises(ConnectionError) as caught:
                await guard.resume(grant_error=original, renewal_check=healthy,
                                   wall_deadline=1002. if change in {"caller", "terminal"} else None)
            assert caught.value is original
            if change == "phase_not_reset":
                assert [v["op"] for v in commands] == ["connect", "release_input"]
            elif not change.startswith("nested_") and change != "active_ack":
                assert commands == []
        assert client.sequence == 0
        assert not any(v["op"] in {"grant", "input", "claim", "start"} for v in commands)
        assert sum(v["event"] == "qualified_native_recovery" for v in records) == (1 if change == "episode_active_grace" else 0)
    asyncio.run(scenario())


@pytest.mark.parametrize("mutation", [
    None, "third", "known_connect", "missing_source", "missing_descriptor", "extra_descriptor",
    "future_source", "extended_source", "manual", "old_event", "closed_episode", "count_jump", "fourth",
    "foreign", "fault", "uncertain", "changed_cause", "changed_source", "connection_changed",
    "caller_expired", "episode_expired", "renewal_lost", "nested_resume", "reporting_failure", "legacy_no_identity",
])
def test_native_qualification_handoff_preserves_source_budget_and_first_error(monkeypatch, mutation):
    """Synthetic source contrasts; the real Core/Gateway cases prove the entrypoint."""
    import asyncio
    import copy
    import json
    from types import SimpleNamespace

    m = adapter()
    pc = [1000.0]
    monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: pc[0]))
    prior = 2 if mutation == "third" else 3 if mutation == "fourth" else 0
    records, calls, guard_errors = [], [], []
    initial = recovery_state()
    initial["run"].update(recovery_episodes=prior, first_cause="feedback_loss" if prior else None)
    paused = copy.deepcopy(initial)
    paused["run"].update(status="paused", intent_revision=3, remaining_s=299.875)
    paused["evidence"].update(active_acknowledged=False, acknowledged_intent_revision=3)
    paused["evidence"]["accepted_host_action"].update(intent_revision=3, acquired_at_monotonic_s=100.125)
    paused["run"]["live_input"]["cursor_intent_revision"] = 3
    paused["events"] = [{"kind": "input_expired", "at": 100.125,
                         "run_id": "owned", "service_incarnation": "service"}]
    revision = 4 if mutation == "known_connect" else 3
    episode = {"started": 100.25, "ceiling": 100.75, "revision": revision}
    injected = copy.deepcopy(paused)
    injected["run"].update(status="recovering", recovery_episodes=prior + 1, first_cause="feedback_loss",
                           intent_revision=revision, remaining_s=299.625, recovery=copy.deepcopy(episode))
    injected["events"].append({"kind": "required_hold", "at": 100.375,
                               "run_id": "owned", "service_incarnation": "service",
                               "recovery": copy.deepcopy(episode)})
    fresh = copy.deepcopy(injected)
    fresh["run"].update(status="paused", intent_revision=revision + 1, remaining_s=299.5, recovery=None)
    fresh["events"].append({"kind": "live_sensing_requalified", "at": 100.5,
                            "run_id": "owned", "service_incarnation": "service"})
    fresh["evidence"].update(acknowledged_intent_revision=revision + 1)
    fresh["evidence"]["accepted_host_action"].update(intent_revision=revision + 1, acquired_at_monotonic_s=100.5)
    fresh["run"]["live_input"]["cursor_intent_revision"] = revision + 1
    hold = injected["events"][-1]
    if mutation == "missing_source":
        injected["events"].pop()
    elif mutation == "missing_descriptor":
        hold.pop("recovery")
    elif mutation == "extra_descriptor":
        hold["recovery"]["extra"] = True
    elif mutation == "future_source":
        hold["recovery"]["started"] = 100.5
    elif mutation == "extended_source":
        hold["recovery"]["ceiling"] = 111.0
    elif mutation == "manual":
        injected["events"].insert(-1, {"kind": "pause", "at": 100.3,
                                      "run_id": "owned", "service_incarnation": "service"})
    elif mutation == "old_event":
        injected["events"][0]["at"] = 100.0
    elif mutation == "closed_episode":
        injected = copy.deepcopy(fresh)
        injected["events"].append({"kind": "resumed", "at": 100.55,
                                    "run_id": "owned", "service_incarnation": "service"})
    elif mutation == "count_jump":
        injected["run"]["recovery_episodes"] += 1
    elif mutation == "foreign":
        injected["run"]["run_id"] = "foreign"
    elif mutation == "fault":
        injected["evidence"]["fault"] = "real native fault"
    elif mutation == "uncertain":
        injected["run"]["uncertain"] = True
    elif mutation == "changed_cause":
        injected["run"]["first_cause"] = "manual"
    elif mutation == "changed_source":
        fresh["events"][-2]["recovery"]["started"] += .0625
    rejected = mutation not in {None, "third", "known_connect"}

    class Client:
        device, generation, connection, sequence = "device", "control", "connection", 0
        reads, resumes = 0, 0

        def __init__(self):
            self.snapshot = recovery_state()

        def identities(self):
            return m.SessionClient.identities(self)

        async def get(self, path):
            assert path == "state"
            self.reads += 1
            if self.reads > 1:
                if mutation == "connection_changed":
                    self.connection = "changed"
                elif mutation == "caller_expired":
                    pc[0] += .125
                elif mutation == "episode_expired":
                    pc[0] += 1
            self.snapshot = copy.deepcopy(paused if self.reads == 1 else fresh)
            return {"snapshot": self.snapshot}

        async def resume(self, *, check, refusal_record=None):
            self.resumes += 1
            check(self.snapshot)
            if self.resumes == 1:
                if mutation == "known_connect":
                    self.connection = str(uuid4())
                self.snapshot = copy.deepcopy(injected)
                check(self.snapshot)
                pytest.fail("unsafe/new history cannot continue the old Resume")
            if mutation == "nested_resume":
                raise ConnectionError("new qualification transport failed")
            calls.append("new-resume-active-ack")
            self.snapshot["run"]["status"] = "running"
            self.snapshot["evidence"]["active_acknowledged"] = True
            check(self.snapshot)

    def record(value):
        if mutation == "reporting_failure" and value["event"] == "native_rejoin_qualification_changed":
            raise ValueError("fault reporting failed")
        records.append(value)

    async def scenario():
        client = Client()
        if mutation == "legacy_no_identity":
            client.identities = None
        guard = m.SameRunRecovery(client, record)
        guard.episodes, guard.cause = prior, initial["run"]["first_cause"]
        client.snapshot = copy.deepcopy(initial)
        original_check = guard.check

        def checked(state, *, history=True):
            try:
                original_check(state, history=history)
            except RuntimeError as error:
                if history and not guard_errors:
                    guard_errors.append(error)
                raise

        guard.check = checked

        def renewal():
            if mutation == "renewal_lost" and guard_errors:
                raise RuntimeError("original HTTP renewal lost")

        # Reporting failure is itself isolated; force a later unsafe source so
        # this contrast also proves that it cannot replace the original guard.
        if mutation == "reporting_failure":
            fresh["events"][-2]["recovery"]["started"] += .0625
        if rejected:
            with pytest.raises(RuntimeError) as caught:
                await guard.resume(expected_pause="input_expired", renewal_check=renewal,
                                   wall_deadline=1000.0625 if mutation == "caller_expired" else None)
            assert caught.value is guard_errors[0]
            assert calls == []
            assert not any(v["event"] == "qualified_native_recovery" for v in records)
        else:
            assert await guard.resume(expected_pause="input_expired", renewal_check=renewal) is True
            assert calls == ["new-resume-active-ack"]
            assert guard.episodes == prior + 1
            qualified = [v for v in records if v["event"] == "qualified_native_recovery"]
            assert len(qualified) == 1 and qualified[0]["original_recovery"] == episode
        assert client.sequence == 0
        assert client.snapshot["run"]["native_live_at"] == 100.0
        assert client.snapshot["run"]["deadline"] == 400.0
        for entry in records:
            if entry["event"] == "native_rejoin_qualification_changed":
                assert len(json.dumps(entry, allow_nan=False).encode()) <= 8192
        if mutation in {"caller_expired", "episode_expired"}:
            assert client.resumes == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("mutation", [
    None, "persistent", "raw_unavailable", "stale_hold", "fault", "uncertain", "owner", "connection",
    "revision", "manual", "episode", "caller_budget", "native_budget", "phase_budget", "renewal",
    "later_timeout", "later_refusal", "active_missing", "reporting", "release_unproven", "receipt_uncertain",
    "superseded_timeout", "oversize_evidence", "invalid_evidence",
])
def test_nonadmitted_alignment_resume_preserves_original_phase_and_error(monkeypatch, mutation):
    """Synthetic safety contrasts for the real Core/Gateway entrypoint witness."""
    import asyncio
    import copy
    import json
    import time
    from types import SimpleNamespace

    m = adapter()
    pc = [1000.]
    monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: pc[0], perf_counter=time.perf_counter))
    state = recovery_state()
    state["run"].update(status="paused", remaining_s=.125 if mutation == "native_budget" else 299.)
    state["evidence"].update(active_acknowledged=False)
    state["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"] = 101.
    state["events"] = [{"kind": "input_expired", "at": 100.9, "run_id": "owned", "service_incarnation": "service"}]
    receipt = {"accepted": False, "status": "refused", "effect_admitted": False,
               "reason": "qualified release/alignment required"}
    if mutation == "receipt_uncertain":
        receipt["uncertain"] = True
    original = m.CommandRefusedError(receipt)
    if mutation == "oversize_evidence":
        original.timing = {"untrusted": "x" * 9000}
    elif mutation == "invalid_evidence":
        original.timing = {"untrusted": float("nan")}
    later = TimeoutError("later Resume result unknown")
    calls, records, waits = [], [], []

    class Client:
        device, generation, connection = "device", "control", "connection"
        identities, resume = m.SessionClient.identities, m.SessionClient.resume
        reads = resumes = 0

        def __init__(self):
            self.snapshot = copy.deepcopy(state)

        async def get(self, path):
            assert path == "state"
            self.reads += 1
            pc[0] += .01
            fresh = copy.deepcopy(self.snapshot)
            if self.reads > 1:
                fresh["run"]["remaining_s"] -= .0625
                fresh["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"] = fresh["run"]["deadline"] - fresh["run"]["remaining_s"]
                if mutation in {"persistent", "raw_unavailable"}:
                    fresh["evidence"]["pose_aligned" if mutation == "persistent" else "feedback"] = False
                elif mutation == "stale_hold":
                    fresh["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"] = 100.
                elif mutation == "fault":
                    fresh["evidence"]["fault"] = "real native fault"
                elif mutation == "uncertain":
                    fresh["run"]["uncertain"] = True
                elif mutation == "owner":
                    fresh["controller"]["controller_generation"] = "foreign"
                elif mutation == "connection":
                    self.connection = "foreign"
                elif mutation == "revision":
                    fresh["run"]["intent_revision"] += 1
                elif mutation == "manual":
                    fresh["events"].append(dict(fresh["events"][-1], kind="pause", at=101.01))
                elif mutation == "episode":
                    fresh["run"].update(recovery_episodes=1, first_cause="feedback_loss")
                elif mutation in {"caller_budget", "native_budget"}:
                    pc[0] += 1.
            return {"snapshot": fresh}

        async def command(self, payload):
            calls.append(copy.deepcopy(payload))
            pc[0] += .01
            if payload["op"] == "connect":
                self.snapshot["run"]["intent_revision"] += 1
                revision = self.snapshot["run"]["intent_revision"]
                self.snapshot["run"]["live_input"]["cursor_intent_revision"] = revision
                self.snapshot["evidence"]["acknowledged_intent_revision"] = revision
                self.snapshot["evidence"]["accepted_host_action"]["intent_revision"] = revision
                return {"accepted": True, "status": "connected", "connection_generation": str(uuid4()), "snapshot": self.snapshot}
            if payload["op"] == "release_input":
                return {"accepted": True} if mutation == "release_unproven" else {
                    "accepted": True, "status": "released", "effect_admitted": False}
            assert payload["op"] == "resume"
            self.resumes += 1
            if self.resumes == 1 or mutation == "phase_budget":
                raise original
            if mutation == "later_timeout" or mutation == "superseded_timeout" and self.resumes > 2:
                raise later
            if mutation == "later_refusal":
                raise m.CommandRefusedError(dict(receipt, reason="stale or malformed intent revision"))
            if mutation == "superseded_timeout":
                pi_now = self.snapshot["run"]["deadline"] - self.snapshot["run"]["remaining_s"] + .0625
                self.snapshot["run"].update(status="paused", intent_revision=payload["intent_revision"] + 2,
                                             remaining_s=400. - pi_now)
                self.snapshot["events"].append({"kind": "input_expired", "at": pi_now,
                                                 "run_id": "owned", "service_incarnation": "service"})
            else:
                self.snapshot["run"].update(status="running", intent_revision=payload["intent_revision"] + 1)
                self.snapshot["evidence"]["active_acknowledged"] = mutation != "active_missing"
            return {"accepted": True, "status": "resuming"}

        async def wait(self, predicate, timeout):
            waits.append(timeout)
            if self.resumes:
                pc[0] += .125
            until = pc[0] + timeout
            while not predicate():
                pc[0] += .125
                if pc[0] >= until:
                    raise TimeoutError("original qualified phase unavailable")
                await asyncio.sleep(0)

    def record(value):
        if mutation == "reporting" and value["event"] == "resume_qualification_refused":
            raise OSError("failed optional fault report")
        records.append(value)

    async def scenario():
        client = Client()
        guard = m.SameRunRecovery(client, record)
        def healthy():
            if mutation == "renewal" and client.resumes:
                raise RuntimeError("original renewal stopped")
        deadline = 1000.125 if mutation == "caller_budget" else 1009. if mutation == "phase_budget" else 1002.
        if mutation is None:
            assert not await guard.resume(expected_pause="input_expired", wall_deadline=deadline, renewal_check=healthy)
            assert client.resumes == 2 and client.snapshot["evidence"]["active_acknowledged"] is True
        else:
            with pytest.raises(m.CommandRefusedError) as caught:
                await guard.resume(expected_pause="input_expired", wall_deadline=deadline, renewal_check=healthy)
            assert caught.value is original
            if mutation in {"later_timeout", "superseded_timeout"}:
                assert original.__cause__ is later
            if mutation == "phase_budget":
                assert client.resumes > 2 and 1008. <= pc[0] < 1009.
            elif mutation == "superseded_timeout":
                assert client.resumes == 3
            elif mutation not in {"later_timeout", "later_refusal", "active_missing"}:
                assert client.resumes == 1
        if mutation != "superseded_timeout":
            assert sum(c["op"] == "connect" for c in calls) == 1
            assert sum(c["op"] == "release_input" for c in calls) == 1
        assert not any(c["op"] in {"claim", "start", "grant", "input"} for c in calls)
        assert guard.episodes == 0 and guard.original["run"]["deadline"] == 400.
        resumes = [c for c in calls if c["op"] == "resume"]
        assert len({c["operation_id"] for c in resumes}) == len(resumes)
        for row in records:
            if row["event"].startswith("resume_qualification_"):
                assert row["proof_source"] in {"pc_sent_snapshot", "authenticated_owner_snapshot"}
                assert len(json.dumps(row, allow_nan=False).encode("utf-8")) <= 8192
        if mutation not in {"release_unproven", "receipt_uncertain", "reporting", "oversize_evidence", "invalid_evidence"}:
            assert any(r["event"] == "resume_qualification_refused" for r in records)
        if mutation is None:
            assert any(r["event"] == "resume_qualification_observed" for r in records)
    asyncio.run(asyncio.wait_for(scenario(), 1.))


@pytest.mark.parametrize("mutation", [
    None, "start", "ceiling", "count", "closed", "manual", "foreign", "fault", "uncertain", "cause",
    "phase_expired", "phase_refill_late", "episode_expired", "caller_expired", "later_error",
])
def test_same_open_native_episode_retains_source_and_original_phase(monkeypatch, mutation):
    """Synthetic53 safety/clock contrasts supplement the actual Core/Gateway sequence."""
    import asyncio
    import copy
    from types import SimpleNamespace

    m = adapter()
    pc, records, errors, commands = [1000.], [], [], []
    monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: pc[0]))
    paused = recovery_state()
    paused["run"].update(status="paused", intent_revision=3, remaining_s=299.875)
    paused["evidence"].update(active_acknowledged=False, acknowledged_intent_revision=3)
    paused["evidence"]["accepted_host_action"].update(intent_revision=3, acquired_at_monotonic_s=100.125)
    paused["run"]["live_input"]["cursor_intent_revision"] = 3
    paused["events"] = [{"kind": "input_expired", "at": 100.125,
                          "run_id": "owned", "service_incarnation": "service"}]
    original = {"started": 100.25, "ceiling": 110.25, "revision": 3}
    opened = copy.deepcopy(paused)
    opened["run"].update(status="recovering", intent_revision=3, remaining_s=299.625,
                         first_cause="feedback_loss", recovery_episodes=1, recovery=copy.deepcopy(original))
    opened["events"].append({"kind": "required_hold", "at": 100.375, "run_id": "owned",
                             "service_incarnation": "service", "recovery": copy.deepcopy(original)})
    ready = copy.deepcopy(opened)
    ready["run"].update(status="paused", intent_revision=4, remaining_s=299.5, recovery=None)
    ready["events"].append({"kind": "live_sensing_requalified", "at": 100.5,
                            "run_id": "owned", "service_incarnation": "service"})
    ready["evidence"].update(acknowledged_intent_revision=4)
    ready["evidence"]["accepted_host_action"].update(intent_revision=4, acquired_at_monotonic_s=100.5)
    ready["run"]["live_input"]["cursor_intent_revision"] = 4
    known = copy.deepcopy(ready)
    known["run"]["intent_revision"] = 5
    repeated = copy.deepcopy(known)
    descriptor = dict(original, revision=5)
    repeated["run"].update(status="recovering", remaining_s=299.375, recovery=descriptor)
    repeated["events"].append({"kind": "required_hold", "at": 100.625, "run_id": "owned",
                               "service_incarnation": "service", "recovery": copy.deepcopy(descriptor)})
    refilled = copy.deepcopy(repeated)
    refilled["run"].update(status="paused", intent_revision=6, remaining_s=299.25, recovery=None)
    refilled["events"].append({"kind": "live_sensing_requalified", "at": 100.75,
                               "run_id": "owned", "service_incarnation": "service"})
    refilled["evidence"].update(acknowledged_intent_revision=6)
    refilled["evidence"]["accepted_host_action"].update(intent_revision=6, acquired_at_monotonic_s=100.75)
    refilled["run"]["live_input"]["cursor_intent_revision"] = 6

    class Client:
        device, generation, connection, sequence = "device", "control", "connection", 0
        reads, attempts = 0, 0

        def __init__(self):
            self.snapshot = recovery_state()

        def identities(self):
            return m.SessionClient.identities(self)

        async def get(self, path):
            assert path == "state"
            self.reads += 1
            if self.reads == 3 and mutation == "phase_refill_late":
                pc[0] += .75
            self.snapshot = copy.deepcopy([paused, ready, refilled][min(self.reads - 1, 2)])
            return {"snapshot": self.snapshot}

        async def resume(self, *, check, refusal_record=None, qualification_ceiling=None):
            self.attempts += 1
            check(self.snapshot)
            assert guard.completed_episodes == 0
            if self.attempts == 1:
                self.snapshot = copy.deepcopy(opened)
            elif self.attempts == 2:
                assert qualification_ceiling == 1008.  # Original held phase, never now + 8.
                self.connection = str(uuid4())
                self.snapshot = copy.deepcopy(known)
                check(self.snapshot)  # Known acknowledged replacement, before the same deficit.
                self.snapshot = copy.deepcopy(repeated)
                if mutation in {"start", "ceiling"}:
                    self.snapshot["run"]["recovery"][{"start": "started", "ceiling": "ceiling"}[mutation]] += .125
                    self.snapshot["events"][-1]["recovery"] = copy.deepcopy(self.snapshot["run"]["recovery"])
                elif mutation == "count":
                    self.snapshot["run"]["recovery_episodes"] = 2
                elif mutation == "closed":
                    guard.completed_episodes = 1  # An already observed active ACK cannot reopen this token.
                elif mutation == "manual":
                    self.snapshot["events"].insert(-1, {"kind": "pause", "at": 100.5625,
                                                       "run_id": "owned", "service_incarnation": "service"})
                elif mutation == "foreign":
                    self.snapshot["run"]["run_id"] = "foreign"
                elif mutation == "fault":
                    self.snapshot["evidence"]["fault"] = "actual native fault"
                elif mutation == "uncertain":
                    self.snapshot["run"]["uncertain"] = True
                elif mutation == "cause":
                    self.snapshot["run"]["first_cause"] = "manual"
                if mutation in {"phase_expired", "episode_expired", "caller_expired", "phase_refill_late"}:
                    pc[0] += {"phase_expired": 8., "episode_expired": 10.5,
                              "caller_expired": .25, "phase_refill_late": 7.5}[mutation]
            else:
                assert qualification_ceiling == 1008.
                if mutation == "later_error":
                    raise ConnectionError("later transport failure")
                commands.append("new-resume-active-ack")
                self.snapshot["run"]["status"] = "running"
                self.snapshot["evidence"]["active_acknowledged"] = True
                check(self.snapshot)
                return
            try:
                check(self.snapshot)
            except m._NativeRecoveryPendingError as outcome:
                outcome.held_view_ceiling = 1008.  # Same field supplied by the real SessionClient phase.
                errors.append(outcome.original_error)
                raise

    async def scenario():
        nonlocal guard
        client = Client()
        guard = m.SameRunRecovery(client, records.append)
        if mutation is None:
            assert await guard.resume(expected_pause="input_expired")
            assert guard.completed_episodes == guard.episodes == 1
            assert commands == ["new-resume-active-ack"]
            assert len([v for v in records if v["event"] == "qualified_native_recovery"]) == 1
        else:
            with pytest.raises(RuntimeError) as caught:
                await guard.resume(expected_pause="input_expired",
                                   wall_deadline=1000.125 if mutation == "caller_expired" else None)
            assert caught.value is errors[0]
            assert guard.completed_episodes == (1 if mutation == "closed" else 0)
            assert commands == []
            assert not any(v["event"] == "qualified_native_recovery" for v in records)
        assert client.sequence == 0
        assert client.snapshot["run"]["deadline"] == 400.
        if mutation == "phase_refill_late":
            assert client.attempts == 2 and pc[0] == 1008.25
        if mutation in {None, "phase_refill_late", "later_error"}:
            handoffs = [v for v in records if v["event"] == "native_recovery_handoff"]
            assert len(handoffs) == 2
            assert all(v["original_recovery"]["started"] == original["started"]
                       and v["original_recovery"]["ceiling"] == original["ceiling"] for v in handoffs)
            assert handoffs[1]["local_ceiling_monotonic_s"] <= 1008.

    guard = None
    asyncio.run(scenario())
