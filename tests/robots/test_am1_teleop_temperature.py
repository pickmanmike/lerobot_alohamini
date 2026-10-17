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
