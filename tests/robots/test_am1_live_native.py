"""Live samples through the existing native owner, with device IO injected."""

import copy
from uuid import uuid4

import pytest

from examples.alohamini.am1_finite_task import JOINT_KEYS
from lerobot.robots.alohamini import alohamini_host as host
from tests.robots.test_am1_protected_host_boundary import Motor, binding
from lerobot.motors import MotorCalibration
from lerobot.motors.feetech import FeetechMotorsBus
from tests.robots.test_am1_selected_joint_evidence import robot as selected_robot
from tests.robots.test_am1_selected_joint_evidence import visible_register_packet, visible_robot
from tests.robots.test_alohamini_lift_motor_feedback import status

KEY = "arm_left_elbow_flex.pos"


class MeasuredMotor(Motor):
    def __init__(self):
        super().__init__()
        self.positions = dict.fromkeys(JOINT_KEYS, 0.0)
        self.acknowledged_commands = dict.fromkeys(JOINT_KEYS, 0.0)

    def configure_workspace(self, raw=1845):
        self.workspace_calibrations = dict(WORKSPACE_CALIBRATION)
        self.workspace_goals = dict(WORKSPACE_GOALS)
        self.workspace_goals[RIGHT] = raw
        for name, value in self.workspace_goals.items():
            calibration = self.workspace_calibrations[name]
            fraction = (value-calibration[3])/(calibration[4]-calibration[3])
            q = fraction*100 if name.endswith("_gripper") else fraction*200-100
            self.positions[name+".pos"] = q
            self.acknowledged_commands[name+".pos"] = q
        self.workspace_raw = raw

    def am1_live_workspace_context(self, binding, measured):
        # Explicit simulated registers/context, checked by the same contract resolver.
        return dict(robot_model="alohamini1", selected_model="sts3215",
                    calibrations={k:list(v) for k,v in self.workspace_calibrations.items()},
                    nonselected_goals={k:v for k,v in self.workspace_goals.items() if k!=RIGHT},
                    right_pose={k:measured[k] for k in measured if k.startswith("arm_right_") and k!=RIGHT_KEY}), self.workspace_raw

    def am1_live_arm_commands(self, binding):
        return dict(self.acknowledged_commands)

    def get_observation(self):
        return dict(self.positions)


def lane():
    now = [10.0]
    identity = binding()
    owner = {"service_incarnation": str(uuid4()), "controller_generation": str(uuid4()), "connection_generation": str(uuid4())}
    control = host.AM1LocalControl(**identity, live=True, clock=lambda: now[0])
    robot = MeasuredMotor()
    return now, identity, owner, control, robot


def frame(identity, owner, *, sequence, mode="pause", epoch=1, revision=0, sample=None, admitted=True):
    return {
        "x.vel": 0, "y.vel": 0, "theta.vel": 0, "lift_axis.vel": 0,
        host.AM1_LOCAL_CONTROL_KEY: {
            "version": 2, "mode": mode, "epoch": epoch, **identity,
            "intent_revision": revision, "action_sequence": sequence,
            "live_binding": owner, "live_input": sample, "live_admitted": admitted,
        },
    }


def sample(*, seq=1, expiry=10.25, target=-2.0):
    return {"target": {KEY: target}, "grant_id": str(uuid4()), "seq": seq,
            "valid_until_monotonic_s": expiry}


def test_native_rate_cursor_completes_only_selected_joint_and_retains_expiry():
    now, identity, owner, control, robot = lane()
    assert control.apply(robot, frame(identity, owner, sequence=1))
    original = sample()
    now[0] += .1
    assert control.apply(robot, frame(identity, owner, sequence=2, mode="active", epoch=2, sample=original))
    action = robot.events[-1][1]
    assert -.1000001 <= action[KEY] <= 0
    assert all(control.accepted_action[k] == 0 for k in JOINT_KEYS if k != KEY)
    assert all(action[k] == 0 for k in ("x.vel", "y.vel", "theta.vel", "lift_axis.vel"))
    assert control.live_snapshot()["valid_until_monotonic_s"] == 10.25


def test_continuing_native_sender_cannot_renew_expired_sample_and_fresh_rejoin_keeps_run():
    now, identity, owner, control, robot = lane()
    control.apply(robot, frame(identity, owner, sequence=1))
    original = sample()
    now[0] = 10.1
    control.apply(robot, frame(identity, owner, sequence=2, mode="active", epoch=2, sample=original))
    deadline = control.live_snapshot()["live_deadline_monotonic_s"]
    now[0] = 10.26
    assert not control.apply(robot, frame(identity, owner, sequence=3, mode="active", epoch=2, sample=original))
    assert control.state == "paused" and robot.events[-2:] == [("zero",), ("hold",)]
    assert control.live_snapshot()["state"] == "expired"
    robot.positions[KEY] = -.06
    control.apply(robot, frame(identity, owner, sequence=4, epoch=3, revision=1))
    assert control.live_snapshot()["cursor_norm"] == -.06
    assert control.live_snapshot()["reference_norm"] == 0
    assert control.live_snapshot()["live_deadline_monotonic_s"] == deadline
    fresh = sample(seq=2, expiry=10.5, target=-1)
    assert control.apply(robot, frame(identity, owner, sequence=5, mode="active", epoch=4, revision=1, sample=fresh))


@pytest.mark.parametrize("mutation", ["expiry", "other_joint", "foreign_connection", "old_seq"])
def test_invalid_samples_cannot_write_or_renew(mutation):
    now, identity, owner, control, robot = lane()
    control.apply(robot, frame(identity, owner, sequence=1))
    original = sample()
    now[0] = 10.1
    control.apply(robot, frame(identity, owner, sequence=2, mode="active", epoch=2, sample=original))
    changed, foreign = copy.deepcopy(original), copy.deepcopy(owner)
    if mutation == "expiry":
        changed["valid_until_monotonic_s"] += 1
    elif mutation == "other_joint":
        changed["target"]["arm_right_elbow_flex.pos"] = 1
    elif mutation == "foreign_connection":
        foreign["connection_generation"] = str(uuid4())
    else:
        changed["seq"] = 0
    count = len(robot.events)
    assert not control.apply(robot, frame(identity, foreign, sequence=3, mode="active", epoch=2, sample=changed))
    assert len(robot.events) == count
    assert control.live_snapshot()["valid_until_monotonic_s"] == original["valid_until_monotonic_s"]


def test_native_expiry_checks_without_commands_and_live_budget_is_not_renewed():
    now, identity, owner, control, robot = lane()
    control.apply(robot, frame(identity, owner, sequence=1))
    now[0] = 10.1
    control.apply(robot, frame(identity, owner, sequence=2, mode="active", epoch=2, sample=sample()))
    now[0] = 10.26
    assert control.check_live(robot)
    assert control.state == "paused"
    now[0] = 100
    assert not control.check_live(robot)


def test_real_motor_prewrite_read_delay_expires_before_goal_write(monkeypatch):
    from tests.robots.test_am1_selected_joint_evidence import robot as selected_robot

    now, identity, owner, control, _ = lane()
    robot, reads, writes = selected_robot(monkeypatch)
    robot.config.no_follower = False
    robot.lift.stop = lambda: None
    monkeypatch.setattr(robot.left_bus, "write", lambda register, motor, value, **kwargs: None)
    held = []
    robot.hold_follower_arms = lambda: held.append(True)
    robot.get_observation = lambda: dict.fromkeys(JOINT_KEYS, 0.0)
    control.apply(robot, frame(identity, owner, sequence=1))
    original_read = robot._read_arm_positions

    def delayed(*args, **kwargs):
        result = original_read(*args, **kwargs)
        now[0] = 10.26
        return result

    monkeypatch.setattr(robot, "_read_arm_positions", delayed)
    now[0] = 10.1
    assert not control.apply(robot, frame(identity, owner, sequence=2, mode="active", epoch=2, sample=sample()))
    assert not [row for row in writes if row[0] == 42]
    assert held and control.live_snapshot()["state"] == "expired"


class ExecutorNativeHost:
    provenance = "protected-host"

    def __init__(self):
        self.robot = MeasuredMotor()
        self.control = None
        self.actions = []

    def start(self, run_id, incarnation, recipe, cancel):
        if recipe.name == "physical-live-right-elbow":
            self.robot.configure_workspace()
        self.control = host.AM1LocalControl(run_id=run_id, host_incarnation=incarnation, live=True, live_recipe=recipe.name)

    def poll(self, previous):
        import time

        self.control.check_live(self.robot)
        observation = self.robot.get_observation()
        self.control.annotate(observation, acquired_at=time.monotonic())
        marker = observation[host.AM1_LOCAL_FEEDBACK_KEY]
        return {"positions": self.robot.get_observation(), "sequence": marker["observation_id"],
                "at": time.monotonic(), "ack": marker, "observation": observation,
                "provenance": "protected-host/measured-feedback"}

    def send(self, action):
        self.actions.append(copy.deepcopy(action))
        self.control.apply(self.robot, action)

    def finish(self, status):
        return {"cleanup": "physical_readback_verified", "uncertain": False, "terminal_status": status}


def test_real_executor_mailbox_streams_original_expiry_and_never_creates_finite_task():
    import time

    from examples.alohamini.am1_session_contract import RECIPES
    from tests.robots.test_am1_physical_executor import executor, until

    io = ExecutorNativeHost()
    e = executor(io)
    recipe = RECIPES["physical-live-left-elbow"]
    e.bind_run(str(uuid4()), recipe)
    identity = {"run_id": e.run_id, "host_incarnation": e.host_incarnation,
                "service_incarnation": str(uuid4()), "controller_generation": str(uuid4()), "connection_generation": str(uuid4()),
                "intent_revision": 0}
    e.bind_control(**identity)
    e.begin(recipe)
    try:
        until(lambda: e.evidence()["lifecycle"]["phase"] == "live")
        assert e.dispatch(recipe)
        until(lambda: e.evidence()["active_acknowledged"])
        expiry = time.monotonic() + .25
        assert e.apply({KEY: -2}, valid_until_monotonic_s=expiry, grant_id=str(uuid4()), seq=1, **identity)
        until(lambda: e.evidence()["live_input"]["state"] == "active")
        until(lambda: e.evidence()["live_input"]["state"] == "expired")
        samples = [a[host.AM1_LOCAL_CONTROL_KEY]["live_input"] for a in io.actions
                   if a[host.AM1_LOCAL_CONTROL_KEY].get("live_input") is not None]
        assert samples and all(s["valid_until_monotonic_s"] == expiry for s in samples)
        assert e.task is None
    finally:
        e.finish("stopped")
        e.worker.join(2)
        assert not e.worker.is_alive()


def test_stale_epoch_cannot_commit_new_sample_or_change_expiry():
    now, identity, owner, control, robot = lane()
    control.apply(robot, frame(identity, owner, sequence=1))
    now[0] = 10.1
    control.apply(robot, frame(identity, owner, sequence=2, mode="active", epoch=2, sample=sample()))
    before = control.live_snapshot()
    newer = sample(seq=2, expiry=10.4)
    assert not control.apply(robot, frame(identity, owner, sequence=3, mode="active", epoch=0, sample=newer))
    assert control.live_snapshot() == before


def test_new_intent_or_extended_grant_requires_qualified_pause_before_input():
    now, identity, owner, control, robot = lane()
    control.apply(robot, frame(identity, owner, sequence=1))
    now[0] = 10.1
    original = sample()
    control.apply(robot, frame(identity, owner, sequence=2, mode="active", epoch=2, sample=original))
    before = control.live_snapshot()
    assert not control.apply(robot, frame(identity, owner, sequence=3, mode="active", epoch=2,
                                          revision=1, sample=sample(seq=2, expiry=10.3)))
    assert not control.apply(robot, frame(identity, owner, sequence=4, mode="active", epoch=2,
                                          sample=sample(seq=2, expiry=100)))
    assert control.live_snapshot() == before


