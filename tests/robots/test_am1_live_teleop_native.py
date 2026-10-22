"""Packet16 complete frames through the native owner; all servo I/O is injected."""

import copy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from lerobot.motors import Motor, MotorCalibration, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus
from lerobot.robots.alohamini import alohamini_host as host
from lerobot.robots.alohamini.alohamini import AlohaMini
from lerobot.robots.alohamini.model_specs import arm_state_keys_for_robot_model

KEYS = sum(arm_state_keys_for_robot_model("alohamini1"), ())
ZERO = {"x.vel": 0, "y.vel": 0, "theta.vel": 0, "lift_axis.vel": 0}


class Bus:
    def __init__(self, side):
        self.motors = {
            key[:-4]: Motor(
                index,
                "sts3215",
                MotorNormMode.RANGE_0_100 if key.endswith("gripper.pos") else MotorNormMode.RANGE_M100_100,
            )
            for index, key in enumerate(KEYS[:6] if side == "left" else KEYS[6:], 1)
        }
        self.calibration = {
            name: MotorCalibration(motor.id, 0, 0, 1000, 3000) for name, motor in self.motors.items()
        }
        elbow = f"arm_{side}_elbow_flex"
        self.calibration[elbow] = MotorCalibration(
            3,
            0,
            1845 if side == "left" else -658,
            864 if side == "left" else 827,
            3073 if side == "left" else 3039,
        )
        normalizer = FeetechMotorsBus("unused-packet16", self.motors, self.calibration)
        self.apply_drive_mode = normalizer.apply_drive_mode
        self.model_resolution_table = normalizer.model_resolution_table
        self._normalize, self._unnormalize = normalizer._normalize, normalizer._unnormalize
        self.raw = {name: 1800 if name.endswith("gripper") else 2000 for name in self.motors}
        self.raw[elbow] = 2124 if side == "left" else 1847
        self.goals, self.writes = dict(self.raw), []
        self.is_connected = True
        self.on_write = None

    def sync_read(self, register, motors, *, normalize=True, **kwargs):
        if register == "Present_Position":
            values = {name: self.raw[name] for name in motors}
        elif register == "Goal_Position":
            values = {name: self.goals[name] for name in motors}
        elif register in {"Present_Current", "Present_Velocity"}:
            return dict.fromkeys(motors, 2 if register == "Present_Current" else 0)
        else:
            raise AssertionError(register)
        if normalize:
            values = self._normalize({self.motors[name].id: value for name, value in values.items()})
            return {name: values[self.motors[name].id] for name in motors}
        return values

    def sync_write(self, register, values, *, normalize=True):
        self.writes.append((register, dict(values)))
        if register == "Goal_Position":
            if normalize:
                raw = self._unnormalize({self.motors[name].id: value for name, value in values.items()})
                values = {name: raw[self.motors[name].id] for name in values}
            self.goals.update(values)
        if self.on_write is not None:
            self.on_write(register, values)


def lane():
    now = [10.0]
    identity = {"run_id": str(uuid4()), "host_incarnation": str(uuid4())}
    binding = {
        "service_incarnation": str(uuid4()),
        "controller_generation": str(uuid4()),
        "connection_generation": str(uuid4()),
    }
    robot = AlohaMini.__new__(AlohaMini)
    robot.config = SimpleNamespace(robot_model="alohamini1", no_follower=False, use_degrees=False)
    robot.left_bus, robot.right_bus = Bus("left"), Bus("right")
    robot.left_arm_motors, robot.right_arm_motors = list(robot.left_bus.motors), list(robot.right_bus.motors)
    robot._am1_teleop_binding = dict(identity, recipe="physical-live-teleop")
    robot.events = []
    robot.stop_motion = lambda: robot.events.append(("zero",))

    def hold():
        robot.events.append(("hold",))
        for bus in (robot.left_bus, robot.right_bus):
            bus.goals = dict(bus.raw)

    robot.hold_follower_arms = hold
    robot.get_observation = lambda: {
        name + ".pos": value
        for bus in (robot.left_bus, robot.right_bus)
        for name, value in robot._normalize_arm_feedback(bus, bus.raw).items()
    }

    def send(action):
        robot._am1_live_before_write()
        robot.events.append(("action", dict(action)))
        return dict(action)

    robot.send_action = send
    control = host.AM1LocalControl(
        **identity, live=True, live_recipe="physical-live-teleop", clock=lambda: now[0]
    )
    return now, identity, binding, robot, control


def frame(identity, binding, number, *, mode="pause", epoch=1, revision=0, sample=None):
    return {
        **ZERO,
        host.AM1_LOCAL_CONTROL_KEY: {
            "version": 2,
            "mode": mode,
            "epoch": epoch,
            **identity,
            "intent_revision": revision,
            "action_sequence": number,
            "live_binding": binding,
            "live_input": sample,
            "live_admitted": True,
        },
    }


def sample(control, *, seq=1, expiry=10.25, source_id=None, acquired=1.01):
    workspace = control.live_snapshot()["workspace"]
    target = {
        key: bound["min"] if abs(workspace["reference"][key] - bound["min"]) > 1e-6 else bound["max"]
        for key, bound in workspace["bounds"].items()
    }
    grant = str(uuid4())
    return {
        "target": target,
        "body": dict(ZERO),
        "grant_id": grant,
        "seq": seq,
        "valid_until_monotonic_s": expiry,
        "source": {
            "kind": "virtual-leaders",
            "source_id": source_id or str(uuid4()),
            "sample_seq": seq,
            "grant_id": grant,
            "acquired_at_monotonic_s": acquired,
            "grant_received_at_monotonic_s": acquired - 0.01,
            "sent_at_monotonic_s": acquired + 0.01,
        },
    }


def test_complete_native_frame_ramps_both_sides_and_grippers_with_original_deadline():
    now, identity, binding, robot, control = lane()
    assert control.apply(robot, frame(identity, binding, 1))
    original = sample(control)
    now[0] = 10.1
    assert control.apply(robot, frame(identity, binding, 2, mode="active", epoch=2, sample=original))
    summary = control.live_snapshot()
    action = robot.events[-1][1]
    assert set(KEYS).issubset(action)
    assert action["arm_left_elbow_flex.pos"] < summary["reference"]["arm_left_elbow_flex.pos"]
    assert action["arm_right_elbow_flex.pos"] > summary["reference"]["arm_right_elbow_flex.pos"]
    assert action["arm_left_gripper.pos"] != summary["reference"]["arm_left_gripper.pos"]
    assert summary["live_deadline_monotonic_s"] == 310
    assert summary["valid_until_monotonic_s"] == 10.25
    assert summary["source"] == original["source"]


def test_tiny_increments_accumulate_without_reseeding_static_measured_pose():
    now, identity, binding, robot, control = lane()
    control.apply(robot, frame(identity, binding, 1))
    original = sample(control)
    values = []
    for index in range(1, 6):
        now[0] = 10 + index * 0.001
        assert control.apply(
            robot, frame(identity, binding, index + 1, mode="active", epoch=2, sample=original)
        )
        values.append(robot.events[-1][1]["arm_right_elbow_flex.pos"])
    assert all(second > first for first, second in zip(values, values[1:], strict=False))
    assert robot.right_bus.raw["arm_right_elbow_flex"] == 1847


