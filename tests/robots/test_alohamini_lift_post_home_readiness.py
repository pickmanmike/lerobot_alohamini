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


# Exact original reply payloads and acquisition intervals, original host log SHA256
# 065439f0a09d0fd28f0ff234cb5678ca4dc4faa1cb873685a9afe66f86f6db3d.
# These original intervals run on an explicitly simulated, rebased test clock.
TELEOP03_POST_HOME = (
    (89421.937178631, 0.0009587679960532114, "0114c8500ac8c801fe000000000000e803000000000001290300005b05722400000000003a00"),
    (89421.989480213, 0.002122437013895251, "0114c8500ac8c801fe000000000000e8030000000000012c0300004e05712400000000003300"),
    (89422.041206034, 0.0014455310010816902, "0114c8500ac8c801fe000000000000e8030000000000012c0300004e05722400000000003400"),
    (89422.093246234, 0.0017886319983517751, "0114c8500ac8c801fe000000000000e8030000000000012c0300004e05722400000000003100"),
    (89422.145210826, 0.0016668210009811446, "0114c8500ac8c801fe000000000000e8030000000000012c0332804d05722400000100003600"),
    (89422.197216713, 0.0017475959903094918, "0114c8500ac8c801fe000000000000e8030000000000012c0300004e05722400000000003500"),
    (89422.249234192, 0.0017453369946451858, "0114c8500ac8c801fe000000000000e8030000000000012c0332804e05722400000100003700"),
    (89422.301217653, 0.001720264001050964, "0114c8500ac8c801fe000000000000e8030000000000012c0300004e05722400000000003300"),
    (89422.353243743, 0.0017647999920882285, "0114c8500ac8c801fe000000000000e8030000000000012c0332804f05722400000100003000"),
    (89422.405216428, 0.0017011899908538908, "0114c8500ac8c801fe000000000000e8030000000000012c0300004e05722400000000003400"),
    (89422.45721667, 0.0017345039959764108, "0114c8500ac8c801fe000000000000e8030000000000012c0300004e05722400000000003300"),
    (89422.509238466, 0.0017738919996190816, "0114c8500ac8c801fe000000000000e8030000000000012c0300004e05722400000000003600"),
    (89422.561220856, 0.0017200610018335283, "0114c8500ac8c801fe000000000000e8030000000000012c0332004c05722400000100003700"),
    (89422.613228282, 0.0017712630069581792, "0114c8500ac8c801fe000000000000e8030000000000012c0300004e05722400000000003300"),
    (89422.665221097, 0.0017515030049253255, "0114c8500ac8c801fe000000000000e8030000000000012d0332804e05722400000100003900"),
    (89422.717221857, 0.0017546890012454242, "0114c8500ac8c801fe000000000000e8030000000000012c0300004e05722400000000003600"),
    (89422.769226468, 0.001743689994327724, "0114c8500ac8c801fe000000000000e8030000000000012d0332004a05722400000100002f00"),
    (89422.821226913, 0.0017510030011180788, "0114c8500ac8c801fe000000000000e8030000000000012c0332004b05722400000100003900"),
    (89422.873222025, 0.0017613549862289801, "0114c8500ac8c801fe000000000000e8030000000000012d0300004e05722400000000003700"),
    (89422.925223655, 0.0017594300006749108, "0114c8500ac8c801fe000000000000e8030000000000012c0332804e05722400000100003300"),
)


