from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from lerobot.motors import Motor, MotorCalibration, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus
from lerobot.robots.alohamini import alohamini_host
from lerobot.robots.alohamini.alohamini import AlohaMini

SHOULDER = "arm_left_shoulder_lift"


def action_robot(monkeypatch):
    motors = {
        SHOULDER: Motor(2, "sts3215", MotorNormMode.RANGE_M100_100),
        "base_left_wheel": Motor(8, "sts3215", MotorNormMode.RANGE_M100_100),
        "base_back_wheel": Motor(9, "sts3215", MotorNormMode.RANGE_M100_100),
        "base_right_wheel": Motor(10, "sts3215", MotorNormMode.RANGE_M100_100),
    }
    calibration = {
        name: MotorCalibration(motor.id, 0, 0, 1000, 2200)
        for name, motor in motors.items()
    }
    bus = FeetechMotorsBus("unused-test-port", motors, calibration)
    bus.port_handler.is_open = True
    reads, writes = [], []

    def raw_read(address, length, ids, **kwargs):
        reads.append((address, length, tuple(ids), kwargs["num_retry"]))
        values = dict.fromkeys(ids, 1738 if address == 56 else 3)
        return values, 0

    def raw_write(address, length, values, **kwargs):
        writes.append((address, length, dict(values)))
        return 0

    monkeypatch.setattr(bus, "_sync_read", raw_read)
    monkeypatch.setattr(bus, "_sync_write", raw_write)
    robot = AlohaMini.__new__(AlohaMini)
    robot.config = SimpleNamespace(robot_model="alohamini1", max_relative_target=20.0)
    robot.left_bus, robot.right_bus = bus, None
    robot.left_arm_motors, robot.base_motors = [SHOULDER], list(motors)[1:]
    robot.cameras, robot.logs = {}, {}
    robot.wheel_radius, robot.base_radius = 0.05, 0.125
    robot.lift = SimpleNamespace(apply_action=lambda _: None)
    robot._gripper_current_limit_ma, robot._joint_current_limit_ma = 500.0, 1800.0
    robot._gripper_release_margin = robot._joint_release_margin = 1.0
    robot._gripper_hold_goal, robot._gripper_hold_direction = {}, {}
    robot._joint_hold_goal, robot._joint_hold_direction = {}, {}
    robot._gripper_open_direction, robot._gripper_hold_close_step = {}, 3
    robot._am1_left_shoulder_evidence_enabled = True
    return robot, reads, writes


def test_reverse_goal_trace_retains_actual_raw_feedback_without_extra_control_reads(monkeypatch):
    robot, reads, writes = action_robot(monkeypatch)
    sent = robot.send_action({
        f"{SHOULDER}.pos": 20.0, "x.vel": 0, "y.vel": 0, "theta.vel": 0,
        "lift_axis.vel": 0,
    })
    trace = robot.logs["action_diagnostics"]["left_shoulder"]
    assert sent[f"{SHOULDER}.pos"] == 20.0
    assert (42, 2, {2: 1720}) in writes
    assert len(reads) == 3  # existing relative-position, current, force-limit position
    assert trace["requested"] == trace["final"] == 20.0
    assert trace["expected_goal_raw"] == 1720
    assert trace["present_position_raw"] == 1738
    assert trace["present_position_normalized"] == pytest.approx(23.0)
    assert trace["present_current_raw"] == 3
    assert trace["present_current_ma"] == 19.5
    assert trace["feedback_available"] is True
    assert trace["position_read_started_at"] <= trace["position_read_completed_at"]
    assert trace["current_read_started_at"] <= trace["current_read_completed_at"]


def command_state():
    state = alohamini_host.HostCommandState(watchdog_timeout_ms=1000, diagnostics_enabled=True)
    state.record_command({"left_shoulder": {
        "requested": 20.0, "final": 20.0, "expected_goal_raw": 1720,
        "present_position_raw": 1738, "present_position_normalized": 23.0,
        "present_current_raw": 3, "present_current_ma": 19.5,
        "feedback_available": True,
        "position_read_started_at": 10.0, "position_read_completed_at": 10.001,
        "current_read_started_at": 9.998, "current_read_completed_at": 9.999,
    }}, received_wall_time_ns=123)
    return state


class OwnerBus:
    motors = {SHOULDER: Motor(2, "sts3215", MotorNormMode.RANGE_M100_100)}
    model_ctrl_table = FeetechMotorsBus.model_ctrl_table

    def __init__(self, *, failure=None):
        self.reads = []
        self.failure = failure

    def read(self, register, motor, *, normalize, num_retry):
        self.reads.append((register, motor, normalize, num_retry))
        if register == "Goal_Position":
            if self.failure is not None:
                raise self.failure
            return 1720
        return 8


def trace_records(capsys):
    return [
        json.loads(line.split("] ", 1)[1])
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("[AM1 LEFT SHOULDER] ")
    ]


