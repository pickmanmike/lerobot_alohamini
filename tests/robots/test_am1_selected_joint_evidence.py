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