def test_grant_readiness_does_not_clear_an_applied_sample():
    now, identity, owner, control, robot = lane()
    control.apply(robot, frame(identity, owner, sequence=1))
    now[0] = 10.1
    original = sample()
    control.apply(robot, frame(identity, owner, sequence=2, mode="active", epoch=2, sample=original))
    count = len(robot.events)
    assert not control.apply(robot, frame(identity, owner, sequence=3, mode="active", epoch=2))
    assert len(robot.events) == count and control.live_sample == original


@pytest.mark.parametrize("recipe,joint,direction", [
    ("physical-live-left-elbow", "arm_left_elbow_flex.pos", -1),
    ("physical-live-right-elbow", "arm_right_elbow_flex.pos", -1),
])
def test_production_host_ipc_expiry_retains_same_run_and_new_intent_resumes(recipe, joint, direction):
    import os
    import time

    if os.name == "nt":
        pytest.skip("filesystem IPC production host is exercised in isolated native Pi checks")
    from tests.robots.test_am1_physical_ipc_transport import actual_mock_host, receive_feedback

    with actual_mock_host(live=True, live_recipe=recipe) as peer:
        owner = {k: str(uuid4()) for k in ("service_incarnation", "controller_generation", "connection_generation")}
        peer.client.send_action(frame(peer.identity, owner, sequence=1))
        _, initial = receive_feedback(peer, lambda m: m.get("live_input", {}).get("state") == "held")
        expiry = time.monotonic() + .25
        value = initial["live_input"]
        target_norm = value["cursor_norm"] + direction * .5
        assert value["corridor_min_norm"] <= target_norm <= value["corridor_max_norm"]
        target = sample(expiry=expiry, target=target_norm)
        target["target"] = {joint: target_norm}
        peer.client.send_action(frame(peer.identity, owner, sequence=2, mode="active", epoch=2, sample=target))
        _, active = receive_feedback(peer, lambda m: m.get("live_input", {}).get("state") == "active")
        assert active["live_input"]["valid_until_monotonic_s"] == expiry
        _, expired = receive_feedback(peer, lambda m: m.get("live_input", {}).get("state") == "expired")
        assert expired["run_id"] == initial["run_id"] and expired["state"] == "paused"
        peer.client.send_action(frame(peer.identity, owner, sequence=3, epoch=3, revision=1))
        _, held = receive_feedback(peer, lambda m: m.get("epoch") == 3 and m.get("state") == "paused")
        assert held["live_input"]["live_deadline_monotonic_s"] == initial["live_input"]["live_deadline_monotonic_s"]
        cursor = held["live_input"]
        fresh_target = max(cursor["corridor_min_norm"], min(cursor["corridor_max_norm"],
                                                        cursor["cursor_norm"] + direction))
        peer.client.send_action(frame(peer.identity, owner, sequence=4, mode="active", epoch=4, revision=1,
                                      sample={"target": {joint: fresh_target}, "grant_id": str(uuid4()), "seq": 2,
                                              "valid_until_monotonic_s": time.monotonic() + .25}))
        assert receive_feedback(peer, lambda m: m.get("epoch") == 4 and m.get("state") == "active")


def live_guard(monkeypatch, *, start=1224, range_min=864, range_max=3073):
    from lerobot.robots.alohamini.selected_joint_evidence import LIVE_RECIPE
    from tests.robots.test_alohamini_lift_motor_feedback import status
    from tests.robots.test_am1_selected_joint_evidence import visible_register_packet, visible_robot

    packets = [status(visible_register_packet(range_min=range_min, range_max=range_max, present=start, goal=start), motor_id=3),
               status(bytes([91]), motor_id=3),
               status(visible_register_packet(torque=1, range_min=range_min, range_max=range_max, present=start, goal=start), motor_id=3),
               status(bytes([91]), motor_id=3)]
    robot, evidence, rows, identity, port, reads, writes = visible_robot(monkeypatch, packets)
    calibration = robot.left_bus.calibration["arm_left_elbow_flex"]
    calibration.range_min, calibration.range_max = range_min, range_max
    identity["recipe"] = LIVE_RECIPE
    now = [10.0]
    evidence.clock = lambda: now[0]
    evidence.prepare_visible(robot, identity)
    evidence.verify_visible_active(robot)
    q0 = (start - range_min) / (range_max - range_min) * 200 - 100
    robot._am1_selected_joint_position = {"raw": start, "normalized": q0,
        "read_started_at_monotonic_s": 9.98, "read_completed_at_monotonic_s": 9.99}
    robot._am1_selected_joint_current = {"raw": 12, "ma": 78.0,
        "read_started_at_monotonic_s": 9.98, "read_completed_at_monotonic_s": 9.99}
    summary = {"reference_norm": q0, "corridor_min_norm": q0 - 10, "corridor_max_norm": q0,
               "live_admitted_at_monotonic_s": 10, "live_deadline_monotonic_s": 100}
    evidence.admit_live(robot, summary)
    control = host.AM1LocalControl(**{k: identity[k] for k in ("run_id", "host_incarnation")}, live=True,
                                  selected_evidence=evidence, clock=lambda: now[0])
    control.state = "paused"
    return robot, evidence, control, summary, now


def test_live_admission_retains_literal_origin_and_quantized_corridor(monkeypatch):
    robot, evidence, control, summary, now = live_guard(monkeypatch)
    assert summary["measured_origin_raw"] == 1224
    assert summary["corridor_max_raw"] == 1223
    assert summary["corridor_min_raw"] == 1113
    assert summary["measured_max_raw"] == 1334
    evidence.guard_live(robot, control)
    robot._am1_selected_joint_current["ma"] = 79
    with pytest.raises(RuntimeError, match="current"):
        evidence.guard_live(robot, control)


def test_live_native_current_fault_remains_original_fault_through_release(monkeypatch):
    robot, evidence, control, summary, now = live_guard(monkeypatch)
    robot._am1_selected_joint_current.update(raw=24, ma=156)
    with pytest.raises(RuntimeError, match="effort"):
        evidence.guard_live(robot, control)
    original = copy.deepcopy(robot._am1_selected_joint_fault)
    evidence.deactivate_live(robot)
    assert robot._am1_selected_joint_fault == original


def test_live_response_window_retains_moving45_and_does_not_require_finite_cycle(monkeypatch):
    robot, evidence, control, summary, now = live_guard(monkeypatch, start=2000, range_min=1000, range_max=3000)
    control.state = "active"
    control.live_sample = sample(expiry=12, target=-10)
    robot._am1_selected_joint_action = {"expected_goal_raw": 1940}
    evidence.guard_live(robot, control)
    now[0] = 11.21
    for cache in (robot._am1_selected_joint_position, robot._am1_selected_joint_current):
        cache.update(read_started_at_monotonic_s=11.19, read_completed_at_monotonic_s=11.2)
    with pytest.raises(RuntimeError, match="response"):
        evidence.guard_live(robot, control)


def test_live_fixed_goal_growing_instability_remains_a_native_fault(monkeypatch):
    robot, evidence, control, summary, now = live_guard(monkeypatch, start=2000, range_min=1000, range_max=3000)
    control.state = "active"
    control.live_sample = sample(expiry=12, target=0)
    robot._am1_selected_joint_action = {"expected_goal_raw": 2000}
    with pytest.raises(RuntimeError, match="instability"):
        for raw in (2001, 1998, 2003, 1996):
            now[0] += .1
            robot._am1_selected_joint_position.update(raw=raw,
                read_started_at_monotonic_s=now[0]-.02, read_completed_at_monotonic_s=now[0]-.01)
            robot._am1_selected_joint_current.update(read_started_at_monotonic_s=now[0]-.02,
                                                    read_completed_at_monotonic_s=now[0]-.01)
            evidence.guard_live(robot, control)


def test_live_static_command_adverse_current_rise_remains_a_native_fault(monkeypatch):
    robot, evidence, control, summary, now = live_guard(monkeypatch, start=2000, range_min=1000, range_max=3000)
    control.state = "active"
    control.live_sample = sample(expiry=12, target=0)
    robot._am1_selected_joint_action = {"expected_goal_raw": 2000}
    with pytest.raises(RuntimeError, match="adverse"):
        for current in (0, 1, 2, 3):
            now[0] += .1
            robot._am1_selected_joint_position.update(read_started_at_monotonic_s=now[0]-.02,
                                                     read_completed_at_monotonic_s=now[0]-.01)
            robot._am1_selected_joint_current.update(raw=current, ma=current*6.5,
                read_started_at_monotonic_s=now[0]-.02, read_completed_at_monotonic_s=now[0]-.01)
            evidence.guard_live(robot, control)


def test_nonselected_out_of_command_range_pose_keeps_its_existing_raw_hold(monkeypatch):
    from lerobot.motors import Motor as Servo, MotorCalibration, MotorNormMode
    from tests.robots.test_am1_selected_joint_evidence import robot as selected_robot

    now, identity, owner, control, _ = lane()
    robot, reads, writes = selected_robot(monkeypatch)
    other = "arm_left_shoulder_lift"
    robot.left_bus.motors[other] = Servo(2, "sts3215", MotorNormMode.RANGE_M100_100)
    robot.left_bus._id_to_model_dict[2] = "sts3215"
    robot.left_bus._id_to_name_dict[2] = other
    robot.left_bus.calibration[other] = MotorCalibration(2, 0, 515, 848, 3223)
    robot.left_arm_motors.append(other)
    robot.left_bus.write = lambda *args, **kwargs: None
    robot.lift.stop = lambda: None
    robot.hold_follower_arms = lambda: None
    robot.get_observation = lambda: {**dict.fromkeys(JOINT_KEYS, 0.0), other + ".pos": 100.08421052631579}
    robot.am1_live_arm_commands = lambda binding: {**dict.fromkeys(JOINT_KEYS, 0.0), other + ".pos": 100.0}
    control.apply(robot, frame(identity, owner, sequence=1))
    now[0] = 10.1
    control.apply(robot, frame(identity, owner, sequence=2, mode="active", epoch=2, sample=sample()))
    assert not [row for row in writes if row[0] == 42 and 2 in row[2]]
    assert control.accepted_action[other + ".pos"] == 100.0
    assert robot.get_observation()[other + ".pos"] == 100.08421052631579


def test_live_goal_readback_reports_actual_servo_goal_separately_from_requested_write(monkeypatch):
    from tests.robots.test_alohamini_lift_motor_feedback import status
    from tests.robots.test_am1_selected_joint_evidence import visible_register_packet

    robot, evidence, control, summary, now = live_guard(monkeypatch)
    control._live_summary.update(summary)
    robot._am1_selected_joint_action = {"expected_goal_raw": 1210, "write_acknowledged": False}
    robot.left_bus.port_handler.responses.extend([
        status(visible_register_packet(torque=1, range_min=864, range_max=3073, goal=1209, present=1220), motor_id=3),
        status(bytes([91]), motor_id=3)])
    evidence.report_live_goal(robot, control)
    row = next(iter(evidence.goal_readbacks.values()))
    assert row["goal_position_raw"] == 1209 and row["requested_goal_raw"] == 1210
    assert row["goal_matches_expected_raw"] is False
    assert row["present_position_raw"] == 1220


