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
    owned_id, foreign_id = str(uuid4()), str(uuid4())
    instances = []

    class Session:
        def __init__(self, *args):
            self.snapshot = {"run": None, "evidence": {}}
            self.sequence = 0
            self.generation = "control"
            self.starts = self.closed = self.saw_foreign = 0
            self.stops = []
            self.start_requests = []
            self.cleanup_reads = 0
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
                "run": {
                    "run_id": run_id,
                    "recipe": "physical-live-teleop",
                    "native_live_at": 1.0,
                    "deadline": 301.0,
                    "status": "running",
                    "uncertain": False,
                    "cleanup": None,
                    "live_input": {
                        "workspace": w,
                        "cursor": copy.deepcopy(w["reference"]),
                        "required_view": {"qualified": True},
                    },
                },
                "evidence": {
                    "active_acknowledged": True,
                    "feedback": True,
                    "native_ack": True,
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
            return {"run_id": owned_id}

        async def resume(self):
            assert self.snapshot["run"]["run_id"] == owned_id

        async def command(self, payload):
            if payload["op"] == "grant":
                return {"accepted": True, "grant_id": str(uuid4()), "expires": 99.0}
            assert payload["op"] == "input"
            self.snapshot["run"]["live_input"].update(
                last_input_seq=payload["seq"], source=payload["source"], body=payload["body"]
            )
            self.snapshot["evidence"]["measured_positions"] = payload["target"]
            return {"accepted": True}

        async def get(self, op):
            assert op == "state"
            self.cleanup_reads += 1
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
        source="usb-leaders" if failure else "virtual-leaders",
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
    if failure and failure != "start_lost":
        with pytest.raises(Exception, match="injected|cleanup failed|native terminal"):
            asyncio.run(m.run(args))
        import json

        summary = json.loads((tmp_path / "evidence" / "summary.json").read_text())
        assert sources[0].closed == 1
        if failure != "start_unknown":
            assert summary["owned_run_id"] == owned_id
        assert "injected" in str(summary) or failure == "terminal_fault"
        if failure == "stop":
            assert summary["native_cleanup_uncertain"] is True
        if failure == "start_unknown":
            assert summary["start_outcome_uncertain"] is True and summary["start_operation_id"]
        if failure == "terminal_fault":
            assert summary["native_cleanup_uncertain"] is False and summary["cleanup_errors"] == []
            assert summary["terminal_failure"]["cause"] == "retained genuine native fault"
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
            else [owned_id]
        )
        and session.closed == 1
    )
    assert session.saw_foreign == (0 if failure == "start_unknown" else 1)
    if failure not in {"start_unknown", *prelive}:
        assert summary["run_id"] == owned_id
    if failure not in {"stop", "start_unknown", "terminal_fault", *prelive}:
        assert summary["native_terminal"]["run"]["status"] == "stopped"
    if failure in {"start_lost", "start_unknown"}:
        assert len(session.start_requests) == 2 and session.start_requests[0] == session.start_requests[1]
        assert summary["start_operation_id"] == session.start_requests[0]["operation_id"]
    assert summary["producer_program_complete"] is False
    if not failure:
        assert summary["active_progress_s"] == 0


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
