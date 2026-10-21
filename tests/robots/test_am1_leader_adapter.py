"""Leader-format acquisition, clutch and finite workload without devices."""

import importlib
from uuid import uuid4

import pytest

from tests.robots.test_am1_live_teleop_contract import KEYS, workspace


def adapter():
    name = "tools.am1_leader_adapter"
    assert importlib.util.find_spec(name), "authenticated PC leader adapter is missing"
    return importlib.import_module(name)


@pytest.mark.parametrize("bench,required,qualified,accepted", [
    (False, True, False, False), (False, False, False, False),
    (True, True, False, False), (True, False, False, True),
    (True, False, True, True), (False, True, True, True),
])
def test_selected_bench_view_policy_preserves_actual_presentation(bench, required, qualified, accepted):
    import copy
    m = adapter()
    state = {"run": {"bench_continuity": bench, "live_input": {
        "required_view": {"required": required, "qualified": qualified}}}}
    original = copy.deepcopy(state)
    assert m._view_admitted(state) is accepted
    assert state == original


def test_ordinary_physical_teleop_lease_preserves_idle_and_other_mode_boundaries(tmp_path):
    from types import SimpleNamespace

    from tests.robots.test_am1_live_teleop_executor import authority_lane
    from tools.am1_session_core import LIVE_ELBOW_SPECS, TERMINAL

    a, executor, _ = authority_lane(tmp_path)
    try:
        for status in ("starting", "preparing", "paused", "resuming", "running", "recovering", "finishing"):
            a.run["status"] = status
            assert a._controller_lease_seconds() == 3.
        a.run.update(status="running", bench_continuity=True)
        assert a._controller_lease_seconds() == 6.
        for status in TERMINAL:
            a.run["status"] = status
            assert a._controller_lease_seconds() == 1.5
        a.run.update(status="running", bench_continuity=False, recipe=next(iter(LIVE_ELBOW_SPECS)))
        assert a._controller_lease_seconds() == 1.5
        a.run["recipe"] = "physical-live-teleop"
        a.executor = SimpleNamespace(source="fake")
        assert a._controller_lease_seconds() == 1.5
        a.run = None
        assert a._controller_lease_seconds() == 1.5
    finally:
        a.executor = executor
        a.run = None
        a.close()


@pytest.mark.parametrize("invalid", [False, True])
def test_bench_continuity_cli_selects_policy_before_start_and_bounds_controller_gap(monkeypatch, invalid):
    m = adapter()
    seen = []
    async def run(args):
        seen.append(args)
        return {}
    monkeypatch.setattr(m, "run", run)
    argv = ["--source", "usb-leaders" if invalid else "virtual-leaders", "--url", "unused", "--ca", "unused",
            "--storage-state", "unused", "--output", "unused", "--bench-continuity", "--workload-segment", "continuity",
            "--interrupt-controller", "--interrupt-at-seconds", "6", "--interruption-seconds", "7"]
    if invalid:
        with pytest.raises(SystemExit):
            m.main(argv)
        assert seen == []
    else:
        m.main(argv)
        assert seen[0].bench_continuity and seen[0].interrupt_controller
        assert seen[0].workload_segment == "continuity" and seen[0].interruption_seconds == 7


def test_combined_body_cli_preserves_normal_required_view_selection(monkeypatch):
    m = adapter()
    seen = []
    async def run(args):
        seen.append(args)
        return {}
    monkeypatch.setattr(m, "run", run)
    m.main(["--source", "virtual-leaders", "--url", "unused", "--ca", "unused",
            "--storage-state", "unused", "--output", "unused", "--workload-segment", "combined-body",
            "--duration-seconds", "65"])
    assert seen[0].workload_segment == "combined-body" and seen[0].duration_seconds == 65.
    assert seen[0].bench_continuity is False and seen[0].interrupt_at_seconds is None


@pytest.mark.parametrize("boundary", ["cli", "run"])
def test_predetermined_segment_refuses_usb_before_creating_run(monkeypatch, boundary):
    import asyncio
    from types import SimpleNamespace
    m = adapter()
    if boundary == "run":
        with pytest.raises(ValueError, match="virtual"):
            asyncio.run(m.run(SimpleNamespace(source="usb-leaders", workload_segment="body")))
        return
    async def forbidden_run(args):
        raise AssertionError("USB segment must refuse before Start")
    monkeypatch.setattr(m, "run", forbidden_run)
    argv = ["--source", "usb-leaders", "--workload-segment", "body", "--url", "unused", "--ca", "unused",
            "--storage-state", "unused", "--output", "unused", "--left-port", "unused", "--right-port", "unused",
            "--leader-id", "unused", "--calibration-dir", "unused"]
    with pytest.raises(SystemExit):
        m.main(argv)


@pytest.mark.parametrize("receipt_seconds,expected_sleep", [(0.02, 0.04), (0.24, 0.0), (0.5, 0.0)])
def test_current_acquisition_cadence_includes_receipt_cost_without_catchup(monkeypatch, receipt_seconds, expected_sleep):
    import asyncio
    from types import SimpleNamespace
    m = adapter()
    waits = []
    monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: 10.0 + receipt_seconds))
    async def sleep(value):
        waits.append(value)
    monkeypatch.setattr(m.asyncio, "sleep", sleep)
    asyncio.run(m._acquisition_delay(10.0))
    assert waits == pytest.approx([expected_sleep])


def test_cancelled_inflight_renewal_is_recorded_separately_from_failure():
    import asyncio
    from types import SimpleNamespace
    m = adapter()
    async def scenario():
        entered, records = asyncio.Event(), []
        async def post(path, body):
            entered.set()
            await asyncio.Event().wait()
        client = SimpleNamespace(generation="current", post=post, record=records.append)
        task = asyncio.create_task(m.SessionClient.renew(client))
        await asyncio.wait_for(entered.wait(), 1.)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(records) == 1 and records[0]["outcome"] == "cancelled"
        assert records[0]["receipt_at_monotonic_s"] >= records[0]["dispatch_at_monotonic_s"]
    asyncio.run(scenario())


@pytest.mark.parametrize("mutation", [None, "refused", "generation", "service", "deadline", "reference", "pause", "terminal",
    "receipt_lost", "qualification_read_lost", "receipt_expired", "receipt_cancelled", "receipt_superseded"])
