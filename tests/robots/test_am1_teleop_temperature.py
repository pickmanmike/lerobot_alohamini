"""TEMP-01 follower parity at the original acquisition/health boundaries.

All temperatures and times here are synthetic continuations, not replayed runs.
"""

import copy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from lerobot.robots.alohamini import am1_live_teleop as teleop
from lerobot.robots.alohamini.lift_operational import (
    OperationalLift as ActualOperationalLift,
    TemperatureWindow,
)
from tests.robots.test_alohamini_lift_operational import operating_robot as operating_robot
from tests.robots.test_am1_pre_activation import activation as activation
from tests.robots.test_am1_teleop_settings import configured_robot, resting_robot


@pytest.fixture
def thermal_robot(monkeypatch):
    clock = SimpleNamespace(now=10.0)
    monkeypatch.setattr(teleop.time, "monotonic", lambda: clock.now)
    robot = SimpleNamespace(
        _am1_teleop_binding={
            "recipe": teleop.RECIPE, "run_id": str(uuid4()), "host_incarnation": str(uuid4()),
        },
        _am1_teleop_settings={},
        warnings=[],
    )
    robot._report_routine = robot.warnings.append
    robot._am1_routine_report = robot.warnings.append
    for side in ("left", "right"):
        names = tuple(f"arm_{side}_{index}" for index in range(1, 7))
        rows = {
            "Max_Temperature_Limit": dict.fromkeys(names, 70),
            "Min_Voltage_Limit": dict.fromkeys(names, 40),
            "Max_Voltage_Limit": dict.fromkeys(names, 140),
            "Present_Temperature": dict.fromkeys(names, 28),
            "Present_Voltage": dict.fromkeys(names, 120),
            "Status": dict.fromkeys(names, 0),
            "Torque_Enable": dict.fromkeys(names, 1),
            "Lock": dict.fromkeys(names, 1),
        }
        bus = SimpleNamespace(
            motors={name: SimpleNamespace(id=index, model="sts3215") for index, name in enumerate(names, 1)},
            rows=rows,
        )
        bus.sync_read = lambda register, names, *, _bus=bus, **kwargs: {
            name: _bus.rows[register][name] for name in names
        }
        setattr(robot, side + "_bus", bus)
        setattr(robot, side + "_arm_motors", names)
        robot._am1_teleop_settings[side] = {
            "registers": {key: dict(value) for key, value in rows.items()}, "timing": {},
        }
    return robot, clock


def acquire(robot, clock, when, *, motor="arm_left_1", value=28, check=True):
    clock.now = when
    robot.left_bus.rows["Present_Temperature"][motor] = value
    records = teleop.read_groups(robot, teleop.HEALTH, active=True)
    if check:
        teleop.check_health(robot, records, active=True)
    return records


def test_isolated_numeric_high_is_retained_and_tolerated_at_both_entrypoints(thermal_robot):
    robot, clock = thermal_robot
    for when in (10.0, 10.1, 10.2, 10.3, 10.4):
        acquire(robot, clock, when, check=when == 10.4)
    records = acquire(robot, clock, 10.5, value=92)
    assert records["left"]["registers"]["Present_Temperature"]["arm_left_1"] == 92
    thermal = records["left"]["temperature"]["arm_left_1"]
    assert thermal["ceiling_c"] == 70
    assert thermal["values_c"] == [28, 28, 28, 28, 92]
    assert thermal["high_count"] == thermal["numeric_outlier_count"] == 1
    assert thermal["sample_monotonic_s"] == 10.5
    assert robot.warnings


def test_three_high_occupied_slots_still_latch_at_the_frozen_role_limit(thermal_robot):
    robot, clock = thermal_robot
    for when in (10.0, 10.1, 10.2, 10.3, 10.4):
        acquire(robot, clock, when, check=when == 10.4)
    acquire(robot, clock, 10.5, value=70)
    acquire(robot, clock, 10.6, value=70)
    with pytest.raises(RuntimeError, match="temperature"):
        acquire(robot, clock, 10.7, value=70)
    assert robot._am1_teleop_fault["offending_value"] == 70
    assert robot._am1_teleop_fault["comparison"]["temperature"]["high_count"] == 3


def test_rapid_highs_cannot_multiply_votes_or_a_later_low_erase_a_slot_peak(thermal_robot):
    robot, clock = thermal_robot
    for when in (10.0, 10.1, 10.2, 10.3, 10.4):
        acquire(robot, clock, when, check=when == 10.4)
    for when in (10.41, 10.42, 10.43):
        acquire(robot, clock, when, value=92)
    records = acquire(robot, clock, 10.44, value=28)
    thermal = records["left"]["temperature"]["arm_left_1"]
    assert thermal["values_c"] == [28, 28, 28, 28, 92]
    assert thermal["samples"][-1] == (10.43, 92)
    assert thermal["high_count"] == 1
    acquire(robot, clock, 10.5, value=70)
    with pytest.raises(RuntimeError, match="temperature"):
        acquire(robot, clock, 10.6, value=70)


@pytest.mark.parametrize("problem", ["duplicate", "backward", "stale", "negative", "overflow"])
def test_missing_invalid_or_nonadvancing_data_cannot_refresh_confirmation(thermal_robot, problem):
    robot, clock = thermal_robot
    for when in (10.0, 10.1, 10.2, 10.3, 10.4):
        acquire(robot, clock, when, check=when == 10.4)
    when, value = {
        "duplicate": (10.4, 28), "backward": (10.3, 28), "stale": (11.0, 28),
        "negative": (10.5, -1), "overflow": (10.5, 256),
    }[problem]
    with pytest.raises(RuntimeError, match="temperature"):
        acquire(robot, clock, when, value=value)


def test_buses_and_ids_have_independent_frozen_role_limits_and_votes(thermal_robot):
    robot, clock = thermal_robot
    robot._am1_teleop_settings["left"]["registers"]["Max_Temperature_Limit"]["arm_left_1"] = 60
    for when in (10.0, 10.1, 10.2, 10.3, 10.4):
        acquire(robot, clock, when, check=when == 10.4)
    acquire(robot, clock, 10.5, value=60)
    robot.left_bus.rows["Present_Temperature"]["arm_left_1"] = 28
    robot.right_bus.rows["Present_Temperature"]["arm_right_1"] = 70
    acquire(robot, clock, 10.6, motor="arm_left_2", value=70)
    robot.right_bus.rows["Present_Temperature"]["arm_right_1"] = 28
    records = acquire(robot, clock, 10.7, motor="arm_left_2", value=28)
    assert records["left"]["temperature"]["arm_left_1"]["high_count"] == 1
    assert records["left"]["temperature"]["arm_left_2"]["high_count"] == 1
    assert records["right"]["temperature"]["arm_right_1"]["high_count"] == 1
    assert records["left"]["temperature"]["arm_left_1"]["ceiling_c"] == 60
    assert records["right"]["temperature"]["arm_right_1"]["ceiling_c"] == 70


def test_second_health_consumer_checks_original_receipt_without_an_extra_vote(thermal_robot):
    robot, clock = thermal_robot
    for when in (10.0, 10.1, 10.2, 10.3, 10.4):
        acquire(robot, clock, when, check=when == 10.4)
    records = acquire(robot, clock, 10.5, value=92)
    teleop.check_health(robot, records, active=True)
    assert robot._am1_teleop_temperatures["left", 1]["window"].outliers == 1
    clock.now = 11.1
    with pytest.raises(RuntimeError, match="temperature"):
        teleop.check_health(robot, records, active=True)


@pytest.mark.parametrize("change", ["run", "bus", "limit"])
def test_identity_or_limit_change_cannot_reset_an_existing_window(thermal_robot, change):
    robot, clock = thermal_robot
    acquire(robot, clock, 10.0, check=False)
    if change == "run":
        robot._am1_teleop_binding["run_id"] = str(uuid4())
    elif change == "bus":
        robot.left_bus = SimpleNamespace(**vars(robot.left_bus))
    else:
        robot._am1_teleop_settings["left"]["registers"]["Max_Temperature_Limit"]["arm_left_1"] = 99
    with pytest.raises(RuntimeError, match="temperature"):
        acquire(robot, clock, 10.1, check=False)


def test_startup_requires_five_genuine_slots_before_active_health(thermal_robot):
    robot, clock = thermal_robot
    with pytest.raises(RuntimeError, match="five fresh follower temperature slots"):
        acquire(robot, clock, 10.0)


def test_prepare_collects_genuine_off_baseline_without_fabricating_cool_slots(activation, monkeypatch):
    monkeypatch.setattr(teleop.time, "sleep", lambda seconds: setattr(
        activation.clock, "now", activation.clock.now + seconds
    ))
    robot = configured_robot(activation)
    windows = [state["window"] for state in robot._am1_teleop_temperatures.values()]
    assert len(windows) == 12
    assert all(window.ready for window in windows)
    for window in windows:
        # Five anchored occupied slots, not a minimum gap between peak receipts:
        # a genuine equal maximum may arrive late in the oldest occupied slot.
        slots = [(round(stamp * 1e9) - window._origin_ns) // 100_000_000
                 for stamp, _ in window.samples]
        assert slots == list(range(slots[0], slots[0] + 5))


def test_supported_rest_uses_follower_limit_and_retains_the_same_history_through_activation(
    activation, monkeypatch,
):
    monkeypatch.setattr(teleop.time, "sleep", lambda seconds: setattr(
        activation.clock, "now", activation.clock.now + seconds
    ))
    robot = resting_robot(activation, monkeypatch)
    originals = {key: state["window"] for key, state in robot._am1_teleop_temperatures.items()}
    # 60 C is below this follower's actual 70 C limit. Lift cold-start40/55
    # belongs to the separate lift role and must not reject follower rest.
    robot.left_bus.registers["Present_Temperature", "arm_left_shoulder_lift"] = 60
    robot.activate_motors()
    assert all(state["window"] is originals[key] for key, state in robot._am1_teleop_temperatures.items())
    assert all(state["window"].ready for state in robot._am1_teleop_temperatures.values())
    assert all(entry["qualified"] for entry in robot._am1_teleop_rest_evidence)


def test_verify_accepts_its_registered_receipt_after_a_due_refresh_on_the_other_bus(
    activation, monkeypatch,
):
    monkeypatch.setattr(teleop.time, "sleep", lambda seconds: setattr(
        activation.clock, "now", activation.clock.now + seconds
    ))
    robot = configured_robot(activation)
    originals = {key: state["window"] for key, state in robot._am1_teleop_temperatures.items()}
    for bus in (robot.left_bus, robot.right_bus):
        for name in bus.calibration:
            bus.registers["Torque_Enable", name] = bus.registers["Lock", name] = 1
        bus.on_read = lambda *_: setattr(activation.clock, "now", activation.clock.now + 0.001)
    teleop.verify_settings(robot)
    assert robot._am1_teleop_startup_temperature is True
    assert all(state["window"] is originals[key] for key, state in robot._am1_teleop_temperatures.items())
    activation.clock.now += 0.05
    teleop.poll_health(robot)
    assert robot._am1_teleop_startup_temperature is False


@pytest.mark.parametrize("confirmed", [False, True])
def test_warning_and_confirmed_fault_keep_history_through_ordinary_verified_cleanup(
    activation, confirmed,
):
    robot = configured_robot(activation)
    robot._am1_protected_cleanup = True
    name = "arm_left_shoulder_pan"
    for when in ((100.1, 100.2) if confirmed else ()):
        activation.clock.now = when
        robot.left_bus.registers["Present_Temperature", name] = 92
        teleop.read_groups(robot, ("Present_Temperature",))
    activation.clock.now = 100.3 if confirmed else 100.1
    robot.left_bus.registers["Present_Temperature", name] = 92
    if confirmed:
        with pytest.raises(RuntimeError, match="temperature") as caught:
            teleop.read_groups(robot, ("Present_Temperature",))
        first = caught.value
    else:
        teleop.read_groups(robot, ("Present_Temperature",))
        first = None
    window = robot._am1_teleop_temperatures["left", 1]["window"]
    original_samples = list(window.samples)
    assert robot._safe_shutdown(close_buses=True) == []
    assert robot._am1_shutdown_evidence["verified"]
    assert robot._am1_shutdown_evidence["buses_closed"]
    assert list(window.samples) == original_samples
    assert getattr(robot, "_am1_teleop_first_error", None) is first
    assert all(row["value"] == 0 for row in robot._am1_shutdown_evidence["readbacks"])


def test_startup_warning_is_retained_without_the_print_fallback_or_optional_sink(
    thermal_robot,
):
    robot, clock = thermal_robot
    del robot._am1_routine_report
    robot._report_routine = lambda _: pytest.fail("startup must never use the print fallback")
    for when in (10.0, 10.1, 10.2, 10.3, 10.4):
        acquire(robot, clock, when, check=when == 10.4)
    acquire(robot, clock, 10.5, value=92)
    assert robot._am1_teleop_temperature_warnings[-1]["value_c"] == 92


def test_lift_startup_callback_refreshes_followers_until_live_and_recovery_keeps_history(
    activation,
):
    robot = configured_robot(activation)
    originals = {key: state["window"] for key, state in robot._am1_teleop_temperatures.items()}
    operation = SimpleNamespace(robot=robot, temperature=TemperatureWindow(), _last_warning=-float("inf"))
    activation.clock.now = 100.1
    ActualOperationalLift._check_temperature(operation, {
        "sample_monotonic_s": 100.1, "request_duration_s": 0.001, "temperature_c": 28,
    }, False)
    assert all(state["last"][1] >= 100.1 for state in robot._am1_teleop_temperatures.values())
    for bus in (robot.left_bus, robot.right_bus):
        for name in bus.calibration:
            bus.registers["Torque_Enable", name] = bus.registers["Lock", name] = 1
    activation.clock.now = 100.2
    teleop.poll_health(robot)
    assert robot._am1_teleop_startup_temperature is False
    # The Live callback must not introduce extra group reads; ordinary Live
    # health owns acquisition. A later recovery poll retains the same windows.
    before = len(activation.events)
    activation.clock.now = 100.25
    ActualOperationalLift._check_temperature(operation, {
        "sample_monotonic_s": 100.25, "request_duration_s": 0.001, "temperature_c": 28,
    }, False)
    assert len(activation.events) == before
    activation.clock.now = 100.3
    teleop.poll_health(robot)
    assert all(state["window"] is originals[key] for key, state in robot._am1_teleop_temperatures.items())


