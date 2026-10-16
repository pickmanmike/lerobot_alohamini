"""Closed ordinary practical presets through the actual native owner boundaries."""

import copy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from examples.alohamini import am1_session_contract as contract
from lerobot.robots.alohamini import alohamini_host as host
from lerobot.robots.alohamini.selected_joint_evidence import live_elbow_spec, live_elbow_workspace
from tests.robots.test_am1_live_native import (
    MeasuredMotor, WORKSPACE_CALIBRATION, WORKSPACE_GOALS, frame, right_robot,
)

RECIPES = ("physical-live-right-elbow-practical-p16", "physical-live-left-elbow-practical-p16")


class PracticalMotor(MeasuredMotor):
    def configure(self, *, right_raw=1854, left_raw=2124):
        self.configure_workspace(right_raw)
        self.workspace_goals["arm_left_elbow_flex"] = left_raw
        self.positions["arm_left_elbow_flex.pos"] = (left_raw - 864) / 2209 * 200 - 100
        self.acknowledged_commands["arm_left_elbow_flex.pos"] = self.positions["arm_left_elbow_flex.pos"]

    def am1_live_workspace_context(self, binding, measured):
        side = "right" if binding["recipe"] == RECIPES[0] else "left"
        selected = "arm_" + side + "_elbow_flex"
        return dict(robot_model="alohamini1", selected_model="sts3215",
                    calibrations={name: list(value) for name, value in self.workspace_calibrations.items()},
                    nonselected_goals={name: value for name, value in self.workspace_goals.items() if name != selected},
                    right_pose=dict(measured)), self.workspace_goals[selected]


def ordinary_lane(recipe, *, right_raw=1854, left_raw=2124):
    now = [10.0]
    identity = {name: str(uuid4()) for name in ("run_id", "host_incarnation")}
    owner = {name: str(uuid4()) for name in ("service_incarnation", "controller_generation", "connection_generation")}
    control = host.AM1LocalControl(**identity, live=True, live_recipe=recipe, clock=lambda: now[0])
    robot = PracticalMotor()
    robot.configure(right_raw=right_raw, left_raw=left_raw)
    return now, identity, owner, control, robot


@pytest.mark.parametrize("recipe,joint,lo,hi", [
    (RECIPES[0], "arm_right_elbow_flex.pos", 1847, 1960),
    (RECIPES[1], "arm_left_elbow_flex.pos", 2011, 2124),
])
def test_native_ordinary_start_resolves_closed_ten_degree_preset_without_trial(recipe, joint, lo, hi):
    now, identity, owner, control, robot = ordinary_lane(recipe)
    assert control.apply(robot, frame(identity, owner, sequence=1))
    summary = control.live_snapshot()
    assert (summary["corridor_min_raw"], summary["corridor_max_raw"]) == (lo, hi)
    assert summary["joint"] == joint and summary["live_deadline_monotonic_s"] == 190.0
    assert summary["reference_norm"] == robot.positions[joint]
    config = contract.PRACTICAL_ELBOW_PRESETS[recipe]
    now[0] = 10.1
    sample = dict(target={joint: config.target_norm(10)}, grant_id=str(uuid4()), seq=1,
                  valid_until_monotonic_s=10.25)
    assert control.apply(robot, frame(identity, owner, sequence=2, mode="active", epoch=2, sample=sample))
    assert all(control.accepted_action[key] == value for key, value in robot.acknowledged_commands.items() if key != joint)
    assert all(robot.events[-1][1][key] == 0 for key in ("x.vel", "y.vel", "theta.vel", "lift_axis.vel"))
    now[0] = 10.26
    assert control.check_live(robot) and control.live_snapshot()["state"] == "expired"
    assert control.apply(robot, frame(identity, owner, sequence=3, epoch=3, revision=1))
    assert control.live_snapshot()["live_deadline_monotonic_s"] == 190.0
    assert not control.apply(robot, frame(identity, owner, sequence=4, mode="active", epoch=4, revision=1, sample=sample))


@pytest.mark.parametrize("recipe", RECIPES)
def test_native_ordinary_target_outside_ten_degree_interval_cannot_write(recipe):
    now, identity, owner, control, robot = ordinary_lane(recipe)
    assert control.apply(robot, frame(identity, owner, sequence=1))
    config = contract.PRACTICAL_ELBOW_PRESETS[recipe]
    before = copy.deepcopy(control.live_snapshot())
    events = len(robot.events)
    now[0] = 10.1
    sample = dict(target={config.spec.joint: before["corridor_max_norm"] + .1},
                  grant_id=str(uuid4()), seq=1, valid_until_monotonic_s=10.25)
    assert not control.apply(robot, frame(identity, owner, sequence=2, mode="active", epoch=2, sample=sample))
    assert len(robot.events) == events and control.live_snapshot() == before


