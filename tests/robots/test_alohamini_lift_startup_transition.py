"""Conservative normal lift transition through real startup and external grouped fakes."""

from __future__ import annotations

import pytest

from lerobot.robots.alohamini import alohamini as robot_module, lift_relief
from lerobot.robots.alohamini.config_alohamini import AlohaMiniConfig
from tests.robots.test_alohamini_lift_operational import (
    operating_robot as _operating_robot,
    operational_records,
)
from tests.robots.test_alohamini_lift_relief import reports, rig as _rig

operating_robot = _operating_robot
rig = _rig


MIXED_FLAGS = [(0, 0), (0, 0), (-50, 1), (-50, 1), (0, 0)]


def model_post_home_feedback(bus, clock, operation, flags, *, repeat=False):
    """Only the external grouped replies and command recording are synthetic."""
    samples = []
    upward_commands = []
    original_write = bus.write

    def write(register, motor, value, **kwargs):
        if (register, motor, int(value)) == ("Goal_Velocity", "lift_axis", -200):
            upward_commands.append(clock.now)
        return original_write(register, motor, value, **kwargs)

    def hook(register):
        check = operation()
        if (
            check is None
            or not check.lift.is_homed
            or check.reader.phase != "homing"
            or bus.registers[("Goal_Velocity", "lift_axis")] != 0
        ):
            return
        if register == "Present_Position":
            assert len(samples) < 30, "External feed exhausted: original stationary deadline did not stop"
            index = len(samples)
            velocity, moving = (
                flags[index % len(flags)] if repeat else (flags[index] if index < len(flags) else (0, 0))
            )
            samples.append({"at": clock.now, "velocity": velocity, "moving": moving})
        elif register == "Present_Velocity":
            sample = samples[-1]
            bus.read_sequences[(register, "lift_axis")] = [sample["velocity"]]
            # Normal fixture transports this independent raw Moving bit in the
            # actual grouped payload; legacy derives the same bit for MIXED_FLAGS.
            bus.read_sequences[("Moving", "lift_axis")] = [sample["moving"]]

    bus.write, bus.hook = write, hook
    return samples, upward_commands


def stopped(bus):
    assert bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert not bus.is_connected


def test_normal_start_keeps_zero_until_five_fresh_quiet_post_home_samples(operating_robot, capsys):
    robot, clock = operating_robot
    bus = robot.left_bus
    _, commands = model_post_home_feedback(
        bus,
        clock,
        lambda: getattr(robot, "_lift_operation", None),
        MIXED_FLAGS,
    )
    try:
        robot.connect(calibrate=False)
        records = operational_records(capsys)
        assert len(commands) == 1
        post_home = [row for row in records if row["phase"] == "post_home"]
        before_upward = [row for row in post_home if row["sample_monotonic_s"] <= commands[0]]
        assert [(row["present_velocity_raw"], row["moving"]) for row in before_upward[:5]] == MIXED_FLAGS
        tail = before_upward[-5:]
        assert len(tail) == 5 and all(
            abs(row["present_velocity_raw"]) <= 5 and row["moving"] == 0 for row in tail
        ), "Upward goal was sent before five genuinely quiet post-home replies"
        assert tail[-1]["sample_monotonic_s"] - tail[0]["sample_monotonic_s"] >= 0.2
        assert len({row["sample_monotonic_s"] for row in tail}) == 5
        assert all(row["goal_velocity_raw"] == 0 and row["torque_enable"] == 1 for row in before_upward)
        assert (
            len(before_upward) == 9
        )  # Two flagged samples reset the candidate; five fresh quiet replies follow.
        qualified = next(row for row in records if row["phase"] == "post_home_stationary_qualified")
        assert qualified["sample_count"] == 5 and qualified["window_s"] >= 0.2
        assert qualified["present_velocity_raw"] == qualified["moving"] == [0] * 5
        assert commands[0] >= tail[-1]["sample_monotonic_s"]
        assert any(row["phase"] == "operational_ready" for row in records)
        assert robot.lift._z0_deg == pytest.approx(-100 * 360 / 4096)  # Existing synthetic home zero.
        writes = [event[2:] for event in bus.events if event[1] == "write"]
        assert writes.count(("Goal_Velocity", "lift_axis", 200)) == 1
        assert writes.count(("Goal_Velocity", "lift_axis", -200)) == 1
    finally:
        if bus.is_connected:
            robot.disconnect()
    stopped(bus)


