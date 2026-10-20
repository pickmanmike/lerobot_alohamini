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