def test_same_operation_reacquisition_rebinds_only_fresh_fenced_authority(monkeypatch, mutation):
    import asyncio
    import copy
    from types import SimpleNamespace
    m = adapter()
    state = recovery_state()
    state["run"].update(bench_continuity=True, operation_id=str(uuid4()), status="paused")
    old, fresh = str(uuid4()), str(uuid4())
    state["run"]["control_intent"] = {"eligible": True, "device_id": "device", "controller_generation": old,
        "service_incarnation": state["service_incarnation"], "start_operation_id": state["run"]["operation_id"]}
    state["controller"] = {"device_id": "device", "controller_generation": old}
    calls, records = [], []
    reads, claims = [0], []
    client = SimpleNamespace(snapshot=copy.deepcopy(state), device="device", generation=old, connection="old")
    guard = m.SameRunRecovery(client, records.append)
    anchored = copy.deepcopy(guard.original)
    state["controller"] = None
    if mutation == "service":
        state["service_incarnation"] = "replacement-service"
    if mutation == "deadline":
        state["run"]["deadline"] += 1
    if mutation == "reference":
        state["run"]["live_input"]["reference"][next(iter(KEYS))] += 1
    if mutation == "pause":
        state["events"].append({"kind": "pause", "run_id": state["run"]["run_id"],
                                "service_incarnation": state["service_incarnation"], "at": 10.1})
    if mutation == "terminal":
        state["run"]["status"] = "stopped"
    async def get(path):
        calls.append(("get", path))
        reads[0] += 1
        if mutation == "qualification_read_lost" and reads[0] == 2:
            raise TimeoutError("fresh current-state read lost after Claim")
        return {"snapshot": copy.deepcopy(state)}
    async def post(path, body):
        calls.append((path, copy.deepcopy(body)))
        claims.append(copy.deepcopy(body))
        if mutation == "refused":
            raise m.HTTPOutcomeError(403, {"accepted": False, "reason": "same-operation reacquisition fence"})
        state["controller"] = {"device_id": "device", "controller_generation": fresh, "presence_expires": 20.0}
        state["run"]["control_intent"]["controller_generation"] = fresh
        if isinstance(mutation, str) and mutation.startswith("receipt_"):
            if len(claims) == 1:
                raise TimeoutError("new Claim accepted but first receipt lost")
            if mutation == "receipt_expired":
                state["controller"] = None
            elif mutation == "receipt_superseded":
                state["controller"]["controller_generation"] = str(uuid4())
            elif mutation == "receipt_cancelled":
                state["run"]["control_intent"]["eligible"] = False
                state["events"].append({"kind": "pause", "run_id": state["run"]["run_id"],
                                        "service_incarnation": state["service_incarnation"], "at": 10.1})
        return {"accepted": True, "status": "claimed", "controller_generation": old if mutation == "generation" else fresh,
                "presence_expires": 20.0, "lease_seconds": 6.0}
    client.get, client.post = get, post
    async def scenario():
        if mutation in {None, "receipt_lost", "qualification_read_lost"}:
            await guard.reacquire()
            assert client.generation == fresh and client.connection is None
            assert guard.original == dict(anchored, controller={"device_id": "device", "controller_generation": fresh})
            claim = next(body for path, body in calls if path == "claim")
            assert str(uuid4()) != claim["operation_id"] and len(claim["operation_id"]) == 36
            assert claim["reacquire"] == {
                "run_id": anchored["run"]["run_id"], "service_incarnation": anchored["service"],
                "start_operation_id": anchored["run"]["operation_id"], "previous_generation": old}
            assert not any(path in {"input", "start", "resume", "connect"} for path, _ in calls)
            if mutation == "receipt_lost":
                assert len(claims) == 2 and claims[0] == claims[1]
        else:
            with pytest.raises((RuntimeError, m.HTTPOutcomeError)):
                await guard.reacquire()
            assert guard.original == anchored and client.generation == old
    asyncio.run(scenario())


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
        "bench_input_gap",
        "bench_controller_gap",
        "bench_renewal_during_resume",
        "bench_renewal_attach_ceiling",
        "bench_renewal_snapshot_race",
        "bench_input_receipt_unknown",
        "bench_input_receipt_pause",
        "bench_input_receipt_takeover",
        "bench_input_receipt_terminal",
        "bench_terminal_ack_delayed", "bench_terminal_ack_missing", "bench_terminal_ack_cancel",
        "bench_terminal_ack_stopped", "bench_terminal_ack_pause", "bench_terminal_ack_takeover",
        "bench_terminal_hold_rejoin", "bench_terminal_hold_pause", "bench_terminal_hold_takeover",
        "bench_terminal_hold_stopped", "bench_terminal_hold_cancel", "bench_terminal_hold_budget",
        "bench_terminal_hold_native_ceiling",
        "bench_program_finish", "bench_program_finish_failed",
        "peer_fixed_baseline_ready", "peer_fixed_baseline_missing", "peer_fixed_baseline_native", "peer_fixed_baseline_blocked",
        "normal_program_finish", "normal_program_finish_failed", "normal_program_finish_combined_body",
        "normal_program_finish_left_maintained", "normal_program_finish_left_reach", "normal_program_finish_left_supported", "normal_program_finish_left_opposite", "usb_finite_finish", "usb_finite_finish_failed", "usb_quit_finish", "usb_quit_empty_finish",
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
    peer_wait_case = isinstance(failure, str) and failure.startswith("peer_fixed_baseline_")
    peer_template = peer_baseline_state(peer_workspace()) if peer_wait_case else None
    initial_pause_case = failure == "initial_resume_pause"
    terminal_hold = isinstance(failure, str) and failure.startswith("bench_terminal_hold_")
    normal_finish = isinstance(failure, str) and failure.startswith(("normal_program_finish", "usb_finite_finish", "usb_quit_"))
    usb_finish = isinstance(failure, str) and failure.startswith(("usb_finite_finish", "usb_quit_"))
    program_finish = failure in {"bench_program_finish", "bench_program_finish_failed"} or normal_finish
    bench_case = failure in {"bench_input_gap", "bench_controller_gap", "bench_renewal_during_resume", "bench_renewal_attach_ceiling", "bench_renewal_snapshot_race",
                             "bench_input_receipt_unknown", "bench_input_receipt_pause", "bench_input_receipt_takeover", "bench_input_receipt_terminal",
                             "bench_terminal_ack_delayed", "bench_terminal_ack_missing", "bench_terminal_ack_cancel",
                             "bench_terminal_ack_stopped", "bench_terminal_ack_pause", "bench_terminal_ack_takeover"} or terminal_hold or program_finish
    ack_wait_case = isinstance(failure, str) and failure.startswith("bench_terminal_ack_")
    input_unknown = isinstance(failure, str) and failure.startswith("bench_input_receipt_")
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
            self.pauses = []
            self.start_requests = []
            self.cleanup_reads = 0
            self.inputs = []
            self.recovery_reads = 0
            self.authoritative = None
            self.resumes = 0
            self.expire_renewal = asyncio.Event()
            self.renewals = 0
            self.attachment_overran = False
            self.claim_posts = 0
            self.terminal_waits = 0
            instances.append(self)

        async def attach(self):
            if failure == "bench_renewal_attach_ceiling" and self.starts:
                await asyncio.sleep(.1)
                self.attachment_overran = True
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
            if bench_case and self.starts:
                raise RuntimeError("ordinary Claim after bench Start invalidates durable intent")

        async def detach(self):
            if bench_case:
                self.snapshot["run"]["status"] = "paused"
                if failure == "bench_controller_gap":
                    self.snapshot["controller"] = None

        async def renew(self):
            self.renewals += 1
            if failure in {"bench_renewal_during_resume", "bench_renewal_attach_ceiling"} and self.renewals == 1:
                await self.expire_renewal.wait()
                raise m.HTTPOutcomeError(403, {"accepted": False, "reason": "controller renewal fence"})
            if failure == "renew":
                from aiohttp import ServerDisconnectedError

                raise ServerDisconnectedError("injected renewal transport failure")
            await asyncio.Event().wait()

        def state(self, run_id):
            w = peer_workspace() if peer_wait_case else workspace()
            if peer_wait_case:
                w["reference"]["arm_left_elbow_flex.pos"] = m.normalized_raw(2043, w["calibration"]["arm_left_elbow_flex"])
            if failure == "normal_program_finish_left_supported":
                w["bounds"]["arm_left_shoulder_lift.pos"]["rate_per_s"] = 3.
            if failure == "normal_program_finish_left_opposite":
                w["bounds"]["arm_left_elbow_flex.pos"]["rate_per_s"] = 3.
            value = {
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

            if peer_wait_case:
                value["service_incarnation"] = peer_template["service_incarnation"]
                value["run"]["backend_incarnation"] = peer_template["run"]["backend_incarnation"]
                if failure == "peer_fixed_baseline_native":
                    value["run"]["remaining_s"] = .12
                value["evidence"].update(copy.deepcopy(peer_template["evidence"]))
                value["evidence"]["accepted_host_action"]["run_id"] = run_id
                value["evidence"]["accepted_host_action"].pop("teleop_elbow_settings")
            return value

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
            if bench_case and op == "claim":
                self.claim_posts += 1
                if failure == "bench_renewal_snapshot_race" and "controller_generation" in payload:
                    self.snapshot["controller"] = None
                    raise m.HTTPOutcomeError(403, {"accepted": False, "status": "refused", "effect_admitted": False,
                                                   "reason": "controller renewal fence"})
                if "reacquire" in payload:
                    generation = str(uuid4())
                    self.snapshot["controller"] = {"device_id": "device", "controller_generation": generation}
                    self.snapshot["run"]["control_intent"]["controller_generation"] = generation
                else:
                    assert payload == {"controller_generation": self.generation}
                    generation = self.generation
                return {"accepted": True, "status": "claimed", "controller_generation": generation}
            if program_finish and op == "pause":
                self.pauses.append(copy.deepcopy(payload))
                current = self.snapshot
                revision = current["run"]["intent_revision"] + 1
                now = current["run"]["deadline"] - current["run"]["remaining_s"]
                current["run"].update(status="paused", intent_revision=revision, recovery=None)
                if current["run"].get("control_intent") is not None:
                    current["run"]["control_intent"].update(eligible=False, invalidated_by="pause")
                current["run"]["live_input"].update(body=dict(m.ZERO_BODY), cursor_intent_revision=revision)
                current["evidence"].update(active_acknowledged=False, acknowledged_intent_revision=revision)
                current["evidence"]["accepted_host_action"].update(state="paused", intent_revision=revision,
                    epoch=2, acquired_at_monotonic_s=now, accepted_action={"mode": "raw_goal_hold", "body_velocity": 0.})
                current["evidence"]["measured_body"].update({"lift_axis.vel": -50, "theta.vel": .5859375})
                current["events"] = [{"kind": "pause", "at": now, "run_id": owned_id, "service_incarnation": "service"}]
                if failure.endswith("failed"):
                    current["evidence"]["fault"] = "Native held qualification unavailable"
                return {"accepted": True, "status": "paused", "effect_admitted": False}
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
                if bench_case:
                    self.snapshot["run"].update(bench_continuity=True, operation_id=self.start_requests[0]["operation_id"],
                        control_intent={"eligible": True, "device_id": self.device, "controller_generation": self.generation,
                            "service_incarnation": "service", "start_operation_id": self.start_requests[0]["operation_id"]})
                    if normal_finish:
                        self.snapshot["run"].update(bench_continuity=False, control_intent=None)
                        self.snapshot["run"]["live_input"]["required_view"].update(required=True, role="chest")
                    if failure == "bench_renewal_attach_ceiling":
                        self.snapshot["run"]["remaining_s"] = .02
                    if program_finish:
                        self.snapshot["evidence"]["accepted_host_action"].update(epoch=1, acquired_at_monotonic_s=1.)
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
            if terminal_hold and len(self.inputs) == 3 and not self.stops:
                self.terminal_waits += 1
                prior = self.inputs[-2]
                current = self.snapshot
                revision = current["run"]["intent_revision"] + 1
                current["run"].update(status="paused", intent_revision=revision, recovery=None)
                current["run"]["live_input"].update(last_input_seq=prior["seq"], source=prior["source"],
                                                      cursor_intent_revision=revision)
                current["evidence"].update(active_acknowledged=False, acknowledged_intent_revision=revision)
                current["evidence"]["accepted_host_action"].update(state="paused", intent_revision=revision)
                current["events"] = [{"kind": "native_input_expired", "at": 2., "run_id": owned_id,
                                      "service_incarnation": "service"}]
                if failure.endswith("pause"):
                    current["events"][0]["kind"] = "pause"
                    current["run"]["control_intent"]["eligible"] = False
                elif failure.endswith("takeover"):
                    current["controller"] = {"device_id": "other", "controller_generation": str(uuid4())}
                elif failure.endswith("stopped"):
                    current["run"].update(status="stopped", cleanup="physical_readback_verified", uncertain=False)
                elif failure.endswith("cancel"):
                    raise asyncio.CancelledError("cancellation after current hold publication")
                assert not predicate(), "the preceding source ACK cannot qualify the current fresh sample"
                await asyncio.sleep(timeout)
                raise TimeoutError("current source publication crossed an ordinary input expiry")
            if ack_wait_case and len(self.inputs) >= 2 and not self.stops:
                self.terminal_waits += 1
                if self.terminal_waits == 1 or failure == "bench_terminal_ack_missing":
                    prior = self.inputs[-2]
                    self.snapshot["run"]["live_input"].update(last_input_seq=prior["seq"], source=prior["source"])
                    assert not predicate(), "an earlier fresh ACK cannot qualify the current acquisition"
                    if failure == "bench_terminal_ack_cancel":
                        raise asyncio.CancelledError("external cancellation during current ACK wait")
                    if failure == "bench_terminal_ack_stopped":
                        self.snapshot["run"].update(status="stopped", cleanup="physical_readback_verified", uncertain=False)
                    elif failure == "bench_terminal_ack_pause":
                        self.snapshot["run"]["status"] = "paused"
                        self.snapshot["run"]["control_intent"]["eligible"] = False
                        self.snapshot["events"] = [{"kind": "pause", "at": 2., "run_id": owned_id, "service_incarnation": "service"}]
                    elif failure == "bench_terminal_ack_takeover":
                        self.snapshot["controller"] = {"device_id": "other", "controller_generation": str(uuid4())}
                    await asyncio.sleep(timeout)
                    raise TimeoutError("current native ACK publication is delayed")
            assert predicate()

        def identities(self):
            if mailbox_case or initial_pause_case or bench_case:
                return dict(run_id=owned_id, service_incarnation="service",
                            controller_generation=self.generation, connection_generation=self.connection,
                            host_incarnation="host", intent_revision=self.snapshot["run"]["intent_revision"])
            return {"run_id": self.snapshot["run"]["run_id"] if foreign_boundary else owned_id}

        async def resume(self, *, check=None, refusal_record=None):
            assert self.snapshot["run"]["run_id"] == owned_id
            if failure in {"bench_renewal_during_resume", "bench_renewal_attach_ceiling"} and self.resumes == 0:
                self.resumes += 1
                self.snapshot["controller"] = None
                self.snapshot["run"]["status"] = "paused"
                self.expire_renewal.set()
                await asyncio.sleep(0)
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
            if recovery_case or ordinary_case or mailbox_case or bench_case:
                assert self.snapshot["run"]["status"] != "recovering"
                self.resumes += 1
                if terminal_hold and self.resumes == 2:
                    assert self.snapshot["events"][-1]["kind"] == "native_input_expired"
                    assert self.snapshot["evidence"]["hold_acknowledged"] is True
                    if failure.endswith(("budget", "native_ceiling")):
                        await asyncio.sleep(2.05)  # A normal rearm can exceed the old whole-finish ceiling.
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
                if terminal_hold and self.snapshot["run"]["status"] != "running":
                    raise m.CommandRefusedError({"accepted": False, "status": "refused", "effect_admitted": False,
                                                "reason": "interactive running required"})
                if ack_wait_case and self.snapshot["run"]["control_intent"]["eligible"] is False:
                    raise m.CommandRefusedError({"accepted": False, "status": "refused", "effect_admitted": False,
                                                "reason": "same-operation control intent cancelled"})
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
            if peer_wait_case:
                if failure == "peer_fixed_baseline_blocked":
                    await asyncio.sleep(10.)  # The one original baseline ceiling must cancel an in-flight receipt wait.
                await asyncio.sleep(.03)  # Actual102-shaped asynchronous Native proof follows accepted reference holds.
                if failure == "peer_fixed_baseline_ready" and len(self.inputs) >= 3:
                    self.snapshot["evidence"]["accepted_host_action"]["teleop_elbow_settings"] = copy.deepcopy(
                        peer_template["evidence"]["accepted_host_action"]["teleop_elbow_settings"])

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
            if peer_wait_case and "teleop_elbow_settings" not in self.snapshot["evidence"]["accepted_host_action"]:
                self.snapshot["run"]["live_input"].update(last_input_seq=-1, source=None, state="held", accepted_at=None)
            self.snapshot["evidence"]["measured_positions"] = payload["target"]
            if terminal_hold:
                self.snapshot["evidence"]["active_acknowledged"] = True
                self.snapshot["evidence"]["measured_body"]["theta.vel"] = -.5859375 if len(self.inputs) == 2 else 0.
            if ordinary_case and len(self.inputs) <= 4:
                self.snapshot["run"].update(
                    status="paused", intent_revision=self.snapshot["run"]["intent_revision"] + 1
                )
                self.snapshot["events"] = [
                    {"kind": "input_expired", "at": 2.0, "run_id": owned_id, "service_incarnation": "service"}
                ]
            if (failure == "bench_input_receipt_terminal" or ack_wait_case or terminal_hold or program_finish) and len(self.inputs) == 1:
                await asyncio.sleep(.03)  # Cross the Windows monotonic tick before terminal qualification.
                if failure == "bench_terminal_ack_missing":
                    self.snapshot["run"]["remaining_s"] = 2.  # The new declared bench finish budget remains Native-clipped.
                if failure == "bench_terminal_hold_native_ceiling":
                    self.snapshot["run"]["remaining_s"] = .6
            if input_unknown and len(self.inputs) == (2 if failure == "bench_input_receipt_terminal" else 1):
                self.snapshot["run"]["status"] = "paused"
                self.snapshot["events"] = [{"kind": "native_input_expired", "at": 2., "run_id": owned_id,
                                            "service_incarnation": "service"}]
                if failure == "bench_input_receipt_pause":
                    self.snapshot["events"][0]["kind"] = "pause"
                    self.snapshot["run"]["control_intent"]["eligible"] = False
                elif failure == "bench_input_receipt_takeover":
                    self.snapshot["controller"] = {"device_id": "new-owner", "controller_generation": str(uuid4())}
                    self.snapshot["run"]["control_intent"]["eligible"] = False
                raise TimeoutError("input accepted; original receipt unavailable")
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
    if peer_wait_case:
        import json
        args.source, args.next_start_seconds, args.duration_seconds = "virtual-leaders", None, .05
        args.workload_segment, args.peer_side, args.peer_direction, args.peer_raw_step = "left-peer-fixed", "left", -1, 52
        if failure == "peer_fixed_baseline_ready":
            summary = asyncio.run(asyncio.wait_for(m.run(args), 5))
        else:
            with pytest.raises(TimeoutError, match="baseline"):
                asyncio.run(asyncio.wait_for(m.run(args), 5))
            summary = json.loads((tmp_path / "evidence/summary.json").read_text())
        records = [json.loads(line) for line in (tmp_path / "evidence/frames.jsonl").read_text().splitlines()]
        frames = [r for r in records if r["event"] == "leader_frame"]
        verified = next((r for r in records if r["event"] == "peer_baseline_settings_verified"), None)
        held = [r for r in frames if verified is None or r["local_monotonic_s"] < verified["local_monotonic_s"]]
        assert len(held) >= (0 if failure.endswith("blocked") else 1 if failure.endswith("native") else 3)
        assert all(r["progress_s"] == 0. for r in held)
        assert all(r["frame"]["target"] == summary["workspace"]["reference"] for r in held)
        assert all(r["frame"]["body"] == m.ZERO_BODY for r in frames)
        if frames:
            assert frames[0]["receipt"]["accepted"] is True
            assert frames[0]["native"]["run"]["live_input"]["last_input_seq"] == -1
            assert frames[0]["native"]["run"]["live_input"]["source"] is None
        assert records[0]["peer_baseline_qualification_budget_seconds"] == 2.
        assert [r["frame"]["source"]["sample_seq"] for r in frames] == list(range(1, len(frames)+1))
        assert len({r["grant"]["grant_id"] for r in frames}) == len(frames)
        assert all(0 <= r["frame"]["source"]["sent_at_monotonic_s"]-r["frame"]["source"]["acquired_at_monotonic_s"] < .25 for r in frames)
        assert summary["peer_baseline_qualification_seconds"] >= .06
        assert summary["original_native_deadline"] == 301. and instances[0].stops == [owned_id]
        if verified:
            assert .05 <= summary["active_progress_s"] <= .15  # One retained max .1s advancement may cross the .05s fixture end.
            assert summary["peer_baseline_qualified"] is True
        else:
            assert summary["active_progress_s"] == 0. and not summary["producer_program_complete"]
            assert summary["peer_baseline_qualified"] is False
            assert (.09 if failure.endswith("native") else 1.95) <= summary["peer_baseline_qualification_seconds"] < (.3 if failure.endswith("native") else 2.5)
        return
    if bench_case:
        import json
        args.source, args.next_start_seconds, args.duration_seconds = "virtual-leaders", None, .001
        args.bench_continuity, args.workload_segment = True, "combined"
        args.interrupt_controller = failure == "bench_controller_gap"
        args.interrupt_at_seconds, args.interruption_seconds = (None if input_unknown or ack_wait_case or terminal_hold or program_finish or failure in {"bench_renewal_during_resume", "bench_renewal_attach_ceiling"} else 0.), .001
        if program_finish:
            if normal_finish:
                args.bench_continuity = False
            if failure == "normal_program_finish_combined_body":
                args.workload_segment = "combined-body"
            if failure in {"normal_program_finish_left_maintained", "normal_program_finish_left_reach"}:
                args.workload_segment = "left-maintained" if failure.endswith("maintained") else "left-reach"
            if failure == "normal_program_finish_left_supported":
                args.workload_segment = "left-supported-reach"
            if failure == "normal_program_finish_left_opposite":
                args.workload_segment = "left-opposite-first"
            if usb_finish:
                args.source, args.workload_segment = "usb-leaders", "full"
            if failure.startswith("usb_quit_"):
                import sys
                from types import ModuleType
                keyboard_module = ModuleType("lerobot.teleoperators.keyboard.teleop_keyboard")
                class Keyboard:
                    is_connected = True
                    def __init__(self, *args):
                        self.calls = 0
                    def connect(self):
                        pass
                    def disconnect(self):
                        pass
                    def get_action(self):
                        self.calls += 1
                        return {} if self.calls == 1 and failure == "usb_quit_finish" else {"q": True}
                keyboard_module.KeyboardTeleop = Keyboard
                keyboard_module.KeyboardTeleopConfig = lambda **kwargs: None
                monkeypatch.setitem(sys.modules, keyboard_module.__name__, keyboard_module)
                args.no_keyboard, args.duration_seconds = False, 10.
            from examples.alohamini import am1_teleop_workload
            original_workload = am1_teleop_workload.TeleopWorkload
            class ShortProgramme(original_workload):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self.duration_s = .001  # Reporting seam only; real Native held proof is exercised below.
            monkeypatch.setattr(am1_teleop_workload, "TeleopWorkload", ShortProgramme)
            if failure in {"normal_program_finish_left_maintained", "normal_program_finish_left_reach", "normal_program_finish_left_supported", "normal_program_finish_left_opposite"}:
                original_selected = m.SelectedLeftWorkload
                class ShortSelected(original_selected):
                    def __init__(self, *args, **kwargs):
                        super().__init__(*args, **kwargs)
                        self.duration_s = .001  # Runner/reporting seam; full16/20s clocks tested independently.
                monkeypatch.setattr(m, "SelectedLeftWorkload", ShortSelected)
            if failure.endswith("failed"):
                with pytest.raises(RuntimeError, match="Native held qualification unavailable"):
                    asyncio.run(asyncio.wait_for(m.run(args), 10))
                summary = json.loads((tmp_path / "evidence/summary.json").read_text())
            else:
                summary = asyncio.run(asyncio.wait_for(m.run(args), 10))
                assert summary["native_held_qualified"] is True and summary["zero_body_command"] is True
                assert summary["measured_body_exact_zero"] is False and summary["final_body_zero"] is False
            session = instances[0]
            records = [json.loads(line) for line in (tmp_path / "evidence/frames.jsonl").read_text().splitlines()]
            start = next(row for row in records if row["event"] == "start_requested")
            if normal_finish:
                assert start["terminal_completion_policy"] == "owned_pause_native_hold_after_finite_input"
                assert start["view_policy"] == "required_presentation"
                assert start["terminal_qualification_budget_seconds"] == 2.
                assert summary["producer_program_complete"] is (not usb_finish)
                assert summary["native_execution_complete"] is False
                if failure == "normal_program_finish_combined_body":
                    assert start["workload_segment"] == summary["workload_segment"] == "combined-body"
                    assert start["program_active_seconds"] == 64.5
                    assert start["bench_continuity"] is False
                if failure in {"normal_program_finish_left_maintained", "normal_program_finish_left_reach"}:
                    expected = "left-maintained" if failure.endswith("maintained") else "left-reach"
                    assert start["workload_segment"] == summary["workload_segment"] == expected
                    assert start["program_active_seconds"] == (16. if expected == "left-maintained" else 20.)
                    plan = next(row for row in records if row["event"] == "workload_plan")
                    assert plan["run_id"] == owned_id and plan["segment"] == expected
                    assert plan["reference"] == summary["workspace"]["reference"]
                    assert summary["selected_profile"]["reference"] == plan["reference"]
                    assert summary["selected_profile"]["endpoint_normalized"] == plan["endpoint_normalized"]
                    assert start["bench_continuity"] is False
                if failure == "normal_program_finish_left_supported":
                    assert start["workload_segment"] == summary["workload_segment"] == "left-supported-reach"
                    assert start["program_active_seconds"] == 28. and start["bench_continuity"] is False
                    assert summary["selected_profile"]["reference"] == summary["workspace"]["reference"]
                    assert summary["selected_profile"]["shoulder_endpoint_normalized"] == summary["workspace"]["bounds"]["arm_left_shoulder_lift.pos"]["max"]
                if failure == "normal_program_finish_left_opposite":
                    assert start["workload_segment"] == summary["workload_segment"] == "left-opposite-first"
                    assert start["program_active_seconds"] == 18. and start["bench_continuity"] is False
                    plan = summary["selected_profile"]
                    assert plan["reference"] == summary["workspace"]["reference"]
                    assert plan["first_endpoint_normalized"] == summary["workspace"]["bounds"]["arm_left_elbow_flex.pos"]["min"]
                    assert plan["endpoint_normalized"] == summary["workspace"]["bounds"]["arm_left_elbow_flex.pos"]["max"]
                    assert plan["gripper_opening_delta"] == 0.
                if usb_finish:
                    assert summary["clean_input_end"] == ("local_quit" if failure.startswith("usb_quit_") else "duration_elapsed")
            else:
                assert start["terminal_completion_policy"] == "owned_pause_native_hold_after_program_complete"
                assert summary["producer_program_complete"] is True and summary["native_execution_complete"] is False
            assert summary["finish_qualified"] is (not failure.endswith("failed"))
            assert summary["physical_register_off_readback_verified"] is True
            assert session.starts == 1 and session.stops == [owned_id] and session.resumes == 1
            if failure == "usb_quit_empty_finish":
                assert len(session.inputs) == 0 and len(session.pauses) == 1
                assert summary["frame_count"] == 0 and summary["endpoint_error_normalized"] == {}
            else:
                assert len(session.inputs) == len(session.pauses) == 1
            if not failure.endswith("failed"):
                assert summary["pause_operation_id"] == session.pauses[0]["operation_id"]
            assert not any(row.get("phase") == "terminal_zero_qualification" for row in records)
            return
        if terminal_hold:
            if failure.endswith(("rejoin", "budget")):
                summary = asyncio.run(asyncio.wait_for(m.run(args), 10))
                assert summary["final_body_zero"] is True and summary["frame_count"] == 1
                assert summary["terminal_qualification_budget_seconds"] == 12.
                assert summary["original_native_deadline"] == 301. and summary["active_progress_s"] < .1
                if failure.endswith("budget"):
                    assert 2. < summary["terminal_qualification_seconds"] < 12.
            else:
                expected = (asyncio.CancelledError if failure.endswith("cancel") else
                            TimeoutError if failure.endswith("native_ceiling") else RuntimeError)
                with pytest.raises(expected):
                    asyncio.run(asyncio.wait_for(m.run(args), 10))
            session = instances[0]
            records = [json.loads(line) for line in (tmp_path / "evidence" / "frames.jsonl").read_text().splitlines()]
            start = next(row for row in records if row["event"] == "start_requested")
            finish = next(row for row in records if row["event"] == "terminal_qualification_started")
            assert start["terminal_qualification_budget_seconds"] == 12.
            assert finish["budget_seconds"] == 12.
            assert finish["original_ceiling_monotonic_s"] == pytest.approx(
                finish["local_monotonic_s"] + (.6 if failure.endswith("native_ceiling") else 12.), abs=.02)
            assert all(row["original_ceiling_monotonic_s"] == finish["original_ceiling_monotonic_s"]
                       for row in records if row["event"] == "terminal_zero_ack_pending")
            assert session.resumes == (2 if failure.endswith(("rejoin", "budget", "native_ceiling")) else 1)
            assert len(session.inputs) == (4 if failure.endswith(("rejoin", "budget")) else 3)
            assert [row["source"]["sample_seq"] for row in session.inputs] == list(range(1, len(session.inputs) + 1))
            assert len({row["grant_id"] for row in session.inputs}) == len(session.inputs)
            assert session.starts == 1 and session.stops == ([] if failure.endswith("stopped") else [owned_id])
            assert not any(row["event"] == "leader_grant_rejected" for row in records)
            return
        if ack_wait_case:
            expected = None if failure == "bench_terminal_ack_delayed" else asyncio.CancelledError if failure.endswith("cancel") else RuntimeError
            if expected is None:
                summary = asyncio.run(asyncio.wait_for(m.run(args), 10))
                assert summary["final_body_zero"] is True and summary["frame_count"] == 1
                assert summary["terminal_qualification_seconds"] < 2.
            else:
                with pytest.raises(expected):
                    asyncio.run(asyncio.wait_for(m.run(args), 10))
            session = instances[0]
            records = [json.loads(line) for line in (tmp_path / "evidence" / "frames.jsonl").read_text().splitlines()]
            if failure == "bench_terminal_ack_delayed":
                assert len(session.inputs) == 3 and session.terminal_waits == 2
                assert any(row["event"] == "terminal_zero_ack_pending" for row in records)
            elif failure == "bench_terminal_ack_missing":
                assert 2 < len(session.inputs) < 20
                assert session.inputs[-1]["source"]["acquired_at_monotonic_s"] > session.inputs[1]["source"]["acquired_at_monotonic_s"]
            else:
                assert len(session.inputs) == 2
            assert len({row["grant_id"] for row in session.inputs}) == len(session.inputs)
            assert [row["source"]["sample_seq"] for row in session.inputs] == list(range(1, len(session.inputs) + 1))
            assert session.starts == 1 and session.stops == ([] if failure.endswith("stopped") else [owned_id])
            return
        if input_unknown:
            if failure in {"bench_input_receipt_unknown", "bench_input_receipt_terminal"}:
                summary = asyncio.run(asyncio.wait_for(m.run(args), 10))
                session = instances[0]
                records = [json.loads(line) for line in (tmp_path / "evidence" / "frames.jsonl").read_text().splitlines()]
                assert session.resumes == 2 and session.starts == 1 and session.stops == [owned_id]
                assert summary["frame_count"] >= 1 and summary["cleanup_errors"] == []
                assert [row["source"]["sample_seq"] for row in session.inputs] == list(range(1, len(session.inputs) + 1))
                assert len({row["grant_id"] for row in session.inputs}) == len(session.inputs)
                assert sum(row["event"] == "leader_frame_receipt_unknown" for row in records) == 1
                assert sum(row["event"] == "bench_input_receipt_requalification" for row in records) == 1
                assert summary["original_native_deadline"] == 301.
                if failure == "bench_input_receipt_terminal":
                    assert summary["frame_count"] == 1 and summary["final_body_zero"] is True
                    assert summary["terminal_qualification_seconds"] < 2.
            else:
                with pytest.raises(RuntimeError):
                    asyncio.run(asyncio.wait_for(m.run(args), 10))
                session = instances[0]
                assert session.starts == 1 and len(session.inputs) == 1 and session.stops == [owned_id]
            return
        if failure == "bench_renewal_attach_ceiling":
            with pytest.raises(TimeoutError):
                asyncio.run(asyncio.wait_for(m.run(args), 10))
            session = instances[0]
            assert not session.attachment_overran and session.claim_posts == 0
            assert session.starts == 1 and session.stops == [owned_id] and session.inputs == []
            return
        summary = asyncio.run(asyncio.wait_for(m.run(args), 10))
        session = instances[0]
        records = [json.loads(line) for line in (tmp_path / "evidence" / "frames.jsonl").read_text().splitlines()]
        assert session.starts == 1 and session.stops == [owned_id]
        assert session.start_requests[0]["bench_continuity"] is True
        assert summary["interrupted"] is (failure != "bench_renewal_during_resume")
        assert summary["frame_count"] >= 1 and summary["cleanup_errors"] == []
        assert sum(row["event"] == "same_operation_reacquired" for row in records) == int(failure != "bench_input_gap")
        if failure == "bench_renewal_during_resume":
            assert session.resumes == 2
            assert any(row["event"] == "controller_renewal_ended" for row in records)
        assert all(frame["source"]["sample_seq"] == n for n, frame in enumerate(session.inputs, 1))
        assert summary["original_native_deadline"] == 301.
        return
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


@pytest.mark.parametrize("cause", ["feedback", "view", "server_connection_replaced", "connect_loss_pre", "connect_loss_post", "connect_attach_loss_pre", "connect_attach_loss_post", "release_loss_pre", "release_loss_post", "release_connection_loss", "resume_view_pause", "resume_view_twice", "resume_view_refusal", "resume_view_refused_twice", "resume_pong_pause", "grant_loss_pre", "grant_loss_post", "grant_connection_loss", "grant_view_reconnect", "grant_view_switch", "connect_view_reconnect", "release_view_reconnect", "qualification_episode_pending", "qualification_episode_cleared", "qualification_same_episode", "resume_result_loss_pre", "resume_result_loss_post", "resume_result_loss_native", "resume_result_loss_pending", "held_qualification_loss", "resume_alignment_refusal", "resume_alignment_refused_twice", "runner_initial_cursor_pending", "resume_reply_timeout_pre", "resume_reply_timeout_post", "resume_reply_timeout_alignment", "resume_reply_timeout_alignment_persistent", "acquisition_input_pause", "receipt_ack_starvation", "ack_never_returns", "ack_close_cancel", "transport_blackout_hb1", "transport_blackout_hb2", "transport_blackout_long", "initial_ack_timely", "initial_ack_missing", "connect_receipt_fast", "connect_receipt_delayed", "connect_receipt_missing", "connect_receipt_late", "connect_receipt_caller_ceiling", "connect_receipt_input", "connect_receipt_grant", "renew_receipt_cost", "renew_receipt_cost_expired", "bench_reacquire", "bench_reacquire_receipt_lost", "bench_reacquire_receipt_cancelled", "bench_input_receipt_unknown_native", "bench_input_receipt_unknown_pause", "grant_close_timeout", "grant_close_timeout_pause", "grant_close_timeout_takeover", "grant_close_timeout_cancel", "resume_send_pre", "resume_send_post", "resume_send_pause", "resume_send_takeover", "resume_send_stop", "resume_send_cancel", "resume_send_budget", "resume_send_reboot", "bench_finish_hold", "bench_finish_no_ack", "bench_finish_stale_revision", "bench_finish_body_nonzero", "bench_finish_fault", "bench_finish_external_pause", "bench_finish_stop", "bench_finish_takeover", "bench_finish_reboot", "bench_finish_cancel", "bench_finish_pause_unknown", "bench_finish_budget", "bench_finish_native_ceiling", "bench_finish_strict", "normal_finish_hold", "normal_finish_no_ack", "normal_finish_stale_revision", "normal_finish_body_nonzero", "normal_finish_fault", "normal_finish_external_pause", "normal_finish_stop", "normal_finish_takeover", "normal_finish_reboot", "normal_finish_cancel", "normal_finish_pause_unknown", "normal_finish_budget", "normal_finish_native_ceiling", "normal_finish_view_before", "normal_finish_view_after", "ordinary_lease_dispatch_stall", "ordinary_lease_expired", "ordinary_start_late_first_renewal", "peer_start_normal", "peer_start_bench", "peer_bench_unqualified_view", "peer_bench_local_loss", "peer_normal_unqualified_view"])
def test_real_core_ipc_tls_wss_recovery_requires_qualified_pause_before_explicit_resume(tmp_path, monkeypatch, cause):
    import asyncio
    import copy
    import json
    import ssl
    import subprocess

    from aiohttp import web

    from tests.robots.am1_session_fixture import OPENSSL
    from tests.robots.test_am1_live_teleop_executor import authority_lane
    from tools.am1_session_ipc import OwnerServer
    from tools.am1_session_service import Gateway, Peer

    m = adapter()
    a, e, ids = authority_lane(tmp_path / "authority")
    a.run.update(backend_incarnation=e.host_incarnation, recovery_episodes=0, status="paused")
    a._teleop_view = {"run_id": e.run_id, "qualified_until": 10.5, "role": "chest"}
    healthy, epoch, commands = [True], [0], []
    cursor_ready = [cause != "runner_initial_cursor_pending"]
    if not cursor_ready[0]:
        e.desired = "hold"
        a.run.update(accepted_at=8., startup_deadline=188.)

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
        if not cursor_ready[0]:
            for key in ("cursor", "cursor_intent_revision", "cursor_feedback_sequence"):
                live.pop(key, None)
            value.update(feedback=False, native_ack=False, pose_aligned=False,
                         hold_acknowledged=False, active_acknowledged=False)
        return value

    e.evidence = evidence  # Explicit fake native sensing/ACK; Core/IPC/gateway/client are production.

    def dispatch(recipe):
        e.desired = "active"
        epoch[0] += 1
        return True

    e.dispatch = dispatch
    real_handle = a.handle

    def handle(command, device):
        commands.append((command["op"], a.run["status"] if a.run else None))
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

    async def scenario(cause=cause):
        owner = OwnerServer(tmp_path / "owner")
        owner.authority.close()
        owner.authority = a
        owner.stopping.set()  # Test explicitly advances the real Core, without any device worker.
        gateway = Gateway(owner.directory, tmp_path / "auth")
        enrolled = gateway.auth.enroll(gateway.auth.pair(control=True))
        a.controller["device_id"] = a.connection["device_id"] = enrolled["device_id"]
        if cause.startswith(("bench_reacquire", "bench_input_receipt_unknown", "grant_close_timeout", "resume_send_", "bench_finish_", "peer_bench_")):
            a.run.update(bench_continuity=True, control_intent={"eligible": True,
                "device_id": enrolled["device_id"], "controller_generation": ids["controller_generation"],
                "service_incarnation": a.service_incarnation, "start_operation_id": a.run["operation_id"]})
            a._teleop_view = None
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
            initial_revision_allowed = [cause not in {"initial_ack_timely", "initial_ack_missing"}]
            if not initial_revision_allowed[0]:
                original_put = Peer.put
                def initial_revision_gate(peer, value, snapshot=False):
                    if value.get("kind") == "snapshot" and not initial_revision_allowed[0]:
                        value = {k: v for k, v in value.items() if k != "revision"}
                    return original_put(peer, value, snapshot)
                monkeypatch.setattr(Peer, "put", initial_revision_gate)
            if cause in {"transport_blackout_hb1", "resume_pong_pause"}:
                original_ws_connect = client.http.ws_connect
                def fixture_heartbeat(*args, **kwargs):
                    kwargs["heartbeat"] = 1  # Retained baseline; other cases exercise common production heartbeat=2.
                    return original_ws_connect(*args, **kwargs)
                monkeypatch.setattr(client.http, "ws_connect", fixture_heartbeat)
            await client.attach()
            if cause in {"peer_start_normal", "peer_start_bench"}:
                bench = cause == "peer_start_bench"
                selected = m._peer_options("left-peer-maintained", "left", -1, 52, bench=bench)
                assert selected == {"side": "left", "direction_raw": -1, "raw_step": 52}
                client.generation = ids["controller_generation"]
                idle = copy.deepcopy(e.evidence())
                e.evidence = lambda: copy.deepcopy(idle)
                begun = []
                e.begin = lambda recipe: begun.append(recipe) or True
                a.run = None
                start_body = {"operation_id": str(uuid4()), "recipe": "physical-live-teleop",
                              "controller_generation": client.generation, "bench_continuity": bench}
                receipt = await client.post("start", start_body)
                assert receipt["accepted"] is True and a.run["operation_id"] == start_body["operation_id"]
                assert a.run["bench_continuity"] is bench and a.run["status"] == "starting"
                assert a._controller_lease_seconds() == (6. if bench else 3.)
                assert bool(a.run.get("control_intent")) is bench
                assert [recipe.name for recipe in begun] == ["physical-live-teleop"]
                assert not any(op in {"grant", "input", "resume"} for op, _ in commands)
                return
            if cause == "ordinary_start_late_first_renewal":
                client.generation, client.connection = ids["controller_generation"], ids["connection_generation"]
                idle_evidence, pi_now, begun = copy.deepcopy(e.evidence()), [10.], []
                a.clock = e.clock = lambda: pi_now[0]
                e.evidence = lambda: copy.deepcopy(idle_evidence)
                e.begin = lambda recipe: begun.append(recipe) or True  # No device worker.
                a.run = None
                initial = await client.post("claim", {"controller_generation": client.generation})
                assert initial["lease_seconds"] == 1.5 and initial["presence_expires"] == 11.5
                generation = client.generation
                pi_now[0] = 10.802256399  # Actual84's admitted Start offset from its idle Claim.
                started = await client.post("start", {"operation_id": str(uuid4()),
                    "recipe": "physical-live-teleop", "controller_generation": generation})
                assert started["accepted"] is True and a.run["status"] == "starting"
                admitted_expiry = a.controller["expires"]
                pi_now[0] = 12.  # First renewal is processed beyond the old idle expiry.
                renewed = await client.post("claim", {"controller_generation": generation})
                assert renewed["accepted"] is True and renewed["controller_generation"] == generation
                assert admitted_expiry == 13.802256399 and renewed["presence_expires"] == 15.
                assert renewed["lease_seconds"] == 3. and len(begun) == 1
                assert a.run["native_live_at"] is None and a.run["deadline"] is None and a.run["progress_s"] == 0.
                assert a.run.get("control_intent") is None and a.run.get("bench_continuity") is False
                assert not any(op in {"connect", "resume", "grant", "input"} for op, _ in commands)
                assert not any(event["kind"] == "presence_expired" for event in a.events)
                return
            client.generation, client.connection = ids["controller_generation"], ids["connection_generation"]
            if cause in {"peer_bench_unqualified_view", "peer_bench_local_loss", "peer_normal_unqualified_view"}:
                bench = cause.startswith("peer_bench_")
                assert m._peer_options("left-peer-maintained", "left", -1, 52, bench=bench)
                a._teleop_view = None
                if cause == "peer_bench_local_loss":
                    original_evidence = e.evidence
                    def missing_local_camera():
                        return dict(original_evidence(), required_observation=False)
                    e.evidence = missing_local_camera
                client.snapshot = (await client.get("state"))["snapshot"]
                view = m._live(client.snapshot)["required_view"]
                assert view["qualified"] is False and view["required"] is (not bench)
                guard = m.SameRunRecovery(client, record)
                if cause == "peer_bench_unqualified_view":
                    await guard.resume()
                    assert a.run["status"] == "running" and e.desired == "active"
                    assert m._view_admitted(client.snapshot) is True
                    assert client.snapshot["evidence"]["required_observation"] is True
                else:
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(guard.resume(), .15)
                    assert e.desired != "active"
                    assert not any(op in {"grant", "input", "resume"} for op, _ in commands)
                assert a.run["native_live_at"] == 9. and a.run["deadline"] == 309.
                return
            if cause.startswith("bench_input_receipt_unknown"):
                guard = m.SameRunRecovery(client, record)
                original = copy.deepcopy(guard.original)
                await guard.resume()
                class Input:
                    kind = "virtual-leaders"
                    def get_action(self):
                        return {key.removeprefix("arm_"): 1. for key in KEYS}
                producer = m.FrameProducer(Input())
                producer.workspace = workspace()
                producer.native_cursor = dict(workspace()["reference"])
                original_call, accepted_inputs, applied_inputs = gateway.call, [], []
                async def delayed_input(command, device):
                    result = await original_call(command, device)
                    if command.get("op") == "input":
                        accepted_inputs.append(copy.deepcopy(command))
                        applied_inputs.append(copy.deepcopy(e._live_target))
                        if len(accepted_inputs) == 1:
                            assert result["accepted"] is True
                            a.clock = e.clock = lambda: 10.3
                            a.tick()  # Original 250ms input expiry causes a real Core hold.
                            if cause.endswith("pause"):
                                assert a.handle({"op": "pause", "run_id": a.run["run_id"],
                                    "operation_id": str(uuid4())}, enrolled["device_id"])["accepted"] is True
                            await asyncio.sleep(1.2)  # Receipt only; never refresh the accepted input.
                    return result
                gateway.call = delayed_input
                with pytest.raises(TimeoutError) as caught:
                    await m.send_leader_sample(client, producer, m.ZERO_BODY, record, phase="combined", progress_s=.01)
                assert len(accepted_inputs) == 1 and producer.sequence == 1
                assert caught.value.input_context["source"] == accepted_inputs[0]["source"]
                assert applied_inputs[0]["valid_until_monotonic_s"] <= 10.25
                assert a.run["status"] == "paused" and a.run["deadline"] == 309.
                assert any(row["event"] == "leader_frame_receipt_unknown" for row in records)
                await client.detach()
                await client.attach()
                assert guard.original == original and client.generation == ids["controller_generation"]
                if cause.endswith("pause"):
                    with pytest.raises(m.CommandRefusedError, match="same-operation control intent cancelled"):
                        await guard.resume()
                    assert len(accepted_inputs) == 1 and a.run["control_intent"]["eligible"] is False
                    return
                await client.post("claim", {"controller_generation": client.generation})
                await guard.resume()
                producer.native_cursor, producer.reacquire = dict(workspace()["reference"]), True
                sent = await m.send_leader_sample(client, producer, m.ZERO_BODY, record, phase="combined", progress_s=.01)
                assert sent is not None and sent[2]["accepted"] is True
                assert [row["source"]["sample_seq"] for row in accepted_inputs] == [1, 2]
                assert accepted_inputs[0]["grant_id"] != accepted_inputs[1]["grant_id"]
                assert applied_inputs[1]["valid_until_monotonic_s"] <= 10.55
                assert a.run["native_live_at"] == 9. and a.run["deadline"] == 309.
                assert guard.original == original
                return
            if cause.startswith("bench_reacquire"):
                guard = m.SameRunRecovery(client, record)
                original = copy.deepcopy(guard.original)
                a.controller["expires"] = 9.9
                a._expire_controls(10.)
                assert a.controller is None and a.run["status"] == "paused"
                if cause != "bench_reacquire":
                    original_post, claim_replays, accepted_expiry = client.post, [], []
                    async def lose_reclaim_receipt(path, body):
                        result = await original_post(path, body)
                        if path == "claim" and "reacquire" in body:
                            claim_replays.append(copy.deepcopy(body))
                            if len(claim_replays) == 1:
                                accepted_expiry.append(a.controller["expires"])
                                if cause.endswith("cancelled"):
                                    paused = a.handle({"op": "pause", "run_id": a.run["run_id"],
                                                       "operation_id": str(uuid4())}, enrolled["device_id"])
                                    assert paused["accepted"] is True
                                raise ConnectionError("fresh Claim accepted but receipt connection lost")
                        return result
                    client.post = lose_reclaim_receipt
                    if cause.endswith("cancelled"):
                        with pytest.raises(RuntimeError, match="superseded"):
                            await guard.reacquire()
                        assert client.generation == ids["controller_generation"]
                        assert guard.original == original and e._live_target is None
                        assert claim_replays[0] == claim_replays[1]
                        assert a.run["control_intent"]["eligible"] is False and a.run["status"] == "paused"
                        return
                await guard.reacquire()
                if cause.endswith("receipt_lost"):
                    assert len(claim_replays) == 2 and claim_replays[0] == claim_replays[1]
                    assert a.controller["expires"] == accepted_expiry[0]
                assert client.generation != ids["controller_generation"]
                assert client.connection is None
                assert guard.original["run"] == original["run"]
                await guard.resume()
                assert a.run["status"] == "running" and e.desired == "active"
                assert a.run["deadline"] == 309. and a.run["native_live_at"] == 9.
                class Input:
                    kind = "virtual-leaders"
                    def get_action(self):
                        return {key.removeprefix("arm_"): 1. for key in KEYS}
                producer = m.FrameProducer(Input())
                producer.workspace = workspace()
                producer.native_cursor = dict(workspace()["reference"])
                sent = await m.send_leader_sample(client, producer, m.ZERO_BODY, record, phase="combined", progress_s=0.)
                assert sent is not None and sent[2]["accepted"] is True
                assert e._live_target["source"] == sent[1]["source"]
                assert e._live_target["source"]["sample_seq"] == 1
                current_view = client.snapshot["run"]["live_input"]["required_view"]
                assert current_view["required"] is False and current_view["qualified"] is False
                assert any(row["event"] == "same_operation_reacquired" for row in records)
                return
            if cause.startswith("renew_receipt_cost"):
                # Only a processed HTTP renewal's reply is delayed. The real
                # normal teleop 3 s control lease advances independently of the
                # 250 ms movement lifetime; no product clock is altered here.
                loop = asyncio.get_running_loop()
                started = loop.time()
                a.clock = e.clock = lambda: 10. + loop.time() - started
                calls, second = [], loop.create_future()
                in_flight, expired = [False], cause.endswith("_expired")
                original_post = client.post

                async def late_renewal(path, body):
                    assert not in_flight[0], "only one current renewal may be in flight"
                    in_flight[0] = True
                    calls.append((loop.time(), copy.deepcopy(body)))
                    try:
                        result = await original_post(path, body)
                    except Exception as error:
                        in_flight[0] = False
                        if not second.done():
                            second.set_exception(error)
                        raise
                    if len(calls) == 1:
                        assert result["accepted"] is True
                        assert 2.9 < result["presence_expires"] - a.clock() <= 3.
                        await asyncio.sleep(3.2 if expired else 2.7)
                    elif not second.done():
                        second.set_result(result)
                    in_flight[0] = False
                    return result

                client.post = late_renewal
                renewal = asyncio.create_task(client.renew())
                try:
                    if expired:
                        with pytest.raises(m.HTTPOutcomeError, match="controller renewal fence"):
                            await asyncio.wait_for(second, 4.1)
                        assert a.controller is None or a.controller["expires"] <= a.clock()
                    else:
                        result = await asyncio.wait_for(second, 4.1)
                        assert result["accepted"] is True
                        assert calls[1][0] - calls[0][0] < 3.
                        assert a.controller["generation"] == ids["controller_generation"]
                        assert a.controller["expires"] > a.clock()
                        assert not any(v["kind"] == "presence_expired" for v in a.events)
                    assert len(calls) == 2 and not in_flight[0]
                    assert all(body == {"controller_generation": ids["controller_generation"]}
                               for _, body in calls)
                    assert a.run["deadline"] == 309. and a.run["native_live_at"] == 9.
                    assert a.run["status"] == "paused" and a.run["recovery_episodes"] == 0
                    assert commands == [("claim", "paused"), ("claim", "paused")]
                    assert e._live_target is None
                finally:
                    renewal.cancel()
                    await asyncio.gather(renewal, return_exceptions=True)
                return
            if cause.startswith("connect_receipt_"):
                # Hold only the correlated result after real Core processing.
                # Snapshot/ACK traffic continues on the actual TLS connection.
                mode = cause.removeprefix("connect_receipt_")
                operation = mode if mode in {"input", "grant"} else "connect"
                if operation != "connect":
                    initial = await client.command(dict(op="connect", **client.identities()))
                    assert initial["accepted"] is True
                    client.connection = initial["connection_generation"]
                    commands.clear()
                original_call, original_send = gateway.call, client.ws.send_json
                processed, envelopes = asyncio.Event(), []
                old_connection = a.connection["generation"]
                old_revision = a.run["intent_revision"]
                result_seen = []

                async def hold_result(command, device):
                    result = await original_call(command, device)
                    if command.get("op") == operation:
                        result_seen.append(copy.deepcopy(result))
                        processed.set()
                        if mode == "missing":
                            await unblock_release.wait()
                        else:
                            await asyncio.sleep(0 if mode == "fast" else 2.2 if mode == "late" else 1.2)
                    return result

                async def capture_envelope(value, **kwargs):
                    if "command" in value:
                        envelopes.append(copy.deepcopy(value))
                    return await original_send(value, **kwargs)

                gateway.call, client.ws.send_json = hold_result, capture_envelope
                loop = asyncio.get_running_loop()
                started = loop.time()
                pending = asyncio.create_task(client.command(dict(op=operation, **client.identities())))
                try:
                    await asyncio.wait_for(processed.wait(), .5)
                    assert len(envelopes) == len(result_seen) == 1
                    assert a.run["status"] == "paused"
                    if operation == "connect":
                        assert result_seen[0]["accepted"] is True
                        assert a.connection["generation"] != old_connection
                        assert a.run["intent_revision"] == old_revision + 1
                    else:
                        # Invalid while held: withholding a refusal must not
                        # lengthen ordinary input/grant result waits either.
                        assert result_seen[0]["accepted"] is False
                    if mode in {"fast", "delayed"}:
                        result = await pending
                        assert result == dict(result_seen[0], id=envelopes[0]["id"], kind="result")
                        if mode == "delayed":
                            assert 1.1 < loop.time() - started < 2
                    else:
                        with pytest.raises(TimeoutError):
                            if mode == "caller_ceiling":
                                await asyncio.wait_for(pending, .2)
                            else:
                                await pending
                        elapsed = loop.time() - started
                        if mode in {"input", "grant"}:
                            assert .9 < elapsed < 1.5
                        elif mode == "caller_ceiling":
                            assert elapsed < .75
                        else:
                            assert 1.8 < elapsed < 2.6
                        if mode == "late":
                            await asyncio.sleep(.3)
                    assert client.pending == {}
                    assert len(envelopes) == 1
                    assert commands == [(operation, "paused")]
                    assert a.run["native_live_at"] == 9. and a.run["deadline"] == 309.
                    assert a.run["recovery_episodes"] == 0 and a.run["first_cause"] is None
                    assert client.generation == ids["controller_generation"]
                    assert e._live_target is None
                finally:
                    unblock_release.set()
                    if not pending.done():
                        pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                return
            if cause in {"initial_ack_timely", "initial_ack_missing"}:
                # Initialized real TLS client, current snapshots but no revision
                # yet. Missing protocol evidence cannot count as local ACK credit.
                envelopes, ack_frames = [], []
                original_send, original_call = client.ws.send_json, gateway.call
                async def capture_initial(value, **kwargs):
                    if "command" in value:
                        envelopes.append(copy.deepcopy(value))
                    elif "ack" in value:
                        ack_frames.append(copy.deepcopy(value))
                    return await original_send(value, **kwargs)
                async def verify_initial(command, device):
                    if command.get("op") == "connect":
                        assert next(iter(gateway.peers)).ack is True
                    return await original_call(command, device)
                client.ws.send_json, gateway.call = capture_initial, verify_initial
                assert client._ack_ws is client.ws and client._ack_revision is None
                pending = asyncio.create_task(client.command(dict(op="connect", **client.identities())))
                await asyncio.sleep(.1)
                assert not pending.done() and envelopes == ack_frames == []
                assert client._acked_revision is None
                if cause == "initial_ack_timely":
                    initial_revision_allowed[0] = True
                    connected = await asyncio.wait_for(pending, .5)
                    assert connected["accepted"] is True and ack_frames
                    assert [v["command"]["op"] for v in envelopes] == ["connect"]
                    assert client._acked_revision == client._ack_revision is not None
                else:
                    with pytest.raises(TimeoutError) as missing:
                        await asyncio.wait_for(pending, 2.5)
                    assert client._ack_error is missing.value and client.transport_error is not None
                    assert envelopes == ack_frames == [] and client._acked_revision is None
                assert a.run["deadline"] == 309. and a.run["native_live_at"] == 9.
                assert not any(op in {"grant", "input", "resume"} for op, _ in commands)
                print("COMMON59_INITIAL_ACK", json.dumps({"mode": cause, "commands": len(envelopes),
                    "ack_frames": len(ack_frames), "credited": client._acked_revision is not None}))
                return
            if cause == "runner_initial_cursor_pending":
                # Synthetic56: native Live is real Core status, not a current
                # cursor/held/active qualification. Fake native sensing advances
                # independently once the runner reaches its producer boundary.
                initial = copy.deepcopy(client.snapshot)
                assert initial["status_projection"] == {"kind": "am1-essential-status", "version": 1}
                assert initial["run"]["native_live_at"] == 9. and initial["run"]["deadline"] == 309.
                assert all(key not in m._live(initial)
                           for key in ("cursor", "cursor_intent_revision", "cursor_feedback_sequence"))
                assert all(initial["evidence"][key] is False for key in
                           ("feedback", "native_ack", "pose_aligned", "hold_acknowledged", "active_acknowledged"))
                producer_created, observed = asyncio.Event(), []
                original_post, original_producer = client.post, m.FrameProducer
                async def native_ready():
                    await producer_created.wait()
                    cursor_ready[0] = True
                    a.tick()
                qualification = asyncio.create_task(native_ready())
                class RunnerSession(m.SessionClient):
                    def __new__(cls, *_args):
                        return client
                class Producer(original_producer):
                    def __init__(self, source):
                        super().__init__(source)
                        producer_created.set()
                async def post(path, body):
                    if path == "start":
                        # Existing admitted fake-native lane; Core/Gateway reads,
                        # connect/release/Resume and ACK qualification stay real.
                        a.run["operation_id"] = body["operation_id"]
                        client.snapshot = a.snapshot()
                        return {"accepted": True, "run_id": e.run_id, "operation_id": body["operation_id"]}
                    if path == "stop":
                        a.run.update(status="stopped", cleanup="physical_readback_verified", uncertain=False)
                        return {"accepted": True}
                    return await original_post(path, body)
                async def first_sample(_client, producer, _body, _record, **_kwargs):
                    assert cursor_ready[0] and qualification.done()
                    current = client.snapshot
                    assert current["evidence"]["active_acknowledged"] is True
                    assert m._live(current)["cursor_intent_revision"] == current["run"]["intent_revision"]
                    assert producer.native_cursor == m._live(current)["cursor"]
                    observed.append(copy.deepcopy(current))
                    raise RuntimeError("synthetic current active cursor boundary")
                async def already_attached():
                    assert client.ws is not None and not client.ws.closed
                client.attach, client.post = already_attached, post
                monkeypatch.setattr(m, "SessionClient", RunnerSession)
                monkeypatch.setattr(m, "FrameProducer", Producer)
                monkeypatch.setattr(m, "send_leader_sample", first_sample)
                args = type("Args", (), {"source": "virtual-leaders", "output": str(tmp_path / "run"),
                    "url": gateway.origin, "ca": cert, "storage_state": storage, "next_start_seconds": None,
                    "duration_seconds": 180, "interrupt_at_seconds": None})()
                with pytest.raises(RuntimeError, match="synthetic current active cursor boundary"):
                    await m.run(args)
                assert len(observed) == 1
                rows = [json.loads(v) for v in (tmp_path / "run/frames.jsonl").read_text().splitlines()]
                admitted = next(v["snapshot"] for v in rows if v["event"] == "native_admitted")
                assert "cursor" not in m._live(admitted)  # Preserve the genuine admission record.
                assert not any(op in {"grant", "input"} for op, _ in commands)
                assert sum(op == "connect" for op, _ in commands) == 1
                assert a.run["native_live_at"] == 9. and a.run["deadline"] == 309.
                return
            if cause == "acquisition_input_pause":
                # The manually initialized fake-native lane omits this public
                # field; actual retained59 explicitly carries null, not absence.
                a.run["native_first_cause"] = None
                # Restore authority_lane's original running state, then use
                # the existing Core hold transition for a prior own event.
                a.run["status"] = "running"
                a._pause_live_input("input_expired")
                client.snapshot = (await client.get("state"))["snapshot"]
            guard = m.SameRunRecovery(client, record)
            await guard.resume()
            initial_connects = sum(op == "connect" for op, _ in commands)

            class Input:
                kind = "virtual-leaders"

                def get_action(self):
                    return {k.removeprefix("arm_"): v for k, v in workspace()["reference"].items()}

            producer = m.FrameProducer(Input())
            producer.workspace, producer.native_cursor = workspace(), workspace()["reference"]
            if cause.startswith("ordinary_lease_"):
                pi_now = [10.]
                a.clock = e.clock = lambda: pi_now[0]
                renewed = await client.post("claim", {"controller_generation": client.generation})
                first = await m.send_leader_sample(client, producer, m.ZERO_BODY, record, phase="combined", progress_s=.01)
                assert first is not None and first[2]["accepted"] is True
                assert first[0]["expires"] - first[0]["issued"] == .25
                assert a.applied_input is not None and e._live_target is not None
                pi_now[0] = 10.249
                a._expire_controls(pi_now[0])
                assert a.run["status"] == "running" and e.desired == "active"
                pi_now[0] = 10.251
                a._expire_controls(pi_now[0])
                assert a.run["status"] == "paused" and e.desired == "hold"
                assert a.applied_input is None and e._live_target is None
                old = client.generation
                if cause == "ordinary_lease_expired":
                    pi_now[0] = 13.001
                    a._expire_controls(pi_now[0])
                    assert a.controller is a.connection is None
                    with pytest.raises(m.HTTPOutcomeError) as refused:
                        await client.post("claim", {"controller_generation": old})
                    assert refused.value.non_admitted and refused.value.response["reason"] == "controller renewal fence"
                    with pytest.raises(m.CommandRefusedError, match="stale control generation"):
                        await client.command(dict(op="grant", **client.identities()))
                    assert a.controller is None and e._live_target is None
                else:
                    pi_now[0] = 11.64  # Actual82's observed gap between renewal dispatches.
                    # Independent required viewing stays current during the PC sender scheduling gap.
                    a._teleop_view["qualified_until"] = pi_now[0] + .5
                    a._expire_controls(pi_now[0])
                    assert a.controller is not None and a.controller["generation"] == old
                    assert renewed["lease_seconds"] == 3. and renewed["presence_expires"] == 13.
                    assert a.run["status"] == "paused" and e._live_target is None
                    competitor = a.handle({"op": "claim"}, "competitor")
                    assert competitor["accepted"] is False and competitor["reason"] == "controller occupied"
                    current = await client.post("claim", {"controller_generation": old})
                    assert current["controller_generation"] == old and current["presence_expires"] == 14.64
                    client.snapshot = (await client.get("state"))["snapshot"]
                    assert not await guard.resume(expected_pause="input_expired")
                    assert a.run["status"] == "running" and e._live_target is None
                    producer.native_cursor, producer.reacquire = m._live(client.snapshot)["cursor"], True
                    fresh = await m.send_leader_sample(client, producer, m.ZERO_BODY, record, phase="combined", progress_s=.02)
                    assert fresh is not None and fresh[2]["accepted"] is True
                    assert fresh[0]["grant_id"] != first[0]["grant_id"]
                    assert fresh[1]["source"]["sample_seq"] > first[1]["source"]["sample_seq"]
                    assert fresh[0]["expires"] - fresh[0]["issued"] == .25
                    assert m._live(client.snapshot)["required_view"]["required"] is True
                    assert a.run.get("control_intent") is None and a.run.get("bench_continuity") is not True
                assert a.run["native_live_at"] == 9. and a.run["deadline"] == 309.
                return
            if cause.startswith("grant_close_timeout"):
                original_call, accepted_grants, applied = gateway.call, [], []
                old_ws, old_reader = client.ws, client.reader
                old_close = old_ws.close
                pending = None
                client.record = record
                close_entered, close_cancelled = asyncio.Event(), asyncio.Event()
                async def stalled_close(*args, **kwargs):
                    close_entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        close_cancelled.set()
                old_ws.close = stalled_close
                async def missing_grant_receipt(command, device):
                    result = await original_call(command, device)
                    if command.get("op") == "grant" and not accepted_grants:
                        accepted_grants.append(copy.deepcopy(result))
                        assert result["accepted"] is True
                        a._pause_live_input("input_expired")
                        await unblock_release.wait()
                    if command.get("op") == "input":
                        applied.append(copy.deepcopy(command))
                    return result
                gateway.call = missing_grant_receipt
                renewal = asyncio.create_task(client.renew())
                def healthy_renewal():
                    if renewal.done():
                        renewal.result()
                try:
                    with pytest.raises(TimeoutError) as caught:
                        await m.send_leader_sample(client, producer, m.ZERO_BODY, record, phase="combined", progress_s=.01)
                    assert producer.sequence == client.sequence == 0 and len(accepted_grants) == 1
                    pending = asyncio.get_running_loop().create_future()
                    client.pending["quarantined-test-request"] = pending
                    if cause.endswith("cancel"):
                        detaching = asyncio.create_task(client.detach())
                        await close_entered.wait()
                        detaching.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await detaching
                        assert close_cancelled.is_set() and old_reader.done()
                        assert client.ws is client.reader is client.snapshot is None
                        assert isinstance(pending.exception(), ConnectionError) and client.pending == {}
                        return
                    if cause.endswith("pause"):
                        assert a.handle({"op": "pause", "run_id": a.run["run_id"],
                            "operation_id": str(uuid4())}, enrolled["device_id"])["accepted"]
                    elif cause.endswith("takeover"):
                        a.controller["expires"] = 9.9
                        a._expire_controls(10.)
                        assert a.handle({"op": "claim"}, "competitor")["accepted"]
                    if cause == "grant_close_timeout":
                        assert not await guard.resume(grant_error=caught.value, renewal_check=healthy_renewal)
                        assert a.run["status"] == "running" and guard.episodes == 0
                        assert old_reader.done() and client.reader is not old_reader and client.ws is not old_ws
                        assert close_cancelled.is_set()
                        assert any(row["event"] == "session_detach_close_interrupted" for row in records)
                        assert isinstance(pending.exception(), ConnectionError) and client.pending == {}
                        producer.native_cursor, producer.reacquire = m._live(client.snapshot)["cursor"], True
                        fresh = await m.send_leader_sample(client, producer, m.ZERO_BODY, record, phase="combined", progress_s=.01)
                        assert fresh is not None and fresh[2]["accepted"] is True
                        assert producer.sequence == 1 and len(applied) == 1
                        assert fresh[0]["grant_id"] != accepted_grants[0]["grant_id"]
                        assert fresh[0]["expires"] - fresh[0]["issued"] <= .25
                    else:
                        with pytest.raises((RuntimeError, TimeoutError, m.CommandRefusedError)):
                            await guard.resume(grant_error=caught.value, renewal_check=healthy_renewal)
                        assert producer.sequence == 0 and applied == []
                        if pending.done():
                            pending.exception()
                    assert a.run["native_live_at"] == 9. and a.run["deadline"] == 309.
                    assert a.run["control_intent"]["eligible"] is (cause == "grant_close_timeout")
                finally:
                    old_ws.close = old_close
                    if pending is not None and pending.done() and not pending.cancelled():
                        pending.exception()
                    unblock_release.set()
                    renewal.cancel()
                    await asyncio.gather(renewal, return_exceptions=True)
                return
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
            if cause in {"grant_view_reconnect", "grant_view_switch", "connect_view_reconnect", "release_view_reconnect"}:
                # Actual86 / Synthetic48: actual Core/IPC/TLS/Gateway view reset, then an
                # independent viewer task publishes genuine advancing progress.
                from types import SimpleNamespace

                from tools import am1_session_service as service
                from tools.am1_onboard_media import OnboardMediaProxy

                pi_clock, old_time = [10. if cause in {"connect_view_reconnect", "release_view_reconnect"} else 10.01], service.time
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
                loss_op = "connect" if cause == "connect_view_reconnect" else "release_input" if cause == "release_view_reconnect" else "grant"
                if loss_op != "grant":
                    a._pause_live_input("input_expired")
                envelopes, observed, published = [], asyncio.Event(), asyncio.Event()
                original_record, original_attach, original_get = guard.record, client.attach, client.get

                async def observe_owner(path):
                    value = await original_get(path)
                    if path == "state" and lost[0] and not m._live(value["snapshot"])["required_view"]["qualified"]:
                        observed.set()
                    return value

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
                    if command.get("op") == loss_op and not lost[0]:
                        lost[0] = True
                        if loss_op == "grant":
                            a._pause_live_input("input_expired")
                        else:
                            assert (await original_call(command, device))["accepted"] is True
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
                    baseline = {"connect": int(loss_op != "grant"), "release_input": int(loss_op == "release_input")}
                    if loss_op == "grant":
                        assert not any(v["command"]["op"] in {"connect", "release_input", "resume", "input"} for v in envelopes)
                    else:
                        assert all(sum(v["command"]["op"] == op for v in envelopes) == count for op, count in baseline.items())
                        assert not any(v["command"]["op"] in {"resume", "input"} for v in envelopes)
                    assert producer.sequence == client.sequence == 0
                    role = "wrist_left" if cause == "grant_view_switch" else "chest"
                    pi_clock[0] += 1.125 if loss_op != "grant" else 1.05  # Existing source re-offer minimum remains enforced.
                    report["video"].update(role=role, capture_generation=generation[role])
                    report["cameras"][role].update(sequence=20, arrived_at_monotonic_s=pi_clock[0] - .02)
                    offered = await proxy.offer(role, b"v=0\r\n", client.device)
                    progress = {"role": role, "owner_generation": generation["owner"],
                                "view_generation": offered[2]["X-Camera-View-Generation"],
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
                    if loss_op == "grant":
                        assert not any(v["command"]["op"] in {"connect", "release_input", "resume", "input"} for v in envelopes)
                    else:
                        assert all(sum(v["command"]["op"] == op for v in envelopes) == count for op, count in baseline.items())
                        assert not any(v["command"]["op"] in {"resume", "input"} for v in envelopes)
                    pi_clock[0] += .03125  # Exact synthetic tick: genuine arrival after the offer's original-floor invalidation.
                    report["cameras"][role].update(sequence=21, arrived_at_monotonic_s=pi_clock[0] - .02)
                    receipt = await client.post("onboard/progress", dict(progress, decoded_frames=10, media_time=.8))
                    assert receipt["accepted"] is True and receipt["required_view"]["qualified"] is True
                    assert receipt["required_view"]["role"] == role
                    published.set()

                capture(client.ws)
                guard.record, client.attach, client.get, gateway.call = record_gap, attached, observe_owner, lose_grant
                renewal = asyncio.create_task(client.renew())
                qualification = asyncio.create_task(fresh_view())
                def view_renewal_healthy():
                    if renewal.done():
                        renewal.result()
                        raise RuntimeError("original renewal ended")
                try:
                    if loss_op == "grant":
                        with pytest.raises(ConnectionError) as caught:
                            await m.send_leader_sample(client, producer, m.ZERO_BODY, record, phase="individual", progress_s=0)
                        assert not await guard.resume(grant_error=caught.value, renewal_check=view_renewal_healthy)
                    else:
                        assert not await guard.resume(expected_pause="input_expired", renewal_check=view_renewal_healthy)
                    await qualification
                    assert published.is_set() and guard.episodes == 0
                    assert client.snapshot["evidence"]["active_acknowledged"] is True
                    assert m._live(client.snapshot)["required_view"]["role"] == ("wrist_left" if cause == "grant_view_switch" else "chest")
                    if loss_op == "grant":
                        assert sum(v["command"]["op"] == "connect" for v in envelopes) == 1
                        assert sum(v["command"]["op"] == "release_input" for v in envelopes) == 1
                    else:
                        assert sum(v["command"]["op"] == "connect" for v in envelopes) == 2
                        assert sum(v["command"]["op"] == "release_input" for v in envelopes) == (2 if loss_op == "release_input" else 1)
                    assert sum(v["command"]["op"] == "resume" for v in envelopes) == 1
                    assert not any(v["command"]["op"] in {"claim", "start"} for v in envelopes)
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
            if cause == "held_qualification_loss":
                # Synthetic55: known connect + positive empty release, but
                # the fresh held proof has NOT qualified and no Resume exists.
                from aiohttp import ClientConnectionResetError

                a._pause_live_input("input_expired")
                pending, lost, envelopes = [True], [False], []
                original_evidence, original_wait, original_attach = e.evidence, client.wait, client.attach
                phase_seen = asyncio.Event()
                def pending_hold():
                    value = original_evidence()
                    value["pose_aligned"] = not pending[0]
                    return value
                e.evidence = pending_hold
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
                async def lost_held_wait(predicate, timeout):
                    if not lost[0] and timeout != 5:
                        lost[0] = True
                        assert [v["command"]["op"] for v in envelopes] == ["connect", "release_input"]
                        assert predicate() is False
                        client.ws._exception = ClientConnectionResetError("Cannot write to closing transport")
                        client.ws._response.connection.transport.abort()
                        await client.reader
                    return await original_wait(predicate, timeout)
                original_record = guard.record
                def record_phase(value):
                    original_record(value)
                    if value["event"] == "held_qualification_transport_closed":
                        phase_seen.set()
                async def native_ready():
                    await phase_seen.wait()
                    await asyncio.sleep(.04)  # Independent native alignment convergence, never a GET side effect.
                    pending[0] = False
                capture(client.ws)
                client.attach, client.wait, guard.record = attached, lost_held_wait, record_phase
                renewal, qualification = asyncio.create_task(client.renew()), asyncio.create_task(native_ready())
                def healthy_renewal():
                    if renewal.done():
                        renewal.result()
                        raise RuntimeError("original renewal ended")
                try:
                    assert not await guard.resume(expected_pause="input_expired", renewal_check=healthy_renewal)
                    await qualification
                    ops = [v["command"]["op"] for v in envelopes]
                    assert ops == ["connect", "release_input", "connect", "release_input", "resume"]
                    assert client.snapshot["evidence"]["active_acknowledged"] is True
                    phase = [v for v in records if v["event"] == "held_qualification_transport_closed"]
                    assert len(phase) == 1 and phase[0]["operation_id"] is None
                    assert phase[0]["outcome"] == "resume_not_requested"
                    assert phase[0]["sent_flags"]["pose_aligned"] is False
                    assert not any(v["event"] == "resume_result_unknown" for v in records)
                    assert a.run["deadline"] == 309. and a.run["native_live_at"] == 9.
                    producer.native_cursor, producer.reacquire = m._live(client.snapshot)["cursor"], True
                    fresh = await m.send_leader_sample(client, producer, m.ZERO_BODY, record, phase="individual", progress_s=.02)
                    assert fresh[2]["accepted"] is True and producer.sequence == 1
                finally:
                    qualification.cancel()
                    renewal.cancel()
                    await asyncio.gather(qualification, renewal, return_exceptions=True)
                return
            if cause.startswith(("bench_finish_", "normal_finish_")):
                normal_finish = cause.startswith("normal_")
                cause = cause.replace("normal_finish_", "bench_finish_", 1)
                original_evidence, original_post, original_hold = e.evidence, client.post, e.hold
                pauses = []
                def held():
                    epoch[0] += 1  # Explicit fake Native acceptance of a NEW hold action epoch.
                    return original_hold()
                e.hold = held
                def final_evidence():
                    value = original_evidence()
                    value["measured_body"].update({"lift_axis.vel": -50, "theta.vel": .5859375,
                                                   "y.vel": .002556634646476069})
                    value["live_input"]["body"] = dict(m.ZERO_BODY)
                    value["accepted_host_action"]["accepted_action"] = (
                        {"mode": "raw_goal_hold", "body_velocity": 0.0} if e.desired != "active" else dict(m.ZERO_BODY))
                    if pauses:
                        if cause == "bench_finish_no_ack":
                            value["hold_acknowledged"] = False
                        elif cause == "bench_finish_stale_revision":
                            value["accepted_host_action"]["intent_revision"] -= 1
                        elif cause == "bench_finish_body_nonzero":
                            value["live_input"]["body"]["x.vel"] = .1
                        elif cause == "bench_finish_fault":
                            value["fault"] = "actual hard native fault"
                    return value
                e.evidence = final_evidence
                async def post(path, body):
                    if path != "pause":
                        return await original_post(path, body)
                    pauses.append(copy.deepcopy(body))
                    if cause in {"bench_finish_budget", "bench_finish_native_ceiling"}:
                        await asyncio.sleep(.3)
                    result = await original_post(path, body)
                    if cause == "bench_finish_pause_unknown":
                        raise TimeoutError("own Pause accepted; receipt unavailable")
                    if cause == "bench_finish_cancel":
                        raise asyncio.CancelledError("cancelled before own Pause qualification")
                    if cause == "bench_finish_external_pause":
                        assert a.handle({"op": "pause", "run_id": a.run["run_id"],
                                         "operation_id": str(uuid4())}, enrolled["device_id"])["accepted"]
                    elif cause == "bench_finish_stop":
                        assert a.handle({"op": "stop", "run_id": a.run["run_id"],
                                         "operation_id": str(uuid4())}, enrolled["device_id"])["accepted"]
                    elif cause == "bench_finish_takeover":
                        a.controller["expires"] = 9.9
                        a._expire_controls(10.)
                        assert a.handle({"op": "claim"}, "competitor")["accepted"]
                    elif cause == "bench_finish_reboot":
                        a.service_incarnation = str(uuid4())
                    elif cause == "bench_finish_view_after":
                        a._teleop_view["qualified_until"] = 9.9
                    return result
                client.post = post
                if cause == "bench_finish_view_before":
                    a._teleop_view["qualified_until"] = 9.9
                if cause == "bench_finish_strict":
                    a.run["bench_continuity"] = False
                if cause == "bench_finish_native_ceiling":
                    a.clock = e.clock = lambda: 308.9
                    a.controller["expires"] = 314.9
                    if normal_finish:
                        a._teleop_view["qualified_until"] = 309.4
                renewal = asyncio.create_task(client.renew())
                def healthy_renewal():
                    if renewal.done():
                        renewal.result()
                        raise RuntimeError("original renewal ended")
                entered = m.time.monotonic()
                try:
                    if cause == "bench_finish_hold":
                        proof = await guard.finish_owned(bench=not normal_finish, wall_deadline=entered + 12, renewal_check=healthy_renewal)
                        assert proof["native_held_qualified"] is True and proof["zero_body_command"] is True
                        assert proof["measured_body_exact_zero"] is False
                        assert proof["pause_operation_id"] == pauses[0]["operation_id"]
                        assert a.run["status"] == "paused" and e.desired == "hold"
                        assert proof["snapshot"]["evidence"]["accepted_host_action"]["intent_revision"] == a.run["intent_revision"]
                        if normal_finish:
                            assert a.run.get("control_intent") is None
                            assert a._controller_lease_seconds() == 3.
                            assert proof["snapshot"]["run"]["live_input"]["required_view"]["required"] is True
                            assert proof["snapshot"]["run"]["live_input"]["required_view"]["qualified"] is True
                            assert len([row for row in records if row["event"] == "normal_finish_hold_qualified"]) == 1
                        else:
                            assert len([row for row in records if row["event"] == "bench_finish_hold_qualified"]) == 1
                    else:
                        expected = (asyncio.CancelledError if cause.endswith("cancel") else
                                    TimeoutError if cause in {"bench_finish_no_ack", "bench_finish_stale_revision",
                                        "bench_finish_budget", "bench_finish_native_ceiling", "bench_finish_pause_unknown", "bench_finish_view_after"} else RuntimeError)
                        with pytest.raises(expected):
                            await guard.finish_owned(bench=not normal_finish, wall_deadline=entered + (.1 if cause in {
                                "bench_finish_no_ack", "bench_finish_stale_revision", "bench_finish_budget", "bench_finish_view_after"} else (2 if normal_finish else 12)),
                                                     renewal_check=healthy_renewal)
                        assert not any(row["event"] == "bench_finish_hold_qualified" for row in records)
                    if normal_finish:
                        if cause != "bench_finish_hold":
                            assert not any(row["event"] == "normal_finish_hold_qualified" for row in records)
                        assert len(pauses) == (0 if cause == "bench_finish_view_before" else 1)
                    else:
                        assert len(pauses) == (0 if cause == "bench_finish_strict" else 1)
                    assert not any(op in {"grant", "input"} for op, _ in commands)
                    assert sum(op == "connect" for op, _ in commands) == 1
                    assert sum(op == "resume" for op, _ in commands) == 1
                    assert a.run["deadline"] == 309. and a.run["native_live_at"] == 9.
                    if cause in {"bench_finish_budget", "bench_finish_native_ceiling"}:
                        assert m.time.monotonic() - entered < .25
                finally:
                    renewal.cancel()
                    await asyncio.gather(renewal, return_exceptions=True)
                return
            if cause.startswith("resume_send_"):
                from aiohttp import ClientConnectionResetError

                a._pause_live_input("input_expired")
                pi_now, failed, failures, envelopes, outcomes = [10.], [False], [], [], []
                a.clock = e.clock = lambda: pi_now[0]
                original_call, original_attach = gateway.call, client.attach
                admitted = asyncio.Event()

                async def called(command, device):
                    result = await original_call(command, device)
                    if command.get("op") == "resume":
                        outcomes.append(copy.deepcopy(result))
                        admitted.set()
                    return result

                def capture(ws):
                    send = ws.send_json
                    async def sent(value, **kwargs):
                        command = value.get("command", {})
                        if command:
                            envelopes.append(copy.deepcopy(value))
                        if command.get("op") == "resume" and not failed[0]:
                            failed[0] = True
                            if cause == "resume_send_cancel":
                                raise asyncio.CancelledError("cancelled during Resume send")
                            if cause == "resume_send_post":
                                await send(value, **kwargs)
                                await admitted.wait()  # Actual Core admission before the synthetic send/drain failure.
                                assert outcomes[-1]["accepted"] is True
                                pi_now[0] += .125
                                a._pause_live_input("native_input_expired")
                            elif cause == "resume_send_pause":
                                assert a.handle({"op": "pause", "run_id": a.run["run_id"],
                                    "operation_id": str(uuid4())}, enrolled["device_id"])["accepted"]
                            elif cause == "resume_send_takeover":
                                a.controller["expires"] = 9.9
                                a._expire_controls(10.)
                                assert a.handle({"op": "claim"}, "competitor")["accepted"]
                            elif cause == "resume_send_stop":
                                assert a.handle({"op": "stop", "run_id": a.run["run_id"],
                                    "operation_id": str(uuid4())}, enrolled["device_id"])["accepted"]
                            elif cause == "resume_send_reboot":
                                a.service_incarnation = str(uuid4())
                            ws._response.connection.transport.abort()
                            try:
                                if cause == "resume_send_post":
                                    raise ClientConnectionResetError("Cannot write to closing transport")
                                return await send(value, **kwargs)  # Installed aiohttp raises BEFORE writing.
                            except ClientConnectionResetError as error:
                                failures.append(error)
                                raise
                        return await send(value, **kwargs)
                    ws.send_json = sent

                async def attached():
                    if cause == "resume_send_budget":
                        await asyncio.sleep(.3)
                    await original_attach()
                    capture(client.ws)

                capture(client.ws)
                client.attach, gateway.call = attached, called
                renewal = asyncio.create_task(client.renew())
                def healthy_renewal():
                    if renewal.done():
                        renewal.result()
                        raise RuntimeError("original renewal ended")
                try:
                    if cause in {"resume_send_pre", "resume_send_post"}:
                        assert not await guard.resume(expected_pause="input_expired", renewal_check=healthy_renewal)
                        assert a.run["status"] == "running" and client.snapshot["evidence"]["active_acknowledged"] is True
                        resumes = [v for v in envelopes if v["command"]["op"] == "resume"]
                        assert len(resumes) == 2
                        assert resumes[0]["command"]["operation_id"] != resumes[1]["command"]["operation_id"]
                        assert resumes[0]["command"]["connection_generation"] != resumes[1]["command"]["connection_generation"]
                        assert len(outcomes) == (2 if cause == "resume_send_post" else 1)
                        ambiguous = [row for row in records if row["event"] == "resume_result_unknown"]
                        assert len(ambiguous) == 1 and ambiguous[0]["outcome"] == "unknown"
                        assert ambiguous[0]["send_failure"]["operation_id"] == resumes[0]["command"]["operation_id"]
                        assert ambiguous[0]["send_failure"]["request_id"] == resumes[0]["id"]
                        producer.native_cursor, producer.reacquire = m._live(client.snapshot)["cursor"], True
                        fresh = await m.send_leader_sample(client, producer, m.ZERO_BODY, record, phase="combined", progress_s=.01)
                        assert fresh is not None and fresh[2]["accepted"] is True and producer.sequence == 1
                        assert fresh[0]["expires"] - fresh[0]["issued"] <= .25
                    elif cause == "resume_send_cancel":
                        with pytest.raises(asyncio.CancelledError):
                            await guard.resume(expected_pause="input_expired", renewal_check=healthy_renewal)
                        assert not any(row["event"] == "resume_result_unknown" for row in records)
                    else:
                        with pytest.raises(ClientConnectionResetError) as caught:
                            await guard.resume(expected_pause="input_expired", renewal_check=healthy_renewal,
                                               wall_deadline=m.time.monotonic() + .15 if cause.endswith("budget") else None)
                        assert caught.value is failures[0]
                        assert len([v for v in envelopes if v["command"]["op"] == "resume"]) == 1
                        assert producer.sequence == 0
                        if cause.endswith("budget"):
                            assert isinstance(caught.value.__cause__, TimeoutError)
                    assert a.run["deadline"] == 309. and a.run["native_live_at"] == 9.
                    assert guard.completed_episodes == 0 and client.pending == {}
                    assert not any(v["command"]["op"] in {"claim", "start"} for v in envelopes)
                finally:
                    renewal.cancel()
                    await asyncio.gather(renewal, return_exceptions=True)
                return
            if cause in {"resume_result_loss_pre", "resume_result_loss_post", "resume_result_loss_native", "resume_result_loss_pending",
                         "resume_reply_timeout_pre", "resume_reply_timeout_post", "resume_reply_timeout_alignment", "resume_reply_timeout_alignment_persistent"}:
                reply_timeout = cause in {"resume_reply_timeout_pre", "resume_reply_timeout_post", "resume_reply_timeout_alignment", "resume_reply_timeout_alignment_persistent"}
                post_admitted = cause in {"resume_result_loss_post", "resume_reply_timeout_post"}
                # Synthetic54/57: the actual Core/Gateway receives a Resume whose
                # result is lost; no negative-admission receipt is invented.
                from aiohttp import ClientConnectionResetError

                pi_clock, lost, thermal = [10.], [False], [None]
                pose_ready, pose_observed = [True], asyncio.Event()
                a.clock = e.clock = lambda: pi_clock[0]
                native_evidence = e.evidence
                def pending_thermal():
                    value = native_evidence()
                    if cause.startswith("resume_reply_timeout_alignment"):
                        value["pose_aligned"] = pose_ready[0]
                    if thermal[0] is not None:
                        proof = copy.deepcopy(thermal[0])
                        if proof["peaks_ready"] and e.desired == "active":
                            proof.update(deficit_started_at_monotonic_s=None, deficit_ceiling_monotonic_s=None,
                                         hold_sample_at_monotonic_s=None, zero_read_started_at_monotonic_s=None,
                                         zero_read_completed_at_monotonic_s=None)
                        value["thermal_history"] = proof
                        value["accepted_host_action"]["thermal_history"] = copy.deepcopy(proof)
                    return value
                e.evidence = pending_thermal
                a._pause_live_input("input_expired")
                original_call, original_attach = gateway.call, client.attach
                envelopes, old_results = [], []

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

                async def lose_resume(command, device):
                    if command.get("op") == "resume" and not lost[0]:
                        lost[0] = True
                        old = copy.deepcopy(command)
                        if cause.startswith("resume_reply_timeout_alignment"):
                            pose_ready[0] = False  # Actual87: current HOLD is not yet stably aligned.
                        if post_admitted:
                            old_results.append(await original_call(old, device))
                            assert old_results[-1]["accepted"] is True
                            pi_clock[0] += .125
                            a._pause_live_input("native_input_expired")
                        elif cause in {"resume_result_loss_native", "resume_result_loss_pending"}:
                            pi_clock[0] += .125
                            healthy[0] = False
                            if thermal[0] is None:
                                thermal[0] = {"version": 1, "raw_sample_at_monotonic_s": pi_clock[0], "peaks_ready": False,
                                              "deficit_started_at_monotonic_s": pi_clock[0],
                                              "deficit_ceiling_monotonic_s": pi_clock[0] + 10,
                                              "hold_sample_at_monotonic_s": None, "zero_read_started_at_monotonic_s": None,
                                              "zero_read_completed_at_monotonic_s": None}
                            else:
                                thermal[0].update(peaks_ready=False, raw_sample_at_monotonic_s=pi_clock[0])
                            a.tick()  # Real source-retained new episode.
                            original_episode = copy.deepcopy(a.run["recovery"])
                            pi_clock[0] += .125
                            healthy[0] = True
                            thermal[0].update(peaks_ready=True, raw_sample_at_monotonic_s=pi_clock[0],
                                              hold_sample_at_monotonic_s=pi_clock[0], zero_read_started_at_monotonic_s=pi_clock[0],
                                              zero_read_completed_at_monotonic_s=pi_clock[0])
                            a.tick()
                            assert a.run["status"] == "paused" and a.run["recovery"] is None
                            assert original_episode["started"] + 10 == original_episode["ceiling"]
                        a._teleop_view["qualified_until"] = pi_clock[0] + .5
                        async def finish_old():
                            await unblock_release.wait()
                            if not post_admitted:
                                old_results.append(await original_call(old, device))
                            return old_results[-1]
                        task = asyncio.create_task(finish_old())
                        delayed.append(task)
                        if not reply_timeout:
                            # Existing54 taxonomy. New57 keeps the actual channel
                            # open until the original command future times out.
                            client.ws._exception = ClientConnectionResetError("Cannot write to closing transport")
                            client.ws._response.connection.transport.abort()
                        return await asyncio.shield(task)
                    return await original_call(command, device)

                async def checked_loss(command, device):
                    try:
                        return await lose_resume(command, device)
                    except (ValueError, TypeError, KeyError) as error:
                        raise AssertionError("new Resume-loss fixture failed: " + repr(error)) from error

                capture(client.ws)
                client.attach, gateway.call = attached, checked_loss
                if cause.startswith("resume_reply_timeout_alignment"):
                    original_record = guard.record
                    def record_alignment(value):
                        original_record(value)
                        if value["event"] == "owner_rejoin_state" and value["snapshot"]["evidence"]["pose_aligned"] is False:
                            pose_observed.set()
                    async def qualify_alignment():
                        await pose_observed.wait()
                        await asyncio.sleep(.05)
                        assert sum(v["command"]["op"] == "connect" for v in envelopes) == 1
                        assert sum(v["command"]["op"] == "release_input" for v in envelopes) == 1
                        assert sum(v["command"]["op"] == "resume" for v in envelopes) == 1
                        assert not any(v["command"]["op"] in {"grant", "input"} for v in envelopes)
                        if cause.endswith("persistent"):
                            return  # No independent Native qualification arrives inside the caller's fixed budget.
                        pi_clock[0] += .25  # Independent Native stable-HOLD qualification, over0.2 s.
                        epoch[0] += 1
                        pose_ready[0] = True
                        a._teleop_view["qualified_until"] = pi_clock[0] + .5
                    guard.record = record_alignment
                    qualification = asyncio.create_task(qualify_alignment())
                renewal = asyncio.create_task(client.renew())
                def healthy_renewal():
                    if renewal.done():
                        renewal.result()
                        raise RuntimeError("original renewal ended")
                if cause == "resume_result_loss_pending":
                    pi_clock[0] += .125
                    healthy[0] = False
                    thermal[0] = {"version": 1, "raw_sample_at_monotonic_s": pi_clock[0], "peaks_ready": False,
                                  "deficit_started_at_monotonic_s": pi_clock[0], "deficit_ceiling_monotonic_s": pi_clock[0] + 10,
                                  "hold_sample_at_monotonic_s": None, "zero_read_started_at_monotonic_s": None,
                                  "zero_read_completed_at_monotonic_s": None}
                    a.tick()
                    original_pending = copy.deepcopy(a.run["recovery"])
                    async def ready_original():
                        await entered.wait()  # Independently supply actual native qualification.
                        pi_clock[0] += .125
                        healthy[0] = True
                        thermal[0].update(peaks_ready=True, raw_sample_at_monotonic_s=pi_clock[0],
                                          hold_sample_at_monotonic_s=pi_clock[0], zero_read_started_at_monotonic_s=pi_clock[0],
                                          zero_read_completed_at_monotonic_s=pi_clock[0])
                        a._teleop_view["qualified_until"] = pi_clock[0] + .5
                        a.tick()
                    qualification = asyncio.create_task(ready_original())
                try:
                    if cause == "resume_reply_timeout_alignment_persistent":
                        ceiling = m.time.monotonic() + 1.5
                        with pytest.raises(TimeoutError):
                            await guard.resume(expected_pause="input_expired", renewal_check=healthy_renewal,
                                               wall_deadline=ceiling)
                        await qualification
                        assert pose_observed.is_set() and pose_ready[0] is False
                        assert m.time.monotonic() < ceiling + .2
                        assert sum(v["command"]["op"] == "connect" for v in envelopes) == 1
                        assert sum(v["command"]["op"] == "release_input" for v in envelopes) == 1
                        assert sum(v["command"]["op"] == "resume" for v in envelopes) == 1
                        assert not any(v["command"]["op"] in {"grant", "input", "claim", "start"} for v in envelopes)
                        assert a.run["status"] == "paused" and a.run["deadline"] == 309. and a.run["native_live_at"] == 9.
                        assert guard.completed_episodes == 0
                        return
                    try:
                        result = await guard.resume(expected_pause="input_expired", renewal_check=healthy_renewal)
                    except ConnectionError:
                        assert client.transport_close["reason"] == "stream-ended"
                        assert client.transport_close["close_code"] == 1006
                        assert client.transport_close["reader_error"] is None
                        assert client.transport_close["exception_type"] == "ClientConnectionResetError"
                        assert client.transport_close["exception"] == "Cannot write to closing transport"
                        raise
                    assert result is (cause in {"resume_result_loss_native", "resume_result_loss_pending"})
                    if cause.startswith("resume_reply_timeout_alignment"):
                        await qualification
                        assert pose_observed.is_set() and client.snapshot["evidence"]["pose_aligned"] is True
                        assert client.snapshot["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"] >= 10.25
                    assert client.snapshot["evidence"]["active_acknowledged"] is True
                    resumes = [v for v in envelopes if v["command"]["op"] == "resume"]
                    assert len(resumes) == 2
                    assert resumes[0]["id"] != resumes[1]["id"]
                    assert resumes[0]["command"]["operation_id"] != resumes[1]["command"]["operation_id"]
                    assert resumes[0]["command"]["connection_generation"] != resumes[1]["command"]["connection_generation"]
                    assert sum(v["command"]["op"] == "connect" for v in envelopes) == 2
                    assert sum(v["command"]["op"] == "release_input" for v in envelopes) == 2
                    assert not any(v["command"]["op"] in {"claim", "start", "grant", "input"} for v in envelopes)
                    assert guard.completed_episodes == int(cause in {"resume_result_loss_native", "resume_result_loss_pending"})
                    if cause == "resume_result_loss_pending":
                        qualified = [v for v in records if v["event"] == "qualified_native_recovery"]
                        assert len(qualified) == 1 and qualified[0]["original_recovery"] == original_pending
                        assert a.run["recovery_episodes"] == 1
                    unblock_release.set()
                    await asyncio.wait_for(asyncio.gather(*delayed), .5)
                    if not post_admitted:
                        assert old_results == [{"accepted": False, "status": "refused", "reason": "stale control generation", "effect_admitted": False}]
                    assert a.run["status"] == "running" and a.run["deadline"] == 309. and a.run["native_live_at"] == 9.
                    ambiguous = [v for v in records if v["event"] == "resume_result_unknown"]
                    assert len(ambiguous) == 1 and ambiguous[0]["operation_id"] == resumes[0]["command"]["operation_id"]
                    assert ambiguous[0]["outcome"] == "unknown"
                    if reply_timeout:
                        timeout = ambiguous[0]["reply_timeout"]
                        assert timeout["operation_id"] == resumes[0]["command"]["operation_id"]
                        assert timeout["request_id"] == resumes[0]["id"]
                        assert timeout["timing"]["response_timeout_observed_at_monotonic_s"] >= timeout["timing"]["send_completed_at_monotonic_s"]
                        assert ambiguous[0]["transport_close"] == {}
                    producer.native_cursor, producer.reacquire = m._live(client.snapshot)["cursor"], True
                    fresh = await m.send_leader_sample(client, producer, m.ZERO_BODY, record, phase="individual", progress_s=.02)
                    assert fresh[2]["accepted"] is True and producer.sequence == 1
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
            if cause in {"receipt_ack_starvation", "ack_never_returns", "ack_close_cancel"}:
                # Controlled common-path contrast: an input was already accepted
                # before a legitimate revision/resync ACK stalls its reader.
                from aiohttp import WSMsgType
                loop = asyncio.get_running_loop()
                timeline, envelopes, incoming = [], [], []
                accepted, release_result = asyncio.Event(), asyncio.Event()
                ack_entered, release_ack = asyncio.Event(), asyncio.Event()
                original_call, send_json, receive = gateway.call, client.ws.send_json, client.ws.receive
                peer = next(iter(gateway.peers))
                put, send_str = peer.put, peer.ws.send_str
                protocol = client.ws._response.connection.protocol
                data_received = protocol._data_received_cb
                old_revision, input_id = peer.revision, [None]
                held_once = [False]
                def stamp(event, **detail):
                    timeline.append(dict(event=event, pc_monotonic_s=loop.time(), pi_monotonic_s=a.clock(), **detail))
                async def traced_call(command, device):
                    result = await original_call(command, device)
                    if command.get("op") == "input" and not accepted.is_set():
                        assert result.get("accepted") is True and result.get("effect_admitted") is True
                        stamp("native_input_accepted", sample_seq=command["source"]["sample_seq"])
                        accepted.set()
                        await release_result.wait()
                    return result
                async def traced_send(value, **kwargs):
                    if "command" in value:
                        envelopes.append(copy.deepcopy(value))
                        if value["command"].get("op") == "input":
                            input_id[0] = value["id"]
                    result = await send_json(value, **kwargs)
                    if "ack" in value and not held_once[0]:
                        held_once[0] = True
                        stamp("ack_written", revision=value["ack"])
                        ack_entered.set()
                        await release_ack.wait()
                        stamp("ack_send_return_released")
                    return result
                def traced_put(value, snapshot=False):
                    if value.get("kind") == "result" and value.get("id") == input_id[0]:
                        stamp("gateway_result_published", request_id=value["id"])
                    return put(value, snapshot)
                async def traced_send_str(value, **kwargs):
                    parsed = json.loads(value)
                    if parsed.get("kind") == "result" and parsed.get("id") == input_id[0]:
                        stamp("gateway_result_send_started", request_id=parsed["id"])
                        result = await send_str(value, **kwargs)
                        stamp("gateway_result_send_returned", request_id=parsed["id"])
                        return result
                    return await send_str(value, **kwargs)
                async def traced_receive(*args, **kwargs):
                    message = await receive(*args, **kwargs)
                    if message.type == WSMsgType.TEXT:
                        value = json.loads(message.data)
                        if value.get("kind") == "result" and value.get("id") == input_id[0]:
                            stamp("client_result_read", request_id=value["id"], pending=value["id"] in client.pending)
                    return message
                def traced_data_received():
                    incoming.append(loop.time())
                    data_received()  # Keep aiohttp3.14's actual transport heartbeat reset.
                gateway.call, client.ws.send_json, client.ws.receive = traced_call, traced_send, traced_receive
                peer.put, peer.ws.send_str, protocol._data_received_cb = traced_put, traced_send_str, traced_data_received
                pending = asyncio.create_task(m.send_leader_sample(
                    client, producer, m.ZERO_BODY, record, phase="individual", progress_s=.04))
                try:
                    await asyncio.wait_for(accepted.wait(), .5)
                    peer.revision, peer.ack = gateway.next_revision(), False
                    peer.put({"kind": "snapshot", "revision": peer.revision, "snapshot": await gateway.current()}, True)
                    await asyncio.wait_for(ack_entered.wait(), .5)
                    while not peer.ack:
                        await asyncio.sleep(.005)
                    stamp("server_ack_observed", revision=peer.revision)
                    assert peer.revision != old_revision
                    release_result.set()
                    result = await asyncio.wait_for(asyncio.shield(pending), .5)
                    stamp("client_input_result_observed")
                    assert result[2]["accepted"] is True and not release_ack.is_set()
                    assert client.pending == {} and not client.reader.done() and not client.ws.closed
                    assert client.transport_error is None and client.transport_close is None
                    assert a.run["status"] == "running"
                    assert [v["command"]["op"] for v in envelopes] == ["grant", "input"]
                    # A pending result is observable, but new control waits for
                    # this same stream/revision's ACK transmission to finish.
                    blocked = asyncio.create_task(client.command(dict(op="grant", **client.identities())))
                    await asyncio.sleep(.1)
                    assert not blocked.done()
                    assert [v["command"]["op"] for v in envelopes] == ["grant", "input"]
                    if cause == "ack_never_returns":
                        with pytest.raises(TimeoutError) as ack_error:
                            await asyncio.wait_for(blocked, 2.5)
                        assert client._ack_error is ack_error.value and client.transport_error is not None
                        assert client._ack_task.done() and len(incoming) >= 5
                        assert [v["command"]["op"] for v in envelopes] == ["grant", "input"]
                        print("COMMON59_ACK_NEVER", json.dumps({"timeline": timeline, "transport_chunks": len(incoming),
                            "error_type": type(ack_error.value).__name__, "bounded_ack_s": 2, "no_new_control": True}))
                        return
                    if cause == "ack_close_cancel":
                        old_task, old_ws = client._ack_task, client.ws
                        # The native fake clock is otherwise fixed at10 in this
                        # receipt contrast; exercise actual original input expiry
                        # before asking the new stream to qualify paused control.
                        a.clock = e.clock = lambda: 10.3
                        a._teleop_view["qualified_until"] = 10.8
                        a.tick()
                        assert a.run["status"] == "paused" and a.events[-1]["kind"] == "input_expired"
                        await client.detach()
                        with pytest.raises(ConnectionError):
                            await blocked
                        assert old_task.done() and client._ack_ws is None and client._acked_revision is None
                        await client.attach()
                        assert client.ws is not old_ws
                        assert not await guard.resume()  # Fresh WSS ACK alone cannot restore Core control.
                        assert client.snapshot["evidence"]["active_acknowledged"] is True
                        fresh_grant = await client.command(dict(op="grant", **client.identities()))
                        assert fresh_grant["accepted"] is True and client._ack_ws is client.ws
                        print("COMMON59_ACK_CLOSE", json.dumps({"cancelled_and_joined": old_task.done(),
                            "old_stream_ack_credited": False, "current_stream_qualified": True}))
                        return
                    release_ack.set()
                    fresh_grant = await asyncio.wait_for(blocked, .5)
                    assert fresh_grant["accepted"] is True
                    assert fresh_grant["grant_id"] != first[0]["grant_id"]
                finally:
                    release_result.set()
                    release_ack.set()
                    await asyncio.gather(pending, return_exceptions=True)
                observed = next(v for v in timeline if v["event"] == "client_result_read")
                assert observed["pending"] is True
                order = [v["event"] for v in timeline]
                assert order.index("native_input_accepted") < order.index("server_ack_observed")
                assert order.index("gateway_result_send_returned") < order.index("client_input_result_observed")
                assert order.index("client_input_result_observed") < order.index("ack_send_return_released")
                assert sum(op == "input" for op, _ in commands) == 2
                assert a.run["deadline"] == 309. and a.run["native_live_at"] == 9.
                print("COMMON59_ACK", json.dumps({"timeline": timeline, "transport_chunks": len(incoming),
                    "outcome": "accepted input receipt observed during ACK send wait; new control barred until current ACK completes"}))
                return
            if cause in {"transport_blackout_hb1", "transport_blackout_hb2", "transport_blackout_long"}:
                # Real loopback transport receive blackout, not merely an app
                # receive pause:3.14 resets heartbeat from transport chunks.
                loop, timeline = asyncio.get_running_loop(), []
                started, pi_now = loop.time(), [10.]
                a.clock = e.clock = lambda: pi_now[0]
                original_reference = copy.deepcopy(m._live(client.snapshot)["reference"])
                old_identity = client.identities()
                old_frame = copy.deepcopy(first[1])
                old_grant = first[0]["grant_id"]
                transport = client.ws._response.connection.transport
                async def native_ticks():
                    expired_seen = False
                    while True:
                        pi_now[0] = 10. + loop.time() - started
                        a._teleop_view["qualified_until"] = pi_now[0] + .5
                        a.tick()
                        if not expired_seen and a.run["status"] == "paused":
                            expired_seen = True
                            timeline.append({"event": "native_paused", "pc_monotonic_s": loop.time(), "pi_monotonic_s": pi_now[0],
                                "cause": a.events[-1]["kind"], "feedback": e.evidence()["feedback"]})
                        await asyncio.sleep(.01)
                ticking = asyncio.create_task(native_ticks())
                renewal = asyncio.create_task(client.renew())
                def healthy_renewal():
                    assert not renewal.done()
                try:
                    await asyncio.sleep(.02)
                    transport.pause_reading()
                    timeline.append({"event": "receive_blackout_started", "pc_monotonic_s": loop.time(), "pi_monotonic_s": pi_now[0]})
                    await asyncio.sleep(3.2 if cause == "transport_blackout_long" else 1.6)
                    if not transport.is_closing():
                        transport.resume_reading()
                    timeline.append({"event": "receive_blackout_ended", "pc_monotonic_s": loop.time(), "pi_monotonic_s": pi_now[0]})
                    assert a.run["status"] == "paused" and a.run["recovery_episodes"] == 0
                    assert a.run["first_cause"] is None and healthy[0]
                    paused = next(v for v in timeline if v["event"] == "native_paused")
                    assert paused["cause"] == "input_expired" and paused["feedback"] is True
                    assert 10.25 <= paused["pi_monotonic_s"] < 10.35
                    if cause in {"transport_blackout_hb1", "transport_blackout_long"}:
                        await asyncio.wait_for(client.reader, .5)
                        close = copy.deepcopy(client.transport_close)
                        assert close["close_code"] == 1006 and close["expected"] is False
                        assert close["exception_type"] == "ServerTimeoutError"
                        expected_pong = .5 if cause == "transport_blackout_hb1" else 1.0
                        assert close["exception"] == f"No PONG received after {expected_pong} seconds"
                        await client.detach()
                        await client.attach()
                    else:
                        await asyncio.sleep(.05)
                        close = copy.deepcopy(client.transport_close)
                        assert close is None and not client.ws.closed and not client.reader.done()
                    timeline.append({"event": "transport_observed", "pc_monotonic_s": loop.time(), "pi_monotonic_s": pi_now[0], "close": close})
                    assert not await guard.resume(expected_pause="input_expired", renewal_check=healthy_renewal)
                    assert client.snapshot["evidence"]["active_acknowledged"] is True
                    with pytest.raises(m.CommandRefusedError) as stale:
                        await client.command(dict(old_frame, op="input", **old_identity))
                    assert stale.value.response["accepted"] is False and stale.value.response.get("effect_admitted") is False
                    with pytest.raises(m.CommandRefusedError) as old_grant_reply:
                        await client.command(dict(old_frame, op="input", **client.identities()))
                    assert old_grant_reply.value.response["accepted"] is False
                    assert old_grant_reply.value.response.get("effect_admitted") is False
                    assert not e._live_target  # Neither old generation nor expired grant can replay a frame.
                    producer.native_cursor, producer.reacquire = m._live(client.snapshot)["cursor"], True
                    fresh = await m.send_leader_sample(client, producer, m.ZERO_BODY, record, phase="individual", progress_s=.04)
                    assert fresh[2]["accepted"] is True and fresh[0]["grant_id"] != old_grant
                    assert fresh[1]["source"]["sample_seq"] == 2
                    assert a.run["deadline"] == 309. and a.run["native_live_at"] == 9.
                    assert m._live(client.snapshot)["reference"] == original_reference
                    assert a.run["recovery_episodes"] == guard.completed_episodes == 0
                    print("COMMON59_BLACKOUT", json.dumps({"cause": cause, "timeline": timeline,
                        "recovered_new_input": True, "original_deadline": a.run["deadline"], "native_episodes": a.run["recovery_episodes"]}))
                finally:
                    if not transport.is_closing():
                        transport.resume_reading()
                    ticking.cancel()
                    renewal.cancel()
                    await asyncio.gather(ticking, renewal, return_exceptions=True)
                return
            if cause == "acquisition_input_pause":
                # Synthetic59: the granted capture overlaps a real Core ordinary
                # expiry. Source/authority remain original; no input was sent.
                pi_now, sent = [10.], []
                a.clock = e.clock = lambda: pi_now[0]
                original_call, original_capture = gateway.call, producer.capture
                loop = asyncio.get_running_loop()
                before_identity = client.identities()

                async def capture_envelopes(command, device):
                    if command["op"] in {"grant", "input", "connect", "release_input", "resume", "claim", "start", "stop"}:
                        sent.append(copy.deepcopy(command))
                    return await original_call(command, device)

                async def expire_before_capture_returns():
                    pi_now[0] += .3
                    a.tick()
                    assert a.run["status"] == "paused" and a.events[-1]["kind"] == "input_expired"
                    await client.wait(lambda: client.snapshot["run"]["intent_revision"] ==
                                      before_identity["intent_revision"] + 1, 1)

                def overlapping_capture(*args, **kwargs):
                    value = original_capture(*args, **kwargs)
                    asyncio.run_coroutine_threadsafe(expire_before_capture_returns(), loop).result(1)
                    return value

                gateway.call, producer.capture = capture_envelopes, overlapping_capture
                discarded = await m.send_leader_sample(
                    client, producer, m.ZERO_BODY, record, phase="individual", progress_s=.02)
                assert discarded is None and producer.sequence == 2
                assert [v["op"] for v in sent] == ["grant"]
                dropped = records[-1]
                assert dropped["event"] == "leader_acquisition_discarded"
                assert dropped["identity"] == before_identity
                assert dropped["current_identity"]["intent_revision"] == before_identity["intent_revision"] + 1
                assert dropped["pause_event"]["kind"] == "input_expired"
                assert dropped["discarded"] is True and dropped["fresh_acquisition_allowed"] is False
                assert not await guard.resume(expected_pause="input_expired")
                assert client.snapshot["evidence"]["active_acknowledged"] is True
                producer.capture = original_capture
                producer.native_cursor, producer.reacquire = m._live(client.snapshot)["cursor"], True
                fresh = await m.send_leader_sample(
                    client, producer, m.ZERO_BODY, record, phase="individual", progress_s=.02)
                assert fresh[2]["accepted"] is True and producer.sequence == 3
                assert fresh[0]["grant_id"] != dropped["grant"]["grant_id"]
                assert fresh[1]["source"]["sample_seq"] == dropped["frame"]["source"]["sample_seq"] + 1
                assert [v["op"] for v in sent] == ["grant", "connect", "release_input", "resume", "grant", "input"]
                assert sent[-1]["grant_id"] != dropped["grant"]["grant_id"]
                assert a.run["deadline"] == 309. and a.run["native_live_at"] == 9.
                assert a.run["recovery_episodes"] == 0 and guard.pause_recoveries == 1
                return
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
                                        "source_fresh": True, "received_at_monotonic_s": pi_clock[0],
                                        "request_received_at_monotonic_s": pi_clock[0]}
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

    asyncio.run(asyncio.wait_for(scenario(), 8 if cause == "transport_blackout_long" else 5))


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
def test_opaque_mailbox_rejoin_still_waits_for_actual_current_held_proof(missing, monkeypatch):
    import asyncio
    import copy
    from types import SimpleNamespace

    m = adapter()
    monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: 1000., perf_counter=m.time.perf_counter))
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


