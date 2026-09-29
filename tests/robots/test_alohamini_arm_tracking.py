"""Default-off selected-arm evidence; real action/observation code, fake buses only."""

import json
import sys
from types import SimpleNamespace

import pytest

from lerobot.robots.alohamini import alohamini_host as host
from tests.robots.test_alohamini_wrist_cadence import ActionBus, make_action_robot


LEFT = "arm_left_shoulder_pan"
RIGHT = "arm_right_elbow_flex"


def test_configuration_snapshot_uses_supported_sts3215_registers_only():
    from lerobot.motors.feetech.tables import MODEL_CONTROL_TABLE
    from lerobot.robots.alohamini.arm_tracking import TRACKING_CONFIGURATION_REGISTERS
    assert set(TRACKING_CONFIGURATION_REGISTERS) <= MODEL_CONTROL_TABLE["sts3215"].keys()
    assert "Phase" not in TRACKING_CONFIGURATION_REGISTERS


class TrackingBus(ActionBus):
    def __init__(self, name, position, *, base=False):
        super().__init__({name: position}, base=base)
        self.goals = dict(self.positions)
        self.reads = []
        self.failure = None

    def sync_read(self, register, motors, **kwargs):
        if register == "Present_Velocity":
            return dict.fromkeys(motors, 0)
        if register == "Present_Current":
            return {motor: self.currents.get(motor, 0) for motor in motors}
        return super().sync_read(register, motors)

    def sync_write(self, register, values):
        super().sync_write(register, values)
        if register == "Goal_Position":
            self.goals.update(values)

    def write(self, register, motor, value, **kwargs):
        self.writes.append((register, {motor: value}))
        if register == "Goal_Position":
            self.goals[motor] = value

    def read(self, register, motor, *, num_retry, **kwargs):
        if register == "Present_Position":
            return self.positions[motor]
        self.reads.append((register, motor, num_retry))
        assert register == "Goal_Position" and motor in self.positions and num_retry == 0
        if self.failure is not None:
            raise self.failure
        return self.goals[motor]


def tracking_robot(*, enabled=True, model="alohamini1", relative_limit=20.0):
    robot = make_action_robot()
    robot.id = "fake-tracking-only"
    robot.config = SimpleNamespace(robot_model=model, max_relative_target=relative_limit, no_follower=False)
    robot.left_bus = TrackingBus(LEFT, -8.0, base=True)
    robot.right_bus = TrackingBus(RIGHT, 34.0)
    robot.left_arm_motors, robot.right_arm_motors = [LEFT], [RIGHT]
    robot._left_arm_state_keys, robot._right_arm_state_keys = (LEFT,), (RIGHT,)
    robot._arm_tracking_readback_enabled = enabled
    robot._last_currents_log_t = 0.0
    robot._overcurrent_count = {}
    robot._overcurrent_trip_n = 20
    robot.lift.contribute_observation = lambda obs: obs.update({"lift_axis.height_mm": 10})
    robot.lift.stop = lambda: None
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


