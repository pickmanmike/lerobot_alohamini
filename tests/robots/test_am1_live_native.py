"""Live samples through the existing native owner, with device IO injected."""

import copy
from uuid import uuid4

import pytest

from examples.alohamini.am1_finite_task import JOINT_KEYS
from lerobot.robots.alohamini import alohamini_host as host
from tests.robots.test_am1_protected_host_boundary import Motor, binding

KEY = "arm_left_elbow_flex.pos"


class MeasuredMotor(Motor):
    def __init__(self):
        super().__init__()
        self.positions = dict.fromkeys(JOINT_KEYS, 0.0)

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
        self.control = host.AM1LocalControl(run_id=run_id, host_incarnation=incarnation, live=True)

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


def test_production_host_ipc_expiry_retains_same_run_and_new_intent_resumes():
    import os
    import time

    if os.name == "nt":
        pytest.skip("filesystem IPC production host is exercised in isolated native Pi checks")
    from tests.robots.test_am1_physical_ipc_transport import actual_mock_host, receive_feedback

    with actual_mock_host(live=True) as peer:
        owner = {k: str(uuid4()) for k in ("service_incarnation", "controller_generation", "connection_generation")}
        peer.client.send_action(frame(peer.identity, owner, sequence=1))
        _, initial = receive_feedback(peer, lambda m: m.get("live_input", {}).get("state") == "held")
        expiry = time.monotonic() + .25
        target = sample(expiry=expiry, target=-.5)
        peer.client.send_action(frame(peer.identity, owner, sequence=2, mode="active", epoch=2, sample=target))
        _, active = receive_feedback(peer, lambda m: m.get("live_input", {}).get("state") == "active")
        assert active["live_input"]["valid_until_monotonic_s"] == expiry
        _, expired = receive_feedback(peer, lambda m: m.get("live_input", {}).get("state") == "expired")
        assert expired["run_id"] == initial["run_id"] and expired["state"] == "paused"
        peer.client.send_action(frame(peer.identity, owner, sequence=3, epoch=3, revision=1))
        _, held = receive_feedback(peer, lambda m: m.get("epoch") == 3 and m.get("state") == "paused")
        assert held["live_input"]["live_deadline_monotonic_s"] == initial["live_input"]["live_deadline_monotonic_s"]
        peer.client.send_action(frame(peer.identity, owner, sequence=4, mode="active", epoch=4, revision=1,
                                      sample=sample(seq=2, expiry=time.monotonic() + .25, target=-1)))
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
    robot.left_bus.calibration[other] = MotorCalibration(2, 0, 1845, 1000, 3000)
    robot.left_arm_motors.append(other)
    robot.left_bus.write = lambda *args, **kwargs: None
    robot.lift.stop = lambda: None
    robot.hold_follower_arms = lambda: None
    robot.get_observation = lambda: {**dict.fromkeys(JOINT_KEYS, 0.0), other + ".pos": 120.0}
    control.apply(robot, frame(identity, owner, sequence=1))
    now[0] = 10.1
    control.apply(robot, frame(identity, owner, sequence=2, mode="active", epoch=2, sample=sample()))
    assert not [row for row in writes if row[0] == 42 and 2 in row[2]]
    assert control.accepted_action[other + ".pos"] == 120.0


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
