"""Verified zero/off cleanup rebuilds genuine thermal slots; Live stays strict."""

from __future__ import annotations

import copy

import pytest

from lerobot.robots.alohamini import lift_motor_feedback as feedback
from lerobot.robots.alohamini.lift_operational import TemperatureWindow
from tests.robots import test_alohamini_lift_operational as operational_tests

operating_robot = operational_tests.operating_robot

ORIGIN = 73224.855329437
LAST_LIVE = 73325.136873374
FIRST_CLEANUP = 73325.256883903
SECOND_CLEANUP = 73325.308908070
PEAKS = (73324.768856710, 73324.869871402, 73324.969892521, 73325.069872493)


@pytest.fixture
def cleanup_robot(operating_robot):
    yield operating_robot
    robot, _ = operating_robot
    # This shared rig has only fake buses. Close them during captured teardown,
    # including expected latched refusals, rather than running a late destructor.
    if robot.is_connected:
        try:
            robot.disconnect()
        except RuntimeError:
            assert not robot.left_bus.is_connected
            assert robot.right_bus is None or not robot.right_bus.is_connected


def recorded_window(offset=0.0, *, retained_highs=False):
    window = TemperatureWindow()
    # Synthetic earlier priming establishes the real origin through production
    # update(), then the exact captured timing tail determines the failing edge.
    for index in range(999):
        window.update(35, ORIGIN + offset + index * 0.1)
    for index, stamp in enumerate(PEAKS):
        window.update(60 if retained_highs and index >= 2 else 35, stamp + offset)
    window.update(35, LAST_LIVE + offset)
    window.outliers = 3  # Original trace's raw counter must survive cloning.
    return window


def state(window):
    return {
        "samples": list(window.samples),
        "origin": window._origin_ns,
        "slot": window._last_slot,
        "latest": window._last_sample_at,
        "outliers": window.outliers,
        "failure": window.failure,
    }


def cleanup_rig(cleanup_robot, *, gap=0.118010529, retained_highs=False):
    robot, clock = cleanup_robot
    robot.connect(calibrate=False)
    op = robot._lift_operation
    op.temperature = recorded_window(clock.now - LAST_LIVE, retained_highs=retained_highs)
    original = op.temperature
    before = state(original)
    # These are the explicit already-zero/off entry preconditions of the actual
    # caller. Real grouped decode still verifies both on every cleanup sample.
    robot.left_bus.registers[("Goal_Velocity", "lift_axis")] = 0
    robot.left_bus.registers[("Torque_Enable", "lift_axis")] = 0
    robot.left_bus.registers[("Present_Temperature", "lift_axis")] = 35
    emitted = []
    op.monitor.emit = lambda row: emitted.append(copy.deepcopy(row))
    clock.sleep(gap)
    return robot, clock, op, original, before, emitted


def samples(emitted):
    return [row for row in emitted if row["phase"] == "cleanup_readback" and "sample_monotonic_s" in row]


def test_default_powered_live_replays_the_original_cleanup_gap_refusal():
    window = recorded_window()
    first = window.update(35, FIRST_CLEANUP)
    assert first["span_s"] == pytest.approx(0.488027, abs=1e-6)
    with pytest.raises(feedback.ComparisonRefusal, match="five-slot.*stale"):
        window.update(35, SECOND_CLEANUP)
    assert window.failure is not None
    assert SECOND_CLEANUP - window.samples[0][0] == pytest.approx(0.540051360)


def test_cleanup_expiration_drops_only_genuinely_expired_peaks_and_requires_new_slot():
    window = recorded_window()
    window.update(35, FIRST_CLEANUP, expire_old_slots=True)
    retained = list(window.samples)
    result = window.update(35, SECOND_CLEANUP, expire_old_slots=True)
    assert list(window.samples) == retained[1:-1] + [(SECOND_CLEANUP, 35)]
    assert result["count"] == 4 and result["ready"] is False
    assert window._origin_ns == round(ORIGIN * 1e9)
    assert window._last_sample_at == SECOND_CLEANUP and window.outliers == 3
    assert window.samples[-1][0] == SECOND_CLEANUP  # Equal maximum has a new receipt, still one slot vote.
    fresh = window.update(35, SECOND_CLEANUP + 0.052, expire_old_slots=True)
    assert fresh["count"] == 5 and fresh["ready"]
    assert fresh["span_s"] <= 0.5


