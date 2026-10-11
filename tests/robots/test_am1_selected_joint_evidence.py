"""Selected AM1 evidence through real control/encoding and injected serial bytes."""

import copy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
import scservo_sdk as scs

from lerobot.motors import Motor, MotorCalibration, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus
from lerobot.robots.alohamini import alohamini_host as host
from lerobot.robots.alohamini.alohamini import AlohaMini
from tests.robots.test_alohamini_lift_motor_feedback import ByteStreamPort, status

ELBOW = "arm_left_elbow_flex"
KEY = ELBOW + ".pos"
EVIDENCE_KEY = "_am1_selected_joint_evidence"


def context(**updates):
    return {
        "profile": "LeftElbowCompare",
        "progress_s": 0.0,
        "phase": "baseline",
        "cycle": 0,
        "reference": 0.0,
        "endpoint_target": 3.0,
        "requested_target": 0.0,
        "sample_key": "baseline",
        **updates,
    }


def command(identity, metadata=None, *, sequence=1, mode="active", epoch=0):
    metadata = context() if metadata is None else metadata
    return {
        KEY: metadata["requested_target"],
        "x.vel": 0,
        "y.vel": 0,
        "theta.vel": 0,
        "lift_axis.vel": 0,
        EVIDENCE_KEY: metadata,
        host.AM1_LOCAL_CONTROL_KEY: {
            "version": 1,
            "mode": mode,
            "epoch": epoch,
            "intent_revision": 0,
            "action_sequence": sequence,
            **identity,
        },
    }


def robot(monkeypatch, *, enabled=True, position=2000, current=2):
    motors = {ELBOW: Motor(3, "sts3215", MotorNormMode.RANGE_M100_100)}
    motors.update(
        {
            name: Motor(i, "sts3215", MotorNormMode.RANGE_M100_100)
            for i, name in enumerate(("base_left_wheel", "base_back_wheel", "base_right_wheel"), 8)
        }
    )
    bus = FeetechMotorsBus(
        "unused-selected-test",
        motors,
        {name: MotorCalibration(m.id, 0, 1845, 1000, 3000) for name, m in motors.items()},
    )
    bus.port_handler.is_open = True
    reads, writes = [], []

    def read(address, length, ids, **kwargs):
        reads.append((address, length, tuple(ids)))
        value = position if address == 56 else (current if address == 69 else 0)
        return dict.fromkeys(ids, value), 0

    def write(address, length, values, **kwargs):
        writes.append((address, length, dict(values)))
        return 0

    monkeypatch.setattr(bus, "_sync_read", read)
    monkeypatch.setattr(bus, "_sync_write", write)
    obj = AlohaMini.__new__(AlohaMini)
    obj.id = "selected-test"
    obj.config = SimpleNamespace(robot_model="alohamini1", max_relative_target=20.0)
    obj.left_bus, obj.right_bus = bus, None
    obj.left_arm_motors, obj.right_arm_motors = [ELBOW], []
    obj.base_motors = list(motors)[1:]
    obj.cameras, obj.logs = {}, {}
    obj.wheel_radius, obj.base_radius = 0.05, 0.125
    obj.lift = SimpleNamespace(
        apply_action=lambda _: None, contribute_observation=lambda _: None, is_connected=True
    )
    obj._gripper_current_limit_ma, obj._joint_current_limit_ma = 500.0, 1800.0
    obj._gripper_release_margin = obj._joint_release_margin = 1.0
    obj._gripper_hold_goal, obj._gripper_hold_direction = {}, {}
    obj._joint_hold_goal, obj._joint_hold_direction = {}, {}
    obj._gripper_open_direction, obj._gripper_hold_close_step = {}, 3
    obj._last_currents_log_t, obj._overcurrent_count, obj._overcurrent_trip_n = 0.0, {}, 3
    obj._am1_selected_joint_evidence_enabled = enabled
    return obj, reads, writes


def sampler():
    cls = getattr(host, "AM1SelectedJointEvidence", None)
    assert cls is not None, "Selected same-owner diagnostic is missing"
    rows = []
    return cls(enabled=True, emit=rows.append), rows


def binding():
    return {"run_id": str(uuid4()), "host_incarnation": str(uuid4())}


