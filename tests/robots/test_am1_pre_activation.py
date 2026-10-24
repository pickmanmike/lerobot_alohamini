"""AM1 pre-activation admission through the actual owner with fake register I/O."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from lerobot.motors import Motor, MotorCalibration, MotorNormMode
from lerobot.robots.alohamini import alohamini as owner, lift_operational


class Bus:
    def __init__(self, side, events):
        names = [
            f"arm_{side}_{joint}"
            for joint in (
                "shoulder_pan",
                "shoulder_lift",
                "elbow_flex",
                "wrist_flex",
                "wrist_roll",
                "gripper",
            )
        ]
        self.motors = {
            name: Motor(index, "sts3215", MotorNormMode.RANGE_M100_100) for index, name in enumerate(names, 1)
        }
        self.calibration = {
            name: MotorCalibration(motor.id, 0, 0, 1000, 2200) for name, motor in self.motors.items()
        }
        if side == "left":
            self.motors.update(
                {
                    name: Motor(index, "sts3215", MotorNormMode.RANGE_M100_100)
                    for index, name in enumerate(
                        ("base_left_wheel", "base_back_wheel", "base_right_wheel", "lift_axis"), 8
                    )
                }
            )
        self.registers = {}
        for name in self.motors:
            self.registers.update(
                {
                    (register, name): value
                    for register, value in (
                        ("Min_Position_Limit", 1000),
                        ("Max_Position_Limit", 2200),
                        ("Homing_Offset", 0),
                        ("Present_Position", 1600),
                        ("Torque_Enable", 0),
                        ("Goal_Position", 1600),
                        ("Goal_Velocity", 0),
                        ("Lock", 0),
                    )
                }
            )
        self.side, self.events, self.read_faults = side, events, {}
        self.readback_mismatch = None
        self.on_read = self.on_write = None
        self.read_overrides = {}
        self.is_connected = True

    def read(self, register, name, *, normalize=True, num_retry=3):
        self.events.append(("read", self.side, register, name, normalize, num_retry))
        if self.on_read is not None:
            self.on_read(register, name)
        if (register, name) in self.read_overrides:
            return self.read_overrides[(register, name)]
        fault = self.read_faults.get((register, name))
        if fault is not None:
            raise fault
        value = self.registers[(register, name)]
        if (register, name) == self.readback_mismatch:
            return value + 1
        return value

    def write(self, register, name, value, *, normalize=True, num_retry=3):
        self.events.append(("write", self.side, register, name, value, normalize, num_retry))
        self.registers[(register, name)] = value
        if self.on_write is not None:
            self.on_write(register, name, value)

    def disconnect(self, *, disable_torque):
        self.events.append(("disconnect", self.side, disable_torque))
        self.is_connected = False


@pytest.fixture
def activation(monkeypatch):
    events = []
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(owner.time, "monotonic", lambda: clock.now)
    robot = object.__new__(owner.AlohaMini)
    robot.config = SimpleNamespace(robot_model="alohamini1")
    robot.left_bus, robot.right_bus = Bus("left", events), Bus("right", events)
    robot.left_arm_motors = list(robot.left_bus.calibration)
    robot.right_arm_motors = list(robot.right_bus.calibration)
    robot.base_motors = [name for name in robot.left_bus.motors if name.startswith("base_")]
    robot.cameras = {}
    robot.lift = SimpleNamespace(
        cfg=SimpleNamespace(name="lift_axis"), mark_unhomed=lambda: events.append(("mark_unhomed",))
    )
    state = SimpleNamespace(robot=robot, events=events, home_change=None, home_calls=0, clock=clock)

    def case_time(): return clock.now

    # Lift IO is replaced here; retain the original-slot scheduler interface.
    class Operation:
        def __init__(self, source):
            self.lift = source.lift
            self.failure = None
            self.last_record = {"sample_monotonic_s": case_time()}
            self.temperature = SimpleNamespace(
                SLOT_S=.1, _origin_ns=round(case_time() * 1e9), _last_slot=0,
                assert_fresh=lambda now: None,
            )

        def raise_if_faulted(self):
            if self.failure is not None:
                raise self.failure

        def poll(self):
            self.last_record = {"sample_monotonic_s": case_time()}
            self.temperature._last_slot = (round(case_time() * 1e9) - self.temperature._origin_ns) // 100_000_000

        def start(self):
            state.home_calls += 1
            events.append(("home",))
            robot.left_bus.write("Torque_Enable", "lift_axis", 1, normalize=False)
            if state.home_change is not None:
                state.home_change(robot)
            self.poll()
            return "home-result"

        def cleanup_readback(self):
            events.append(("lift_cleanup",))

    monkeypatch.setattr(lift_operational, "OperationalLift", Operation)
    return state


def energizing(events):
    return [
        event for event in events if event[0] == "write" and event[2] == "Torque_Enable" and event[4] == 1
    ]


def goals(events):
    return [event for event in events if event[0] == "write" and event[2] == "Goal_Position"]


@pytest.mark.parametrize("side", ["left", "right"])
@pytest.mark.parametrize("raw", [999, 2201, 3223, True, 1600.5, float("nan")])
def test_initial_outside_or_malformed_raw_refuses_before_any_goal_or_power(activation, side, raw):
    case, robot = activation, activation.robot
    bus = getattr(robot, f"{side}_bus")
    bus.registers[("Present_Position", f"arm_{side}_shoulder_lift")] = raw
    with pytest.raises(RuntimeError, match="motor activation failed"):
        robot.activate_motors()
    assert not goals(case.events) and not energizing(case.events)
    assert case.home_calls == 0
    assert not robot.left_bus.is_connected and not robot.right_bus.is_connected
    assert all(
        value == 0
        for bus in (robot.left_bus, robot.right_bus)
        for (register, _), value in bus.registers.items()
        if register == "Torque_Enable"
    )


@pytest.mark.parametrize(
    "problem", ["missing", "id", "cache_bounds", "actual_min", "actual_max", "offset", "torque"]
)
def test_initial_cache_and_current_eeprom_must_match_before_power(activation, problem):
    case, robot = activation, activation.robot
    bus, name = robot.right_bus, "arm_right_wrist_flex"
    if problem == "missing":
        del bus.calibration[name]
    elif problem == "id":
        bus.calibration[name].id += 1
    elif problem == "cache_bounds":
        bus.calibration[name].range_min = bus.calibration[name].range_max
    else:
        register, value = {
            "actual_min": ("Min_Position_Limit", 999),
            "actual_max": ("Max_Position_Limit", 2201),
            "offset": ("Homing_Offset", 1),
            "torque": ("Torque_Enable", 1),
        }[problem]
        bus.registers[(register, name)] = value
    with pytest.raises(RuntimeError, match="motor activation failed"):
        robot.activate_motors()
    assert not goals(case.events) and not energizing(case.events)
    assert case.home_calls == 0


@pytest.mark.parametrize("side", ["left", "right"])
def test_initial_read_fault_prevents_both_arm_buses_and_home_from_enabling(activation, side):
    case, robot = activation, activation.robot
    bus, name = getattr(robot, f"{side}_bus"), f"arm_{side}_gripper"
    bus.read_faults[("Present_Position", name)] = ConnectionError("original raw fault")
    with pytest.raises(RuntimeError, match="motor activation failed") as caught:
        robot.activate_motors()
    assert isinstance(caught.value.__cause__, ConnectionError)
    assert str(caught.value.__cause__) == "original raw fault"
    assert not goals(case.events) and not energizing(case.events)
    assert case.home_calls == 0


@pytest.mark.parametrize("side", ["left", "right"])
def test_post_home_drift_refuses_all_arm_goals_and_arm_base_power(activation, side):
    case, robot = activation, activation.robot
    case.home_change = lambda source: getattr(source, f"{side}_bus").registers.update(
        {("Present_Position", f"arm_{side}_shoulder_lift"): 2201}
    )
    with pytest.raises(RuntimeError, match="motor activation failed"):
        robot.activate_motors()
    assert case.home_calls == 1 and not goals(case.events)
    assert all(event[3] == "lift_axis" for event in energizing(case.events))
    assert ("lift_cleanup",) in case.events
    assert robot.left_bus.registers[("Torque_Enable", "lift_axis")] == 0


def test_post_home_cache_mutation_does_not_reuse_old_admission(activation):
    case, robot = activation, activation.robot
    case.home_change = lambda source: setattr(
        source.right_bus.calibration["arm_right_gripper"], "range_max", 2300
    )
    with pytest.raises(RuntimeError, match="motor activation failed"):
        robot.activate_motors()
    assert not goals(case.events)
    assert all(event[3] == "lift_axis" for event in energizing(case.events))


@pytest.mark.parametrize("side", ["left", "right"])
def test_seeded_goal_readback_mismatch_refuses_before_any_arm_or_base_power(activation, side):
    case, robot = activation, activation.robot
    getattr(robot, f"{side}_bus").readback_mismatch = ("Goal_Position", f"arm_{side}_wrist_roll")
    with pytest.raises(RuntimeError, match="motor activation failed"):
        robot.activate_motors(home_lift=False)
    assert len(goals(case.events)) == 12
    assert not energizing(case.events)
    assert not robot.left_bus.is_connected and not robot.right_bus.is_connected


@pytest.mark.parametrize("home", [False, True])
def test_valid_activation_qualifies_both_buses_and_exact_goals_before_arm_base_power(activation, home):
    case, robot = activation, activation.robot
    robot.left_bus.registers[("Present_Position", "arm_left_shoulder_lift")] = 1000
    robot.right_bus.registers[("Present_Position", "arm_right_shoulder_lift")] = 2200
    if home:
        case.home_change = lambda source: source.left_bus.registers.update(
            {("Present_Position", "arm_left_shoulder_lift"): 1100}
        )
    assert robot.activate_motors(home_lift=home) == ("home-result" if home else None)
    arm_power = [event for event in energizing(case.events) if event[3] != "lift_axis"]
    assert len(arm_power) == 15
    first_power = case.events.index(arm_power[0])
    written_goals = goals(case.events)
    assert len(written_goals) == 12
    read_goals = [event for event in case.events if event[:1] == ("read",) and event[2] == "Goal_Position"]
    assert len(read_goals) == 12
    assert all(case.events.index(event) < first_power for event in written_goals + read_goals)
    first_goal = case.events.index(written_goals[0])
    last_raw = [
        event for event in case.events[:first_goal] if event[0] == "read" and event[2] == "Present_Position"
    ]
    assert len(last_raw) == 24
    assert all(event[-1] == owner.REGISTER_RETRIES and event[-2] is False for event in last_raw + read_goals)
    assert robot.left_bus.registers[("Goal_Position", "arm_left_shoulder_lift")] == (1100 if home else 1000)
    assert robot.right_bus.registers[("Goal_Position", "arm_right_shoulder_lift")] == 2200
    for bus in (robot.left_bus, robot.right_bus):
        for name in bus.calibration:
            assert bus.registers[("Goal_Position", name)] == bus.registers[("Present_Position", name)]
    limits = [
        event
        for event in case.events
        if event[0] == "read" and event[2] in {"Min_Position_Limit", "Max_Position_Limit", "Homing_Offset"}
    ]
    assert len(limits) == 36  # No redundant EEPROM batch after powered lift home.
    if home:
        assert all(case.events.index(event) < case.events.index(("home",)) for event in limits)
    assert robot.left_bus.is_connected and robot.right_bus.is_connected


def test_am2_retains_legacy_raw_seed_without_am1_calibration_admission(activation):
    case, robot = activation, activation.robot
    robot.config.robot_model = "alohamini2"
    robot.left_bus.calibration.clear()
    robot.left_bus.registers[("Present_Position", "arm_left_shoulder_lift")] = 3223
    robot.activate_motors(home_lift=False)
    assert len(goals(case.events)) == 12
    assert len(energizing(case.events)) == 15
    assert robot.left_bus.registers[("Goal_Position", "arm_left_shoulder_lift")] == 3223
    assert not any(event[0] == "read" and event[2] == "Min_Position_Limit" for event in case.events)


@pytest.mark.parametrize("phase", ["before_home", "seed_readback"])
def test_expired_original_raw_vector_refuses_before_power_even_after_matching_readbacks(activation, phase):
    case, robot = activation, activation.robot
    register = "Present_Position" if phase == "before_home" else "Goal_Position"

    def delay(register_read, name):
        if register_read == register and name == "arm_right_gripper":
            case.clock.now += 1.001

    robot.right_bus.on_read = delay
    with pytest.raises(RuntimeError, match="motor activation failed") as caught:
        robot.activate_motors()
    assert "expired" in str(caught.value.__cause__)
    assert all(event[3] == "lift_axis" for event in energizing(case.events))
    assert case.home_calls == (0 if phase == "before_home" else 1)
    present = [
        row
        for row in robot._am1_activation_readbacks
        if row["phase"] == ("before_home" if phase == "before_home" else "after_home")
        and row["register"] == "Present_Position"
    ]
    assert present[0]["read_started_at"] == 100.0
    assert len(present) == 12
    if phase == "seed_readback":
        assert len(goals(case.events)) == 12
        assert all(row["read_started_at"] == 100.0 for row in present)


@pytest.mark.parametrize("elapsed,refuse", [(0.999, False), (1.0, True), (1.001, True)])
def test_activation_raw_age_bound_does_not_extend_after_goal_readback(activation, elapsed, refuse):
    case, robot = activation, activation.robot

    def delay(register, name):
        if register == "Goal_Position" and name == "arm_right_gripper":
            case.clock.now += elapsed

    robot.right_bus.on_read = delay
    if refuse:
        with pytest.raises(RuntimeError, match="motor activation failed"):
            robot.activate_motors(home_lift=False)
        assert not energizing(case.events)
    else:
        robot.activate_motors(home_lift=False)
        assert len(energizing(case.events)) == 15


def test_raw_age_is_checked_before_each_arm_and_base_enable(activation):
    case, robot = activation, activation.robot

    def delay(register, name, value):
        if register == "Lock" and name == "arm_left_shoulder_pan" and value == 1:
            case.clock.now += 1.001

    robot.left_bus.on_write = delay
    with pytest.raises(RuntimeError, match="motor activation failed"):
        robot.activate_motors(home_lift=False)
    assert [row[3] for row in energizing(case.events)] == ["arm_left_shoulder_pan"]
    assert all(
        value == 0
        for bus in (robot.left_bus, robot.right_bus)
        for (register, _), value in bus.registers.items()
        if register == "Torque_Enable"
    )


def test_post_home_fresh_vector_does_not_retime_or_reuse_pre_home_samples(activation):
    case, robot = activation, activation.robot
    case.home_change = lambda source: setattr(case.clock, "now", case.clock.now + 5.0)
    robot.activate_motors()
    evidence = robot._am1_activation_readbacks
    before = [
        row for row in evidence if row["phase"] == "before_home" and row["register"] == "Present_Position"
    ]
    after = [
        row for row in evidence if row["phase"] == "after_home" and row["register"] == "Present_Position"
    ]
    assert len(before) == len(after) == 12
    assert all(row["read_started_at"] == row["read_completed_at"] == 100.0 for row in before)
    assert all(row["read_started_at"] == row["read_completed_at"] == 105.0 for row in after)
    assert len(energizing(case.events)) == 16


@pytest.mark.parametrize("value", [True, 1600.5, float("nan")])
def test_malformed_seed_readback_refuses_both_buses_before_power(activation, value):
    case, robot = activation, activation.robot
    robot.right_bus.read_overrides[("Goal_Position", "arm_right_wrist_roll")] = value
    with pytest.raises(RuntimeError, match="motor activation failed"):
        robot.activate_motors(home_lift=False)
    assert len(goals(case.events)) == 12
    assert not energizing(case.events)


def test_refused_activation_restores_actual_integral_trial_only_after_torque_off(
    activation,
    monkeypatch,
    tmp_path,
):
    from lerobot.robots.alohamini.am1_shoulder_integral import AM1ShoulderIntegralTrial

    case, robot = activation, activation.robot
    name = "arm_left_shoulder_lift"
    for flag in ("AM1_SCRIPTED_PREPARE", "AM1_LEFT_SHOULDER_EVIDENCE", "AM1_SHOULDER_INTEGRAL_TEST"):
        monkeypatch.setenv(flag, "1")
    for register, value in (
        ("Model_Number", 777),
        ("Firmware_Major_Version", 3),
        ("Firmware_Minor_Version", 10),
        ("Operating_Mode", 0),
        ("P_Coefficient", 16),
        ("I_Coefficient", 0),
        ("D_Coefficient", 32),
        ("Minimum_Startup_Force", 16),
    ):
        robot.left_bus.registers[(register, name)] = value
    snapshot = tmp_path / "trial.json"
    robot._am1_shoulder_integral_trial = AM1ShoulderIntegralTrial(snapshot_path=snapshot)
    assert robot._am1_shoulder_integral_trial.configure(robot)
    assert robot.left_bus.registers[("I_Coefficient", name)] == 1
    robot.right_bus.registers[("Present_Position", "arm_right_shoulder_lift")] = 2201
    with pytest.raises(RuntimeError, match="motor activation failed"):
        robot.activate_motors()
    restored = next(
        index
        for index, event in enumerate(case.events)
        if event[:5] == ("write", "left", "I_Coefficient", name, 0)
    )
    torque_off = [
        index
        for index, event in enumerate(case.events)
        if event[0] == "write" and event[2] == "Torque_Enable" and event[4] == 0
    ]
    assert len(torque_off) == 16 and all(index < restored for index in torque_off)
    assert not energizing(case.events) and not goals(case.events) and case.home_calls == 0
    assert robot.left_bus.registers[("I_Coefficient", name)] == 0
    assert json.loads(snapshot.read_text(encoding="utf-8"))["status"] == "restored"


def protected_endpoint_case(case, monkeypatch, recipe="physical-live-right-elbow"):
    """Installed Packet10 endpoint; fake registers and clocks, no physical IO."""
    from uuid import uuid4
    from examples.alohamini.am1_session_contract import LIVE_ELBOW_SPECS
    robot = case.robot
    robot.config.no_follower = False
    spec = LIVE_ELBOW_SPECS[recipe]
    robot._am1_visible_control_binding = {"run_id": str(uuid4()), "host_incarnation": str(uuid4()), "recipe": recipe}
    selected_bus = getattr(robot, spec.side + "_bus")
    robot._am1_visible_control_evidence = SimpleNamespace(
        live_spec=spec, motor=spec.joint.removesuffix(".pos"), position=spec.joint,
        _bus=lambda robot: selected_bus, verify_visible_active=lambda robot: None,
        emit=lambda row: case.events.append(("live_evidence", row)),
    )
    for bus in (robot.left_bus, robot.right_bus):
        bus.apply_drive_mode = False
        bus.model_resolution_table = {"sts3215": 4096}
        bus._id_to_name = lambda ident, source=bus: next(n for n, m in source.motors.items() if m.id == ident)
        bus._normalize = lambda values, source=bus: owner.FeetechMotorsBus._normalize(source, values)
        bus._id_to_model = lambda ident: "sts3215"
        bus.sync_read = lambda reg, names, normalize=False, num_retry=0, source=bus: {
            name: source.read(reg, name, normalize=normalize, num_retry=num_retry) for name in names}
    name = "arm_left_shoulder_lift"
    robot.left_bus.calibration[name] = MotorCalibration(2, 0, 515, 848, 3223)
    for bus in (robot.left_bus, robot.right_bus):
        for motor in bus.motors:
            bus.registers.update({(reg, motor): value for reg, value in (
                ("ID", bus.motors[motor].id), ("Model_Number", 777),
                ("Firmware_Major_Version", 3), ("Firmware_Minor_Version", 10),
                ("Operating_Mode", 0), ("Phase", 12), ("CW_Dead_Zone", 1), ("CCW_Dead_Zone", 1),
                ("P_Coefficient", 16), ("I_Coefficient", 0), ("D_Coefficient", 32),
                ("Present_Current", 0), ("Status", 0), ("Present_Temperature", 35),
                ("Present_Velocity", 0), ("Moving", 0), ("Present_Voltage", 118),
                ("Min_Voltage_Limit", 40), ("Max_Voltage_Limit", 140), ("Max_Temperature_Limit", 70),
            )})
    robot.left_bus.registers.update({(reg, name): value for reg, value in (
        ("Min_Position_Limit", 848), ("Max_Position_Limit", 3223), ("Homing_Offset", 515),
        ("Present_Position", 3224), ("Goal_Position", 3224),
    )})
    monkeypatch.setattr(owner.time, "sleep", lambda seconds: setattr(case.clock, "now", case.clock.now + seconds))
    # This fixture replaces lift IO; dedicated startup thermal tests retain real lift coverage.
    robot._refresh_am1_startup_lift_feedback = lambda: None
    return robot


@pytest.mark.parametrize("recipe", ["physical-live-left-elbow", "physical-live-right-elbow"])
@pytest.mark.parametrize("home", [False, True])
def test_protected_live_endpoint_seeds_only_representable_goal_and_freezes_acknowledged_map(activation, monkeypatch, recipe, home):
    robot = protected_endpoint_case(activation, monkeypatch, recipe)
    robot.activate_motors(home_lift=home)
    shoulder_goals = [row for row in goals(activation.events) if row[3] == "arm_left_shoulder_lift"]
    assert [row[4] for row in shoulder_goals] == [3223]
    assert robot.left_bus.registers["Present_Position", "arm_left_shoulder_lift"] == 3224
    proof = robot._am1_live_endpoint_evidence
    assert {row["phase"] for row in proof} == {"before_home", "after_home"}
    assert all(len(row["samples"]) >= 9 and row["window_s"] >= .4 and len(row["temperature_slots"]) == 5 for row in proof)
    commands = robot.am1_live_arm_commands(dict(robot._am1_visible_control_binding))
    assert commands["arm_left_shoulder_lift.pos"] == 100
    assert len(commands) == 12
    first_enable = next(i for i, row in enumerate(activation.events) if row in energizing(activation.events) and row[3].startswith("arm_"))
    before = activation.events[:first_enable]
    assert len([row for row in before if row[0] == "read" and row[2] == "Goal_Position" and row[3].startswith("arm_")]) >= 12


@pytest.mark.parametrize("register,value", [("Present_Current", 24), ("Status", 1), ("Present_Temperature", 60), ("Present_Velocity", 1), ("Moving", 1), ("Phase", 13), ("CW_Dead_Zone", 2), ("ID", 3), ("Model_Number", 1), ("I_Coefficient", 1)])
def test_protected_live_endpoint_health_settings_refuses_before_home_or_any_arm_goal(activation, monkeypatch, register, value):
    robot = protected_endpoint_case(activation, monkeypatch)
    robot.left_bus.registers[register, "arm_left_shoulder_lift"] = value
    with pytest.raises(RuntimeError, match="activation failed"):
        robot.activate_motors()
    assert not goals(activation.events) and not energizing(activation.events) and activation.home_calls == 0


@pytest.mark.parametrize("side,joint,raw", [("left", "shoulder_lift", 3225), ("right", "shoulder_lift", 2201), ("left", "elbow_flex", 2201)])
def test_protected_live_endpoint_exception_never_expands_other_joint_or_larger_range(activation, monkeypatch, side, joint, raw):
    robot = protected_endpoint_case(activation, monkeypatch)
    getattr(robot, side + "_bus").registers["Present_Position", f"arm_{side}_{joint}"] = raw
    with pytest.raises(RuntimeError):robot.activate_motors()
    assert not goals(activation.events) and activation.home_calls == 0


def test_protected_live_hold_only_selected_goal_changes_and_current_map_mismatch_refuses(activation, monkeypatch):
    robot = protected_endpoint_case(activation, monkeypatch)
    robot.activate_motors(home_lift=False)
    nonselected = {name: bus.registers["Goal_Position", name] for bus in (robot.left_bus, robot.right_bus) for name in bus.calibration if name != "arm_right_elbow_flex"}
    # Other-joint feedback moves by one tick; it must never reseed eleven goals.
    for bus in (robot.left_bus, robot.right_bus):
        for name in bus.calibration:bus.registers["Present_Position", name] += 1
    boundary = len(activation.events)
    robot.hold_follower_arms()
    assert [(row[1], row[3]) for row in goals(activation.events[boundary:])] == [("right", "arm_right_elbow_flex")]
    assert all(bus.registers["Goal_Position", name] == nonselected[name] for bus in (robot.left_bus, robot.right_bus) for name in bus.calibration if name in nonselected)
    robot.left_bus.registers["Goal_Position", "arm_left_shoulder_lift"] = 3224
    boundary = len(activation.events)
    with pytest.raises(RuntimeError):robot.hold_follower_arms()
    assert not goals(activation.events[boundary:])


@pytest.mark.parametrize("mutation", ["missing", "binding", "calibration", "stale"])
def test_protected_live_hold_missing_changed_or_stale_map_is_refusal_not_reseed(activation, monkeypatch, mutation):
    robot = protected_endpoint_case(activation, monkeypatch)
    robot.activate_motors(home_lift=False)
    if mutation == "missing":robot._am1_live_frozen_holds = None
    elif mutation == "binding":robot._am1_visible_control_binding["host_incarnation"] = "00000000-0000-0000-0000-000000000000"
    elif mutation == "calibration":robot.left_bus.calibration["arm_left_wrist_flex"].range_max += 1
    else:robot.right_bus.on_read = lambda reg, name: setattr(activation.clock, "now", activation.clock.now + .3)
    boundary = len(activation.events)
    with pytest.raises(RuntimeError):robot.hold_follower_arms()
    assert not goals(activation.events[boundary:])


@pytest.mark.parametrize("mutation", ["binding", "calibration"])
def test_protected_live_startup_mutation_after_seed_refuses_before_arm_enable(activation, monkeypatch, mutation):
    robot = protected_endpoint_case(activation, monkeypatch)
    def mutate(register, name, value):
        if register == "Goal_Position" and name == "arm_right_gripper":
            if mutation == "binding":robot._am1_visible_control_binding["recipe"] = "physical-live-unknown"
            else:robot.left_bus.calibration["arm_left_wrist_flex"].homing_offset += 1
    robot.right_bus.on_write = mutate
    with pytest.raises(RuntimeError):robot.activate_motors(home_lift=False)
    assert not energizing(activation.events)
    assert robot._am1_live_frozen_holds is None


def test_protected_endpoint_known_current_fault_precedes_later_read_failure(activation, monkeypatch):
    robot = protected_endpoint_case(activation, monkeypatch)
    robot._am1_activation_readbacks = []
    robot._am1_live_endpoint_evidence = []
    name = "arm_left_shoulder_lift"
    robot.left_bus.registers["Present_Current", name] = 24
    robot.left_bus.read_faults["Status", name] = OSError("later status unavailable")
    with pytest.raises(RuntimeError, match="endpoint.*qualification failed"):
        robot._qualify_am1_live_endpoint(robot.left_bus, name, (2, 0, 515, 848, 3223), phase="before_home")
    assert not any(row[:4] == ("read", "left", "Status", name) for row in activation.events)
    assert not goals(activation.events)


def test_protected_live_raw_ack_evidence_is_json_safe_for_numpy_bus_values(activation, monkeypatch):
    import numpy as np
    robot = protected_endpoint_case(activation, monkeypatch)
    read = robot.left_bus.read
    robot.left_bus.read = lambda *args, **kwargs: np.int64(read(*args, **kwargs))
    robot.activate_motors(home_lift=False)
    rows = [row[1] for row in activation.events if row[0] == "live_evidence"]
    assert rows and json.loads(json.dumps(rows))[0]["kind"] == "protected_live_startup_raw_goals"


def test_protected_endpoint_rejects_negative_unsigned_current(activation, monkeypatch):
    robot = protected_endpoint_case(activation, monkeypatch)
    robot.left_bus.registers["Present_Current", "arm_left_shoulder_lift"] = -1
    with pytest.raises(RuntimeError):robot.activate_motors(home_lift=False)
    assert not energizing(activation.events) and not goals(activation.events)


def current_rest_normal_activation_case(case, tmp_path, monkeypatch, *, workload_segment="left-reach"):
    """Real protected transaction/activation; replace only external register I/O."""
    import copy

    from test_am1_elbow_integral import CURRENT_REST_PROFILE, ELBOW, left_response_p_robot

    from lerobot.robots.alohamini import am1_live_teleop

    _, bound, trial, _, _, _ = left_response_p_robot(
        tmp_path, monkeypatch, workload_segment=workload_segment,
        workspace_profile=("ordinary-arms-left-current-rest-1051-1164" if workload_segment == "ordinary-arms" else CURRENT_REST_PROFILE),
        left_shoulder_startup_degrees=1 if workload_segment == "ordinary-arms" else None,
    )
    robot = case.robot
    robot.config.no_follower = False
    robot._am1_protected_cleanup = True
    robot._am1_selected_joint_evidence_enabled = False
    robot._am1_teleop_binding = copy.deepcopy(bound._am1_teleop_binding)
    robot._am1_left_response_p_binding = copy.deepcopy(bound._am1_left_response_p_binding)
    robot._am1_left_response_p_trial = trial
    robot._am1_teleop_settings = copy.deepcopy(bound._am1_teleop_settings)
    robot.left_bus.calibration[ELBOW] = copy.deepcopy(bound.left_bus.calibration[ELBOW])
    robot.left_bus.registers.update({key: value for key, value in bound.left_bus.values.items() if key[1] == ELBOW})
    robot.left_bus._read = bound.left_bus._read
    bound._am1_left_response_p_trial = None
    # Dedicated real thermal/settings fixtures retain those acquisition tests;
    # this fixture runs the actual ordinary arm-calibration/seed/activation code.
    monkeypatch.setattr(am1_live_teleop, "refresh_startup_temperatures", lambda source: None)
    monkeypatch.setattr(am1_live_teleop, "verify_settings", lambda source: None)
    trial.prepare(robot)
    trial.disable_torque(robot)
    trial.apply(robot)
    robot.left_bus.registers["Goal_Position", ELBOW] = 2050
    case.events.clear()

    def observed_goal_transition(register, name, value):
        if register == "Goal_Position":
            robot.left_bus.registers["Torque_Enable", name] = 1
    robot.left_bus.on_write = observed_goal_transition
    robot.right_bus.on_write = lambda register, name, value: (
        robot.right_bus.registers.__setitem__(("Torque_Enable", name), 1)
        if register == "Goal_Position" else None
    )
    return robot, trial, ELBOW


@pytest.mark.parametrize("workload_segment", ["left-reach", "ordinary-arms"])
def test_current_rest_normal_activation_uses_fresh_afterhome_q0_and_never_replays_original_goal(
    activation, tmp_path, monkeypatch, workload_segment,
):
    case = activation
    robot, trial, elbow = current_rest_normal_activation_case(case, tmp_path, monkeypatch, workload_segment=workload_segment)
    assert trial.binding["request"]["workload_segment"] == workload_segment
    assert trial.normal_rest_startup_degrees(robot) == (1. if workload_segment == "ordinary-arms" else .5)
    def after_home(source):
        source.left_bus.registers["Present_Position", elbow] = 1091
        case.clock.now += 0.25
    case.home_change = after_home
    robot.activate_motors()
    selected = [row for row in goals(case.events) if row[3] == elbow]
    assert len(selected) == 1 and selected[0][4] == 1091
    assert len(goals(case.events)) == 12 and all(row[4] != 2050 for row in goals(case.events))
    assert all(row[4] == getattr(robot, row[1] + "_bus").registers["Present_Position", row[3]]
               for row in goals(case.events) if row[3] != elbow)
    evidence = trial.evidence["normal_activation"]
    assert evidence["policy"] == "ordinary-fresh-measured-after-home"
    assert evidence["position"] in robot._am1_activation_readbacks
    assert evidence["position"]["phase"] == "after_home" and evidence["position"]["raw"] == 1091
    assert evidence["torque_off"]["raw"] == 0 and evidence["torque_off"]["phase"] == "after_home"
    assert evidence["goal_readback"] in robot._am1_activation_readbacks
    assert evidence["goal_readback"]["phase"] == "seed_readback" and evidence["goal_readback"]["raw"] == 1091
    assert evidence["goal_write"]["value"] == 1091
    assert evidence["position"]["read_completed_at"] <= evidence["goal_write"]["started_at_monotonic_s"]
    assert evidence["goal_write"]["completed_at_monotonic_s"] <= evidence["goal_readback"]["read_started_at"]
    assert trial.active_receipt(robot)["configured_p"] == 32
    boundary = len(case.events)
    robot._safe_shutdown(close_buses=True)
    assert not goals(case.events[boundary:])
    assert robot.left_bus.registers["Goal_Position", elbow] == 1091
    assert robot.left_bus.registers["P_Coefficient", elbow] == 16
    assert robot.left_bus.registers["Torque_Enable", elbow] == 0
    assert robot.left_bus.registers["Lock", elbow] == 0 and not trial.restore_required


@pytest.mark.parametrize("fault", ["outside", "stale", "binding", "stop"])
def test_current_rest_normal_activation_refuses_current_corridor_or_authority_before_first_seed(
    activation, tmp_path, monkeypatch, fault
):
    case = activation
    robot, trial, elbow = current_rest_normal_activation_case(case, tmp_path, monkeypatch)
    if fault == "outside":
        case.home_change = lambda source: source.left_bus.registers.__setitem__(("Present_Position", elbow), 1165)
    else:
        def changed(register, name):
            if case.home_calls and register == "Present_Position" and name == "arm_right_gripper":
                if fault == "stale":
                    case.clock.now += 1.0
                elif fault == "binding":
                    robot._am1_left_response_p_binding["host_incarnation"] = "99999999-9999-4999-8999-999999999999"
                else:
                    robot._am1_shutdown_in_progress = True
        robot.right_bus.on_read = changed
    with pytest.raises(RuntimeError, match="motor activation failed"):
        robot.activate_motors()
    assert not goals(case.events)
    assert not [row for row in energizing(case.events) if row[3].startswith("arm_")]
    assert robot.left_bus.registers["Torque_Enable", elbow] == 0
    assert trial.evidence["original_registers"]["Goal_Position"] == 2050


def test_current_rest_normal_activation_cannot_continue_seed_after_original_vector_expires(
    activation, tmp_path, monkeypatch
):
    case = activation
    robot, _, elbow = current_rest_normal_activation_case(case, tmp_path, monkeypatch)
    ordinary = robot.left_bus.on_write
    def delayed(register, name, value):
        ordinary(register, name, value)
        if register == "Goal_Position" and name == "arm_left_shoulder_lift":
            case.clock.now += 1.0
    robot.left_bus.on_write = delayed
    with pytest.raises(RuntimeError, match="motor activation failed"):
        robot.activate_motors()
    assert [row[3] for row in goals(case.events)] == ["arm_left_shoulder_pan", "arm_left_shoulder_lift"]
    assert not [row for row in goals(case.events) if row[3] == elbow]
    assert robot.left_bus.registers["Torque_Enable", elbow] == 0


def test_current_rest_normal_activation_seed_readback_failure_restores_p_without_stale_goal(
    activation, tmp_path, monkeypatch
):
    case = activation
    robot, trial, elbow = current_rest_normal_activation_case(case, tmp_path, monkeypatch)
    robot.left_bus.readback_mismatch = ("Goal_Position", elbow)
    with pytest.raises(RuntimeError, match="motor activation failed"):
        robot.activate_motors()
    assert not [row for row in goals(case.events) if row[4] == 2050]
    assert robot.left_bus.registers["P_Coefficient", elbow] == 16
    assert robot.left_bus.registers["Torque_Enable", elbow] == 0
    assert robot.left_bus.registers["Lock", elbow] == 0 and not trial.restore_required