def test_expiry_requires_fresh_acquisition_after_rejoin_without_renewing_workspace():
    now, identity, binding, robot, control = lane()
    control.apply(robot, frame(identity, binding, 1))
    original = sample(control)
    frozen = control.live_snapshot()
    now[0] = 10.1
    control.apply(robot, frame(identity, binding, 2, mode="active", epoch=2, sample=original))
    now[0] = 10.26
    assert control.check_live(robot)
    assert control.state == "paused" and robot.events[-2:] == [("zero",), ("hold",)]
    control.apply(robot, frame(identity, binding, 3, epoch=3, revision=1))
    stale = copy.deepcopy(original)
    stale.update(seq=2, valid_until_monotonic_s=10.5, grant_id=str(uuid4()))
    stale["source"]["grant_id"] = stale["grant_id"]
    assert not control.apply(
        robot, frame(identity, binding, 4, mode="active", epoch=4, revision=1, sample=stale)
    )
    fresh = sample(control, seq=2, expiry=10.5, source_id=original["source"]["source_id"], acquired=1.3)
    assert control.apply(robot, frame(identity, binding, 5, mode="active", epoch=4, revision=1, sample=fresh))
    assert control.live_snapshot()["workspace"] == frozen["workspace"]
    assert control.live_snapshot()["reference"] == frozen["reference"]
    assert control.live_snapshot()["live_deadline_monotonic_s"] == 310
    now[0] = 310
    assert not control.check_live(robot)


@pytest.mark.parametrize("mutation", ["partial", "nan", "extra", "expired", "overlong", "source", "body"])
def test_bad_complete_frames_cannot_change_native_targets(mutation):
    now, identity, binding, robot, control = lane()
    control.apply(robot, frame(identity, binding, 1))
    original = sample(control)
    if mutation == "partial":
        original["target"].pop(KEYS[0])
    elif mutation == "nan":
        original["target"][KEYS[0]] = float("nan")
    elif mutation == "extra":
        original["target"]["raw.servo"] = 4
    elif mutation == "expired":
        original["valid_until_monotonic_s"] = 10
    elif mutation == "overlong":
        original["valid_until_monotonic_s"] = 11
    elif mutation == "source":
        original["source"]["acquired_at_monotonic_s"] = 0.5
    else:
        original["body"]["x.vel"] = 20
    events = list(robot.events)
    assert not control.apply(robot, frame(identity, binding, 2, mode="active", epoch=2, sample=original))
    assert robot.events == events