def observe(obj, control):
    observation = obj.get_observation()
    import time

    control.annotate(observation, acquired_at=time.monotonic())
    return observation


def group_payload(*, goal=2000, present=2000, current=2, torque=1, mode=0, servo_status=0):
    data = bytearray(38)
    data[0], data[7], data[29], data[30], data[32] = mode, torque, 119, 30, servo_status
    for address, value in (
        (42, goal),
        (44, 123),
        (46, 32775),
        (48, 1000),
        (56, present),
        (60, 1027),
        (69, current),
    ):
        offset = address - 33
        data[offset : offset + 2] = value.to_bytes(2, "little")
    return bytes(data)


def attach_port(obj, responses):
    port = ByteStreamPort(responses)
    obj.left_bus.port_handler, obj.left_bus.packet_handler = port, scs.PacketHandler(0)
    return port


def test_selected_context_is_stripped_and_sparse_read_is_same_owner_once(monkeypatch):
    obj, reads, writes = robot(monkeypatch)
    evidence, rows = sampler()
    identity = binding()
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    port = attach_port(obj, [status(group_payload(), motor_id=3)])
    original = command(identity)
    assert control.apply(obj, original)
    observation = observe(obj, control)
    evidence.report(obj, control, observation)
    evidence.report(obj, control, observation)
    assert original[EVIDENCE_KEY] == context()
    assert len(reads) == 6  # three ordinary action reads + ordinary position/base/current.
    assert (42, 2, {3: 2000}) in writes
    sparse = [row for row in rows if row["kind"] == "sparse_readback"]
    assert len(sparse) == 1 and len(port.writes) == 1
    row = sparse[0]
    assert row["decoded"]["goal_position_raw"] == row["decoded"]["present_position_raw"] == 2000
    assert row["decoded"]["present_load_raw"] == -3
    assert row["decoded"]["present_current_ma"] == 13.0
    assert row["decoded"]["goal_time_raw"] == 123
    assert row["decoded"]["goal_velocity_raw"] == -7
    assert row["decoded"]["torque_limit_raw"] == 1000
    assert row["motor"]["id"] == 3 and row["motor"]["bus"] == "left"
    assert row["trace"]["request_bytes"] == [255, 255, 254, 5, 130, 33, 38, 3, 48]
    assert row["trace"]["response_bytes"] == list(status(group_payload(), motor_id=3))
    assert row["action"]["write_acknowledged"] is False
    assert row["action"]["encoded_goal_word"] == 2000
    assert row["action"]["encoded_goal_bytes"] == [208, 7]
    native = [row for row in rows if row["kind"] == "native_trace"]
    assert len(native) == 1
    assert native[0]["position"]["raw"] == 2000
    assert native[0]["current"]["raw"] == 2


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m.update(reference=1.0),
        lambda m: m.update(endpoint_target=4.0),
        lambda m: m.update(requested_target=float("nan")),
        lambda m: m.update(cycle=True),
        lambda m: m.update(phase="bogus"),
        lambda m: m.update(progress_s=19.0),
        lambda m: m.update(sample_key="cycle1_endpoint"),
        lambda m: m.update(extra=0),
    ],
)
def test_invalid_selected_context_never_reaches_actuation(monkeypatch, mutate):
    obj, reads, writes = robot(monkeypatch)
    evidence, _ = sampler()
    identity = binding()
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    assert control.apply(obj, command(identity))
    before = copy.deepcopy(writes)
    metadata = context()
    mutate(metadata)
    with pytest.raises(RuntimeError):
        control.apply(obj, command(identity, metadata, sequence=2))
    assert writes == before


def test_current_limited_wire_goal_is_evidence_not_a_requested_goal(monkeypatch):
    obj, _, writes = robot(monkeypatch, current=300)
    evidence, rows = sampler()
    identity = binding()
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    metadata = context(progress_s=3.0, phase="outbound", cycle=1, requested_target=3.0, sample_key=None)
    assert control.apply(obj, command(identity, metadata))
    evidence.report(obj, control, observe(obj, control))
    row = next(row for row in rows if row["kind"] == "native_trace")
    assert row["action"]["requested"] == row["action"]["relative_limited"] == 3.0
    assert row["action"]["current_limited"] == row["action"]["final_normalized"] == 0.0
    assert row["action"]["expected_goal_raw"] == 2000
    assert (42, 2, {3: 2000}) in writes