def test_actual_teleop03_acquires_post_settle_window_without_retiming_home_or_deadline():
    monitor, clock, transport, _ = comparison([(809, 0, 0)])
    first_start = TELEOP03_POST_HOME[0][0] - TELEOP03_POST_HOME[0][1]
    original_sleep = clock.sleep

    def scheduled_sleep(seconds):
        if .04 <= seconds <= feedback.POLL_S and transport.read_count < len(TELEOP03_POST_HOME):
            stamp, duration, _ = TELEOP03_POST_HOME[transport.read_count]
            clock.now = stamp - duration - first_start
        else:
            original_sleep(seconds)

    def original_reply(start, length, **kwargs):
        assert (start, length) == (feedback.FEEDBACK_START, feedback.FEEDBACK_LENGTH)
        assert kwargs["timeout_ms"] == 40
        assert transport.read_count < len(TELEOP03_POST_HOME)
        stamp, _, payload = TELEOP03_POST_HOME[transport.read_count]
        transport.read_count += 1
        clock.now = stamp - first_start
        transport.arrivals.append(clock.now)
        value = bytes.fromhex(payload)
        return value, {"response_payload_bytes": list(value), "checksum_ok": True}

    monitor.sleep, transport.read_group = scheduled_sleep, original_reply
    result = qualify(monitor, origin=809)
    proof = result["stationary_qualification"]
    assert proof["readiness_branch"] == "bounded_indicator"
    assert proof["motion_feedback_quiet"] is False
    assert proof["am1_post_home_origin_raw"] == 809
    assert proof["position_origin_raw"] == 812
    assert proof["position_excursion_raw"] == 1
    assert proof["candidate_reset_count"] == 1
    assert proof["candidate_qualified_sample_index"] == 6
    assert proof["whole_phase_position_excursion_raw"] == 4
    assert proof["sample_count"] == 19
    assert clock.now == pytest.approx(1)
    assert transport.read_count == len(TELEOP03_POST_HOME) == 20
    assert all(stamp < 1 for stamp in transport.arrivals)



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
    axis = LiftAxis(LiftAxisConfig(), bus_left=SimpleNamespace(), bus_right=None)
    axis._last_tick, axis._extended_ticks, axis._z0_deg, axis.is_homed = origin, 0, 0, True
    heights = [0.0, 0.0]

    def original_home_guard(record):
        height = axis.get_height_mm(read_raw=lambda _: int(record["present_position_raw"]))
        heights[0], heights[1] = min(heights[0], height), max(heights[1], height)
        if abs(height) > .5 or heights[1] - heights[0] > .5:
            monitor.refuse(record, "post_home: process-local zero exceeded its stationary bound.")

    kwargs = {
        "expected_torque": 1,
        "expected_goal": 0,
        "allow_settling": True,
        "timeout_s": 1.0,
        "require_quiet_motion_feedback": True,
        "am1_post_home_origin_raw": origin,
        "on_sample": original_home_guard,
    }
    kwargs.update(overrides)
    return monitor.qualify_stationary("post_home", **kwargs)


@pytest.mark.parametrize("callback", [None, False, "not-a-callback"])
def test_am1_readiness_requires_its_original_whole_phase_geometry_observer(callback):
    monitor, _, transport, _ = comparison(ORIGINAL_303)
    with pytest.raises(ValueError, match="observer|guard"):
        qualify(monitor, on_sample=callback)
    assert transport.read_count == 0


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


def test_qualified_candidate_freezes_once_and_later_two_tick_jump_cannot_reacquire():
    first = [(813, 0, 0), (813, 50, 1), (813, 0, 0), (813, -50, 1), (813, 0, 0)]
    later = [(815, 50, 1), (815, 0, 0), (815, -50, 1), (815, 0, 0)] * 4
    monitor, clock, transport, emitted = comparison(first + later)
    with pytest.raises(feedback.ComparisonRefusal, match="stationary"):
        qualify(monitor, origin=813)
    assert clock.now == pytest.approx(1)
    assert transport.read_count == 20
    assert not any(row["phase"] == "post_home_stationary_qualified" for row in emitted)


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
    assert evidence["sample_count"] == 19
    assert evidence["whole_phase_sample_count"] == transport.read_count == 20
    assert evidence["stationary_acquisition_sample_index"] == 2
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