def test_live_optional_goal_timeout_does_not_clear_motor_fault_or_rearm(monkeypatch):
    robot, evidence, control, summary, now = live_guard(monkeypatch)
    control._live_summary.update(summary)
    evidence.report_live_goal(robot, control)
    row = next(iter(evidence.goal_readbacks.values()))
    assert row["diagnostic_available"] is False and control.state == "paused"


# Right elbow coverage uses independent fixture names; left fixtures above stay unchanged.




RIGHT_RECIPE = "physical-live-right-elbow"
RIGHT = "arm_right_elbow_flex"
RIGHT_KEY = RIGHT + ".pos"
LEFT_KEY = "arm_left_elbow_flex.pos"


def right_lane():
    now = [10.0]
    identity = {"run_id": str(uuid4()), "host_incarnation": str(uuid4())}
    owner = dict.fromkeys(("service_incarnation", "controller_generation", "connection_generation"))
    owner.update({key: str(uuid4()) for key in owner})
    control = host.AM1LocalControl(**identity, live=True, live_recipe=RIGHT_RECIPE, clock=lambda: now[0])
    obj = MeasuredMotor()
    obj.configure_workspace()
    return now, identity, owner, control, obj


def right_sample(*, seq=1, expiry=10.25, target=-8.9, key=RIGHT_KEY):
    return {"target": {key: target}, "grant_id": str(uuid4()), "seq": seq,
            "valid_until_monotonic_s": expiry}


def test_right_native_freezes_negative_corridor_and_moves_only_right():
    now, identity, owner, control, obj = right_lane()
    assert control.apply(obj, frame(identity, owner, sequence=1))
    assert control.live_snapshot()["joint"] == RIGHT_KEY
    assert control.live_snapshot()["corridor_min_norm"] == pytest.approx((1834-827)/2212*200-100)
    assert control.live_snapshot()["corridor_max_norm"] == pytest.approx((1867-827)/2212*200-100)
    now[0] = 10.1
    assert control.apply(obj, frame(identity, owner, sequence=2, mode="active", epoch=2, sample=right_sample()))
    action = obj.events[-1][1]
    assert action[RIGHT_KEY] == pytest.approx((1845-827)/2212*200-100-.1)
    assert LEFT_KEY not in action
    assert control.accepted_action[LEFT_KEY] == pytest.approx((2124-864)/2209*200-100)
    assert all(action[k] == 0 for k in ("x.vel", "y.vel", "theta.vel", "lift_axis.vel"))


@pytest.mark.parametrize("key,target", [(LEFT_KEY, 18.0), (RIGHT_KEY, -13.01), (RIGHT_KEY, -9.99), (RIGHT_KEY, -5.9)])
def test_right_rejects_wrong_joint_or_outside_original_corridor(key, target):
    now, identity, owner, control, obj = right_lane()
    control.apply(obj, frame(identity, owner, sequence=1))
    now[0] = 10.1
    before = control.live_snapshot()
    events = len(obj.events)
    assert not control.apply(obj, frame(identity, owner, sequence=2, mode="active", epoch=2,
                                        sample=right_sample(key=key, target=target)))
    assert control.live_snapshot() == before
    assert len(obj.events) == events


def test_right_expiry_rejoin_preserves_reference_deadline_and_other_hold():
    now, identity, owner, control, obj = right_lane()
    assert control.apply(obj, frame(identity, owner, sequence=1))
    deadline = control.live_snapshot()["live_deadline_monotonic_s"]
    now[0] = 10.1
    assert control.apply(obj, frame(identity, owner, sequence=2, mode="active", epoch=2, sample=right_sample()))
    now[0] = 10.26
    assert control.check_live(obj)
    assert obj.events[-2:] == [("zero",), ("hold",)]
    obj.positions[RIGHT_KEY], obj.positions[LEFT_KEY] = (1845-827)/2212*200-100-.05, 18.5
    assert control.apply(obj, frame(identity, owner, sequence=3, epoch=3, revision=1))
    summary = control.live_snapshot()
    assert summary["cursor_norm"] == pytest.approx((1845-827)/2212*200-100-.05)
    assert summary["reference_norm"] == pytest.approx((1845-827)/2212*200-100)
    assert summary["live_deadline_monotonic_s"] == deadline
    assert control.live_held[LEFT_KEY] == pytest.approx((2124-864)/2209*200-100)
    now[0] = 10.3
    count = len(obj.events)
    assert not control.apply(obj, frame(identity, owner, sequence=4, mode="active", epoch=4, revision=1,
                                        sample=right_sample(expiry=10.25)))
    assert len(obj.events) == count
    assert control.apply(obj, frame(identity, owner, sequence=4, mode="active", epoch=4, revision=1,
                                    sample=right_sample(seq=2, expiry=10.5, target=(1845-827)/2212*200-100)))
    assert obj.events[-1][1][RIGHT_KEY] > (1845-827)/2212*200-100-.05


def right_packet(*, torque=0, goal=1845, present=1845, phase=12):
    packet = bytearray(visible_register_packet(torque=torque, range_min=827, range_max=3039,
                                               goal=goal, present=present))
    packet[18] = phase
    packet[31:33] = (2048 + 658).to_bytes(2, "little")
    return bytes(packet)


def right_robot(monkeypatch, packets=(), *, position=1845):
    obj, evidence, rows, identity, port, reads, writes = visible_robot(monkeypatch, packets)
    original_bus = obj.left_bus
    motor = original_bus.motors["arm_left_elbow_flex"]
    right_bus = FeetechMotorsBus("right-selected-test", {RIGHT: motor},
                                {RIGHT: MotorCalibration(3, 0, -658, 827, 3039)})
    right_bus.port_handler, right_bus.packet_handler = original_bus.port_handler, original_bus.packet_handler
    monkeypatch.setattr(right_bus, "_sync_write",
                        lambda address, length, values, **kwargs: writes.append((address, length, dict(values))) or 0)
    obj.right_bus, obj.right_arm_motors = right_bus, [RIGHT]
    left, _, left_writes = selected_robot(monkeypatch, position=2200, current=7)
    obj.left_bus, obj.left_arm_motors, obj.base_motors = left.left_bus, left.left_arm_motors, left.base_motors

    def right_read(address, length, ids, **kwargs):
        reads.append((address, length, tuple(ids)))
        return dict.fromkeys(ids, position if address == 56 else 2 if address == 69 else 0), 0

    monkeypatch.setattr(right_bus, "_sync_read", right_read)
    from lerobot.motors import Motor, MotorNormMode
    from lerobot.robots.alohamini.alohamini import AlohaMini
    for side in ("left", "right"):
        bus = getattr(obj, side+"_bus")
        names = [k for k in WORKSPACE_CALIBRATION if k.startswith("arm_"+side+"_")]
        for name in names:
            calibration = WORKSPACE_CALIBRATION[name]
            bus.motors[name] = Motor(calibration[0],"sts3215",MotorNormMode.RANGE_0_100 if name.endswith("_gripper") else MotorNormMode.RANGE_M100_100)
            bus.calibration[name] = MotorCalibration(*calibration)
        setattr(obj,side+"_arm_motors",names)
        bus._id_to_name_dict = {motor.id:name for name,motor in bus.motors.items()}
        bus._id_to_model_dict = {motor.id:motor.model for motor in bus.motors.values()}
        def read_all(address,length,ids,source=bus,**kwargs):
            values = {}
            for ident in ids:
                name = source._id_to_name(ident)
                values[ident] = (position if name==RIGHT else WORKSPACE_GOALS.get(name,0)) if address in (42,56) else 2 if address==69 else 0
            reads.append((address,length,tuple(ids)))
            return values,0
        monkeypatch.setattr(bus,"_sync_read",read_all)
    obj._am1_live_frozen_holds = tuple((getattr(obj,side+"_bus"),name,tuple(WORKSPACE_CALIBRATION[name]),position if name==RIGHT else WORKSPACE_GOALS[name])
        for side in ("left","right") for name in getattr(obj,side+"_arm_motors"))
    obj._am1_live_frozen_binding = {**identity,"recipe":RIGHT_RECIPE}
    del obj.am1_live_arm_commands
    identity["recipe"] = RIGHT_RECIPE
    obj._am1_visible_control_binding = identity
    return obj, evidence, rows, identity, port, reads, writes, left_writes


def right_guard(monkeypatch, *, start=1845, before_admit=None):
    obj, evidence, rows, identity, port, reads, writes, left_writes = right_robot(monkeypatch, [
        status(right_packet(present=start, goal=start), motor_id=3), status(bytes([91]), motor_id=3),
        status(right_packet(torque=1, present=start, goal=start), motor_id=3),
        status(bytes([91]), motor_id=3)], position=start)
    now = [10.0]
    evidence.clock = lambda: now[0]
    evidence.prepare_visible(obj, identity)
    evidence.verify_visible_active(obj)
    obj.get_observation()
    obj.read_and_check_currents(1800, False)
    q0 = (start - 827) / 2212 * 200 - 100
    summary = {"reference_norm": q0, "corridor_min_norm": q0 - 3,
               "corridor_max_norm": q0,
               "live_admitted_at_monotonic_s": 10, "live_deadline_monotonic_s": 100}
    if before_admit is not None:
        before_admit(obj, summary)
    evidence.admit_live(obj, summary)
    obj._am1_selected_joint_position.update(read_started_at_monotonic_s=9.98,read_completed_at_monotonic_s=9.99)
    obj._am1_selected_joint_current.update(read_started_at_monotonic_s=9.98,read_completed_at_monotonic_s=9.99)
    control = host.AM1LocalControl(**{k: identity[k] for k in ("run_id", "host_incarnation")},
                                  live=True, live_recipe=RIGHT_RECIPE, selected_evidence=evidence,
                                  clock=lambda: now[0])
    control.state = "paused"
    return obj, evidence, control, summary, now, port, writes, left_writes


def test_right_owned_settings_feedback_and_encoded_envelopes(monkeypatch):
    obj, evidence, control, summary, now, port, writes, left_writes = right_guard(monkeypatch)
    assert evidence.visible_settings["before"]["registers"]["Phase"] == 12
    assert evidence.visible_settings["before"]["registers"]["Homing_Offset"] == -658
    assert obj._am1_selected_joint_position["raw"] == 1845
    assert obj._am1_selected_joint_position["normalized"] == pytest.approx(-7.956600361663661)
    assert obj._am1_selected_joint_current["raw"] == 2
    assert summary["measured_origin_raw"] == 1845
    assert summary["corridor_min_raw"] == 1834
    assert summary["corridor_max_raw"] == 1867
    assert summary["measured_min_raw"] == 1811
    assert summary["measured_max_raw"] == 1878
    evidence.guard_live(obj, control)
    obj._am1_selected_joint_position["raw"] = 1810
    with pytest.raises(RuntimeError, match="corridor"):
        evidence.guard_live(obj, control)


def test_right_selected_action_uses_right_encoded_write_and_provenance(monkeypatch):
    obj, evidence, control, summary, now, port, writes, left_writes = right_guard(monkeypatch)
    obj.send_action({RIGHT_KEY: -8.951175406871613, "x.vel": 0, "y.vel": 0, "theta.vel": 0, "lift_axis.vel": 0})
    action = obj._am1_selected_joint_action
    assert action["expected_goal_raw"] == 1834
    assert action["sync_write_returned"] is True
    assert action["encoded_goal_word"] == 1834
    assert [row for row in writes if row[0] == 42][-1][2] == {3: 1834}
    assert not [row for row in left_writes if row[0] == 42]
    assert evidence._base(obj, control, {})["motor"]["bus"] == "right"
    assert evidence._base(obj, control, {})["motor"]["name"] == RIGHT


