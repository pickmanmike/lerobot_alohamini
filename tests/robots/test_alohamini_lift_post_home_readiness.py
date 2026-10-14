"""AM1-only post-home readiness through real sample decode and original guards."""

from __future__ import annotations

import copy
import math
from types import SimpleNamespace

import pytest

from lerobot.robots.alohamini import lift_motor_feedback as feedback, lift_relief
from lerobot.robots.alohamini.lift_axis import LiftAxis, LiftAxisConfig, LiftHomeResult
from lerobot.robots.alohamini.lift_operational import OperationalLift, TemperatureWindow
from tests.robots.test_alohamini_lift_motor_feedback import FakeClock, FakeTransport, dynamic_payload

ORIGINAL_303 = [(808, 50, 1), (808, 0, 0), (809, -50, 1), (808, 0, 0), (809, 0, 0)]


class SequenceTransport(FakeTransport):
    def __init__(self, clock, rows, *, error=None, mutation=None, clock_fault=None, delay=0.002):
        super().__init__(clock=clock)
        self.torque = 1
        self.rows = rows
        self.read_count = 0
        self.error, self.mutation, self.clock_fault, self.delay = error, mutation, clock_fault, delay
        self.arrivals = []

    def read_group(self, start, length, **kwargs):
        if (start, length) != (feedback.FEEDBACK_START, feedback.FEEDBACK_LENGTH):
            return super().read_group(start, length, **kwargs)
        assert kwargs["timeout_ms"] == 40.0
        if self.error:
            raise feedback.TransportRefusal(self.error, {"operation": "sync_read", "checksum_ok": False})
        self.clock.sleep(self.delay)
        if self.clock_fault and self.read_count == 1:
            self.clock.now = self.arrivals[-1] - (0.001 if self.clock_fault == "backward" else 0.0)
        self.arrivals.append(self.clock.now)
        position, velocity, moving = self.rows[self.read_count % len(self.rows)]
        self.read_count += 1
        fields = {
            "torque": 1,
            "goal_velocity": 0,
            "present_position": position,
            "present_velocity": velocity,
            "moving": moving,
            "current": 40,
        }
        fields.update(self.mutation or {})
        mode = fields.pop("mode", 1)
        payload = bytearray(dynamic_payload(**fields))
        payload[feedback.OPERATING_MODE_ADDRESS - feedback.FEEDBACK_START] = mode
        return bytes(payload), {"response_payload_bytes": list(payload), "checksum_ok": True}

    def write_register(self, *_args, **_kwargs):
        pytest.fail("readiness qualification attempted a motor write")


def comparison(rows, **transport_options):
    clock = FakeClock()
    transport = SequenceTransport(clock, rows, **transport_options)
    emitted = []
    operation = OperationalLift.__new__(OperationalLift)
    operation.temperature = TemperatureWindow()
    operation._last_warning = -math.inf
    for at in (-0.4, -0.3, -0.2, -0.1, 0.0):
        operation.temperature.update(38, at)
    monitor = feedback.MotorFeedbackComparison(
        transport,
        input_fn=lambda _: pytest.fail("readiness requested operator input"),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        emit=lambda row: emitted.append(copy.deepcopy(row)),
        min_stationary_samples=5,
        sample_timeout_ms=40.0,
        immediate_current_abort_ma=2000.0,
        temperature_check=operation._check_temperature,
    )
    operation.monitor = monitor
    monitor.read_configuration()
    return monitor, clock, transport, emitted


def qualify(monitor, *, origin=808, **overrides):
    kwargs = {
        "expected_torque": 1,
        "expected_goal": 0,
        "allow_settling": True,
        "timeout_s": 1.0,
        "require_quiet_motion_feedback": True,
        "am1_post_home_origin_raw": origin,
    }
    kwargs.update(overrides)
    return monitor.qualify_stationary("post_home", **kwargs)