@pytest.mark.parametrize("change", [
    None, "post", "view_wait", "twice", "wrong_type", "wrong_message", "reader", "expected", "code",
    "manual", "foreign", "fault", "uncertain", "revision", "running", "stale_ack", "phase_expired",
    "phase_extended", "caller_expired", "renewal", "later_error", "reporting", "persistent_view",
    "held_wait", "held_foreign", "held_wrong_type", "held_phase_expired", "held_renewal", "held_reporting",
    "reply_foreign", "reply_phase_expired", "reply_nested",
])
def test_unknown_resume_result_keeps_authority_original_phase_and_error(monkeypatch, change):
    """Synthetic guards supplement the real Core/Gateway pre/post/native cases."""
    import asyncio
    import copy
    from types import SimpleNamespace

    m = adapter()
    pc, commands, failures, records = [1000.], [], [], []
    monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: pc[0]))
    initial = recovery_state()
    initial["run"].update(status="paused", intent_revision=3, remaining_s=299.875)
    initial["events"] = [{"kind": "input_expired", "at": 100.125,
                           "run_id": "owned", "service_incarnation": "service"}]

    def held(state, revision, stamp):
        state["run"].update(status="paused", intent_revision=revision, remaining_s=400. - stamp)
        state["evidence"].update(active_acknowledged=False, acknowledged_intent_revision=revision)
        state["evidence"]["accepted_host_action"].update(state="paused", intent_revision=revision,
                                                        acquired_at_monotonic_s=stamp)
        state["run"]["live_input"]["cursor_intent_revision"] = revision
        return state

    held(initial, 3, 100.125)

    class Client:
        device, generation, connection, sequence = "device", "control", "connection", 0
        resume = m.SessionClient.resume
        identities = m.SessionClient.identities
        transport_close, reads, attaches = None, 0, 0

        def __init__(self):
            self.snapshot = copy.deepcopy(initial)
            self.fresh = None
            if change is not None and change.startswith("reply_"):
                self.ws = SimpleNamespace(closed=False, exception=lambda: None)
                self.reader, self.transport_error = None, None

        async def get(self, path):
            assert path == "state"
            self.reads += 1
            if self.fresh is not None:
                pc[0] += .125
                state = copy.deepcopy(self.fresh)
                if change == "persistent_view":
                    pc[0] += 8
                if change in {"view_wait", "persistent_view"}:
                    state["run"]["live_input"]["required_view"] = {
                        "required": True, "qualified": self.reads > 2 and change != "persistent_view",
                        "role": "wrist_left", "cause": None if self.reads > 2 and change != "persistent_view" else "required_view_expired"}
                self.snapshot = state
            return {"snapshot": copy.deepcopy(self.snapshot)}

        async def command(self, command):
            commands.append(copy.deepcopy(command))
            op = command["op"]
            if op == "connect":
                if failures and change == "later_error":
                    raise ConnectionError("new fenced operation failed")
                self.snapshot = held(copy.deepcopy(self.snapshot), self.snapshot["run"]["intent_revision"] + 1,
                                     100.25 if not failures else 100.5)
                return {"accepted": True, "status": "connected", "connection_generation": str(uuid4()),
                        "snapshot": copy.deepcopy(self.snapshot)}
            if op == "release_input":
                return {"accepted": True, "status": "released", "effect_admitted": False}
            assert op == "resume"
            if not failures or change == "twice" and len(failures) < 2 or change == "reply_nested":
                self.fresh = held(copy.deepcopy(self.snapshot), command["intent_revision"],
                                  100.375 if not failures else 100.625)
                if change == "post":
                    self.fresh = held(self.fresh, command["intent_revision"] + 2, 100.5)
                    self.fresh["events"].append({"kind": "native_input_expired", "at": 100.375,
                                                  "run_id": "owned", "service_incarnation": "service"})
                if change == "manual":
                    self.fresh["events"].append({"kind": "pause", "at": 100.375,
                                                  "run_id": "owned", "service_incarnation": "service"})
                elif change == "foreign":
                    self.fresh["run"]["run_id"] = "foreign"
                elif change == "fault":
                    self.fresh["evidence"]["fault"] = "actual hard fault"
                elif change == "uncertain":
                    self.fresh["run"]["uncertain"] = True
                elif change == "revision":
                    self.fresh["run"]["intent_revision"] += 1
                elif change == "running":
                    self.fresh["run"]["status"] = "running"
                elif change == "stale_ack":
                    self.fresh["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"] = 99.
                if change in {"phase_expired", "caller_expired"}:
                    pc[0] += 8. if change == "phase_expired" else .25
                self.transport_close = {"expected": change == "expected", "close_code": 1008 if change == "code" else 1006,
                    "reason": "stream-ended", "reader_error": {"error_type": "RuntimeError"} if change == "reader" else None,
                    "exception_type": "RuntimeError" if change == "wrong_type" else "ClientConnectionResetError",
                    "exception": "other reset" if change == "wrong_message" else "Cannot write to closing transport"}
                if change is not None and change.startswith("reply_"):
                    self.transport_close = None
                    if change == "reply_foreign":
                        self.fresh["run"]["run_id"] = "foreign"
                    if change == "reply_phase_expired":
                        pc[0] += 8
                    error = TimeoutError()
                    error.resume_reply_timeout = {"operation_id": command["operation_id"], "request_id": str(uuid4()),
                        "timing": {"request_started_at_monotonic_s": 10., "send_started_at_monotonic_s": 10.,
                                   "send_completed_at_monotonic_s": 10., "response_timeout_observed_at_monotonic_s": 11.}}
                else:
                    error = ConnectionError("current WSS connection lost")
                failures.append(error)
                raise error
            if change == "post":
                pc[0] = 1007.75  # Qualification finished within the ORIGINAL eight seconds.
            self.snapshot["run"].update(status="running", intent_revision=command["intent_revision"] + 1)
            self.snapshot["evidence"]["active_acknowledged"] = True
            return {"accepted": True, "status": "running", "effect_admitted": False}

        async def wait(self, predicate, timeout):
            if change is not None and change.startswith("held_") and not failures:
                assert not any(c["op"] == "resume" for c in commands)
                self.fresh = held(copy.deepcopy(self.snapshot), self.snapshot["run"]["intent_revision"], 100.375)
                if change == "held_foreign":
                    self.fresh["run"]["run_id"] = "foreign"
                if change == "held_phase_expired":
                    pc[0] += 8
                self.transport_close = {"expected": False, "close_code": 1006, "reason": "stream-ended", "reader_error": None,
                    "exception_type": "RuntimeError" if change == "held_wrong_type" else "ClientConnectionResetError",
                    "exception": "Cannot write to closing transport"}
                error = ConnectionError("current WSS connection ended: stream-ended")
                failures.append(error)
                raise error
            if change == "post" and timeout == 5:
                pc[0] += .5  # Distinct active-ACK phase may cross the earlier held deadline.
                self.snapshot["run"]["remaining_s"] = 1400. - pc[0]
                self.snapshot["evidence"]["accepted_host_action"].update(
                    state="active", intent_revision=self.snapshot["run"]["intent_revision"],
                    acquired_at_monotonic_s=pc[0] - 900.)
            assert predicate(), "no success can bypass held/view or actual active ACK"

        async def detach(self):
            self.snapshot = None

        async def attach(self):
            self.attaches += 1
            self.snapshot = copy.deepcopy(self.fresh)

    async def scenario():
        client = Client()
        def record(value):
            if value["event"] in {"resume_result_unknown", "held_qualification_transport_closed"}:
                assert len(__import__("json").dumps(value, allow_nan=False).encode()) <= 2048
                assert value["outcome"] == ("resume_not_requested" if change is not None and change.startswith("held_") else "unknown")
                if change is not None and change.startswith("held_"):
                    assert value["operation_id"] is None
                if change in {"reporting", "held_reporting"}:
                    raise ValueError("fault reporting failed")
                if change == "phase_extended":
                    failures[-1].resume_result_context["phase_ceiling"] += 1
            records.append(value)
        guard = m.SameRunRecovery(client, record)
        def renewal():
            if failures and change in {"renewal", "held_renewal"}:
                raise RuntimeError("original renewal failed")
        if change in {None, "post", "view_wait", "twice", "held_wait"}:
            assert not await guard.resume(expected_pause="input_expired", renewal_check=renewal)
            resumes = [c for c in commands if c["op"] == "resume"]
            assert len(resumes) == (1 if change == "held_wait" else 3 if change == "twice" else 2)
            assert len({c["operation_id"] for c in resumes}) == len(resumes)
            assert len({c["connection_generation"] for c in resumes}) == len(resumes)
            assert client.snapshot["evidence"]["active_acknowledged"] is True
            assert client.attaches == (1 if change == "held_wait" else len(resumes) - 1)
            assert len([v for v in records if v["event"] == "resume_result_unknown"]) == (0 if change == "held_wait" else len(resumes) - 1)
        elif change.startswith("reply_"):
            with pytest.raises(TimeoutError) as caught:
                await guard.resume(expected_pause="input_expired", renewal_check=renewal)
            assert caught.value is failures[0]
            assert sum(c["op"] == "connect" for c in commands) == (2 if change == "reply_nested" else 1)
            assert sum(c["op"] == "resume" for c in commands) == (2 if change == "reply_nested" else 1)
            assert guard.completed_episodes == 0
        else:
            with pytest.raises(ConnectionError) as caught:
                await guard.resume(expected_pause="input_expired", renewal_check=renewal,
                                   wall_deadline=1000.125 if change == "caller_expired" else None)
            assert caught.value is failures[0]
            assert sum(c["op"] == "resume" for c in commands) == (0 if change.startswith("held_") else 1)
            if change != "later_error":
                assert sum(c["op"] == "connect" for c in commands) == 1
            assert guard.completed_episodes == 0
        assert not any(c["op"] in {"claim", "start", "grant", "input"} for c in commands)
        assert client.sequence == 0 and guard.original["run"]["deadline"] == 400.
        assert all(v["phase_ceiling_monotonic_s"] == 1008. for v in records if v["event"] == "resume_result_unknown")

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["send", "outer", "non_resume"])
def test_resume_reply_timeout_tag_requires_the_actual_inner_response_future(phase):
    import asyncio
    from types import SimpleNamespace

    m = adapter()
    sent = []
    original = TimeoutError("original send timeout")
    async def send(value):
        sent.append(value)
        if phase == "send":
            raise original
    client = SimpleNamespace(transport_error=None, ws=SimpleNamespace(closed=False, send_json=send),
                             reader=None, pending={})
    async def scenario():
        command = {"op": "grant" if phase == "non_resume" else "resume", "operation_id": str(uuid4())}
        with pytest.raises(TimeoutError) as caught:
            if phase == "outer":
                await asyncio.wait_for(m.SessionClient.command(client, command), .01)
            else:
                await m.SessionClient.command(client, command)
        assert not hasattr(caught.value, "resume_reply_timeout")
        assert not hasattr(caught.value, "resume_result_context")
        assert client.pending == {} and len(sent) == 1
        if phase == "send":
            assert caught.value is original
    asyncio.run(scenario())