@pytest.mark.parametrize("change", ["retimed", "changed_value"])
def test_cached_health_cannot_create_an_unregistered_original_acquisition(thermal_robot, change):
    robot, clock = thermal_robot
    for when in (10.0, 10.1, 10.2, 10.3, 10.4):
        acquire(robot, clock, when, check=when == 10.4)
    records = copy.deepcopy(acquire(robot, clock, 10.5, value=92))
    clock.now = 10.51
    for side in ("left", "right"):
        records[side]["timing"]["Present_Temperature"] = {
            "read_started_at_monotonic_s": 10.51, "read_completed_at_monotonic_s": 10.51,
        }
    if change == "changed_value":
        records["left"]["registers"]["Present_Temperature"]["arm_left_1"] = 93
    with pytest.raises(RuntimeError, match="registered"):
        teleop.check_health(robot, records, active=True)
    assert robot._am1_teleop_temperatures["left", 1]["window"].outliers == 1


def test_startup_warning_flushes_once_when_first_live_poll_has_the_existing_writer(activation):
    robot = configured_robot(activation)
    name = "arm_left_shoulder_pan"
    robot.left_bus.registers["Present_Temperature", name] = 92
    activation.clock.now = 100.1
    teleop.read_groups(robot, ("Present_Temperature",))
    warning = robot._am1_teleop_temperature_warnings[-1]
    assert warning["value_c"] == 92
    emitted = []
    robot._am1_routine_report = emitted.append
    for bus in (robot.left_bus, robot.right_bus):
        for motor in bus.calibration:
            bus.registers["Torque_Enable", motor] = bus.registers["Lock", motor] = 1
    for when in (100.2, 100.21):
        activation.clock.now = when
        teleop.poll_health(robot)
    assert len(emitted) == 1
    import json

    logged = json.loads(emitted[0].split("] ", 1)[1])
    assert logged["value_c"] == 92
    assert logged["sample_monotonic_s"] == warning["sample_monotonic_s"]
    assert logged["numeric_outlier_count"] == 1


def test_startup_failure_retains_compact_original_warning_context_before_writer_exists(activation):
    from tests.robots.test_am1_teleop_settings import fault_note

    robot = configured_robot(activation)
    name = "arm_left_shoulder_pan"
    robot.left_bus.registers["Present_Temperature", name] = 92
    activation.clock.now = 100.1
    teleop.read_groups(robot, ("Present_Temperature",))
    warning = robot._am1_teleop_temperature_warnings[-1]
    robot.left_bus.registers["Status", name] = 1
    activation.clock.now = 100.2
    with pytest.raises(RuntimeError, match="status") as caught:
        teleop.poll_health(robot)
    snapshot = fault_note(caught.value)
    assert any(
        entry["value_c"] == 92 and entry["sample_monotonic_s"] == warning["sample_monotonic_s"]
        for entry in snapshot.get("temperature_warnings", [])
    )


def test_combined16_saved_stopped_peaks_still_refuse_the_actual_missing_slot():
    # Actual retained peaks from Combined16's FIRST failure, not a complete
    # baseline or a synthetic continuation of that failed run. Earlier peaks
    # absent from the stopped snapshot are deliberately not manufactured.
    saved = [
        (155502.524492496, 32), (155502.624574612, 32), (155502.724499211, 32),
        (155502.824513753, 32), (155503.031678142, 32),
    ]
    window = TemperatureWindow(ceiling_c=70)
    for when, value in saved[:-1]:
        window.update(value, when)
    with pytest.raises(RuntimeError, match="five-slot feedback window is stale") as caught:
        window.update(saved[-1][1], saved[-1][0])
    assert list(window.samples) == saved
    assert window.outliers == 0
    assert saved[-1][0] - saved[0][0] == pytest.approx(0.507185646)
    with pytest.raises(RuntimeError) as repeated:
        window.update(32, 155503.04)
    assert repeated.value is caught.value


def test_actual_connect_calibration_scans_and_before_home_keep_due_follower_slots(
    activation, monkeypatch,
):
    from lerobot.motors import MotorCalibration
    from lerobot.motors.feetech import FeetechMotorsBus

    robot = resting_robot(activation, monkeypatch, raw=3223)
    originals = {key: state["window"] for key, state in robot._am1_teleop_temperatures.items()}
    boundary, scans = len(activation.events), []
    # Synthetic allocation across real existing operations; Combined16 only
    # measured their aggregate unrefreshed gap of0.207164389s, not each stage.
    # 24 torque/Lock writes*.0035 +lift.017164389 +96 calibration reads*.001
    # +10 before-home reads*.001 reproduces that aggregate before rest.
    for bus in (robot.left_bus, robot.right_bus):
        for name, motor in bus.motors.items():
            if name not in bus.calibration:
                bus.calibration[name] = MotorCalibration(motor.id, 0, 0, 1000, 2200)
        bus.protocol_version = 0
        bus.read_calibration = FeetechMotorsBus.read_calibration.__get__(bus)
        bus.is_connected = False
        bus.connect = lambda _bus=bus, **_: setattr(_bus, "is_connected", True)
        bus.on_read = lambda *_: setattr(activation.clock, "now", activation.clock.now + 0.001)
        bus.on_write = lambda *_: setattr(activation.clock, "now", activation.clock.now + 0.0035)
    def calibrated(bus):
        began = len(activation.events)
        result = FeetechMotorsBus.is_calibrated.fget(bus)
        scans.append((bus.side, len(activation.events) - began))
        return result

    monkeypatch.setattr(type(robot.left_bus), "is_calibrated", property(calibrated), raising=False)
    robot.lift.configure = lambda **_: setattr(activation.clock, "now", activation.clock.now + 0.017164389)
    robot.connect(calibrate=True, home_lift=True)
    assert activation.home_calls == 1
    assert all(entry["qualified"] for entry in robot._am1_teleop_rest_evidence)
    assert all(state["window"] is originals[key] for key, state in robot._am1_teleop_temperatures.items())
    assert all(state["window"].ready for state in robot._am1_teleop_temperatures.values())
    assert scans == [("left", 30), ("right", 18), ("left", 30), ("right", 18)]
    startup = activation.events[boundary:]
    home = next(index for index, event in enumerate(startup) if event[0] == "home")
    assert [
        event[2:5] for event in startup[:home]
        if event[0] == "write" and event[2] in {"Torque_Enable", "Lock"} and event[3].startswith("arm_")
    ] == [
        (register, name, 0)
        for name in (*robot.left_arm_motors, *robot.right_arm_motors)
        for register in ("Torque_Enable", "Lock")
    ]


def test_startup_calibration_false_still_short_circuits_the_right_chain(activation, monkeypatch):
    robot = configured_robot(activation)

    def calibrated(bus):
        if bus is robot.right_bus:
            pytest.fail("left calibration refusal must still short-circuit the right-chain scan")
        return False

    monkeypatch.setattr(type(robot.left_bus), "is_calibrated", property(calibrated), raising=False)
    assert robot.is_calibrated is False



def combined20_live_boundary(activation, *, right_delay=0.004, recent_highs=False):
    """Synthetic same-owner handoff using saved20 origin/peaks and real SDK reads.

    The bridging readings and right-bus4ms duration are synthetic fixture inputs,
    not additional records or a continuation of the historical failed run.
    """
    from tests.robots.test_am1_teleop_settings import reply_packet, sdk_bus

    robot = configured_robot(activation, initial_temperature=31)
    robot._am1_teleop_startup_temperature = False
    saved = [
        166142.864883104, 166142.984871457, 166143.074904165,
        166143.194007574, 166143.276878311,
    ]
    for (side, motor_id), state in robot._am1_teleop_temperatures.items():
        origin = 166119.062919393 if side == "left" else 166119.126953826
        window = TemperatureWindow(ceiling_c=70)
        first_slot = (round(saved[0] * 1e9) - round(origin * 1e9)) // 100_000_000
        bridge = [origin + index * 0.1 for index in range(first_slot)]
        isolated = 166135.653541218 if (side, motor_id) == ("left", 1) else None
        for when in sorted([*bridge, *([isolated] if isolated else [])]):
            window.update(125 if when == isolated else 31, when)
        for index, when in enumerate(saved):
            window.update(70 if recent_highs and side == "left" and motor_id == 1 and index >= 3 else 31, when)
        window.update(31, 166143.317915514)
        state["window"] = window
    narrow = []
    for side, bus, names in teleop._groups(robot):
        wide_respond = bus.port_handler.respond
        sdk_bus(robot, side, [])
        for name in names:
            bus.registers["Torque_Enable", name] = bus.registers["Lock", name] = 1
        bus.on_read = lambda *_, _side=side: setattr(
            activation.clock, "now", activation.clock.now + (
                0.003906161 if _side == "left" else right_delay
            ),
        )

        def respond(packet, _bus=bus, _wide=wide_respond, _side=side, _names=names):
            if packet[5:7] == [33, 38]:
                return _wide(packet)
            assert packet[5:7] == [63, 1]
            narrow.append((_side, activation.clock.now))
            activation.clock.now += 0.004
            return [
                byte for name in _names
                for byte in reply_packet(
                    _bus.motors[name].id, [_bus.registers["Present_Temperature", name]],
                    error=getattr(_bus, "thermal_device_error", 0),
                )
            ]

        bus.port_handler.respond = respond
        bus.port_handler.writes.clear()
    activation.clock.now = 166143.358028699
    return robot, narrow


def test_live_consumer_saved20_boundary_acquires_next_real_slot_before_strict_check(activation):
    robot, narrow = combined20_live_boundary(activation)
    originals = {key: state["window"] for key, state in robot._am1_teleop_temperatures.items()}
    origin = originals["left", 1]._origin_ns
    teleop.poll_health(robot)
    assert [side for side, _ in narrow] == ["left", "right"]
    thermal = robot._am1_teleop_temperatures["left", 1]["window"]
    assert thermal is originals["left", 1] and thermal._origin_ns == origin
    assert thermal.outliers == 1 and len(thermal.samples) == 5
    assert thermal.samples[0] == (166142.984871457, 31)
    assert thermal.samples[-1][0] == pytest.approx(166143.369934860)
    health = robot._am1_teleop_health["left"]
    assert health["timing"]["Status"]["read_completed_at_monotonic_s"] == pytest.approx(166143.361934860)
    assert health["timing"]["Present_Temperature"]["read_completed_at_monotonic_s"] == thermal._last_sample_at
    assert health["temperature"]["arm_left_shoulder_pan"]["samples"] == list(thermal.samples)
    for state in robot._am1_teleop_temperatures.values():
        state["window"].assert_fresh(activation.clock.now)
    assert all(state["window"] is originals[key] for key, state in robot._am1_teleop_temperatures.items())


def test_live_consumer_without_new_slot_keeps_exact_original_wide_reads(activation):
    robot, narrow = combined20_live_boundary(activation, right_delay=0.0005)
    teleop.poll_health(robot)
    assert narrow == []
    assert [len(bus.port_handler.writes) for bus in (robot.left_bus, robot.right_bus)] == [1, 1]
    assert robot._am1_teleop_temperatures["left", 1]["window"].samples[0][0] == 166142.864883104


@pytest.mark.parametrize("problem", ["stale_raw", "latched"])
def test_live_consumer_due_pass_refuses_lost_raw_or_latched_history_before_extra_io(
    activation, monkeypatch, problem,
):
    robot, narrow = combined20_live_boundary(activation)
    original = teleop.read_groups
    window = robot._am1_teleop_temperatures["left", 1]["window"]
    def delayed(*args, **kwargs):
        records = original(*args, **kwargs)
        if problem == "stale_raw":
            activation.clock.now += 0.501
        else:
            with pytest.raises(RuntimeError):
                window.refuse("retained thermal first cause")
        return records
    monkeypatch.setattr(teleop, "read_groups", delayed)
    with pytest.raises(RuntimeError, match="stale|retained thermal first cause"):
        teleop.poll_health(robot)
    assert narrow == []
    assert [len(bus.port_handler.writes) for bus in (robot.left_bus, robot.right_bus)] == [1, 1]
    assert window.failure is not None


def test_live_consumer_due_read_cannot_erase_confirmed_high_slots(activation):
    robot, narrow = combined20_live_boundary(activation, recent_highs=True)
    old_right = robot.right_bus.on_read
    def heat_after_left(*args):
        old_right(*args)
        robot.left_bus.registers["Present_Temperature", "arm_left_shoulder_pan"] = 70
    robot.right_bus.on_read = heat_after_left
    with pytest.raises(RuntimeError, match="majority"):
        teleop.poll_health(robot)
    window = robot._am1_teleop_temperatures["left", 1]["window"]
    assert [side for side, _ in narrow] == ["left"]
    assert sum(value >= 70 for _, value in window.samples) == 3
    assert list(window.samples)[-3:-1] == [(166143.194007574, 70), (166143.276878311, 70)]
    first = robot._am1_teleop_first_error
    writes = len(robot.left_bus.port_handler.writes)
    with pytest.raises(RuntimeError) as repeated:
        teleop.poll_health(robot)
    assert repeated.value is first and len(robot.left_bus.port_handler.writes) == writes


def test_live_consumer_due_read_retains_actual_device_error_guard(activation):
    robot, narrow = combined20_live_boundary(activation)
    robot.left_bus.thermal_device_error = 126
    with pytest.raises(RuntimeError, match="actual device error"):
        teleop.poll_health(robot)
    assert [side for side, _ in narrow] == ["left"]
    assert robot._am1_teleop_fault["group_reply"]["packets"][0]["device_error"] == 126


def test_live_consumer_stale_after_one_due_pass_still_faults_without_retry(activation, monkeypatch):
    robot, narrow = combined20_live_boundary(activation)
    original = teleop.read_groups
    def delayed(*args, **kwargs):
        records = original(*args, **kwargs)
        if args[1] == ("Present_Temperature",):
            activation.clock.now += 0.12
        return records
    monkeypatch.setattr(teleop, "read_groups", delayed)
    with pytest.raises(RuntimeError, match="five-slot feedback window is stale"):
        teleop.poll_health(robot)
    assert [side for side, _ in narrow] == ["left", "right"]
    window = robot._am1_teleop_temperatures["left", 1]["window"]
    assert activation.clock.now - window._last_sample_at < 0.5
    assert activation.clock.now - window.samples[0][0] > 0.5