# Exact original grouped reply payloads; clocks below are explicitly simulated.
# Original payload provenance and production-clock replay are retained privately.
ACTUAL_POST_HOME_PAYLOADS = {'02': ['0114c8500ac8c801fe000000000000e8030000000000012f0364007605722600000100003c00', '0114c8500ac8c801fe000000000000e8030000000000012f0300006f05722600000000004400', '0114c8500ac8c801fe000000000000e8030000000000012f0300006f05722600000000004300', '0114c8500ac8c801fe000000000000e8030000000000012f0300006f05722600000000003c00', '0114c8500ac8c801fe000000000000e8030000000000012f0300006f05722600000000003e00', '0114c8500ac8c801fe000000000000e8030000000000012f0300006f05722600000000004200'], '03': ['0114c8500ac8c801fe000000000000e8030000000000012d0364006505722600000100003500', '0114c8500ac8c801fe000000000000e8030000000000012e0332005705732600000100003a00', '0114c8500ac8c801fe000000000000e8030000000000012e0300005705722600000000003a00', '0114c8500ac8c801fe000000000000e8030000000000012f0300005b05732600000100003c00', '0114c8500ac8c801fe000000000000e8030000000000012f0332005705722600000100003b00', '0114c8500ac8c801fe000000000000e8030000000000012e0300005b05722600000000003e00', '0114c8500ac8c801fe000000000000e8030000000000012e0300005b05722600000000003500', '0114c8500ac8c801fe000000000000e8030000000000012f0300005b05732600000000003700', '0114c8500ac8c801fe000000000000e8030000000000012e0332005905722600000100003400', '0114c8500ac8c801fe000000000000e8030000000000012f0300005705732600000000003600', '0114c8500ac8c801fe000000000000e8030000000000012e0332805a05722600000100003a00', '0114c8500ac8c801fe000000000000e8030000000000012f0332805c05722600000100003d00', '0114c8500ac8c801fe000000000000e8030000000000012e0300005b05722600000000003e00', '0114c8500ac8c801fe000000000000e8030000000000012f0332005905722600000100003d00', '0114c8500ac8c801fe000000000000e8030000000000012e0300005705732600000000003c00', '0114c8500ac8c801fe000000000000e8030000000000012e0300005705732600000000003600', '0114c8500ac8c801fe000000000000e8030000000000012e0300005705732600000000003900', '0114c8500ac8c801fe000000000000e8030000000000012f0332005705722600000100003c00', '0114c8500ac8c801fe000000000000e8030000000000012f0300005705722600000000003a00', '0114c8500ac8c801fe000000000000e8030000000000012f0300005705732600000000003500']}


@pytest.mark.parametrize("trial,origin,branch", [("02", 814, "strict_quiet"), ("03", 813, "bounded_indicator")])
def test_actual_post_home_payloads_replay_with_explicit_frozen_acquisition_provenance(trial, origin, branch):
    monitor, clock, transport, emitted = comparison([(origin, 0, 0)])
    packets = [bytes.fromhex(value) for value in ACTUAL_POST_HOME_PAYLOADS[trial]]
    def read(start, length, **kwargs):
        assert (start, length) == (feedback.FEEDBACK_START, feedback.FEEDBACK_LENGTH)
        assert kwargs["timeout_ms"] == 40.0
        assert transport.read_count < len(packets), "replay must not fabricate later replies"
        payload = packets[transport.read_count]
        transport.read_count += 1
        clock.sleep(.002)
        transport.arrivals.append(clock.now)
        return payload, {"response_payload_bytes": list(payload), "checksum_ok": True}
    transport.read_group = read
    record = qualify(monitor, origin=origin)
    proof = record["stationary_qualification"]
    assert proof["readiness_policy"] == "am1-post-home-qualified-window-v4"
    assert proof["readiness_policy_version"] == 4
    assert proof["readiness_branch"] == branch
    assert proof["am1_post_home_origin_raw"] == origin
    assert proof["stationary_acquisition_origin_raw"] == (815 if trial == "02" else 814)
    assert proof["stationary_acquisition_sample_index"] == (2 if trial == "02" else 3)
    assert proof["stationary_acquisition_at_monotonic_s"] == transport.arrivals[1 if trial == "02" else 2]
    assert proof["whole_phase_sample_count"] == transport.read_count == len(packets)
    if trial == "03":
        assert clock.now == pytest.approx(1.0)
        assert proof["whole_phase_position_excursion_raw"] == proof["whole_phase_position_drift_raw"] == 2
        assert proof["whole_phase_max_abs_velocity_raw"] == 100
        assert proof["position_origin_raw"] == 814 and proof["position_excursion_raw"] == proof["position_drift_raw"] == 1
        assert proof["sample_count"] == 18 and max(abs(v) for v in proof["present_velocity_raw"]) == 50
        assert len([row for row in emitted if row["phase"] == "post_home"]) == 20
    else:
        assert clock.now < 1 and proof["motion_feedback_quiet"] is True
        assert proof["sample_count"] == 5