def test_actual_observation_keeps_unclipped_raw_and_original_bus_read_interval(monkeypatch):
    from lerobot.robots.alohamini import alohamini as owner, am1_live_teleop as native
    from tests.robots.test_am1_left_shoulder_evidence import SHOULDER, action_robot

    robot, _, _ = action_robot(monkeypatch)
    robot.id = "synthetic-shared-feedback"
    robot._am1_teleop_binding = {"recipe": "physical-live-teleop"}
    robot.right_arm_motors = []
    robot.lift = SimpleNamespace(contribute_observation=lambda obs: None)
    original_lift = {"present_velocity_raw": -3, "sample_monotonic_s": 49.94, "request_duration_s": 0.02}
    robot._lift_operation = SimpleNamespace(
        contribute_observation=lambda obs: obs.update({"lift_axis.height_mm": 12.5, "lift_axis.vel": -3}),
        last_record=original_lift,
        height_mm=12.5,
    )
    robot.read_and_check_currents = lambda **kwargs: None
    monkeypatch.setattr(native, "poll_health", lambda source: None)
    now = [50.0]
    monkeypatch.setattr(owner.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(owner.time, "perf_counter", lambda: 1000.0)

    def read(address, length, ids, **kwargs):
        now[0] += 0.03
        return dict.fromkeys(ids, 2225 if address == 56 else 0), 0

    monkeypatch.setattr(robot.left_bus, "_sync_read", read)
    measured = robot.get_observation()
    original = measured["_am1_arm_feedback"]["positions"][SHOULDER + ".pos"]
    assert original["raw"] == 2225
    assert original["normalized"] > 100
    assert original["read_started_at_monotonic_s"] == 50
    assert original["read_completed_at_monotonic_s"] == 50.03
    assert measured["_am1_lift_feedback"] == {"version": 1, "height_mm": 12.5, **original_lift}
    assert robot._lift_operation.last_record == original_lift


def test_original_raw_feedback_survives_inside_existing_client_retained_marker():
    _, identity, binding, robot, control = lane()
    control.apply(robot, frame(identity, binding, 1))
    raw = {
        "positions": {
            KEYS[0]: {
                "raw": 2225,
                "normalized": 100.1,
                "read_started_at_monotonic_s": 9.9,
                "read_completed_at_monotonic_s": 9.93,
            }
        }
    }
    lift = {
        "version": 1,
        "height_mm": 12.5,
        "present_velocity_raw": 0,
        "sample_monotonic_s": 9.9,
        "request_duration_s": 0.02,
    }
    observation = {"_am1_arm_feedback": copy.deepcopy(raw), "_am1_lift_feedback": copy.deepcopy(lift)}
    control.annotate(observation, acquired_at=10.0)
    assert observation[host.AM1_LOCAL_FEEDBACK_KEY]["arm_feedback"] == raw
    assert observation[host.AM1_LOCAL_FEEDBACK_KEY]["lift_feedback"] == lift
    observation["_am1_arm_feedback"]["positions"][KEYS[0]]["raw"] = 0
    observation["_am1_lift_feedback"]["present_velocity_raw"] = 200
    assert observation[host.AM1_LOCAL_FEEDBACK_KEY]["arm_feedback"] == raw
    assert observation[host.AM1_LOCAL_FEEDBACK_KEY]["lift_feedback"] == lift


def test_actual_routine_current_and_contact_output_uses_bounded_sink(monkeypatch):
    from tests.robots.test_am1_selected_joint_evidence import robot as make_robot

    robot, _, _ = make_robot(monkeypatch, enabled=False, current=300)
    rows = []
    robot._am1_routine_report = rows.append
    robot.read_and_check_currents(limit_ma=2000, print_currents=True)
    robot._limit_joint_goal_by_current(robot.left_bus, {"arm_left_elbow_flex.pos": 20})
    assert any("[Currents]" in line for line in rows)
    assert any("[JointCurrentLimit]" in line for line in rows)


def test_changed_calibration_is_refused_before_native_frame_write():
    now, identity, binding, robot, control = lane()
    control.apply(robot, frame(identity, binding, 1))
    original = sample(control)
    robot.left_bus.calibration["arm_left_wrist_flex"].homing_offset += 1
    now[0] = 10.1
    events = list(robot.events)
    with pytest.raises(RuntimeError, match="calibration"):
        control.apply(robot, frame(identity, binding, 2, mode="active", epoch=2, sample=original))
    assert robot.events == events


def test_expiry_at_real_write_boundary_holds_without_delivering_frame():
    now, identity, binding, robot, control = lane()
    control.apply(robot, frame(identity, binding, 1))
    original = sample(control)

    def delayed(action):
        now[0] = 10.26
        robot._am1_live_before_write()
        robot.events.append(("unsafe-action", action))

    robot.send_action = delayed
    now[0] = 10.1
    assert not control.apply(robot, frame(identity, binding, 2, mode="active", epoch=2, sample=original))
    assert not any(event[0] == "unsafe-action" for event in robot.events)
    assert robot.events[-2:] == [("zero",), ("hold",)]


def test_pause_cursor_uses_actual_held_goal_when_measured_pose_has_residual():
    now, identity, binding, robot, control = lane()
    original_observation = robot.get_observation

    def residual():
        measured = original_observation()
        measured["arm_right_wrist_flex.pos"] += 1.0
        return measured

    robot.get_observation = residual
    control.apply(robot, frame(identity, binding, 1))
    original = sample(control)
    original["target"] = {
        name + ".pos": value
        for bus in (robot.left_bus, robot.right_bus)
        for name, value in robot._normalize_arm_feedback(bus, bus.goals).items()
    }
    now[0] = 10.1
    control.apply(robot, frame(identity, binding, 2, mode="active", epoch=2, sample=original))
    assert robot.events[-1][1]["arm_right_wrist_flex.pos"] == 0
    assert control.live_snapshot()["reference"]["arm_right_wrist_flex.pos"] == 1


def test_actual_second_bus_expiry_clears_prior_body_and_prevents_late_right_write():
    now, identity, binding, robot, control = lane()
    control.apply(robot, frame(identity, binding, 1))
    robot.config.max_relative_target = 20.0
    robot.logs, robot.cameras = {}, {}
    robot.base_motors = ["base_left_wheel", "base_back_wheel", "base_right_wheel"]
    robot.wheel_radius, robot.base_radius = 0.05, 0.125
    robot.lift = SimpleNamespace(apply_action=lambda action: None)
    robot._gripper_current_limit_ma, robot._joint_current_limit_ma = 500.0, 1800.0
    robot._gripper_release_margin = robot._joint_release_margin = 1.0
    robot._gripper_hold_goal, robot._gripper_hold_direction = {}, {}
    robot._joint_hold_goal, robot._joint_hold_direction = {}, {}
    robot._gripper_open_direction, robot._gripper_hold_close_step = {}, 3
    robot.send_action = AlohaMini.send_action.__get__(robot)

    def stop():
        robot.left_bus.sync_write("Goal_Velocity", dict.fromkeys(robot.base_motors, 0))

    robot.stop_motion = stop
    now[0] = 10.01
    control.apply(robot, frame(identity, binding, 2, mode="active", epoch=2))
    original = sample(control)
    original["body"]["x.vel"] = 0.15
    now[0] = 10.05
    assert control.apply(robot, frame(identity, binding, 3, mode="active", epoch=2, sample=original))
    assert any(
        any(values.values()) for register, values in robot.left_bus.writes if register == "Goal_Velocity"
    )
    robot.left_bus.writes.clear()
    robot.right_bus.writes.clear()

    def slow(register, values):
        if register == "Goal_Position":
            now[0] = 10.26

    robot.left_bus.on_write = slow
    now[0] = 10.1
    assert not control.apply(robot, frame(identity, binding, 4, mode="active", epoch=2, sample=original))
    assert any(register == "Goal_Position" for register, _ in robot.left_bus.writes)
    assert not any(register == "Goal_Position" for register, _ in robot.right_bus.writes)
    assert all(
        not any(values.values()) for register, values in robot.left_bus.writes if register == "Goal_Velocity"
    )
    assert control.live_snapshot()["body"] == ZERO


@pytest.fixture(params=["named", "main"])
def expiry_host(request, monkeypatch):
    if request.param == "named":
        return host
    from tests.robots.test_am1_live_native import load_host_as_main

    return load_host_as_main(monkeypatch)


def real_action_lane(entrypoint):
    now, identity, binding, robot, _ = lane()
    control = entrypoint.AM1LocalControl(
        **identity, live=True, live_recipe="physical-live-teleop", clock=lambda: now[0]
    )
    robot.config.max_relative_target = 20.0
    robot.logs, robot.cameras = {}, {}
    robot.base_motors = ["base_left_wheel", "base_back_wheel", "base_right_wheel"]
    robot.wheel_radius, robot.base_radius = 0.05, 0.125
    robot.lift = SimpleNamespace(apply_action=lambda action: None)
    robot._gripper_current_limit_ma, robot._joint_current_limit_ma = 500.0, 1800.0
    robot._gripper_release_margin = robot._joint_release_margin = 1.0
    robot._gripper_hold_goal, robot._gripper_hold_direction = {}, {}
    robot._joint_hold_goal, robot._joint_hold_direction = {}, {}
    robot._gripper_open_direction, robot._gripper_hold_close_step = {}, 3
    robot.send_action = AlohaMini.send_action.__get__(robot)

    def stop():
        robot.events.append(("zero",))
        robot.left_bus.sync_write("Goal_Velocity", dict.fromkeys(robot.base_motors, 0))

    robot.stop_motion = stop
    return now, identity, binding, robot, control


@pytest.mark.parametrize("boundary", ["before_left", "before_right"])
def test_entrypoint_expiry_holds_without_committing_and_fresh_rejoin_preserves_run(expiry_host, boundary):
    # Synthetic real-send_action boundary, not a reconstruction of the unlogged expiry instant.
    now, identity, binding, robot, control = real_action_lane(expiry_host)
    assert control.apply(robot, frame(identity, binding, 1))
    now[0] = 10.01
    assert control.apply(robot, frame(identity, binding, 2, mode="active", epoch=2))
    original = sample(control)
    original["body"]["x.vel"] = 0.15
    now[0] = 10.05
    assert control.apply(robot, frame(identity, binding, 3, mode="active", epoch=2, sample=original))
    frozen = control.live_snapshot()
    accepted_sequence = control.accepted_action_sequence
    robot.left_bus.writes.clear()
    robot.right_bus.writes.clear()
    if boundary == "before_left":
        previous_read = robot.left_bus.sync_read

        def delayed(register, motors, **kwargs):
            result = previous_read(register, motors, **kwargs)
            if register == "Present_Position":
                now[0] = 10.26
            return result

        robot.left_bus.sync_read = delayed
    else:
        def delayed(register, values):
            if register == "Goal_Position":
                now[0] = 10.26

        robot.left_bus.on_write = delayed
    now[0] = 10.1
    assert not control.apply(robot, frame(identity, binding, 4, mode="active", epoch=2, sample=original))
    snapshot = control.live_snapshot()
    assert control.state == "paused" and snapshot["state"] == "expired"
    assert robot.events[-2:] == [("zero",), ("hold",)]
    assert control.live_sample is None and snapshot["body"] == ZERO
    assert control.accepted_action_sequence == accepted_sequence
    assert snapshot["last_input_seq"] == frozen["last_input_seq"] == 1
    assert snapshot["cursor"] == frozen["cursor"]
    assert snapshot["valid_until_monotonic_s"] == original["valid_until_monotonic_s"]
    assert not set(host.AM1_LIVE_WRITE_PROOF_FIELDS).intersection(snapshot)
    assert "accepted_target" not in snapshot
    assert not any(register == "Goal_Position" for register, _ in robot.right_bus.writes)
    assert any(register == "Goal_Position" for register, _ in robot.left_bus.writes) == (boundary == "before_right")
    assert all(not any(values.values()) for register, values in robot.left_bus.writes if register == "Goal_Velocity")
    if boundary == "before_left":
        robot.left_bus.sync_read = previous_read
    robot.left_bus.on_write = None
    now[0] = 10.3
    assert control.apply(robot, frame(identity, binding, 5, epoch=3, revision=1))
    fresh = sample(control, seq=2, expiry=10.55, source_id=original["source"]["source_id"], acquired=1.3)
    now[0] = 10.35
    assert control.apply(robot, frame(identity, binding, 6, mode="active", epoch=4, revision=1, sample=fresh))
    resumed = control.live_snapshot()
    assert resumed["last_input_seq"] == 2 and resumed["state"] == "active"
    assert control.binding == identity and control.live_binding == binding
    for key in ("reference", "workspace", "live_admitted_at_monotonic_s", "live_deadline_monotonic_s"):
        assert resumed[key] == frozen[key]


@pytest.mark.parametrize("fault", ["motor", "hold", "foreign_same_name"])
def test_entrypoint_expiry_handler_preserves_exact_genuine_fault(expiry_host, fault):
    now, identity, binding, robot, _ = lane()
    control = expiry_host.AM1LocalControl(
        **identity, live=True, live_recipe="physical-live-teleop", clock=lambda: now[0]
    )
    assert control.apply(robot, frame(identity, binding, 1))
    original = sample(control)
    now[0] = 10.1
    assert control.apply(robot, frame(identity, binding, 2, mode="active", epoch=2, sample=original))
    error = (type("AM1LiveInputExpired", (Exception,), {})("foreign")
             if fault == "foreign_same_name" else RuntimeError(fault + " failed"))

    def fail():
        raise error

    def send(action):
        if fault == "hold":
            now[0] = 10.26
            robot._am1_live_before_write()
        raise error

    if fault == "hold":
        robot.hold_follower_arms = fail
    robot.send_action = send
    with pytest.raises(type(error)) as caught:
        control.apply(robot, frame(identity, binding, 3, mode="active", epoch=2, sample=original))
    assert caught.value is error
    assert control.accepted_action_sequence == 2


@pytest.mark.parametrize("last_outcome", ["accepted", "none", "expired"])
def test_whole_robot_goal_evidence_uses_real_apply_standard_and_existing_nonblocking_sink(monkeypatch, last_outcome):
    import json
    import threading

    import scservo_sdk as scs

    from tests.robots.test_alohamini_lift_motor_feedback import ByteStreamPort, status
    from tests.robots.test_am1_selected_joint_evidence import group_payload
    from tools.am1_physical_executor import read_physical_sample

    now, identity, binding, robot, control = lane()
    robot.config.max_relative_target = None
    robot._gripper_hold_goal = robot._joint_hold_goal = {}
    robot._am1_selected_joint_evidence_enabled = False
    evidence = host.AM1SelectedJointEvidence(enabled=False, clock=lambda: now[0])
    control.selected_evidence = evidence
    bus = robot.left_bus
    codec = FeetechMotorsBus("unused-whole-goal", bus.motors, bus.calibration)
    port = ByteStreamPort([status(group_payload(goal=2124, present=2120), motor_id=3)])
    bus.port, bus.port_handler, bus.packet_handler = codec.port, port, scs.PacketHandler(0)
    bus.model_ctrl_table, bus._decode_sign = codec.model_ctrl_table, codec._decode_sign
    bus._encode_sign, bus._split_into_byte_chunks = codec._encode_sign, codec._split_into_byte_chunks
    # The bilateral diagnostic also validates the peer's actual SDK codec, even
    # though this fixture makes only the first (left) sparse read.
    peer = robot.right_bus
    peer_codec = FeetechMotorsBus("unused-whole-peer", peer.motors, peer.calibration)
    peer.port, peer.port_handler, peer.packet_handler = peer_codec.port, ByteStreamPort([]), scs.PacketHandler(0)
    peer.model_ctrl_table, peer._decode_sign = peer_codec.model_ctrl_table, peer_codec._decode_sign
    peer._encode_sign, peer._split_into_byte_chunks = peer_codec._encode_sign, peer_codec._split_into_byte_chunks
    returned = []
    def send(action):
        robot._am1_live_before_write()
        if now[0] >= 10.7 and last_outcome != "accepted":
            if last_outcome == "expired":
                raise host.AM1LiveInputExpired()
            return None
        accepted = {k: v for k,v in action.items() if not k.endswith(".pos")}
        for arm in (robot.left_bus, robot.right_bus):
            goals = {k:v for k,v in action.items() if k.endswith(".pos") and k[:-4] in arm.motors}
            normalized, raw = robot._encode_am1_arm_goals(arm, goals, arm.raw)
            arm.sync_write("Goal_Position", raw, normalize=False)
            accepted.update(normalized)
        returned.append(accepted)
        return accepted
    robot.send_action = send
    assert control.apply(robot, frame(identity, binding, 1))
    frozen = control.live_snapshot()
    original = sample(control, expiry=10.34)
    original["target"] = dict(frozen["cursor"])
    now[0] = 10.1
    assert control.apply(robot, frame(identity, binding, 2, mode="active", epoch=2, sample=original))
    assert evidence.teleop_action["action_sequence"] == control.accepted_action_sequence == 2
    for index in range(2, 7):
        now[0] = 10.+index/10
        fresh = sample(control, seq=index, expiry=now[0]+.24,
                       source_id=original["source"]["source_id"], acquired=1.01+(index-1)/10)
        fresh["target"] = dict(frozen["cursor"])
        assert control.apply(robot, frame(identity, binding, index+1, mode="active", epoch=2, sample=fresh))
    observation = robot.get_observation()
    observation["_am1_arm_feedback"] = {"version": 1, "positions": {
        name+".pos": {"raw": arm.raw[name], "normalized": observation[name+".pos"],
                      "read_started_at_monotonic_s": 10.59, "read_completed_at_monotonic_s": 10.6}
        for arm in (robot.left_bus, robot.right_bus) for name in arm.motors}}
    control.annotate(observation, acquired_at=now[0])
    observation[host.AM1_LOCAL_FEEDBACK_KEY]["read_started_at_monotonic_s"] = 10.59
    evidence.publish(robot, control, observation)
    assert observation["_am1_local_feedback"]["selected_joint"]["goal_readbacks"] == {}
    started, release, written = threading.Event(), threading.Event(), []
    def slow_sink(data):
        started.set()
        assert release.wait(2)
        written.append(data)
        return len(data)
    writer = host.AM1RoutineRecordWriter(write=slow_sink)
    evidence.emit = writer.emit_selected
    try:
        evidence.report(robot, control, observation)
        assert started.wait(1) and not release.is_set()
        assert len(port.writes) == 1
        evidence.publish(robot, control, observation)
        row = next(iter(observation["_am1_local_feedback"]["selected_joint"]["goal_readbacks"].values()))
        assert row["diagnostic_available"] is True
        assert row["goal_position_raw"] == row["requested_goal_raw"] == 2124 and row["present_position_raw"] == 2120
        assert row["position"]["raw"] == 2124 and row["position"]["read_started_at_monotonic_s"] == 10.59
        assert row["action_sequence"] == control.accepted_action_sequence == 7 and row["last_input_seq"] == 6
        assert row["epoch"] == control.epoch == 2 and row["intent_revision"] == control.intent_revision == 0
        consumer = SimpleNamespace(get_observation=lambda: observation,
            latest_am1_local_feedback=copy.deepcopy(observation[host.AM1_LOCAL_FEEDBACK_KEY]),
            latest_observation_received_at=10.61, latest_observation_roundtrip_age_s=.02,
            latest_observation_error=None, latest_raw_observation_keys=frozenset(observation))
        consumed = read_physical_sample(consumer, identity["run_id"], identity["host_incarnation"], -1, clock=lambda: 10.62)
        selected = consumed["selected_joint"]
        assert selected["version"] == 1 and selected["epoch"] == 2 and selected["intent_revision"] == 0
        assert selected["action_sequence"] == 7 and selected["observation_id"] == control.observation_id
        assert selected["acquired_at_monotonic_s"] == 10.6 and selected["goal_readbacks"] == evidence.goal_readbacks
        consumer.latest_am1_local_feedback["selected_joint"]["observation_id"] += 1
        with pytest.raises(ValueError, match="foreign selected native feedback"):
            read_physical_sample(consumer, identity["run_id"], identity["host_incarnation"], -1, clock=lambda: 10.62)
        assert row["run_id"] == identity["run_id"] and row["host_incarnation"] == identity["host_incarnation"]
        assert control.live_snapshot()["workspace"] == frozen["workspace"] and control.live_snapshot()["reference"] == frozen["reference"]
        assert control.live_snapshot()["live_deadline_monotonic_s"] == 310 and control.live_sample["valid_until_monotonic_s"] == 10.84
        returned[-1]["arm_left_elbow_flex.pos"] += 1
        assert evidence.teleop_action["accepted_action"] == control.accepted_action
        if last_outcome != "accepted":
            now[0] = 10.7
            next_sample = sample(control, seq=7, expiry=10.94, source_id=original["source"]["source_id"], acquired=1.61)
            next_sample["target"] = dict(frozen["cursor"])
            assert control.apply(robot, frame(identity, binding, 8, mode="active", epoch=2, sample=next_sample)) is (last_outcome == "none")
            assert evidence.teleop_action is None
        else:
            now[0] = 10.85
            assert control.check_live(robot) and control.state == "paused"
            evidence.publish(robot, control, observation)
            assert "selected_joint" not in observation["_am1_local_feedback"]
        assert robot._am1_selected_joint_evidence_enabled is False and not evidence.enabled
        assert evidence.visible_settings is None and evidence.response_boundary is None and evidence.context is None
    finally:
        release.set()
        writer.close()
    emitted = json.loads(written[0].decode().removeprefix("[AM1 SELECTED JOINT] "))
    assert emitted["goal_position_raw"] == 2124 and emitted["action"]["write_acknowledged"] is False


# Existing native lane; the gain transaction itself has separate injected-wire tests.
def install_left_response_trial(monkeypatch, robot, *, workload="left-maintained", proportional=32, installed=True):
    from lerobot.robots.alohamini import am1_elbow_integral as gains

    binding = {"version": 1, "kind": "left-response-p", "run_id": robot._am1_teleop_binding["run_id"],
               "host_incarnation": robot._am1_teleop_binding["host_incarnation"], "request_sha256": "a" * 64,
               "request": {"proportional": proportional, "workload_segment": workload}}

    class Trial:
        def __init__(self, value, directory):
            assert value == binding
            self.binding, self.directory, self.proportional = copy.deepcopy(value), directory, proportional
            self.restore_required, self.verified, self.events = False, False, []

        @property
        def receipt(self):
            return {"version": 1, "kind": "left-response-p", "run_id": binding["run_id"],
                    "host_incarnation": binding["host_incarnation"], "request_sha256": binding["request_sha256"],
                    "source_head": "b" * 40, "request_id": "c" * 36, "workload_segment": workload,
                    "original_p": 16, "configured_p": proportional, "restore_required": self.restore_required,
                    "original_settings_sha256": "d" * 64, "prepared_at_monotonic_s": 1., "configured_at_monotonic_s": 2.}

        @property
        def evidence(self):
            return {"binding": copy.deepcopy(binding), "original_registers": {"P_Coefficient": 16},
                    "original_settings_sha256": "d" * 64, "configured_p_readback": proportional,
                    "events": list(self.events)}

        def prepare(self, owner):
            assert owner is robot and owner._am1_left_response_p_trial is self
            assert all(bus.registers["Torque_Enable", name] == 0
                       for bus, names in ((owner.left_bus, owner.left_arm_motors), (owner.right_bus, owner.right_arm_motors))
                       for name in names)
            assert owner._am1_teleop_settings["left"]["registers"]["P_Coefficient"]["arm_left_elbow_flex"] == 16
            self.restore_required = True
            self.events.append("prepare")

        def disable_torque(self, owner):
            assert owner is robot
            if hasattr(owner.left_bus, "registers"):
                owner.left_bus.registers["Torque_Enable", "arm_left_elbow_flex"] = 0
                owner.left_bus.registers["Lock", "arm_left_elbow_flex"] = 1
            self.events.append("off")

        def apply(self, owner):
            assert owner is robot and self.restore_required
            owner.left_bus.registers["P_Coefficient", "arm_left_elbow_flex"] = proportional
            self.events.append("apply")

        def verify_active(self, owner):
            assert owner is robot
            self.verified = True
            self.events.append("active")

        def active_receipt(self, owner):
            if owner is not robot or not self.verified or not self.restore_required or owner._am1_left_response_p_binding != self.binding:
                raise RuntimeError("left response active binding changed")
            return self.receipt

        def restore(self, owner):
            assert owner is robot
            self.disable_torque(owner)
            if hasattr(owner.left_bus, "registers"):
                owner.left_bus.registers["P_Coefficient", "arm_left_elbow_flex"] = 16
                owner.left_bus.registers["Lock", "arm_left_elbow_flex"] = 0
            self.events.append("restore")
            self.restore_required = False

    monkeypatch.setattr(gains, "AM1LeftResponseProportionalTrial", Trial, raising=False)
    robot._am1_left_response_p_binding = copy.deepcopy(binding)
    robot._am1_left_response_p_trial_directory = "/private/left-response"
    trial = Trial(binding, robot._am1_left_response_p_trial_directory)
    trial.restore_required = trial.verified = installed
    if installed:
        robot._am1_left_response_p_trial = trial
    return trial


def test_left_response_cli_binding_is_opt_in_and_exact_protected_teleop(monkeypatch):
    from lerobot.robots.alohamini import am1_elbow_integral as gains

    args = SimpleNamespace(robot_model="alohamini1", no_follower=False, no_cameras=True,
                           am1_run_id=str(uuid4()), am1_host_incarnation=str(uuid4()),
                           am1_protected_run_directory="/private/run", am1_left_response_p_trial_file=None)
    assert host.left_response_p_trial_binding(args, None) is None
    args.am1_left_response_p_trial_file = "/private/run/left-response-p.json"
    identity = {"run_id": args.am1_run_id, "host_incarnation": args.am1_host_incarnation, "recipe": "physical-live-teleop"}
    expected = {"kind": "left-response-p", "run_id": args.am1_run_id, "host_incarnation": args.am1_host_incarnation,
                "request": {"source_head": "b" * 40}}
    import subprocess
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: b"b" * 40 + b"\n")
    calls = []
    monkeypatch.setattr(gains, "load_left_response_p_binding", lambda *a: calls.append(a) or expected, raising=False)
    monkeypatch.setenv("AM1_VISIBLE_CONTROL_RECIPE", "physical-live-teleop")
    monkeypatch.setenv("AM1_SCRIPTED_PREPARE", "1")
    monkeypatch.delenv("AM1_SELECTED_JOINT_EVIDENCE", raising=False)
    assert host.left_response_p_trial_binding(args, identity) == expected
    assert calls == [(args.am1_left_response_p_trial_file, "/private/run", args.am1_run_id, args.am1_host_incarnation)]
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: b"9" * 40 + b"\n")
    with pytest.raises(ValueError, match="actual motor source differs"):
        host.left_response_p_trial_binding(args, identity)
    assert len(calls) == 2
    args.robot_model = "alohamini2"
    with pytest.raises(ValueError, match="left.*response|Left.*response"):
        host.left_response_p_trial_binding(args, identity)
    assert len(calls) == 2


