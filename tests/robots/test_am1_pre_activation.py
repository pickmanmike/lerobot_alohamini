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

    class Operation:
        def __init__(self, source):
            self.lift = source.lift

        def start(self):
            state.home_calls += 1
            events.append(("home",))
            robot.left_bus.write("Torque_Enable", "lift_axis", 1, normalize=False)
            if state.home_change is not None:
                state.home_change(robot)
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