def test_owner_trace_is_rate_limited_keeps_source_times_and_reads_settings_once(capsys):
    clock = SimpleNamespace(now=20.0)
    bus = OwnerBus()
    robot = SimpleNamespace(config=SimpleNamespace(robot_model="alohamini1"), left_bus=bus)
    sampler = alohamini_host.AM1LeftShoulderEvidence(
        enabled=True, clock=lambda: clock.now, wall_clock_ns=lambda: 999,
    )
    state = command_state()
    sampler.report(robot, state)
    sampler.report(robot, state)
    clock.now = 21.1
    sampler.report(robot, state)
    records = trace_records(capsys)
    assert len(records) == 2
    assert sum(row[0] == "Goal_Position" for row in bus.reads) == 2
    assert sum(row[0] == "CW_Dead_Zone" for row in bus.reads) == 1
    assert len(bus.reads) <= 14  # two goals and one bounded settings snapshot
    assert all(row[1:] == (SHOULDER, False, 0) for row in bus.reads)
    assert records[-1]["goal_position_readback_raw"] == 1720
    assert records[-1]["goal_matches_expected_raw"] is True
    assert records[-1]["command_sequence"] == 1
    assert records[-1]["command_received_wall_time_ns"] == 123
    assert records[-1]["position_read_completed_at"] == 10.001
    assert records[-1]["current_read_completed_at"] == 9.999
    assert records[-1]["goal_read_started_at"] == 21.1
    assert records[-1]["goal_read_completed_at"] == 21.1
    assert records[-1]["diagnostic_available"] is True
    assert records[-1]["feedback_qualified"] is False


def test_failed_owner_read_is_unavailable_and_preserves_original_feedback_time(capsys):
    bus = OwnerBus(failure=ConnectionError("original goal read failed"))
    robot = SimpleNamespace(config=SimpleNamespace(robot_model="alohamini1"), left_bus=bus)
    sampler = alohamini_host.AM1LeftShoulderEvidence(enabled=True, clock=lambda: 20.0)
    sampler.report(robot, command_state())
    record = trace_records(capsys)[0]
    assert record["diagnostic_available"] is False
    assert record["feedback_qualified"] is False
    assert record["goal_position_readback_raw"] is None
    assert record["goal_matches_expected_raw"] is None
    assert record["position_read_completed_at"] == 10.001
    assert "original goal read failed" in record["goal_read_error"]


@pytest.mark.parametrize(("enabled", "model"), [(False, "alohamini1"), (True, "alohamini2")])
def test_disabled_or_other_model_trace_performs_no_owner_io(enabled, model, capsys):
    robot = SimpleNamespace(config=SimpleNamespace(robot_model=model))
    sampler = alohamini_host.AM1LeftShoulderEvidence(enabled=enabled)
    sampler.report(robot, command_state())
    assert trace_records(capsys) == []


def test_default_action_path_has_identical_wire_goals_and_read_count(monkeypatch):
    robot, reads, writes = action_robot(monkeypatch)
    robot._am1_left_shoulder_evidence_enabled = False
    sent = robot.send_action({
        f"{SHOULDER}.pos": 20.0, "x.vel": 0, "y.vel": 0, "theta.vel": 0,
        "lift_axis.vel": 0,
    })
    assert sent[f"{SHOULDER}.pos"] == 20.0
    assert (42, 2, {2: 1720}) in writes
    assert len(reads) == 3
    assert "left_shoulder" not in robot.logs["action_diagnostics"]


def test_settings_budget_stops_more_serial_reads_and_never_retries(capsys):
    clock = SimpleNamespace(now=20.0)

    class SlowOwnerBus(OwnerBus):
        def read(self, register, motor, *, normalize, num_retry):
            value = super().read(register, motor, normalize=normalize, num_retry=num_retry)
            if register != "Goal_Position":
                clock.now += 0.011
            return value

    bus = SlowOwnerBus()
    robot = SimpleNamespace(config=SimpleNamespace(robot_model="alohamini1"), left_bus=bus)
    sampler = alohamini_host.AM1LeftShoulderEvidence(enabled=True, clock=lambda: clock.now)
    sampler.report(robot, command_state())
    clock.now = 22.0
    sampler.report(robot, command_state())
    records = trace_records(capsys)
    assert len(bus.reads) == 4  # two goals and two settings, then deadline
    snapshot = records[0]["settings_snapshot"]
    assert snapshot["complete"] is False
    assert len(snapshot["registers_raw"]) == 2
    assert snapshot["unavailable"]["Operating_Mode"] == "20 ms snapshot budget exhausted"
    assert records[-1]["settings_snapshot"] == snapshot


