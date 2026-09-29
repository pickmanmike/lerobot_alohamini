"""Default-off selected-arm evidence; real action/observation code, fake buses only."""

import json
import sys
from types import SimpleNamespace

import pytest

from lerobot.robots.alohamini import alohamini_host as host
from tests.robots.test_alohamini_wrist_cadence import ActionBus, make_action_robot


LEFT = "arm_left_shoulder_pan"
RIGHT = "arm_right_elbow_flex"


class TrackingBus(ActionBus):
    def __init__(self, name, position, *, base=False):
        super().__init__({name: position}, base=base)
        self.goals = dict(self.positions)
        self.reads = []
        self.failure = None

    def sync_read(self, register, motors):
        if register == "Present_Velocity":
            return dict.fromkeys(motors, 0)
        if register == "Present_Current":
            return {motor: self.currents.get(motor, 0) for motor in motors}
        return super().sync_read(register, motors)

    def sync_write(self, register, values):
        super().sync_write(register, values)
        if register == "Goal_Position":
            self.goals.update(values)

    def read(self, register, motor, *, num_retry):
        self.reads.append((register, motor, num_retry))
        assert register == "Goal_Position" and motor in self.positions and num_retry == 0
        if self.failure is not None:
            raise self.failure
        return self.goals[motor]


def tracking_robot(*, enabled=True, model="alohamini1", relative_limit=20.0):
    robot = make_action_robot()
    robot.id = "fake-tracking-only"
    robot.config = SimpleNamespace(robot_model=model, max_relative_target=relative_limit)
    robot.left_bus = TrackingBus(LEFT, -8.0, base=True)
    robot.right_bus = TrackingBus(RIGHT, 34.0)
    robot.left_arm_motors, robot.right_arm_motors = [LEFT], [RIGHT]
    robot._left_arm_state_keys, robot._right_arm_state_keys = (LEFT,), (RIGHT,)
    robot._arm_tracking_readback_enabled = enabled
    robot._last_currents_log_t = 0.0
    robot._overcurrent_count = {}
    robot._overcurrent_trip_n = 20
    robot.lift.contribute_observation = lambda obs: obs.update({"lift_axis.height_mm": 10})
    return robot