@pytest.fixture
def verification_lift_boundary(operating_robot, arm_names=None):
    """Actual owner/verify entrypoints; synthetic clock aligned to Combined23 slots.

    The five offsets after the bridge retain the saved lift reply spacing and
    values. The bridge and per-register2.1ms latency are synthetic inputs, not
    missing historical samples or a continuation of the failed physical run.
    """
    robot, clock = operating_robot
    robot.connect(calibrate=False)
    operation = robot._lift_operation
    robot.config.no_follower = False
    # The existing lift-only fixture has no followers. Add synthetic register
    # chains while retaining its real lift bus/monitor/operation unchanged.
    robot.left_arm_motors = tuple(arm_names["left"]) if arm_names else tuple(f"arm_left_{index}" for index in range(1, 7))
    robot.right_arm_motors = tuple(arm_names["right"]) if arm_names else tuple(f"arm_right_{index}" for index in range(1, 7))
    robot.left_bus.motors.update({
        name: SimpleNamespace(id=index, model="sts3215")
        for index, name in enumerate(robot.left_arm_motors, 1)
    })
    robot.right_bus = SimpleNamespace(
        motors={name: SimpleNamespace(id=index, model="sts3215")
                for index, name in enumerate(robot.right_arm_motors, 1)},
        registers={}, is_connected=True,
    )
    robot.right_bus.sync_read = lambda register, names, **kwargs: {
        name: robot.right_bus.registers[register, name] for name in names
    }
    robot._am1_teleop_binding = {
        "recipe": teleop.RECIPE, "run_id": str(uuid4()), "host_incarnation": str(uuid4()),
    }
    robot._am1_teleop_settings = {}
    robot._am1_teleop_temperatures = {}
    robot._am1_teleop_startup_temperature = False
    for side, bus, names in teleop._groups(robot):
        rows = {register: {} for register in (*teleop.STATIC, *teleop.HEALTH)}
        for name in names:
            values = {register: 1 for register in teleop.STATIC}
            values.update({
                "ID": bus.motors[name].id, "Model_Number": 777,
                "Max_Temperature_Limit": 70, "Min_Voltage_Limit": 40,
                "Max_Voltage_Limit": 140, "Operating_Mode": 0,
                "Status": 0, "Present_Temperature": 31, "Present_Voltage": 120,
                "Torque_Enable": 1, "Lock": 1,
            })
            for register, value in values.items():
                bus.registers[register, name] = rows[register][name] = value
        robot._am1_teleop_settings[side] = {"registers": rows, "timing": {}}
    window = operation.temperature
    origin = window._origin_ns / 1e9
    base = origin + (int((clock.now - origin) / 0.1) + 2) * 0.1
    for index, (offset, value) in enumerate((
        (-0.084660631, 32),  # Synthetic preceding occupied slot.
        (0.015339369, 32), (0.123349347, 32), (0.231354882, 32),
        (0.325340583, 33), (0.377366133, 32),
    )):
        clock.now = base + offset - 0.002
        robot.left_bus.read_sequences["Present_Temperature", "lift_axis"] = [value]
        operation.poll()
        if index == 0:
            teleop.read_groups(robot, ("Present_Temperature",))
            robot._am1_teleop_startup_temperature = True
    for _side, bus, _names in teleop._groups(robot):
        original = bus.sync_read
        def timed_read(*args, _original=original, **kwargs):
            values = _original(*args, **kwargs)
            clock.sleep(0.0021)
            return values
        bus.sync_read = timed_read
    clock.now = base + 0.392773296
    try:
        yield robot, clock, base, window
    finally:
        # No destructor or fixture cleanup work is added to this offline case.
        robot.left_bus.is_connected = robot.right_bus.is_connected = False


def test_verify_settings_keeps_lift_slot_between_saved23_startup_boundaries(
    verification_lift_boundary, monkeypatch,
):
    robot, clock, base, window = verification_lift_boundary
    operation = robot._lift_operation
    origin = window._origin_ns
    original_poll = operation.poll
    depth = 0
    def nonrecursive_poll(*args, **kwargs):
        nonlocal depth
        assert depth == 0
        depth += 1
        try:
            return original_poll(*args, **kwargs)
        finally:
            depth -= 1
    monkeypatch.setattr(operation, "poll", nonrecursive_poll)
    teleop.verify_settings(robot)
    # Existing post-verification host refresh/first two polls, trace-aligned.
    assert clock.now < base + 0.499348681
    clock.now = base + 0.499348681
    robot._refresh_am1_startup_lift_feedback()
    operation.poll()
    clock.now = base + 0.534349109
    operation.poll()
    assert operation.temperature is window and window._origin_ns == origin
    assert window.outliers == 0 and len(window.samples) == 5
    assert any(base + 0.4 <= when < base + 0.5 for when, _ in window.samples)
    assert operation.failure is None
    assert robot._am1_teleop_active_settings


@pytest.mark.parametrize("problem,register,value,reason", [
    ("latched", None, None, "retained lift first cause"),
    ("status", "Status", 4, "status"),
    ("current", "Present_Current", 400, "current"),
    ("device", "Present_Temperature", IOError("checksum/device failure"), "grouped feedback"),
])
def test_verify_settings_lift_due_read_preserves_original_faults(
    verification_lift_boundary, problem, register, value, reason,
):
    from lerobot.robots.alohamini.lift_motor_feedback import ComparisonRefusal

    robot, _clock, _base, _window = verification_lift_boundary
    operation = robot._lift_operation
    if problem == "latched":
        operation.failure = ComparisonRefusal(reason)
    else:
        robot.left_bus.read_sequences[register, "lift_axis"] = [value]
    with pytest.raises(RuntimeError, match=reason) as caught:
        teleop.verify_settings(robot)
    assert operation.failure is caught.value
    reads = len(operation.transport.group_reads)
    with pytest.raises(RuntimeError) as repeated:
        robot._refresh_am1_startup_lift_feedback()
    assert repeated.value is caught.value and len(operation.transport.group_reads) == reads


def test_verify_settings_cannot_refresh_a_lost_raw_lift_history(verification_lift_boundary):
    robot, clock, _base, window = verification_lift_boundary
    last = window._last_sample_at
    # Followers continue genuinely; the independent lift owner has lost its RAW age.
    for offset in (0.1, 0.2, 0.3, 0.4, 0.501):
        clock.now = last + offset
        teleop.read_groups(robot, ("Present_Temperature",))
    reads = len(robot._lift_operation.transport.group_reads)
    with pytest.raises(RuntimeError, match="stale"):
        teleop.verify_settings(robot)
    assert len(robot._lift_operation.transport.group_reads) == reads
    assert window.failure is not None


@pytest.mark.parametrize("startup,has_operation,registers", [
    (True, False, ("Status",)),  # Explicit pre-home/cleanup path without an owner.
    (False, True, ("Status",)),  # Startup is sealed before steady Live/restore.
    (True, True, ("Present_Temperature",)),  # Lift callback cannot recurse.
])
def test_startup_lift_hook_skips_unowned_sealed_and_temperature_only_reads(
    thermal_robot, startup, has_operation, registers,
):
    robot, _clock = thermal_robot
    robot._am1_teleop_startup_temperature = startup
    robot._am1_teleop_temperatures = {}
    robot._lift_operation = object() if has_operation else None
    def forbidden():
        pytest.fail("unexpected lift I/O outside guarded startup register cadence")
    robot._refresh_am1_startup_lift_feedback = forbidden
    teleop.read_groups(robot, registers, active=True)


def test_saved23_empty_lift_slot_still_latches_without_an_intervening_acquisition(
    verification_lift_boundary,
):
    robot, clock, base, window = verification_lift_boundary
    robot._am1_teleop_startup_temperature = False
    teleop.verify_settings(robot)
    clock.now = base + 0.499348681
    robot._refresh_am1_startup_lift_feedback()
    robot._lift_operation.poll()
    clock.now = base + 0.534349109
    with pytest.raises(RuntimeError, match="five-slot feedback window is stale"):
        robot._lift_operation.poll()
    assert window.samples[0] == (base + 0.015339369, 32)
    assert not any(base + 0.4 <= when < base + 0.5 for when, _ in window.samples)
    assert window.outliers == 0 and window.failure is not None


def test_verify_settings_lift_due_read_cannot_purge_two_existing_high_slots(
    verification_lift_boundary,
):
    robot, clock, base, window = verification_lift_boundary
    operation = robot._lift_operation
    for offset in (0.4, 0.5):
        clock.now = base + offset - 0.002
        robot.left_bus.read_sequences["Present_Temperature", "lift_axis"] = [55]
        operation.poll()
    retained = list(window.samples)[-2:]
    assert sum(value >= 55 for _, value in window.samples) == 2
    clock.now = base + 0.6
    robot.left_bus.read_sequences["Present_Temperature", "lift_axis"] = [55]
    reads = len(operation.transport.group_reads)
    with pytest.raises(RuntimeError, match="majority") as caught:
        teleop.verify_settings(robot)
    assert operation.failure is caught.value
    assert len(operation.transport.group_reads) == reads + 1
    assert list(window.samples)[-3:-1] == retained
    assert sum(value >= 55 for _, value in window.samples) == 3


@pytest.fixture
def observation_retry_boundary(verification_lift_boundary, monkeypatch):
    """Real owner/SDK retry semantics; finite synthetic failures, not measured28 attempts."""
    import math
    import threading
    import scservo_sdk as sdk
    from lerobot.motors import Motor, MotorCalibration, MotorNormMode
    from lerobot.motors.feetech import FeetechMotorsBus
    from lerobot.motors.feetech.tables import MODEL_CONTROL_TABLE
    from lerobot.motors.motors_bus import MotorsBus
    from lerobot.robots.alohamini import alohamini_host as host
    from tests.robots.test_am1_teleop_settings import ReplyPort, reply_packet

    robot, clock, _base, window = verification_lift_boundary
    operation = robot._lift_operation
    robot._am1_teleop_startup_temperature = False
    events, written, attempts = [], [], {"left": 0, "right": 0}
    options = {"failed_attempts": {"left": 0, "right": 0}, "attempt_s": .055,
               "success_s": .004358, "block_sink": False}
    sink_gate, sink_entered = threading.Event(), threading.Event()
    owner_thread = threading.get_ident()
    for side, bus, names in teleop._groups(robot):
        bus.calibration = getattr(bus, "calibration", {})
        for name in names:
            motor_id = bus.motors[name].id
            bus.motors[name] = Motor(motor_id, "sts3215", MotorNormMode.RANGE_M100_100)
            bus.calibration[name] = MotorCalibration(motor_id, 0, 0, 1000, 2200)
            for register, value in (("Present_Position", 1600), ("Goal_Position", 1600),
                                    ("Present_Current", 2), ("Present_Velocity", 0),
                                    ("Protection_Current", 310)):
                bus.registers[register, name] = value
            robot._am1_teleop_settings[side]["registers"]["Protection_Current"][name] = 310
        normalizer = FeetechMotorsBus("unused-observation-retry", bus.motors, bus.calibration)
        for attr in ("_assert_protocol_is_compatible", "_get_motors_list", "_decode_sign",
                     "_id_to_name", "_normalize", "_unnormalize"):
            setattr(bus, attr, getattr(normalizer, attr))
        bus._has_different_ctrl_tables = normalizer._has_different_ctrl_tables
        bus.normalized_data = normalizer.normalized_data
        bus.model_ctrl_table = normalizer.model_ctrl_table
        bus.apply_drive_mode = normalizer.apply_drive_mode
        bus.model_resolution_table = normalizer.model_resolution_table
        bus.port_handler = ReplyPort()
        bus.packet_handler = sdk.PacketHandler(0)
        bus.sync_reader = sdk.GroupSyncRead(bus.port_handler, bus.packet_handler, 0, 0)
        bus._setup_sync_reader = MotorsBus._setup_sync_reader.__get__(bus)
        bus._sync_read = MotorsBus._sync_read.__get__(bus)
        bus._is_comm_success = lambda result: result == 0
        bus.sync_read = MotorsBus.sync_read.__get__(bus)

        def respond(packet, _bus=bus, _side=side, _names=names):
            assert threading.get_ident() == owner_thread
            address, width = packet[5:7]
            ids = packet[7:-1]
            position = (address, width) == MODEL_CONTROL_TABLE["sts3215"]["Present_Position"]
            if position:
                attempts[_side] += 1
                began = clock.now
                failed = attempts[_side] <= options["failed_attempts"][_side]
                clock.sleep(options["attempt_s"] if failed else options["success_s"])
                events.append(("position_attempt", _side, began, clock.now, failed))
                if failed:
                    return []  # The real SDK returns COMM_RX_TIMEOUT; actual MotorsBus decides retries.
            else:
                clock.sleep(options.get("follower_read_s", .0001) if (address, width) == (63, 1) else .0001)
                events.append(("other_read", _side, address, width, clock.now))
            packets = []
            by_id = {motor.id: name for name, motor in _bus.motors.items()}
            for motor_id in ids:
                payload = [0] * width
                for register, (start, size) in MODEL_CONTROL_TABLE["sts3215"].items():
                    if address <= start and start + size <= address + width:
                        value = _bus.registers.get((register, by_id[motor_id]), 0)
                        payload[start-address:start-address+size] = [(value >> (8*i)) & 255 for i in range(size)]
                packets.append(reply_packet(motor_id, payload))
            return [byte for packet in packets for byte in packet]

        bus.port_handler.respond = respond
    origin = window._origin_ns / 1e9
    start = origin + math.ceil((clock.now-origin)/.1)*.1
    # Align the retained five-peak/last-RAW shape with28. Earlier fixture readings
    # remain genuine; these additional values/times are explicitly synthetic.
    for offset in (.035523895, .125495254, .214515523, .323508181, .416603235):
        # Each follower retains its different original slot origin. Genuine
        #20ms acquisitions bridge these synthetic lift-aligned peak intervals.
        while clock.now + .02 < start + offset - .002:
            clock.sleep(.02)
            teleop.read_groups(robot, ("Present_Temperature",), active=True)
        clock.now = start + offset - .002
        robot.left_bus.read_sequences["Present_Temperature", "lift_axis"] = [34]
        operation.poll()
        teleop.read_groups(robot, ("Present_Temperature",), active=True)
    clock.now = start + .461495813 - .002
    robot.left_bus.read_sequences["Present_Temperature", "lift_axis"] = [34]
    operation.poll(defer_sample_log=True)
    teleop.read_groups(robot, ("Present_Temperature",), active=True)
    robot.config.use_degrees = False
    robot._am1_routine_report = lambda line: None
    originals = {key: state["window"] for key, state in robot._am1_teleop_temperatures.items()}

    def write(data):
        if options["block_sink"]:
            sink_entered.set()
            sink_gate.wait(2)
        error = options.get("sink_error")
        if error is not None:
            sink_entered.set()
            raise error
        written.append(bytes(data))
        return len(data)

    writer = host.AM1RoutineRecordWriter(write=write)
    def drain():
        assert threading.get_ident() == owner_thread
        events.append(("drain", len(operation._pending_samples)))
        writer.raise_if_failed()
        writer.emit_lift_samples(operation)
        writer.raise_if_failed()
    robot._am1_routine_lift_drain = drain
    clock.now = start + .483630817
    state = SimpleNamespace(robot=robot, clock=clock, operation=operation, window=window,
                            start=start, events=events, attempts=attempts, options=options,
                            writer=writer, written=written, sink_gate=sink_gate,
                            sink_entered=sink_entered, originals=originals)
    try:
        yield state
    finally:
        sink_gate.set()
        try:
            writer.close()
        except BaseException as error:
            if error is not options.get("sink_error"):
                raise


