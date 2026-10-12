"""AM1 startup acquisition scheduling with real thermal policy and sole-owner grouped fake I/O."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from lerobot.robots.alohamini import (
    alohamini_host as host,
    lift_motor_feedback as feedback,
)
from tests.robots.test_alohamini_lift_operational import operating_robot as _operating_robot

operating_robot = _operating_robot


@pytest.fixture
def startup_window(operating_robot):
    robot, clock = operating_robot
    robot.connect(calibrate=False)
    operation = robot._lift_operation
    robot.left_bus.registers[("Present_Temperature", "lift_axis")] = 34
    origin = operation.temperature._origin_ns / 1e9
    start = origin + math.ceil((clock.now - origin) / 0.1) * 0.1
    # Genuine grouped mocked reads reconstruct the native45d4 occupied-slot
    # offsets. Retain this SAME already-running window/object/origin.
    for offset in (0.067284123, 0.169340880, 0.224324460, 0.328343799, 0.432361898, 0.486275928):
        clock.sleep(start + offset - clock.now - 0.002)
        operation.poll()
    assert len(operation.temperature.samples) == 5
    try:
        yield robot, clock, start + 0.486275928
    finally:
        # Only close fake bookkeeping after deliberate hardfault injections.
        # No cleanup qualification or physical evidence is claimed here.
        for bus in (robot.left_bus, robot.right_bus):
            if bus is not None:
                bus.is_connected = False


def at(clock, when):
    clock.sleep(when - clock.now)


def test_real_activation_and_final_handoff_fill_skipped_slot_without_reset(startup_window):
    robot, clock, first = startup_window
    operation = robot._lift_operation
    window = operation.temperature
    origin = window._origin_ns
    original_peaks = list(window.samples)
    # The two natural activation guards span the original117ms handoff.
    at(clock, first + 0.055)
    robot._require_am1_activation_fresh("after_home")
    at(clock, first + 0.090)
    robot._require_am1_activation_fresh("after_home")
    at(clock, first + 0.117072585 - 0.002)
    operation.poll()
    for elapsed in (0.151013739, 0.184015360):
        at(clock, first + elapsed - 0.002)
        operation.poll()
    assert operation.temperature is window and window._origin_ns == origin
    assert window.failure is None and operation.failure is None
    assert window.MAX_AGE_S == 0.5 and window.CEILING_C == 55
    assert original_peaks[-1] in list(window.samples)
    assert all(clock.now - stamp <= 0.5 for stamp, _ in window.samples)


def test_original_uninterleaved_window_still_refuses_native_clock_pattern(startup_window):
    robot, clock, first = startup_window
    operation = robot._lift_operation
    for elapsed in (0.117072585, 0.151013739):
        at(clock, first + elapsed - 0.002)
        operation.poll()
    at(clock, first + 0.184015360 - 0.002)
    with pytest.raises(feedback.ComparisonRefusal, match="five-slot.*stale"):
        operation.poll()


def test_not_due_startup_boundary_does_not_read_or_stamp_new_evidence(startup_window, monkeypatch):
    robot, clock, first = startup_window
    operation = robot._lift_operation
    old = operation.last_record
    peaks = list(operation.temperature.samples)
    at(clock, first + 0.025)
    monkeypatch.setattr(operation.transport, "read_group", lambda *a, **k: pytest.fail("not due"))
    robot._refresh_am1_startup_lift_feedback()
    assert operation.last_record is old and list(operation.temperature.samples) == peaks


@pytest.mark.parametrize("fault", ["status", "current", "reply_budget", "cancel"])
def test_due_startup_acquisition_preserves_original_fault_or_cancellation(startup_window, monkeypatch, fault):
    robot, clock, first = startup_window
    operation = robot._lift_operation
    original = operation.transport.read_group

    def read_group(*a, **k):
        if fault == "cancel":
            raise KeyboardInterrupt("startup cancelled")
        if fault == "reply_budget":
            clock.sleep(0.041)
        return original(*a, **k)

    monkeypatch.setattr(operation.transport, "read_group", read_group)
    if fault == "status":
        robot.left_bus.registers[("Status", "lift_axis")] = 1
    if fault == "current":
        robot.left_bus.rest_current = 330
    at(clock, first + 0.055)
    error_type = KeyboardInterrupt if fault == "cancel" else feedback.ComparisonRefusal
    with pytest.raises(error_type) as caught:
        robot._require_am1_activation_fresh("after_home")
    assert operation.failure is caught.value
    with pytest.raises(error_type) as again:
        robot._refresh_am1_startup_lift_feedback()
    assert again.value is caught.value


def test_preexisting_heat_latch_is_retained_before_any_new_startup_io(startup_window, monkeypatch):
    robot, clock, first = startup_window
    operation = robot._lift_operation
    original = feedback.ComparisonRefusal("temperature majority stop latched")
    operation.temperature.failure = original
    at(clock, first + 0.055)
    monkeypatch.setattr(
        operation.transport, "read_group", lambda *a, **k: pytest.fail("latched heat cannot acquire lows")
    )
    with pytest.raises(feedback.ComparisonRefusal) as caught:
        robot._refresh_am1_startup_lift_feedback()
    assert caught.value is original and operation.temperature.failure is original


@pytest.mark.parametrize("stamp", [None, True, float("nan"), "forged"])
def test_missing_or_malformed_original_acquisition_refused_before_io(startup_window, monkeypatch, stamp):
    robot, _, _ = startup_window
    robot._lift_operation.last_record = {"sample_monotonic_s": stamp}
    monkeypatch.setattr(
        robot._lift_operation.transport, "read_group", lambda *a, **k: pytest.fail("invalid cache")
    )
    with pytest.raises(feedback.ComparisonRefusal):
        robot._refresh_am1_startup_lift_feedback()


def test_expired_raw_feedback_is_not_reacquired_into_permission(startup_window, monkeypatch):
    robot, clock, first = startup_window
    at(clock, first + 0.501)
    monkeypatch.setattr(
        robot._lift_operation.transport, "read_group", lambda *a, **k: pytest.fail("stale raw")
    )
    with pytest.raises(feedback.ComparisonRefusal, match="stale"):
        robot._refresh_am1_startup_lift_feedback()


def test_original_arm_vector_one_second_guard_is_not_renewed_by_lift_read(startup_window):
    robot, clock, first = startup_window
    robot.left_arm_motors = ["synthetic_arm"]
    robot._am1_activation_readbacks = [
        {
            "phase": "after_home",
            "register": "Present_Position",
            "read_started_at": clock.now - 1.01,
            "read_completed_at": clock.now - 1.0,
        }
    ]
    before = [dict(r) for r in robot._am1_activation_readbacks]
    at(clock, first + 0.055)
    with pytest.raises(RuntimeError, match="original raw position vector expired"):
        robot._require_am1_activation_fresh("after_home")
    assert robot._am1_activation_readbacks == before


def test_before_home_and_am2_never_gain_startup_lift_io(startup_window, monkeypatch):
    robot, _, _ = startup_window
    monkeypatch.setattr(
        robot._lift_operation.transport, "read_group", lambda *a, **k: pytest.fail("legacy path")
    )
    robot._require_am1_activation_fresh("before_home")
    robot.config.robot_model = "alohamini2"
    robot._refresh_am1_startup_lift_feedback()


def test_real_host_final_handoff_acquires_only_due_on_owner_thread(startup_window, monkeypatch):
    robot, clock, first = startup_window
    operation = robot._lift_operation
    window = operation.temperature
    acquired = []
    read = operation.transport.read_group

    def recorded(*a, **k):
        if operation.bus.expected_torque == 1:
            acquired.append(clock.now)
        return read(*a, **k)

    monkeypatch.setattr(operation.transport, "read_group", recorded)
    monkeypatch.setattr(host, "AlohaMini", lambda _: robot)
    monkeypatch.setattr(host, "connect_robot", lambda *a, **k: None)

    def factory(_):
        clock.sleep(0.055)
        return SimpleNamespace(
            connection_time_s=0, max_loop_freq_hz=30, watchdog_timeout_ms=1000, disconnect=lambda: None
        )

    monkeypatch.setattr(host, "AlohaMiniHost", factory)
    args = host.make_parser().parse_args(["--no_cameras", "--no_follower"])
    host._run_host(args)
    assert len(acquired) == 1, "zero ordinary-loop iterations still need one due startup acquisition"
    assert acquired[0] >= first + 0.055
    assert operation.temperature is window
