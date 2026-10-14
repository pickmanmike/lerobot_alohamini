"""Exact native173b slot timing with real production lift/thermal guards and fake owner I/O."""

from __future__ import annotations

import math

import pytest

from lerobot.robots.alohamini import lift_motor_feedback as feedback
from tests.robots.test_alohamini_lift_operational import operating_robot as _operating_robot
from tests.robots.test_am1_startup_thermal_handoff import at

operating_robot = _operating_robot


@pytest.fixture
def native173b_slots(operating_robot):
    robot, clock = operating_robot
    robot.connect(calibrate=False)
    operation = robot._lift_operation
    window = operation.temperature
    origin = window._origin_ns / 1e9
    start = origin + math.ceil((clock.now - origin) / 0.1) * 0.1
    # Exact relative actual peaks 173831.439692190..858685799 and later lower
    # 173831.912692596. Real production grouped fake reads take2ms; no physical
    # measurement is invented, and original already-running Window is retained.
    robot.left_bus.registers[("Present_Temperature", "lift_axis")] = 35
    for offset in (0.020346665, 0.122749302, 0.229352434, 0.333356198, 0.439340274):
        at(clock, start + offset - 0.002)
        operation.poll()
    robot.left_bus.registers[("Present_Temperature", "lift_axis")] = 34
    at(clock, start + 0.493347071 - 0.002)
    operation.poll()
    assert operation.last_record["sample_monotonic_s"] == pytest.approx(start + 0.493347071, abs=1e-9)
    assert list(window.samples) == pytest.approx(
        [(start + x, 35) for x in (0.020346665, 0.122749302, 0.229352434, 0.333356198, 0.439340274)]
    )
    try:
        yield robot, clock, start
    finally:
        for bus in (robot.left_bus, robot.right_bus):
            if bus is not None:
                bus.is_connected = False


def test_exact_new_slot_reacquired_before_enable_with_raw_poll_not_yet_due(native173b_slots):
    robot, clock, start = native173b_slots
    operation = robot._lift_operation
    window = operation.temperature
    origin = window._origin_ns
    old_record = operation.last_record
    old_peaks = list(window.samples)
    at(clock, start + 0.525)
    assert clock.now - old_record["sample_monotonic_s"] < feedback.POLL_S
    assert clock.now - old_peaks[0][0] > window.MAX_AGE_S
    robot._require_am1_activation_fresh("after_home")
    assert operation.last_record is not old_record
    assert operation.last_record["sample_monotonic_s"] == pytest.approx(start + 0.527, abs=1e-9)
    assert operation.temperature is window and window._origin_ns == origin
    assert list(window.samples)[:-1] == old_peaks[1:]
    assert window.samples[-1][1] == 34 and all(clock.now - t <= 0.5 for t, _ in window.samples)
    assert window.failure is None and operation.failure is None
    assert window.MAX_AGE_S == 0.5 and window.CEILING_C == 55 and window.SIZE == 5


def test_same_slot_lower_reply_retains_peak_and_no_extra_startup_read(native173b_slots, monkeypatch):
    robot, clock, start = native173b_slots
    operation = robot._lift_operation
    old = operation.last_record
    peaks = list(operation.temperature.samples)
    at(clock, start + 0.498)
    monkeypatch.setattr(
        operation.transport, "read_group", lambda *a, **k: pytest.fail("same slot and raw notdue")
    )
    robot._refresh_am1_startup_lift_feedback()
    assert operation.last_record is old and list(operation.temperature.samples) == peaks
    assert peaks[-1] == pytest.approx((start + 0.439340274, 35))


@pytest.mark.parametrize("fault", ["status", "current", "reply_budget", "cancel"])
def test_new_slot_due_retains_existing_full_poll_hard_faults(native173b_slots, monkeypatch, fault):
    robot, clock, start = native173b_slots
    operation = robot._lift_operation
    original = operation.transport.read_group

    def read(*args, **kwargs):
        if fault == "cancel":
            raise KeyboardInterrupt("startup cancellation")
        if fault == "reply_budget":
            clock.sleep(0.041)
        return original(*args, **kwargs)

    monkeypatch.setattr(operation.transport, "read_group", read)
    if fault == "status":
        robot.left_bus.registers[("Status", "lift_axis")] = 1
    if fault == "current":
        robot.left_bus.rest_current = 330
    at(clock, start + 0.505)
    assert clock.now - operation.last_record["sample_monotonic_s"] < feedback.POLL_S
    error_type = KeyboardInterrupt if fault == "cancel" else feedback.ComparisonRefusal
    with pytest.raises(error_type) as caught:
        robot._refresh_am1_startup_lift_feedback()
    assert operation.failure is caught.value
    with pytest.raises(error_type) as again:
        robot._refresh_am1_startup_lift_feedback()
    assert again.value is caught.value


def test_new_slot_cannot_reacquire_through_latched_heat(native173b_slots, monkeypatch):
    robot, clock, start = native173b_slots
    operation = robot._lift_operation
    window = operation.temperature
    original = feedback.ComparisonRefusal("temperature majority stop latched")
    window.failure = original
    peaks = list(window.samples)
    origin = window._origin_ns
    at(clock, start + 0.505)
    monkeypatch.setattr(
        operation.transport, "read_group", lambda *a, **k: pytest.fail("latched heat must stop before IO")
    )
    with pytest.raises(feedback.ComparisonRefusal) as caught:
        robot._refresh_am1_startup_lift_feedback()
    assert (
        caught.value is original
        and window.failure is original
        and window._origin_ns == origin
        and list(window.samples) == peaks
    )


def test_new_slot_cannot_wash_stale_raw_gap_into_permission(native173b_slots, monkeypatch):
    robot, clock, start = native173b_slots
    operation = robot._lift_operation
    window = operation.temperature
    origin = window._origin_ns
    peaks = list(window.samples)
    at(clock, start + 1.0)
    monkeypatch.setattr(
        operation.transport, "read_group", lambda *a, **k: pytest.fail("raw gap must stop before IO")
    )
    with pytest.raises(feedback.ComparisonRefusal, match="stale"):
        robot._refresh_am1_startup_lift_feedback()
    assert window._origin_ns == origin and list(window.samples) == peaks


def test_actual_production_majority_heat_latch_prevents_later_new_slot_read(native173b_slots, monkeypatch):
    robot, clock, start = native173b_slots
    operation = robot._lift_operation
    window = operation.temperature
    robot.left_bus.registers[("Present_Temperature", "lift_axis")] = 55
    for offset in (0.505, 0.605):
        at(clock, start + offset - 0.002)
        operation.poll()
    at(clock, start + 0.705 - 0.002)
    with pytest.raises(feedback.ComparisonRefusal, match="majority") as caught:
        operation.poll()
    original = caught.value
    window_latch = window.failure
    origin = window._origin_ns
    peaks = list(window.samples)
    # The unchanged monitor wraps observer refusal; preserve BOTH existing
    # failure identities and their complete original cause, without policy edits.
    assert sum(t >= 55 for _, t in peaks) == 3 and window_latch is not None
    assert str(window_latch) == str(original)
    robot.left_bus.registers[("Present_Temperature", "lift_axis")] = 34
    at(clock, start + 0.805)
    monkeypatch.setattr(
        operation.transport, "read_group", lambda *a, **k: pytest.fail("low cannot wash actual heat latch")
    )
    with pytest.raises(feedback.ComparisonRefusal) as again:
        robot._refresh_am1_startup_lift_feedback()
    assert again.value is original and window.failure is window_latch and operation.failure is original
    assert window._origin_ns == origin and list(window.samples) == peaks