@pytest.mark.parametrize("sides", [("left",), ("left", "right")])
def test_observation_retry_entrypoint_preserves_both_role_slots_and_bounded_writer(
    observation_retry_boundary, sides,
):
    import time
    state = observation_retry_boundary
    for side in sides:
        state.options["failed_attempts"][side] = 3
    state.options["block_sink"] = True
    before_writes = len(state.robot.left_bus.events)
    normalizations = []
    normalize = state.robot._normalize_arm_feedback
    def counted(bus, values):
        normalizations.append(bus)
        return normalize(bus, values)
    state.robot._normalize_arm_feedback = counted
    before = time.perf_counter()
    result = state.robot.get_observation()
    assert normalizations == [state.robot.left_bus, state.robot.right_bus]
    context = state.robot._am1_arm_position_read_context
    assert set(context) == {"left", "right"} and "arm_position_acquisitions" not in result
    for side in ("left", "right"):
        assert [row["outcome"] for row in context[side]["attempts"]] == (
            ["communication_error"]*3+["ok"] if side in sides else ["ok"])
        assert context[side]["normalization"]["outcome"] == "ok"
    assert not any(row[1] == "write" for row in state.robot.left_bus.events[before_writes:] if len(row)>1)
    assert time.perf_counter()-before < .5  # Main owner cannot wait for the deliberately blocked stdout worker.
    assert not state.sink_gate.is_set()
    assert state.attempts == {side: 4 if side in sides else 1 for side in ("left", "right")}
    assert state.operation.temperature is state.window
    assert state.operation.failure is None and result["lift_axis.height_mm"] == state.operation.height_mm
    state.window.assert_fresh(state.clock.now)
    for key, original in state.originals.items():
        assert state.robot._am1_teleop_temperatures[key]["window"] is original
        original.assert_fresh(state.clock.now)
    drains = [row for row in state.events if row[0] == "drain"]
    assert drains and max(row[1] for row in drains) <= 3
    assert len(state.operation._pending_samples) <= 3
    for side in sides:
        sequence = [index for index, row in enumerate(state.events) if row[:2] == ("position_attempt", side)]
        assert len(sequence) == 4
        assert any(row[0] == "drain" for row in state.events[sequence[0]+1:sequence[-1]])


def test_saved28_original_lift_slot_hole_remains_stale_and_latched():
    origin = 174532.065592377
    window = TemperatureWindow()
    for slot in range(1701):  # Synthetic preceding bridge preserves the exact recorded origin.
        window.update(34, origin+slot*.1)
    for stamp in (174702.201116272, 174702.291087631, 174702.380107900,
                  174702.489100558, 174702.582195612, 174702.627088190):
        window.update(34, stamp)
    with pytest.raises(RuntimeError, match="five-slot feedback window is stale") as failed:
        window.update(34, 174702.824100602)
    assert list(window.samples) == [(stamp, 34) for stamp in (
        174702.291087631, 174702.380107900, 174702.489100558,
        174702.627088190, 174702.824100602)]  # Original later equal34 in slot1705.
    assert window.samples[-1][0]-window.samples[0][0] == pytest.approx(.533012971)
    assert window._origin_ns == round(origin*1e9) and window.outliers == 0
    with pytest.raises(RuntimeError) as repeated:
        window.assert_fresh(174702.825)
    assert repeated.value is failed.value


@pytest.mark.parametrize("stage", ["single_attempt", "normalization"])
def test_observation_retry_cannot_repair_a_single_uninterrupted_stall(
    observation_retry_boundary, monkeypatch, stage,
):
    state = observation_retry_boundary
    if stage == "single_attempt":
        state.options["success_s"] = .169358
    else:
        normalize = state.robot._normalize_arm_feedback
        def delayed(bus, values):
            if bus is state.robot.left_bus:
                state.clock.sleep(.169358)
            return normalize(bus, values)
        monkeypatch.setattr(state.robot, "_normalize_arm_feedback", delayed)
    with pytest.raises(RuntimeError, match="five-slot feedback window is stale") as caught:
        state.robot.get_observation()
    assert state.operation.failure is caught.value
    assert state.attempts["left"] == 1
    assert all(row[1] <= 3 for row in state.events if row[0] == "drain")
    assert state.attempts["right"] == 0  # The original strict fault precedes further ordinary I/O.
    import json
    note = next(row for row in caught.value.__notes__ if row.startswith("AM1 arm position acquisition context: "))
    context = json.loads(note.split(": ", 1)[1])["recent_acquisitions"]
    assert set(context) == {"left"}  # No fabricated receipt for the unread right side.
    assert len(context["left"]["attempts"]) == 1
    interval = (context["left"]["attempts"][0] if stage == "single_attempt" else
                context["left"]["normalization"])
    assert interval["completed_at_monotonic_s"]-interval["started_at_monotonic_s"] == pytest.approx(.169358)
    assert interval["outcome"] == "ok"


def test_observation_retry_exhaustion_still_has_exactly_four_attempts(observation_retry_boundary):
    state = observation_retry_boundary
    state.options["failed_attempts"]["left"] = 4
    with pytest.raises(ConnectionError, match="Failed to sync read"):
        state.robot.get_observation()
    assert state.attempts == {"left": 4, "right": 0}
    assert len(state.operation._pending_samples) <= 3
    assert state.operation.failure is None
    assert state.window.failure is None


@pytest.mark.parametrize("fault", ["latched", "stale_raw", "status", "current", "device", "missing_drain", "log"])
def test_observation_retry_does_not_retry_acquisition_or_logging_faults(
    observation_retry_boundary, fault,
):
    state = observation_retry_boundary
    state.options["failed_attempts"]["left"] = 3
    primary = RuntimeError("retained lift fault")
    expected = {"latched": "retained lift fault", "stale_raw": "stale", "status": "status",
                "current": "current", "device": "grouped feedback", "missing_drain": "bounded routine",
                "log": "synthetic existing writer failure"}[fault]
    reads = len(state.operation.transport.group_reads)
    if fault == "latched":
        state.operation.failure = primary
    elif fault == "stale_raw":
        state.clock.sleep(.51)
    elif fault in {"status", "current", "device"}:
        register, value = {"status": ("Status", 4), "current": ("Present_Current", 400),
                           "device": ("Present_Temperature", OSError("synthetic grouped device failure"))}[fault]
        state.robot.left_bus.read_sequences[register, "lift_axis"] = [value]
    elif fault == "missing_drain":
        del state.robot._am1_routine_lift_drain
    elif fault == "log":
        primary = ConnectionError("synthetic existing writer failure")
        state.options["sink_error"] = primary
        state.writer.submit_line("synthetic writer-fault setup")
        assert state.sink_entered.wait(1)
    with pytest.raises((RuntimeError, ConnectionError), match=expected) as caught:
        state.robot.get_observation()
    assert state.attempts == {"left": 0 if fault in {"latched", "stale_raw"} else 1, "right": 0}
    if fault in {"latched", "log"}:
        assert caught.value is primary
    if fault in {"latched", "stale_raw", "missing_drain", "log"}:
        assert len(state.operation.transport.group_reads) == reads


@pytest.mark.parametrize("role", ["lift", "follower"])
def test_observation_retry_preserves_third_distinct_high_slot(observation_retry_boundary, role):
    state = observation_retry_boundary
    for offset in (.505, .605):
        state.clock.now = state.start+offset-.002
        state.robot.left_bus.read_sequences["Present_Temperature", "lift_axis"] = [55 if role == "lift" else 34]
        if role == "follower":
            state.robot.left_bus.registers["Present_Temperature", "arm_left_1"] = 70
        state.operation.poll()
        teleop.read_groups(state.robot, ("Present_Temperature",), active=True)
    high_window = (state.window if role == "lift" else
                   state.robot._am1_teleop_temperatures["left", 1]["window"])
    retained = [(stamp, value) for stamp, value in high_window.samples if value >= high_window.ceiling_c]
    assert len(retained) == 2
    state.robot.left_bus.read_sequences["Present_Temperature", "lift_axis"] = [55 if role == "lift" else 34]
    state.clock.now = state.start+.65
    state.options["failed_attempts"]["left"] = 3
    with pytest.raises(RuntimeError, match="majority"):
        state.robot.get_observation()
    assert state.attempts == {"left": 0 if role == "follower" else 1, "right": 0}
    assert sum(value >= high_window.ceiling_c for _, value in high_window.samples) == 3
    assert all(peak in high_window.samples for peak in retained)
    assert high_window.failure is not None


@pytest.mark.parametrize("fault", ["decode", "normalization"])
def test_observation_retry_preserves_foreign_error_and_commits_no_failed_raw_map(
    observation_retry_boundary, monkeypatch, fault,
):
    state = observation_retry_boundary
    primary = ValueError("synthetic original "+fault+" fault")
    raw = {}
    calls = []
    def fail(*args, **kwargs):
        calls.append(True)
        raise primary
    if fault == "decode":
        monkeypatch.setattr(state.robot.left_bus, "_decode_sign", fail)
    else:
        monkeypatch.setattr(state.robot, "_normalize_arm_feedback", fail)
    with pytest.raises(ValueError) as caught:
        state.robot._read_arm_positions(state.robot.left_bus, state.robot.left_arm_motors, raw_positions=raw)
    assert caught.value is primary and len(calls) == 1 and raw == {}
    import json
    note = next(row for row in caught.value.__notes__ if row.startswith("AM1 arm position acquisition context: "))
    context = json.loads(note.split(": ", 1)[1])
    assert context["side"] == "left" and len(context["attempts"]) == 1
    assert context["attempts"][0]["outcome"] == ("error" if fault == "decode" else "ok")
    assert context["normalization"] is None if fault == "decode" else context["normalization"]["outcome"] == "error"
    assert state.attempts == {"left": 1, "right": 0}
    assert not any(row[0] == "drain" for row in state.events)


@pytest.mark.parametrize("mode", ["am2", "legacy", "startup"])
def test_observation_retry_leaves_other_roles_on_original_bus_retry_path(observation_retry_boundary, mode):
    state = observation_retry_boundary
    if mode == "am2":
        state.robot.config.robot_model = "alohamini2"
    elif mode == "legacy":
        state.robot._am1_teleop_binding = None
    else:
        state.robot._am1_teleop_startup_temperature = True
    state.options["failed_attempts"]["left"] = 3
    before = list(state.window.samples)
    raw = {}
    values = state.robot._read_arm_positions(state.robot.left_bus, state.robot.left_arm_motors, raw_positions=raw)
    assert len(values) == 6 and state.attempts == {"left": 4, "right": 0}
    assert list(state.window.samples) == before
    assert not any(row[0] == "drain" for row in state.events)


def test_observation_retry_rechecks_lift_slot_after_follower_acquisition_work(observation_retry_boundary):
    import math
    state = observation_retry_boundary
    origin = state.window._origin_ns/1e9
    base = origin+math.ceil((state.clock.now-origin)/.1)*.1
    state.robot._am1_routine_lift_drain()
    for _side, bus, names in teleop._groups(state.robot):
        for name in names:
            bus.registers["Present_Temperature", name] = 32
    for offset in (0, .1, .2, .3, .4):
        state.clock.now = base+offset-.002
        state.robot.left_bus.read_sequences["Present_Temperature", "lift_axis"] = [34]
        state.operation.poll()
        teleop.read_groups(state.robot, ("Present_Temperature",), active=True)
    state.clock.now = base+.49-.002
    state.robot.left_bus.read_sequences["Present_Temperature", "lift_axis"] = [34]
    state.operation.poll(defer_sample_log=True)
    assert state.window.samples[0][0] == pytest.approx(base, abs=1e-9)
    state.options.update(attempt_s=.0001, follower_read_s=.002)
    state.options["failed_attempts"]["left"] = 1
    state.clock.now = base+.4989
    # First failed PP returns at.499: lift still same slot, follower slot due.
    # Two genuine2ms follower reads cross.5; a new actual lift slot must follow.
    result = state.robot.get_observation()
    assert result["lift_axis.height_mm"] == state.operation.height_mm
    assert state.attempts == {"left": 2, "right": 1}
    assert any(stamp >= base+.5 for stamp, _ in state.window.samples)
    assert state.operation.failure is None
    state.window.assert_fresh(state.clock.now)
    for key, window in state.originals.items():
        assert state.robot._am1_teleop_temperatures[key]["window"] is window
        window.assert_fresh(state.clock.now)


def test_observation_retry_refreshes_followers_after_lift_io_crosses_their_slot(
    observation_retry_boundary, monkeypatch,
):
    state = observation_retry_boundary
    read_group = state.operation.transport.read_group
    def delayed_group(*args, **kwargs):
        state.clock.sleep(.018)  # Plus the existing2ms reply remains inside40ms.
        return read_group(*args, **kwargs)
    monkeypatch.setattr(state.operation.transport, "read_group", delayed_group)
    state.options["failed_attempts"]["left"] = 1
    state.options["attempt_s"] = .0001
    state.clock.now = state.start+.4999
    before = {key: window._last_slot for key, window in state.originals.items()}
    initial = state.start+.5
    assert all((round(initial*1e9)-window._origin_ns)//100_000_000 == before[key]
               for key, window in state.originals.items())
    result = state.robot.get_observation()
    assert result["lift_axis.height_mm"] == state.operation.height_mm
    assert state.attempts == {"left": 2, "right": 1}
    for key, window in state.originals.items():
        assert window._last_slot > before[key]
        assert window._last_sample_at > initial+.02
        window.assert_fresh(state.clock.now)
    state.window.assert_fresh(state.clock.now)


