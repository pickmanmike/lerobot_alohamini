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
    assert thermal["samples"][-1] == (10.41, 92)
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
    assert all(window.samples[-1][0] - window.samples[0][0] >= 0.399999 for window in windows)


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
def verification_lift_boundary(operating_robot):
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
    robot.left_arm_motors = tuple(f"arm_left_{index}" for index in range(1, 7))
    robot.right_arm_motors = tuple(f"arm_right_{index}" for index in range(1, 7))
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
        174702.582195612, 174702.824100602)]
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
    assert not any(row[0] == "drain" for row in state.events)
    import json
    note = next(row for row in caught.value.__notes__ if row.startswith("AM1 observation freshness context: "))
    context = json.loads(note.split(": ", 1)[1])["arm_position_acquisitions"]
    assert set(context) == {"left", "right"}
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
    assert state.attempts == {"left": 1, "right": 0}
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
    assert state.attempts == {"left": 1, "right": 0}
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
    assert state.attempts == {"left": 1, "right": 0}
    assert not any(row[0] == "other_read" and row[2:4] == (63, 1)
                   for row in state.events[before:])