@pytest.mark.parametrize("change", [None, "socket", "operation", "request", "identity", "typed_identity",
    "clock", "future", "extra", "error", "message", "untagged", "non_resume"])
def test_resume_send_failure_requires_original_current_request_socket_and_provenance(change):
    import asyncio
    import copy
    from types import SimpleNamespace

    from aiohttp import ClientConnectionResetError

    m = adapter()
    identity = {"run_id": "owned", "service_incarnation": "service", "controller_generation": "controller",
                "connection_generation": "connection", "host_incarnation": "host", "intent_revision": 1}
    operation_id, sent = str(uuid4()), []
    original = ClientConnectionResetError("Cannot write to closing transport")
    async def send(value):
        sent.append(copy.deepcopy(value))
        raise original
    ws = SimpleNamespace(closed=False, send_json=send)
    client = SimpleNamespace(transport_error=None, ws=ws, reader=None, pending={})

    async def scenario():
        with pytest.raises(ClientConnectionResetError) as caught:
            await m.SessionClient.command(client, dict(op="grant" if change == "non_resume" else "resume",
                                                      operation_id=operation_id, **identity))
        assert caught.value is original and client.pending == {} and len(sent) == 1
        if change == "non_resume":
            assert not hasattr(original, "resume_send_failure")
            return
        detail = original.resume_send_failure
        assert detail["operation_id"] == operation_id and detail["request_id"] == sent[0]["id"]
        assert detail["identity"] == identity and original._resume_send_ws is ws
        assert detail["error_type"] == "ClientConnectionResetError" and detail["message"] == str(original)
        assert "send_completed_at_monotonic_s" not in detail["timing"]
        assert not hasattr(original, "resume_reply_timeout")
        current_ws = ws
        if change == "socket":
            current_ws = object()
        elif change == "operation":
            detail["operation_id"] = str(uuid4())
        elif change == "request":
            detail["request_id"] = "invalid UUID"
        elif change == "identity":
            detail["identity"]["connection_generation"] = "replacement"
        elif change == "typed_identity":
            detail["identity"]["intent_revision"] = True
        elif change == "clock":
            detail["timing"]["send_failed_at_monotonic_s"] = detail["timing"]["send_started_at_monotonic_s"] - 1
        elif change == "future":
            detail["timing"]["send_failed_at_monotonic_s"] = float("inf")
        elif change == "extra":
            detail["effect_admitted"] = False
        elif change == "error":
            detail["error_type"] = "ConnectionError"
        elif change == "message":
            detail["message"] = "different error"
        elif change == "untagged":
            del original._resume_send_ws
        assert m._resume_send_failure(original, identity, operation_id, current_ws) == (detail if change is None else None)
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["closed-history", "manual", "deadline", "workspace", "episode", "controller"])
def test_acquisition_pause_discard_keeps_immutable_authority_and_closed_history(change):
    import asyncio
    import copy

    m = adapter()
    records, calls = [], []
    before = recovery_state()
    before["run"]["operation_id"] = str(uuid4())
    before["run"].update(first_cause="feedback_loss", recovery_episodes=2)
    before["run"]["live_input"].update(live_admitted_at_monotonic_s=100., live_deadline_monotonic_s=400.)
    before["events"] = [{"kind": "resumed", "at": 100.005, "run_id": "owned", "service_incarnation": "service"}]
    after = copy.deepcopy(before)
    after["run"].update(status="paused", intent_revision=3, remaining_s=299.9)
    after["events"].append({"kind": "input_expired", "at": 100.08, "run_id": "owned", "service_incarnation": "service"})
    if change == "manual":
        after["events"][-1]["kind"] = "pause"
    elif change == "deadline":
        after["run"]["deadline"] += 1
    elif change == "workspace":
        after["run"]["live_input"]["workspace"]["reference"][KEYS[0]] += 1
    elif change == "episode":
        after["run"]["recovery_episodes"] += 1
    elif change == "controller":
        after["controller"]["controller_generation"] = "foreign"

    class Client:
        snapshot = copy.deepcopy(before)
        generation, connection, device, sequence = "control", "connection", "device", 0
        identities = m.SessionClient.identities

        async def command(self, command):
            calls.append(command)
            assert command["op"] == "grant"
            return {"grant_id": str(uuid4())}

    client = Client()

    class Input:
        kind = "virtual-leaders"

        def get_action(self):
            client.snapshot = copy.deepcopy(after)
            return {key.removeprefix("arm_"): value for key, value in workspace()["reference"].items()}

    producer = m.FrameProducer(Input())
    producer.workspace, producer.native_cursor = workspace(), workspace()["reference"]
    async def scenario():
        return await m.send_leader_sample(client, producer, m.ZERO_BODY, records.append,
                                          phase="individual", progress_s=0.)
    if change == "closed-history":
        assert asyncio.run(scenario()) is None
        assert records[-1]["discarded"] is True and records[-1]["fresh_acquisition_allowed"] is False
        assert client.snapshot["run"]["first_cause"] == "feedback_loss"
        assert client.snapshot["run"]["recovery_episodes"] == 2
    else:
        with pytest.raises(RuntimeError, match="current input identity changed during acquisition"):
            asyncio.run(scenario())
        assert not any(row["event"] == "leader_acquisition_discarded" for row in records)
    assert [value["op"] for value in calls] == ["grant"]