def test_expiration_keeps_an_exactly_half_second_old_peak():
    window = TemperatureWindow()
    for stamp in (0.0, 0.2, 0.3, 0.4, 0.45):
        window.update(35, stamp)
    window.update(35, 0.5, expire_old_slots=True)
    assert window.failure is None
    assert window.samples[0] == (0.0, 35)
    assert all(0.5 - stamp <= 0.5 for stamp, _ in window.samples)


@pytest.mark.parametrize("stamp", [LAST_LIVE, LAST_LIVE - 0.001])
def test_cleanup_expiration_does_not_accept_replayed_or_backward_raw_reply(stamp):
    window = recorded_window()
    with pytest.raises(feedback.ComparisonRefusal, match="timestamp"):
        window.update(35, stamp, expire_old_slots=True)


def test_cleanup_forward_gap_refills_five_real_slots_without_extending_collection_budget():
    window = recorded_window()
    origin, outliers = window._origin_ns, window.outliers
    first = LAST_LIVE + 0.501
    receipts = [first+index*.101 for index in range(5)]
    for index, stamp in enumerate(receipts):
        result = window.update(35, stamp, expire_old_slots=True)
        assert result["count"] == index+1 and result["ready"] is (index == 4)
    assert {stamp for stamp, _ in window.samples} == set(receipts)
    assert len({(round(stamp*1e9)-origin)//round(.1*1e9) for stamp in receipts}) == 5
    assert receipts[-1]-receipts[0] <= 1.0
    assert window._origin_ns == origin and window.outliers == outliers


def test_operational_cleanup_exact_gap_uses_clone_and_leaves_live_history_unchanged(cleanup_robot):
    robot, clock, op, original, before, emitted = cleanup_rig(cleanup_robot)
    began = clock.now
    op.cleanup_readback()
    rows = samples(emitted)
    assert len(rows) >= 5
    assert any(not row["temperature_window"]["ready"] for row in rows)
    assert rows[-1]["temperature_window"]["ready"]
    assert rows[-1]["temperature_window"]["count"] == 5
    assert rows[-1]["temperature_window"]["span_s"] <= 0.5
    assert all(row["torque_enable"] == row["goal_velocity_raw"] == 0 for row in rows)
    assert all(row["temperature_c"] == 35 for row in rows)
    assert clock.now - began <= 1.0
    assert op.temperature is original and state(original) == before
    assert op.failure is None and not robot.lift.is_homed


def test_missing_occupied_slots_wait_for_five_genuine_new_cleanup_slots(cleanup_robot):
    _, clock, op, original, before, emitted = cleanup_rig(cleanup_robot, gap=0.49)
    began = clock.now
    op.cleanup_readback()
    rows = samples(emitted)
    assert rows[0]["temperature_window"]["count"] == 2  # LAST_LIVE is still fresh beside the new slot.
    assert rows[0]["temperature_window"]["ready"] is False
    assert rows[-1]["temperature_window"]["count"] == 5
    assert rows[-1]["temperature_window"]["ready"]
    slots = {(round(row["sample_monotonic_s"] * 1e9) - before["origin"]) // round(0.1 * 1e9) for row in rows}
    assert len(slots) == 5  # First/last partial slots need not span a full 0.4 s.
    assert feedback.STATIONARY_WINDOW_S < clock.now - began <= 1.0
    assert state(original) == before


def test_retained_high_slots_plus_new_cleanup_high_still_confirm_majority(cleanup_robot):
    robot, _, op, original, before, emitted = cleanup_rig(cleanup_robot, retained_highs=True)
    robot.left_bus.registers[("Present_Temperature", "lift_axis")] = 60
    with pytest.raises(feedback.ComparisonRefusal, match="3/5"):
        op.cleanup_readback()
    rejected = [row for row in samples(emitted) if row.get("rejected")]
    assert rejected and rejected[-1]["temperature_c"] == 60
    assert state(original) == before  # No shared-history mutation or cleared high.


@pytest.mark.parametrize(
    "register,value,reason",
    [
        ("Present_Current", 308, "current"),
        ("Status", 4, "status"),
        ("Operating_Mode", 0, "mode"),
        ("Torque_Enable", 1, "torque"),
        ("Goal_Velocity", 100, "goal velocity"),
        ("Present_Voltage", 15, "voltage"),
        ("Present_Current", RuntimeError("modeled reply failure"), "grouped feedback"),
    ],
)
def test_hard_zero_off_transport_guards_precede_cleanup_temperature_gate(
    cleanup_robot, register, value, reason
):
    robot, _, op, original, before, emitted = cleanup_rig(cleanup_robot)
    robot.left_bus.read_sequences[(register, "lift_axis")] = [value]
    with pytest.raises(feedback.ComparisonRefusal, match=reason):
        op.cleanup_readback()
    rows = samples(emitted)
    assert not any("temperature_window" in row for row in rows)
    assert state(original) == before


def test_existing_primary_and_thermal_faults_remain_latched_after_stopped_readback(cleanup_robot):
    _, _, op, original, before, _ = cleanup_rig(cleanup_robot)
    primary = feedback.ComparisonRefusal("original confirmed thermal fault")
    op.failure = original.failure = primary
    before["failure"] = primary
    op.cleanup_readback()
    assert op.failure is primary and original.failure is primary
    assert op.temperature is original and state(original) == before


def test_cleanup_temperature_ready_false_expires_at_original_one_second(cleanup_robot):
    _, clock, op, _, _, _ = cleanup_rig(cleanup_robot)
    op.monitor.temperature_check = None  # Isolate qualifier gate/deadline from thermal algorithm.
    began = clock.now
    with pytest.raises(feedback.ComparisonRefusal, match="stationary|1.second"):
        op.monitor.qualify_stationary(
            "cleanup_readback",
            expected_torque=0,
            expected_goal=0,
            allow_settling=True,
            timeout_s=1.0,
            cleanup_temperature_ready=lambda *_: False,
        )
    assert clock.now - began <= 1.0 + 1e-9


@pytest.mark.parametrize(
    "phase,torque,goal,timeout",
    [
        ("live", 0, 0, 1.0),
        ("post_home", 0, 0, 1.0),
        ("cleanup_readback", 1, 0, 1.0),
        ("cleanup_readback", 0, 1, 1.0),
        ("cleanup_readback", 0, 0, 3.0),
    ],
)
def test_cleanup_readiness_callback_refuses_any_powered_or_other_phase_scope(
    cleanup_robot, phase, torque, goal, timeout
):
    _, _, op, _, _, emitted = cleanup_rig(cleanup_robot)
    with pytest.raises(ValueError, match="[Cc]leanup"):
        op.monitor.qualify_stationary(
            phase,
            expected_torque=torque,
            expected_goal=goal,
            allow_settling=True,
            timeout_s=timeout,
            cleanup_temperature_ready=lambda *_: True,
        )
    assert not samples(emitted)


def test_cleanup_still_rejects_grouped_reply_over_original_40_ms_budget(cleanup_robot, monkeypatch):
    _, clock, op, original, before, emitted = cleanup_rig(cleanup_robot)
    read_group = op.transport.read_group

    def delayed_read(*args, **kwargs):
        assert kwargs["timeout_ms"] == 40.0  # Ordinary OperationalLift request bound.
        clock.sleep(0.041)
        return read_group(*args, **kwargs)

    monkeypatch.setattr(op.transport, "read_group", delayed_read)
    with pytest.raises(feedback.ComparisonRefusal, match="40 ms"):
        op.cleanup_readback()
    assert not any("temperature_window" in row for row in samples(emitted))
    assert state(original) == before


@pytest.mark.parametrize("register", ["Present_Position", "Present_Velocity"])
def test_thermal_readiness_does_not_expand_original_stationary_motion_bound(cleanup_robot, register):
    robot, clock, op, original, before, _ = cleanup_rig(cleanup_robot)
    if register == "Present_Position":
        origin = round(robot.left_bus.position)
        values = [origin, origin + 2] * 30
    else:
        values = [100] * 60
    robot.left_bus.read_sequences[(register, "lift_axis")] = values
    began = clock.now
    with pytest.raises(feedback.ComparisonRefusal, match="stationary"):
        op.cleanup_readback()
    assert clock.now - began <= 1.0 + op.monitor.sample_timeout_ms / 1000
    assert state(original) == before