@pytest.mark.parametrize("flags", [[(0, 0), (50, 1)], [(0, 0), (0, 1)]], ids=["velocity", "moving"])
def test_alternating_post_home_flags_use_bounded_am1_deadline_before_relief(
    operating_robot,
    capsys,
    flags,
):
    robot, clock = operating_robot
    bus = robot.left_bus
    _, commands = model_post_home_feedback(
        bus,
        clock,
        lambda: getattr(robot, "_lift_operation", None),
        flags,
        repeat=True,
    )
    try:
        robot.connect(calibrate=False)
        records = operational_records(capsys)
        samples = [row for row in records if row["phase"] == "post_home" and not row.get("rejected")]
        qualified = next(row for row in records if row["phase"] == "post_home_stationary_qualified")
        # Strict quiet retains the entire original opportunity. Only the
        # explicitly identified AM1 alternative may proceed at that deadline.
        assert 0.95 <= commands[0] - samples[0]["sample_monotonic_s"] <= 1.04
        assert qualified["readiness_branch"] == "bounded_indicator"
        assert qualified["motion_feedback_quiet"] is False
        assert qualified["whole_phase_position_excursion_raw"] == 0
        assert len(samples) >= 5
        assert [(row["present_velocity_raw"], row["moving"]) for row in samples] == [
            flags[index % len(flags)] for index in range(len(samples))
        ]
        assert len({row["sample_monotonic_s"] for row in samples}) == len(samples)
        assert all(row["goal_velocity_raw"] == 0 and row["torque_enable"] == 1 for row in samples)
        assert len(commands) == 1
        assert any(row["phase"] == "operational_ready" for row in records)
    finally:
        if bus.is_connected:
            robot.disconnect()
    stopped(bus)


def test_quiet_post_home_prefix_preserves_two_wrong_sign_relief_refusal(operating_robot, capsys):
    robot, clock = operating_robot
    bus = robot.left_bus
    bus.up_factor = 0  # External mechanics have no actual upward encoder progress.
    _, commands = model_post_home_feedback(
        bus,
        clock,
        lambda: getattr(robot, "_lift_operation", None),
        [(0, 0)],
        repeat=True,
    )
    original_hook = bus.hook

    def hook(register):
        original_hook(register)
        operation = getattr(robot, "_lift_operation", None)
        if (
            operation is not None
            and operation.reader.phase == "relief"
            and bus.registers[("Goal_Velocity", "lift_axis")] == -200
            and register == "Present_Velocity"
        ):
            bus.read_sequences[(register, "lift_axis")] = [50]

    bus.hook = hook
    with pytest.raises(RuntimeError, match="AlohaMini motor activation failed") as failure:
        robot.connect(calibrate=False)
    assert failure.value.__cause__ is robot._lift_operation.failure
    assert str(failure.value.__cause__) == "relief: repeated velocity/position direction disagreement."
    records = operational_records(capsys)
    quiet = next(row for row in records if row["phase"] == "post_home_stationary_qualified")
    assert quiet["present_velocity_raw"] == quiet["moving"] == [0] * 5
    assert quiet["window_s"] >= 0.2 and len(commands) == 1
    relief = [row for row in records if row["phase"] == "relief" and not row.get("rejected")]
    assert len(relief) == 2
    assert all(row["goal_velocity_raw"] == -200 and row["present_velocity_raw"] == 50 for row in relief)
    assert relief[0]["present_position_raw"] == relief[1]["present_position_raw"]
    assert relief[0]["sample_monotonic_s"] < relief[1]["sample_monotonic_s"]
    assert not any(row["phase"] in ("relief_direction_qualified", "operational_ready") for row in records)
    assert any(row["phase"] == "shutdown_verified" for row in records)
    stopped(bus)