def test_original_303_one_count_intermittent_flags_qualifies_only_at_original_deadline():
    monitor, clock, transport, _ = comparison(ORIGINAL_303)
    record = qualify(monitor)
    evidence = record["stationary_qualification"]
    assert evidence["readiness_branch"] == "bounded_indicator"
    assert evidence["motion_feedback_quiet"] is False
    assert evidence["am1_post_home_origin_raw"] == 808
    assert evidence["whole_phase_position_excursion_raw"] == 1
    assert abs(evidence["whole_phase_position_drift_raw"]) <= 1
    assert evidence["sample_count"] >= 5 and evidence["window_s"] >= 0.2
    assert clock.now == pytest.approx(1.0)
    assert 0 <= clock.now - record["sample_monotonic_s"] <= feedback.POLL_S
    assert all(at < 1.0 for at in transport.arrivals)
    assert transport.read_count == 20


def test_predeadline_acquisition_remains_eligible_when_emit_returns_after_deadline():
    monitor, clock, transport, _ = comparison(ORIGINAL_303, delay=0.0024)
    original_emit = monitor.emit

    def delayed_emit(record):
        original_emit(record)
        if record.get("phase") == "post_home" and transport.read_count == 20:
            clock.sleep(0.003)

    monitor.emit = delayed_emit
    record = qualify(monitor)
    evidence = record["stationary_qualification"]
    assert evidence["readiness_branch"] == "bounded_indicator"
    assert evidence["sample_count"] == transport.read_count == 20
    assert record["sample_monotonic_s"] == pytest.approx(0.998)
    assert clock.now == pytest.approx(1.001)
    assert all(at < 1.0 for at in transport.arrivals)
    assert clock.now - record["sample_monotonic_s"] < feedback.POLL_S


@pytest.mark.parametrize(
    "rows,origin",
    [
        ([(809, 50, 1), *[(809, 0, 0)] * 5], 809),
        ([(810, 0, 0), (813, 50, 1), *[(814, 0, 0)] * 5], 810),
    ],
)
def test_c700_and_historical_four_count_settle_keep_strict_success_before_deadline(rows, origin):
    monitor, clock, _, _ = comparison(rows)
    record = qualify(monitor, origin=origin)
    evidence = record["stationary_qualification"]
    assert evidence["readiness_branch"] == "strict_quiet"
    assert evidence["motion_feedback_quiet"] is True
    assert evidence["present_velocity_raw"] == evidence["moving"] == [0] * 5
    assert evidence["sample_count"] == 5 and evidence["window_s"] >= 0.2
    assert clock.now < 0.4


def test_strict_diagnostic_without_am1_opt_in_still_refuses_original_303():
    monitor, _, _, _ = comparison(ORIGINAL_303)
    with pytest.raises(feedback.ComparisonRefusal, match="stationary"):
        monitor.qualify_stationary(
            "post_home",
            expected_torque=1,
            expected_goal=0,
            allow_settling=True,
            timeout_s=1.0,
            require_quiet_motion_feedback=True,
        )


@pytest.mark.parametrize(
    "rows",
    [
        [(808, 50, 1)] * 3,
        [(808, -50, 1)] * 3,
        [(808, 0, 1)] * 3,
        [(808, 50, 1), (808, -50, 1), (808, 50, 1)],
        [(808, 50, 0)] * 3,
    ],
)
def test_persistent_indications_abort_across_all_phase_frames_not_candidates(rows):
    monitor, clock, transport, _ = comparison(rows)
    with pytest.raises(feedback.ComparisonRefusal, match="persistent"):
        qualify(monitor)
    assert transport.read_count == 3 and clock.now < 0.15


@pytest.mark.parametrize(
    "rows,origin",
    [
        ([(808, 0, 0), (809, 50, 1), (809, 0, 0), (810, -50, 1), (810, 0, 0), (811, 50, 1)], 808),
        ([(4094, 0, 0), (4095, 50, 1), (0, 0, 0), (1, -50, 1)], 4094),
    ],
)
def test_sparse_and_wrapped_real_position_creep_abort_before_deadline(rows, origin):
    monitor, clock, _, _ = comparison(rows)
    with pytest.raises(feedback.ComparisonRefusal, match="position|drift|creep"):
        qualify(monitor, origin=origin)
    assert clock.now < 0.3


def test_wrapped_one_tick_reversal_can_qualify_without_erasing_home_origin():
    rows = [(4095, 50, 1), (4095, 0, 0), (0, -50, 1), (4095, 0, 0), (0, 0, 0)]
    monitor, _, _, _ = comparison(rows)
    evidence = qualify(monitor, origin=4095)["stationary_qualification"]
    assert evidence["readiness_branch"] == "bounded_indicator"
    assert evidence["whole_phase_position_excursion_raw"] == 1