def test_current_stream_ack_credit_cannot_come_from_a_late_old_stream():
    import asyncio
    from types import SimpleNamespace

    m = adapter()
    async def scenario():
        old_entered, old_release, new_entered, new_release = (asyncio.Event() for _ in range(4))
        async def old_send(value):
            assert value == {"ack": "old-revision"}
            old_entered.set()
            await old_release.wait()
        async def new_send(value):
            assert value == {"ack": "new-revision"}
            new_entered.set()
            await new_release.wait()
        old_ws, new_ws = (SimpleNamespace(closed=False, send_json=f) for f in (old_send, new_send))
        client = SimpleNamespace(ws=old_ws, _ack_ws=old_ws, _ack_task=None,
            _ack_slot=("old-revision", m.time.monotonic() + 2), _ack_revision="old-revision",
            _acked_revision=None, _ack_error=None, transport_error=None, _ack_changed=asyncio.Event())
        old_task = asyncio.create_task(m.SessionClient._send_acks(client, old_ws))
        await old_entered.wait()
        client.ws = client._ack_ws = new_ws
        client._ack_revision, client._ack_slot = "new-revision", ("new-revision", m.time.monotonic() + 2)
        barrier = asyncio.create_task(m.SessionClient._wait_current_ack(client, new_ws, m.time.monotonic() + 2))
        old_release.set()
        await old_task
        await asyncio.sleep(0)
        assert client._acked_revision is None and not barrier.done()
        new_task = asyncio.create_task(m.SessionClient._send_acks(client, new_ws))
        await new_entered.wait()
        new_release.set()
        await asyncio.gather(new_task, barrier)
        assert client._acked_revision == "new-revision" and client._ack_error is None
    asyncio.run(asyncio.wait_for(scenario(), 1))