def test_legacy_installed_comparison_keeps_permissive_stationary_default(rig, tmp_path, capsys):
    clock, _, _ = rig
    robot = robot_module.AlohaMini(
        AlohaMiniConfig(
            robot_model="alohamini1",
            no_follower=True,
            diagnostic_lift_only=True,
            cameras={},
            calibration_dir=tmp_path,
        )
    )
    bus = robot.left_bus
    bus.connect(handshake=False)
    check = lift_relief.InstalledLiftCheck(robot, input_fn=lambda _: "RELIEF")
    _, commands = model_post_home_feedback(bus, clock, lambda: check, MIXED_FLAGS)
    try:
        check.compare()
        records = reports(capsys)
        qualified = next(row for row in records if row["phase"] == "post_home_stationary_qualified")
        assert qualified["sample_count"] >= 4
        assert qualified["present_velocity_raw"][:4] == [0, 0, -50, -50]
        assert qualified["moving"][:4] == [0, 0, 1, 1]
        assert qualified["window_s"] >= 0.15
        assert len(commands) == 1 and any(row["phase"] == "rest_complete" for row in records)
        assert check.lift._z0_deg == pytest.approx(-100 * 360 / 4096)
    finally:
        assert not robot._safe_shutdown(close_buses=True, motor_shutdown_check=check.cleanup_readback)
    stopped(bus)



def model_final_setup_off(monkeypatch, robot, clock, *, torque=(1, 0), fault=None, write_effect=None):
    """Vary only external grouped replies/write timing during the real final guard."""
    from lerobot.robots.alohamini import lift_motor_feedback as feedback

    bus = robot.left_bus
    original_guard = lift_relief.InstalledLiftCheck.home_guard
    original_sample = feedback.MotorFeedbackComparison.sample
    original_write = bus.write
    state = {"active": False, "samples": [], "writes": [], "index": 0}

    def guard(check, phase, displacement):
        state["active"] = phase == "before_torque"
        original_clock = check.monitor.monotonic
        try:
            return original_guard(check, phase, displacement)
        finally:
            check.monitor.monotonic = original_clock
            state["active"] = False

    def sample(monitor, phase, **kwargs):
        final = state["active"]
        if final:
            index = state["index"]
            state["index"] += 1
            bus.read_sequences[("Torque_Enable", "lift_axis")] = [torque[min(index, len(torque) - 1)]]
            bus.read_sequences[("Present_Current", "lift_axis")] = [1]
            if fault is not None:
                fault(index, phase, monitor)
        result = original_sample(monitor, phase, **kwargs)
        if final:
            state["samples"].append(result)
        return result

    def write(register, motor, value, **kwargs):
        state["writes"].append((clock.now, state["active"], register, motor, value))
        if state["active"] and write_effect is not None:
            write_effect(register, motor, value)
        return original_write(register, motor, value, **kwargs)

    monkeypatch.setattr(lift_relief.InstalledLiftCheck, "home_guard", guard)
    monkeypatch.setattr(feedback.MotorFeedbackComparison, "sample", sample)
    monkeypatch.setattr(bus, "write", write)
    return state