@pytest.mark.parametrize(
    "bad", ["timeout", "checksum", "servo", "mode", "torque", "status", "wrong_id", "pending", "tx_failed"]
)
def test_sparse_unavailable_is_not_retried_and_actual_faults_propagate(monkeypatch, bad):
    obj, _, _ = robot(monkeypatch)
    evidence, rows = sampler()
    identity = binding()
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    payload = group_payload(
        mode=1 if bad == "mode" else 0,
        torque=0 if bad == "torque" else 1,
        servo_status=8 if bad == "status" else 0,
    )
    reply = bytearray(
        status(payload, motor_id=4 if bad == "wrong_id" else 3, error=8 if bad == "servo" else 0)
    )
    if bad == "checksum":
        reply[-1] ^= 1
    port = attach_port(obj, [] if bad == "timeout" else [bytes(reply)])
    if bad == "pending":
        port.incoming.extend(b"stale input")
    if bad == "tx_failed":
        monkeypatch.setattr(port, "writePort", lambda _: 0)
    control.apply(obj, command(identity))
    observation = observe(obj, control)
    if bad == "timeout":
        evidence.report(obj, control, observation)
    else:
        with pytest.raises(RuntimeError):
            evidence.report(obj, control, observation)
    evidence.report(obj, control, observation)
    sparse = [row for row in rows if row["kind"] == "sparse_readback"]
    assert len(sparse) == 1 and len(port.writes) == (0 if bad in ("pending", "tx_failed") else 1)
    assert sparse[0]["diagnostic_available"] is False
    assert sparse[0]["read_completed_at_monotonic_s"] >= sparse[0]["read_started_at_monotonic_s"]


def test_evidence_flag_and_protected_owner_are_required_before_actuation(monkeypatch):
    obj, _, writes = robot(monkeypatch, enabled=False)
    cls = getattr(host, "AM1SelectedJointEvidence", None)
    assert cls is not None, "Selected same-owner diagnostic is missing"
    identity = binding()
    for evidence, control_binding in ((cls(enabled=False), identity), (cls(enabled=True), {})):
        control = host.AM1LocalControl(**control_binding, selected_evidence=evidence)
        with pytest.raises(RuntimeError):
            control.apply(obj, command(identity))
    assert not writes


def test_reference_and_progress_do_not_reset_and_pause_suppresses_diagnostic_io(monkeypatch):
    obj, _, _ = robot(monkeypatch)
    evidence, rows = sampler()
    identity = binding()
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    port = attach_port(obj, [status(group_payload(), motor_id=3)])
    control.apply(obj, command(identity))
    evidence.report(obj, control, observe(obj, control))
    # The actual pause mechanism keeps ordinary zero/hold behavior.
    obj.stop_motion, obj.hold_follower_arms = lambda: None, lambda: None
    paused = command(identity, sequence=2, mode="pause", epoch=1)
    paused.pop(EVIDENCE_KEY)
    control.apply(obj, paused)
    evidence.report(obj, control, observe(obj, control))
    assert len(port.writes) == 1
    assert len([r for r in rows if r["kind"] == "native_trace"]) == 1


