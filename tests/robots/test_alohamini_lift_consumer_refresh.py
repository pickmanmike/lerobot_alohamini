"""Same-owner host/consumer timing with real controls and fake grouped motor I/O."""

from __future__ import annotations

import json
import math
from types import SimpleNamespace

import pytest

from lerobot.robots.alohamini import alohamini as robot_module
from lerobot.robots.alohamini import alohamini_host as host
from lerobot.robots.alohamini import lift_motor_feedback as feedback
from lerobot.robots.alohamini.config_alohamini import AlohaMiniConfig
from tests.robots.test_alohamini_lift_operational import operating_robot  # noqa: F401


@pytest.fixture
def host_case(operating_robot, monkeypatch, tmp_path):
    """Run one real host tick; inject elapsed work, never replace its decisions."""
    _, clock = operating_robot

    def run(*, mode="active", consumer="observation", work_s=0.03505, refresh_fault=None):
        robot = robot_module.AlohaMini(AlohaMiniConfig(
            robot_model="alohamini1", no_follower=False, cameras={},
            calibration_dir=tmp_path, max_relative_target=20.0,
        ))
        control = host.AM1LocalControl()
        state = SimpleNamespace(
            robot=robot, clock=clock, control=control, records=[], observations=[],
            actions=[], events=[], runtime_reads=[], start_ticks=None, start_zero=None,
            sample_phase=None, rejected=[],
        )
        arm_names = robot.left_arm_motors + robot.right_arm_motors
        arm_targets = {f"{name}.pos": 13 for name in arm_names}
        for bus, names in ((robot.left_bus, robot.left_arm_motors), (robot.right_bus, robot.right_arm_motors)):
            for name in names:
                bus.registers[("Present_Position", name)] = 10
        command = {
            **arm_targets, "x.vel": 0, "y.vel": 0, "theta.vel": 0, "lift_axis.vel": 0,
            host.AM1_LOCAL_CONTROL_KEY: {"version": 1, "mode": "pause" if mode == "pause" else "active",
                                      "epoch": {"active": 0, "pause": 1, "resume": 2}[mode]},
        }
        original_connect = robot.connect

        def connect(**kwargs):
            original_connect(calibrate=False, **kwargs)
            op = robot._lift_operation
            if mode == "resume":
                control.apply(robot, {host.AM1_LOCAL_CONTROL_KEY: {"version": 1, "mode": "pause", "epoch": 1}})
            # Five *real grouped reads* in occupied slots. Do not reset history or
            # stamp synthetic lows into it. The final same-slot read models the
            # saved 468.019 ms age plus 35.05 ms of consumer-path work.
            origin = op.temperature._origin_ns / 1e9
            start = origin + math.ceil((clock.now - origin) / 0.1) * 0.1
            for index in range(5):
                clock.sleep(start + index * 0.1 - clock.now - 0.002)
                if refresh_fault == "high" and index >= 3:
                    robot.left_bus.registers[("Present_Temperature", "lift_axis")] = 60
                op.poll()
            robot.left_bus.registers[("Present_Temperature", "lift_axis")] = 30
            clock.sleep(start + 0.466019 - clock.now)
            state.start_ticks = robot.lift._extended_ticks
            state.start_zero = robot.lift._z0_deg
            state.initial_writes = len(robot.left_bus.events)
            read = op.transport.read_group
            emit = op.emit
            sample = op.monitor.sample

            def sample_phase(phase, **kwargs):
                state.sample_phase = phase
                return sample(phase, **kwargs)

            def read_group(*args, **kwargs):
                if state.sample_phase == "live":
                    state.runtime_reads.append(clock.now)
                    if len(state.runtime_reads) == 2:
                        if refresh_fault == "delayed":
                            clock.sleep(0.04)  # Plus the ordinary 2 ms reply.
                        elif refresh_fault == "cancel":
                            raise KeyboardInterrupt
                        elif refresh_fault == "transport":
                            raise feedback.TransportRefusal("synthetic bad checksum", {"checksum_ok": False})
                        else:
                            changes = {
                                "high": ("Present_Temperature", 60), "status": ("Status", 4),
                                "current": ("Present_Current", 400), "voltage": ("Present_Voltage", 20),
                            }
                            if refresh_fault in changes:
                                register, value = changes[refresh_fault]
                                robot.left_bus.read_sequences[(register, "lift_axis")] = [value]
                else:
                    # Fresh cleanup evidence never clears the prior refusal.
                    for register, value in (("Status", 0), ("Present_Temperature", 30), ("Present_Voltage", 120)):
                        robot.left_bus.registers[(register, "lift_axis")] = value
                return read(*args, **kwargs)

            def emit_sample(record):
                if record["phase"] == "live":
                    (state.rejected if record.get("rejected") else state.records).append(dict(record))
                    state.events.append("sample_log")
                emit(record)

            op.transport.read_group = read_group
            op.monitor.sample = sample_phase
            op.emit = emit_sample
            op.monitor.emit = emit_sample

        original_action = robot.send_action
        original_observation = robot.get_observation
        original_hold = robot.hold_follower_arms
        observation_work_s = 0.1 if consumer == "both" else work_s

        def action(values):
            sent = original_action(values)
            state.actions.append(sent)
            state.events.append("action")
            if consumer in ("observation", "both") and mode != "pause":
                clock.sleep(observation_work_s * 0.6)
            return sent

        def hold():
            original_hold()
            state.events.append("hold")
            if consumer in ("observation", "both"):
                clock.sleep(observation_work_s * 0.6)

        def observation():
            if consumer in ("observation", "both"):
                clock.sleep(observation_work_s * 0.4)
            result = original_observation()
            state.observations.append(result)
            state.events.append("observation")
            return result

        def receive(flags):
            if consumer in ("action", "both"):
                clock.sleep(work_s)
            return json.dumps(command)

        def send(parts, **kwargs):
            assert parts[:2] == [b"client", b"request"]
            state.events.append("reply")

        monkeypatch.setattr(robot, "connect", connect)
        monkeypatch.setattr(robot, "send_action", action)
        monkeypatch.setattr(robot, "hold_follower_arms", hold)
        monkeypatch.setattr(robot, "get_observation", observation)
        monkeypatch.setattr(host, "AlohaMini", lambda config: robot)
        monkeypatch.setattr(host, "AM1LocalControl", lambda: control)
        monkeypatch.setattr(host.time, "perf_counter", clock.monotonic)
        monkeypatch.setattr("sys.argv", ["host", "--robot_model", "alohamini1", "--no_cameras"])
        monkeypatch.setattr(host, "AlohaMiniHost", lambda config: SimpleNamespace(
            connection_time_s=0.01, max_loop_freq_hz=30, watchdog_timeout_ms=1000,
            zmq_cmd_socket=SimpleNamespace(recv_string=receive),
            zmq_observation_socket=SimpleNamespace(
                recv_multipart=lambda **kwargs: [b"client", b"request"], send_multipart=send,
            ),
            disconnect=lambda: state.events.append("socket_closed"),
        ))
        state.run = host.main
        return state

    return run