@pytest.mark.parametrize("lost_op", ["connect", "release_input"])
@pytest.mark.parametrize("change", [None, "persistent", "phase", "caller", "native", "cancel", "pause", "stop",
    "takeover", "reboot", "expired", "revision", "cursor", "ack", "backwards_ack", "remaining",
    "feedback", "observation", "hold", "alignment", "native_ack", "fault", "bool_alignment", "malformed_view", "view_cause", "wrong_role", "renewal",
    "alignment_ready", "alignment_persistent", "alignment_stale_ack"])
def test_ordinary_binding_view_wait_keeps_current_hold_and_original_ceiling(monkeypatch, lost_op, change):
    import asyncio
    import copy
    from types import SimpleNamespace

    m = adapter()
    pc, records, calls = [1000.], [], []
    real_sleep = asyncio.sleep
    monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: pc[0], perf_counter=m.time.perf_counter))
    async def sleep(value):
        pc[0] += value
        await real_sleep(0)
    monkeypatch.setattr(m.asyncio, "sleep", sleep)
    before = recovery_state()
    before["run"].update(status="paused", operation_id=str(uuid4()))
    before["evidence"].update(active_acknowledged=False)
    before["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"] = 100.
    before["events"] = [{"kind": "input_expired", "at": 100., "run_id": "owned", "service_incarnation": "service"}]
    original = ConnectionError("original ordinary command result unknown")

    class Client:
        snapshot = copy.deepcopy(before)
        device, generation, connection, sequence = "device", "control", "connection", 0
        identities, resume = m.SessionClient.identities, m.SessionClient.resume
        transport_close = {"expected": False, "reason": "stream-ended", "close_code": 1006, "reader_error": None}
        reads, connects, releases, revision = 0, 0, 0, before["run"]["intent_revision"]
        lost, pending_stamp = False, None

        def current(self):
            value = copy.deepcopy(before)
            elapsed = pc[0] - 1000.
            value["run"].update(intent_revision=self.revision, remaining_s=300. - elapsed)
            value["run"]["live_input"]["cursor_intent_revision"] = self.revision
            value["evidence"]["acknowledged_intent_revision"] = self.revision
            value["evidence"]["accepted_host_action"].update(intent_revision=self.revision, acquired_at_monotonic_s=100. + elapsed)
            return value

        async def get(self, path):
            assert path == "state"
            self.reads += 1
            pc[0] += 3. if change == "phase" else .01
            value = self.current()
            if self.lost:
                assert sum(v["op"] == "connect" for v in calls) == 1
                assert sum(v["op"] == "release_input" for v in calls) == int(lost_op == "release_input")
                assert not any(v["op"] in {"resume", "grant", "input", "claim", "start"} for v in calls)
                pending = self.reads == 2 or change in {"persistent", "phase"}
                if change and change.startswith("alignment_"):
                    value["evidence"]["pose_aligned"] = not (self.reads == 2 or change == "alignment_persistent")
                    pending = False
                    if self.reads == 2:
                        self.pending_stamp = value["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"]
                    if change == "alignment_stale_ack" and self.reads > 2:
                        value["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"] = self.pending_stamp
                value["run"]["live_input"]["required_view"].update(
                    required=True, qualified=not pending, role="chest", cause="required_view_expired" if pending else None)
                if self.reads > 2:
                    if change == "cancel":
                        raise asyncio.CancelledError()
                    if change == "pause":
                        value["events"].append(dict(value["events"][-1], kind="pause", at=100.05))
                    if change == "stop":
                        value["run"]["status"] = "stopped"
                    if change == "takeover":
                        value["controller"]["controller_generation"] = "newer-owner"
                    if change == "reboot":
                        value["service_incarnation"] = "new-service"
                    if change == "expired":
                        value["controller"] = None
                    if change == "revision":
                        value["run"]["intent_revision"] += 1
                    if change == "cursor":
                        value["run"]["live_input"]["cursor_intent_revision"] -= 1
                    if change == "ack":
                        value["evidence"]["accepted_host_action"]["intent_revision"] -= 1
                    if change == "backwards_ack":
                        value["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"] = 100.01
                    if change == "remaining":
                        value["run"]["remaining_s"] = 300.
                    if change in {"feedback", "observation", "hold", "alignment", "native_ack"}:
                        value["evidence"][{"observation": "required_observation", "hold": "hold_acknowledged", "alignment": "pose_aligned"}.get(change, change)] = False
                    if change == "fault":
                        value["evidence"]["fault"] = "genuine hard fault"
                    if change == "bool_alignment":
                        value["evidence"]["pose_aligned"] = 0
                    view = value["run"]["live_input"]["required_view"]
                    if change == "malformed_view":
                        view.pop("required")
                    if change == "view_cause":
                        view["cause"] = "required_view_expired"
                    if change == "wrong_role":
                        view["role"] = "unconfigured"
            if change == "native":
                value["run"]["remaining_s"] = .05 - (pc[0] - 1000.)
            return {"snapshot": value}

        async def detach(self):
            self.snapshot = None

        async def attach(self):
            self.snapshot = self.current()

        async def command(self, command):
            calls.append(copy.deepcopy(command))
            pc[0] += .01
            if command["op"] == "connect":
                self.connects += 1
                self.revision += 1
                if lost_op == "connect" and not self.lost:
                    self.lost = True
                    raise original
                value = self.current()
                return {"connection_generation": "acknowledged-" + str(self.connects), "snapshot": value}
            if command["op"] == "release_input":
                self.releases += 1
                if lost_op == "release_input" and not self.lost:
                    self.lost = True
                    raise original
                return {"accepted": True}
            assert command["op"] == "resume"
            assert self.snapshot["run"]["live_input"]["required_view"]["qualified"] is True
            self.snapshot["run"]["status"] = "running"
            self.snapshot["evidence"]["active_acknowledged"] = True
            return {"accepted": True}

        async def wait(self, predicate, timeout):
            assert 0 < timeout <= 8.
            assert predicate(), "fresh required-view and current Native HOLD are still required"

    async def scenario():
        client = Client()
        guard = m.SameRunRecovery(client, records.append)
        def renewed():
            if change == "renewal" and client.reads > 2:
                raise RuntimeError("current renewal ended")
        until = 1000.055 if change == "caller" else None
        if change in {None, "alignment_ready"}:
            assert not await guard.resume(expected_pause="input_expired", wall_deadline=until, renewal_check=renewed)
            assert client.reads == 3 and client.connects == 2
            assert client.snapshot["evidence"]["active_acknowledged"] is True
            assert [v["op"] for v in calls] == (["connect", "connect", "release_input", "resume"] if lost_op == "connect"
                                                    else ["connect", "release_input", "connect", "release_input", "resume"])
        elif change == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await guard.resume(expected_pause="input_expired", renewal_check=renewed)
        else:
            with pytest.raises((ConnectionError, TimeoutError)) as caught:
                await guard.resume(expected_pause="input_expired", wall_deadline=until, renewal_check=renewed)
            if client.lost:
                assert caught.value is original
            assert client.connects == 1 and not any(v["op"] == "resume" for v in calls)
            if change in {"persistent", "phase", "alignment_persistent"}:
                assert 1008. <= pc[0] < 1009.2
            if change == "caller":
                assert pc[0] < 1000.11
        assert not any(v["op"] in {"claim", "start", "grant", "input"} for v in calls)
        assert guard.original["run"]["deadline"] == 400. and guard.episodes == 0
        assert client.generation == "control" and client.sequence == 0
    asyncio.run(asyncio.wait_for(scenario(), 1.))