def test_right_configuration_preserves_selected_baseline_settings(monkeypatch):
    obj, evidence, rows, identity, port, reads, writes, left_writes = right_robot(monkeypatch, [
        status(right_packet(), motor_id=3), status(bytes([91]), motor_id=3)])
    events = []
    for side, bus in (("left", obj.left_bus), ("right", obj.right_bus)):
        monkeypatch.setattr(bus, "write",
                            lambda register, motor, value, _side=side, **kwargs:
                            events.append((_side, register, motor, value)))
    obj.lift.configure = lambda **kwargs: None
    obj.configure()
    immutable = set(evidence.VISIBLE_STATIC_REGISTERS) | {"Minimum_Startup_Force"}
    assert not [r for r in events if r[0] == "right" and r[2] == RIGHT and r[1] in immutable]
    assert ("left", "P_Coefficient", "arm_left_elbow_flex", 16) in events
    assert ("right", "Torque_Enable", RIGHT, 0) in events


def test_right_binding_cannot_switch_after_settings_acquisition(monkeypatch):
    obj, evidence, control, summary, now, port, writes, left_writes = right_guard(monkeypatch)
    obj._am1_visible_control_binding["recipe"] = "physical-live-left-elbow"
    with pytest.raises(RuntimeError):
        evidence.guard_live(obj, control)




def native_right_ready(monkeypatch):
    from examples.alohamini.am1_finite_task import JOINT_KEYS

    obj, evidence, control, summary, now, port, writes, left_writes = right_guard(monkeypatch)
    control.state, control.epoch = "ready", -1
    control._live_summary.update(summary)
    measured = dict.fromkeys(JOINT_KEYS, 0.0)
    for name, raw in WORKSPACE_GOALS.items():
        calibration = WORKSPACE_CALIBRATION[name]
        fraction=(raw-calibration[3])/(calibration[4]-calibration[3])
        measured[name+".pos"] = fraction*100 if name.endswith("_gripper") else fraction*200-100
    obj.get_observation = lambda: dict(measured)
    obj.stop_motion = lambda: None
    obj.hold_follower_arms = lambda: None
    identity = dict(control.binding)
    owner = {k: str(uuid4()) for k in ("service_incarnation", "controller_generation", "connection_generation")}
    assert control.apply(obj, frame(identity, owner, sequence=1))
    return obj, control, now, identity, owner, writes


def test_right_live_summary_exports_only_actual_current_selected_write(monkeypatch):
    obj, control, now, identity, owner, writes = native_right_ready(monkeypatch)
    now[0] = 10.1
    assert control.apply(obj, frame(identity, owner, sequence=2, mode="active", epoch=2,
                                    sample=right_sample(target=-8.951175406871613)))
    summary, actual = control.live_snapshot(), obj._am1_selected_joint_action
    assert summary["encoded_goal_raw"] == 1843
    assert summary["goal_write_returned"] is True
    assert summary["goal_write_started_at_monotonic_s"] == actual["write_started_at_monotonic_s"]
    assert summary["goal_write_completed_at_monotonic_s"] == actual["write_completed_at_monotonic_s"]
    assert summary["goal_action_sequence"] == 2
    assert actual["joint"] == RIGHT_KEY
    assert actual["action_sequence"] == 2


@pytest.mark.parametrize("bad", ["stale", "wrong_joint", "failed_write", "wrong_sequence"])
def test_right_live_summary_never_relabels_unmatched_cache_as_current_write(monkeypatch, bad):
    obj, control, now, identity, owner, writes = native_right_ready(monkeypatch)
    now[0] = 10.1
    assert control.apply(obj, frame(identity, owner, sequence=2, mode="active", epoch=2,
                                    sample=right_sample(target=-8.951175406871613)))
    assert control.live_snapshot()["encoded_goal_raw"] == 1843
    original_send = obj.send_action

    def changed(action):
        if bad == "stale":
            return dict(action)
        result = original_send(action)
        if bad == "wrong_joint":
            obj._am1_selected_joint_action["joint"] = LEFT_KEY
        elif bad == "failed_write":
            obj._am1_selected_joint_action["sync_write_returned"] = False
        else:
            obj._am1_selected_joint_action["action_sequence"] = 2
        return result

    monkeypatch.setattr(obj, "send_action", changed)
    now[0] = 10.15
    assert control.apply(obj, frame(identity, owner, sequence=3, mode="active", epoch=2,
                                    sample=right_sample(seq=2, expiry=10.4, target=-8.951175406871613)))
    assert "encoded_goal_raw" not in control.live_snapshot()
    assert "goal_action_sequence" not in control.live_snapshot()


def test_right_expiry_before_actual_write_never_exports_write_proof(monkeypatch):
    obj, control, now, identity, owner, writes = native_right_ready(monkeypatch)
    original_read = obj._read_arm_positions

    def delayed(*args, **kwargs):
        result = original_read(*args, **kwargs)
        now[0] = 10.26
        return result

    monkeypatch.setattr(obj, "_read_arm_positions", delayed)
    now[0] = 10.1
    assert not control.apply(obj, frame(identity, owner, sequence=2, mode="active", epoch=2,
                                        sample=right_sample(target=-8.951175406871613)))
    assert not [r for r in writes if r[0] == 42]
    assert "encoded_goal_raw" not in control.live_snapshot()
    assert control.live_snapshot()["state"] == "expired"



def test_right_negative_command_corridor_cannot_cross_actual_calibrated_lower_limit(monkeypatch):
    with pytest.raises(RuntimeError, match="workspace|admission/encoded corridor"):
        right_guard(monkeypatch, start=838)


@pytest.mark.parametrize("left_raw,expected_type,expected_events", [
    (25, RuntimeError, ["left-read", "left-check"]),
    (2, OSError, ["left-read", "left-check", "right-read"]),
])
def test_selected_left_current_is_checked_before_right_transport_failure(
    monkeypatch, left_raw, expected_type, expected_events
):
    from types import SimpleNamespace

    obj, evidence, control, summary, now = live_guard(monkeypatch)
    events = []
    original_left_read = obj.left_bus.sync_read
    original_check = evidence.check_visible_current

    def left_read(register, motors, **kwargs):
        events.append("left-read")
        assert register == "Present_Current"
        result = original_left_read(register, motors, **kwargs)
        result["arm_left_elbow_flex"] = left_raw
        return result

    def check(*args, **kwargs):
        events.append("left-check")
        assert kwargs["source"] == "ordinary_current"
        assert kwargs["sample"]["raw"] == left_raw
        return original_check(*args, **kwargs)

    def right_read(*args, **kwargs):
        events.append("right-read")
        raise OSError("right transport sentinel")

    monkeypatch.setattr(obj.left_bus, "sync_read", left_read)
    monkeypatch.setattr(evidence, "check_visible_current", check)
    obj.right_bus = SimpleNamespace(motors={"arm_right_elbow_flex": object()}, sync_read=right_read)
    with pytest.raises(expected_type):
        obj.read_and_check_currents(1800, False)
    assert events == expected_events
    sample = obj._am1_selected_joint_current
    assert sample["raw"] == left_raw
    assert sample["ma"] == left_raw * 6.5
    assert sample["read_started_at_monotonic_s"] <= sample["read_completed_at_monotonic_s"]
    if left_raw == 25:
        fault = obj._am1_selected_joint_fault
        assert fault["motor"]["bus"] == "left"
        assert fault["current"]["raw"] == 25
        assert fault["source"] == "ordinary_current"


def test_selected_right_current_is_checked_immediately_after_its_own_read(monkeypatch):
    obj, evidence, control, summary, now, port, writes, left_writes = right_guard(monkeypatch)
    events = []
    original_check = evidence.check_visible_current

    def left_read(register, motors, **kwargs):
        events.append("left-read")
        return dict.fromkeys(motors, 2)

    def right_read(register, motors, **kwargs):
        events.append("right-read")
        return dict.fromkeys(motors, 25)

    def check(*args, **kwargs):
        events.append("right-check")
        assert kwargs["source"] == "ordinary_current"
        assert kwargs["sample"]["raw"] == 25
        return original_check(*args, **kwargs)

    monkeypatch.setattr(obj.left_bus, "sync_read", left_read)
    monkeypatch.setattr(obj.right_bus, "sync_read", right_read)
    monkeypatch.setattr(evidence, "check_visible_current", check)
    with pytest.raises(RuntimeError, match="effort ceiling"):
        obj.read_and_check_currents(1800, False)
    assert events == ["left-read", "right-read", "right-check"]
    assert obj._am1_selected_joint_fault["motor"]["bus"] == "right"
    assert obj._am1_selected_joint_fault["current"]["raw"] == 25