@pytest.mark.parametrize("workload", ["left-maintained", "left-reach"])
@pytest.mark.parametrize("change", ["other_arm", "right_gripper", "body", "lift", "left_gripper"])
def test_left_response_native_frames_fence_unselected_targets_before_admission(monkeypatch, workload, change):
    now, identity, owner, robot, control = lane()
    install_left_response_trial(monkeypatch, robot, workload=workload)
    assert control.apply(robot, frame(identity, owner, 0))
    from lerobot.robots.alohamini.am1_live_teleop import before_write
    robot._am1_live_before_write = lambda: before_write(control, robot)
    assert control.apply(robot, frame(identity, owner, 1, mode="active", epoch=2))
    robot.events.clear()  # Empty readiness is not the following input-admission attempt.
    value = sample(control)
    value["target"] = dict(control._live_summary["reference"])
    bound = control._live_summary["workspace"]["bounds"]["arm_left_elbow_flex.pos"]
    value["target"]["arm_left_elbow_flex.pos"] = bound["min"]
    key = {"other_arm": "arm_left_shoulder_pan.pos", "right_gripper": "arm_right_gripper.pos",
           "left_gripper": "arm_left_gripper.pos"}.get(change)
    if key:
        value["target"][key] = control._live_summary["workspace"]["bounds"][key]["max"]
    elif change == "body":
        value["body"]["x.vel"] = .001
    else:
        value["body"]["lift_axis.vel"] = .001
    before = copy.deepcopy(control._live_summary)
    allowed = change == "left_gripper" and workload == "left-reach"
    assert control.apply(robot, frame(identity, owner, 2, mode="active", epoch=2, sample=value)) is allowed
    if not allowed:
        assert control.live_sample is None and control._live_summary == before
        assert not [e for e in robot.events if e[0] == "action"]