def test_ordinary_native_config_cannot_inherit_experimental_or_other_side_parameters():
    ordinary = contract.PRACTICAL_ELBOW_PRESETS[RECIPES[0]]
    experimental = contract.PracticalElbowConfig(reference_raw=1847, direction=1)
    for foreign in (experimental, contract.PRACTICAL_ELBOW_PRESETS[RECIPES[1]]):
        with pytest.raises(ValueError):
            live_elbow_spec(ordinary.spec.recipe, foreign)
        with pytest.raises(ValueError):
            live_elbow_workspace(ordinary.spec.recipe, foreign)
    assert live_elbow_workspace(experimental.spec.recipe, experimental).command_max_raw == 2074


@pytest.mark.parametrize("recipe", RECIPES)
def test_ordinary_host_start_binds_run_without_experiment_file_or_gain_transaction(monkeypatch, recipe):
    identity = {name: str(uuid4()) for name in ("run_id", "host_incarnation")}
    monkeypatch.setenv("AM1_VISIBLE_CONTROL_RECIPE", recipe)
    monkeypatch.setenv("AM1_SCRIPTED_PREPARE", "1")
    monkeypatch.setenv("AM1_SELECTED_JOINT_EVIDENCE", "1")
    args = host.make_parser().parse_args(["--no_cameras", "--skip_lift_home"])
    args.am1_run_id, args.am1_host_incarnation = identity["run_id"], identity["host_incarnation"]
    args.am1_protected_run_directory = "/private/run"
    robot = SimpleNamespace(config=SimpleNamespace(robot_model="alohamini1"), is_connected=False,
                            _safe_shutdown=lambda **kwargs: [])
    monkeypatch.setattr(host, "AlohaMini", lambda _: robot)
    class ReachedNativeConnection(Exception):
        pass
    def connect(*args, **kwargs):
        assert robot._am1_visible_control_binding == dict(identity, recipe=recipe)
        assert robot._am1_practical_config is contract.PRACTICAL_ELBOW_PRESETS[recipe]
        assert not hasattr(robot, "_am1_right_p20_trial_binding")
        raise ReachedNativeConnection
    monkeypatch.setattr(host, "connect_robot", connect)
    with pytest.raises(ReachedNativeConnection):
        host._run_host(args)
    args.am1_right_p20_trial_file = "/private/run/spent-request.json"
    with pytest.raises(ValueError):
        host.right_p20_trial_binding(args, dict(identity, recipe=recipe))

@pytest.mark.parametrize("recipe,side,ref,offset,low,high,phase", [
    (RECIPES[0], "right", 1847, -658, 827, 3039, 12),
    (RECIPES[1], "left", 2124, 1845, 864, 3073, 12),
])
def test_native_ordinary_readonly_settings_use_selected_mapping_and_hardware_protection(
        monkeypatch, recipe, side, ref, offset, low, high, phase):
    from lerobot.motors import MotorCalibration
    from tests.robots.test_am1_selected_joint_evidence import visible_register_packet
    from tests.robots.test_alohamini_lift_motor_feedback import status

    packets = []
    for torque in (0, 1):
        packet = bytearray(visible_register_packet(torque=torque, goal=ref, present=ref,
                                                   range_min=low, range_max=high))
        packet[18] = phase
        packet[28:30] = (310).to_bytes(2, "little")
        packet[31:33] = (offset if offset >= 0 else 2048 + abs(offset)).to_bytes(2, "little")
        packets.extend([status(bytes(packet), motor_id=3), status(bytes([91]), motor_id=3)])
    robot, evidence, rows, identity, port, reads, writes, left_writes = right_robot(monkeypatch, packets)
    if side == "left":
        from tests.robots.test_am1_selected_joint_evidence import attach_port
        port = attach_port(robot, packets)
    identity["recipe"] = recipe
    robot._am1_practical_config = contract.PRACTICAL_ELBOW_PRESETS[recipe]
    selected = "arm_" + side + "_elbow_flex"
    getattr(robot, side + "_bus").calibration[selected] = MotorCalibration(3, 0, offset, low, high)
    evidence.prepare_visible(robot, identity)
    evidence.verify_visible_active(robot)
    proof = evidence.visible_control_proof(robot)
    assert proof["unchanged"] is True
    assert proof["before"]["registers"]["Protection_Current"] == 310
    assert proof["active"]["registers"]["Lock"] == 1
    assert robot._am1_protected_live_spec().joint == selected + ".pos"
    assert evidence._live_definition.hardware_current_limit_ma == 2015
    assert not writes and not left_writes
    assert not any(row.get("kind") == "right_p20_trial" for row in rows)
    broken = copy.deepcopy(proof)
    broken["active"]["registers"]["Protection_Current"] = 1
    assert not evidence._live_definition.settings_valid(broken)
    robot._am1_right_p20_trial_binding = {"request": {"config": {"reference_raw": 1847, "direction": 1}}}
    with pytest.raises(RuntimeError, match="protected recipe"):
        evidence.visible_control_proof(robot)