@pytest.mark.parametrize("mode", ["active", "pause", "resume"])
def test_host_refreshes_genuine_lift_feedback_after_action_or_hold_work(host_case, mode):
    """Break: retaining only the pre-work sample kills a recoverable host tick."""
    case = host_case(mode=mode)
    case.run()
    robot, op = case.robot, case.robot._lift_operation
    assert len(case.observations) == 1
    assert case.control.state == ("paused" if mode == "pause" else "active")
    # Initial grouped read plus one actual refresh. Cleanup reads are separate.
    assert len(case.records) == 2
    assert case.records[0]["temperature_window"]["span_s"] == pytest.approx(0.468019)
    assert case.records[1]["sample_monotonic_s"] - case.records[0]["sample_monotonic_s"] == pytest.approx(0.03705)
    assert case.records[1]["temperature_window"]["span_s"] < 0.5
    assert case.events.index("reply") < case.events.index("sample_log")
    assert robot.lift._extended_ticks == case.start_ticks
    assert robot.lift._z0_deg == case.start_zero
    for bus, names in ((robot.left_bus, robot.left_arm_motors), (robot.right_bus, robot.right_arm_motors)):
        assert all(bus.registers[("Goal_Position", name)] == (10 if mode == "pause" else 13) for name in names)
        assert not bus.is_connected
    assert all(robot.left_bus.registers[("Goal_Velocity", name)] == 0 for name in robot.base_motors + ["lift_axis"])
    assert robot.left_bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert op.failure is None


@pytest.mark.parametrize("mode", ["active", "resume"])
def test_host_refreshes_before_action_if_command_work_used_the_history_margin(host_case, mode):
    """Break: refreshing observations alone still rejects a valid action consumer."""
    case = host_case(mode=mode, consumer="action")
    case.run()
    assert len(case.actions) == len(case.observations) == 1
    assert len(case.records) == 2
    assert case.robot._lift_operation.failure is None
    assert case.events.index("action") < case.events.index("observation") < case.events.index("sample_log")
    assert all(case.actions[0][key] == 0 for key in ("x.vel", "y.vel", "theta.vel", "lift_axis.vel"))
    assert not case.robot.left_bus.is_connected and not case.robot.right_bus.is_connected