@pytest.mark.parametrize("segment,duration", [("left-maintained", 16.), ("left-reach", 20.)])
def test_selected_left_profile_keeps_fixed_targets_schedule_and_other_admitted_joints(segment, duration):
    m = adapter()
    native = workspace()
    task = m.SelectedLeftWorkload(native, segment=segment)
    elbow, gripper = "arm_left_elbow_flex.pos", "arm_left_gripper.pos"
    reference = dict(native["reference"])
    endpoint = native["bounds"][elbow]["max"]
    checks = [(0., 0.), (2., 0.), (4., .5), (6., 1.), (8., 1.)]
    checks += [(10., 1.), (12., .5), (14., 0.), (16., 0.)] if duration == 16 else [(10., 1.), (12., 1.), (14., .5), (16., 0.), (20., 0.)]
    for at, fraction in checks:
        task.elapsed_s = at
        action = {"arm_" + key: value for key, value in task.get_action().items()}
        assert action[elbow] == pytest.approx(reference[elbow] + fraction * (endpoint - reference[elbow]))
        assert all(action[key] == reference[key] for key in KEYS if key not in {elbow, gripper})
        expected_opening = min(reference[gripper] + 20., native["bounds"][gripper]["max"],
                               reference[gripper] + 2 * native["bounds"][gripper]["rate_per_s"])
        opening_fraction = (at - 8) / 2 if 8 < at <= 10 else (12 - at) / 2 if 10 < at < 12 else 0.
        assert action[gripper] == pytest.approx(reference[gripper] + opening_fraction * (expected_opening - reference[gripper]) if duration == 20 else reference[gripper])
        assert task.body_phase_elapsed is None
    assert task.complete and task.duration_s == duration
    assert task.plan()["reference"] == reference
    assert task.plan()["endpoint_normalized"] == endpoint
    assert task.plan()["program_active_seconds"] == duration
    assert task.plan()["gripper_opening_delta"] == (expected_opening - reference[gripper] if duration == 20 else 0.)


@pytest.mark.parametrize("segment", ["left-maintained", "left-reach"])
def test_selected_left_profile_recovery_preserves_absolute_endpoint_and_truthful_interrupted_dwell(segment):
    m = adapter()
    native = workspace()
    task = m.SelectedLeftWorkload(native, segment=segment)
    elbow = "arm_left_elbow_flex.pos"
    origin, endpoint = native["reference"][elbow], native["bounds"][elbow]["max"]
    task.elapsed_s = 7.
    task.admit(10.)
    now = [10.]
    producer = m.FrameProducer(task, clock=lambda: now[0])
    producer.workspace, producer.native_cursor = native, dict(native["reference"])
    frame = producer.capture({"grant_id": str(uuid4())}, body=m.ZERO_BODY)
    assert frame["target"][elbow] == endpoint
    original_plan = task.plan()
    task.freeze()
    task.advance(80.)
    assert task.elapsed_s == 7. and task.interrupted_phases == {"left_endpoint_hold"}
    task.admit(80.)
    now[0] = 80.
    producer.native_cursor[elbow] = origin + 1.
    producer.reacquire = True
    fresh = producer.capture({"grant_id": str(uuid4())}, body=m.ZERO_BODY)
    assert fresh["target"] == frame["target"] and fresh["body"] == m.ZERO_BODY
    assert fresh["source"]["sample_seq"] == 2 and fresh["source"]["grant_id"] != frame["source"]["grant_id"]
    assert task.plan()["reference"] == original_plan["reference"]
    assert task.plan()["endpoint_normalized"] == original_plan["endpoint_normalized"]
    assert task.plan()["interrupted_phases"] == ["left_endpoint_hold"]
    assert task.plan()["dwell_basis"] == "producer active intent; native delivered dwell assessed separately"
    task.elapsed_s = 15. if segment == "left-maintained" else 18.
    now[0] = 81.
    returned = producer.capture({"grant_id": str(uuid4())}, body=m.ZERO_BODY)
    assert returned["target"] == native["reference"]


def test_selected_left_profile_chooses_supported_far_bound_and_never_moves_an_outside_other_reference():
    import copy
    m = adapter()
    native = workspace()
    elbow = "arm_left_elbow_flex.pos"
    native["reference"][elbow] = native["bounds"][elbow]["max"]
    native["bounds"][elbow]["rate_per_s"] = 3.
    task = m.SelectedLeftWorkload(native)
    assert task.plan()["endpoint_normalized"] == native["bounds"][elbow]["min"]
    assert task.plan()["direction_normalized"] == -1
    outside = copy.deepcopy(native)
    outside["reference"]["arm_left_wrist_flex.pos"] = -1.
    with pytest.raises(ValueError, match="workspace"):
        m.SelectedLeftWorkload(outside)


@pytest.mark.parametrize("segment,duration", [("left-maintained", 16.), ("left-reach", 20.), ("left-supported-reach", 28.), ("left-opposite-first", 18.)])
def test_selected_left_cli_declares_finite_normal_virtual_profile(monkeypatch, segment, duration):
    m = adapter()
    seen = []
    async def run(args):
        seen.append(args)
        return {}
    monkeypatch.setattr(m, "run", run)
    m.main(["--source", "virtual-leaders", "--url", "unused", "--ca", "unused", "--storage-state", "unused",
            "--output", "unused", "--workload-segment", segment, "--duration-seconds", str(duration)])
    assert seen[0].workload_segment == segment and seen[0].duration_seconds == duration
    assert not seen[0].bench_continuity and seen[0].interrupt_at_seconds is None


@pytest.mark.parametrize("segment", ["left-maintained", "left-reach"])
def test_selected_left_profile_original_active_clock_does_not_reset_dwell_after_hold(segment):
    m = adapter()
    task = m.SelectedLeftWorkload(workspace(), segment=segment)
    task.admit(0.)
    for step in range(1, 71):
        task.advance(step / 10)
    assert task.elapsed_s == 7.
    task.freeze()
    task.advance(90.)
    assert task.elapsed_s == 7. and task.interrupted_phases == {"left_endpoint_hold"}
    task.admit(90.)
    for step in range(1, 201):
        task.advance(90. + step / 10)
    assert task.complete and task.elapsed_s == task.duration_s
    assert task.phases_completed == {name for name, _, _ in task.schedule}
    assert task.plan()["interrupted_phases"] == ["left_endpoint_hold"]
    assert task.plan()["phases"][2]["start_s"] == 6.
    assert task.plan()["phases"][2]["end_s"] == (10. if segment == "left-maintained" else 8.)


def test_selected_left_profile_refuses_endpoint_that_cannot_fit_existing_ramp_rate():
    m = adapter()
    native = workspace()
    native["bounds"]["arm_left_elbow_flex.pos"]["rate_per_s"] = .1
    with pytest.raises(ValueError, match="unchanged.*ramp rate"):
        m.SelectedLeftWorkload(native)


@pytest.mark.parametrize("at,shoulder_fraction,elbow_fraction,opening_fraction", [
    (0., 0., 0., 0.), (2., 0., 0., 0.), (4., .5, 0., 0.), (6., 1., 0., 0.),
    (8., 1., 0., 0.), (10., 1., .5, 0.), (12., 1., 1., 0.), (14., 1., 1., 0.),
    (15., 1., 1., .5), (16., 1., 1., 1.), (17., 1., 1., .5), (18., 1., 1., 0.),
    (20., 1., .5, 0.), (22., 1., 0., 0.), (24., .5, 0., 0.), (26., 0., 0., 0.), (28., 0., 0., 0.),
])
def test_supported_left_reach_keeps_declared_shoulder_first_order_and_nine_other_targets(at, shoulder_fraction, elbow_fraction, opening_fraction):
    m = adapter()
    native = workspace()
    shoulder, elbow, gripper = "arm_left_shoulder_lift.pos", "arm_left_elbow_flex.pos", "arm_left_gripper.pos"
    native["bounds"][shoulder]["rate_per_s"] = 3.
    task = m.SelectedLeftWorkload(native, segment="left-supported-reach")
    task.elapsed_s = at
    action = {"arm_" + key: value for key, value in task.get_action().items()}
    origin = native["reference"]
    assert action[shoulder] == pytest.approx(origin[shoulder] + shoulder_fraction * (native["bounds"][shoulder]["max"] - origin[shoulder]))
    assert action[elbow] == pytest.approx(origin[elbow] + elbow_fraction * (task.endpoint-origin[elbow]))
    assert action[gripper] == pytest.approx(origin[gripper] + opening_fraction * (task.opening-origin[gripper]))
    assert all(action[key] == origin[key] for key in KEYS if key not in {shoulder, elbow, gripper})
    assert task.duration_s == 28. and task.body_phase_elapsed is None
    plan = task.plan()
    assert plan["shoulder_endpoint_normalized"] == native["bounds"][shoulder]["max"]
    assert plan["reference"] == origin and plan["program_active_seconds"] == 28.


def test_supported_left_reach_recovery_preserves_both_absolute_endpoints_and_interrupted_phase():
    m = adapter()
    native = workspace()
    shoulder, elbow = "arm_left_shoulder_lift.pos", "arm_left_elbow_flex.pos"
    native["bounds"][shoulder]["rate_per_s"] = 3.
    task = m.SelectedLeftWorkload(native, segment="left-supported-reach")
    task.elapsed_s = 13.
    task.admit(10.)
    now = [10.]
    producer = m.FrameProducer(task, clock=lambda: now[0])
    producer.workspace, producer.native_cursor = native, dict(native["reference"])
    original = producer.capture({"grant_id": str(uuid4())}, body=m.ZERO_BODY)
    task.freeze()
    task.advance(90.)
    assert task.elapsed_s == 13. and task.interrupted_phases == {"left_endpoint_hold"}
    task.admit(90.)
    now[0] = 90.
    producer.native_cursor.update({shoulder: 1., elbow: 6.})
    producer.reacquire = True
    fresh = producer.capture({"grant_id": str(uuid4())}, body=m.ZERO_BODY)
    assert fresh["target"] == original["target"] and fresh["body"] == m.ZERO_BODY
    assert fresh["source"]["sample_seq"] == 2 and fresh["source"]["grant_id"] != original["source"]["grant_id"]
    for step in range(1, 151):
        task.advance(90. + step / 10)
    assert task.complete and task.elapsed_s == 28.
    assert task.plan()["reference"] == native["reference"]
    assert task.plan()["interrupted_phases"] == ["left_endpoint_hold"]
    assert task.phases_completed == {name for name, _, _ in task.schedule}


def test_supported_left_reach_refuses_unsupported_shoulder_ramp():
    m = adapter()
    native = workspace()
    native["bounds"]["arm_left_shoulder_lift.pos"]["rate_per_s"] = .1
    with pytest.raises(ValueError, match="shoulder.*unchanged.*ramp rate"):
        m.SelectedLeftWorkload(native, segment="left-supported-reach")


@pytest.mark.parametrize("at,position", [
    (0., "reference"), (2., "reference"), (3., "first_midpoint"), (4., "min"), (6., "min"),
    (8., "corridor_midpoint"), (10., "max"), (12., "max"), (14., "return_midpoint"),
    (16., "reference"), (18., "reference"),
])
def test_opposite_first_left_profile_streams_bounded_direction_changes_and_holds_other_eleven(at, position):
    m = adapter()
    native = workspace()
    elbow = "arm_left_elbow_flex.pos"
    native["bounds"][elbow]["rate_per_s"] = 3.
    task = m.SelectedLeftWorkload(native, segment="left-opposite-first")
    bound, origin = native["bounds"][elbow], native["reference"][elbow]
    expected = {"reference": origin, "min": bound["min"], "max": bound["max"],
                "first_midpoint": (origin + bound["min"]) / 2,
                "corridor_midpoint": (bound["min"] + bound["max"]) / 2,
                "return_midpoint": (bound["max"] + origin) / 2}[position]
    task.elapsed_s = at
    producer = m.FrameProducer(task, clock=lambda: 10.)
    producer.workspace, producer.native_cursor = native, dict(native["reference"])
    frame = producer.capture({"grant_id": str(uuid4())}, body=m.ZERO_BODY)
    assert frame["target"][elbow] == pytest.approx(expected)
    assert all(frame["target"][key] == native["reference"][key] for key in KEYS if key != elbow)
    assert frame["body"] == m.ZERO_BODY and task.body_phase_elapsed is None
    plan = task.plan()
    assert plan["reference"] == native["reference"] and plan["program_active_seconds"] == 18.
    assert plan["first_endpoint_normalized"] == bound["min"]
    assert plan["first_endpoint_raw_bound"] == native["raw_bounds"][elbow][0]
    assert plan["endpoint_normalized"] == bound["max"]
    assert plan["endpoint_raw_bound"] == native["raw_bounds"][elbow][1]
    assert plan["gripper_opening_delta"] == 0.
    assert plan["phases"] == [
        {"phase": name, "start_s": start, "end_s": end} for name, start, end in (
            ("left_baseline", 0., 2.), ("left_opposite", 2., 4.), ("left_opposite_hold", 4., 6.),
            ("left_outward", 6., 10.), ("left_endpoint_hold", 10., 12.),
            ("left_retract", 12., 16.), ("left_final_hold", 16., 18.))]


def test_opposite_first_left_profile_respects_each_unchanged_ramp_rate_and_dynamic_reference():
    m = adapter()
    native = workspace()
    elbow = "arm_left_elbow_flex.pos"
    native["bounds"][elbow]["rate_per_s"] = 3.
    native["reference"][elbow] = 6.25
    task = m.SelectedLeftWorkload(native, segment="left-opposite-first")
    previous = None
    for step in range(361):
        task.elapsed_s = step / 20
        target = {"arm_" + key: value for key, value in task.get_action().items()}
        m.validate_target(target, workspace=native)
        if previous is not None:
            assert abs(target[elbow] - previous) <= .05 * native["bounds"][elbow]["rate_per_s"] + 1e-9
        previous = target[elbow]
    assert previous == 6.25 and task.complete
    native["bounds"][elbow]["rate_per_s"] = 2.
    with pytest.raises(ValueError, match="opposite.*unchanged.*ramp rate"):
        m.SelectedLeftWorkload(native, segment="left-opposite-first")
    native["bounds"][elbow]["rate_per_s"] = 3.
    native["reference"][elbow] = native["bounds"][elbow]["max"]
    with pytest.raises(ValueError, match="opposite.*unchanged.*ramp rate"):
        m.SelectedLeftWorkload(native, segment="left-opposite-first")


@pytest.mark.parametrize("elapsed,phase", [(5., "left_opposite_hold"), (11., "left_endpoint_hold")])
def test_opposite_first_left_rejoin_preserves_current_run_reference_endpoints_and_input_expiry(elapsed, phase):
    m = adapter()
    native = workspace()
    elbow = "arm_left_elbow_flex.pos"
    native["bounds"][elbow]["rate_per_s"] = 3.
    task = m.SelectedLeftWorkload(native, segment="left-opposite-first")
    task.elapsed_s = elapsed
    task.admit(10.)
    now = [10.]
    producer = m.FrameProducer(task, clock=lambda: now[0])
    producer.workspace, producer.native_cursor = native, dict(native["reference"])
    original = producer.capture({"grant_id": str(uuid4())}, body=m.ZERO_BODY)
    plan = task.plan()
    task.freeze()
    task.advance(90.)
    assert task.elapsed_s == elapsed and task.interrupted_phases == {phase}
    now[0] = 90.
    with pytest.raises(ValueError, match="expired"):
        producer.serialize(original)
    task.admit(90.)
    producer.native_cursor[elbow] = 7.
    producer.reacquire = True
    fresh = producer.capture({"grant_id": str(uuid4())}, body=m.ZERO_BODY)
    assert fresh["target"] == original["target"] and fresh["body"] == m.ZERO_BODY
    assert fresh["source"]["sample_seq"] == 2 and fresh["source"]["grant_id"] != original["source"]["grant_id"]
    assert fresh["source"]["acquired_at_monotonic_s"] == 90.
    assert all(task.plan()[key] == plan[key] for key in ("reference", "first_endpoint_normalized", "endpoint_normalized", "phases"))
    for step in range(1, 181):
        task.advance(90. + step / 10)
    assert task.complete and task.elapsed_s == 18.
    assert task.plan()["interrupted_phases"] == [phase]
    assert task.phases_completed == {name for name, _, _ in task.schedule}
    now[0] = 108.
    returned = producer.capture({"grant_id": str(uuid4())}, body=m.ZERO_BODY)
    assert returned["target"] == native["reference"]


def peer_workspace():
    m = adapter()
    value = workspace()
    for side, cal, raw in (("left", [3, 0, 1845, 864, 3073], 2066), ("right", [3, 0, -658, 827, 3039], 1855)):
        key = f"arm_{side}_elbow_flex.pos"
        value["calibration"][key[:-4]] = cal
        lo, hi = value["raw_bounds"][key]
        value["bounds"][key].update(min=m.normalized_raw(lo, cal), max=m.normalized_raw(hi, cal))
        value["reference"][key] = m.normalized_raw(raw, cal)
    return value


def peer_baseline_state(value):
    settings = {"version": 1, "sha256": "a" * 64}
    for side in ("left", "right"):
        motor = f"arm_{side}_elbow_flex"
        settings[side] = {
            "motor": motor,
            "motor_id": 3,
            "calibration": value["calibration"][motor],
            "original": [3, 777, 3, 10, 16, 0, 32, 0],
            "active": [3, 777, 3, 10, 16, 0, 32, 0],
            "original_read_interval": [1.0, 1.01],
            "active_read_interval": [2.0, 2.01],
        }
    run, host, service = str(uuid4()), str(uuid4()), str(uuid4())
    return {
        "service_incarnation": service,
        "run": {"run_id": run, "backend_incarnation": host, "intent_revision": 2},
        "evidence": {
            "host_incarnation": host,
            "native_ack": True,
            "active_acknowledged": True,
            "acknowledged_intent_revision": 2,
            "feedback": True,
            "feedback_age_s": 0.02,
            "required_observation": True,
            "observation_age_s": 0.03,
            "observation": {"qualified": True},
            "accepted_host_action": {
                "run_id": run,
                "host_incarnation": host,
                "epoch": 1,
                "intent_revision": 2,
                "accepted_action_sequence": 3,
                "read_started_at_monotonic_s": 3.0,
                "acquired_at_monotonic_s": 3.01,
                "teleop_elbow_settings": settings,
            },
        },
    }


@pytest.mark.parametrize("side,direction,raw,endpoint", [("left", -1, 2066, 2014), ("right", 1, 1855, 1907)])
def test_peer_maintained_explicit_raw_geometry_and_other_eleven_targets(side, direction, raw, endpoint):
    m = adapter()
    native = peer_workspace()
    task = m.SelectedLeftWorkload(
        native, segment=side + "-peer-maintained", peer_side=side, peer_direction=direction, peer_raw_step=52
    )
    assert task.observe_peer_baseline(peer_baseline_state(native)) is True
    key = f"arm_{side}_elbow_flex.pos"
    cal = native["calibration"][key[:-4]]
    expected = m.normalized_raw(endpoint, cal)
    for at, fraction in (
        (0.0, 0.0),
        (2.0, 0.0),
        (4.0, 0.5),
        (6.0, 1.0),
        (8.0, 1.0),
        (10.0, 1.0),
        (12.0, 0.5),
        (14.0, 0.0),
        (16.0, 0.0),
    ):
        task.elapsed_s = at
        action = m.map_leader_action(task.get_action())
        assert action[key] == pytest.approx(
            native["reference"][key] + fraction * (expected - native["reference"][key])
        )
        assert all(action[k] == native["reference"][k] for k in KEYS if k != key)
        assert task.body_phase_elapsed is None
    plan = task.plan()
    assert task.complete and plan["program_active_seconds"] == 16.0
    assert plan["peer_side"] == side and plan["direction_raw"] == direction
    assert plan["reference_raw"] == raw and plan["endpoint_raw"] == endpoint and plan["raw_step"] == 52
    assert plan["travel_encoder_degrees"] == direction * 4.5703125
    assert [(x["phase"], x["start_s"], x["end_s"]) for x in plan["phases"]] == [
        (side + "_baseline", 0.0, 2.0),
        (side + "_outward", 2.0, 6.0),
        (side + "_endpoint_hold", 6.0, 10.0),
        (side + "_retract", 10.0, 14.0),
        (side + "_final_hold", 14.0, 16.0),
    ]


@pytest.mark.parametrize(
    "fault",
    [
        "side",
        "direction",
        "boolean_step",
        "zero_step",
        "large_step",
        "unsupported_endpoint",
        "fractional_reference",
        "slow_rate",
        "old_segment",
    ],
)
def test_peer_maintained_refuses_geometry_without_flip_clamp_or_old_mode_change(fault):
    m = adapter()
    native = peer_workspace()
    kwargs = {
        "segment": "left-peer-maintained",
        "peer_side": "left",
        "peer_direction": -1,
        "peer_raw_step": 52,
    }
    key = "arm_left_elbow_flex.pos"
    if fault == "side":
        kwargs["peer_side"] = "right"
    elif fault == "direction":
        kwargs["peer_direction"] = 0
    elif fault == "boolean_step":
        kwargs["peer_raw_step"] = True
    elif fault == "zero_step":
        kwargs["peer_raw_step"] = 0
    elif fault == "large_step":
        kwargs["peer_raw_step"] = 80
    elif fault == "unsupported_endpoint":
        kwargs["peer_raw_step"] = 79
    elif fault == "fractional_reference":
        native["reference"][key] += 0.01
    elif fault == "slow_rate":
        native["bounds"][key]["rate_per_s"] = 0.01
    else:
        kwargs["segment"] = "left-maintained"
    with pytest.raises(ValueError):
        m.SelectedLeftWorkload(native, **kwargs)


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "p32",
        "wrong_other_pid",
        "firmware",
        "calibration",
        "unknown_field",
        "bad_hash",
        "missing_timing",
        "future_timing",
        "stale_marker",
        "foreign_host",
        "no_ack",
    ],
)
def test_peer_maintained_requires_both_exact_baselines_before_first_outward(fault):
    from contextlib import suppress

    m = adapter()
    native = peer_workspace()
    task = m.SelectedLeftWorkload(
        native, segment="left-peer-maintained", peer_side="left", peer_direction=-1, peer_raw_step=52
    )
    state = peer_baseline_state(native)
    marker = state["evidence"]["accepted_host_action"]
    proof = marker["teleop_elbow_settings"]
    if fault == "missing":
        marker.pop("teleop_elbow_settings")
    elif fault == "p32":
        proof["left"]["active"][4] = 32
    elif fault == "wrong_other_pid":
        proof["right"]["original"][5] = 1
    elif fault == "firmware":
        proof["right"]["active"][3] = 11
    elif fault == "calibration":
        proof["left"]["calibration"] = [3, 0, 0, 864, 3073]
    elif fault == "unknown_field":
        proof["right"]["allow_nonbaseline"] = True
    elif fault == "bad_hash":
        proof["sha256"] = "not-a-hash"
    elif fault == "missing_timing":
        proof["left"].pop("original_read_interval")
    elif fault == "future_timing":
        proof["right"]["active_read_interval"][1] = 5.0
    elif fault == "stale_marker":
        marker["intent_revision"] = 1
    elif fault == "foreign_host":
        marker["host_incarnation"] = str(uuid4())
    else:
        state["evidence"]["native_ack"] = False
    with suppress(RuntimeError):
        assert task.observe_peer_baseline(state) is False
    task.elapsed_s = 2.0
    assert m.map_leader_action(task.get_action()) == native["reference"]
    task.elapsed_s = 2.01
    with pytest.raises(RuntimeError, match="baseline"):
        task.get_action()