@pytest.mark.parametrize("torque", [(1, 0), (1, 1, 0)])
def test_final_setup_off_acquires_actual_zero_then_nine_fresh_samples_before_home(
    operating_robot, monkeypatch, capsys, torque,
):
    robot, clock = operating_robot
    state = model_final_setup_off(monkeypatch, robot, clock, torque=torque)
    try:
        robot.connect(calibrate=False)
        records = operational_records(capsys)
        started = next(i for i, row in enumerate(records) if row["phase"] == "before_torque_off_acquisition_started")
        completed = next(i for i, row in enumerate(records) if row["phase"] == "before_torque_off_acquisition_completed")
        final = records[started + 1:completed]
        acquisition = [row for row in final if row["phase"] == "startup_off_acquisition"]
        baseline = [row for row in final if row["phase"] == "baseline"]
        assert [row["torque_enable"] for row in acquisition] == list(torque)
        assert len(baseline) == 9 and all(row["torque_enable"] == 0 for row in baseline)
        assert baseline[0]["sample_monotonic_s"] > acquisition[-1]["sample_monotonic_s"]
        assert baseline[-1]["sample_monotonic_s"] - baseline[0]["sample_monotonic_s"] >= 0.4
        request = next(row for row in final if row["phase"] == "startup_off_request_started")
        deadline = request["absolute_deadline_monotonic_s"]
        assert deadline - request["requested_at_monotonic_s"] == pytest.approx(0.6)
        assert baseline[-1]["sample_monotonic_s"] <= deadline
        assert all(row["sample_monotonic_s"] <= deadline for row in state["samples"])
        assert baseline[-1]["temperature_window"]["count"] == 5
        assert robot._lift_operation.monitor.min_stationary_samples == 5
        # Initial and final evidence remain separately chronological; the same
        # temperature owner/history survives the second bounded acquisition.
        initial = [row for row in records[:started] if row["phase"] == "baseline"]
        assert len(initial) == 9 and all(row["torque_enable"] == 0 for row in initial)
        assert baseline[0]["sample_monotonic_s"] > initial[-1]["sample_monotonic_s"]
        final_writes = [(reg, motor, value) for _, active, reg, motor, value in state["writes"] if active]
        assert final_writes == [("Goal_Velocity", "lift_axis", 0), ("Torque_Enable", "lift_axis", 0)]
        energized = [at for at, _, reg, motor, value in state["writes"]
                     if motor == "lift_axis" and ((reg == "Torque_Enable" and value == 1)
                                                  or (reg == "Goal_Velocity" and value != 0))]
        assert energized and min(energized) >= baseline[-1]["sample_monotonic_s"]
        assert any(row["phase"] == "operational_ready" for row in records)
    finally:
        if robot.left_bus.is_connected:
            robot.disconnect()
    stopped(robot.left_bus)


def assert_final_setup_refused(robot, failure, records):
    assert failure.value.__cause__ is robot._lift_operation.failure
    assert not any(row["phase"] in ("before_torque_off_acquisition_completed", "operational_ready") for row in records)
    assert not any(event[1] == "write" and event[3] == "lift_axis"
                   and ((event[2] == "Torque_Enable" and event[4] == 1)
                        or (event[2] == "Goal_Velocity" and event[4] != 0))
                   for event in robot.left_bus.events if len(event) == 5)
    stopped(robot.left_bus)


@pytest.mark.parametrize("torque", [(1,), (1, 1, 1, 1, 0), (0, 0, 1)])
def test_final_setup_off_stuck_late_or_reappearing_on_never_authorizes_home(
    operating_robot, monkeypatch, capsys, torque,
):
    robot, clock = operating_robot
    model_final_setup_off(monkeypatch, robot, clock, torque=torque)
    with pytest.raises(RuntimeError, match="AlohaMini motor activation failed") as failure:
        robot.connect(calibrate=False)
    records = operational_records(capsys)
    assert any(row["phase"] == "before_torque_off_acquisition_started" for row in records)
    assert_final_setup_refused(robot, failure, records)


@pytest.mark.parametrize("register,value", [
    ("Torque_Enable", 2), ("Status", 8), ("Operating_Mode", 0),
    ("Present_Voltage", 0), ("Present_Current", 31), ("Goal_Velocity", 1),
    ("Present_Velocity", 51), ("Moving", 2),
])
def test_final_setup_off_keeps_hard_grouped_feedback_refusals(
    operating_robot, monkeypatch, capsys, register, value,
):
    robot, clock = operating_robot

    def fault(index, phase, monitor):
        if index == 0:
            robot.left_bus.read_sequences[(register, "lift_axis")] = [value]

    model_final_setup_off(monkeypatch, robot, clock, fault=fault)
    with pytest.raises(RuntimeError, match="AlohaMini motor activation failed") as failure:
        robot.connect(calibrate=False)
    records = operational_records(capsys)
    assert any(row.get("rejected") for row in records)
    assert_final_setup_refused(robot, failure, records)