@pytest.mark.parametrize("consumer", ["action", "observation"])
@pytest.mark.parametrize("work_s,expected_reads", [(0.0, 1), (0.235, 2), (0.501, 1)])
def test_consumer_refresh_is_bounded_and_never_repairs_a_raw_outage(host_case, consumer, work_s, expected_reads):
    case = host_case(consumer=consumer, work_s=work_s)
    if work_s:
        with pytest.raises(feedback.ComparisonRefusal, match="five-slot feedback window is stale") as caught:
            case.run()
        assert case.robot._lift_operation.failure is caught.value
        count = len(case.runtime_reads)
        with pytest.raises(feedback.ComparisonRefusal) as again:
            case.robot._lift_operation.apply_action({"lift_axis.vel": 0})
        assert again.value is caught.value
        assert len(case.runtime_reads) == count  # No automatic retry or latch reset.
        assert not case.observations
        if consumer == "action":
            assert not case.actions
    else:
        case.run()
        assert len(case.actions) == len(case.observations) == 1
    assert len(case.runtime_reads) == expected_reads
    assert not case.robot.left_bus.is_connected and not case.robot.right_bus.is_connected
    assert all(case.robot.left_bus.registers[("Goal_Velocity", name)] == 0
               for name in case.robot.base_motors + ["lift_axis"])
    assert case.robot.left_bus.registers[("Torque_Enable", "lift_axis")] == 0


def test_each_consumer_can_refresh_once_without_losing_prior_raw_samples(host_case):
    case = host_case(consumer="both")
    case.run()
    assert len(case.runtime_reads) == len(case.records) == 3
    assert len(case.actions) == len(case.observations) == 1
    times = [r["sample_monotonic_s"] for r in case.records]
    assert times[1] - times[0] == pytest.approx(0.03705)
    assert times[2] - times[1] == pytest.approx(0.102)
    assert all(r["temperature_window"]["span_s"] <= 0.5 for r in case.records)
    assert case.events.index("reply") < case.events.index("sample_log")
    assert not case.robot._lift_operation._pending_samples


def test_pending_raw_batch_cannot_grow_past_one_host_iteration(operating_robot):
    robot, clock = operating_robot
    robot.connect(calibrate=False)
    op = robot._lift_operation
    reads = len(op.transport.group_reads)
    for _ in range(3):
        clock.sleep(0.03)
        op.poll(defer_sample_log=True)
    with pytest.raises(feedback.ComparisonRefusal, match="pending feedback exceeds"):
        op.poll(defer_sample_log=True)
    assert len(op.transport.group_reads) - reads == 3
    assert len(op._pending_samples) == 3
    robot.disconnect()
    op.emit_pending_sample()
    assert not op._pending_samples
    assert not robot.left_bus.is_connected


@pytest.mark.parametrize("consumer", ["action", "observation"])
@pytest.mark.parametrize("fault,reason", [
    ("high", "majority"), ("status", "status"), ("transport", "bad checksum"),
    ("delayed", "40 ms"), ("current", "current"), ("voltage", "voltage"), ("cancel", None),
])
def test_refresh_keeps_high_history_faults_and_cancellation_terminal(host_case, consumer, fault, reason):
    case = host_case(consumer=consumer, refresh_fault=fault)
    if fault == "cancel":
        case.run()  # Existing host KeyboardInterrupt path still zeros/off/closes.
        assert isinstance(case.robot._lift_operation.failure, KeyboardInterrupt)
    else:
        with pytest.raises(feedback.ComparisonRefusal, match=reason) as caught:
            case.run()
        assert case.robot._lift_operation.failure is caught.value
        assert len(case.rejected) == 1
    assert len(case.runtime_reads) == 2
    assert len(case.records) == 1  # Earlier genuine sample survives the rejected refresh.
    assert not case.observations
    if consumer == "action":
        assert not case.actions
        assert all(case.robot.left_bus.registers[("Goal_Position", name)] == 10
                   for name in case.robot.left_arm_motors)
    if fault == "high":
        assert [t for _, t in case.rejected[0]["temperature_history"]][-3:] == [60, 60, 60]
        assert case.records[0]["temperature_window"]["high_count"] == 2
    # Post-fault raw logging does not delay cleanup or relabel its failure.
    assert case.events.index("socket_closed") < len(case.events) - 1
    assert case.events[-1] == "sample_log"
    assert not case.robot.left_bus.is_connected and not case.robot.right_bus.is_connected
    assert all(case.robot.left_bus.registers[("Goal_Velocity", name)] == 0
               for name in case.robot.base_motors + ["lift_axis"])
    assert case.robot.left_bus.registers[("Torque_Enable", "lift_axis")] == 0