@pytest.mark.parametrize(
    "rows",
    [
        [(808, 0, 0), (810, 50, 1), (808, 0, 0), (808, -50, 1), (808, 0, 0)],
        [(808, 100, 1), (808, 0, 0), (809, -50, 1), (808, 0, 0), (809, 0, 0)],
    ],
)
def test_whole_phase_excursion_or_speed_permanently_disables_bounded_fallback(rows):
    monitor, clock, _, _ = comparison(rows)
    with pytest.raises(feedback.ComparisonRefusal, match="stationary|post_home"):
        qualify(monitor)
    assert clock.now <= 1.0


@pytest.mark.parametrize(
    "mutation",
    [
        {"goal_velocity": 1},
        {"torque": 0},
        {"mode": 0},
        {"status_value": 1},
        {"voltage": 150},
        {"current": 308},
        {"temperature": 55},
        {"moving": 2},
    ],
)
def test_bounded_branch_never_bypasses_actual_hard_feedback_guards(mutation):
    monitor, _, _, _ = comparison(ORIGINAL_303, mutation=mutation)
    with pytest.raises(feedback.ComparisonRefusal):
        qualify(monitor)


@pytest.mark.parametrize("error", ["missing reply", "checksum invalid", "foreign ID", "incomplete reply"])
def test_transport_refusal_cannot_be_replaced_by_readiness(error):
    monitor, _, transport, _ = comparison(ORIGINAL_303, error=error)
    with pytest.raises(feedback.ComparisonRefusal, match="grouped feedback failed"):
        qualify(monitor)
    assert transport.read_count == 0


@pytest.mark.parametrize("clock_fault", ["repeated", "backward"])
def test_nonadvancing_actual_timestamps_refuse(clock_fault):
    monitor, _, _, _ = comparison(ORIGINAL_303, clock_fault=clock_fault)
    with pytest.raises(feedback.ComparisonRefusal, match="timestamp|fresh|backward|repeated"):
        qualify(monitor)


def test_over_budget_actual_request_refuses_before_readiness():
    monitor, _, _, _ = comparison(ORIGINAL_303, delay=0.041)
    with pytest.raises(feedback.ComparisonRefusal, match="40 ms"):
        qualify(monitor)


def test_original_deadline_cannot_accept_a_stale_latest_predeadline_sample():
    monitor, clock, transport, _ = comparison(ORIGINAL_303)
    ordinary_sleep = clock.sleep

    def scheduled_sleep(seconds):
        if seconds >= 0.04 and clock.now >= 0.85:
            clock.now = 1.0  # Scheduler delay; no fabricated extra measurement.
        else:
            ordinary_sleep(seconds)

    clock.sleep = scheduled_sleep
    monitor.sleep = scheduled_sleep
    with pytest.raises(feedback.ComparisonRefusal, match="fresh|stale|stationary"):
        qualify(monitor)
    assert 1.0 - transport.arrivals[-1] > feedback.POLL_S
    assert all(at < 1.0 for at in transport.arrivals)


def test_inherited_on_sample_guard_sees_nonquiet_samples_before_any_reset():
    monitor, _, _, _ = comparison([(808, 100, 1), *[(808, 0, 0)] * 5])
    checked = []

    def guard(record):
        checked.append(record["present_velocity_raw"])
        if checked == [100]:
            raise feedback.ComparisonRefusal("modeled inherited0.5mm process-zero guard")

    with pytest.raises(feedback.ComparisonRefusal, match="inherited0.5mm"):
        qualify(monitor, on_sample=guard)
    assert checked == [100]


@pytest.mark.parametrize(
    "phase,overrides",
    [
        ("baseline", {}),
        ("post_home", {"expected_torque": 0}),
        ("post_home", {"expected_goal": 1}),
        ("post_home", {"require_quiet_motion_feedback": False}),
    ],
)
def test_am1_opt_in_requires_narrow_exact_post_home_scope(phase, overrides):
    monitor, _, transport, _ = comparison(ORIGINAL_303)
    kwargs = {
        "expected_torque": 1,
        "expected_goal": 0,
        "allow_settling": True,
        "timeout_s": 1.0,
        "require_quiet_motion_feedback": True,
        "am1_post_home_origin_raw": 808,
    }
    kwargs.update(overrides)
    with pytest.raises(ValueError):
        monitor.qualify_stationary(phase, **kwargs)
    assert transport.read_count == 0


