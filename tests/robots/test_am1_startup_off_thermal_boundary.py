"""Combined17 off-state thermal handoff; all owner I/O and added work are synthetic.

The recorded completion offsets are replayed through the real startup monitor.
The native record does not time individual callback/output work or its final
freshness guard. Added boundary work below is a synthetic hypothesis, not a
measurement or a replacement for Combined17's original failed outcome.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from lerobot.robots.alohamini import lift_motor_feedback as feedback
from lerobot.robots.alohamini.lift_operational import OperationalLift, TemperatureWindow
from tests.robots.test_alohamini_lift_operational import operating_robot as _operating_robot
from tests.robots.test_alohamini_lift_relief import GroupedLiftTransport

operating_robot = _operating_robot

# Immutable Combined17 native863e6c59…: one off acquisition and nine baseline
# replies. Relative to the original requested_at158883.898733134, not new times.
REQUESTED = 158883.898733134
COMPLETIONS = (
    158883.902560333, 158883.954584262, 158884.010593500,
    158884.066589257, 158884.122592699, 158884.174591462,
    158884.230621589, 158884.286606198, 158884.342606566,
    158884.398617916,
)


class ReachedHomeBoundary(BaseException):
    """Stop this unpowered test before the first home/enable operation."""


@pytest.fixture
def off_boundary(operating_robot, monkeypatch):
    robot, clock = operating_robot
    robot.connect(calibrate=False, activate=False, home_lift=False)
    operation = OperationalLift(robot)
    robot._lift_operation = operation
    bus = robot.left_bus
    state = SimpleNamespace(
        robot=robot, operation=operation, clock=clock, records=[], reads=[],
        requests=[], index=0, target=1, work="new-slot", fault=None,
        armed=False, changed=False, baseline_peaks=None, window=operation.temperature,
    )
    sample = operation.monitor.sample

    def read(*args, **kwargs):
        if args[:2] == (feedback.FEEDBACK_START, feedback.FEEDBACK_LENGTH):
            state.reads.append((clock.now, kwargs.get("timeout_ms")))
            if state.armed and state.fault == "reply-budget":
                clock.sleep(0.041)
        # A finite synthetic1ms transaction allows the original recorded
        # completion offsets; this is not a native request-duration claim.
        clock.sleep(0.001)
        return GroupedLiftTransport.read_group(operation.transport, *args, **kwargs)

    def scripted_sample(phase, **kwargs):
        if phase in ("startup_off_acquisition", "baseline"):
            completed = state.requests[-1] + COMPLETIONS[state.index] - REQUESTED
            assert clock.now <= completed - 0.001 + 1e-9
            clock.sleep(completed - 0.001 - clock.now)
            # These are original slot maxima from genuine fake group replies.
            value = 32
            if state.fault in ("majority", "cold") and state.index >= 6:
                value = 55 if state.fault == "majority" else 41
            if state.fault == "early-majority" and state.index >= 4:
                value = 55
            bus.registers["Present_Temperature", "lift_axis"] = value
            state.index += 1
        return sample(phase, **kwargs)

    def emit(record):
        state.records.append(dict(record))
        phase = record["phase"]
        if phase == "startup_off_request_started":
            state.requests.append(record["requested_at_monotonic_s"])
            state.index = 0
        event = "startup_off_baseline_qualified" if state.target == 1 else "before_torque_off_acquisition_completed"
        if phase != event or len(state.requests) != state.target or state.changed:
            return
        state.changed = True
        state.baseline_peaks = list(state.window.samples)
        state.original_origin = state.window._origin_ns
        state.read_count = len(state.reads)
        oldest = state.baseline_peaks[0][0]
        if state.work == "new-slot":
            clock.sleep(oldest + 0.505 - clock.now)
        elif state.work == "deadline":
            clock.sleep(state.requests[-1] + feedback.STATIONARY_TIMEOUT_S - clock.now)
        elif state.work == "raw-stale":
            clock.sleep(state.window._last_sample_at + 0.501 - clock.now)
        elif state.work == "latch":
            state.original_failure = feedback.ComparisonRefusal("original confirmed heat stop")
            state.window.failure = state.original_failure
        state.armed = True
        if state.fault in ("majority", "isolated-high", "cold"):
            bus.registers["Present_Temperature", "lift_axis"] = 41 if state.fault == "cold" else 55
        if state.fault == "status":
            bus.registers["Status", "lift_axis"] = 1
        if state.fault in ("current", "gross-current"):
            bus.read_sequences["Present_Current", "lift_axis"] = [31 if state.fault == "current" else 308]
        if state.fault == "motion":
            bus.position += 2

    def home(**kwargs):
        if state.target == 2:
            # Genuine off feedback preserves this SAME window between the two
            # private qualifiers; no baseline or origin is reconstructed/reset.
            clock.sleep(feedback.POLL_S)
            operation.monitor.sample("between_off_qualifiers", expected_torque=0, expected_goal=0, cold_start=True)
            origin = state.window._origin_ns / 1e9
            next_origin = origin + math.ceil((clock.now - origin + 0.05) / 0.1) * 0.1
            requested = next_origin - (COMPLETIONS[0] - REQUESTED)
            clock.sleep(requested - clock.now)
            operation._qualify_before_torque()
        state.at_home = clock.now
        raise ReachedHomeBoundary

    monkeypatch.setattr(operation.monitor, "sample", scripted_sample)
    monkeypatch.setattr(operation.transport, "read_group", read)
    monkeypatch.setattr(operation, "emit", emit)
    monkeypatch.setattr(operation.monitor, "emit", emit)
    monkeypatch.setattr(operation, "home_and_relieve", home)
    try:
        yield state
    finally:
        # Fake fixture bookkeeping, not a physical cleanup qualification.
        for owned_bus in (robot.left_bus, robot.right_bus):
            if owned_bus is not None:
                owned_bus.is_connected = False


@pytest.mark.parametrize("qualifier", [1, 2])
def test_due_original_slot_is_acquired_off_before_home_or_torque(off_boundary, qualifier):
    state = off_boundary
    state.target = qualifier
    with pytest.raises(ReachedHomeBoundary):
        state.operation.start()
    assert state.operation.temperature is state.window
    assert state.window._origin_ns == state.original_origin
    assert list(state.window.samples)[:-1] == state.baseline_peaks[1:]
    assert len(state.reads) == state.read_count + 1
    assert all(state.at_home - stamp <= 0.5 for stamp, _ in state.window.samples)
    qualifications = [r for r in state.records if r["phase"] == "baseline_stationary_qualified"]
    assert len(qualifications) == qualifier and all(r["sample_count"] == 9 for r in qualifications)
    assert all(r["expected_torque"] == r["expected_goal_velocity_raw"] == 0 for r in qualifications)
    assert state.reads[-1][1] <= 40
    assert not any(e[1:] == ("write", "Torque_Enable", "lift_axis", 1) for e in state.robot.left_bus.events)


def test_same_slot_fresh_window_has_no_additional_read(off_boundary):
    state = off_boundary
    state.work = "none"
    with pytest.raises(ReachedHomeBoundary):
        state.operation.start()
    assert len(state.reads) == state.read_count
    assert list(state.window.samples) == state.baseline_peaks


@pytest.mark.parametrize("work,reason", [("deadline", "deadline"), ("raw-stale", "stale"), ("latch", "original confirmed heat")])
def test_expired_budget_raw_gap_or_latch_refuses_before_another_read(off_boundary, work, reason):
    state = off_boundary
    state.work = work
    with pytest.raises(feedback.ComparisonRefusal, match=reason) as caught:
        state.operation.start()
    assert len(state.reads) == state.read_count
    assert list(state.window.samples) == state.baseline_peaks
    assert state.operation.failure is caught.value
    if work == "latch":
        assert caught.value is state.original_failure


@pytest.mark.parametrize("fault,reason", [
    ("status", "status"), ("current", "200 mA"), ("gross-current", "2000 mA"),
    ("motion", "one tick"), ("reply-budget", "40 ms"), ("majority", "majority"), ("cold", "40 C"),
])
def test_genuine_handoff_reply_keeps_existing_fault_guards(off_boundary, fault, reason):
    state = off_boundary
    state.fault = fault
    with pytest.raises(feedback.ComparisonRefusal, match=reason) as caught:
        state.operation.start()
    assert len(state.reads) == state.read_count + 1
    assert state.operation.failure is caught.value
    assert not hasattr(state, "at_home")
    if fault == "majority":
        assert sum(value >= 55 for _, value in state.window.samples) == 3
        assert state.window.failure is not None


def test_one_new_numeric_high_is_retained_without_heat_purge(off_boundary):
    state = off_boundary
    state.fault = "isolated-high"
    with pytest.raises(ReachedHomeBoundary):
        state.operation.start()
    assert list(state.window.samples)[-1][1] == 55 and state.window.outliers == 1
    assert any(r["phase"] == "temperature_warning" for r in state.records)


def test_confirmed_high_during_original_baseline_stops_before_handoff(off_boundary):
    state = off_boundary
    state.fault = "early-majority"
    with pytest.raises(feedback.ComparisonRefusal, match="majority"):
        state.operation.start()
    assert not state.changed and not hasattr(state, "at_home")
    assert state.window.failure is not None


def test_saved_combined17_cool_peaks_still_refuse_when_original_age_expires():
    window = TemperatureWindow()
    for stamp in COMPLETIONS:
        window.update(32, stamp, cold_start=True)
    peaks = list(window.samples)
    assert COMPLETIONS[-1] - peaks[0][0] == pytest.approx(0.496057583)
    # Synthetic later guard time, not a claimed timestamp of the native guard.
    with pytest.raises(feedback.ComparisonRefusal, match="stale"):
        window.assert_fresh(peaks[0][0] + 0.505)
    assert list(window.samples) == peaks