def test_left_response_native_quantization_and_rearm_keep_original_reference(monkeypatch):
    now, identity, owner, robot, control = lane()
    install_left_response_trial(monkeypatch, robot)
    assert control.apply(robot, frame(identity, owner, 0))
    original = copy.deepcopy(control._live_summary["reference"])
    value = sample(control)
    value["target"] = dict(original)
    cal = control._live_summary["workspace"]["calibration"]["arm_left_shoulder_pan"]
    value["target"]["arm_left_shoulder_pan.pos"] += 200 / (cal[4] - cal[3])
    assert control.apply(robot, frame(identity, owner, 1, mode="active", epoch=2, sample=value))
    now[0] += .05
    assert control.apply(robot, frame(identity, owner, 2, revision=1, epoch=3))
    assert control._live_summary["reference"] == original
    value = sample(control, seq=2, expiry=now[0] + .24, source_id=value["source"]["source_id"], acquired=1.06)
    value["target"] = dict(original)
    assert control.apply(robot, frame(identity, owner, 3, mode="active", revision=1, epoch=4, sample=value))
    assert control._live_summary["reference"] == original


@pytest.mark.parametrize("changed", ["restored", "binding", "late_target", "late_body"])
def test_left_response_active_fences_cannot_be_lost_between_validation_and_write(monkeypatch, changed):
    now, identity, owner, robot, control = lane()
    trial = install_left_response_trial(monkeypatch, robot)
    assert control.apply(robot, frame(identity, owner, 0))
    value = sample(control)
    value["target"] = dict(control._live_summary["reference"])
    if changed == "restored":
        trial.restore_required = False
    elif changed == "binding":
        robot._am1_left_response_p_binding = dict(trial.binding, run_id=str(uuid4()))
    else:
        send = robot.send_action
        def mutate(action):
            if changed == "late_body":
                control.live_sample["body"]["x.vel"] = .001
            else:
                control.live_sample["target"]["arm_right_gripper.pos"] += 2
            return send(action)
        robot.send_action = mutate
    with pytest.raises(RuntimeError, match="left response"):
        control.apply(robot, frame(identity, owner, 1, mode="active", epoch=2, sample=value))
    assert not [e for e in robot.events if e[0] == "action"]