def test_five_phase_snapshots_use_existing_owner_without_open_close_or_other_ids(monkeypatch):
    obj, _, _ = robot(monkeypatch)
    evidence, rows = sampler()
    identity = binding()
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    port = attach_port(obj, [status(group_payload(), motor_id=3) for _ in range(5)])
    from lerobot.robots.alohamini.lift_motor_feedback import DirectSdkTransport

    def forbidden(*_args, **_kwargs):
        pytest.fail("Selected evidence cannot open/close/write the existing bus")

    for method in ("open", "close", "write_register"):
        monkeypatch.setattr(DirectSdkTransport, method, forbidden)
    phases = (
        ("baseline", 0, 0, 0),
        ("endpoint", 1, 3, 4),
        ("origin", 1, 0, 8),
        ("endpoint", 2, 3, 12),
        ("origin", 2, 0, 16),
        ("final", 2, 0, 18),
    )
    for sequence, (phase, cycle, target, progress) in enumerate(phases, 1):
        key = "baseline" if phase == "baseline" else f"cycle{cycle}_{phase}" if phase != "final" else None
        metadata = context(
            phase=phase, cycle=cycle, requested_target=target, progress_s=progress, sample_key=key
        )
        control.apply(obj, command(identity, metadata, sequence=sequence))
        observation = observe(obj, control)
        evidence.report(obj, control, observation)
        evidence.report(obj, control, observation)
    assert len(port.writes) == 5
    assert {row["sample_key"] for row in rows if row["kind"] == "sparse_readback"} == {
        "baseline",
        "cycle1_endpoint",
        "cycle1_origin",
        "cycle2_endpoint",
        "cycle2_origin",
    }
    assert all(packet == port.writes[0] for packet in port.writes)
    assert len([row for row in rows if row["kind"] == "native_trace"]) == 6


def test_missing_ordinary_coverage_is_explicit_unavailable_and_foreign_feedback_is_not_relabelled(
    monkeypatch,
):
    obj, _, _ = robot(monkeypatch)
    evidence, rows = sampler()
    identity = binding()
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    port = attach_port(obj, [])
    control.apply(obj, command(identity))
    observation = observe(obj, control)
    foreign = copy.deepcopy(observation)
    foreign[host.AM1_LOCAL_FEEDBACK_KEY]["host_incarnation"] = str(uuid4())
    evidence.report(obj, control, foreign)
    assert not rows and not port.writes
    del obj._am1_selected_joint_current
    evidence.report(obj, control, observation)
    assert not port.writes
    sparse = next(row for row in rows if row["kind"] == "sparse_readback")
    assert sparse["unavailable_reason"] == "ordinary selected acquisition unavailable"
    assert sparse["diagnostic_available"] is False


@pytest.mark.parametrize("model", ["alohamini1", "alohamini2"])
def test_unmarked_ordinary_actions_and_observation_do_not_add_selected_cache_or_io(monkeypatch, model):
    obj, reads, _ = robot(monkeypatch)
    obj.config.robot_model = model
    control = host.AM1LocalControl()
    ordinary = command(binding())
    ordinary.pop(EVIDENCE_KEY)
    ordinary.pop(host.AM1_LOCAL_CONTROL_KEY)
    control.apply(obj, ordinary)
    obj.get_observation()
    assert len(reads) == 6
    assert not hasattr(obj, "_am1_selected_joint_action")
    assert not hasattr(obj, "_am1_selected_joint_position")
    assert not hasattr(obj, "_am1_selected_joint_current")


def test_native_loop_defers_sparse_read_until_after_ordinary_response_and_counts_its_time(monkeypatch):
    from lerobot.robots.alohamini import alohamini as robot_module

    obj, _, _ = robot(monkeypatch)
    identity, events, rows = binding(), [], []
    now = SimpleNamespace(value=10.0)
    clock = SimpleNamespace(
        monotonic=lambda: now.value,
        perf_counter=lambda: now.value,
        time_ns=lambda: int(now.value * 1e9),
        sleep=lambda duration: setattr(now, "value", now.value + duration),
    )
    port = attach_port(obj, [status(group_payload(), motor_id=3)])
    real_write = port.writePort

    def diagnostic_write(packet):
        events.append("diagnostic")
        now.value += 0.04
        return real_write(packet)

    port.writePort = diagnostic_write
    cls = host.AM1SelectedJointEvidence
    monkeypatch.setattr(
        host, "AM1SelectedJointEvidence", lambda **kw: cls(**kw, clock=clock.monotonic, emit=rows.append)
    )
    monkeypatch.setenv("AM1_SELECTED_JOINT_EVIDENCE", "1")
    monkeypatch.setattr(host, "time", clock)
    monkeypatch.setattr(robot_module, "time", clock)
    monkeypatch.setattr(host, "AlohaMini", lambda _: obj)
    monkeypatch.setattr(host, "connect_robot", lambda *a, **kw: None)
    obj.disconnect = lambda **kw: events.append("cleanup")

    class Socket:
        def recv_string(self, flags):
            return json.dumps(command(identity))

        def recv_multipart(self, flags):
            return [b"client", b"original-token"]

        def send_multipart(self, parts, flags):
            events.append("response")

    class Owner:
        watchdog_timeout_ms, connection_time_s, max_loop_freq_hz = 1000, 0.03, 30

        def __init__(self, _):
            self.zmq_cmd_socket = self.zmq_observation_socket = Socket()

        def disconnect(self):
            events.append("socket-close")

    monkeypatch.setattr(host, "AlohaMiniHost", Owner)
    args = host.make_parser().parse_args(["--no_cameras", "--skip_lift_home"])
    args.am1_run_id, args.am1_host_incarnation = identity["run_id"], identity["host_incarnation"]
    host._run_host(args)
    assert events == ["response", "diagnostic", "cleanup", "socket-close"]
    assert now.value == pytest.approx(10.04)  # no full-period sleep added after sparse I/O
    sparse = next(row for row in rows if row["kind"] == "sparse_readback")
    assert sparse["read_started_at_monotonic_s"] == 10.0
    assert sparse["read_completed_at_monotonic_s"] == pytest.approx(10.04)