def test_default_native_imports_and_disabled_evidence_do_not_require_examples(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    script = """
import importlib.abc
import sys
from types import SimpleNamespace

class NoExamples(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "examples" or fullname.startswith("examples."):
            raise ModuleNotFoundError("default package attempted checkout-only examples import")
        return None

sys.meta_path.insert(0, NoExamples())
from lerobot.robots.alohamini.alohamini import AlohaMini
from lerobot.robots.alohamini.selected_joint_evidence import MOTOR, POSITION, AM1SelectedJointEvidence
from lerobot.robots.alohamini.alohamini_host import AM1LocalControl
assert MOTOR == "arm_left_elbow_flex"
assert POSITION == "arm_left_elbow_flex.pos"
assert AM1LocalControl(live=False).live is False
evidence = AM1SelectedJointEvidence(enabled=False)
for robot in (SimpleNamespace(), SimpleNamespace(_am1_visible_control_binding=None)):
    evidence.deactivate(robot)
    assert evidence.prepare(robot, {"x.vel": 0}, {}) == {"x.vel": 0}
    try:
        evidence.guard_visible(robot, None)
    except RuntimeError as error:
        assert "settings/owner binding" in str(error)
    else:
        raise AssertionError("unbound visible guard unexpectedly admitted operation")
print("default native imports and disabled paths passed")
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=environment,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "default native imports and disabled paths passed" in result.stdout


from tests.robots.test_am1_pre_activation import activation as endpoint_activation, protected_endpoint_case


@pytest.mark.parametrize("recipe,direction", [("physical-live-left-elbow", -1), ("physical-live-right-elbow", -1)])
def test_real_frozen_eleven_goals_across_live_owned_hold_boundaries(endpoint_activation, monkeypatch, recipe, direction):
    from lerobot.robots.alohamini import alohamini as owner_module
    case = endpoint_activation
    if recipe==RIGHT_RECIPE:
        robot,_,_,_=workspace_owner(case,monkeypatch)
    else:
        robot=protected_endpoint_case(case,monkeypatch,recipe)
        robot.activate_motors(home_lift=False)
    spec = host.live_elbow_spec(recipe)
    key, motor = spec.joint, spec.joint.removesuffix(".pos")
    selected_bus = getattr(robot, spec.side + "_bus")
    original_goals = {(side, name): bus.registers["Goal_Position", name]
                      for side, bus in (("left", robot.left_bus), ("right", robot.right_bus))
                      for name in bus.calibration if name != motor}
    def observation():
        values = {name + ".pos": value for bus in (robot.left_bus, robot.right_bus)
                for name, value in robot._normalize_arm_feedback(bus, {
                    name: bus.registers["Present_Position", name] for name in bus.calibration}).items()}
        if recipe==RIGHT_RECIPE:
            robot._am1_selected_joint_position=dict(raw=selected_bus.registers["Present_Position",motor],normalized=values[key],
                read_started_at_monotonic_s=case.clock.now,read_completed_at_monotonic_s=case.clock.now)
        return values
    robot.get_observation = observation
    robot.stop_motion = lambda: case.events.append(("owned_body_zero",))
    # Active device transport is injected; all hold/admission behavior is the real owner.
    def send(action):
        target = action.get(key)
        if target is not None:
            raw = owner_module.FeetechMotorsBus._unnormalize(selected_bus, {3: target})[3]
            selected_bus.write("Goal_Position", motor, raw, normalize=False)
        assert all(action[name] == 0 for name in ("x.vel", "y.vel", "theta.vel", "lift_axis.vel"))
        return dict(action)
    robot.send_action = send
    identity = {name: robot._am1_visible_control_binding[name] for name in ("run_id", "host_incarnation")}
    control = host.AM1LocalControl(**identity, live=True, live_recipe=recipe, clock=lambda: case.clock.now)
    client = {name: str(uuid4()) for name in ("service_incarnation", "controller_generation", "connection_generation")}
    boundary = len(case.events)
    assert control.apply(robot, frame(identity, client, sequence=1))
    initial = control.live_snapshot()
    assert control.live_held["arm_left_shoulder_lift.pos"] == 100
    assert observation()["arm_left_shoulder_lift.pos"] == 100.08421052631579
    def active(sequence, epoch, revision, seq):
        case.clock.now += .05
        intent = {"target": {key: -8.9 if recipe==RIGHT_RECIPE else direction}, "grant_id": str(uuid4()), "seq": seq,
                  "valid_until_monotonic_s": case.clock.now + .25}
        assert control.apply(robot, frame(identity, client, sequence=sequence, mode="active", epoch=epoch,
                                          revision=revision, sample=intent))
        return intent
    active(2, 2, 0, 1)
    assert control.apply(robot, frame(identity, client, sequence=3, epoch=3, revision=1))  # Release.
    last = active(4, 4, 1, 2)  # Resume.
    case.clock.now = last["valid_until_monotonic_s"] + .001
    assert control.check_live(robot) and control.live_snapshot()["state"] == "expired"
    assert control.apply(robot, frame(identity, client, sequence=5, epoch=5, revision=2))
    active(6, 6, 2, 3)
    control.watchdog_stop(robot)
    assert control.apply(robot, frame(identity, client, sequence=7, epoch=7, revision=3))
    active(8, 8, 3, 4)
    # This is the existing outer sender's exact body-zero cleanup shape.
    assert control.apply(robot, {name: 0 for name in ("x.vel", "y.vel", "theta.vel", "lift_axis.vel")})
    assert control.state == "stopped"
    stopped_boundary = len(case.events)
    case.clock.now = initial["live_deadline_monotonic_s"]
    assert not control.check_live(robot)
    assert control.state == "stopped"
    assert not control.apply(robot, frame(identity, client, sequence=9, epoch=9, revision=4))
    assert len(case.events) == stopped_boundary
    assert control.live_snapshot()["reference_norm"] == initial["reference_norm"]
    assert control.live_snapshot()["live_deadline_monotonic_s"] == initial["live_deadline_monotonic_s"]
    assert all(bus.registers["Goal_Position", name] == original_goals[side, name]
               for side, bus in (("left", robot.left_bus), ("right", robot.right_bus))
               for name in bus.calibration if name != motor)
    writes = [row for row in case.events[boundary:] if row[0] == "write" and row[2] == "Goal_Position"]
    assert writes and all(row[1] == spec.side and row[3] == motor for row in writes)
    traces = [row[1] for row in case.events if row[0] == "live_evidence"]
    startup = next(row for row in traces if row["kind"] == "protected_live_startup_raw_goals")
    assert len(startup["acknowledged_goals"]) == 12
    assert all(row["raw_goal"] == row["readback"]["raw"] for row in startup["acknowledged_goals"])
    holds = [row for row in traces if row["kind"] == "protected_live_hold_raw_goals"]
    assert len(holds) >= 6 and all(sum(len(group["raw_goals"]) for group in row["nonselected_readbacks"]) == 11 for row in holds)


@pytest.mark.parametrize("mutation", ["not_admitted", "bool", "nan", "nonzero", "missing", "extra", "foreign_binding"])
def test_owned_body_zero_cleanup_is_closed_and_never_admits_unqualified_or_foreign_live(mutation):
    now, identity, client, control, robot = lane()
    command = {name: 0 for name in ("x.vel", "y.vel", "theta.vel", "lift_axis.vel")}
    control.apply(robot, frame(identity, client, sequence=1, admitted=mutation != "not_admitted"))
    if mutation == "bool":command["x.vel"] = False
    elif mutation == "nan":command["x.vel"] = float("nan")
    elif mutation == "nonzero":command["x.vel"] = 1
    elif mutation == "missing":command.pop("lift_axis.vel")
    elif mutation == "extra":command[KEY] = 0
    elif mutation == "foreign_binding":
        robot.am1_live_arm_commands = lambda binding: (_ for _ in ()).throw(RuntimeError("foreign frozen binding"))
    before = len(robot.events)
    if mutation == "foreign_binding":
        with pytest.raises(RuntimeError):control.apply(robot, command)
    else:assert not control.apply(robot, command)
    assert len(robot.events) == before


# Historical returned-write evidence is never current active qualification.
HISTORICAL_WRITE_KEYS = {
    "version", "provenance", "run_id", "host_incarnation", "service_incarnation",
    "controller_generation", "connection_generation", "joint", "side", "epoch",
    "intent_revision", "grant_id", "last_input_seq", "valid_until_monotonic_s",
    "accepted_target_norm", *host.AM1_LIVE_WRITE_PROOF_FIELDS,
}


def expected_historical_write(control, owner, original, actual):
    return {
        "version": 1, "provenance": "native-encoded-goal-helper-return/historical",
        **control.binding, **owner, "joint": control.live_spec.joint,
        "side": control.live_spec.side, "epoch": actual["epoch"],
        "intent_revision": actual["intent_revision"], "grant_id": original["grant_id"],
        "last_input_seq": original["seq"], "valid_until_monotonic_s": original["valid_until_monotonic_s"],
        "accepted_target_norm": control.accepted_action[control.live_spec.joint],
        "encoded_goal_raw": actual["expected_goal_raw"], "goal_write_returned": True,
        "goal_write_started_at_monotonic_s": actual["write_started_at_monotonic_s"],
        "goal_write_completed_at_monotonic_s": actual["write_completed_at_monotonic_s"],
        "goal_action_sequence": actual["action_sequence"],
    }


def test_historical_write_real_right_expiry_retains_bound_success_without_current_proof(monkeypatch):
    obj, control, now, identity, owner, writes = native_right_ready(monkeypatch)
    original = right_sample(target=-8.951175406871613)
    now[0] = 10.1
    assert control.apply(obj, frame(identity, owner, sequence=2, mode="active", epoch=2, sample=original))
    actual = copy.deepcopy(obj._am1_selected_joint_action)
    expected = expected_historical_write(control, owner, original, actual)
    assert actual["sync_write_returned"] is True and expected["encoded_goal_raw"] == 1843
    now[0] = 10.26
    assert control.check_live(obj)
    expired = control.live_snapshot()
    assert expired["state"] == "expired" and control.state == "paused"
    assert not (set(host.AM1_LIVE_WRITE_PROOF_FIELDS) & set(expired))
    assert expired["last_successful_write"] == expected
    assert set(expired["last_successful_write"]) == HISTORICAL_WRITE_KEYS
    assert expired["hold_completed_at_monotonic_s"] >= original["valid_until_monotonic_s"]
    # Consumer mutation cannot alter the host's retained historical receipt.
    expired["last_successful_write"]["encoded_goal_raw"] = -1
    assert control.live_snapshot()["last_successful_write"] == expected


@pytest.mark.parametrize("recipe,key,side", [
    ("physical-live-left-elbow", KEY, "left"), (RIGHT_RECIPE, RIGHT_KEY, "right"),
])
def test_historical_write_both_recipes_use_verified_selected_return(monkeypatch, recipe, key, side):
    now, identity, owner, control, obj = lane() if side == "left" else right_lane()
    assert control.live_spec.recipe == recipe
    original_send = obj.send_action

    def returned(action):
        result = original_send(action)
        obj._am1_selected_joint_action = {
            **obj._am1_live_action_binding, "side": side, "motor": key.removesuffix(".pos"),
            "expected_goal_raw": 1900, "sync_write_returned": True,
            "write_started_at_monotonic_s": now[0], "write_completed_at_monotonic_s": now[0],
        }
        return result

    monkeypatch.setattr(obj, "send_action", returned)
    assert control.apply(obj, frame(identity, owner, sequence=1))
    original = sample() if side == "left" else right_sample()
    now[0] = 10.1
    assert control.apply(obj, frame(identity, owner, sequence=2, mode="active", epoch=2, sample=original))
    expected = expected_historical_write(control, owner, original, obj._am1_selected_joint_action)
    now[0] = 10.26
    assert control.check_live(obj)
    summary = control.live_snapshot()
    assert summary["last_successful_write"] == expected
    assert summary["last_successful_write"]["joint"] == key
    assert summary["state"] == "expired"
    assert not (set(host.AM1_LIVE_WRITE_PROOF_FIELDS) & set(summary))


@pytest.mark.parametrize("bad", ["stale", "wrong_joint", "wrong_sequence", "failed_write", "foreign_host"])
def test_historical_write_unmatched_new_attempt_cannot_relabel_previous_success(monkeypatch, bad):
    obj, control, now, identity, owner, writes = native_right_ready(monkeypatch)
    original = right_sample(target=-8.951175406871613)
    now[0] = 10.1
    assert control.apply(obj, frame(identity, owner, sequence=2, mode="active", epoch=2, sample=original))
    expected = expected_historical_write(control, owner, original, copy.deepcopy(obj._am1_selected_joint_action))
    original_send = obj.send_action

    def unmatched(action):
        if bad == "stale":
            return dict(action)
        result = original_send(action)
        field, value = {
            "wrong_joint": ("joint", LEFT_KEY), "wrong_sequence": ("action_sequence", 2),
            "failed_write": ("sync_write_returned", False), "foreign_host": ("host_incarnation", str(uuid4())),
        }[bad]
        obj._am1_selected_joint_action[field] = value
        return result

    monkeypatch.setattr(obj, "send_action", unmatched)
    now[0] = 10.15
    newer = right_sample(seq=2, expiry=10.4, target=-8.951175406871613)
    assert control.apply(obj, frame(identity, owner, sequence=3, mode="active", epoch=2, sample=newer))
    current = control.live_snapshot()
    assert current["grant_id"] == newer["grant_id"]
    assert not (set(host.AM1_LIVE_WRITE_PROOF_FIELDS) & set(current))
    assert current["last_successful_write"] == expected
    now[0] = 10.41
    assert control.check_live(obj)
    assert control.live_snapshot()["last_successful_write"] == expected


def test_historical_write_prewrite_expiry_never_creates_success(monkeypatch):
    obj, control, now, identity, owner, writes = native_right_ready(monkeypatch)
    original_read = obj._read_arm_positions

    def delayed(*args, **kwargs):
        result = original_read(*args, **kwargs)
        now[0] = 10.26
        return result

    monkeypatch.setattr(obj, "_read_arm_positions", delayed)
    now[0] = 10.1
    assert not control.apply(obj, frame(identity, owner, sequence=2, mode="active", epoch=2,
                                      sample=right_sample(target=-8.951175406871613)))
    summary = control.live_snapshot()
    assert summary["state"] == "expired"
    assert not [row for row in writes if row[0] == 42]
    assert "last_successful_write" not in summary
    assert not (set(host.AM1_LIVE_WRITE_PROOF_FIELDS) & set(summary))


def test_historical_write_pause_readiness_preserve_history_until_fresh_success(monkeypatch):
    obj, control, now, identity, owner, writes = native_right_ready(monkeypatch)
    now[0] = 10.1
    original = right_sample(target=-8.951175406871613)
    assert control.apply(obj, frame(identity, owner, sequence=2, mode="active", epoch=2, sample=original))
    expected = expected_historical_write(control, owner, original, copy.deepcopy(obj._am1_selected_joint_action))
    admission = {k: control.live_snapshot()[k] for k in
                 ("reference_norm", "corridor_min_norm", "corridor_max_norm", "live_deadline_monotonic_s")}
    now[0] = 10.26
    assert control.check_live(obj)
    assert control.apply(obj, frame(identity, owner, sequence=3, epoch=3, revision=1))
    assert control.live_snapshot()["last_successful_write"] == expected
    now[0] = 10.3
    assert control.apply(obj, frame(identity, owner, sequence=4, mode="active", epoch=4, revision=1))
    ready = control.live_snapshot()
    assert ready["state"] == "awaiting_input"
    assert ready["last_successful_write"] == expected
    # Readiness may perform a real measured-hold write, but is not a new input receipt.
    assert {k: ready[k] for k in admission} == admission
    now[0] = 10.31
    fresh = right_sample(seq=2, expiry=10.55, target=-8.9)
    assert control.apply(obj, frame(identity, owner, sequence=5, mode="active", epoch=4, revision=1, sample=fresh))
    changed = control.live_snapshot()
    assert changed["last_successful_write"] == expected_historical_write(
        control, owner, fresh, obj._am1_selected_joint_action)
    assert changed["last_successful_write"]["goal_action_sequence"] == 5
    assert {k: changed[k] for k in admission} == admission


def test_historical_write_executor_expired_evidence_carries_receipt_after_hold(monkeypatch):
    import time

    from examples.alohamini.am1_session_contract import RECIPES
    from tests.robots.test_am1_physical_executor import executor, until

    io = ExecutorNativeHost()
    original_send = io.robot.send_action

    def returned(action):
        result = original_send(action)
        binding = getattr(io.robot, "_am1_live_action_binding", None)
        if binding:
            io.robot._am1_selected_joint_action = {
                **binding, "side": "left", "motor": "arm_left_elbow_flex", "expected_goal_raw": 1900,
                "sync_write_returned": True, "write_started_at_monotonic_s": time.monotonic(),
                "write_completed_at_monotonic_s": time.monotonic(),
            }
        return result

    monkeypatch.setattr(io.robot, "send_action", returned)
    e = executor(io)
    recipe = RECIPES["physical-live-left-elbow"]
    e.bind_run(str(uuid4()), recipe)
    identity = {"run_id": e.run_id, "host_incarnation": e.host_incarnation,
                "service_incarnation": str(uuid4()), "controller_generation": str(uuid4()),
                "connection_generation": str(uuid4()), "intent_revision": 0}
    e.bind_control(**identity)
    e.begin(recipe)
    try:
        until(lambda: e.evidence()["lifecycle"]["phase"] == "live")
        assert e.dispatch(recipe)
        until(lambda: e.evidence()["active_acknowledged"])
        expiry, grant = time.monotonic() + .25, str(uuid4())
        assert e.apply({KEY: -2}, valid_until_monotonic_s=expiry, grant_id=grant, seq=1, **identity)
        until(lambda: e.evidence()["live_input"]["state"] == "active")
        until(lambda: e.evidence()["live_input"]["state"] == "expired")
        historical = e.evidence()["live_input"]["last_successful_write"]
        assert historical["grant_id"] == grant and historical["valid_until_monotonic_s"] == expiry
        assert historical["run_id"] == e.run_id and historical["host_incarnation"] == e.host_incarnation
        until(lambda: e.sample["ack"]["live_input"]["state"] == "held")
        retained = e.evidence()
        assert retained["active_acknowledged"] is False
        assert retained["live_input"]["state"] == "expired"
        assert retained["live_input"]["last_successful_write"] == historical
        assert not (set(host.AM1_LIVE_WRITE_PROOF_FIELDS) & set(retained["live_input"]))
    finally:
        e.finish("stopped")
        e.worker.join(2)
        assert not e.worker.is_alive()


# Retained AM1 bench calibration/acknowledged Goals from actual08/10.
WORKSPACE_CALIBRATION = {
    "arm_left_shoulder_pan": (1, 0, -1761, 731, 3438),
    "arm_left_shoulder_lift": (2, 0, 515, 848, 3223),
    "arm_left_elbow_flex": (3, 0, 1845, 864, 3073),
    "arm_left_wrist_flex": (4, 0, -1939, 1314, 3208),
    "arm_left_wrist_roll": (5, 0, 879, 0, 4095),
    "arm_left_gripper": (6, 0, 933, 1886, 3401),
    "arm_right_shoulder_pan": (1, 0, -1719, 752, 3456),
    "arm_right_shoulder_lift": (2, 0, -1852, 1331, 3264),
    "arm_right_elbow_flex": (3, 0, -658, 827, 3039),
    "arm_right_wrist_flex": (4, 0, -1609, 1461, 3167),
    "arm_right_wrist_roll": (5, 0, 1869, 0, 4095),
    "arm_right_gripper": (6, 0, -1811, 1657, 3191),
}
WORKSPACE_GOALS = dict(zip(WORKSPACE_CALIBRATION, (2199,3223,2124,1546,2200,3167,2141,3252,1845,2772,2443,1981), strict=True))


def workspace_owner(case, monkeypatch, raw=1845, *, wrist_raw=2443, left_wrist_raw=1546, shoulder_pan_raw=2141, left_elbow_raw=2124, left_gripper_raw=3167):
    """Actual owner/frozen-map methods; only device registers and clock are injected."""
    from lerobot.robots.alohamini import alohamini as module
    robot = protected_endpoint_case(case, monkeypatch)
    from lerobot.motors import MotorNormMode
    for bus in (robot.left_bus, robot.right_bus):
        for name in bus.calibration:
            calibration = WORKSPACE_CALIBRATION[name]
            bus.motors[name].norm_mode=MotorNormMode.RANGE_0_100 if name.endswith("_gripper") else MotorNormMode.RANGE_M100_100
            bus.calibration[name] = MotorCalibration(*calibration)
            value = raw if name == RIGHT else wrist_raw if name == "arm_right_wrist_roll" else left_wrist_raw if name == "arm_left_wrist_flex" else shoulder_pan_raw if name == "arm_right_shoulder_pan" else left_elbow_raw if name == "arm_left_elbow_flex" else left_gripper_raw if name == "arm_left_gripper" else WORKSPACE_GOALS[name]
            bus.registers.update({(key,name):v for key,v in (
                ("Min_Position_Limit",calibration[3]),("Max_Position_Limit",calibration[4]),
                ("Homing_Offset",calibration[2]),("Present_Position",3224 if name=="arm_left_shoulder_lift" else value),
                ("Goal_Position",value),)})
    robot.activate_motors(home_lift=False)
    def observation():
        result = {name+".pos":value for bus in (robot.left_bus,robot.right_bus)
                  for name,value in robot._normalize_arm_feedback(bus,{name:bus.registers["Present_Position",name] for name in bus.calibration}).items()}
        robot._am1_selected_joint_position = {"raw":robot.right_bus.registers["Present_Position",RIGHT],"normalized":result[RIGHT_KEY],
            "read_started_at_monotonic_s":case.clock.now,"read_completed_at_monotonic_s":case.clock.now}
        return result
    robot.get_observation = observation
    robot.stop_motion = lambda: case.events.append(("owned_body_zero",))
    def send(action):
        assert all(action[k]==0 for k in ("x.vel","y.vel","theta.vel","lift_axis.vel"))
        if RIGHT_KEY in action:
            encoded=module.FeetechMotorsBus._unnormalize(robot.right_bus,{3:action[RIGHT_KEY]})[3]
            robot.right_bus.write("Goal_Position",RIGHT,encoded,normalize=False)
        return dict(action)
    robot.send_action=send
    identity={k:robot._am1_visible_control_binding[k] for k in ("run_id","host_incarnation")}
    control=host.AM1LocalControl(**identity,live=True,live_recipe=RIGHT_RECIPE,clock=lambda:case.clock.now)
    owner={k:str(uuid4()) for k in ("service_incarnation","controller_generation","connection_generation")}
    return robot,control,identity,owner


@pytest.mark.parametrize("raw", [1845,1847])
def test_workspace_next_start_keeps_actual_q0_and_reuses_inward_bounds(endpoint_activation,monkeypatch,raw):
    robot,control,identity,owner=workspace_owner(endpoint_activation,monkeypatch,raw)
    before=len(endpoint_activation.events)
    assert control.apply(robot,frame(identity,owner,sequence=1))
    live=control.live_snapshot()
    assert live["reference_norm"]==pytest.approx((raw-827)/2212*200-100)
    assert live["corridor_min_norm"]==pytest.approx((1834-827)/2212*200-100)
    assert live["corridor_max_norm"]==pytest.approx((1867-827)/2212*200-100)
    assert live["corridor_max_norm"]-live["corridor_min_norm"]<=3
    assert live["measured_origin_raw"]==raw
    assert (live["corridor_min_raw"],live["corridor_max_raw"])==(1834,1867)
    assert live["admission_version"]==2
    assert live["workspace"]["id"]=="am1-right-elbow-rest-neighbourhood-v2"
    assert robot.right_bus.registers["Goal_Position",RIGHT]==raw
    assert not any(e[0]=="write" and e[2]=="Goal_Position" and e[4]==1867 for e in endpoint_activation.events[before:])


@pytest.mark.parametrize("mutation", ["goal","calibration","right_pose","outside"])
def test_workspace_native_admission_refuses_incompatible_current_context(endpoint_activation,monkeypatch,mutation):
    robot,control,identity,owner=workspace_owner(endpoint_activation,monkeypatch)
    if mutation=="goal":
        # A stale frozen map must not be reinterpreted as a different permissible pose.
        robot.right_bus.registers["Goal_Position","arm_right_shoulder_pan"]+=1
    elif mutation=="calibration":
        robot.right_bus.calibration[RIGHT].homing_offset=-657
    elif mutation=="right_pose":
        robot.right_bus.registers["Present_Position","arm_right_wrist_flex"]+=50
    else:
        robot.right_bus.registers["Present_Position",RIGHT]=1817
    with pytest.raises(RuntimeError,match="workspace|acknowledged|calibration"):
        control.apply(robot,frame(identity,owner,sequence=1))
    assert "live_admitted_at_monotonic_s" not in control.live_snapshot()


def test_workspace_context_accepts_one_tick_measurement_with_unchanged_actual_goals(endpoint_activation,monkeypatch):
    robot,control,identity,owner=workspace_owner(endpoint_activation,monkeypatch)
    robot.right_bus.registers["Present_Position","arm_right_wrist_flex"]+=1
    assert control.apply(robot,frame(identity,owner,sequence=1))
    assert control.live_snapshot()["measured_origin_raw"]==1845
    assert robot.right_bus.registers["Goal_Position","arm_right_wrist_flex"]==2772


def test_workspace_selected_admission_does_not_take_second_q0_read(monkeypatch):
    def after_first(obj,summary):
        obj.get_observation=lambda: (_ for _ in ()).throw(AssertionError("Do not reread/reseed the first fresh q0"))
    obj,evidence,control,summary,*_=right_guard(monkeypatch,before_admit=after_first)
    assert summary["measured_origin_raw"]==1845
    assert summary["reference_norm"]==pytest.approx((1845-827)/2212*200-100)


def test_workspace_actual_gripper_zero_to_hundred_pose_matches_retained_raw_goal(endpoint_activation,monkeypatch):
    from lerobot.motors import MotorNormMode
    robot,control,identity,owner=workspace_owner(endpoint_activation,monkeypatch)
    robot.right_bus.motors["arm_right_gripper"].norm_mode=MotorNormMode.RANGE_0_100
    assert robot.get_observation()["arm_right_gripper.pos"]==pytest.approx(21.121251629726206)
    assert control.apply(robot,frame(identity,owner,sequence=1))
    assert control.live_snapshot()["admission_version"]==2


def test_workspace_owned_writes_reach_both_fixed_endpoints_across_actual_q0(monkeypatch):
    obj,control,now,identity,owner,writes=native_right_ready(monkeypatch)
    original=copy.deepcopy(control.live_snapshot())
    frozen=obj._am1_live_frozen_holds
    action_seq=1
    input_seq=0
    for target,expected,steps in ((original["corridor_max_norm"],1867,22),
                                  (original["corridor_min_norm"],1834,32)):
        for _ in range(steps):
            now[0]+=0.1
            action_seq+=1
            input_seq+=1
            assert control.apply(obj,frame(identity,owner,sequence=action_seq,mode="active",epoch=2,
                sample=right_sample(seq=input_seq,expiry=now[0]+.25,target=target)))
        live=control.live_snapshot()
        assert live["encoded_goal_raw"]==expected
        assert live["goal_write_returned"] is True
        assert live["goal_action_sequence"]==action_seq
        assert live["cursor_norm"]==target
        assert live["reference_norm"]==original["reference_norm"]
        assert live["measured_origin_raw"]==1845
        assert live["workspace"]==original["workspace"]
        assert live["live_deadline_monotonic_s"]==original["live_deadline_monotonic_s"]
        assert (live["measured_min_raw"],live["measured_max_raw"])==(1811,1878)
        assert obj._am1_live_frozen_holds==frozen
    goals=[row for row in writes if row[0]==42]
    assert {raw for _,_,values in goals for raw in values.values()} >= {1834,1867}
    assert all(set(values)=={3} for _,_,values in goals)


def wrist_workspace_context(raw=2443, *, left_wrist_raw=1546, shoulder_pan_raw=2141, left_elbow_raw=2124, left_gripper_raw=3167):
    """Existing actual AM1 fixture; preserve actual changed Goal and measured pose."""
    goals={name:value for name,value in WORKSPACE_GOALS.items() if name!=RIGHT}
    goals["arm_right_wrist_roll"]=raw
    goals["arm_left_wrist_flex"]=left_wrist_raw
    goals["arm_right_shoulder_pan"]=shoulder_pan_raw
    goals["arm_left_elbow_flex"]=left_elbow_raw
    goals["arm_left_gripper"]=left_gripper_raw
    pose={}
    for name,value in goals.items():
        if not name.startswith("arm_right_"):continue
        calibration=WORKSPACE_CALIBRATION[name]
        fraction=(value-calibration[3])/(calibration[4]-calibration[3]) if type(value) is int else 0
        pose[name+".pos"]=fraction*100 if name.endswith("_gripper") else fraction*200-100
    return dict(robot_model="alohamini1",selected_model="sts3215",
        calibrations={name:list(value) for name,value in WORKSPACE_CALIBRATION.items()},
        nonselected_goals=goals,right_pose=pose)


@pytest.mark.parametrize("raw",[2443,2444])
def test_workspace_fixed_wrist_anchor_admits_actual_one_tick_seed_without_rewrite(endpoint_activation,monkeypatch,raw):
    robot,control,identity,owner=workspace_owner(endpoint_activation,monkeypatch,wrist_raw=raw)
    assert control.apply(robot,frame(identity,owner,sequence=1))
    wrist="arm_right_wrist_roll"
    frozen=next(row for row in robot._am1_live_frozen_holds if row[1]==wrist)
    assert frozen[3]==robot.right_bus.registers["Goal_Position",wrist]==raw
    assert control.live_held[wrist+".pos"]==pytest.approx(raw/4095*200-100)
    assert control.live_snapshot()["measured_origin_raw"]==1845
    assert (control.live_snapshot()["corridor_min_raw"],control.live_snapshot()["corridor_max_raw"])==(1834,1867)
    assert control.live_snapshot()["live_deadline_monotonic_s"]-control.live_snapshot()["live_admitted_at_monotonic_s"]==90


@pytest.mark.parametrize("raw",[2441,2445,2443.0,True,None,"2443",float("nan")])
def test_workspace_fixed_wrist_anchor_rejects_outside_two_ticks_or_noninteger(raw):
    from examples.alohamini.am1_session_contract import LIVE_ELBOW_WORKSPACES
    with pytest.raises(ValueError):LIVE_ELBOW_WORKSPACES[RIGHT_RECIPE].qualify(wrist_workspace_context(raw))


@pytest.mark.parametrize("name",[name for name in WORKSPACE_GOALS if name not in {RIGHT,"arm_right_wrist_roll","arm_left_wrist_flex","arm_right_shoulder_pan","arm_left_elbow_flex","arm_left_gripper"}])
def test_workspace_rest_box_keeps_other_six_goal_coordinates_exact(name):
    from examples.alohamini.am1_session_contract import LIVE_ELBOW_WORKSPACES
    context=wrist_workspace_context(2444,left_wrist_raw=1547,shoulder_pan_raw=2142);context["nonselected_goals"][name]+=1
    with pytest.raises(ValueError):LIVE_ELBOW_WORKSPACES[RIGHT_RECIPE].qualify(context)


def test_workspace_wrist_exception_keeps_calibration_and_measured_pose_checks_exact():
    from examples.alohamini.am1_session_contract import LIVE_ELBOW_WORKSPACES
    spec=LIVE_ELBOW_WORKSPACES[RIGHT_RECIPE]
    context=wrist_workspace_context(2444);context["calibrations"]["arm_right_wrist_roll"][2]+=1
    with pytest.raises(ValueError):spec.qualify(context)
    context=wrist_workspace_context(2444);context["right_pose"]["arm_right_wrist_roll.pos"]+=1.01
    with pytest.raises(ValueError):spec.qualify(context)


def test_workspace_wrist_fixed_anchor_does_not_ratchet_or_mutate_actual_context():
    from examples.alohamini.am1_session_contract import LIVE_ELBOW_WORKSPACES
    spec=LIVE_ELBOW_WORKSPACES[RIGHT_RECIPE];context=wrist_workspace_context(2444);original=copy.deepcopy(context)
    spec.qualify(context);assert context==original
    with pytest.raises(ValueError):spec.qualify(wrist_workspace_context(2445))


def test_workspace_wrist_anchor_does_not_relax_same_run_frozen_goal_reads(endpoint_activation,monkeypatch):
    robot,control,identity,owner=workspace_owner(endpoint_activation,monkeypatch,wrist_raw=2444)
    assert control.apply(robot,frame(identity,owner,sequence=1))
    robot.right_bus.registers["Goal_Position","arm_right_wrist_roll"]=2443
    with pytest.raises(RuntimeError,match="acknowledged"):
        robot.am1_live_arm_commands({**identity,"recipe":RIGHT_RECIPE})


def test_workspace_wrist_policy_identity_covers_anchor_and_tolerance_without_schema_growth():
    from dataclasses import replace
    from examples.alohamini.am1_session_contract import LIVE_ELBOW_WORKSPACE_POLICIES
    spec=LIVE_ELBOW_WORKSPACE_POLICIES["am1-right-elbow-retained-v1"]
    assert spec.wrist_roll_goal_anchor_raw==2443 and spec.wrist_roll_goal_tolerance_raw==1
    assert spec.context_sha256!=replace(spec,wrist_roll_goal_anchor_raw=2444).context_sha256
    assert spec.context_sha256!=replace(spec,wrist_roll_goal_tolerance_raw=2).context_sha256
    assert spec.context_sha256!="837dc65e96b6c905891b5cfe6fb58f5e24d35f9c4a6ce5b329642b46390958f0"
    assert len(spec.descriptor)==5 and len(spec.summary())==11


@pytest.mark.parametrize("raw", [1546,1547])
def test_workspace_left_wrist_fixed_anchor_admits_actual_seed_without_goal_rewrite(endpoint_activation,monkeypatch,raw):
    robot,control,identity,owner=workspace_owner(endpoint_activation,monkeypatch,wrist_raw=2444,left_wrist_raw=raw)
    assert control.apply(robot,frame(identity,owner,sequence=1))
    motor="arm_left_wrist_flex"
    frozen=next(row for row in robot._am1_live_frozen_holds if row[1]==motor)
    assert frozen[3]==robot.left_bus.registers["Goal_Position",motor]==raw
    assert control.live_held[motor+".pos"]==pytest.approx((raw-1314)/1894*200-100)
    assert control.live_snapshot()["measured_origin_raw"]==1845
    assert (control.live_snapshot()["corridor_min_raw"],control.live_snapshot()["corridor_max_raw"])==(1834,1867)
    robot.left_bus.registers["Goal_Position",motor]=raw+1
    with pytest.raises(RuntimeError,match="acknowledged"):
        robot.am1_live_arm_commands({**identity,"recipe":RIGHT_RECIPE})


@pytest.mark.parametrize("raw",[1544,1548,1546.0,True,None,"1546",float("nan")])
def test_workspace_left_wrist_fixed_anchor_rejects_two_ticks_and_wrong_types(raw):
    from examples.alohamini.am1_session_contract import LIVE_ELBOW_WORKSPACES
    with pytest.raises(ValueError):
        LIVE_ELBOW_WORKSPACES[RIGHT_RECIPE].qualify(wrist_workspace_context(2444,left_wrist_raw=raw))


def test_workspace_left_wrist_fixed_anchor_preserves_actual_context_and_calibration():
    from examples.alohamini.am1_session_contract import LIVE_ELBOW_WORKSPACES
    spec=LIVE_ELBOW_WORKSPACES[RIGHT_RECIPE]
    context=wrist_workspace_context(2444,left_wrist_raw=1547);original=copy.deepcopy(context)
    spec.qualify(context);assert context==original
    with pytest.raises(ValueError):spec.qualify(wrist_workspace_context(2444,left_wrist_raw=1548))
    context["calibrations"]["arm_left_wrist_flex"][2]+=1
    with pytest.raises(ValueError):spec.qualify(context)


def test_workspace_left_wrist_fixed_anchor_changes_policy_identity_not_schema():
    from dataclasses import replace
    from examples.alohamini.am1_session_contract import LIVE_ELBOW_WORKSPACE_POLICIES
    spec=LIVE_ELBOW_WORKSPACE_POLICIES["am1-right-elbow-retained-v1"]
    assert spec.left_wrist_flex_goal_anchor_raw==1546
    assert spec.left_wrist_flex_goal_tolerance_raw==1
    assert spec.context_sha256!=replace(spec,left_wrist_flex_goal_anchor_raw=1547).context_sha256
    assert spec.context_sha256!=replace(spec,left_wrist_flex_goal_tolerance_raw=2).context_sha256
    assert spec.context_sha256!="349363aae4d53eea1ee508775dffcbd2e363606cf6067ab7b15427bb4afcc333"
    assert len(spec.descriptor)==5 and len(spec.summary())==11


@pytest.mark.parametrize("raw",[2140,2141,2142])
def test_workspace_shoulder_pan_fixed_anchor_admits_actual_seed_without_goal_rewrite(endpoint_activation,monkeypatch,raw):
    robot,control,identity,owner=workspace_owner(endpoint_activation,monkeypatch,wrist_raw=2444,left_wrist_raw=1547,shoulder_pan_raw=raw)
    assert control.apply(robot,frame(identity,owner,sequence=1))
    motor="arm_right_shoulder_pan"
    frozen=next(row for row in robot._am1_live_frozen_holds if row[1]==motor)
    assert frozen[3]==robot.right_bus.registers["Goal_Position",motor]==raw
    assert control.live_held[motor+".pos"]==pytest.approx((raw-752)/2704*200-100)
    summary=control.live_snapshot()
    assert summary["measured_origin_raw"]==1845
    assert (summary["corridor_min_raw"],summary["corridor_max_raw"])==(1834,1867)
    assert summary["live_deadline_monotonic_s"]-summary["live_admitted_at_monotonic_s"]==90
    robot.right_bus.registers["Goal_Position",motor]=raw+1
    with pytest.raises(RuntimeError,match="acknowledged"):
        robot.am1_live_arm_commands({**identity,"recipe":RIGHT_RECIPE})


@pytest.mark.parametrize("raw",[2137,2143,2141.0,True,None,"2141",float("nan")])
def test_workspace_shoulder_pan_fixed_anchor_rejects_two_ticks_and_wrong_types(raw):
    from examples.alohamini.am1_session_contract import LIVE_ELBOW_WORKSPACES
    with pytest.raises(ValueError):
        LIVE_ELBOW_WORKSPACES[RIGHT_RECIPE].qualify(wrist_workspace_context(2444,left_wrist_raw=1547,shoulder_pan_raw=raw))


def test_workspace_shoulder_pan_fixed_anchor_preserves_actual_context_calibration_and_pose():
    from examples.alohamini.am1_session_contract import LIVE_ELBOW_WORKSPACES
    spec=LIVE_ELBOW_WORKSPACES[RIGHT_RECIPE]
    context=wrist_workspace_context(2444,left_wrist_raw=1547,shoulder_pan_raw=2142);original=copy.deepcopy(context)
    spec.qualify(context);assert context==original
    with pytest.raises(ValueError):spec.qualify(wrist_workspace_context(2444,left_wrist_raw=1547,shoulder_pan_raw=2143))
    context["calibrations"]["arm_right_shoulder_pan"][2]+=1
    with pytest.raises(ValueError):spec.qualify(context)
    context=copy.deepcopy(original);context["right_pose"]["arm_right_shoulder_pan.pos"]+=1.01
    with pytest.raises(ValueError):spec.qualify(context)


def test_workspace_shoulder_pan_fixed_anchor_is_in_policy_identity_without_schema_growth():
    from dataclasses import replace
    from examples.alohamini.am1_session_contract import LIVE_ELBOW_WORKSPACE_POLICIES
    spec=LIVE_ELBOW_WORKSPACE_POLICIES["am1-right-elbow-retained-v1"]
    assert spec.right_shoulder_pan_goal_anchor_raw==2141
    assert spec.right_shoulder_pan_goal_tolerance_raw==1
    assert spec.context_sha256!=replace(spec,right_shoulder_pan_goal_anchor_raw=2142).context_sha256
    assert spec.context_sha256!=replace(spec,right_shoulder_pan_goal_tolerance_raw=2).context_sha256
    assert spec.context_sha256!="75e93cad4c958797494679e1a5e3f9c992587004a53ef2f6aa900e38911b0fcf"
    assert len(spec.descriptor)==5 and len(spec.summary())==11


# Actual retained startup operands; full calibration/eight fixed Goals use the existing fixture.
@pytest.mark.parametrize("trial,pan,left_wrist,right_wrist", [
    ("p10-02",2141,1546,2443), ("p10-04",2141,1546,2443),
    ("p10-05",2141,1546,2443), ("p10-06",2141,1546,2443),
    ("p10-08",2141,1546,2443), ("p10-09",2141,1546,2443),
    ("p10-10",2141,1546,2443), ("p11-01",2141,1546,2443),
    ("p11-02",2141,1546,2444), ("p11-03",2141,1547,2444),
    ("p11-04",2141,1547,2444), ("p11-05",2141,1547,2444),
    ("p11-06",2141,1547,2444), ("p11-07",2142,1547,2444),
    ("p11-10",2138,1547,2444),
])
def test_workspace_rest_box_preserves_retained_context_operands(trial,pan,left_wrist,right_wrist):
    from examples.alohamini.am1_session_contract import LIVE_ELBOW_WORKSPACES
    context=wrist_workspace_context(right_wrist,left_wrist_raw=left_wrist,shoulder_pan_raw=pan)
    original=copy.deepcopy(context)
    LIVE_ELBOW_WORKSPACES[RIGHT_RECIPE].qualify(context)
    assert context==original,trial


@pytest.mark.parametrize("pan,left_wrist,right_wrist,left_elbow,left_gripper", [
    (pan,left,right,elbow,gripper) for pan in range(2138,2143) for left in (1546,1547)
    for right in (2443,2444) for elbow in (2124,2125) for gripper in (3167,3168)
])
def test_workspace_rest_box_is_fixed_between_starts(pan,left_wrist,right_wrist,left_elbow,left_gripper):
    from examples.alohamini.am1_session_contract import LIVE_ELBOW_WORKSPACES
    spec=LIVE_ELBOW_WORKSPACES[RIGHT_RECIPE]
    before=copy.deepcopy(spec.descriptor)
    spec.qualify(wrist_workspace_context(right_wrist,left_wrist_raw=left_wrist,shoulder_pan_raw=pan,
        left_elbow_raw=left_elbow,left_gripper_raw=left_gripper))
    assert spec.descriptor==before
    for refused in (2137,2143):
        with pytest.raises(ValueError):
            spec.qualify(wrist_workspace_context(right_wrist,left_wrist_raw=left_wrist,shoulder_pan_raw=refused,
                left_elbow_raw=left_elbow,left_gripper_raw=left_gripper))


@pytest.mark.parametrize("name,value,low,high", [
    ("arm_right_shoulder_pan",2137,2138,2142), ("arm_right_shoulder_pan",2143,2138,2142),
    ("arm_left_wrist_flex",1545,1546,1547), ("arm_left_wrist_flex",1548,1546,1547),
    ("arm_right_wrist_roll",2442,2443,2444), ("arm_right_wrist_roll",2445,2443,2444),
    ("arm_left_elbow_flex",2123,2124,2125), ("arm_left_elbow_flex",2126,2124,2125),
    ("arm_left_gripper",3166,3167,3168), ("arm_left_gripper",3169,3167,3168),
])
def test_workspace_rest_box_refusal_identifies_actual_coordinate_and_fixed_bounds(name,value,low,high):
    from examples.alohamini.am1_session_contract import LIVE_ELBOW_WORKSPACES
    context=wrist_workspace_context();context["nonselected_goals"][name]=value
    with pytest.raises(ValueError) as error:LIVE_ELBOW_WORKSPACES[RIGHT_RECIPE].qualify(context)
    message=str(error.value)
    assert all(str(part) in message for part in (name,value,low,high,"am1-right-elbow-rest-neighbourhood-v2"))


@pytest.mark.parametrize("name",["arm_right_shoulder_pan","arm_left_wrist_flex","arm_right_wrist_roll","arm_left_elbow_flex","arm_left_gripper"])
@pytest.mark.parametrize("value",[True,None,1.0,"2141",float("nan"),float("inf")])
def test_workspace_rest_box_refuses_wrong_raw_types(name,value):
    from examples.alohamini.am1_session_contract import LIVE_ELBOW_WORKSPACES
    context=wrist_workspace_context();context["nonselected_goals"][name]=value
    with pytest.raises(ValueError):LIVE_ELBOW_WORKSPACES[RIGHT_RECIPE].qualify(context)


def test_workspace_rest_box_versions_policy_without_rewriting_legacy_descriptor():
    from examples.alohamini.am1_session_contract import LIVE_ELBOW_WORKSPACES, LIVE_ELBOW_WORKSPACE_POLICIES
    legacy=LIVE_ELBOW_WORKSPACE_POLICIES["am1-right-elbow-retained-v1"]
    original=dict(version=1,id="am1-right-elbow-retained-v1",
        context_sha256="8766486351b401fad533f0df28032cc7fa4fb0924739b9e6b13ab41d77b59ccf",
        evidence_min_raw=1833,evidence_max_raw=1867)
    assert legacy.descriptor==original
    legacy.qualify(wrist_workspace_context(2442,left_wrist_raw=1545,shoulder_pan_raw=2140))
    with pytest.raises(ValueError):legacy.qualify(wrist_workspace_context(2444,left_wrist_raw=1547,shoulder_pan_raw=2138))
    current=LIVE_ELBOW_WORKSPACES[RIGHT_RECIPE]
    assert current.id=="am1-right-elbow-rest-neighbourhood-v2"
    assert current.context_sha256!=legacy.context_sha256
    assert len(current.descriptor)==5 and current.descriptor["version"]==1 and len(current.summary())==11
    assert legacy.descriptor==original
    with pytest.raises(TypeError):LIVE_ELBOW_WORKSPACE_POLICIES[current.id]=legacy


def test_workspace_rest_box_fresh_prospective_seed_keeps_native_eleven_holds_exact(endpoint_activation,monkeypatch):
    robot,control,identity,owner=workspace_owner(endpoint_activation,monkeypatch,
        wrist_raw=2444,left_wrist_raw=1547,shoulder_pan_raw=2138,left_elbow_raw=2125,left_gripper_raw=3168)
    assert control.apply(robot,frame(identity,owner,sequence=1))
    frozen=robot._am1_live_frozen_holds
    live=control.live_snapshot()
    assert live["workspace"]["id"]=="am1-right-elbow-rest-neighbourhood-v2"
    assert live["measured_origin_raw"]==1845
    assert (live["corridor_min_raw"],live["corridor_max_raw"])==(1834,1867)
    assert live["live_deadline_monotonic_s"]-live["live_admitted_at_monotonic_s"]==90
    for bus,name,value,other in ((robot.right_bus,"arm_right_shoulder_pan",2138,2139),
                               (robot.left_bus,"arm_left_wrist_flex",1547,1546),
                               (robot.right_bus,"arm_right_wrist_roll",2444,2443),
                               (robot.left_bus,"arm_left_elbow_flex",2125,2124),
                               (robot.left_bus,"arm_left_gripper",3168,3167)):
        assert bus.registers["Goal_Position",name]==value
        bus.registers["Goal_Position",name]=other
        with pytest.raises(RuntimeError,match="acknowledged"):
            robot.am1_live_arm_commands({**identity,"recipe":RIGHT_RECIPE})
        bus.registers["Goal_Position",name]=value
    assert robot._am1_live_frozen_holds==frozen


def test_workspace_rest_box_fresh_passive_pp_is_prospective_seed_not_old_goal():
    from examples.alohamini.am1_session_contract import LIVE_ELBOW_WORKSPACES
    # Three actual stopped passes: PP2125/3168 vs retained Goal2124/3167; no write occurred.
    context=wrist_workspace_context(2444,left_wrist_raw=1547,shoulder_pan_raw=2138,
        left_elbow_raw=2125,left_gripper_raw=3168)
    original=copy.deepcopy(context)
    result=LIVE_ELBOW_WORKSPACES[RIGHT_RECIPE].admission(context,1845,-7.956600361663661)
    assert result["measured_origin_raw"]==1845 and result["admission_version"]==2
    assert (result["corridor_min_raw"],result["corridor_max_raw"])==(1834,1867)
    assert context==original