@pytest.mark.parametrize("prefix", [[(813,150,1)], [(813,500,1)], [(813,-100,1)], [(813,100,1),(813,100,1)]])
def test_new_fallback_never_admits_unproven_prequiet_speed_or_repeated_settling(prefix):
    rows = prefix + [(814,0,0),(815,50,1),(814,0,0),(815,-50,1)] * 8
    monitor, clock, _, _ = comparison(rows)
    with pytest.raises(feedback.ComparisonRefusal):qualify(monitor, origin=813)
    assert clock.now <= 1


@pytest.mark.parametrize("prefix", [[(813,150,1)], [(813,500,1)], [(813,-100,1)], [(813,100,1),(813,100,1)]])
def test_existing_strict_quiet_priority_does_not_gain_new_prequiet_speed_rules(prefix):
    monitor, clock, _, _ = comparison(prefix + [(814,0,0)] * 6)
    proof = qualify(monitor, origin=813)["stationary_qualification"]
    assert proof["readiness_branch"] == "strict_quiet" and clock.now < 1


@pytest.mark.parametrize("rows", [
    [(813,50,0),(813,-50,1)],
    [(813,50,0),(813,-50,1)] * 9 + [(814,0,0)],
    [(813,100,1),(814,0,0),(815,50,1),(814,0,0),(816,0,0),(816,-50,1),(816,0,0),(815,50,1)],
    [(813,100,1),(814,0,0),(814,100,1),(814,0,0),(815,-50,1),(814,0,0)],
    [(813,100,1),(814,0,0),(816,50,1),(814,0,0),(814,-50,1),(814,0,0)],
])
def test_frozen_first_quiet_anchor_is_required_and_never_replaced_after_disqualification(rows):
    monitor, clock, _, _ = comparison(rows)
    with pytest.raises(feedback.ComparisonRefusal):qualify(monitor, origin=813)
    assert clock.now <= 1


@pytest.mark.parametrize("after_anchor", [False,True])
@pytest.mark.parametrize("mutation", [{"status_value":1},{"current":308},{"temperature":55},{"torque":0},{"goal_velocity":1},{"mode":0}])
def test_full_phase_hard_guards_apply_before_and_after_frozen_acquisition(after_anchor, mutation):
    monitor, clock, transport, _ = comparison([(813,100,1),(814,0,0),(814,0,0)])
    read = transport.read_group
    def mutated(*args, **kwargs):
        if transport.read_count >= (2 if after_anchor else 0):transport.mutation = mutation
        return read(*args, **kwargs)
    transport.read_group = mutated
    with pytest.raises(feedback.ComparisonRefusal):qualify(monitor, origin=813)
    if "temperature" in mutation:
        # Preserve the original three-of-five thermal policy, not an invented one-read gate.
        assert transport.read_count == (7 if after_anchor else 5)
        assert clock.now < .4
    else:
        assert transport.read_count == (3 if after_anchor else 1)
        assert clock.now < .2