def test_drive_mode_and_homing_offset_are_not_applied_twice(monkeypatch):
    obj, _, writes = robot(monkeypatch)
    obj.left_bus.calibration[ELBOW].drive_mode = 1
    evidence, rows = sampler()
    identity = binding()
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    port = attach_port(obj, [status(group_payload(goal=1970, present=1970), motor_id=3)])
    metadata = context(
        progress_s=4, phase="endpoint", cycle=1, requested_target=3.0, sample_key="cycle1_endpoint"
    )
    control.apply(obj, command(identity, metadata))
    evidence.report(obj, control, observe(obj, control))
    sparse = next(row for row in rows if row["kind"] == "sparse_readback")
    assert (42, 2, {3: 1970}) in writes and len(port.writes) == 1
    assert sparse["action"]["expected_goal_raw"] == 1970
    assert sparse["decoded"]["present_position_raw"] == 1970
    assert sparse["decoded"]["present_position_normalized"] == pytest.approx(3.0)
    assert sparse["motor"]["calibration"]["homing_offset"] == 1845
    assert sparse["goal_matches_expected_raw"] is True


def test_actual_send_failure_retains_original_cause_and_failed_wire_attempt(monkeypatch):
    obj, _, _ = robot(monkeypatch)
    evidence, rows = sampler()
    identity = binding()
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    original = ConnectionError("original failed position write")

    def fail_write(*args, **kwargs):
        raise original

    monkeypatch.setattr(obj.left_bus, "_sync_write", fail_write)
    with pytest.raises(ConnectionError) as caught:
        control.apply(obj, command(identity))
    assert caught.value is original and not rows
    assert control.accepted_action_sequence == -1
    note = next(note for note in original.__notes__ if note.startswith("AM1 selected joint action context: "))
    actual = json.loads(note.removeprefix("AM1 selected joint action context: "))
    assert actual["expected_goal_raw"] == actual["encoded_goal_word"] == 2000
    assert actual["sync_write_returned"] is False and actual["write_acknowledged"] is False
    assert actual["write_completed_at_monotonic_s"] >= actual["write_started_at_monotonic_s"]


def test_optional_sparse_coverage_refuses_ordinary_samples_older_than_half_second(monkeypatch):
    obj, _, _ = robot(monkeypatch)
    evidence, rows = sampler()
    identity = binding()
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    port = attach_port(obj, [status(group_payload(), motor_id=3)])
    control.apply(obj, command(identity))
    observation = observe(obj, control)
    evidence.clock = lambda: obj._am1_selected_joint_current["read_completed_at_monotonic_s"] + 0.6
    evidence.report(obj, control, observation)
    assert not port.writes
    sparse = next(row for row in rows if row["kind"] == "sparse_readback")
    assert sparse["diagnostic_available"] is False
    assert sparse["unavailable_reason"] == "ordinary selected acquisition unavailable"
    assert control.state == "active" and control.accepted_action_sequence == 1