@pytest.mark.parametrize("side,direction", [("left", -1), ("right", 1)])
def test_peer_maintained_rejoin_keeps_absolute_geometry_and_no_dwell_reset(side, direction):
    m = adapter()
    native = peer_workspace()
    task = m.SelectedLeftWorkload(
        native, segment=side + "-peer-maintained", peer_side=side, peer_direction=direction, peer_raw_step=52
    )
    state = peer_baseline_state(native)
    assert task.observe_peer_baseline(state)
    task.elapsed_s = 7.0
    task.admit(10.0)
    now = [10.0]
    producer = m.FrameProducer(task, clock=lambda: now[0])
    producer.workspace, producer.native_cursor = native, dict(native["reference"])
    first = producer.capture({"grant_id": str(uuid4())}, body=m.ZERO_BODY)
    task.freeze()
    task.advance(70.0)
    task.admit(70.0)
    now[0] = 70.0
    producer.native_cursor[task.elbow] += 0.5
    producer.reacquire = True
    fresh = producer.capture({"grant_id": str(uuid4())}, body=m.ZERO_BODY)
    assert fresh["target"] == first["target"] and fresh["body"] == m.ZERO_BODY
    assert fresh["source"]["sample_seq"] == 2 and fresh["source"]["grant_id"] != first["source"]["grant_id"]
    assert task.elapsed_s == 7.0 and task.interrupted_phases == {side + "_endpoint_hold"}
    assert task.plan()["reference"] == native["reference"]


@pytest.mark.parametrize("fault", [None, "missing", "mismatched", "old_segment"])
def test_peer_maintained_cli_requires_explicit_prestart_selection(monkeypatch, fault):
    m = adapter()
    seen = []

    async def run(args):
        seen.append(args)
        return {}

    monkeypatch.setattr(m, "run", run)
    argv = [
        "--source",
        "virtual-leaders",
        "--url",
        "unused",
        "--ca",
        "unused",
        "--storage-state",
        "unused",
        "--output",
        "unused",
        "--workload-segment",
        "left-peer-maintained",
        "--duration-seconds",
        "16",
        "--peer-side",
        "left",
        "--peer-direction",
        "-1",
        "--peer-raw-step",
        "52",
    ]
    if fault == "missing":
        argv = argv[:-2]
    elif fault == "mismatched":
        argv[argv.index("--peer-side") + 1] = "right"
    elif fault == "old_segment":
        argv[argv.index("--workload-segment") + 1] = "left-maintained"
    if fault:
        with pytest.raises(SystemExit):
            m.main(argv)
        assert seen == []
    else:
        m.main(argv)
        assert (seen[0].peer_side, seen[0].peer_direction, seen[0].peer_raw_step) == ("left", -1, 52)
        assert seen[0].bench_continuity is False


@pytest.mark.parametrize("change", ["service", "run", "host", "proof_hash", "active_pid"])
def test_peer_maintained_original_baseline_cannot_cross_operation_or_change_settings(change):
    m = adapter()
    native = peer_workspace()
    task = m.SelectedLeftWorkload(
        native, segment="right-peer-maintained", peer_side="right", peer_direction=1, peer_raw_step=52
    )
    state = peer_baseline_state(native)
    assert task.observe_peer_baseline(state)
    if change == "service":
        state["service_incarnation"] = str(uuid4())
    elif change in {"run", "host"}:
        state["run"]["run_id" if change == "run" else "backend_incarnation"] = str(uuid4())
    elif change == "proof_hash":
        state["evidence"]["accepted_host_action"]["teleop_elbow_settings"]["sha256"] = "b" * 64
    else:
        state["evidence"]["accepted_host_action"]["teleop_elbow_settings"]["right"]["active"][4] = 32
    with pytest.raises(RuntimeError):
        task.observe_peer_baseline(state)


@pytest.mark.parametrize("change", ["calibration_bool", "interval_backwards", "version_bool", "missing_id"])
def test_peer_maintained_malformed_baseline_never_opens_outward_gate(change):
    from contextlib import suppress

    m = adapter()
    native = peer_workspace()
    task = m.SelectedLeftWorkload(
        native, segment="left-peer-maintained", peer_side="left", peer_direction=-1, peer_raw_step=52
    )
    state = peer_baseline_state(native)
    proof = state["evidence"]["accepted_host_action"]["teleop_elbow_settings"]
    if change == "calibration_bool":
        proof["left"]["calibration"] = [3, False, 1845, 864, 3073]
    elif change == "interval_backwards":
        proof["right"]["active_read_interval"] = [2.5, 2.0]
    elif change == "version_bool":
        proof["version"] = True
    else:
        state["run"]["run_id"] = None
    with suppress(RuntimeError):
        assert task.observe_peer_baseline(state) is False
    task.elapsed_s = 2.01
    with pytest.raises(RuntimeError, match="baseline"):
        task.get_action()


def test_peer_maintained_run_refuses_invalid_prestart_selection_before_output_or_client(
    tmp_path, monkeypatch
):
    import asyncio
    from types import SimpleNamespace

    m = adapter()
    output = tmp_path / "not-created"
    monkeypatch.setattr(
        m, "SessionClient", lambda *a, **k: pytest.fail("peer invalid selection created client")
    )
    args = SimpleNamespace(
        source="virtual-leaders",
        workload_segment="right-peer-maintained",
        peer_side="left",
        peer_direction=1,
        peer_raw_step=52,
        output=output,
    )
    with pytest.raises(ValueError, match="matching side"):
        asyncio.run(m.run(args))
    assert not output.exists()


@pytest.mark.parametrize("side,bench", [("left", False), ("right", False), ("left", True), ("right", True)])
def test_peer_maintained_cli_explicit_bench_keeps_ordinary_default(monkeypatch, side, bench):
    m = adapter()
    seen = []
    async def run(args):
        seen.append(args)
        return {}
    monkeypatch.setattr(m, "run", run)
    argv = ["--source", "virtual-leaders", "--url", "unused", "--ca", "unused", "--storage-state", "unused",
            "--output", "unused", "--workload-segment", side + "-peer-maintained", "--duration-seconds", "16",
            "--peer-side", side, "--peer-direction", "-1" if side == "left" else "1", "--peer-raw-step", "52"]
    if bench:
        argv.append("--bench-continuity")
    m.main(argv)
    assert len(seen) == 1 and seen[0].bench_continuity is bench
    assert seen[0].workload_segment == side + "-peer-maintained" and seen[0].peer_raw_step == 52
    assert seen[0].interrupt_at_seconds is None and seen[0].next_start_seconds is None
@pytest.mark.parametrize(
    "side,q0,start,end,direction", [("left", 2043, 2067, 2015, -1), ("right", 1861, 1855, 1907, 1)]
)
def test_peer_fixed_preparation_keeps_exact_interval_and_actual_reference(side, q0, start, end, direction):
    m = adapter()
    native = peer_workspace()
    key = f"arm_{side}_elbow_flex.pos"
    cal = native["calibration"][key[:-4]]
    native["reference"][key] = m.normalized_raw(q0, cal)
    task = m.SelectedLeftWorkload(
        native, segment=side + "-peer-fixed", peer_side=side, peer_direction=direction, peer_raw_step=52
    )
    assert task.observe_peer_baseline(peer_baseline_state(native)) is True
    origin = dict(native["reference"])
    start_value, end_value = m.normalized_raw(start, cal), m.normalized_raw(end, cal)
    for at, expected in (
        (0.0, origin[key]),
        (2.0, (origin[key] + start_value) / 2),
        (4.0, start_value),
        (5.0, start_value),
        (6.0, start_value),
        (8.0, (start_value + end_value) / 2),
        (10.0, end_value),
        (12.0, end_value),
        (14.0, end_value),
        (16.0, (end_value + start_value) / 2),
        (18.0, start_value),
        (20.0, start_value),
    ):
        task.elapsed_s = at
        action = m.map_leader_action(task.get_action())
        assert action[key] == pytest.approx(expected)
        assert all(action[k] == origin[k] for k in KEYS if k != key)
        assert task.body_phase_elapsed is None
    plan = task.plan()
    assert task.complete and plan["program_active_seconds"] == 20.0
    assert plan["reference"] == origin and plan["reference_raw"] == q0
    assert (plan["comparison_start_raw"], plan["endpoint_raw"], plan["raw_step"]) == (start, end, 52)
    assert [(p["phase"], p["start_s"], p["end_s"]) for p in plan["phases"]] == [
        (side + "_prepare", 0.0, 4.0),
        (side + "_baseline", 4.0, 6.0),
        (side + "_outward", 6.0, 10.0),
        (side + "_endpoint_hold", 10.0, 14.0),
        (side + "_retract", 14.0, 18.0),
        (side + "_final_hold", 18.0, 20.0),
    ]
    assert (
        plan["comparison_start_qualification"] == "commanded preparation; achieved pose assessed separately"
    )


@pytest.mark.parametrize("fault", ["direction", "step", "unsupported_reference", "rate", "relative_p32"])
def test_peer_fixed_refuses_retargeting_or_unsupported_preparation(fault):
    m = adapter()
    native = peer_workspace()
    kwargs = {"segment": "left-peer-fixed", "peer_side": "left", "peer_direction": -1, "peer_raw_step": 52}
    if fault == "direction":
        kwargs["peer_direction"] = 1
    elif fault == "step":
        kwargs["peer_raw_step"] = 51
    elif fault == "unsupported_reference":
        native["reference"]["arm_left_elbow_flex.pos"] = m.normalized_raw(
            2010, native["calibration"]["arm_left_elbow_flex"]
        )
    elif fault == "rate":
        native["bounds"]["arm_left_elbow_flex.pos"]["rate_per_s"] = 0.01
    else:
        kwargs.update(segment="left-peer-maintained", peer_active_p=32)
    with pytest.raises(ValueError):
        m.SelectedLeftWorkload(native, **kwargs)


@pytest.mark.parametrize("side,direction", [("left", -1), ("right", 1)])
def test_peer_fixed_rejoin_retains_preparation_and_endpoint_clock(side, direction):
    m = adapter()
    native = peer_workspace()
    task = m.SelectedLeftWorkload(
        native, segment=side + "-peer-fixed", peer_side=side, peer_direction=direction, peer_raw_step=52
    )
    assert task.observe_peer_baseline(peer_baseline_state(native))
    task.elapsed_s = 11.0
    task.admit(20.0)
    now = [20.0]
    producer = m.FrameProducer(task, clock=lambda: now[0])
    producer.workspace, producer.native_cursor = native, dict(native["reference"])
    before = producer.capture({"grant_id": str(uuid4())}, body=m.ZERO_BODY)
    original = task.plan()
    task.freeze()
    task.advance(100.0)
    task.admit(100.0)
    now[0] = 100.0
    producer.native_cursor[task.elbow] += 0.5
    producer.reacquire = True
    after = producer.capture({"grant_id": str(uuid4())}, body=m.ZERO_BODY)
    assert after["target"] == before["target"] and after["body"] == m.ZERO_BODY
    assert after["source"]["sample_seq"] == before["source"]["sample_seq"] + 1
    assert after["source"]["grant_id"] != before["source"]["grant_id"]
    assert task.elapsed_s == 11.0 and task.interrupted_phases == {side + "_endpoint_hold"}
    assert all(
        task.plan()[key] == original[key]
        for key in ("reference", "comparison_start_raw", "endpoint_raw", "phases")
    )


def test_peer_fixed_requires_verified_settings_before_preparation_motion():
    m = adapter()
    native = peer_workspace()
    task = m.SelectedLeftWorkload(
        native, segment="left-peer-fixed", peer_side="left", peer_direction=-1, peer_raw_step=52
    )
    assert m.map_leader_action(task.get_action()) == native["reference"]
    task.elapsed_s = 0.01
    with pytest.raises(RuntimeError, match="baseline"):
        task.get_action()


def test_peer_fixed_retains_actual_achieved_start_without_precision_gate():
    m = adapter()
    native = peer_workspace()
    task = m.SelectedLeftWorkload(
        native, segment="left-peer-fixed", peer_side="left", peer_direction=-1, peer_raw_step=52
    )
    state = peer_baseline_state(native)
    state["evidence"]["measured_positions"] = dict(native["reference"])
    state["evidence"]["measured_positions"][task.elbow] = m.normalized_raw(
        2043, native["calibration"][task.elbow[:-4]]
    )
    assert task.observe_peer_baseline(state)
    task.elapsed_s = 6.01
    assert task.observe_peer_baseline(state)
    observation = task.plan()["comparison_start_observation"]
    assert observation["measured_positions"] == state["evidence"]["measured_positions"]
    assert observation["accepted_host_action"] == state["evidence"]["accepted_host_action"]
    assert observation["commanded_start_raw"] == 2067 and observation["position_qualified"] is False
    state["evidence"]["measured_positions"][task.elbow] += 0.1
    task.elapsed_s = 12.0
    assert task.observe_peer_baseline(state)
    assert task.plan()["comparison_start_observation"] == observation
    assert m.map_leader_action(task.get_action())[task.elbow] == pytest.approx(task.endpoint)


@pytest.mark.parametrize(
    "side,active_p,bench",
    [("left", None, False), ("right", 16, False), ("left", 32, True), ("right", 32, True)],
)
def test_peer_fixed_cli_declares_explicit_interval_and_active_settings(monkeypatch, side, active_p, bench):
    m = adapter()
    seen = []

    async def run(args):
        seen.append(args)
        return {}

    monkeypatch.setattr(m, "run", run)
    argv = [
        "--source",
        "virtual-leaders",
        "--url",
        "unused",
        "--ca",
        "unused",
        "--storage-state",
        "unused",
        "--output",
        "unused",
        "--workload-segment",
        side + "-peer-fixed",
        "--duration-seconds",
        "20",
        "--peer-side",
        side,
        "--peer-direction",
        "-1" if side == "left" else "1",
        "--peer-raw-step",
        "52",
    ]
    if active_p is not None:
        argv += ["--peer-active-p", str(active_p)]
    if bench:
        argv.append("--bench-continuity")
    m.main(argv)
    assert len(seen) == 1 and seen[0].peer_active_p is active_p and seen[0].bench_continuity is bench
    assert seen[0].workload_segment == side + "-peer-fixed" and seen[0].duration_seconds == 20.0
    assert seen[0].interrupt_at_seconds is None and seen[0].next_start_seconds is None


def peer_fixed_trial_state(native, side, proportional):
    state = peer_baseline_state(native)
    proof = state["evidence"]["accepted_host_action"]["teleop_elbow_settings"]
    proof[side]["active"][4] = proportional
    proof["version"] = 2
    proof["trial"] = [side+"-response-p", "b"*64, str(uuid4()), proportional]
    for role in ("left", "right"):
        row = proof[role]
        proof[role] = [row["calibration"], row["original"], row["active"],
                       row["original_read_interval"] + row["active_read_interval"]]
    return state


@pytest.mark.parametrize(
    "side,proportional,trial",
    [
        ("left", 16, False),
        ("right", 16, False),
        ("left", 16, True),
        ("right", 16, True),
        ("left", 32, True),
        ("right", 32, True),
    ],
)
def test_peer_fixed_requires_exact_current_trial_for_selected_temporary_p(side, proportional, trial):
    m = adapter()
    native = peer_workspace()
    key = f"arm_{side}_elbow_flex.pos"
    native["reference"][key] = m.normalized_raw(
        1861 if side == "right" else 2043, native["calibration"][key[:-4]]
    )
    task = m.SelectedLeftWorkload(
        native,
        segment=side + "-peer-fixed",
        peer_side=side,
        peer_direction=-1 if side == "left" else 1,
        peer_raw_step=52,
        peer_active_p=proportional,
    )
    state = peer_fixed_trial_state(native, side, proportional) if trial else peer_baseline_state(native)
    assert task.observe_peer_baseline(state)
    task.elapsed_s = 2.0
    assert m.map_leader_action(task.get_action())[task.elbow] != native["reference"][task.elbow]
    plan = task.plan()
    assert plan["comparison_active_p"] == proportional and plan["baseline_settings_verified"] is True
    if trial:
        assert (
            plan["baseline_settings_binding"]["trial"]
            == state["evidence"]["accepted_host_action"]["teleop_elbow_settings"]["trial"]
        )


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "other_side",
        "segment",
        "original_p",
        "configured_p",
        "hash",
        "source",
        "request_id",
        "original_hash",
        "nan",
        "backwards",
        "future",
        "old_original",
        "early_active",
        "extra",
        "other_active",
        "relative",
        "changed_request",
        "changed_source",
    ],
)
def test_peer_fixed_temporary_proof_never_weakens_settings_or_operation_binding(fault):
    m = adapter()
    native = peer_workspace()
    kwargs = {
        "segment": "left-peer-fixed",
        "peer_side": "left",
        "peer_direction": -1,
        "peer_raw_step": 52,
        "peer_active_p": 32,
    }
    if fault == "relative":
        kwargs.update(segment="left-peer-maintained", peer_active_p=None)
    task = m.SelectedLeftWorkload(native, **kwargs)
    state = peer_fixed_trial_state(native, "left", 32)
    proof = state["evidence"]["accepted_host_action"]["teleop_elbow_settings"]
    trial = proof["trial"]
    if fault in {"changed_request", "changed_source"}:
        assert task.observe_peer_baseline(state)
        if fault == "changed_request":
            trial[2] = str(uuid4())
        else:
            proof["sha256"] = "e" * 64
    elif fault == "missing":
        proof.pop("trial")
    elif fault == "other_side":
        trial[0] = "right-response-p"
    elif fault == "segment":
        trial.append("left-maintained")
    elif fault == "original_p":
        proof["left"][1][4] = 32
    elif fault == "configured_p":
        trial[3] = 16
    elif fault == "hash":
        trial[1] = "not-a-hash"
    elif fault == "source":
        proof["sha256"] = "not-a-source-proof"
    elif fault == "request_id":
        trial[2] = "not-a-uuid"
    elif fault == "original_hash":
        trial[1] = "not-an-original-request-hash"
    elif fault == "nan":
        proof["left"][3][0] = float("nan")
    elif fault == "backwards":
        proof["left"][3][0] = 2.
    elif fault == "future":
        proof["left"][3][-1] = 4.
    elif fault == "old_original":
        proof["left"][3][1] = 2.1
    elif fault == "early_active":
        proof["left"][3][2] = 2.5
    elif fault == "extra":
        trial.append("unknown")
    elif fault == "other_active":
        proof["right"][2][4] = 32
    with pytest.raises(RuntimeError, match="baseline"):
        task.observe_peer_baseline(state)
    task.elapsed_s = 2.01 if fault == "relative" else 0.01
    if fault not in {"changed_request", "changed_source"}:
        with pytest.raises(RuntimeError, match="baseline"):
            task.get_action()


@pytest.mark.parametrize("side,proportional", [("left", 16), ("right", 16), ("left", 32), ("right", 32)])
def test_peer_fixed_v2_trial_fits_existing_gate_with_current_ack(side, proportional):
    m = adapter()
    native = peer_workspace()
    key = f"arm_{side}_elbow_flex.pos"
    native["reference"][key] = m.normalized_raw(2043 if side == "left" else 1861,
                                               native["calibration"][key[:-4]])
    task = m.SelectedLeftWorkload(native, segment=side+"-peer-fixed", peer_side=side,
                                 peer_direction=-1 if side == "left" else 1,
                                 peer_raw_step=52, peer_active_p=proportional)
    state = peer_fixed_trial_state(native, side, proportional)
    assert task.observe_peer_baseline(state)
    original = task.plan()["baseline_settings_binding"]
    task.elapsed_s = 1.
    assert m.map_leader_action(task.get_action())[task.elbow] != native["reference"][task.elbow]
    state["run"]["intent_revision"] += 1
    state["evidence"]["acknowledged_intent_revision"] += 1
    state["evidence"]["accepted_host_action"]["intent_revision"] += 1
    assert task.observe_peer_baseline(state)
    assert task.plan()["baseline_settings_binding"] == original


@pytest.mark.parametrize("fault", ["ordinary", "relative", "extra", "version", "missing_trial", "trial_width",
                                  "trial_side", "trial_hash", "trial_uuid", "trial_p", "row_width", "calibration",
                                  "original_p", "nonselected_p", "backwards", "future", "changed_request", "changed_proof"])
def test_peer_fixed_v2_rejects_foreign_or_malformed_trial(fault):
    m = adapter()
    native = peer_workspace()
    task = m.SelectedLeftWorkload(native, segment="left-peer-fixed", peer_side="left", peer_direction=-1,
                                 peer_raw_step=52, peer_active_p=32)
    state = peer_fixed_trial_state(native, "left", 32)
    proof = state["evidence"]["accepted_host_action"]["teleop_elbow_settings"]
    if fault in {"changed_request", "changed_proof"}:
        assert task.observe_peer_baseline(state)
        if fault == "changed_request":
            proof["trial"][2] = str(uuid4())
        else:
            proof["sha256"] = "e" * 64
    elif fault == "ordinary":
        task = m.SelectedLeftWorkload(native)
    elif fault == "relative":
        task = m.SelectedLeftWorkload(native, segment="left-peer-maintained", peer_side="left", peer_direction=-1, peer_raw_step=52)
    elif fault == "extra":
        proof["extra"] = True
    elif fault == "version":
        proof["version"] = True
    elif fault == "missing_trial":
        del proof["trial"]
    elif fault == "trial_width":
        proof["trial"].append("extra")
    elif fault == "trial_side":
        proof["trial"][0] = "right-response-p"
    elif fault == "trial_hash":
        proof["trial"][1] = "g" * 64
    elif fault == "trial_uuid":
        proof["trial"][2] = "not-a-uuid"
    elif fault == "trial_p":
        proof["trial"][3] = 16
    elif fault == "row_width":
        proof["left"].append("extra")
    elif fault == "calibration":
        proof["left"][0][2] += 1
    elif fault == "original_p":
        proof["left"][1][4] = 32
    elif fault == "nonselected_p":
        proof["right"][2][4] = 32
    elif fault == "backwards":
        proof["left"][3] = [2., 1., 2., 3.]
    elif fault == "future":
        proof["left"][3][-1] = 99.
    if fault == "ordinary":
        assert task.observe_peer_baseline(state) is False
    else:
        with pytest.raises(RuntimeError, match="baseline"):
            task.observe_peer_baseline(state)


@pytest.mark.parametrize("proportional", [16, 32])
def test_peer_fixed_v1_rejects_obsolete_trial_wire_shape(proportional):
    m = adapter()
    native = peer_workspace()
    task = m.SelectedLeftWorkload(native, segment="left-peer-fixed", peer_side="left", peer_direction=-1,
                                 peer_raw_step=52, peer_active_p=proportional)
    state = peer_baseline_state(native)
    proof = state["evidence"]["accepted_host_action"]["teleop_elbow_settings"]
    proof["left"]["active"][4] = proportional
    proof["trial"] = ["left-response-p", "b"*64, "c"*40, str(uuid4()), "left-peer-fixed", 16,
                       proportional, "d"*64, 1.1, 1.2]
    with pytest.raises(RuntimeError, match="baseline"):
        task.observe_peer_baseline(state)


@pytest.mark.parametrize("selected_p", [16, 32])
def test_peer_fixed_v1_cannot_credit_active_p32_without_trial(selected_p):
    m = adapter()
    native = peer_workspace()
    task = m.SelectedLeftWorkload(native, segment="left-peer-fixed", peer_side="left", peer_direction=-1,
                                 peer_raw_step=52, peer_active_p=selected_p)
    state = peer_baseline_state(native)
    state["evidence"]["accepted_host_action"]["teleop_elbow_settings"]["left"]["active"][4] = 32
    with pytest.raises(RuntimeError, match="baseline"):
        task.observe_peer_baseline(state)