@pytest.mark.parametrize("after_anchor", [False,True])
def test_original_process_zero_guard_and_temperature_origin_are_never_rebased(after_anchor):
    monitor, clock, transport, _ = comparison([(813,100,1),(814,0,0),(815,50,1),(814,0,0)])
    thermal = monitor.temperature_check.__self__.temperature
    original_origin = thermal._origin_ns
    checked = []
    def guard(record):
        checked.append(record["sample_monotonic_s"])
        if len(checked) == (3 if after_anchor else 1):
            raise feedback.ComparisonRefusal("original0.5mm whole-phase process zero guard")
    with pytest.raises(feedback.ComparisonRefusal, match="original0.5mm"):
        qualify(monitor, origin=813, on_sample=guard)
    assert len(checked) == transport.read_count == (3 if after_anchor else 1)
    assert thermal._origin_ns == original_origin


@pytest.mark.parametrize("rows", [
    [(813,100,1),(814,50,1),(815,0,1),(816,0,0)],
    [(813,100,0),(814,50,0),(815,50,0),(816,0,0)],
    [(813,100,1),(814,0,0),(815,50,1),(816,0,0)],
    [(813,100,1),(814,0,0),(814,50,1),(814,50,1),(814,50,1)],
])
def test_whole_phase_persistent_motion_counters_cross_frozen_anchor_unchanged(rows):
    monitor, clock, _, _ = comparison(rows)
    with pytest.raises(feedback.ComparisonRefusal, match="persistent|continuing"):
        qualify(monitor, origin=813)
    assert clock.now < .3


@pytest.mark.parametrize("mutation", [{"present_position":810},{"present_velocity":100},{"status_value":1}])
def test_final_inflight_reply_cannot_hide_new_disqualifier_behind_predeadline_window(mutation):
    monitor, clock, transport, _ = comparison(ORIGINAL_303)
    sleep, read = clock.sleep, transport.read_group
    def almost_deadline(seconds):
        sleep(seconds - .000001 if seconds < .05 and clock.now > .95 else seconds)
    def final_read(*args, **kwargs):
        if transport.read_count == 20:transport.mutation = mutation
        return read(*args, **kwargs)
    monitor.sleep, transport.read_group = almost_deadline, final_read
    with pytest.raises(feedback.ComparisonRefusal):qualify(monitor)
    assert transport.read_count == 21 and transport.arrivals[-1] > 1


# Exact 18 recorded physical07 grouped payloads. Test clocks and any explicitly
# appended continuation are simulated, not fabricated later physical evidence.
ACTUAL_07_POST_HOME_PAYLOADS = [
    '0114c8500ac8c801fe000000000000e8030000000000012d0364006405732500000100003b00',
    '0114c8500ac8c801fe000000000000e8030000000000012f0300005905732500000000003500',
    '0114c8500ac8c801fe000000000000e8030000000000012f0300005505722600000000003b00',
    '0114c8500ac8c801fe000000000000e8030000000000012e0300005505732500000000003400',
    '0114c8500ac8c801fe000000000000e8030000000000012f0300005505732500000000003400',
    '0114c8500ac8c801fe000000000000e8030000000000012f0332005505732500000100003700',
    '0114c8500ac8c801fe000000000000e8030000000000012f0300005905732500000000003500',
    '0114c8500ac8c801fe000000000000e8030000000000012f0332005405732500000100003500',
    '0114c8500ac8c801fe000000000000e8030000000000012e0332805805732500000100003d00',
    '0114c8500ac8c801fe000000000000e8030000000000012f0300005505732500000000003700',
    '0114c8500ac8c801fe000000000000e8030000000000012e0300005905732600000000003e00',
    '0114c8500ac8c801fe000000000000e8030000000000012f0300005505722600000000003c00',
    '0114c8500ac8c801fe000000000000e8030000000000012f0332005705722600000100003500',
    '0114c8500ac8c801fe000000000000e8030000000000012f0300005505732600000000003500',
    '0114c8500ac8c801fe000000000000e8030000000000012e0300005505732600000000003500',
    '0114c8500ac8c801fe000000000000e8030000000000012f0332805905722500000100003400',
    '0114c8500ac8c801fe000000000000e8030000000000012e0332805805732500000100003400',
    '0114c8500ac8c801fe000000000000e8030000000000012f0332005705732500000100003400',
]
ACTUAL_07_POST_HOME_ROWS = [
    (813, 100, 1), (815, 0, 0), (815, 0, 0), (814, 0, 0),
    (815, 0, 0), (815, 50, 1), (815, 0, 0), (815, 50, 1),
    (814, -50, 1), (815, 0, 0), (814, 0, 0), (815, 0, 0),
    (815, 50, 1), (815, 0, 0), (814, 0, 0),
    (815, -50, 1), (814, -50, 1), (815, 50, 1),
]