def test_final_setup_off_keeps_whole_phase_position_anchor(operating_robot, monkeypatch, capsys):
    robot, clock = operating_robot

    def fault(index, phase, monitor):
        if index in (1, 2):
            robot.left_bus.position += 1

    model_final_setup_off(monkeypatch, robot, clock, fault=fault)
    with pytest.raises(RuntimeError, match="AlohaMini motor activation failed") as failure:
        robot.connect(calibrate=False)
    records = operational_records(capsys)
    assert any(row.get("rejected") and "position" in row["rejection_reason"] for row in records)
    assert_final_setup_refused(robot, failure, records)


def test_final_setup_off_helper_time_consumes_one_original_deadline(operating_robot, monkeypatch, capsys):
    robot, clock = operating_robot

    def delay(register, motor, value):
        if register == "Goal_Velocity":
            clock.sleep(0.12)

    model_final_setup_off(monkeypatch, robot, clock, torque=(1, 0), write_effect=delay)
    with pytest.raises(RuntimeError, match="AlohaMini motor activation failed") as failure:
        robot.connect(calibrate=False)
    records = operational_records(capsys)
    started = next(i for i, row in enumerate(records) if row["phase"] == "before_torque_off_acquisition_started")
    final = records[started + 1:]
    request = next(row for row in final if row["phase"] == "startup_off_request_started")
    deadline = request["absolute_deadline_monotonic_s"]
    samples = [row for row in final if row["phase"] in ("startup_off_acquisition", "baseline") and not row.get("rejected")]
    assert samples and all(row["sample_monotonic_s"] <= deadline for row in samples)
    assert deadline - request["requested_at_monotonic_s"] == pytest.approx(0.6)
    assert "deadline" in str(failure.value.__cause__)
    assert_final_setup_refused(robot, failure, records)


def test_final_setup_off_helper_first_cause_survives_and_no_outer_retry(operating_robot, monkeypatch, capsys):
    from lerobot.robots.alohamini import lift_operational

    robot, clock = operating_robot
    state = model_final_setup_off(monkeypatch, robot, clock)
    original = lift_operational.write_register
    cause = RuntimeError("synthetic final off helper failed")
    attempts = []

    def fail(bus, register, motor, value):
        if state["active"] and register == "Torque_Enable":
            attempts.append((register, value))
            raise cause
        return original(bus, register, motor, value)

    monkeypatch.setattr(lift_operational, "write_register", fail)
    with pytest.raises(RuntimeError, match="AlohaMini motor activation failed") as failure:
        robot.connect(calibrate=False)
    records = operational_records(capsys)
    assert failure.value.__cause__ is cause
    assert attempts == [("Torque_Enable", 0)]
    assert any(row["phase"] == "startup_off_write_helper_failed" for row in records)
    assert_final_setup_refused(robot, failure, records)



@pytest.mark.parametrize("kind", ["stale_timestamp", "slow_group_reply"])
def test_final_setup_off_does_not_freshen_stale_or_slow_feedback(
    operating_robot, monkeypatch, capsys, kind,
):
    robot, clock = operating_robot
    def fault(index, phase, monitor):
        if kind == "stale_timestamp" and index == 0:
            monitor.monotonic = lambda: clock.now - 0.1

    state = model_final_setup_off(monkeypatch, robot, clock, fault=fault)
    delayed = []

    def hook(register):
        if kind == "slow_group_reply" and state["active"] and not delayed and register == "Present_Position":
            delayed.append(clock.now)
            clock.sleep(0.041)

    robot.left_bus.hook = hook
    with pytest.raises(RuntimeError, match="AlohaMini motor activation failed") as failure:
        robot.connect(calibrate=False)
    records = operational_records(capsys)
    cause = str(failure.value.__cause__)
    assert ("timestamp" in cause) if kind == "stale_timestamp" else ("40 ms" in cause)
    assert_final_setup_refused(robot, failure, records)