@pytest.mark.parametrize(("opt_in", "profile", "expected_reads"), [
    (False, True, 0), (True, True, 13), (True, False, 0),
])
def test_host_opt_in_requires_profile_and_existing_am1_owner(
    monkeypatch, capsys, opt_in, profile, expected_reads,
):
    args = alohamini_host.make_parser().parse_args(
        ["--robot_model", "alohamini1", "--no_cameras", "--skip_lift_home"]
    )
    args.profile_cadence, args.profile_timing = profile, False
    if opt_in:
        monkeypatch.setenv("AM1_LEFT_SHOULDER_EVIDENCE", "1")
    else:
        monkeypatch.delenv("AM1_LEFT_SHOULDER_EVIDENCE", raising=False)
    clock, bus = SimpleNamespace(now=0.0), OwnerBus()
    enabled = []

    class Socket:
        def recv_string(self, flags):
            return json.dumps({
                f"{SHOULDER}.pos": 20.0,
                alohamini_host.AM1_LOCAL_CONTROL_KEY: {
                    "version": 1, "mode": "active", "epoch": 0,
                },
            })

        def recv_multipart(self, flags):
            raise alohamini_host.zmq.Again()

    class Robot:
        def __init__(self, config):
            self.config, self.is_connected, self.cameras = config, True, {}
            self.logs, self.left_bus = {}, bus

        def send_action(self, action):
            enabled.append(self._am1_left_shoulder_evidence_enabled)
            self.logs["action_diagnostics"] = {
                "left_shoulder": {
                    "requested": 20.0, "final": 20.0, "expected_goal_raw": 1720,
                    "position_read_completed_at": 0.5, "feedback_available": True,
                },
            }

        def get_observation(self):
            clock.now = 1.1
            return {f"{SHOULDER}.pos": 23.0}

        def disconnect(self, **kwargs):
            self.is_connected = False

    class Host:
        watchdog_timeout_ms, connection_time_s, max_loop_freq_hz = 1000, 0.5, 30

        def __init__(self, config):
            self.zmq_cmd_socket = self.zmq_observation_socket = Socket()

        def disconnect(self):
            pass

    monkeypatch.setattr(alohamini_host, "time", SimpleNamespace(
        monotonic=lambda: clock.now, perf_counter=lambda: clock.now,
        time_ns=lambda: int(clock.now * 1e9), sleep=lambda _: None,
    ))
    monkeypatch.setattr(alohamini_host, "make_parser", lambda: SimpleNamespace(parse_args=lambda: args))
    monkeypatch.setattr(alohamini_host, "AlohaMini", Robot)
    monkeypatch.setattr(alohamini_host, "AlohaMiniHost", Host)
    monkeypatch.setattr(alohamini_host, "connect_robot", lambda *args, **kwargs: None)
    alohamini_host.main()
    assert enabled == [opt_in]
    assert len(bus.reads) == expected_reads


@pytest.mark.parametrize("failure", [
    RuntimeError("original servo status fault"),
    AttributeError("original software defect"),
])
def test_goal_status_fault_or_software_defect_is_reported_then_preserved(capsys, failure):
    bus = OwnerBus(failure=failure)
    robot = SimpleNamespace(config=SimpleNamespace(robot_model="alohamini1"), left_bus=bus)
    sampler = alohamini_host.AM1LeftShoulderEvidence(enabled=True, clock=lambda: 20.0)
    with pytest.raises(type(failure)) as caught:
        sampler.report(robot, command_state())
    assert caught.value is failure
    record = trace_records(capsys)[0]
    assert record["diagnostic_available"] is False
    assert record["position_read_completed_at"] == 10.001
    assert str(failure) in record["goal_read_error"]


def test_settings_status_fault_is_reported_and_terminal_without_more_register_reads(capsys):
    failure = RuntimeError("settings servo fault")

    class FaultySettings(OwnerBus):
        def read(self, register, *args, **kwargs):
            value = super().read(register, *args, **kwargs)
            if register == "CW_Dead_Zone":
                raise failure
            return value

    bus = FaultySettings()
    robot = SimpleNamespace(config=SimpleNamespace(robot_model="alohamini1"), left_bus=bus)
    sampler = alohamini_host.AM1LeftShoulderEvidence(enabled=True, clock=lambda: 20.0)
    with pytest.raises(RuntimeError) as caught:
        sampler.report(robot, command_state())
    assert caught.value is failure
    record = trace_records(capsys)[0]
    assert record["diagnostic_available"] is False
    assert record["settings_snapshot"]["complete"] is False
    assert str(failure) in record["settings_snapshot"]["unavailable"]["CW_Dead_Zone"]
    assert len(bus.reads) == 2


def test_repeated_extra_goal_read_unavailability_remains_visible(capsys):
    bus = OwnerBus(failure=ConnectionError("goal unavailable"))
    robot = SimpleNamespace(config=SimpleNamespace(robot_model="alohamini1"), left_bus=bus)
    clock = SimpleNamespace(now=20.0)
    sampler = alohamini_host.AM1LeftShoulderEvidence(enabled=True, clock=lambda: clock.now)
    sampler.report(robot, command_state())
    clock.now = 21.1
    sampler.report(robot, command_state())
    record = trace_records(capsys)[-1]
    assert record["goal_read_attempt_count"] == 2
    assert record["goal_read_unavailable_count"] == 2
    assert record["position_read_completed_at"] == 10.001
    assert record["diagnostic_available"] is False