def test_left_response_optout_does_not_read_git_or_load_a_trial(monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: pytest.fail("ordinary mode must not read Git"))
    assert host.left_response_p_trial_binding(SimpleNamespace(am1_left_response_p_trial_file=None), None) is None


def test_left_response_explicit_comparison_refuses_usb_source_without_changing_ordinary_mode(monkeypatch):
    for selected in (False, True):
        now, identity, owner, robot, control = lane()
        if selected:
            install_left_response_trial(monkeypatch, robot)
        assert control.apply(robot, frame(identity, owner, 0))
        value = sample(control)
        value["target"] = dict(control._live_summary["reference"])
        value["source"]["kind"] = "usb-leaders"
        before = copy.deepcopy(control._live_summary)
        assert control.apply(robot, frame(identity, owner, 1, mode="active", epoch=2, sample=value)) is not selected
        if selected:
            assert control.live_sample is None and control._live_summary == before
            assert not [e for e in robot.events if e[0] == "action"]



def test_whole_robot_bilateral_elbow_readbacks_follow_actual_apply_and_cached_settings(monkeypatch):
    import scservo_sdk as scs

    from tests.robots.test_alohamini_lift_motor_feedback import ByteStreamPort, status
    from tests.robots.test_am1_selected_joint_evidence import cached_elbow_settings, group_payload
    from tools.am1_physical_executor import read_physical_sample

    now, identity, binding, robot, control = lane()
    robot.config.max_relative_target = None
    robot._gripper_hold_goal = robot._joint_hold_goal = {}
    robot._am1_selected_joint_evidence_enabled = False
    rows, returned = [], []
    evidence = host.AM1SelectedJointEvidence(enabled=False, clock=lambda: now[0], emit=rows.append)
    control.selected_evidence = evidence
    ports = {}
    for side, goal, present in (("left", 2124, 2120), ("right", 1847, 1834)):
        bus = getattr(robot, side+"_bus")
        codec = FeetechMotorsBus("unused-bilateral-"+side, bus.motors, bus.calibration)
        port = ports[side] = ByteStreamPort([status(group_payload(goal=goal, present=present), motor_id=3)])
        bus.port, bus.port_handler, bus.packet_handler = codec.port, port, scs.PacketHandler(0)
        bus.model_ctrl_table, bus._decode_sign = codec.model_ctrl_table, codec._decode_sign
        bus._encode_sign, bus._split_into_byte_chunks = codec._encode_sign, codec._split_into_byte_chunks
    cached_elbow_settings(robot)
    def send(action):
        robot._am1_live_before_write()
        accepted = {k: v for k, v in action.items() if not k.endswith(".pos")}
        for bus in (robot.left_bus, robot.right_bus):
            goals = {k: v for k, v in action.items() if k.endswith(".pos") and k[:-4] in bus.motors}
            normalized, raw = robot._encode_am1_arm_goals(bus, goals, bus.raw)
            bus.sync_write("Goal_Position", raw, normalize=False)
            accepted.update(normalized)
        returned.append(accepted)
        return accepted
    robot.send_action = send
    assert control.apply(robot, frame(identity, binding, 1))
    frozen = control.live_snapshot()
    source_id = str(uuid4())
    for index in range(1, 17):
        now[0] = 10.+index/10
        fresh = sample(control, seq=index, expiry=now[0]+.24, source_id=source_id, acquired=1.+index/10)
        fresh["target"] = dict(frozen["cursor"])
        assert control.apply(robot, frame(identity, binding, index+1, mode="active", epoch=2, sample=fresh))
        observation = robot.get_observation()
        observation["_am1_arm_feedback"] = {"version": 1, "positions": {
            name+".pos": {"raw": bus.raw[name], "normalized": observation[name+".pos"],
                          "read_started_at_monotonic_s": now[0]-.01, "read_completed_at_monotonic_s": now[0]}
            for bus in (robot.left_bus, robot.right_bus) for name in bus.motors}}
        control.annotate(observation, acquired_at=now[0])
        observation[host.AM1_LOCAL_FEEDBACK_KEY]["read_started_at_monotonic_s"] = now[0]-.01
        evidence.report(robot, control, observation)
    assert len(ports["left"].writes) == len(ports["right"].writes) == 1
    assert [row["action"]["side"] for row in rows] == ["left", "right"]
    assert [(row["requested_goal_raw"], row["goal_position_raw"], row["present_position_raw"]) for row in rows] == [(2124, 2124, 2120), (1847, 1847, 1834)]
    evidence.publish(robot, control, observation)
    consumer = SimpleNamespace(get_observation=lambda: observation,
        latest_am1_local_feedback=copy.deepcopy(observation[host.AM1_LOCAL_FEEDBACK_KEY]),
        latest_observation_received_at=now[0]+.01, latest_observation_roundtrip_age_s=.02,
        latest_observation_error=None, latest_raw_observation_keys=frozenset(observation))
    consumed = read_physical_sample(consumer, identity["run_id"], identity["host_incarnation"], -1, clock=lambda: now[0]+.02)
    proof = consumed["selected_joint"]["teleop_elbow_settings"]
    assert proof["right"]["calibration"] == [3, 0, -658, 827, 3039]
    assert proof["right"]["original"]["P_Coefficient"] == proof["right"]["active"]["P_Coefficient"] == 16
    assert proof["left"]["active"]["Firmware_Minor_Version"] == 10
    assert consumed["selected_joint"]["action_sequence"] == control.accepted_action_sequence == 17
    assert all(row["diagnostic_available"] and row["action"]["write_acknowledged"] is False for row in rows)
    assert all(value == 0 for key, value in returned[-1].items() if not key.endswith(".pos"))
    assert control.live_snapshot()["workspace"] == frozen["workspace"]
    assert control.live_snapshot()["live_deadline_monotonic_s"] == 310


