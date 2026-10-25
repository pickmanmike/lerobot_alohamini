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
    # This fixture replaces startup/device IO; explicitly declare its accepted holds.
    from examples.alohamini.am1_finite_task import JOINT_KEYS
    obj.fixture_acknowledged_arm_commands = dict.fromkeys(JOINT_KEYS, 0.0)
    obj.am1_live_arm_commands = lambda identity: dict(obj.fixture_acknowledged_arm_commands)
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


def group_payload(
    *,
    goal=2000,
    present=2000,
    current=2,
    torque=1,
    mode=0,
    servo_status=0,
    goal_time=123,
    goal_velocity=32775,
    acceleration=0,
    lock=0,
):
    data = bytearray(38)
    data[0], data[7], data[8], data[22], data[29], data[30], data[32] = (
        mode,
        torque,
        acceleration,
        lock,
        119,
        30,
        servo_status,
    )
    for address, value in (
        (42, goal),
        (44, goal_time),
        (46, goal_velocity),
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


def visible_identity():
    return {**binding(), "recipe": "physical-left-elbow-positive-control"}


def visible_register_packet(
    *,
    torque=0,
    integral=0,
    mode=0,
    goal=2000,
    present=2000,
    current=0,
    acceleration=77,
    range_min=1000,
    range_max=3000,
):
    data = bytearray(71)
    fields = {
        0: (3, 1),
        1: (10, 1),
        3: (777, 2),
        5: (3, 1),
        7: (6, 1),
        9: (range_min, 2),
        11: (range_max, 2),
        18: (17, 1),
        21: (16, 1),
        22: (32, 1),
        23: (integral, 1),
        24: (16, 1),
        25: (0, 1),
        31: (1845, 2),
        33: (mode, 1),
        40: (torque, 1),
        41: (acceleration, 1),
        42: (goal, 2),
        44: (0, 2),
        46: (0, 2),
        48: (1000, 2),
        55: (torque, 1),
        56: (present, 2),
        62: (119, 1),
        63: (30, 1),
        65: (0, 1),
        69: (current, 2),
    }
    for address, (value, width) in fields.items():
        data[address : address + width] = value.to_bytes(width, "little")
    return bytes(data)


def visible_robot(monkeypatch, packets=()):
    obj, reads, writes = robot(monkeypatch)
    evidence, rows = sampler()
    identity = visible_identity()
    obj._am1_visible_control_binding = identity
    obj._am1_visible_control_evidence = evidence
    port = attach_port(obj, packets)
    return obj, evidence, rows, identity, port, reads, writes


def test_visible_readonly_settings_proof_keeps_actual_85_and_packets(monkeypatch):
    obj, evidence, rows, ident, port, reads, writes = visible_robot(
        monkeypatch,
        [
            status(visible_register_packet(), motor_id=3),
            status(bytes([91]), motor_id=3),
            status(visible_register_packet(torque=1), motor_id=3),
            status(bytes([91]), motor_id=3),
        ],
    )
    assert callable(getattr(evidence, "prepare_visible", None)), (
        "Visible04 pre-configure read-only proof missing"
    )
    evidence.prepare_visible(obj, ident)
    evidence.verify_visible_active(obj)
    proof = evidence.visible_control_proof(obj)
    assert proof["binding"] == ident and proof["unchanged"] is True
    assert proof["before"]["registers"]["I_Coefficient"] == proof["active"]["registers"]["I_Coefficient"] == 0
    assert (
        proof["before"]["registers"]["Maximum_Acceleration"]
        == proof["active"]["registers"]["Maximum_Acceleration"]
        == 91
    )
    assert proof["before"]["registers"]["Acceleration"] == 77
    assert proof["before"]["individual_bytes"] == {"24": 16, "25": 0}
    assert [(p[5], p[6], p[7]) for p in port.writes] == [(0, 71, 3), (85, 1, 3), (0, 71, 3), (85, 1, 3)]
    assert not reads and not writes
    assert len(json.dumps(proof)) < 4096
    assert len(rows) == 2 and all(row["kind"] == "visible_settings" for row in rows)


@pytest.mark.parametrize(
    "bad",
    [
        "integral",
        "mode",
        "foreign_binding",
        "timeout",
        "changed_acceleration",
        "changed_max_acceleration",
        "current_cap",
    ],
)
def test_visible_settings_mismatch_never_becomes_qualified(monkeypatch, bad):
    before = visible_register_packet(integral=1 if bad == "integral" else 0, mode=1 if bad == "mode" else 0)
    active = visible_register_packet(
        torque=1,
        acceleration=78 if bad == "changed_acceleration" else 77,
        current=16 if bad == "current_cap" else 0,
    )
    packets = [
        status(before, motor_id=3),
        status(bytes([91]), motor_id=3),
        status(active, motor_id=3),
        status(bytes([92 if bad == "changed_max_acceleration" else 91]), motor_id=3),
    ]
    if bad == "timeout":
        packets = [b""]
    obj, evidence, rows, ident, port, reads, writes = visible_robot(monkeypatch, packets)
    if bad == "foreign_binding":
        ident = dict(ident, run_id=str(uuid4()))
    assert callable(getattr(evidence, "prepare_visible", None)), (
        "Visible04 pre-configure read-only proof missing"
    )
    with pytest.raises((RuntimeError, ValueError)):
        evidence.prepare_visible(obj, ident)
        evidence.verify_visible_active(obj)
    assert not reads and not writes


def test_visible_host_recipe_binding_has_no_integral_trial(monkeypatch):
    monkeypatch.setenv("AM1_VISIBLE_CONTROL_RECIPE", "physical-left-elbow-positive-control")
    monkeypatch.setenv("AM1_SCRIPTED_PREPARE", "1")
    monkeypatch.setenv("AM1_SELECTED_JOINT_EVIDENCE", "1")
    args = SimpleNamespace(
        robot_model="alohamini1",
        no_follower=False,
        no_cameras=True,
        am1_run_id=str(uuid4()),
        am1_host_incarnation=str(uuid4()),
        am1_protected_run_directory="/private/run",
    )
    assert callable(getattr(host, "visible_control_binding", None)), "Visible04 exact recipe binding missing"
    actual = host.visible_control_binding(args)
    assert actual == {
        "run_id": args.am1_run_id,
        "host_incarnation": args.am1_host_incarnation,
        "recipe": "physical-left-elbow-positive-control",
    }
    monkeypatch.setenv("AM1_ELBOW_RECOVERY", "I1")
    with pytest.raises(ValueError):
        host.visible_control_binding(args)


def test_visible_cache_is_continuous_before_diagnostic_context(monkeypatch):
    obj, reads, writes = robot(monkeypatch)
    obj._am1_visible_control_binding = visible_identity()
    obj._am1_visible_control_evidence = sampler()[0]
    obj.get_observation()
    assert getattr(obj, "_am1_selected_joint_position", None) is not None
    assert getattr(obj, "_am1_selected_joint_current", None) is not None


def visible_guard(monkeypatch, *, current_raw=0, initial_raw=2000, range_min=1000, range_max=3000):
    obj, evidence, rows, ident, port, reads, writes = visible_robot(
        monkeypatch,
        [
            status(
                visible_register_packet(
                    present=initial_raw, goal=initial_raw, range_min=range_min, range_max=range_max
                ),
                motor_id=3,
            ),
            status(bytes([91]), motor_id=3),
            status(
                visible_register_packet(
                    torque=1, present=initial_raw, goal=initial_raw, range_min=range_min, range_max=range_max
                ),
                motor_id=3,
            ),
            status(bytes([91]), motor_id=3),
        ],
    )
    obj.left_bus.calibration[ELBOW].range_min = range_min
    obj.left_bus.calibration[ELBOW].range_max = range_max
    clock = [1.0]
    evidence.clock = lambda: clock[0]
    evidence.prepare_visible(obj, ident)
    evidence.verify_visible_active(obj)
    control = SimpleNamespace(
        binding={k: ident[k] for k in ("run_id", "host_incarnation")}, state="paused", epoch=1
    )

    def sample(raw=initial_raw, current=current_raw):
        obj._am1_selected_joint_position = {
            "raw": raw,
            "read_started_at_monotonic_s": clock[0] - 0.01,
            "read_completed_at_monotonic_s": clock[0],
        }
        obj._am1_selected_joint_current = {
            "raw": current,
            "ma": current * 6.5,
            "read_started_at_monotonic_s": clock[0] - 0.01,
            "read_completed_at_monotonic_s": clock[0],
        }

    sample()
    return obj, evidence, control, clock, sample, port


def test_visible_native_paused_cap_has_no_extra_bus_read(monkeypatch):
    obj, evidence, control, clock, sample, port = visible_guard(monkeypatch, current_raw=16)
    assert callable(getattr(evidence, "guard_visible", None)), "Visible04 native guard missing"
    count = len(port.writes)
    with pytest.raises(RuntimeError, match="effort"):
        evidence.guard_recovery(obj, control, {})
    assert len(port.writes) == count


def test_visible_native_live_deadline_and_first_clock_do_not_reset(monkeypatch):
    obj, evidence, control, clock, sample, port = visible_guard(monkeypatch)
    assert callable(getattr(evidence, "guard_visible", None)), "Visible04 native guard missing"
    evidence.guard_recovery(obj, control, {})
    obj._am1_selected_joint_current["read_started_at_monotonic_s"] = 0.1
    with pytest.raises(RuntimeError, match="acquisition"):
        evidence.guard_recovery(obj, control, {})
    clock[0] = 61
    sample()
    with pytest.raises(RuntimeError, match="60-second"):
        evidence.guard_recovery(obj, control, {})


def test_visible_wrong_direction_stays_enforced_during_changing_ramp_goals(monkeypatch):
    obj, evidence, control, clock, sample, port = visible_guard(monkeypatch)
    assert callable(getattr(evidence, "guard_visible", None)), "Visible04 native guard missing"
    evidence.guard_recovery(obj, control, {})
    control.state = "active"
    evidence.context = {
        "phase": "outbound",
        "cycle": 1,
        "reference": 0.0,
        "endpoint_target": 10.0,
        "admitted_reference": 0.0,
    }
    obj._am1_selected_joint_action = {"expected_goal_raw": 2005}
    clock[0] = 1.1
    sample(2005)
    evidence.guard_recovery(obj, control, {})
    obj._am1_selected_joint_action = {"expected_goal_raw": 2006}
    clock[0] = 1.2
    sample(2002)
    with pytest.raises(RuntimeError, match="direction"):
        evidence.guard_recovery(obj, control, {})


@pytest.mark.parametrize(
    "currents,refused", [([0, 1, 2, 3], True), ([0, 1, 0, 2, 0, 3], False), ([0, 1, 1, 2, 2, 3], True)]
)
def test_visible_static_endpoint_actual_current_rise_has_chronological_drop_reset(
    monkeypatch, currents, refused
):
    obj, evidence, control, clock, sample, port = visible_guard(monkeypatch)
    assert callable(getattr(evidence, "guard_visible", None)), "Visible04 native guard missing"
    evidence.guard_recovery(obj, control, {})
    control.state = "active"
    evidence.context = {
        "phase": "endpoint",
        "cycle": 1,
        "reference": 0.0,
        "endpoint_target": 10.0,
        "admitted_reference": 0.0,
    }
    obj._am1_selected_joint_action = {"expected_goal_raw": 2100}

    def run():
        for current in currents:
            clock[0] += 0.1
            sample(2000, current)
            evidence.guard_recovery(obj, control, {})

    if refused:
        with pytest.raises(RuntimeError, match="adverse"):
            run()
    else:
        run()


def test_visible_fixed_goal_growing_crossings_are_native_instability(monkeypatch):
    obj, evidence, control, clock, sample, port = visible_guard(monkeypatch)
    evidence.guard_recovery(obj, control, {})
    control.state = "active"
    evidence.context = {
        "phase": "baseline",
        "cycle": 0,
        "reference": 0.0,
        "endpoint_target": 10.0,
        "admitted_reference": 0.0,
    }
    obj._am1_selected_joint_action = {"expected_goal_raw": 2000}
    with pytest.raises(RuntimeError, match="instability"):
        for raw in [2001, 1998, 2003, 1996]:
            clock[0] += 0.1
            sample(raw)
            evidence.guard_recovery(obj, control, {})


def test_visible_planned_goal_changes_do_not_count_as_growing_crossings(monkeypatch):
    obj, evidence, control, clock, sample, port = visible_guard(monkeypatch)
    evidence.guard_recovery(obj, control, {})
    control.state = "active"
    evidence.context = {
        "phase": "outbound",
        "cycle": 1,
        "reference": 0.0,
        "endpoint_target": 10.0,
        "admitted_reference": 0.0,
    }
    for raw, goal in [(2000, 2001), (2005, 2003), (2006, 2009), (2013, 2009)]:
        obj._am1_selected_joint_action = {"expected_goal_raw": goal}
        clock[0] += 0.1
        sample(raw)
        evidence.guard_recovery(obj, control, {})


def test_visible_configure_skips_only_actual_selected_settings(monkeypatch):
    obj, evidence, rows, ident, port, reads, writes = visible_robot(
        monkeypatch, [status(visible_register_packet(), motor_id=3), status(bytes([91]), motor_id=3)]
    )
    events = []
    monkeypatch.setattr(
        obj.left_bus,
        "write",
        lambda register, motor, value, **kwargs: events.append((register, motor, value)),
    )
    obj.lift.configure = lambda **kwargs: None
    obj.configure()
    immutable = set(evidence.VISIBLE_STATIC_REGISTERS) | {"Minimum_Startup_Force"}
    assert not [row for row in events if row[1] == ELBOW and row[0] in immutable]
    assert ("Torque_Enable", ELBOW, 0) in events and ("Lock", ELBOW, 0) in events
    assert ("Maximum_Acceleration", "base_left_wheel", 254) in events
    assert evidence.visible_settings["before"]["registers"]["Maximum_Acceleration"] == 91
    assert evidence.visible_settings["before"]["registers"]["Acceleration"] == 77
    assert getattr(obj, "_am1_elbow_integral_trial", None) is None


@pytest.mark.parametrize("acquisition,admitted", [(0, 0.0), (2, -1.0)])
def test_visible_exact_context_and_final_sparse_key_reuse_three_owner_reads(
    monkeypatch, acquisition, admitted
):
    from examples.alohamini.am1_session_contract import LEFT_ELBOW_POSITIVE_CONTROL

    definition = LEFT_ELBOW_POSITIVE_CONTROL

    packets = [
        status(visible_register_packet(), motor_id=3),
        status(bytes([91]), motor_id=3),
        status(visible_register_packet(torque=1), motor_id=3),
        status(bytes([91]), motor_id=3),
    ]
    for goal in (2000, 2100, 2000):
        packets.append(
            status(
                group_payload(goal=goal, current=0, goal_time=0, goal_velocity=0, acceleration=77, lock=1),
                motor_id=3,
            )
        )
    obj, evidence, rows, ident, port, reads, writes = visible_robot(monkeypatch, packets)
    evidence.prepare_visible(obj, ident)
    evidence.verify_visible_active(obj)
    identity = {k: ident[k] for k in ("run_id", "host_incarnation")}
    control = host.AM1LocalControl(**identity, selected_evidence=evidence)
    for sequence, t in enumerate(
        (acquisition, acquisition + 13.0, acquisition + 25.0, acquisition + 27.8), 1
    ):
        cycle, phase, fraction = definition.phase(t, acquisition)
        c = context(
            profile=definition.profile,
            progress_s=t,
            phase=phase,
            cycle=cycle,
            reference=0.0,
            endpoint_target=10.0,
            requested_target=10 * fraction,
            sample_key=definition.sample_key(t, acquisition),
            acquisition_s=acquisition,
            admitted_reference=admitted,
        )
        assert control.apply(obj, command(identity, c, sequence=sequence))
        observation = observe(obj, control)
        evidence.publish(obj, control, observation)
        assert (
            observation[host.AM1_LOCAL_FEEDBACK_KEY]["selected_joint"]["visible_control"]["unchanged"] is True
        )
        evidence.report(obj, control, observation)
    probes = [row for row in rows if row["kind"] == "sparse_readback"]
    assert [row["sample_key"] for row in probes] == ["baseline", "cycle1_endpoint", "cycle1_origin"]
    assert probes[-1]["context"]["phase"] == "final" and len(port.writes) == 7
    assert all(row["decoded"]["acceleration_raw"] == 77 for row in probes)
    assert all(row["action"]["write_acknowledged"] is False for row in probes)


@pytest.mark.parametrize("bad", ["no_cameras", "recipe", "uuid", "shoulder"])
def test_visible_malformed_or_gain_admission_is_refused_before_owner(monkeypatch, bad):
    monkeypatch.setenv("AM1_VISIBLE_CONTROL_RECIPE", "physical-left-elbow-positive-control")
    monkeypatch.setenv("AM1_SCRIPTED_PREPARE", "1")
    monkeypatch.setenv("AM1_SELECTED_JOINT_EVIDENCE", "1")
    a = SimpleNamespace(
        robot_model="alohamini1",
        no_follower=False,
        no_cameras=True,
        am1_run_id=str(uuid4()),
        am1_host_incarnation=str(uuid4()),
        am1_protected_run_directory="/private",
    )
    if bad == "no_cameras":
        a.no_cameras = False
    elif bad == "recipe":
        monkeypatch.setenv("AM1_VISIBLE_CONTROL_RECIPE", "physical-left-elbow-recovery-i1")
    elif bad == "uuid":
        a.am1_run_id = "not-a-uuid"
    else:
        monkeypatch.setenv("AM1_SHOULDER_INTEGRAL_TEST", "1")
    with pytest.raises(ValueError):
        host.visible_control_binding(a)


def test_visible_absolute_envelope_uses_actual_encoder_endpoints(monkeypatch):
    from examples.alohamini.am1_session_contract import LEFT_ELBOW_POSITIVE_CONTROL

    definition = LEFT_ELBOW_POSITIVE_CONTROL

    obj, evidence, control, clock, sample, port = visible_guard(
        monkeypatch, initial_raw=1224, range_min=864, range_max=3073
    )
    calibration = {
        k: int(getattr(obj.left_bus.calibration[ELBOW], k))
        for k in ("drive_mode", "homing_offset", "range_min", "range_max")
    }
    q0 = obj._normalize_arm_feedback(obj.left_bus, {ELBOW: 1224})[ELBOW]
    _, actual = obj._encode_am1_arm_goals(obj.left_bus, {KEY: q0 + 10}, {ELBOW: 1224})
    assert definition.encoded_goal(q0, calibration) == 1223
    assert definition.encoded_goal(q0 + 10, calibration) == actual[ELBOW] == 1334
    evidence.reference = q0
    evidence.context = {
        "phase": "outbound",
        "cycle": 1,
        "reference": q0,
        "endpoint_target": q0 + 10,
        "admitted_reference": q0,
    }
    evidence.guard_recovery(obj, control, {})
    count = len(port.writes)
    clock[0] = 1.1
    sample(actual[ELBOW])
    evidence.guard_recovery(obj, control, {})
    clock[0] = 1.2
    sample(actual[ELBOW] + 1)
    with pytest.raises(RuntimeError, match="absolute excursion"):
        evidence.guard_recovery(obj, control, {})
    assert len(port.writes) == count


def test_visible_pause_never_recenters_confirmation_absolute_reference(monkeypatch):
    obj, evidence, control, clock, sample, port = visible_guard(monkeypatch, initial_raw=2020)
    evidence.reference = 0.0
    evidence.context = {
        "phase": "acquire",
        "cycle": 0,
        "reference": 0.0,
        "endpoint_target": 10.0,
        "admitted_reference": 2.0,
    }
    evidence.guard_recovery(obj, control, {})
    evidence.deactivate(obj)
    assert evidence.context is None and evidence.reference == 0.0
    clock[0] = 1.1
    sample(2110)
    with pytest.raises(RuntimeError, match="absolute excursion"):
        evidence.guard_recovery(obj, control, {})



@pytest.mark.parametrize("proportional", [20, 16, 24, 32])
def test_right_p20_raw_paired_visible_proof_is_trial_only_and_never_rewrites_pid(monkeypatch, tmp_path, request, proportional):
    from tests.robots.test_am1_elbow_integral import right_p20_robot, practical_robot, RIGHT_P20_MOTOR
    from lerobot.robots.alohamini.am1_elbow_integral import RIGHT_P20_RECIPE
    from examples.alohamini.am1_session_contract import PositiveElbowControl
    if proportional == 20:
        obj, trial, directory = right_p20_robot(tmp_path)
    else:
        obj, trial, directory, _, _ = practical_robot(tmp_path, proportional)
    request.addfinalizer(lambda: obj._safe_shutdown(close_buses=True))
    evidence, rows = sampler()
    obj._am1_visible_control_evidence = evidence
    before = bytearray(visible_register_packet(present=1845, goal=1845, range_min=827, range_max=3039))
    before[18] = 12; before[31:33] = (2048 + 658).to_bytes(2, "little")
    if proportional != 20:
        before[28:30] = (310).to_bytes(2, "little")
    active = bytearray(before); active[21] = proportional; active[40] = active[55] = 1
    obj.right_bus.port_handler = None
    # Full reads use the real DirectSdkTransport over a finite SDK port fixture.
    port = attach_port(obj, [])
    obj.right_bus.port_handler = obj.left_bus.port_handler
    obj.right_bus.packet_handler = obj.left_bus.packet_handler
    decoder = FeetechMotorsBus("unused-right-p20", obj.right_bus.motors, obj.right_bus.calibration)
    obj.right_bus.port = decoder.port
    obj.right_bus._decode_sign = decoder._decode_sign
    obj.right_bus.model_ctrl_table = decoder.model_ctrl_table
    obj.right_bus.apply_drive_mode = decoder.apply_drive_mode
    port.responses = [status(bytes(before), motor_id=3), status(bytes([254]), motor_id=3),
                    status(bytes(active), motor_id=3), status(bytes([254]), motor_id=3)]
    evidence.prepare_visible(obj, obj._am1_visible_control_binding)
    trial.prepare(obj); trial.disable_torque(obj); trial.apply(obj)
    obj.right_bus.values[("Torque_Enable", RIGHT_P20_MOTOR)] = 1
    trial.verify_active(obj)
    evidence.verify_visible_active(obj)
    proof = evidence.visible_control_proof(obj)
    assert proof["before"]["registers"]["P_Coefficient"] == 16
    assert proof["active"]["registers"]["P_Coefficient"] == proportional
    assert proof["unchanged"] is (proportional == 16) and proof["right_p20_trial"]["restore_required"] is True
    if proportional != 20:
        assert proof["before"]["registers"]["Protection_Current"] == 310
        assert evidence._live_definition.hardware_current_limit_ma == 2015
        from tests.robots.test_am1_live_native import MeasuredMotor
        measured = MeasuredMotor(); measured.configure_workspace()
        obj.am1_live_workspace_context = measured.am1_live_workspace_context
        obj._am1_selected_joint_position = dict(raw=1845)
        summary = dict(reference_norm=(1845-827)/2212*200-100)
        evidence.admit_live(obj, summary, measured=measured.positions)
        assert summary["corridor_min_raw"] == 1847 and summary["corridor_max_raw"] == 2074
        assert summary["measured_min_raw"] == 1813 and summary["measured_max_raw"] == 2108
    assert not PositiveElbowControl._settings_valid(proof, recipe_name=RIGHT_P20_RECIPE, current_cap_ma=150)
    assert evidence._live_definition.settings_valid(proof)
    import copy
    masked = copy.deepcopy(proof)
    masked.pop("right_p20_trial")
    masked["active"]["registers"]["P_Coefficient"] = 16
    masked["unchanged"] = True
    assert not evidence._live_definition.settings_valid(masked), "an opted trial cannot fall back to normal proof"
    for mutation in ("left", "integral", "p", "request", "context", "clock", "restored", "profile", "hardware"):
        bad = copy.deepcopy(proof)
        if mutation == "left": bad["binding"]["recipe"] = "physical-live-left-elbow"
        elif mutation == "integral": bad["active"]["registers"]["I_Coefficient"] = 1
        elif mutation == "p": bad["active"]["registers"]["P_Coefficient"] = 21
        elif mutation == "request": bad["right_p20_trial"]["request_sha256"] = "x" * 64
        elif mutation == "context": bad["right_p20_trial"]["context_sha256"] = "0" * 64
        elif mutation == "clock": bad["right_p20_trial"]["configured_at_monotonic_s"] = proof["active"]["read_completed_at_monotonic_s"] + 1
        elif mutation == "restored": bad["right_p20_trial"]["restore_required"] = False
        elif mutation == "profile": bad["active"]["registers"]["Torque_Limit"] += 1
        else: bad["active"]["registers"]["Protection_Current"] = 0
        assert not evidence._live_definition.settings_valid(bad), mutation
    assert len(json.dumps(proof, separators=(",", ":"))) < 4096


def teleop_goal_lane(monkeypatch):
    """Real codec/sparse SDK bytes; no device, old selected flags or response rule."""
    from examples.alohamini.am1_live_teleop import TELEOP_ARM_KEYS, ZERO_BODY

    obj, _, writes = robot(monkeypatch, enabled=False)
    obj.config.no_follower = obj.config.use_degrees = False
    now, rows = [10.], []
    evidence = host.AM1SelectedJointEvidence(enabled=False, clock=lambda: now[0], emit=rows.append)
    ident = binding()
    obj._am1_teleop_binding = dict(ident, recipe="physical-live-teleop")
    control = host.AM1LocalControl(**ident, selected_evidence=evidence, live=True,
                                   live_recipe="physical-live-teleop", clock=lambda: now[0])
    control.state, control.epoch, control.intent_revision = "active", 2, 0
    control._live_summary.update(state="active", live_deadline_monotonic_s=310.,
        reference={**dict.fromkeys(TELEOP_ARM_KEYS, 0.), KEY: float(obj.left_bus._normalize({3:2050})[3])},
        held_goals_raw={"left": {ELBOW: 2050}})

    def acquire(raw=2020, *, target_raw=None):
        bus = obj.left_bus
        normalized = float(bus._normalize({3: raw})[3])
        desired = float(bus._normalize({3: raw if target_raw is None else target_raw})[3])
        control.accepted_action_sequence += 1
        control.accepted_action = {**dict.fromkeys(TELEOP_ARM_KEYS, 0.), **ZERO_BODY, KEY: normalized}
        control.live_sample = {"seq": control.accepted_action_sequence, "target": {k: desired if k == KEY else control.accepted_action[k] for k in TELEOP_ARM_KEYS},
                               "valid_until_monotonic_s": now[0]+.24}
        marker = {**ident, "epoch": 2, "intent_revision": 0, "action_sequence": control.accepted_action_sequence}
        evidence.record_teleop_action(obj, control, marker, control.accepted_action)
        observation = {}
        control.annotate(observation, acquired_at=now[0])
        return observation
    return obj, evidence, control, now, rows, writes, acquire


def test_teleop_goal_evidence_maintains_old_flags_and_correlates_actual_readback(monkeypatch):
    obj, evidence, control, now, rows, writes, acquire = teleop_goal_lane(monkeypatch)
    port = attach_port(obj, [status(group_payload(goal=2020, present=2012), motor_id=3)])
    observation = acquire()
    evidence.report(obj, control, observation)
    assert not port.writes
    now[0] += .5
    observation = acquire()  # Genuine fresh input/action at the same maintained target.
    evidence.report(obj, control, observation)
    assert len(port.writes) == 1 and writes == []
    row = next(r for r in rows if r["kind"] == "live_goal_readback")
    assert row["goal_position_raw"] == row["requested_goal_raw"] == 2020
    assert row["present_position_raw"] == 2012 and row["goal_matches_expected_raw"] is True
    assert row["action"]["provenance"] == "accepted-normalized-from-encoded-integer/derived-roundtrip"
    assert row["action"]["encoded_goal_word"] == 2020 and row["action"]["write_acknowledged"] is False
    assert row["run_id"] == control.binding["run_id"] and row["host_incarnation"] == control.binding["host_incarnation"]
    assert row["epoch"] == 2 and row["intent_revision"] == 0 and row["action_sequence"] == control.accepted_action_sequence
    evidence.publish(obj, control, observation)
    assert len(observation["_am1_local_feedback"]["selected_joint"]["goal_readbacks"]) == 1
    assert not evidence.enabled and obj._am1_selected_joint_evidence_enabled is False
    assert getattr(obj, "_am1_selected_joint_evidence_context", None) is None
    assert getattr(obj, "_am1_visible_control_binding", None) is None and evidence.context is None
    assert evidence.response_boundary is None and evidence.visible_settings is None


@pytest.mark.parametrize("mode", ["ordinary", "protected_hold", "inward_cap"])
@pytest.mark.parametrize("drive", [0, 1])
def test_teleop_expected_raw_is_accepted_codec_roundtrip_not_requested_wire_capture(monkeypatch, mode, drive):
    obj, evidence, control, _, _, _, acquire = teleop_goal_lane(monkeypatch)
    obj.left_bus.calibration[ELBOW] = MotorCalibration(3, drive, 1845, 864, 3073)
    measured = 2124
    held = float(obj.left_bus._normalize({3: measured})[3])
    desired = held + 8.751 * (-1 if drive else 1)
    if mode == "protected_hold":
        desired = held
        obj._joint_hold_goal[ELBOW] = held
    elif mode == "inward_cap":
        obj.config.max_relative_target = .31
    accepted, actual_raw = obj._encode_am1_arm_goals(obj.left_bus, {KEY: desired}, {ELBOW: measured})
    acquire(actual_raw[ELBOW])
    record = evidence.teleop_action
    assert record["action"]["expected_goal_raw"] == actual_raw[ELBOW]
    assert record["action"]["final_normalized"] == accepted[KEY]
    assert record["action"]["calibration"] == [3, drive, 1845, 864, 3073]
    assert record["action"]["write_acknowledged"] is False
    assert not evidence.enabled and obj._am1_selected_joint_evidence_enabled is False


@pytest.mark.parametrize("changed", ["run", "host", "epoch", "revision", "sequence", "accepted", "calibration", "expired", "legacy_flag"])
def test_teleop_goal_readback_refuses_changed_or_expired_correlation_without_extra_io(monkeypatch, changed):
    obj, evidence, control, now, rows, _, acquire = teleop_goal_lane(monkeypatch)
    port = attach_port(obj, [status(group_payload(goal=2020), motor_id=3)])
    acquire()
    now[0] += .5
    observation = acquire()
    original_decode = evidence._decoded
    def decode(bus, payload):
        result = original_decode(bus, payload)
        if changed in {"run", "host"}:
            control.binding["run_id" if changed == "run" else "host_incarnation"] = str(uuid4())
        elif changed in {"epoch", "revision", "sequence"}:
            attr = {"epoch": "epoch", "revision": "intent_revision", "sequence": "accepted_action_sequence"}[changed]
            setattr(control, attr, getattr(control, attr)+1)
        elif changed == "accepted":
            control.accepted_action[KEY] += .1
        elif changed == "calibration":
            obj.left_bus.calibration[ELBOW].range_min += 1
        elif changed == "legacy_flag":
            obj._am1_selected_joint_evidence_enabled = True
        else:
            now[0] += .3
        return result
    monkeypatch.setattr(evidence, "_decoded", decode)
    evidence.report(obj, control, observation)
    assert len(port.writes) == 1
    row = rows[-1]
    assert row["diagnostic_available"] is False and row["unavailable_reason"] == "teleop action correlation changed during read"
    assert row["goal_matches_expected_raw"] is None
    evidence.report(obj, control, observation)
    assert len(port.writes) == 1


def test_teleop_goal_checkpoints_include_maintained_outward_and_return_with_maximum_eight_reads(monkeypatch):
    obj, evidence, control, now, rows, _, acquire = teleop_goal_lane(monkeypatch)
    port = attach_port(obj, [status(group_payload(goal=raw), motor_id=3) for raw in (2020, 2100, 2020, 2100, 2020, 2100, 2020, 2100)])
    for index in range(10):
        now[0] = 10.+index*2
        raw = 2020 if index%2 == 0 else 2100
        evidence.report(obj, control, acquire(raw))
        now[0] += .5
        evidence.report(obj, control, acquire(raw))
        now[0] += .1
        evidence.report(obj, control, acquire(raw))
    assert len(port.writes) == len(rows) == len(evidence.goal_readbacks) == 8
    assert [r["requested_goal_raw"] for r in rows[:3]] == [2020, 2100, 2020]
    assert all(b["read_started_at_monotonic_s"]-a["read_started_at_monotonic_s"] >= 1 for a,b in zip(rows, rows[1:], strict=False))


@pytest.mark.parametrize("bad", ["timeout", "checksum", "servo", "status", "torque", "mode"])
def test_teleop_goal_optional_empty_timeout_and_actual_faults_keep_existing_meaning(monkeypatch, bad):
    obj, evidence, control, now, rows, _, acquire = teleop_goal_lane(monkeypatch)
    payload=group_payload(goal=2020, servo_status=8 if bad=="status" else 0, torque=0 if bad=="torque" else 1, mode=1 if bad=="mode" else 0)
    packet=bytearray(status(payload,motor_id=3,error=8 if bad=="servo" else 0))
    if bad=="checksum":
        packet[-1]^=1
    port=attach_port(obj,[] if bad=="timeout" else [bytes(packet)])
    acquire()
    now[0]+=.5
    observation=acquire()
    if bad=="timeout":
        evidence.report(obj,control,observation)
    else:
        with pytest.raises(RuntimeError):
            evidence.report(obj,control,observation)
    assert len(port.writes)==1 and rows[-1]["diagnostic_available"] is False
    assert not evidence.enabled and obj._am1_selected_joint_evidence_enabled is False


def test_left_response_goal_evidence_keeps_exact_trial_and_old_flags_off(monkeypatch):
    from tests.robots.test_am1_live_teleop_native import install_left_response_trial

    obj, evidence, control, now, rows, writes, acquire = teleop_goal_lane(monkeypatch)
    trial = install_left_response_trial(monkeypatch, obj)
    port = attach_port(obj, [status(group_payload(goal=2020, present=2012), motor_id=3)])
    evidence.report(obj, control, acquire())
    now[0] += .5
    observation = acquire()
    evidence.report(obj, control, observation)
    row = next(r for r in rows if r["kind"] == "live_goal_readback")
    assert row["left_response_p_trial"] == trial.receipt
    assert row["left_response_p_trial"]["original_p"] == 16
    assert row["left_response_p_trial"]["configured_p"] == 32
    assert row["goal_matches_expected_raw"] and len(port.writes) == 1 and not writes
    evidence.publish(obj, control, observation)
    assert observation["_am1_local_feedback"]["selected_joint"]["left_response_p_trial"] == trial.receipt
    assert not evidence.enabled and not obj._am1_selected_joint_evidence_enabled
    assert getattr(obj, "_am1_visible_control_binding", None) is None and evidence.context is None


@pytest.mark.parametrize("change", ["restored", "binding", "legacy"])
def test_left_response_goal_evidence_refuses_retired_trial_or_legacy_flags_without_io(monkeypatch, change):
    from tests.robots.test_am1_live_teleop_native import install_left_response_trial

    obj, evidence, control, now, rows, writes, acquire = teleop_goal_lane(monkeypatch)
    trial = install_left_response_trial(monkeypatch, obj)
    port = attach_port(obj, [status(group_payload(goal=2020, present=2012), motor_id=3)])
    evidence.report(obj, control, acquire())
    now[0] += .5
    if change == "restored":
        trial.restore_required = False
    elif change == "binding":
        obj._am1_left_response_p_binding = dict(trial.binding, run_id=str(uuid4()))
    else:
        obj._am1_selected_joint_evidence_enabled = True
    observation = acquire()
    evidence.report(obj, control, observation)
    assert not port.writes and not writes
    assert not [row for row in rows if row["kind"] == "live_goal_readback"]


def cached_elbow_settings(obj):
    for _phase, attr, at in (
        ("original", "_am1_teleop_settings", 8.0),
        ("active", "_am1_teleop_active_settings", 9.0),
    ):
        records = {}
        for side, bus in (("left", obj.left_bus), ("right", obj.right_bus)):
            motor = f"arm_{side}_elbow_flex"
            cal = bus.calibration[motor]
            values = {
                "ID": 3,
                "Model_Number": 777,
                "Firmware_Major_Version": 3,
                "Firmware_Minor_Version": 10,
                "Min_Position_Limit": cal.range_min,
                "Max_Position_Limit": cal.range_max,
                "Homing_Offset": cal.homing_offset,
                "Phase": 12,
                "P_Coefficient": 16,
                "I_Coefficient": 0,
                "D_Coefficient": 32,
                "Acceleration": 0,
                "Maximum_Acceleration": 0,
                "Protection_Current": 310,
                "Max_Temperature_Limit": 70,
                "Min_Voltage_Limit": 40,
                "Max_Voltage_Limit": 140,
                "Operating_Mode": 0,
            }
            records[side] = {
                "registers": {register: {motor: value} for register, value in values.items()},
                "timing": {
                    register: {"read_started_at_monotonic_s": at, "read_completed_at_monotonic_s": at + 0.01}
                    for register in values
                },
            }
        setattr(obj, attr, records)


def two_elbow_goal_lane(monkeypatch):
    obj, evidence, control, now, rows, writes, _ = teleop_goal_lane(monkeypatch)
    obj.left_bus.calibration[ELBOW] = MotorCalibration(3, 0, 1845, 864, 3073)
    right = "arm_right_elbow_flex"
    obj.right_bus = FeetechMotorsBus(
        "unused-right-elbow",
        {right: Motor(3, "sts3215", MotorNormMode.RANGE_M100_100)},
        {right: MotorCalibration(3, 0, -658, 827, 3039)},
    )
    obj.right_arm_motors = [right]
    control._live_summary["held_goals_raw"]["right"]={right:1866}
    control._live_summary["reference"].update({KEY: float(obj.left_bus._normalize({3:2050})[3]),
        right+".pos": float(obj.right_bus._normalize({3:1866})[3])})
    cached_elbow_settings(obj)

    def acquire(left_raw=2020, right_raw=1847):
        from examples.alohamini.am1_live_teleop import TELEOP_ARM_KEYS, ZERO_BODY

        control.accepted_action_sequence += 1
        values = {
            KEY: float(obj.left_bus._normalize({3: left_raw})[3]),
            right + ".pos": float(obj.right_bus._normalize({3: right_raw})[3]),
        }
        control.accepted_action = {**dict.fromkeys(TELEOP_ARM_KEYS, 0.0), **ZERO_BODY, **values}
        control.live_sample = {
            "seq": control.accepted_action_sequence,
            "target": {k: control.accepted_action[k] for k in TELEOP_ARM_KEYS},
            "valid_until_monotonic_s": now[0] + 0.24,
        }
        evidence.record_teleop_action(
            obj,
            control,
            {
                **control.binding,
                "epoch": control.epoch,
                "intent_revision": control.intent_revision,
                "action_sequence": control.accepted_action_sequence,
            },
            control.accepted_action,
        )
        observation = {}
        control.annotate(observation, acquired_at=now[0])
        return observation

    return obj, evidence, control, now, rows, writes, acquire


def test_whole_teleop_two_elbows_read_actual_separate_buses_with_shared_spacing(monkeypatch):
    obj, evidence, control, now, rows, writes, acquire = two_elbow_goal_lane(monkeypatch)
    left = attach_port(obj, [status(group_payload(goal=2020, present=2012), motor_id=3)])
    right = ByteStreamPort([status(group_payload(goal=1847, present=1834, current=39), motor_id=3)])
    obj.right_bus.port_handler, obj.right_bus.packet_handler = right, scs.PacketHandler(0)
    acquire()
    now[0] = 10.5
    evidence.report(obj, control, acquire())
    now[0] = 10.99
    evidence.report(obj, control, acquire())
    assert len(left.writes) == 1 and not right.writes
    now[0] = 11.5
    observation = acquire()
    evidence.report(obj, control, observation)
    assert len(left.writes) == len(right.writes) == 1 and not writes
    assert [r["action"]["side"] for r in rows] == ["left", "right"]
    row = rows[-1]
    assert (
        row["action"]["motor"] == "arm_right_elbow_flex"
        and row["action"]["joint"] == "arm_right_elbow_flex.pos"
    )
    assert row["action"]["calibration"] == [3, 0, -658, 827, 3039]
    assert (
        row["requested_goal_raw"] == row["goal_position_raw"] == 1847 and row["present_position_raw"] == 1834
    )
    assert row["decoded"]["present_position_normalized"] == pytest.approx((1834 - 827) / 2212 * 200 - 100)
    assert row["decoded"]["present_current_ma"] == 253.5 and row["decoded"]["voltage_v"] == 11.9
    assert row["decoded"]["torque_limit_raw"] == 1000 and row["decoded"]["present_load_raw"] == -3
    assert row["decoded"]["status"] == 0 and row["diagnostic_available"] is True
    assert all(
        r["run_id"] == control.binding["run_id"]
        and r["host_incarnation"] == control.binding["host_incarnation"]
        for r in rows
    )
    evidence.publish(obj, control, observation)
    assert len(observation["_am1_local_feedback"]["selected_joint"]["goal_readbacks"]) == 2


def test_whole_teleop_two_elbows_share_eight_attempt_budget_not_eight_per_bus(monkeypatch):
    obj, evidence, control, now, rows, _, acquire = two_elbow_goal_lane(monkeypatch)
    left = attach_port(obj, [status(group_payload(goal=2020), motor_id=3) for _ in range(8)])
    right = ByteStreamPort([status(group_payload(goal=1847), motor_id=3) for _ in range(8)])
    obj.right_bus.port_handler, obj.right_bus.packet_handler = right, scs.PacketHandler(0)
    for index in range(12):
        now[0] = 10.0 + index * 2
        acquire(2020 + index, 1847 + index)
        now[0] += 0.5
        evidence.report(obj, control, acquire(2020 + index, 1847 + index))
    assert len(rows) == len(left.writes) + len(right.writes) == 8
    assert len(left.writes) == len(right.writes) == 4
    assert all(
        b["read_started_at_monotonic_s"] - a["read_started_at_monotonic_s"] >= 1
        for a, b in zip(rows, rows[1:], strict=False)
    )


@pytest.mark.parametrize("change", ["bus", "calibration", "run", "input", "accepted"])
def test_whole_teleop_right_read_rejects_changed_owner_or_current_intent(monkeypatch, change):
    obj, evidence, control, now, rows, _, acquire = two_elbow_goal_lane(monkeypatch)
    attach_port(obj, [status(group_payload(goal=2020), motor_id=3)])
    right = ByteStreamPort([status(group_payload(goal=1847), motor_id=3)])
    obj.right_bus.port_handler, obj.right_bus.packet_handler = right, scs.PacketHandler(0)
    acquire()
    now[0] = 10.5
    evidence.report(obj, control, acquire())
    now[0] = 11.5
    observation = acquire()
    original = evidence._decoded

    def decode(bus, payload, **kwargs):
        result = original(bus, payload, **kwargs)
        if change == "bus":
            obj.right_bus = obj.left_bus
        elif change == "calibration":
            obj.right_bus.calibration["arm_right_elbow_flex"].range_min += 1
        elif change == "run":
            control.binding["run_id"] = str(uuid4())
        elif change == "input":
            now[0] += 0.3
        else:
            control.accepted_action["arm_right_elbow_flex.pos"] += 0.1
        return result

    monkeypatch.setattr(evidence, "_decoded", decode)
    evidence.report(obj, control, observation)
    assert len(right.writes) == 1 and rows[-1]["action"]["side"] == "right"
    assert rows[-1]["diagnostic_available"] is False and rows[-1]["goal_matches_expected_raw"] is None
    evidence.publish(obj, control, observation)
    assert "teleop_elbow_settings" not in observation.get("_am1_local_feedback", {}).get("selected_joint", {})


def test_whole_teleop_cached_two_elbow_settings_publish_original_scalar_and_timing_without_io(monkeypatch):
    obj, evidence, control, now, rows, writes, acquire = two_elbow_goal_lane(monkeypatch)
    observation = acquire()
    evidence.publish(obj, control, observation)
    selected = observation["_am1_local_feedback"]["selected_joint"]
    proof = selected.get("teleop_elbow_settings")
    assert isinstance(proof, dict) and set(proof) == {"left", "right"}
    assert set(proof["right"]) == {
        "motor",
        "motor_id",
        "calibration",
        "original",
        "active",
        "original_read_timing",
        "active_read_timing",
    }
    assert proof["right"]["motor"] == "arm_right_elbow_flex" and proof["right"]["motor_id"] == 3
    assert proof["right"]["calibration"] == [3, 0, -658, 827, 3039]
    assert proof["left"]["calibration"] == [3, 0, 1845, 864, 3073]
    for side in ("left", "right"):
        assert proof[side]["original"]["P_Coefficient"] == proof[side]["active"]["P_Coefficient"] == 16
        assert proof[side]["active"]["I_Coefficient"] == 0 and proof[side]["active"]["D_Coefficient"] == 32
        assert proof[side]["active"]["Model_Number"] == 777
        assert (
            proof[side]["active"]["Firmware_Major_Version"] == 3
            and proof[side]["active"]["Firmware_Minor_Version"] == 10
        )
        assert proof[side]["active"]["Protection_Current"] == 310
        assert proof[side]["original_read_timing"]["P_Coefficient"] == {
            "read_started_at_monotonic_s": 8.0,
            "read_completed_at_monotonic_s": 8.01,
        }
        assert proof[side]["active_read_timing"]["P_Coefficient"] == {
            "read_started_at_monotonic_s": 9.0,
            "read_completed_at_monotonic_s": 9.01,
        }
    assert selected["acquired_at_monotonic_s"] == now[0] == 10.0
    assert not writes and not rows
    proof["right"]["active"]["P_Coefficient"] = 99
    evidence.publish(obj, control, observation)
    assert (
        observation["_am1_local_feedback"]["selected_joint"]["teleop_elbow_settings"]["right"]["active"][
            "P_Coefficient"
        ]
        == 16
    )


@pytest.mark.parametrize(
    "bad", ["missing", "timing", "future", "boolean", "calibration", "bus_swap", "mutated_cache"]
)
def test_whole_teleop_cached_settings_never_invent_missing_or_changed_proof(monkeypatch, bad):
    obj, evidence, control, now, _, _, acquire = two_elbow_goal_lane(monkeypatch)
    observation = acquire()
    if bad == "missing":
        del obj._am1_teleop_active_settings["right"]
    elif bad == "timing":
        del obj._am1_teleop_active_settings["right"]["timing"]["P_Coefficient"]
    elif bad == "future":
        obj._am1_teleop_active_settings["right"]["timing"]["P_Coefficient"][
            "read_completed_at_monotonic_s"
        ] = 11.0
    elif bad == "boolean":
        obj._am1_teleop_active_settings["right"]["registers"]["P_Coefficient"]["arm_right_elbow_flex"] = True
    elif bad == "calibration":
        obj.right_bus.calibration["arm_right_elbow_flex"].range_min += 1
    elif bad == "bus_swap":
        obj.right_bus = obj.left_bus
    else:
        obj._am1_teleop_active_settings["right"]["registers"]["P_Coefficient"]["arm_right_elbow_flex"] = 32
    evidence.publish(obj, control, observation)
    assert "teleop_elbow_settings" not in observation.get("_am1_local_feedback", {}).get("selected_joint", {})


@pytest.mark.parametrize("drive", [0, 1])
def test_whole_teleop_right_expected_raw_uses_right_encoder_and_actual_drive(monkeypatch, drive):
    obj, evidence, _, _, _, _, acquire = two_elbow_goal_lane(monkeypatch)
    motor, key = "arm_right_elbow_flex", "arm_right_elbow_flex.pos"
    obj.right_bus.calibration[motor] = MotorCalibration(3, drive, -658, 827, 3039)
    desired = float(obj.right_bus._normalize({3: 1900})[3])
    accepted, raw = obj._encode_am1_arm_goals(obj.right_bus, {key: desired}, {motor: 1847})
    acquire(right_raw=raw[motor])
    action = evidence.teleop_states["right"]["action"]["action"]
    assert action["expected_goal_raw"] == raw[motor] == 1900
    assert action["final_normalized"] == accepted[key]
    assert action["calibration"] == [3, drive, -658, 827, 3039]
    assert action["encoded_goal_bytes"] == [108, 7] and action["write_acknowledged"] is False


@pytest.mark.parametrize("proportional", [16, 32])
def test_whole_teleop_explicit_left_trial_never_publishes_ordinary_baseline_settings(
    monkeypatch, proportional
):
    from tests.robots.test_am1_live_teleop_native import install_left_response_trial

    obj, evidence, control, _, _, _, acquire = two_elbow_goal_lane(monkeypatch)
    trial = install_left_response_trial(monkeypatch, obj, proportional=proportional)
    observation = acquire()
    evidence.publish(obj, control, observation)
    selected = observation["_am1_local_feedback"]["selected_joint"]
    assert selected["left_response_p_trial"] == trial.receipt
    assert "teleop_elbow_settings" not in selected


@pytest.mark.parametrize("which", ["port", "port_handler", "packet_handler"])
def test_whole_teleop_right_transport_replacement_invalidates_actual_read_correlation(monkeypatch, which):
    obj, evidence, control, now, rows, _, acquire = two_elbow_goal_lane(monkeypatch)
    attach_port(obj, [status(group_payload(goal=2020), motor_id=3)])
    right = ByteStreamPort([status(group_payload(goal=1847), motor_id=3)])
    obj.right_bus.port_handler, obj.right_bus.packet_handler = right, scs.PacketHandler(0)
    acquire()
    now[0] = 10.5
    evidence.report(obj, control, acquire())
    now[0] = 11.5
    observation = acquire()
    original = evidence._decoded

    def decode(bus, payload, **kwargs):
        decoded = original(bus, payload, **kwargs)
        setattr(obj.right_bus, which, "different-port" if which == "port" else object())
        return decoded

    monkeypatch.setattr(evidence, "_decoded", decode)
    evidence.report(obj, control, observation)
    assert len(right.writes) == 1 and rows[-1]["diagnostic_available"] is False
    assert rows[-1]["goal_matches_expected_raw"] is None


@pytest.mark.parametrize("side", ["left", "right"])
@pytest.mark.parametrize("proportional", [16, 32])
def test_fixed_response_cached_proof_contains_exact_trial_and_both_baselines(monkeypatch, side, proportional):
    from tests.robots.test_am1_live_teleop_native import (
        install_left_response_trial,
        install_right_response_trial,
    )
    obj, evidence, control, _, _, writes, acquire = two_elbow_goal_lane(monkeypatch)
    install = install_left_response_trial if side == "left" else install_right_response_trial
    trial = install(monkeypatch, obj, workload=side + "-peer-fixed", proportional=proportional)
    obj._am1_teleop_active_settings[side]["registers"]["P_Coefficient"]["arm_" + side + "_elbow_flex"] = proportional
    observation = acquire()
    evidence.publish(obj, control, observation)
    selected = observation["_am1_local_feedback"]["selected_joint"]
    proof = selected["teleop_elbow_settings"]
    assert set(proof) == {"left", "right", "trial"} and proof["trial"] == trial.receipt
    assert selected[side + "_response_p_trial"] == trial.receipt
    for current in ("left", "right"):
        assert proof[current]["original"]["P_Coefficient"] == 16
        assert proof[current]["active"]["P_Coefficient"] == (proportional if current == side else 16)
        assert proof[current]["original"]["I_Coefficient"] == proof[current]["active"]["I_Coefficient"] == 0
        assert proof[current]["original"]["D_Coefficient"] == proof[current]["active"]["D_Coefficient"] == 32
    assert not writes


def three_teleop_goal_lane(monkeypatch, drive=0):
    """The existing owner/read codec with only its two elbows plus LEFT shoulder ID2."""
    from examples.alohamini.am1_live_teleop import TELEOP_ARM_KEYS, ZERO_BODY

    obj, evidence, control, now, rows, writes, _ = two_elbow_goal_lane(monkeypatch)
    shoulder = "arm_left_shoulder_lift"
    obj.left_bus = FeetechMotorsBus("unused-tracking-left",
        {**obj.left_bus.motors, shoulder: Motor(2, "sts3215", MotorNormMode.RANGE_M100_100)},
        {**obj.left_bus.calibration, shoulder: MotorCalibration(2, drive, 515, 848, 3223)})
    obj.left_arm_motors.append(shoulder)
    control._live_summary["held_goals_raw"]["left"][shoulder]=3222
    control._live_summary["reference"][shoulder+".pos"] = float(obj.left_bus._normalize({2:3222})[2])

    def acquire(left=2050, right=1866, lift=3222, lift_target=None):
        control.accepted_action_sequence += 1
        values = {KEY: float(obj.left_bus._normalize({3:left})[3]),
                  "arm_right_elbow_flex.pos": float(obj.right_bus._normalize({3:right})[3]),
                  shoulder+".pos": float(obj.left_bus._normalize({2:lift})[2])}
        control.accepted_action = {**dict.fromkeys(TELEOP_ARM_KEYS, 0.), **ZERO_BODY, **values}
        control.live_sample = {"seq": control.accepted_action_sequence,
            "target": {key:control.accepted_action[key] for key in TELEOP_ARM_KEYS},
            "valid_until_monotonic_s": now[0]+.24}
        if lift_target is not None:
            control.live_sample["target"][shoulder+".pos"] = float(obj.left_bus._normalize({2:lift_target})[2])
        evidence.record_teleop_action(obj, control, {**control.binding, "epoch":control.epoch,
            "intent_revision":control.intent_revision,"action_sequence":control.accepted_action_sequence},
            control.accepted_action)
        observation = {}
        control.annotate(observation, acquired_at=now[0])
        return observation
    return obj, evidence, control, now, rows, writes, acquire


@pytest.mark.parametrize("drive", [0, 1])
def test_teleop_tracking_reads_only_closed_left_shoulder_id2_and_pairs_current_goal(monkeypatch, drive):
    obj, evidence, control, now, rows, writes, acquire = three_teleop_goal_lane(monkeypatch, drive)
    port = attach_port(obj, [status(group_payload(goal=3187, present=3221), motor_id=2) for _ in range(2)])
    evidence.report(obj, control, acquire())
    now[0] = 10.5
    evidence.report(obj, control, acquire())
    assert not port.writes, "initial immutable-reference holds cannot spend the finite changed-target budget"
    now[0] = 11.
    acquire(lift=3187)
    now[0] = 11.5
    evidence.report(obj, control, acquire(lift=3187))
    now[0] = 12.5
    evidence.report(obj, control, acquire(lift=3187))
    assert port.writes == [bytes([255,255,254,5,130,33,38,2,49])]*2 and writes == []
    assert len(rows) == 2 and all(row["action"]["motor"] == "arm_left_shoulder_lift" for row in rows)
    assert [row["opportunity_sample_index"] for row in rows] == [1,2]
    assert len({(row["epoch"],row["intent_revision"],row["opportunity_index"]) for row in rows}) == 1
    assert rows[1]["read_started_at_monotonic_s"]-rows[0]["read_started_at_monotonic_s"] == 1.
    for row in rows:
        assert row["action"]["side"] == "left" and row["action"]["calibration"] == [2,drive,515,848,3223]
        assert row["requested_goal_raw"] == row["goal_position_raw"] == 3187
        assert row["present_position_raw"] == 3221 and row["goal_matches_expected_raw"] is True
        assert row["decoded"]["present_position_normalized"] == pytest.approx((3221-848)/2375*200-100 if not drive else -((3221-848)/2375*200-100))
    assert not evidence.enabled and obj._am1_selected_joint_evidence_enabled is False


def test_teleop_tracking_same_target_recovery_cannot_spend_another_read_or_fabricate_pair(monkeypatch):
    obj, evidence, control, now, rows, _, acquire = three_teleop_goal_lane(monkeypatch)
    port = attach_port(obj, [status(group_payload(goal=3187),motor_id=2),
                             status(group_payload(goal=3200),motor_id=2),status(group_payload(goal=3200),motor_id=2)])
    acquire(lift=3187)
    now[0]=10.5
    evidence.report(obj,control,acquire(lift=3187))
    control.epoch,control.intent_revision=4,1
    now[0]=11.5
    acquire(lift=3187)
    now[0]=12.
    evidence.report(obj,control,acquire(lift=3187))
    assert len(port.writes)==1, "unchanged target in a recovery epoch cannot consume another opportunity"
    now[0]=13.
    acquire(lift=3200)
    now[0]=13.5
    evidence.report(obj,control,acquire(lift=3200))
    now[0]=14.5
    evidence.report(obj,control,acquire(lift=3200))
    assert len(port.writes)==3
    assert [(r["epoch"],r["opportunity_sample_index"]) for r in rows]==[(2,1),(4,1),(4,2)]


def test_teleop_tracking_reserves_two_attempts_for_late_closed_roles_with_one_shared_budget(monkeypatch):
    obj,evidence,control,now,rows,writes,acquire=three_teleop_goal_lane(monkeypatch)
    left=attach_port(obj,[status(group_payload(goal=2020+i),motor_id=3) for i in range(4)]+
                         [status(group_payload(goal=3187),motor_id=2) for _ in range(2)])
    right=ByteStreamPort([status(group_payload(goal=1941),motor_id=3) for _ in range(2)])
    obj.right_bus.port_handler,obj.right_bus.packet_handler=right,scs.PacketHandler(0)
    for i in range(5):
        now[0]=10.+2*i
        acquire(left=2020+i)
        now[0]+=.5
        evidence.report(obj,control,acquire(left=2020+i))
    assert len(left.writes)==4 and not right.writes, "earlier changes must leave two reads for each late role"
    now[0]=21.
    acquire(left=2024,right=1941)
    for at in (21.5,22.5):
        now[0]=at
        evidence.report(obj,control,acquire(left=2024,right=1941))
    now[0]=24.
    acquire(left=2024,right=1941,lift=3187)
    for at in (24.5,25.5,26.5):
        now[0]=at
        evidence.report(obj,control,acquire(left=2024,right=1941,lift=3187))
    assert len(rows)==len(left.writes)+len(right.writes)==8 and not writes
    assert [row["action"]["motor"] for row in rows]==["arm_left_elbow_flex"]*4+["arm_right_elbow_flex"]*2+["arm_left_shoulder_lift"]*2
    assert all(b["read_started_at_monotonic_s"]-a["read_started_at_monotonic_s"]>=1 for a,b in zip(rows,rows[1:]))


@pytest.mark.parametrize("changed", ["id", "calibration", "binding", "expired", "status", "torque", "mode"])
def test_teleop_tracking_shoulder_keeps_closed_identity_freshness_and_fault_policy(monkeypatch, changed):
    obj,evidence,control,now,rows,writes,acquire=three_teleop_goal_lane(monkeypatch)
    payload=group_payload(goal=3187,servo_status=8 if changed=="status" else 0,
                          torque=0 if changed=="torque" else 1,mode=1 if changed=="mode" else 0)
    port=attach_port(obj,[status(payload,motor_id=2)])
    acquire(lift=3187)
    now[0]=10.5
    observation=acquire(lift=3187)
    if changed=="id":obj.left_bus.motors["arm_left_shoulder_lift"].id=1
    elif changed=="calibration":obj.left_bus.calibration["arm_left_shoulder_lift"].range_min+=1
    elif changed=="binding":control.binding["run_id"]=str(uuid4())
    elif changed=="expired":now[0]+=.3
    if changed in {"status","torque","mode"}:
        with pytest.raises(RuntimeError):evidence.report(obj,control,observation)
        assert len(port.writes)==1 and rows[-1]["diagnostic_available"] is False
    else:
        evidence.report(obj,control,observation)
        assert not port.writes and not rows
    assert not writes and not evidence.enabled and obj._am1_selected_joint_evidence_enabled is False


def test_teleop_tracking_later_same_epoch_read_reports_changed_constraint_goal_instead_of_inventing_dwell(monkeypatch):
    obj,evidence,control,now,rows,_,acquire=three_teleop_goal_lane(monkeypatch)
    port=attach_port(obj,[status(group_payload(goal=3187,present=3221),motor_id=2),
                         status(group_payload(goal=3200,present=3221),motor_id=2)])
    acquire(lift=3187,lift_target=3187)
    now[0]=10.5
    evidence.report(obj,control,acquire(lift=3187,lift_target=3187))
    now[0]=11.
    acquire(lift=3200,lift_target=3187)
    now[0]=11.5
    evidence.report(obj,control,acquire(lift=3200,lift_target=3187))
    assert len(port.writes)==2
    assert [row["goal_position_raw"] for row in rows]==[3187,3200]
    assert [row["requested_goal_raw"] for row in rows]==[3187,3200]
    assert rows[0]["action"]["requested"]==rows[1]["action"]["requested"]
    assert [row["opportunity_sample_index"] for row in rows]==[1,2]
    assert len({(row["epoch"],row["intent_revision"],row["opportunity_index"]) for row in rows})==1


def test_teleop_tracking_shoulder_does_not_label_itself_with_the_elbow_p32_transaction(monkeypatch):
    from tests.robots.test_am1_live_teleop_native import install_left_response_trial

    obj,evidence,control,now,rows,writes,acquire=three_teleop_goal_lane(monkeypatch)
    trial=install_left_response_trial(monkeypatch,obj)
    port=attach_port(obj,[status(group_payload(goal=3187),motor_id=2)])
    acquire(lift=3187)
    now[0]=10.5
    evidence.report(obj,control,acquire(lift=3187))
    assert len(port.writes)==1 and not writes and trial.restore_required
    assert rows[-1]["action"]["motor"]=="arm_left_shoulder_lift"
    assert "left_response_p_trial" not in rows[-1], "the run's elbow P32 receipt is not a shoulder setting"


def test_teleop_tracking_initial_hold_uses_original_literal_goal_instead_of_measured_rest(monkeypatch):
    obj,evidence,control,now,rows,_,acquire=three_teleop_goal_lane(monkeypatch)
    key="arm_left_shoulder_lift.pos"
    control._live_summary["reference"][key]=float(obj.left_bus._normalize({2:3221})[2])
    port=attach_port(obj,[status(group_payload(goal=3222,present=3221),motor_id=2)])
    acquire()
    now[0]=10.5
    evidence.report(obj,control,acquire())
    assert not port.writes and not rows, "an existing rawGoal differing from PP is still the initial hold"


@pytest.mark.parametrize("drive", [0, 1])
@pytest.mark.parametrize("motor,mode,calibration,raws", [
    ("arm_left_shoulder_pan", MotorNormMode.RANGE_M100_100, (1, -1761, 731, 3438), (731, 2628, 3438)),
    ("arm_left_gripper", MotorNormMode.RANGE_0_100, (6, 933, 1886, 3401), (1886, 2628, 2980, 3401)),
    ("arm_left_elbow_flex", MotorNormMode.DEGREES, (3, 1845, 864, 3073), (864, 1299, 2628, 3073)),
])
def test_am1_encoder_preserves_exact_canonical_integer_goals_without_io(monkeypatch, drive, motor, mode, calibration, raws):
    obj,reads,writes = robot(monkeypatch)
    motor_id,offset,low,high = calibration
    bus = FeetechMotorsBus("unused-canonical-roundtrip", {motor:Motor(motor_id,"sts3215",mode)},
                          {motor:MotorCalibration(motor_id,drive,offset,low,high)})
    obj.config.max_relative_target = None
    for raw in raws:
        canonical = obj._normalize_arm_feedback(bus,{motor:raw})[motor]
        accepted,encoded = obj._encode_am1_arm_goals(bus,{motor+".pos":canonical},{motor:raw})
        assert encoded == {motor:raw}
        assert accepted == {motor+".pos":canonical}
    assert reads == writes == []


@pytest.mark.parametrize("drive", [0, 1])
@pytest.mark.parametrize("mode", [MotorNormMode.RANGE_M100_100, MotorNormMode.RANGE_0_100, MotorNormMode.DEGREES])
def test_am1_encoder_keeps_noncanonical_fractional_goal_truncation(monkeypatch, drive, mode):
    obj,_,_ = robot(monkeypatch)
    name = "arm_left_gripper" if mode is MotorNormMode.RANGE_0_100 else ELBOW
    bus = FeetechMotorsBus("unused-fractional-roundtrip",{name:Motor(3,"sts3215",mode)},
                          {name:MotorCalibration(3,drive,-1761,731,3438)})
    obj.config.max_relative_target = None
    raw = 2627.75
    if mode is MotorNormMode.DEGREES:
        goal = (raw-(731+3438)/2)*360/4095
    elif mode is MotorNormMode.RANGE_0_100:
        goal = (raw-731)/2707*100
        if drive:goal = 100-goal
    else:
        goal = (raw-731)/2707*200-100
        if drive:goal = -goal
    _,encoded = obj._encode_am1_arm_goals(bus,{name+".pos":goal},{name:2628})
    assert encoded[name] == 2627, "a noncanonical fractional request keeps existing truncation"