def test_actual07_reversed_velocity_payloads_do_not_end_acquisition_early():
    monitor, clock, transport, emitted = comparison([(812, 0, 0)])
    packets = [bytes.fromhex(value) for value in ACTUAL_07_POST_HOME_PAYLOADS]

    class RecordedEvidenceExhausted(Exception):
        pass

    def recorded_read(start, length, **kwargs):
        assert (start, length, kwargs['timeout_ms']) == (33, 38, 40.0)
        if transport.read_count == len(packets):
            raise RecordedEvidenceExhausted('no later physical07 replies are recorded')
        payload = packets[transport.read_count]
        transport.read_count += 1
        clock.sleep(.002)
        transport.arrivals.append(clock.now)
        return payload, {'response_payload_bytes': list(payload), 'checksum_ok': True}

    transport.read_group = recorded_read
    # Finishing the original one-second window needs later actual feedback.
    # Eighteen captured replies may demonstrate removal of an early refusal,
    # never a new qualification that physical07 itself did not achieve.
    with pytest.raises(RecordedEvidenceExhausted):
        qualify(monitor, origin=812)
    assert transport.read_count == 18 and clock.now < 1
    assert not any(r['phase']=='post_home_stationary_qualified' for r in emitted)
    acquired = [r for r in emitted if r['phase']=='post_home']
    assert [(r['present_position_raw'],r['present_velocity_raw'],r['moving']) for r in acquired] == ACTUAL_07_POST_HOME_ROWS


def test_simulated07_continuation_waits_for_original_deadline_with_frozen_band():
    # The last two quiet replies below are explicitly synthetic continuation.
    monitor, clock, transport, _ = comparison(ACTUAL_07_POST_HOME_ROWS + [(815,0,0)] * 2)
    proof = qualify(monitor, origin=812)['stationary_qualification']
    assert clock.now == pytest.approx(1.0) and transport.read_count == 20
    assert proof['readiness_branch']=='bounded_indicator'
    assert proof['motion_feedback_quiet'] is False
    assert proof['readiness_policy']=='am1-post-home-qualified-window-v4'
    assert proof['readiness_policy_version']==4
    assert proof['am1_post_home_origin_raw']==812
    assert proof['stationary_acquisition_origin_raw']==815
    assert proof['stationary_acquisition_sample_index']==2
    assert proof['whole_phase_position_excursion_raw']==3
    assert proof['whole_phase_position_drift_raw']==3
    assert proof['position_excursion_raw']==1 and proof['position_drift_raw']==0
    assert proof['whole_phase_present_velocity_raw'][-5:]==[-50,-50,50,0,0]
    assert proof['whole_phase_moving'][-5:]==[1,1,1,0,0]
    assert proof['sample_count']==19 and proof['window_s']>=.9