def install_right_response_trial(monkeypatch, robot, *, workload="right-peer-fixed", proportional=32, installed=True):
    from lerobot.robots.alohamini import am1_elbow_integral as gains

    binding = {"version": 1, "kind": "right-response-p", "run_id": robot._am1_teleop_binding["run_id"],
               "host_incarnation": robot._am1_teleop_binding["host_incarnation"], "request_sha256": "a" * 64,
               "request": {"proportional": proportional, "workload_segment": workload}}

    class Trial:
        def __init__(self, value, directory):
            assert value == binding
            self.binding, self.directory, self.proportional = copy.deepcopy(value), directory, proportional
            self.restore_required, self.verified, self.events = False, False, []

        @property
        def receipt(self):
            return {"version": 1, "kind": "right-response-p", "run_id": binding["run_id"],
                    "host_incarnation": binding["host_incarnation"], "request_sha256": binding["request_sha256"],
                    "source_head": "b" * 40, "request_id": "c" * 36, "workload_segment": workload,
                    "original_p": 16, "configured_p": proportional, "restore_required": self.restore_required,
                    "original_settings_sha256": "d" * 64, "prepared_at_monotonic_s": 1., "configured_at_monotonic_s": 2.}

        @property
        def evidence(self):
            return {"binding": copy.deepcopy(binding), "original_registers": {"P_Coefficient": 16},
                    "original_settings_sha256": "d" * 64, "configured_p_readback": proportional,
                    "events": list(self.events)}

        def prepare(self, owner):
            assert owner is robot and owner._am1_right_response_p_trial is self
            assert all(bus.registers["Torque_Enable", name] == 0
                       for bus, names in ((owner.right_bus, owner.right_arm_motors), (owner.right_bus, owner.right_arm_motors))
                       for name in names)
            assert owner._am1_teleop_settings["right"]["registers"]["P_Coefficient"]["arm_right_elbow_flex"] == 16
            self.restore_required = True
            self.events.append("prepare")

        def disable_torque(self, owner):
            assert owner is robot
            if hasattr(owner.right_bus, "registers"):
                owner.right_bus.registers["Torque_Enable", "arm_right_elbow_flex"] = 0
                owner.right_bus.registers["Lock", "arm_right_elbow_flex"] = 1
            self.events.append("off")

        def apply(self, owner):
            assert owner is robot and self.restore_required
            owner.right_bus.registers["P_Coefficient", "arm_right_elbow_flex"] = proportional
            self.events.append("apply")

        def verify_active(self, owner):
            assert owner is robot
            self.verified = True
            self.events.append("active")

        def active_receipt(self, owner):
            if owner is not robot or not self.verified or not self.restore_required or owner._am1_right_response_p_binding != self.binding:
                raise RuntimeError("right response active binding changed")
            return self.receipt

        def restore(self, owner):
            assert owner is robot
            self.disable_torque(owner)
            if hasattr(owner.right_bus, "registers"):
                owner.right_bus.registers["P_Coefficient", "arm_right_elbow_flex"] = 16
                owner.right_bus.registers["Lock", "arm_right_elbow_flex"] = 0
            self.events.append("restore")
            self.restore_required = False

    monkeypatch.setattr(gains, "AM1RightResponseProportionalTrial", Trial, raising=False)
    robot._am1_right_response_p_binding = copy.deepcopy(binding)
    robot._am1_right_response_p_trial_directory = "/private/right-response"
    trial = Trial(binding, robot._am1_right_response_p_trial_directory)
    trial.restore_required = trial.verified = installed
    if installed:
        robot._am1_right_response_p_trial = trial
    return trial



def test_right_response_cli_binding_is_opt_in_and_exact_protected_teleop(monkeypatch):
    from lerobot.robots.alohamini import am1_elbow_integral as gains

    args = SimpleNamespace(robot_model="alohamini1", no_follower=False, no_cameras=True,
                           am1_run_id=str(uuid4()), am1_host_incarnation=str(uuid4()),
                           am1_protected_run_directory="/private/run", am1_right_response_p_trial_file=None)
    assert host.right_response_p_trial_binding(args, None) is None
    args.am1_right_response_p_trial_file = "/private/run/right-response-p.json"
    identity = {"run_id": args.am1_run_id, "host_incarnation": args.am1_host_incarnation, "recipe": "physical-live-teleop"}
    expected = {"kind": "right-response-p", "run_id": args.am1_run_id, "host_incarnation": args.am1_host_incarnation,
                "request": {"source_head": "b" * 40}}
    import subprocess
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: b"b" * 40 + b"\n")
    calls = []
    monkeypatch.setattr(gains, "load_right_response_p_binding", lambda *a: calls.append(a) or expected, raising=False)
    monkeypatch.setenv("AM1_VISIBLE_CONTROL_RECIPE", "physical-live-teleop")
    monkeypatch.setenv("AM1_SCRIPTED_PREPARE", "1")
    monkeypatch.delenv("AM1_SELECTED_JOINT_EVIDENCE", raising=False)
    assert host.right_response_p_trial_binding(args, identity) == expected
    assert calls == [(args.am1_right_response_p_trial_file, "/private/run", args.am1_run_id, args.am1_host_incarnation)]
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: b"9" * 40 + b"\n")
    with pytest.raises(ValueError, match="actual motor source differs"):
        host.right_response_p_trial_binding(args, identity)
    assert len(calls) == 2
    args.robot_model = "alohamini2"
    with pytest.raises(ValueError, match="right.*response|Right.*response"):
        host.right_response_p_trial_binding(args, identity)
    assert len(calls) == 2