def test_ordinary_cross_side_start_keeps_actual_other_elbow_hold_and_original_task_reference():
    # Left's completed practical rest differs from the old exact left2124 context.
    now, identity, owner, control, robot = ordinary_lane(RECIPES[0], right_raw=1934, left_raw=2040)
    assert control.apply(robot, frame(identity, owner, sequence=1))
    summary = control.live_snapshot()
    assert summary["measured_origin_raw"] == 1934
    assert summary["corridor_min_raw"] == 1847 and summary["corridor_max_raw"] == 1960
    assert control.live_held["arm_left_elbow_flex.pos"] == pytest.approx((2040-864)/2209*200-100)
    config = contract.PRACTICAL_ELBOW_PRESETS[RECIPES[0]]
    now[0] = 10.1
    sample = dict(target={config.spec.joint: config.target_norm(0)}, grant_id=str(uuid4()), seq=1,
                  valid_until_monotonic_s=10.25)
    assert control.apply(robot, frame(identity, owner, sequence=2, mode="active", epoch=2, sample=sample))
    assert robot.events[-1][1][config.spec.joint] < summary["reference_norm"]
    assert control.accepted_action["arm_left_elbow_flex.pos"] == control.live_held["arm_left_elbow_flex.pos"]


from tests.robots.test_am1_pre_activation import activation as endpoint_activation, protected_endpoint_case


@pytest.mark.parametrize("recipe", RECIPES)
def test_actual_ordinary_owner_context_preserves_twelve_measurements_and_eleven_frozen_goals(
        endpoint_activation, monkeypatch, recipe):
    from lerobot.motors import MotorCalibration, MotorNormMode

    case = endpoint_activation
    robot = protected_endpoint_case(case, monkeypatch, recipe)
    config = contract.PRACTICAL_ELBOW_PRESETS[recipe]
    robot._am1_practical_config = config
    for side in ("left", "right"):
        bus = getattr(robot, side + "_bus")
        for name in bus.calibration:
            calibration = WORKSPACE_CALIBRATION[name]
            raw = 2040 if name == "arm_left_elbow_flex" else 1934 if name == "arm_right_elbow_flex" else WORKSPACE_GOALS[name]
            bus.motors[name].norm_mode = MotorNormMode.RANGE_0_100 if name.endswith("_gripper") else MotorNormMode.RANGE_M100_100
            bus.calibration[name] = MotorCalibration(*calibration)
            bus.registers.update({(register, name): value for register, value in (
                ("Min_Position_Limit", calibration[3]), ("Max_Position_Limit", calibration[4]),
                ("Homing_Offset", calibration[2]), ("Goal_Position", raw),
                ("Present_Position", 3224 if name == "arm_left_shoulder_lift" else raw))})
    robot.activate_motors(home_lift=False)
    actual = {name + ".pos": value for side in ("left", "right")
              for name, value in robot._normalize_arm_feedback(getattr(robot, side + "_bus"), {
                  name: getattr(robot, side + "_bus").registers["Present_Position", name]
                  for name in getattr(robot, side + "_arm_motors")}).items()}
    selected = config.spec.joint.removesuffix(".pos")
    selected_bus = getattr(robot, config.spec.side + "_bus")
    robot._am1_selected_joint_position = dict(raw=selected_bus.registers["Present_Position", selected],
        read_started_at_monotonic_s=case.clock.now, read_completed_at_monotonic_s=case.clock.now)
    context, raw = robot.am1_live_workspace_context(robot._am1_visible_control_binding, actual)
    assert context["right_pose"] == actual and len(actual) == 12
    assert len(context["nonselected_goals"]) == 11 and selected not in context["nonselected_goals"]
    config.workspace.admission(context, raw, actual[config.spec.joint])
    frozen = dict(context["nonselected_goals"])
    robot.hold_follower_arms()
    assert all(getattr(robot, name.split("_")[1] + "_bus").registers["Goal_Position", name] == value
               for name, value in frozen.items())
    robot._am1_visible_control_binding["run_id"] = str(uuid4())
    with pytest.raises(RuntimeError, match="binding"):
        robot.am1_live_workspace_context(robot._am1_visible_control_binding, actual)