def run_deferred_host(monkeypatch, capsys, actions, *, limit=20.0, fault=None, cancel=False):
    """Real host, Local state machine, action/observation and capture; fake I/O only."""
    clock = [0.0]
    monkeypatch.setattr(host.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(host.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(host.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + max(seconds, .01)))
    monkeypatch.setenv("AM1_ARM_TRACKING_READBACK", "1")
    monkeypatch.setenv("AM1_ARM_TRACKING_START", "right-elbow-request")
    monkeypatch.delenv("AM1_SYNC_SHOULDER_READBACK", raising=False)
    robot = tracking_robot(enabled=False, relative_limit=limit)
    cleanup = []
    robot.connect = lambda **kwargs: None
    robot.disconnect = lambda **kwargs: cleanup.append("robot")
    robot.right_bus.failure = fault
    pending = iter(actions)
    class Commands:
        def recv_string(self, flags):
            try:
                when, mode, epoch, left, right = next(pending)
            except StopIteration:
                if cancel:
                    raise KeyboardInterrupt()
                clock[0] = 400
                raise host.zmq.Again()
            clock[0] = when
            return json.dumps({**command(left, right), "_am1_local_control": {
                "version": 1, "mode": mode, "epoch": epoch,
            }})
    class Observations:
        def recv_multipart(self, flags):
            raise host.zmq.Again()
    monkeypatch.setattr(host, "AlohaMini", lambda config: robot)
    monkeypatch.setattr(host, "AlohaMiniHost", lambda config: SimpleNamespace(
        zmq_cmd_socket=Commands(), zmq_observation_socket=Observations(), connection_time_s=300,
        watchdog_timeout_ms=1000, max_loop_freq_hz=30, disconnect=lambda: cleanup.append("host"),
    ))
    monkeypatch.setattr(sys, "argv", ["host", "--robot_model", "alohamini1", "--no_cameras"])
    if fault:
        with pytest.raises(type(fault)) as caught:
            host.main()
        assert caught.value is fault
    else:
        host.main()
    rows = [json.loads(line.split("] ", 1)[1]) for line in capsys.readouterr().out.splitlines()
            if line.startswith("[AM1 ARM TRACKING] ")]
    assert cleanup == ["robot", "host"]
    return robot, rows


def test_deferred_actual_host_waits_for_requested_elbow_not_observed_or_limited(monkeypatch, capsys):
    # Break caught: starting on baseline/time/left motion or gating on final/observed elbow.
    robot, rows = run_deferred_host(monkeypatch, capsys, [
        (0, "active", 0, -8, 34), (200, "active", 0, -5, 34.0),
        (201, "active", 0, -5, 37), (201.1, "active", 0, -5, 37),
        (201.3, "active", 0, -8, 34),
    ], limit=0.0)
    samples = [r for r in rows if r["event"] == "am1_arm_tracking"]
    assert [r["command_sequence"] for r in samples] == [3, 5]
    assert len(robot.left_bus.reads) == len(robot.right_bus.reads) == 2
    assert samples[0]["joints"][RIGHT] == {
        "requested": 37, "relative_limited": 34, "final": 34,
        "observed_position": 34, "current_ma": 0, "goal_position_readback": 34,
    }
    trigger = next(r for r in rows if r["event"] == "am1_arm_tracking_trigger")
    assert trigger["baseline_requested"] == 34 and trigger["requested"] == 37
    assert trigger["command_sequence"] == 3 and trigger["triggered_at"] == 201


@pytest.mark.parametrize("cancel", [False, True])
def test_deferred_no_trigger_and_cancel_are_explicit_without_reads(monkeypatch, capsys, cancel):
    robot, rows = run_deferred_host(monkeypatch, capsys, [
        (0, "active", 0, -8, 34), (200, "active", 0, -5, 34.0),
    ], cancel=cancel)
    assert robot.left_bus.reads == robot.right_bus.reads == []
    assert rows[-1]["event"] == "am1_arm_tracking_capture_complete"
    assert rows[-1]["reason"] == "no_trigger"
    assert rows[-1]["stop_reason"] == ("cancelled" if cancel else "session_end")
    assert rows[-1]["baseline_requested"] == 34 and rows[-1]["samples"] == 0


def test_deferred_real_host_read_fault_retains_partial_evidence_and_cleanup(monkeypatch, capsys):
    failure = RuntimeError("deferred read fault")
    robot, rows = run_deferred_host(monkeypatch, capsys, [
        (0, "active", 0, -8, 34), (2, "active", 0, -8, 37),
    ], fault=failure)
    samples = [r for r in rows if r["event"] == "am1_arm_tracking"]
    assert len(samples) == 1 and "deferred read fault" in samples[0]["readback_error"]
    assert samples[0]["command_sequence"] == 2
    assert samples[0]["joints"][LEFT]["goal_position_readback"] == -8
    assert samples[0]["joints"][RIGHT]["goal_position_readback"] is None
    assert len(robot.left_bus.reads) == len(robot.right_bus.reads) == 1


def test_deferred_baseline_float_equality_is_not_servo_quantization(monkeypatch):
    import math
    clock = [0.0]
    monkeypatch.setattr(host.time, "monotonic", lambda: clock[0])
    rows = []
    robot = tracking_robot()
    capture = host.AM1ArmTrackingCapture(start="right-elbow-request", emit=rows.append)
    for seq, right in enumerate([34, 34.0, math.nextafter(34.0, math.inf)], 1):
        robot.send_action(command(right=right))
        robot.get_observation()
        capture.after_command(robot, command_sequence=seq, observation_id=seq, epoch=0)
        clock[0] += 200
    samples = [r for r in rows if r["event"] == "am1_arm_tracking"]
    assert len(samples) == 1 and samples[0]["command_sequence"] == 3
    assert len(robot.right_bus.reads) == 1


@pytest.mark.parametrize("report_delay", [.4, 129.0])
def test_deferred_trigger_reporting_cannot_stale_timestamp_or_overrun_deadline(monkeypatch, report_delay):
    clock = [0.0]
    monkeypatch.setattr(host.time, "monotonic", lambda: clock[0])
    rows = []
    def delayed_emit(row):
        rows.append(row)
        if row["event"] == "am1_arm_tracking_trigger":
            clock[0] += report_delay
    robot = tracking_robot()
    capture = host.AM1ArmTrackingCapture(start="right-elbow-request", emit=delayed_emit)
    for seq, right in enumerate([34, 37], 1):
        clock[0] = float(seq - 1)
        robot.send_action(command(right=right))
        robot.get_observation()
        capture.after_command(robot, command_sequence=seq, observation_id=seq, epoch=0)
    assert capture.started_at == 1.0  # Never reset to conceal reporting time.
    if report_delay >= 120:
        assert robot.left_bus.reads == robot.right_bus.reads == []
        assert rows[-1]["reason"] == "time_bound"
    else:
        assert rows[-1]["read_started_at"] == 1.0 + report_delay
        assert len(robot.left_bus.reads) == len(robot.right_bus.reads) == 1


def test_deferred_epoch_changes_do_not_reset_baseline_or_deadline(monkeypatch, capsys):
    robot, rows = run_deferred_host(monkeypatch, capsys, [
        (0, "active", 0, -8, 34), (1, "pause", 1, -8, 34),
        (130, "active", 2, -8, 34), (131, "active", 2, -8, 37),
        (132, "pause", 3, -8, 37), (250, "active", 4, -8, 34),
        (251, "active", 4, -8, 37), (252, "active", 4, -8, 34),
    ])
    assert len([r for r in rows if r["event"] == "am1_arm_tracking_trigger"]) == 1
    samples = [r for r in rows if r["event"] == "am1_arm_tracking"]
    assert [(r["epoch"], r["read_started_at"]) for r in samples] == [(2, 131), (4, 250)]
    assert rows[-1]["reason"] == "time_bound"
    assert len(robot.right_bus.reads) == 2


def test_deferred_actual_host_caps_reads_without_catchup(monkeypatch, capsys):
    actions = [(0, "active", 0, -8, 34)]
    for index in range(482):
        when = 10 + index * .25
        actions.extend([(when, "active", 0, -8, 37), (when + .01, "active", 0, -8, 37)])
    robot, rows = run_deferred_host(monkeypatch, capsys, actions)
    samples = [row for row in rows if row["event"] == "am1_arm_tracking"]
    assert len(samples) == 480
    assert len(robot.left_bus.reads) == len(robot.right_bus.reads) == 480
    assert [row["read_started_at"] for row in samples] == [10 + index * .25 for index in range(480)]
    assert len([row for row in rows if row["event"] == "am1_arm_tracking_capture_complete"]) == 1


def test_deferred_completion_reporting_failure_preserves_motor_error_and_cleanup(monkeypatch, capsys):
    original = host.AM1ArmTrackingCapture.finish
    def broken_finish(self, reason):
        original(self, reason)
        raise OSError("completion sink failed")
    monkeypatch.setattr(host.AM1ArmTrackingCapture, "finish", broken_finish)
    failure = RuntimeError("primary readback failure")
    run_deferred_host(monkeypatch, capsys, [
        (0, "active", 0, -8, 34), (2, "active", 0, -8, 37),
    ], fault=failure)
    assert any("completion sink failed" in note for note in failure.__notes__)


@pytest.mark.parametrize("model,enabled,fault", [("alohamini1", True, False), ("alohamini1", True, True),
                                                 ("alohamini1", False, False), ("alohamini2", True, False),
                                                 ("alohamini2pro", True, False)])
def test_selected_configuration_snapshot_is_once_before_activation_and_read_only(monkeypatch, capsys, model, enabled, fault):
    robot = tracking_robot(model=model)
    robot._arm_tracking_config_snapshot_enabled = enabled
    events = []
    failure = ConnectionError("settings read failed")
    for label, bus in (("left", robot.left_bus), ("right", robot.right_bus)):
        bus.is_connected = False
        bus.is_calibrated = True
        bus.connect = lambda bus=bus: setattr(bus, "is_connected", True)
        def read(register, motor, *, normalize, num_retry, label=label):
            assert normalize is False and num_retry == 0
            events.append(("read", label, register, motor))
            if fault and label == "right":
                raise failure
            return 16 if register == "P_Coefficient" else 0
        bus.read = read
    robot.configure = lambda: events.append(("configure",))
    robot.activate_motors = lambda **kw: events.append(("activate",))
    robot._safe_shutdown = lambda **kw: events.append(("cleanup",))
    robot.lift.home = lambda: events.append(("am2_home",))
    if fault:
        with pytest.raises(ConnectionError) as caught:
            robot.connect()
        assert caught.value is failure
        assert events[-1] == ("cleanup",) and ("activate",) not in events
    else:
        robot.connect()
    out = capsys.readouterr().out
    if enabled and model == "alohamini1":
        reads = [e for e in events if e[0] == "read"]
        assert reads and events[0] == ("configure",)
        assert {e[3] for e in reads} <= {LEFT, RIGHT}
        assert all(e[2] != "Phase" for e in reads)
        assert len(reads) == len(set(reads))
        assert '"phase":"post_configuration_pre_activation"' in out
        if not fault:
            from lerobot.robots.alohamini.arm_tracking import TRACKING_CONFIGURATION_REGISTERS
            assert len(reads) == 32
            assert {e[2] for e in reads} == set(TRACKING_CONFIGURATION_REGISTERS)
            assert events[-1] == ("activate",)
            assert '"P_Coefficient":16' in out
    else:
        assert all(e[0] != "read" for e in events) and "ARM CONFIG" not in out
    assert robot.left_bus.writes == robot.right_bus.writes == []