@pytest.mark.parametrize("change", ["other_arm", "right_gripper", "body", "lift", "usb"])
def test_right_response_fixed_native_frames_fence_other_roles_and_source(monkeypatch, change):
    now, identity, owner, robot, control = lane()
    install_right_response_trial(monkeypatch, robot)
    assert control.apply(robot, frame(identity, owner, 0))
    from lerobot.robots.alohamini.am1_live_teleop import before_write
    robot._am1_live_before_write = lambda: before_write(control, robot)
    assert control.apply(robot, frame(identity, owner, 1, mode="active", epoch=2))
    robot.events.clear()
    value = sample(control)
    value["target"] = dict(control._live_summary["reference"])
    if change in {"other_arm", "right_gripper"}:
        key = "arm_left_shoulder_pan.pos" if change == "other_arm" else "arm_right_gripper.pos"
        value["target"][key] = control._live_summary["workspace"]["bounds"][key]["max"]
    elif change == "usb":
        value["source"]["kind"] = "usb-leaders"
    else:
        value["body"]["x.vel" if change == "body" else "lift_axis.vel"] = .001
    before = copy.deepcopy(control._live_summary)
    assert not control.apply(robot, frame(identity, owner, 2, mode="active", epoch=2, sample=value))
    assert control.live_sample is None and control._live_summary == before
    assert not [event for event in robot.events if event[0] == "action"]


def test_right_response_fixed_native_target_and_rearm_keep_original_reference(monkeypatch):
    now, identity, owner, robot, control = lane()
    install_right_response_trial(monkeypatch, robot)
    assert control.apply(robot, frame(identity, owner, 0))
    original = copy.deepcopy(control._live_summary)
    value = sample(control)
    value["target"] = dict(original["reference"])
    value["target"]["arm_right_elbow_flex.pos"] = original["workspace"]["bounds"]["arm_right_elbow_flex.pos"]["max"]
    assert control.apply(robot, frame(identity, owner, 1, mode="active", epoch=2, sample=value))
    now[0] += .05
    assert control.apply(robot, frame(identity, owner, 2, revision=1, epoch=3))
    assert control._live_summary["reference"] == original["reference"]
    assert control._live_summary["live_deadline_monotonic_s"] == original["live_deadline_monotonic_s"]


@pytest.mark.parametrize("side", ["left", "right"])
def test_response_host_rejects_two_trial_files_before_loading_or_source_query(monkeypatch, side):
    from lerobot.robots.alohamini import am1_elbow_integral as gains
    args = SimpleNamespace(robot_model="alohamini1", no_follower=False, no_cameras=True,
                           am1_run_id=str(uuid4()), am1_host_incarnation=str(uuid4()),
                           am1_protected_run_directory="/private/run",
                           am1_left_response_p_trial_file="/private/run/left.json",
                           am1_right_response_p_trial_file="/private/run/right.json")
    identity = {"run_id": args.am1_run_id, "host_incarnation": args.am1_host_incarnation,
                "recipe": "physical-live-teleop"}
    monkeypatch.setenv("AM1_VISIBLE_CONTROL_RECIPE", "physical-live-teleop")
    monkeypatch.setenv("AM1_SCRIPTED_PREPARE", "1")
    def no_load(*args):
        raise AssertionError("loader must not run for dual selection")
    monkeypatch.setattr(gains, "load_" + side + "_response_p_binding", no_load)
    with pytest.raises(ValueError, match="exact protected"):
        getattr(host, side + "_response_p_trial_binding")(args, identity)


@pytest.mark.parametrize("changed", ["restored", "binding", "late_target", "late_body"])
def test_right_response_fixed_fence_remains_at_actual_write(monkeypatch, changed):
    now, identity, owner, robot, control = lane()
    trial = install_right_response_trial(monkeypatch, robot)
    assert control.apply(robot, frame(identity, owner, 0))
    value = sample(control)
    value["target"] = dict(control._live_summary["reference"])
    if changed == "restored":
        trial.restore_required = False
    elif changed == "binding":
        robot._am1_right_response_p_binding = dict(trial.binding, run_id=str(uuid4()))
    else:
        send = robot.send_action
        def mutate(action):
            if changed == "late_body":
                control.live_sample["body"]["x.vel"] = .001
            else:
                control.live_sample["target"]["arm_left_gripper.pos"] += 2
            return send(action)
        robot.send_action = mutate
    with pytest.raises(RuntimeError, match="response"):
        control.apply(robot, frame(identity, owner, 1, mode="active", epoch=2, sample=value))
    assert not [event for event in robot.events if event[0] == "action"]


@pytest.mark.parametrize("bad", [None, "no_trial", "inactive", "binding", "outside", "gain", "segment", "unknown"])
def test_current_rest_native_workspace_requires_exact_active_trial_and_keeps_original_reference(monkeypatch, bad):
    now, identity, owner, robot, control = lane()
    profile = "left-current-rest-1051-1164"
    trial = install_left_response_trial(monkeypatch, robot, workload="left-maintained" if bad == "segment" else "left-reach", proportional=16 if bad == "gain" else 32)
    trial.binding["request"]["workspace_profile"] = "unknown" if bad == "unknown" else profile
    robot._am1_left_response_p_binding = copy.deepcopy(trial.binding)
    robot.left_bus.raw["arm_left_elbow_flex"] = 1090
    robot.left_bus.goals["arm_left_elbow_flex"] = 1090
    if bad == "no_trial":
        robot._am1_left_response_p_trial = robot._am1_left_response_p_binding = None
    elif bad == "inactive":
        trial.verified = False
    elif bad == "binding":
        robot._am1_left_response_p_binding["run_id"] = str(uuid4())
    elif bad == "outside":
        robot.left_bus.raw["arm_left_elbow_flex"] = 1165
        robot.left_bus.goals["arm_left_elbow_flex"] = 1165
    if bad:
        with pytest.raises(RuntimeError):
            control.apply(robot, frame(identity, owner, 0))
    else:
        assert control.apply(robot, frame(identity, owner, 0))
        workspace = copy.deepcopy(control._live_summary["workspace"])
        assert workspace["workspace_profile"] == profile
        assert workspace["raw_bounds"]["arm_left_elbow_flex.pos"] == [1051, 1164]
        assert workspace["raw_bounds"]["arm_right_elbow_flex.pos"] == [1847, 1960]
        original = copy.deepcopy(workspace["reference"])
        now[0] += .05
        assert control.apply(robot, frame(identity, owner, 1, epoch=3, revision=1))
        assert control._live_summary["workspace"] == workspace and control._live_summary["reference"] == original