def command(left=-5.0, right=37.0):
    return {f"{LEFT}.pos": left, f"{RIGHT}.pos": right,
            "x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 0.0}


def test_actual_action_records_requested_relative_and_final_targets_without_changing_writes():
    # Missing pre-current-limit evidence or substituting requested for final must fail.
    robot = tracking_robot(relative_limit=1.0)
    robot.right_bus.currents[RIGHT] = 300  # Synthetic force-limit trip, not real run evidence.
    sent = robot.send_action(command())
    assert sent[f"{LEFT}.pos"] == -7
    assert sent[f"{RIGHT}.pos"] == 34
    assert "arm_tracking_action" in robot.logs
    evidence = robot.logs["arm_tracking_action"]
    assert evidence[LEFT] == {"requested": -5, "relative_limited": -7, "final": -7}
    assert evidence[RIGHT] == {"requested": 37, "relative_limited": 35, "final": 34}
    assert robot.left_bus.writes == [
        ("Goal_Position", {LEFT: -7}),
        ("Goal_Velocity", {"base_left_wheel": 0, "base_back_wheel": 0, "base_right_wheel": 0}),
    ]
    assert robot.right_bus.writes == [("Goal_Position", {RIGHT: 34})]


@pytest.mark.parametrize("enabled,model", [(False, "alohamini1"), (True, "alohamini2"), (True, "alohamini2pro")])
def test_disabled_and_other_models_leave_commands_and_reads_unchanged(enabled, model):
    robot = tracking_robot(enabled=enabled, model=model)
    sent = robot.send_action(command())
    robot.get_observation()
    assert sent[f"{LEFT}.pos"] == -5 and sent[f"{RIGHT}.pos"] == 37
    assert "arm_tracking_action" not in robot.logs
    assert "arm_tracking_observation" not in robot.logs
    assert robot.left_bus.reads == robot.right_bus.reads == []


def test_same_owner_capture_pairs_actual_endpoint_and_return_with_goal_and_feedback(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(host.time, "monotonic", lambda: clock[0])
    records = []
    robot = tracking_robot()
    capture = host.AM1ArmTrackingCapture(emit=records.append)
    for sequence, (left, right) in enumerate([(-5, 37), (-8, 34)], 1):
        robot.send_action(command(left, right))
        robot.get_observation()
        capture.after_command(robot, command_sequence=sequence, observation_id=sequence + 10, epoch=0)
        clock[0] += 0.3
    assert len(records) == 2
    for row, expected in zip(records, [(-5, 37), (-8, 34)], strict=True):
        assert row["sync_write_returned"] is True
        assert row["servo_write_acknowledged"] is False
        assert row["readback_writes_performed"] is False
        assert row["joints"][LEFT]["goal_position_readback"] == expected[0]
        assert row["joints"][RIGHT]["goal_position_readback"] == expected[1]
        assert row["joints"][LEFT]["observed_position"] == -8
        assert row["joints"][RIGHT]["observed_position"] == 34
        assert row["joints"][RIGHT]["current_ma"] == 0
        assert row["observation_id"] == row["command_sequence"] + 10
    assert robot.left_bus.reads == [("Goal_Position", LEFT, 0)] * 2
    assert robot.right_bus.reads == [("Goal_Position", RIGHT, 0)] * 2


def test_capture_is_time_bounded_has_no_catchup_and_does_not_read_same_command_twice(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(host.time, "monotonic", lambda: clock[0])
    records = []
    robot = tracking_robot()
    robot.send_action(command())
    robot.get_observation()
    capture = host.AM1ArmTrackingCapture(emit=records.append)
    def sample(sequence):
        capture.after_command(robot, command_sequence=sequence, observation_id=sequence, epoch=0)
    sample(1)
    clock[0] = 10.24
    sample(2)
    assert len(records) == 1
    clock[0] = 12.0
    sample(2)
    sample(3)
    clock[0] = 12.3
    sample(2)
    assert len(records) == 2  # One late sample, no replay or catch-up.
    clock[0] = 130.0
    sample(4)
    clock[0] = 131.0
    sample(5)
    assert len(robot.left_bus.reads) == 2
    assert records[-1]["event"] == "am1_arm_tracking_capture_complete"
    assert records[-1]["reason"] == "time_bound"


def test_capture_preserves_real_readback_error_and_partial_evidence(monkeypatch):
    robot = tracking_robot()
    robot.send_action(command())
    robot.get_observation()
    records = []
    capture = host.AM1ArmTrackingCapture(emit=records.append)
    failure = RuntimeError("synthetic servo read failure")
    robot.right_bus.failure = failure
    with pytest.raises(RuntimeError) as caught:
        capture.after_command(robot, command_sequence=7, observation_id=9, epoch=0)
    assert caught.value is failure
    assert records[0]["joints"][LEFT]["goal_position_readback"] == -5
    assert records[0]["joints"][RIGHT]["goal_position_readback"] is None
    assert "synthetic servo read failure" in records[0]["readback_error"]


def test_reporting_failure_does_not_replace_the_primary_readback_error():
    robot = tracking_robot()
    robot.send_action(command())
    robot.get_observation()
    failure = RuntimeError("original servo error")
    robot.right_bus.failure = failure
    def broken_emit(row):
        raise OSError("log sink failed")
    capture = host.AM1ArmTrackingCapture(emit=broken_emit)
    with pytest.raises(RuntimeError) as caught:
        capture.after_command(robot, command_sequence=1, observation_id=1, epoch=0)
    assert caught.value is failure
    assert any("log sink failed" in note for note in failure.__notes__)


@pytest.mark.parametrize("enabled,fault", [(False, False), (True, False), (True, True)])
def test_actual_host_only_reads_opted_in_active_commands_and_cleans_up(monkeypatch, capsys, enabled, fault):
    clock = [0.0]
    monkeypatch.setattr(host.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(host.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(host.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setenv("AM1_ARM_TRACKING_READBACK", "1" if enabled else "0")
    monkeypatch.delenv("AM1_SYNC_SHOULDER_READBACK", raising=False)
    robot = tracking_robot(enabled=False)  # The actual host must opt it in.
    cleanup = []
    robot.connect = lambda **kwargs: None
    robot.disconnect = lambda **kwargs: cleanup.append("robot")
    failure = RuntimeError("actual host readback fault")
    if fault:
        robot.right_bus.failure = failure
    class Commands:
        def recv_string(self, flags):
            return json.dumps({**command(), "_am1_local_control": {"version": 1, "mode": "active", "epoch": 0}})
    class Observations:
        def recv_multipart(self, flags):
            raise host.zmq.Again()
    monkeypatch.setattr(host, "AlohaMini", lambda config: robot)
    monkeypatch.setattr(host, "AlohaMiniHost", lambda config: SimpleNamespace(
        zmq_cmd_socket=Commands(), zmq_observation_socket=Observations(),
        connection_time_s=1.1, watchdog_timeout_ms=1000, max_loop_freq_hz=30,
        disconnect=lambda: cleanup.append("host"),
    ))
    monkeypatch.setattr(sys, "argv", ["host", "--robot_model", "alohamini1", "--no_cameras"])
    if fault:
        with pytest.raises(RuntimeError) as caught:
            host.main()
        assert caught.value is failure
    else:
        host.main()
    rows = [json.loads(line.split("] ", 1)[1]) for line in capsys.readouterr().out.splitlines()
            if line.startswith("[AM1 ARM TRACKING] ")]
    assert bool(rows) is enabled
    assert bool(robot.left_bus.reads) is enabled
    assert set(cleanup) == {"robot", "host"}
    if enabled and not fault:
        assert 3 <= len(rows) <= 5
        assert all(row["epoch"] == 0 and row["observation_id"] > 0 for row in rows)