def test_reversing_moving_bits_without_first_quiet_cannot_qualify():
    monitor, clock, transport, _ = comparison([(808,50,1),(808,-50,1),(808,50,1)])
    with pytest.raises(feedback.ComparisonRefusal, match='stationary'):
        qualify(monitor)
    assert clock.now == pytest.approx(1.0) and transport.read_count == 20


@pytest.mark.parametrize('tail', [
    [(814,0,1)] * 3,
    [(814,50,1)] * 3,
    [(814,-50,1)] * 3,
    [(814,50,0)] * 3,
    [(814,-50,0)] * 3,
])
def test_actual_persistent_zero_velocity_moving_or_signed_velocity_still_abort(tail):
    monitor, clock, _, _ = comparison([(813,100,1),(814,0,0),*tail])
    with pytest.raises(feedback.ComparisonRefusal, match='persistent'):
        qualify(monitor, origin=813)
    assert clock.now < .3


def test_reversing_moving_bits_do_not_allow_two_tick_frozen_band():
    monitor, clock, _, _ = comparison([(813,100,1),(814,0,0),*([(816,50,1),(814,-50,1)]*10)])
    with pytest.raises(feedback.ComparisonRefusal, match='stationary'):
        qualify(monitor, origin=813)
    assert clock.now <= 1


def test_reversing_moving_bits_do_not_allow_late_first_quiet():
    monitor, clock, _, _ = comparison([(813,50,1),(813,-50,1)]*9+[(813,0,0)]*2)
    with pytest.raises(feedback.ComparisonRefusal, match='stationary'):
        qualify(monitor, origin=813)
    assert clock.now <= 1


def test_reversing_moving_bits_do_not_hide_three_real_position_steps():
    monitor, clock, _, _ = comparison([(813,100,1),(814,0,0),(815,-50,1),(816,50,1)])
    with pytest.raises(feedback.ComparisonRefusal, match='position'):
        qualify(monitor, origin=813)
    assert clock.now < .2


@pytest.mark.parametrize('mutation', [
    {'status_value':1}, {'current':308}, {'temperature':55},
    {'torque':0}, {'goal_velocity':1}, {'mode':0},
])
def test_reversing_moving_sequence_keeps_sample_health_guards(mutation):
    monitor, clock, transport, _ = comparison(ACTUAL_07_POST_HOME_ROWS+[(815,0,0)]*2)
    original_read = transport.read_group
    def changed_read(*args, **kwargs):
        if transport.read_count >= 8:
            transport.mutation=mutation
        return original_read(*args, **kwargs)
    transport.read_group=changed_read
    with pytest.raises(feedback.ComparisonRefusal):
        qualify(monitor, origin=812)
    assert clock.now < 1


def test_wrapped_one_tick_reversing_moving_sequence_keeps_fixed_acquisition():
    rows=[(4095,100,1),(0,0,0),*([(4095,-50,1),(0,50,1)]*10)]
    monitor, clock, _, _ = comparison(rows)
    proof=qualify(monitor, origin=4094)['stationary_qualification']
    assert clock.now == pytest.approx(1.0)
    assert proof['readiness_branch']=='bounded_indicator'
    assert proof['am1_post_home_origin_raw']==4094
    assert proof['stationary_acquisition_origin_raw']==0
    assert proof['stationary_acquisition_sample_index']==2
    assert proof['position_excursion_raw']==1 and abs(proof['position_drift_raw'])<=1



def test_former_three_moving_prefix_never_qualifies_without_stationary_tail():
    # Two positive encoder steps and a zero-velocity Moving1 frame are not
    # three same-direction steps. Repeating this actual-style settling prefix
    # still cannot meet the frozen one-tick band or original one-second budget.
    rows=[(813,100,1),(814,50,1),(815,0,1),(815,0,0)]
    monitor, clock, transport, _ = comparison(rows)
    with pytest.raises(feedback.ComparisonRefusal, match='stationary'):
        qualify(monitor, origin=813)
    assert clock.now == pytest.approx(1.0) and transport.read_count == 20
