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
def test_alternating_post_home_flags_stop_on_original_deadline_without_relief(
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
        with pytest.raises(RuntimeError, match="AlohaMini motor activation failed") as failure:
            robot.connect(calibrate=False)
        primary = failure.value.__cause__
        assert primary is robot._lift_operation.failure
        assert "post_home" in str(primary) and "stationary" in str(primary) and "1" in str(primary)
        records = operational_records(capsys)
        samples = [row for row in records if row["phase"] == "post_home" and not row.get("rejected")]
        rejected = next(row for row in records if row["phase"] == "post_home" and row.get("rejected"))
        # A single original deadline governs all candidate resets. One already
        # started grouped read retains its unchanged 40 ms request allowance.
        assert 0.95 <= rejected["sample_monotonic_s"] - samples[0]["sample_monotonic_s"] <= 1.04
        assert len(samples) >= 5
        assert [(row["present_velocity_raw"], row["moving"]) for row in samples] == [
            flags[index % len(flags)] for index in range(len(samples))
        ]
        assert len({row["sample_monotonic_s"] for row in samples}) == len(samples)
        assert all(row["goal_velocity_raw"] == 0 and row["torque_enable"] == 1 for row in samples)
        assert not commands
        assert not any(
            row["phase"] in ("post_home_stationary_qualified", "relief", "operational_ready")
            for row in records
        )
        assert any(row["phase"] == "shutdown_verified" for row in records)
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