def test_observation_retry_preserves_direct_lift_pending_input_refusal(observation_retry_boundary):
    from lerobot.robots.alohamini.lift_motor_feedback import DirectSdkTransport
    state = observation_retry_boundary
    pending = bytearray([255, 255, 11])
    serial = SimpleNamespace(in_waiting=len(pending),
                             read=lambda count: bytes(pending[:count]),
                             reset_input_buffer=lambda: pending.clear())
    port = state.robot.left_bus.port_handler
    port.ser = serial
    direct = DirectSdkTransport("unused-existing-owner", port_handler=port,
                                packet_handler=state.robot.left_bus.packet_handler)
    # The real same-owner transport's guard runs before its request/capture.
    state.operation.transport.read_group = direct.read_group
    state.options["failed_attempts"]["left"] = 3
    with pytest.raises(RuntimeError, match="Unexpected pending input"):
        state.robot.get_observation()
    assert state.attempts == {"left": 1, "right": 0}
    assert not pending
    assert state.operation.failure is not None


def test_observation_retry_checks_new_queue_failure_after_bounded_drain(observation_retry_boundary):
    state = observation_retry_boundary
    state.options["block_sink"] = True
    state.writer.submit_line("occupy worker")
    assert state.sink_entered.wait(1)
    for index in range(128):
        assert state.writer.submit_line(str(index))
    state.options["failed_attempts"]["left"] = 3
    with pytest.raises(RuntimeError, match="queue full") as caught:
        state.robot.get_observation()
    state.options["sink_error"] = caught.value  # Preserve the expected original on fixture writer close.
    assert state.attempts == {"left": 1, "right": 0}
    with pytest.raises(RuntimeError) as first:
        state.writer.raise_if_failed()
    assert first.value is caught.value


def test_observation_retry_refuses_follower_raw_expiry_during_lift_before_follower_io(
    observation_retry_boundary, monkeypatch,
):
    state = observation_retry_boundary
    for offset in (.5, .6, .7, .8):
        state.clock.now = state.start+offset-.002
        state.robot.left_bus.read_sequences["Present_Temperature", "lift_axis"] = [34]
        state.operation.poll()
    read_group = state.operation.transport.read_group
    def delayed_group(*args, **kwargs):
        state.clock.sleep(.018)
        return read_group(*args, **kwargs)
    monkeypatch.setattr(state.operation.transport, "read_group", delayed_group)
    state.options["failed_attempts"]["left"] = 3
    state.options["attempt_s"] = .0001
    state.clock.now = state.start+.9499
    before = len(state.events)
    with pytest.raises(RuntimeError, match="stale"):
        state.robot.get_observation()
    assert state.attempts == {"left": 0, "right": 0}
    assert not any(row[0] == "other_read" and row[2:4] == (63, 1)
                   for row in state.events[before:])