def test_late_final_read_keeps_latest_real_lift_accumulator_with_earlier_bounded_receipt(monkeypatch):
    rows = [(808, velocity, moving) for _, velocity, moving in ORIGINAL_303]
    monitor, clock, transport, _ = comparison(rows)
    monkeypatch.setattr(lift_relief.time, "monotonic", clock.monotonic)
    ordinary_sleep, ordinary_read = clock.sleep, transport.read_group

    def almost_deadline_sleep(seconds):
        # Model a predeadline wake followed by an already-started valid2ms read.
        ordinary_sleep(seconds - 0.000001 if seconds < 0.05 and clock.now > 0.95 else seconds)

    def final_read(start, length, **kwargs):
        payload, trace = ordinary_read(start, length, **kwargs)
        if transport.read_count == 21:
            payload = bytearray(payload)
            position = 809
            offset = feedback.PRESENT_POSITION_ADDRESS - feedback.FEEDBACK_START
            payload[offset : offset + 2] = bytes((position & 255, position >> 8))
            return bytes(payload), {**trace, "response_payload_bytes": list(payload)}
        return payload, trace

    monitor.sleep = almost_deadline_sleep
    transport.read_group = final_read
    axis = LiftAxis(LiftAxisConfig(), bus_left=SimpleNamespace(), bus_right=None)
    axis._last_tick = 808
    axis._extended_ticks = axis._z0_deg = 0.0
    axis.is_homed = True
    monkeypatch.setattr(axis, "home", lambda **_: LiftHomeResult(0.0, "modeled home", 808, 300.0))
    operation = lift_relief.InstalledLiftCheck.__new__(lift_relief.InstalledLiftCheck)
    operation.lift, operation.monitor, operation.home_started = axis, monitor, 0.0
    operation.home_guard = lambda *_: None
    returned = []
    ordinary_qualify = monitor.qualify_stationary

    def keep_receipt(*args, **kwargs):
        record = ordinary_qualify(*args, **kwargs)
        returned.append(record)
        return record

    monitor.qualify_stationary = keep_receipt

    class ReliefBoundaryError(Exception):
        pass

    def phase(name):
        if name == "relief_setup":
            raise ReliefBoundaryError()

    operation.reader = SimpleNamespace(set_phase=phase)
    with pytest.raises(ReliefBoundaryError):
        operation.home_and_relieve(require_quiet_post_home=True, am1_post_home_readiness=True)
    assert returned[0]["sample_monotonic_s"] < 1.0 < transport.arrivals[-1]
    assert returned[0]["present_position_raw"] == 808
    assert axis._last_tick == 809 and axis._extended_ticks == 1.0


def test_inherited_half_mm_guard_bounds_whole_phase_span_not_only_absolute_height(monkeypatch):
    monitor, clock, _, _ = comparison([(828, 50, 1), *[(788, 0, 0)] * 5])
    monkeypatch.setattr(lift_relief.time, "monotonic", clock.monotonic)
    axis = LiftAxis(LiftAxisConfig(), bus_left=SimpleNamespace(), bus_right=None)
    axis._last_tick = 808
    axis._extended_ticks = axis._z0_deg = 0.0
    axis.is_homed = True
    monkeypatch.setattr(axis, "home", lambda **_: LiftHomeResult(0.0, "modeled home", 808, 300.0))
    operation = lift_relief.InstalledLiftCheck.__new__(lift_relief.InstalledLiftCheck)
    operation.lift, operation.monitor, operation.home_started = axis, monitor, 0.0
    operation.home_guard = lambda *_: None

    def phase(name):
        if name == "relief_setup":
            pytest.fail("opposite0.41mm displacements escaped whole-phase0.5mm bound")

    operation.reader = SimpleNamespace(set_phase=phase)
    with pytest.raises(feedback.ComparisonRefusal, match="bound"):
        operation.home_and_relieve(require_quiet_post_home=True, am1_post_home_readiness=True)
    assert clock.now < 0.1