@pytest.fixture
def command_cadence_boundary(operating_robot, monkeypatch):
    """Reuse the original SDK/owner fixture with explicitly synthetic command costs."""
    from contextlib import contextmanager

    from lerobot.motors import Motor, MotorCalibration, MotorNormMode
    from lerobot.motors.feetech.tables import MODEL_CONTROL_TABLE

    robot, clock = operating_robot
    names = {side: tuple("arm_" + side + "_" + joint for joint in
                         ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"))
             for side in ("left", "right")}
    robot.base_motors = ["base_left_wheel", "base_back_wheel", "base_right_wheel"]
    for index, name in enumerate(robot.base_motors, 8):
        robot.left_bus.motors[name] = Motor(index, "sts3215", MotorNormMode.RANGE_M100_100)
        robot.left_bus.calibration[name] = MotorCalibration(index, 0, 0, 1000, 2200)
        for register, value in (("Present_Velocity", 0), ("Present_Current", 2)):
            robot.left_bus.registers[register, name] = value
    with (
        contextmanager(verification_lift_boundary.__wrapped__)(operating_robot, arm_names=names) as prepared,
        contextmanager(observation_retry_boundary.__wrapped__)(prepared, monkeypatch) as state,
    ):
        robot.config.max_relative_target = 20.
        state.command_reads, state.command_writes = [], []
        state.profile = None
        for side, bus, _names in teleop._groups(robot):
            original = bus.port_handler.respond
            def respond(packet, _original=original, _side=side):
                address, width = packet[5:7]
                ids = tuple(packet[7:-1])
                register = next((name for name in ("Present_Position", "Present_Current", "Present_Velocity")
                                 if MODEL_CONTROL_TABLE["sts3215"][name] == (address, width)), None)
                target = ((state.profile == "current" and _side == "left" and register == "Present_Current" and len(ids) == 1)
                          or (state.profile == "trace_pp" and _side == "left" and register == "Present_Position" and len(ids) == 5)
                          or (state.profile == "base" and register == "Present_Velocity" and ids == (8, 9, 10)))
                failures = sum(row[0:3] == (_side, register, ids) and row[5] for row in state.command_reads)
                began = clock.now
                if target and failures < 3:
                    clock.sleep(.055)
                    state.command_reads.append((_side, register, ids, began, clock.now, True))
                    return []
                if state.profile == "successful_chain" and register in {"Present_Position", "Present_Current"}:
                    clock.sleep(.017)
                result = _original(packet)
                if register:
                    state.command_reads.append((_side, register, ids, began, clock.now, False))
                return result
            bus.port_handler.respond = respond
            def write(register, values, *, _side=side, _bus=bus, **options):
                state.command_writes.append((_side, register, dict(values), clock.now))
                for name, value in values.items():
                    _bus.registers[register, name] = value
                clock.sleep(.0005)
                if getattr(state, "after_write", None):
                    state.after_write(_side, register, values)
            bus.sync_write = write
        for side, bus, _names in teleop._groups(robot):
            bus.motors["arm_" + side + "_gripper"].norm_mode = MotorNormMode.RANGE_0_100
            teleop._install_group_reply_guard(robot, side, bus)
        state.action = {**{name + ".pos": value for _side, bus, motors in teleop._groups(robot)
                           for name, value in robot._normalize_arm_feedback(bus, dict.fromkeys(motors, 1600)).items()},
                        "x.vel": 0., "y.vel": 0., "theta.vel": 0., "lift_axis.vel": 0.}
        yield state


@pytest.mark.parametrize("profile", ["current", "trace_pp", "successful_chain", "base"])
def test_shared_command_and_observation_io_services_all_roles_before_slots_are_lost(command_cadence_boundary, profile):
    state = command_cadence_boundary
    robot = state.robot
    state.profile = profile
    if profile == "trace_pp":
        robot._am1_left_shoulder_evidence_enabled = True
    result = robot.send_action(state.action)
    observed = robot.get_observation()
    assert all(result[name] == value for name, value in state.action.items() if name.endswith(".pos"))
    assert len([row for row in state.command_writes if row[1] == "Goal_Position"]) == 2
    assert len([row for row in state.command_writes if row[1] == "Goal_Velocity"]) == 1
    assert observed["lift_axis.height_mm"] == state.operation.height_mm
    assert state.operation.temperature is state.window and state.operation.failure is None
    state.window.assert_fresh(state.clock.now)
    for original in state.originals.values():
        original.assert_fresh(state.clock.now)
    if profile != "successful_chain":
        failed = [row for row in state.command_reads if row[5]]
        assert len(failed) == 3
        key = failed[0][:3]
        assert len([row for row in state.command_reads if row[:3] == key]) == 4
    assert len(state.operation._pending_samples) <= 3


@pytest.mark.parametrize("stage", ["single_current", "normalization"])
def test_shared_io_cannot_repair_one_opaque_read_or_normalization_stall(command_cadence_boundary, monkeypatch, stage):
    state = command_cadence_boundary
    calls = []
    if stage == "single_current":
        port = state.robot.left_bus.port_handler
        original = port.respond
        def stalled(packet):
            if packet[5:7] == [69, 2] and not calls:
                calls.append(True)
                state.clock.sleep(.191)
            return original(packet)
        port.respond = stalled
    else:
        original = state.robot._normalize_arm_feedback
        def stalled(*args, **kwargs):
            values = original(*args, **kwargs)
            if not calls:
                calls.append(True)
                state.clock.sleep(.191)
            return values
        monkeypatch.setattr(state.robot, "_normalize_arm_feedback", stalled)
    from lerobot.robots.alohamini.lift_motor_feedback import ComparisonRefusal
    with pytest.raises((RuntimeError, ComparisonRefusal), match="stale"):
        state.robot.send_action(state.action)
    assert calls == [True]
    assert not state.command_writes


@pytest.mark.parametrize("fault", ["current", "device", "exhausted", "drain"])
def test_shared_force_current_acquisition_cannot_swallow_protected_faults(command_cadence_boundary, fault):
    from tests.robots.test_am1_teleop_settings import reply_packet
    state = command_cadence_boundary
    robot = state.robot
    port, attempts = robot.left_bus.port_handler, []
    original = port.respond
    primary = OSError("bounded original writer failure")
    if fault == "current":
        robot.left_bus.registers["Present_Current", "arm_left_gripper"] = 310
    if fault == "drain":
        state.clock.sleep(.03)
        def broken():
            raise primary
        robot._am1_routine_lift_drain = broken
    def respond(packet):
        if packet[5:7] == [69, 2]:
            attempts.append(tuple(packet[7:-1]))
            if fault == "device":
                return reply_packet(packet[7], [], 126)
            if fault == "exhausted":
                state.clock.sleep(.055)
                return []
        return original(packet)
    port.respond = respond
    with pytest.raises(Exception) as caught:
        robot.send_action(state.action)
    if fault == "drain":
        assert caught.value is primary and not attempts
    elif fault == "exhausted":
        assert isinstance(caught.value, ConnectionError) and len(attempts) == 4
    else:
        assert caught.value is robot._am1_teleop_first_error and len(attempts) == 1
        assert "current" in str(caught.value) if fault == "current" else "device" in str(caught.value)
    assert not state.command_writes


def test_shared_io_preserves_actual_position_and_trace_receipts_through_servicing(command_cadence_boundary):
    state = command_cadence_boundary
    state.profile = "successful_chain"
    state.robot._am1_left_shoulder_evidence_enabled = True
    state.robot.send_action(state.action)
    trace = state.robot._am1_left_shoulder_feedback
    currents = [row for row in state.command_reads if row[:3] == ("left", "Present_Current", (1, 2, 3, 4, 5))]
    positions = [row for row in state.command_reads if row[:3] == ("left", "Present_Position", (1, 2, 3, 4, 5))]
    assert (trace["current_read_started_at"], trace["current_read_completed_at"]) == currents[-1][3:5]
    assert (trace["position_read_started_at"], trace["position_read_completed_at"]) == positions[-1][3:5]
    observed = state.robot.get_observation()
    for side in ("left", "right"):
        original = [row for row in state.command_reads if row[:3] == (side, "Present_Position", (1, 2, 3, 4, 5, 6))][-1]
        for name in getattr(state.robot, side + "_arm_motors"):
            sample = observed["_am1_arm_feedback"]["positions"][name + ".pos"]
            assert (sample["read_started_at_monotonic_s"], sample["read_completed_at_monotonic_s"]) == original[3:5]
    assert state.clock.now > original[4]


def test_shared_current_subset_keeps_contact_hold_below_installed_hard_ceiling(command_cadence_boundary):
    state = command_cadence_boundary
    state.robot.left_bus.registers["Present_Current", "arm_left_gripper"] = 80  #520mA, below installed2015mA.
    state.action["arm_left_gripper.pos"] = 40.  # Closing intent; opening still releases the ordinary contact hold.
    result = state.robot.send_action(state.action)
    assert result["arm_left_gripper.pos"] == 47.
    assert not getattr(state.robot, "_am1_teleop_first_error", None)
    assert len([row for row in state.command_writes if row[1] == "Goal_Position"]) == 2


@pytest.mark.parametrize("boundary", ["before_left", "after_left"])
def test_shared_servicing_preserves_real_native_expiry_without_partial_cursor_commit(command_cadence_boundary, boundary):
    from lerobot.robots.alohamini.alohamini_host import AM1LocalControl
    from tests.robots.test_am1_live_teleop_native import frame, sample

    state = command_cadence_boundary
    robot = state.robot
    # Existing complete-native admission requires the supported elbow raw box.
    for side, raw in (("left", 2124), ("right", 1847)):
        bus = getattr(robot, side + "_bus")
        for register in ("Present_Position", "Goal_Position"):
            bus.registers[register, "arm_" + side + "_elbow_flex"] = raw
    identity = {key: robot._am1_teleop_binding[key] for key in ("run_id", "host_incarnation")}
    owner = {key: str(uuid4()) for key in ("service_incarnation", "controller_generation", "connection_generation")}
    control = AM1LocalControl(**identity, live=True, live_recipe=teleop.RECIPE, clock=lambda: state.clock.now)
    assert control.apply(robot, frame(identity, owner, 1))
    assert control.apply(robot, frame(identity, owner, 2, mode="active", epoch=2))
    original = sample(control, expiry=state.clock.now + .25)
    assert control.apply(robot, frame(identity, owner, 3, mode="active", epoch=2, sample=original))
    frozen, accepted = control.live_snapshot(), control.accepted_action_sequence
    state.command_writes.clear()
    before_events = len(robot.left_bus.events)
    fresh = sample(control, seq=2, expiry=state.clock.now + (.005 if boundary == "before_left" else .25),
                   source_id=original["source"]["source_id"], acquired=1.04)
    if boundary == "after_left":
        def expire(side, register, values):
            if side == "left" and register == "Goal_Position":
                state.clock.now = fresh["valid_until_monotonic_s"]
        state.after_write = expire
    assert not control.apply(robot, frame(identity, owner, 4, mode="active", epoch=2, sample=fresh))
    summary = control.live_snapshot()
    assert control.state == "paused" and summary["state"] == "expired"
    assert control.live_sample is None and control.accepted_action_sequence == accepted
    assert summary["cursor"] == frozen["cursor"]
    assert summary["reference"] == frozen["reference"] and summary["workspace"] == frozen["workspace"]
    assert summary["live_deadline_monotonic_s"] == frozen["live_deadline_monotonic_s"]
    goals = [row for row in state.command_writes if row[1] == "Goal_Position"]
    assert [row[0] for row in goals] == ([] if boundary == "before_left" else ["left"])
    stopped = {row[3] for row in robot.left_bus.events[before_events:]
               if row[1:3] == ("write", "Goal_Velocity") and row[4] == 0}
    assert set(robot.base_motors) | {"lift_axis"} <= stopped  # Actual direct safety writes, without service.


def test_saved35_original_right_slot_hole_remains_stale_and_latched():
    origin = 183724.594396945
    window = TemperatureWindow(ceiling_c=70)
    for slot in range(1702):  # Synthetic preceding bridge; original35 timestamps below remain exact.
        window.update(30, origin+slot*.1)
    stamps = (183894.801738533, 183894.923767774, 183895.009750827,
              183895.131971493, 183895.347767430)
    for stamp in stamps[:-1]:
        window.update(30, stamp)
    with pytest.raises(RuntimeError, match="five-slot feedback window is stale") as failed:
        window.update(30, stamps[-1])
    assert list(window.samples) == [(stamp, 30) for stamp in stamps]
    assert (stamps[-1]-stamps[0])*1000 == pytest.approx(546.028897)
    assert [(round(stamp*1e9)-window._origin_ns)//100_000_000 for stamp in stamps] == [1702, 1703, 1704, 1705, 1707]
    assert window.outliers == 0
    with pytest.raises(RuntimeError) as repeated:
        window.update(30, stamps[-1]+.01)
    assert repeated.value is failed.value


def test_recorded40_equal_maximum_uses_only_the_newer_actual_same_slot_receipt():
    origin = 194543.276201539
    window = TemperatureWindow(ceiling_c=70)
    # Synthetic preceding bridge only; these two actual40 receipts share slot1560.
    for slot in range(1560):
        window.update(31, origin + slot * .1)
    window.update(31, 194699.278931687)
    window.update(31, 194699.344850194)
    assert window._origin_ns == round(origin * 1e9) and window._last_slot == 1560
    assert window.samples[-1] == (194699.344850194, 31)
    assert len(window.samples) == 5 and window.outliers == 0


@pytest.mark.parametrize("ceiling", [55, 70])
def test_synthetic_equal_maxima_keep_five_original_acquisitions_without_age_waiver(ceiling):
    # Fully synthetic advancing receipts, NOT missing historical40 acquisitions.
    window = TemperatureWindow(ceiling_c=ceiling)
    for slot in range(5):
        window.update(31, slot * .1)
        window.update(31, slot * .1 + .06)
    original_origin = window._origin_ns
    result = window.update(31, .61)
    expected = [(.16, 31), (.26, 31), (.36, 31), (.46, 31), (.61, 31)]
    assert [at for at, _ in window.samples] == pytest.approx([at for at, _ in expected])
    assert [value for _, value in window.samples] == [value for _, value in expected]
    window.assert_fresh(.61)
    assert window._origin_ns == original_origin and window._last_slot == 6
    assert result["count"] == 5 and result["high_count"] == window.outliers == 0
    assert result["span_s"] == .45 and window.MAX_AGE_S == .5 and window.SLOT_S == .1


def test_equal_high_is_one_vote_and_lower_reading_cannot_borrow_its_timestamp():
    window = TemperatureWindow(ceiling_c=70)
    for slot in range(5):
        window.update(31, slot * .1)
    window.update(70, .41)
    same = window.update(70, .46)
    lower = window.update(31, .49)
    assert window.samples[-1] == (.46, 70)
    assert same["high_count"] == lower["high_count"] == 1
    assert window.outliers == 2 and len(window.samples) == 5
    window.update(70, .5)
    with pytest.raises(RuntimeError, match="3/5") as failed:
        window.update(70, .6)
    retained = list(window.samples)
    with pytest.raises(RuntimeError) as repeated:
        window.update(70, .61)
    assert repeated.value is failed.value and list(window.samples) == retained


@pytest.mark.parametrize("stamp", [.45, .44, 1.001], ids=["duplicate", "backward", "stale"])
def test_equal_reading_cannot_refresh_nonadvancing_or_stale_raw_history(stamp):
    window = TemperatureWindow(ceiling_c=70)
    for slot in range(5):
        window.update(31, slot * .1)
    window.update(31, .45)
    retained = list(window.samples)
    with pytest.raises(RuntimeError) as failed:
        window.update(31, stamp)
    assert list(window.samples) == retained
    with pytest.raises(RuntimeError) as repeated:
        window.update(31, 1.01)
    assert repeated.value is failed.value


def test_partial40_replay_still_refuses_without_invented_oldest_equal_receipts():
    origin = 194543.276201539
    window = TemperatureWindow(ceiling_c=70)
    # Synthetic bridge establishes the original anchor; oldest-slot later ties
    # were NOT retained. Reuse only saved peaks and the separately retained RAW.
    for slot in range(1557):
        window.update(31, origin + slot * .1)
    for stamp in (194698.976866344, 194699.076832658, 194699.179007021,
                  194699.278931687, 194699.344850194):
        window.update(31, stamp)
    with pytest.raises(RuntimeError, match="five-slot feedback window is stale") as failed:
        window.update(31, 194699.528991684)
    assert window.samples[0] == (194698.976866344, 31)
    assert 194699.528991684 - window.samples[0][0] == pytest.approx(.552125340)
    assert window.outliers == 0
    with pytest.raises(RuntimeError) as repeated:
        window.update(31, 194699.54)
    assert repeated.value is failed.value


def test_follower_real_health_entrypoint_accepts_fresh_equal_peak_receipts(thermal_robot):
    robot, clock = thermal_robot
    for slot in range(5):
        acquire(robot, clock, 10 + slot * .1, check=slot == 4)
        acquire(robot, clock, 10 + slot * .1 + .06, check=slot == 4)
    state = robot._am1_teleop_temperatures["left", 1]
    window = state["window"]
    origin = window._origin_ns
    records = acquire(robot, clock, 10.61)
    thermal = records["left"]["temperature"]["arm_left_1"]
    assert state["window"] is window and window._origin_ns == origin
    assert [at for at, _ in window.samples] == pytest.approx([10.16, 10.26, 10.36, 10.46, 10.61])
    assert thermal["count"] == 5 and thermal["high_count"] == 0 and thermal["ceiling_c"] == 70
    teleop.check_health(robot, records, active=True)  # Cached consumer adds no acquisition or timestamp.
    assert window.samples[-1] == (10.61, 28)


def test_equal_peak_updates_are_per_actual_follower_id_and_cannot_erase_another_high(thermal_robot):
    robot, clock = thermal_robot
    for slot in range(5):
        acquire(robot, clock, 10 + slot * .1, check=slot == 4)
    acquire(robot, clock, 10.41, value=70)
    records = acquire(robot, clock, 10.46, value=28)
    windows = {key: state["window"] for key, state in robot._am1_teleop_temperatures.items()}
    assert windows["left", 1].samples[-1] == (10.41, 70)
    assert windows["left", 2].samples[-1] == windows["right", 1].samples[-1] == (10.46, 28)
    assert windows["left", 1] is not windows["right", 1]
    assert records["left"]["temperature"]["arm_left_1"]["high_count"] == 1
    assert windows["left", 1].outliers == 1 and windows["right", 1].outliers == 0


def test_lift_actual_poll_accepts_only_its_new_equal_peak_receipts(operating_robot):
    robot, clock = operating_robot
    robot.connect(calibrate=False)
    operation = robot._lift_operation
    window = operation.temperature
    origin = window._origin_ns
    base = origin / 1e9 + (window._last_slot + 1) * .1
    robot.left_bus.registers["Present_Temperature", "lift_axis"] = 31
    try:
        completions = []
        for slot in range(5):
            for fraction in (.008, .068):
                clock.now = base + slot * .1 + fraction
                operation.poll()
                if fraction == .068:
                    completions.append(operation.last_record["sample_monotonic_s"])
        clock.now = base + .628
        operation.poll()
        assert operation.temperature is window and window._origin_ns == origin
        assert [at for at, _ in window.samples] == completions[1:] + [operation.last_record["sample_monotonic_s"]]
        assert all(value == 31 for _, value in window.samples)
        assert operation.last_record["temperature_window"]["ready"]
        assert operation.last_record["temperature_window"]["high_count"] == 0
        assert window.ceiling_c == 55 and window.MAX_AGE_S == .5
    finally:
        robot.disconnect()


@pytest.mark.parametrize("enabled", [False, True])
def test_history_hold_saved45_slot_hole_preserves_original_peaks_and_never_forges_readiness(enabled):
    """Saved45 peaks/raw receipts; the preceding1605 bridge is explicitly synthetic."""
    window = TemperatureWindow(ceiling_c=70)
    origin = 201638.116670038
    window._origin_ns = round(origin * 1e9)
    window.samples.extend((stamp, 29) for stamp in (
        201798.698457272, 201798.798457272, 201798.894473781,
        201799.002469488, 201799.098480496,
    ))
    window._last_slot = 1609
    window._last_sample_at = 201799.098480496
    window.allow_history_hold = enabled
    assert window.update(29, 201799.281493231)["ready"]
    original = list(window.samples)
    if not enabled:
        with pytest.raises(RuntimeError, match="five-slot feedback window is stale"):
            window.update(29, 201799.304482964)
        assert window.failure is not None
        return
    evidence = window.update(29, 201799.304482964)
    assert evidence["ready"] is False and window.failure is None
    assert window.history_deficit_at == 201799.304482964
    assert list(window.samples)[:-1] == original[:-1]
    assert window.samples[-1] == (201799.304482964, 29)
    assert window._origin_ns == round(origin * 1e9)
    # Natural, distinct new slots restore readiness; there is no clear/reset.
    for stamp in (201799.324, 201799.424, 201799.524, 201799.624):
        evidence = window.update(29, stamp)
    assert evidence["ready"] and window.failure is None
    assert window._origin_ns == round(origin * 1e9)


@pytest.mark.parametrize("fault", ["raw_loss", "duplicate", "backward", "invalid", "confirmed_high"])
def test_history_hold_never_softens_raw_clock_or_confirmed_heat_faults(fault):
    window = TemperatureWindow(ceiling_c=70)
    for stamp in (10., 10.1, 10.2, 10.3, 10.4):
        window.update(70 if fault == "confirmed_high" and stamp in (10.2, 10.3) else 29, stamp)
    window.allow_history_hold = True
    stamp, value = {
        "raw_loss": (10.901, 29), "duplicate": (10.4, 29), "backward": (10.39, 29),
        "invalid": (10.61, float("nan")), "confirmed_high": (10.61, 70),
    }[fault]
    with pytest.raises(RuntimeError, match="majority" if fault == "confirmed_high" else "temperature"):
        window.update(value, stamp)
    first = window.failure
    assert first is not None
    with pytest.raises(RuntimeError) as retained:
        window.update(29, stamp + .1)
    assert retained.value is first


def test_history_hold_real_follower_acquisition_keeps_incomplete_history_separate_from_fault(thermal_robot):
    robot, clock = thermal_robot
    for stamp in (10., 10.1, 10.2, 10.3, 10.4):
        acquire(robot, clock, stamp, check=stamp == 10.4)
    windows = [state["window"] for state in robot._am1_teleop_temperatures.values()]
    for window in windows:
        window.allow_history_hold = True
    records = acquire(robot, clock, 10.61)
    assert all(not row["ready"] for side in ("left", "right") for row in records[side]["temperature"].values())
    assert all(window.failure is None for window in windows)
    assert getattr(robot, "_am1_teleop_first_error", None) is None
    # Right status must still be checked after left history becomes incomplete.
    robot.right_bus.rows["Status"]["arm_right_6"] = 8
    with pytest.raises(RuntimeError, match="status fault"):
        acquire(robot, clock, 10.62)
    assert all(window.failure is None for window in windows)
    assert robot._am1_teleop_first_error is not None


@pytest.mark.parametrize("fault", [None, "nonzero", "missing", "long", "raw_expiry", "hold_failure", "failed", "resume_gap"])
def test_history_hold_real_owner_requires_actual_bounded_zero_and_never_restarts_native_clock(
    command_cadence_boundary, monkeypatch, fault,
):
    from lerobot.robots.alohamini.alohamini_host import AM1LocalControl
    from tests.robots.test_am1_live_teleop_native import frame

    state, robot = command_cadence_boundary, command_cadence_boundary.robot
    for side, raw in (("left", 2124), ("right", 1847)):
        bus = getattr(robot, side + "_bus")
        for register in ("Present_Position", "Goal_Position"):
            bus.registers[register, "arm_" + side + "_elbow_flex"] = raw
    for motor in (*robot.base_motors, "lift_axis"):
        robot.left_bus.registers["Goal_Velocity", motor] = 0
    identity = {key: robot._am1_teleop_binding[key] for key in ("run_id", "host_incarnation")}
    owner = {key: str(uuid4()) for key in ("service_incarnation", "controller_generation", "connection_generation")}
    control = AM1LocalControl(**identity, live=True, live_recipe=teleop.RECIPE, clock=lambda: state.clock.now)
    assert control.apply(robot, frame(identity, owner, 1))
    assert control.apply(robot, frame(identity, owner, 2, mode="active", epoch=2))
    windows = [state.operation.temperature, *(row["window"] for row in robot._am1_teleop_temperatures.values())]
    assert all(window.allow_history_hold is True for window in windows)
    frozen = control.live_snapshot()
    origins = [window._origin_ns for window in windows]
    state.clock.sleep(.21)  # Explicit synthetic opaque owner work; not a saved45 subcall.
    observation = robot.get_observation()
    before = state.clock.now
    original = robot.left_bus.sync_read
    zero_calls, read_error = [], ConnectionError("actual zero proof reply failed")
    def read(register, motors=None, **kwargs):
        if register == "Goal_Velocity":
            zero_calls.append(state.clock.now)
            if fault == "failed":
                raise read_error
        if register == "Goal_Velocity" and fault == "nonzero":
            # Servo retained a nonzero goal AFTER the actual direct zero request.
            robot.left_bus.registers[register, robot.base_motors[0]] = 1
        result = original(register, motors, **kwargs)
        if register == "Goal_Velocity":
            if fault == "missing":
                result.pop(next(iter(result)))
            if fault in {"long", "raw_expiry"}:
                state.clock.sleep(.041 if fault == "long" else .501)
        return result
    monkeypatch.setattr(robot.left_bus, "sync_read", read)
    hold_error = RuntimeError("actual direct zero failed")
    if fault == "hold_failure":
        monkeypatch.setattr(robot, "stop_motion", lambda: (_ for _ in ()).throw(hold_error))
    if fault is not None and fault != "resume_gap":
        with pytest.raises((RuntimeError, ConnectionError)) as caught:
            control.reconcile_thermal_history(robot, observation)
        if fault == "hold_failure":
            assert caught.value is hold_error
        if fault == "failed":
            assert caught.value is read_error and len(zero_calls) == 1
        return
    control.reconcile_thermal_history(robot, observation)
    assert control.state == "paused" and control.live_sample is None
    status = teleop.thermal_history_status(robot)
    assert status["peaks_ready"] is False and status["hold_sample_at_monotonic_s"] is not None
    assert status["deficit_started_at_monotonic_s"] <= before
    anchor, ceiling = status["deficit_started_at_monotonic_s"], status["deficit_ceiling_monotonic_s"]
    assert ceiling == min(anchor + 10, frozen["live_deadline_monotonic_s"])
    assert 0 <= status["zero_read_completed_at_monotonic_s"] - status["zero_read_started_at_monotonic_s"] <= .04
    for _ in range(8):
        state.clock.sleep(.05)
        observation = robot.get_observation()
        control.reconcile_thermal_history(robot, observation)
    status = teleop.thermal_history_status(robot)
    assert status["peaks_ready"] and status["deficit_started_at_monotonic_s"] == anchor
    assert [window._origin_ns for window in windows] == origins
    assert all(window.failure is None for window in windows)
    assert control.live_snapshot()["live_deadline_monotonic_s"] == frozen["live_deadline_monotonic_s"]
    # Real same-owner new pause/active ACK closes this episode; it is not input replay.
    assert control.apply(robot, frame(identity, owner, 3, epoch=3))
    if fault == "resume_gap":
        apply_standard = control._apply_standard
        def delayed_active(_robot, action):
            accepted = apply_standard(_robot, action)
            if action["_am1_local_control"]["mode"] == "active":
                state.clock.sleep(.21)
            return accepted
        monkeypatch.setattr(control, "_apply_standard", delayed_active)
        # Genuine collection after the opaque work replaces an original oldest
        # slot; the duration alone cannot decide the actual thirteen-role proof.
        assert control.apply(robot, frame(identity, owner, 4, mode="active", epoch=4))
        now = state.clock.now
        assert control.state == "active"
        assert all(window.ready and len(window.samples) == 5 for window in windows)
        assert max(now - window.samples[0][0] for window in windows) == pytest.approx(.47388)
        assert all(0 <= now - window._last_sample_at <= .5 for window in windows)
        assert [window._origin_ns for window in windows] == origins
        assert control.live_snapshot()["live_deadline_monotonic_s"] == frozen["live_deadline_monotonic_s"]
        assert teleop.thermal_history_status(robot)["deficit_started_at_monotonic_s"] is None
        return
    assert control.apply(robot, frame(identity, owner, 4, mode="active", epoch=4))
    assert teleop.thermal_history_status(robot)["deficit_started_at_monotonic_s"] is None



@pytest.mark.parametrize("boundary", ["before_left", "after_left"])
def test_history_hold_partial_native_action_never_commits_cursor_or_later_goal(
    command_cadence_boundary, monkeypatch, boundary,
):
    from lerobot.robots.alohamini.alohamini_host import AM1LocalControl
    from tests.robots.test_am1_live_teleop_native import frame, sample

    state, robot = command_cadence_boundary, command_cadence_boundary.robot
    for side, raw in (("left", 2124), ("right", 1847)):
        for register in ("Present_Position", "Goal_Position"):
            getattr(robot, side + "_bus").registers[register, "arm_" + side + "_elbow_flex"] = raw
    identity = {key: robot._am1_teleop_binding[key] for key in ("run_id", "host_incarnation")}
    owner = {key: str(uuid4()) for key in ("service_incarnation", "controller_generation", "connection_generation")}
    control = AM1LocalControl(**identity, live=True, live_recipe=teleop.RECIPE, clock=lambda: state.clock.now)
    assert control.apply(robot, frame(identity, owner, 1))
    assert control.apply(robot, frame(identity, owner, 2, mode="active", epoch=2))
    frozen, sequence = control.live_snapshot(), control.accepted_action_sequence
    original = sample(control, expiry=state.clock.now + .25)
    state.command_writes.clear()
    if boundary == "before_left":
        state.clock.sleep(.21)
        original["valid_until_monotonic_s"] = state.clock.now + .25
    else:
        def stall(side, register, _values):
            if side == "left" and register == "Goal_Position":
                state.after_write = None
                state.clock.sleep(.21)
        state.after_write = stall
    assert not control.apply(robot, frame(identity, owner, 3, mode="active", epoch=2, sample=original))
    summary = control.live_snapshot()
    assert control.state == "paused" and control.live_sample is None
    assert control.accepted_action_sequence == sequence
    assert "cursor" not in summary and "accepted_target" not in summary
    assert summary["reference"] == frozen["reference"] and summary["workspace"] == frozen["workspace"]
    assert summary["live_deadline_monotonic_s"] == frozen["live_deadline_monotonic_s"]
    goals = [side for side, register, _values, _at in state.command_writes if register == "Goal_Position"]
    assert goals == ([] if boundary == "before_left" else ["left"])
    proof = teleop.thermal_history_status(robot)
    assert proof["hold_sample_at_monotonic_s"] is not None and not proof["peaks_ready"]



@pytest.mark.parametrize("fault", ["current", "status", "confirmed_heat", "raw_loss"])
def test_history_hold_real_owner_cannot_downgrade_any_role_hard_fault(command_cadence_boundary, fault):
    from lerobot.robots.alohamini.alohamini_host import AM1LocalControl
    from tests.robots.test_am1_live_teleop_native import frame, sample

    state, robot = command_cadence_boundary, command_cadence_boundary.robot
    for side, raw in (("left", 2124), ("right", 1847)):
        for register in ("Present_Position", "Goal_Position"):
            getattr(robot, side + "_bus").registers[register, "arm_" + side + "_elbow_flex"] = raw
    identity = {key: robot._am1_teleop_binding[key] for key in ("run_id", "host_incarnation")}
    owner = {key: str(uuid4()) for key in ("service_incarnation", "controller_generation", "connection_generation")}
    control = AM1LocalControl(**identity, live=True, live_recipe=teleop.RECIPE, clock=lambda: state.clock.now)
    assert control.apply(robot, frame(identity, owner, 1))
    assert control.apply(robot, frame(identity, owner, 2, mode="active", epoch=2))
    state.clock.sleep(.21 if fault != "raw_loss" else .501)
    if fault == "current":
        robot.right_bus.registers["Present_Current", "arm_right_gripper"] = 310
    elif fault == "status":
        robot.right_bus.registers["Status", "arm_right_gripper"] = 8
    elif fault == "confirmed_heat":
        window = robot._am1_teleop_temperatures["right", 6]["window"]
        window.samples[-1] = (window.samples[-1][0], 70)
        window.samples[-2] = (window.samples[-2][0], 70)
        robot.right_bus.registers["Present_Temperature", "arm_right_gripper"] = 70
    original = sample(control, expiry=state.clock.now + .25)
    with pytest.raises(RuntimeError, match={"current": "current protection", "status": "status fault",
                                          "confirmed_heat": "majority", "raw_loss": "stale"}[fault]):
        control.apply(robot, frame(identity, owner, 3, mode="active", epoch=2, sample=original))
    assert control.live_snapshot().get("last_successful_write") is None



def test_history_hold_refill_never_resets_the_original_isolated_high_counter_or_origin():
    window = TemperatureWindow(ceiling_c=70)
    for stamp, value in ((10., 80), (10.1, 29), (10.2, 29), (10.3, 29), (10.4, 29)):
        window.update(value, stamp)
    origin = window._origin_ns
    window.allow_history_hold = True
    assert not window.update(29, 10.61)["ready"]
    assert window.outliers == 1
    for stamp in (10.71, 10.81, 10.91, 11.01):
        window.update(29, stamp)
    assert window.ready and window.outliers == 1 and window._origin_ns == origin
    assert len(window.samples) == 5 and window.failure is None


def test_history_hold_reconciliation_preserves_the_prior_partial_write_expiry(command_cadence_boundary):
    from lerobot.robots.alohamini.alohamini_host import AM1LocalControl
    from tests.robots.test_am1_live_teleop_native import frame, sample

    state, robot = command_cadence_boundary, command_cadence_boundary.robot
    for side, raw in (("left", 2124), ("right", 1847)):
        for register in ("Present_Position", "Goal_Position"):
            getattr(robot, side + "_bus").registers[register, "arm_" + side + "_elbow_flex"] = raw
    identity = {key: robot._am1_teleop_binding[key] for key in ("run_id", "host_incarnation")}
    owner = {key: str(uuid4()) for key in ("service_incarnation", "controller_generation", "connection_generation")}
    control = AM1LocalControl(**identity, live=True, live_recipe=teleop.RECIPE, clock=lambda: state.clock.now)
    assert control.apply(robot, frame(identity, owner, 1))
    assert control.apply(robot, frame(identity, owner, 2, mode="active", epoch=2))
    original = sample(control, expiry=state.clock.now + .25)
    assert control.apply(robot, frame(identity, owner, 3, mode="active", epoch=2, sample=original))
    frozen, accepted = control.live_snapshot(), control.accepted_action_sequence
    fresh = sample(control, seq=2, expiry=state.clock.now + .25,
                   source_id=original["source"]["source_id"], acquired=1.04)
    def expire(side, register, _values):
        if side == "left" and register == "Goal_Position":
            state.after_write = None
            state.clock.now = fresh["valid_until_monotonic_s"]
    state.after_write = expire
    assert not control.apply(robot, frame(identity, owner, 4, mode="active", epoch=2, sample=fresh))
    assert control.live_snapshot()["state"] == "expired"
    before = teleop.thermal_history_status(robot)
    assert before["deficit_started_at_monotonic_s"] is not None
    for _ in range(2):
        state.operation.emit_pending_sample()  # The ordinary owner drains each completed iteration.
        observation = robot.get_observation()
        control.reconcile_thermal_history(robot, observation)
        summary = control.live_snapshot()
        proof = teleop.thermal_history_status(robot)
        assert control.state == "paused" and summary["state"] == "expired"
        assert control.live_sample is None and control.accepted_action_sequence == accepted
        assert "cursor" not in summary and "accepted_target" not in summary
        assert summary["reference"] == frozen["reference"] and summary["workspace"] == frozen["workspace"]
        assert summary["live_deadline_monotonic_s"] == frozen["live_deadline_monotonic_s"]
        assert proof["deficit_started_at_monotonic_s"] == before["deficit_started_at_monotonic_s"]
        assert proof["deficit_ceiling_monotonic_s"] == before["deficit_ceiling_monotonic_s"]
        assert proof["hold_sample_at_monotonic_s"] is not None


def test_history_hold_entry_preserves_expiry_of_the_original_stored_sample(command_cadence_boundary):
    from lerobot.robots.alohamini.alohamini_host import AM1LocalControl
    from tests.robots.test_am1_live_teleop_native import frame, sample

    state, robot = command_cadence_boundary, command_cadence_boundary.robot
    for side, raw in (("left", 2124), ("right", 1847)):
        for register in ("Present_Position", "Goal_Position"):
            getattr(robot, side + "_bus").registers[register, "arm_" + side + "_elbow_flex"] = raw
    identity = {key: robot._am1_teleop_binding[key] for key in ("run_id", "host_incarnation")}
    owner = {key: str(uuid4()) for key in ("service_incarnation", "controller_generation", "connection_generation")}
    control = AM1LocalControl(**identity, live=True, live_recipe=teleop.RECIPE, clock=lambda: state.clock.now)
    assert control.apply(robot, frame(identity, owner, 1))
    assert control.apply(robot, frame(identity, owner, 2, mode="active", epoch=2))
    original = sample(control, expiry=state.clock.now + .25)
    assert control.apply(robot, frame(identity, owner, 3, mode="active", epoch=2, sample=original))
    frozen, accepted = control.live_snapshot(), control.accepted_action_sequence
    # Synthetic owner work outlasts the STORED original sample, not the incoming one.
    state.clock.now = original["valid_until_monotonic_s"]
    fresh = sample(control, seq=2, expiry=state.clock.now + .25,
                   source_id=original["source"]["source_id"], acquired=1.04)
    state.command_writes.clear()
    assert not control.apply(robot, frame(identity, owner, 4, mode="active", epoch=2, sample=fresh))
    assert control.live_snapshot()["state"] == "expired"
    assert control.live_sample is None and control.accepted_action_sequence == accepted
    proof = teleop.thermal_history_status(robot)
    anchor, ceiling = proof["deficit_started_at_monotonic_s"], proof["deficit_ceiling_monotonic_s"]
    assert anchor is not None and ceiling == min(anchor + 10, frozen["live_deadline_monotonic_s"])
    for _ in range(2):
        state.operation.emit_pending_sample()  # The ordinary owner drains each completed iteration.
        observation = robot.get_observation()
        control.reconcile_thermal_history(robot, observation)
        proof = teleop.thermal_history_status(robot)
        summary = control.live_snapshot()
        assert control.state == "paused" and summary["state"] == "expired"
        assert control.live_sample is None and control.accepted_action_sequence == accepted
        assert "cursor" not in summary and "accepted_target" not in summary
        assert proof["deficit_started_at_monotonic_s"] == anchor
        assert proof["deficit_ceiling_monotonic_s"] == ceiling
        assert summary["reference"] == frozen["reference"] and summary["workspace"] == frozen["workspace"]
        assert summary["live_deadline_monotonic_s"] == frozen["live_deadline_monotonic_s"]
    assert not any(register == "Goal_Position" for _side, register, _values, _at in state.command_writes)


@pytest.mark.parametrize("active", [None, True], ids=["startup-unchanged", "live"])
@pytest.mark.parametrize("value", [28, 70], ids=["equal", "lower-than-retained-high"])
def test_live_cadence_collects_real_within_slot_receipts_without_new_votes(thermal_robot, active, value):
    robot, clock = thermal_robot
    for stamp in (10., 10.1, 10.2, 10.3, 10.4):
        acquire(robot, clock, stamp, value=value if stamp == 10.4 else 28, check=stamp == 10.4)
    window = robot._am1_teleop_temperatures["left", 1]["window"]
    original_peak, origin = window.samples[-1], window._origin_ns
    robot.left_bus.rows["Present_Temperature"]["arm_left_1"] = 28
    clock.now = 10.46
    teleop._refresh_due_temperatures(robot, active=active)
    assert window._last_sample_at == (10.46 if active is True else 10.4)
    assert window.samples[-1] == ((10.46, 28) if active is True and value == 28 else original_peak)
    assert len(window.samples) == 5 and window._origin_ns == origin
    assert sum(t >= 70 for _, t in window.samples) == int(value == 70)


def _live_cadence_owner(state, *, admit_active=True):
    """Actual owner/SDK with a finite synthetic sequence of genuine role reads."""
    from lerobot.robots.alohamini.alohamini_host import AM1LocalControl
    from tests.robots.test_am1_live_teleop_native import frame

    robot = state.robot
    for side, raw in (("left", 2124), ("right", 1847)):
        for register in ("Present_Position", "Goal_Position"):
            getattr(robot, side + "_bus").registers[register, "arm_" + side + "_elbow_flex"] = raw
    for motor in (*robot.base_motors, "lift_axis"):
        robot.left_bus.registers["Goal_Velocity", motor] = 0
    identity = {key: robot._am1_teleop_binding[key] for key in ("run_id", "host_incarnation")}
    owner = {key: str(uuid4()) for key in ("service_incarnation", "controller_generation", "connection_generation")}
    control = AM1LocalControl(**identity, live=True, live_recipe=teleop.RECIPE, clock=lambda: state.clock.now)
    assert control.apply(robot, frame(identity, owner, 1))
    if not admit_active:
        return control, identity, owner, None
    assert control.apply(robot, frame(identity, owner, 2, mode="active", epoch=2))
    base = state.clock.now + .01
    for index in range(5):
        state.operation.emit_pending_sample()
        state.clock.now = base + index * .1
        state.operation.poll(defer_sample_log=True)
        teleop.read_groups(robot, ("Present_Temperature",), active=True)
    assert teleop.thermal_history_status(robot)["deficit_started_at_monotonic_s"] is None
    return control, identity, owner, base


@pytest.mark.parametrize("consumer", ["admission", "before_write"])
def test_live_cadence_due_batch_precedes_cached_all_role_admission(command_cadence_boundary, consumer):
    from tests.robots.test_am1_live_teleop_native import frame

    state = command_cadence_boundary
    control, identity, owner, base = _live_cadence_owner(state)
    windows = [state.operation.temperature, *(row["window"] for row in state.robot._am1_teleop_temperatures.values())]
    origins = [window._origin_ns for window in windows]
    state.clock.now = base + .51  # Cached oldest peaks expired; last RAW is still fresh.
    if consumer == "admission":
        assert control.apply(state.robot, frame(identity, owner, 3, mode="active", epoch=2))
    else:
        teleop.before_write(control, state.robot)
    proof = teleop.thermal_history_status(state.robot)
    assert proof["peaks_ready"] and proof["deficit_started_at_monotonic_s"] is None
    assert all(window._last_sample_at >= base + .51 for window in windows)
    assert [window._origin_ns for window in windows] == origins
    assert all(window.failure is None for window in windows)
    assert not state.robot._am1_thermal_history_episode


@pytest.mark.parametrize("delay", [.21, .501], ids=["missing-history", "expired-raw"])
def test_live_cadence_incomplete_batch_cannot_acknowledge_or_write(command_cadence_boundary, delay):
    from tests.robots.test_am1_live_teleop_native import frame

    state = command_cadence_boundary
    control, identity, owner, base = _live_cadence_owner(state)
    frozen = control.live_snapshot()
    state.clock.now = base + .4 + delay
    state.command_writes.clear()
    if delay > .5:
        with pytest.raises(RuntimeError, match="stale"):
            control.apply(state.robot, frame(identity, owner, 3, mode="active", epoch=2))
    else:
        assert not control.apply(state.robot, frame(identity, owner, 3, mode="active", epoch=2))
        proof = teleop.thermal_history_status(state.robot)
        assert not proof["peaks_ready"] and proof["hold_sample_at_monotonic_s"] is not None
        assert proof["deficit_ceiling_monotonic_s"] == min(
            proof["deficit_started_at_monotonic_s"] + 10, frozen["live_deadline_monotonic_s"])
    assert not any(register == "Goal_Position" for _side, register, _values, _at in state.command_writes)
    assert control.live_snapshot()["live_deadline_monotonic_s"] == frozen["live_deadline_monotonic_s"]


def test_live_cadence_ack_annotation_never_acquires_future_original_proof(command_cadence_boundary, monkeypatch):
    from lerobot.robots.alohamini.alohamini_host import AM1_LOCAL_FEEDBACK_KEY

    state = command_cadence_boundary
    control, _identity, _owner, _base = _live_cadence_owner(state)
    acquired_at = state.clock.now
    for _side, bus, _names in teleop._groups(state.robot):
        monkeypatch.setattr(bus, "sync_read", lambda *a, **k: pytest.fail("ACK annotation performed device I/O"))
    result = {}
    control.annotate(result, acquired_at=acquired_at)
    proof = result[AM1_LOCAL_FEEDBACK_KEY]["thermal_history"]
    assert proof["raw_sample_at_monotonic_s"] <= acquired_at
    assert state.clock.now == acquired_at


@pytest.mark.parametrize("delay", [.11, .21, .501], ids=["due-batch", "missing-history", "expired-raw"])
def test_live_cadence_active_ack_requalifies_after_actual_owner_work(command_cadence_boundary, monkeypatch, delay):
    from tests.robots.test_am1_live_teleop_native import frame

    state = command_cadence_boundary
    control, identity, owner, base = _live_cadence_owner(state)
    state.clock.now = base + .43
    original = control._apply_standard
    def delayed(robot, action):
        accepted = original(robot, action)
        state.clock.sleep(delay)  # Explicit synthetic work before active ACK; no invented historical subcall.
        return accepted
    monkeypatch.setattr(control, "_apply_standard", delayed)
    if delay > .5:
        with pytest.raises(RuntimeError, match="stale"):
            control.apply(state.robot, frame(identity, owner, 3, mode="active", epoch=2))
    else:
        accepted = control.apply(state.robot, frame(identity, owner, 3, mode="active", epoch=2))
        assert accepted is (delay == .11)
        proof = teleop.thermal_history_status(state.robot)
        if accepted:
            assert proof["peaks_ready"] and proof["raw_sample_at_monotonic_s"] >= base + .54
            assert proof["deficit_started_at_monotonic_s"] is None
        else:
            assert control.state == "paused" and not proof["peaks_ready"]
            assert proof["hold_sample_at_monotonic_s"] is not None


def test_live_cadence_write_checks_all_roles_after_calibration_work(command_cadence_boundary, monkeypatch):
    state = command_cadence_boundary
    control, _identity, _owner, base = _live_cadence_owner(state)
    state.clock.now = base + .43
    original = state.robot._am1_activation_calibration
    delayed = False
    def calibration(bus, name):
        nonlocal delayed
        value = original(bus, name)
        if not delayed:
            delayed = True
            state.clock.sleep(.21)  # Synthetic CPU work between collection and the real write guard.
        return value
    monkeypatch.setattr(state.robot, "_am1_activation_calibration", calibration)
    with pytest.raises(teleop.ThermalHistoryHoldRequiredError):
        teleop.before_write(control, state.robot)
    assert not teleop.thermal_history_status(state.robot)["peaks_ready"]


def test_live_cadence_repeated_real_shared_batches_keep_all_original_role_histories(command_cadence_boundary):
    state = command_cadence_boundary
    control, _identity, _owner, base = _live_cadence_owner(state)
    windows = [state.operation.temperature, *(row["window"] for row in state.robot._am1_teleop_temperatures.values())]
    origins = [window._origin_ns for window in windows]
    for offset in range(45, 201, 5):
        state.clock.now = base + offset / 100
        teleop.before_write(control, state.robot)
        proof = teleop.thermal_history_status(state.robot)
        assert proof["peaks_ready"] and proof["deficit_started_at_monotonic_s"] is None
        assert [window._origin_ns for window in windows] == origins
        assert all(len(window.samples) == 5 for window in windows)
    assert state.operation.failure is None


def test_live_cadence_first_active_ack_cannot_skip_original_raw_revalidation(command_cadence_boundary, monkeypatch):
    from tests.robots.test_am1_live_teleop_native import frame

    state = command_cadence_boundary
    control, identity, owner, _base = _live_cadence_owner(state, admit_active=False)
    assert not getattr(state.robot, "_am1_thermal_history_enabled", False)
    original = control._apply_standard
    def delayed(robot, action):
        accepted = original(robot, action)
        state.clock.sleep(.501)
        return accepted
    monkeypatch.setattr(control, "_apply_standard", delayed)
    with pytest.raises(RuntimeError, match="stale"):
        control.apply(state.robot, frame(identity, owner, 2, mode="active", epoch=2))


def test_reconcile_services_saved58_due_lift_before_history_permission(command_cadence_boundary, monkeypatch):
    """Observed58 ages/next-read interval with explicitly synthetic compatible middle slots."""
    state = command_cadence_boundary
    control, _identity, _owner, _base = _live_cadence_owner(state)
    observation = state.robot.get_observation()
    target = state.clock.now + .06
    # Only oldest peak, last RAW, deficit and next RAW are exact saved facts.
    # The compatible origin/middle slots below are controlled fixture choices.
    ages = ((.506547105, 33), (.35, 32), (.25, 33), (.15, 33), (.056513387, 32))
    replay = TemperatureWindow()
    replay.allow_history_hold = True
    replay._origin_ns = round((target - .609) * 1e9)
    for age, value in ages:
        replay.update(value, target - age)
    read_group = state.operation.transport.read_group
    def delayed_group(*args, **kwargs):
        # Synthetic bounded work reproduces the observed 18.133514 ms interval;
        # this does not identify the historical wire/request cost.
        state.clock.sleep(.016133514)
        return read_group(*args, **kwargs)
    monkeypatch.setattr(state.operation.transport, "read_group", delayed_group)
    window = state.operation.temperature
    window.__dict__.update(replay.__dict__)  # Fixture setup; production never resets a window.
    origins = [w._origin_ns for w in [window, *(v["window"] for v in state.robot._am1_teleop_temperatures.values())]]
    deadline = control.live_snapshot()["live_deadline_monotonic_s"]
    state.clock.now = target
    assert state.clock.now - window.samples[0][0] == pytest.approx(.506547105, abs=1e-9)
    assert state.clock.now - window._last_sample_at == pytest.approx(.056513387, abs=1e-9)
    state.command_writes.clear()
    control.reconcile_thermal_history(state.robot, observation)
    assert state.robot._am1_thermal_history_episode is None
    assert control.state == "active" and window.ready and window.failure is None
    assert window._last_sample_at == pytest.approx(target + .018133514, abs=1e-9)
    assert len(window.samples) == 5
    assert [w._origin_ns for w in [window, *(v["window"] for v in state.robot._am1_teleop_temperatures.values())]] == origins
    assert window.outliers == 0 and not state.command_writes
    assert control.live_snapshot()["live_deadline_monotonic_s"] == deadline


@pytest.mark.parametrize("guard", ["stale-raw", "expired-episode", "first-fault", "nested-collection"])
def test_history_permission_collection_preserves_pre_io_guards(command_cadence_boundary, monkeypatch, guard):
    state = command_cadence_boundary
    _live_cadence_owner(state)
    state.clock.sleep(.06)
    expected = None
    if guard == "stale-raw":
        state.clock.sleep(.501)
    elif guard == "expired-episode":
        state.robot._am1_thermal_history_episode = {
            "started": state.clock.now - 10, "ceiling": state.clock.now,
            "held_goals": None, "hold_sample": None, "zero_started": None, "zero_completed": None,
        }
    elif guard == "first-fault":
        expected = RuntimeError("retained first owner fault")
        state.robot._am1_teleop_first_error = expected
    else:
        state.robot._am1_thermal_history_collecting = True
    for _side, bus, _names in teleop._groups(state.robot):
        monkeypatch.setattr(bus, "sync_read", lambda *a, **k: pytest.fail("guard allowed device I/O"))
    with pytest.raises(RuntimeError) as error:
        teleop.thermal_history_status(state.robot, collect_due=True)
    if expected is not None:
        assert error.value is expected
    else:
        assert any(word in str(error.value) for word in ("stale", "deadline", "nested"))


@pytest.mark.parametrize("registered", [False, True], ids=["original-window-deficit", "existing-episode"])
def test_history_permission_collection_keeps_original_deficit_after_refill(command_cadence_boundary, registered):
    state = command_cadence_boundary
    _live_cadence_owner(state)
    state.clock.sleep(.06)
    window = state.operation.temperature
    started = state.clock.now - .01
    window.history_deficit_at = started
    if registered:
        state.robot._am1_thermal_history_episode = {
            "started": started, "ceiling": started + 10,
            "held_goals": None, "hold_sample": None, "zero_started": None, "zero_completed": None,
        }
    origin, outliers = window._origin_ns, window.outliers
    proof = teleop.thermal_history_status(state.robot, collect_due=True)
    assert proof["peaks_ready"] and window.history_deficit_at is None
    assert proof["deficit_started_at_monotonic_s"] == started
    assert proof["deficit_ceiling_monotonic_s"] == started + 10
    assert window._origin_ns == origin and window.outliers == outliers
    assert state.robot._am1_thermal_history_collecting is False


def test_history_permission_collection_rejects_recursive_drain_before_nested_bus_io(command_cadence_boundary):
    state = command_cadence_boundary
    _live_cadence_owner(state)
    state.clock.sleep(.06)
    state.robot._am1_routine_lift_drain = lambda: teleop.thermal_history_status(state.robot, collect_due=True)
    state.command_reads.clear()
    with pytest.raises(RuntimeError, match="nested"):
        teleop.thermal_history_status(state.robot, collect_due=True)
    assert not state.command_reads
    assert state.robot._am1_thermal_history_collecting is False
