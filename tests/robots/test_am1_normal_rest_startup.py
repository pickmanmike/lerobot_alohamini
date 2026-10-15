"""Normal-rest startup through real AM1 ownership and Feetech encoding.

All coordinates and calibration records here are synthetic. Only the serial
transport and operational lift I/O boundary are replaced; connect, configure,
activation, shutdown, calibration loading, and position conversion remain real.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from lerobot.motors import MotorCalibration
from lerobot.motors.feetech import FeetechMotorsBus
from lerobot.motors.motors_bus import SerialMotorsBus
from lerobot.robots.alohamini import alohamini as owner, lift_operational
from lerobot.robots.alohamini.config_alohamini import AlohaMiniConfig

SHOULDER = "arm_left_shoulder_lift"
JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")


class RegisterTransport(FeetechMotorsBus):
    """A servo transport that enforces limits and moves to accepted powered goals.

    Present_Position is electrical angle minus the hardware homing offset. Its
    value already includes that offset, as on the real device. Inherited public
    read/write/sync methods retain Feetech sign encoding and normalization.
    """

    def __init__(self, *, port, motors, calibration, events, rest_raw, acceptance):
        SerialMotorsBus.__init__(self, port, motors, calibration)
        self.protocol_version = 0
        self._comm_success = self._no_error = 0
        self._connected = False
        self.events = events
        self.acceptance = acceptance
        self.first_enables = []
        self.limit_change_on_goal = None
        self.electrical = {}
        self.registers = {}
        for name, motor in motors.items():
            table = self.model_ctrl_table[motor.model]
            for register in table:
                self.registers[register, name] = 0
            record = self.calibration[name]
            self.registers.update(
                {
                    ("Min_Position_Limit", name): record.range_min,
                    ("Max_Position_Limit", name): record.range_max,
                    ("Homing_Offset", name): record.homing_offset,
                    ("Phase", name): 0x11,
                    ("Goal_Position", name): record.range_min,
                    ("Goal_Velocity", name): 240 if name.startswith("arm_") else 0,
                    ("Present_Temperature", name): 25,
                    ("Present_Voltage", name): 120,
                }
            )
            self.electrical[name] = rest_raw.get(name, 2000) + record.homing_offset

    @property
    def is_connected(self):
        return self._connected

    def connect(self, handshake=True):
        self._connected = True
        self.events.append(("connect", self.port))

    def disconnect(self, disable_torque=True):
        if disable_torque:
            self.disable_torque()
        self.events.append(("disconnect", self.port))
        self._connected = False

    def present(self, name):
        return self.electrical[name] - self.registers["Homing_Offset", name]

    def settle(self, name, raw):
        if self.registers["Torque_Enable", name]:
            raise RuntimeError("A powered motor cannot settle freely")
        self.electrical[name] = raw + self.registers["Homing_Offset", name]

    def _register_name(self, address, length, motor_id):
        table = self.model_ctrl_table[self._id_to_model(motor_id)]
        return next(name for name, location in table.items() if location == (address, length))

    def _read(self, address, length, motor_id, **kwargs):
        name = self._id_to_name(motor_id)
        register = self._register_name(address, length, motor_id)
        value = self.present(name) if register == "Present_Position" else self.registers[register, name]
        self.events.append(("read", self.port, register, name, value))
        return self._encode_sign(register, {motor_id: value})[motor_id], 0, 0

    def _write(self, address, length, motor_id, value, **kwargs):
        name = self._id_to_name(motor_id)
        register = self._register_name(address, length, motor_id)
        value = self._decode_sign(register, {motor_id: value})[motor_id]
        self.events.append(("write", self.port, register, name, value))
        if register == "Goal_Position":
            if self.limit_change_on_goal == name:
                # Device limits can change after qualification but before acceptance.
                self.registers["Max_Position_Limit", name] = 2400
                self.limit_change_on_goal = None
            low = self.registers["Min_Position_Limit", name]
            high = self.registers["Max_Position_Limit", name]
            if not low <= value <= high and self.acceptance == "refuse":
                raise RuntimeError("servo rejected unrepresentable goal")
            value = min(high, max(low, value))
        self.registers[register, name] = value
        if name.startswith("arm_") and register == "Torque_Enable" and value == 1:
            before = self.present(name)
            goal = self.registers["Goal_Position", name]
            self.first_enables.append(
                {
                    "name": name,
                    "before": before,
                    "goal": goal,
                    "mode": self.registers["Operating_Mode", name],
                    "speed": self.registers["Goal_Velocity", name],
                    "acceleration": self.registers["Acceleration", name],
                    "maximum_acceleration": self.registers["Maximum_Acceleration", name],
                }
            )
            if self.registers["Operating_Mode", name] == 0:
                self.electrical[name] = goal + self.registers["Homing_Offset", name]
        elif (
            name.startswith("arm_")
            and register == "Goal_Position"
            and self.registers["Torque_Enable", name]
            and self.registers["Operating_Mode", name] == 0
        ):
            self.electrical[name] = value + self.registers["Homing_Offset", name]
        return 0, 0

    def _sync_read(self, address, length, motor_ids, **kwargs):
        return {motor_id: self._read(address, length, motor_id)[0] for motor_id in motor_ids}, 0

    def _sync_write(self, address, length, ids_values, **kwargs):
        for motor_id, value in ids_values.items():
            self._write(address, length, motor_id, value)
        return 0


@pytest.fixture
def startup(monkeypatch, tmp_path):
    monkeypatch.delenv("AM1_SHOULDER_INTEGRAL_TEST", raising=False)
    events = []
    state = SimpleNamespace(events=events, home_delta=0, home_calls=0)

    class LiftIO:
        def __init__(self, robot):
            self.robot, self.lift = robot, robot.lift
            # This coordinate/encoding fixture replaces lift I/O completely.
            # Real acquisition/thermal semantics are exercised separately by
            # test_am1_startup_thermal_handoff and the operational-lift suites.
            self.failure = None
            self.last_record = {"sample_monotonic_s": owner.time.monotonic()}
            # Explicit mock interface for the original-slot startup scheduler.
            # This fixture replaces lift I/O; real Window voting/clock guards
            # remain exercised by the two dedicated thermal startup suites.
            self.temperature = SimpleNamespace(
                SLOT_S=0.1,
                _origin_ns=round(self.last_record["sample_monotonic_s"] * 1_000_000_000),
                _last_slot=0,
                assert_fresh=lambda now: None,
            )

        def raise_if_faulted(self):
            if self.failure is not None:
                raise self.failure

        def poll(self):
            self.last_record = {"sample_monotonic_s": owner.time.monotonic()}
            self.temperature._last_slot = (
                round(self.last_record["sample_monotonic_s"] * 1_000_000_000) - self.temperature._origin_ns
            ) // round(self.temperature.SLOT_S * 1_000_000_000)

        def start(self):
            state.home_calls += 1
            events.append(("lift_home",))
            bus = self.robot.left_bus
            bus.settle(SHOULDER, bus.present(SHOULDER) + state.home_delta)
            # Keep actual lift configuration, home, zero reference and handoff.
            # The register transport models its external hard-stop feedback.
            result = self.lift.home()
            self.poll()
            return result

        def contribute_observation(self, observation):
            self.lift.contribute_observation(observation)

        def apply_action(self, action):
            self.lift.apply_action(action)

        def cleanup_readback(self):
            for bus in (self.robot.left_bus, self.robot.right_bus):
                for name in bus.motors:
                    if bus.read("Torque_Enable", name, normalize=False):
                        raise RuntimeError("torque remained enabled after shutdown")

    monkeypatch.setattr(lift_operational, "OperationalLift", LiftIO)

    def make(*, shoulder_max=3000, rest=2500, acceptance="clip", relative_limit=3.0):
        records = {
            f"arm_{side}_{joint}": MotorCalibration(index, 0, 0, 1000, 3000)
            for side in ("left", "right")
            for index, joint in enumerate(JOINTS, 1)
        }
        # Nonzero hardware offset and inverted software direction are deliberate.
        records[SHOULDER] = MotorCalibration(2, 1, 120, 1000, shoulder_max)
        records.update(
            {
                name: MotorCalibration(index, 0, 0, 0, 4095)
                for index, name in enumerate(
                    ("base_left_wheel", "base_back_wheel", "base_right_wheel", "lift_axis"), 8
                )
            }
        )
        calibration = tmp_path / "synthetic-rest.json"
        calibration.write_text(json.dumps({name: asdict(record) for name, record in records.items()}))

        def bus_factory(**kwargs):
            return RegisterTransport(
                **kwargs, events=events, rest_raw={SHOULDER: rest}, acceptance=acceptance
            )

        monkeypatch.setattr(owner, "FeetechMotorsBus", bus_factory)
        config = AlohaMiniConfig(
            id="synthetic-rest",
            calibration_dir=tmp_path,
            robot_model="alohamini1",
            left_port="synthetic-left",
            right_port="synthetic-right",
            cameras={},
            max_relative_target=relative_limit,
        )
        robot = owner.AlohaMini(config)
        state.robot = robot
        return robot

    state.make = make
    return state


def arm_goals(events):
    return [row for row in events if row[0] == "write" and row[2] == "Goal_Position"]


def arm_enables(events):
    return [
        row
        for row in events
        if row[0] == "write" and row[2] == "Torque_Enable" and row[3].startswith("arm_") and row[4] == 1
    ]


def assert_stopped(robot):
    assert not robot.is_connected
    for bus in (robot.left_bus, robot.right_bus):
        assert all(bus.registers["Torque_Enable", name] == 0 for name in bus.motors)
    assert all(
        robot.left_bus.registers["Goal_Velocity", name] == 0 for name in (*robot.base_motors, "lift_axis")
    )


@pytest.mark.parametrize("home", [False, True])
def test_corrected_normal_rest_seeds_fresh_accepted_hold_before_any_arm_power(startup, home):
    """An endpoint seed, offset subtraction or enable-before-readback must fail."""
    robot = startup.make()
    startup.home_delta = 20 if home else 0
    robot.connect(calibrate=False, home_lift=home)
    expected_rest = 2520 if home else 2500
    expected_electrical = 2640 if home else 2620
    assert robot.left_bus.present(SHOULDER) == expected_rest
    assert robot.left_bus.electrical[SHOULDER] == expected_electrical
    assert robot.left_bus.registers["Goal_Position", SHOULDER] == expected_rest
    assert startup.home_calls == int(home)

    written = arm_goals(startup.events)
    enabled = arm_enables(startup.events)
    assert len(written) == len(enabled) == 12
    first_enable = startup.events.index(enabled[0])
    goal_readbacks = [
        row for row in startup.events[:first_enable] if row[0] == "read" and row[2] == "Goal_Position"
    ]
    assert len(goal_readbacks) == 12
    assert all(startup.events.index(row) < first_enable for row in written)
    assert [row[4] for row in written if row[3] == SHOULDER] == [expected_rest]
    assert [row[4] for row in goal_readbacks if row[3] == SHOULDER] == [expected_rest]
    for bus in (robot.left_bus, robot.right_bus):
        for first in bus.first_enables:
            assert first["before"] == first["goal"]  # Actual device acceptance cannot move on enable.
            assert (first["mode"], first["speed"], first["acceleration"], first["maximum_acceleration"]) == (
                0,
                240,
                254,
                254,
            )
    assert robot.left_bus.registers["Homing_Offset", SHOULDER] == 120
    assert robot.left_bus.registers["Phase", SHOULDER] == 0x11
    assert not any(
        row[0] == "write" and row[2] in {"Homing_Offset", "Min_Position_Limit", "Max_Position_Limit", "Phase"}
        for row in startup.events
    )
    robot.disconnect()
    assert_stopped(robot)


def assert_exact_endpoint_start_and_live_hold(robot, events):
    written = arm_goals(events)
    enabled = arm_enables(events)
    assert len(written) == len(enabled) == 12
    first_enable = events.index(enabled[0])
    goal_readbacks = [row for row in events[:first_enable] if row[0] == "read" and row[2] == "Goal_Position"]
    assert len(goal_readbacks) == 12
    assert all(events.index(row) < first_enable for row in written)
    assert [row[4] for row in written if row[3] == SHOULDER] == [2500]
    assert [row[4] for row in goal_readbacks if row[3] == SHOULDER] == [2500]
    first = next(row for row in reversed(robot.left_bus.first_enables) if row["name"] == SHOULDER)
    assert first["before"] == first["goal"] == 2500
    assert robot.left_bus.registers["Max_Position_Limit", SHOULDER] == 2500
    assert robot.left_bus.present(SHOULDER) == 2500
    assert robot.left_bus.electrical[SHOULDER] == 2620
    observation = robot.get_observation()
    assert observation[f"{SHOULDER}.pos"] == -100.0
    hold = {key: value for key, value in observation.items() if key.endswith(".pos")}
    hold.update({"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0})
    sent = robot.send_action(hold)
    assert sent[f"{SHOULDER}.pos"] == -100.0
    assert robot.left_bus.registers["Goal_Position", SHOULDER] == 2500
    assert robot.left_bus.present(SHOULDER) == 2500
    assert robot.left_bus.electrical[SHOULDER] == 2620


@pytest.mark.parametrize("home", [False, True])
def test_exact_corrected_upper_endpoint_starts_and_hands_off_without_a_jump(startup, home):
    """Excluding an exact endpoint or changing its first live hold must fail."""
    robot = startup.make(shoulder_max=2500, rest=2500)
    robot.connect(calibrate=False, home_lift=home)
    assert startup.home_calls == int(home)
    assert_exact_endpoint_start_and_live_hold(robot, startup.events)
    robot.disconnect()
    assert_stopped(robot)


@pytest.mark.parametrize("home", [False, True])
def test_normal_stop_settling_at_exact_upper_endpoint_restarts_without_a_jump(startup, home):
    """A stale preceding hold or refusal of the freshly settled endpoint must fail."""
    robot = startup.make(shoulder_max=2500, rest=1800)
    robot.connect(calibrate=False, home_lift=False)
    robot.stop_motion()
    robot.disconnect()
    assert_stopped(robot)
    robot.left_bus.settle(SHOULDER, 2500)
    boundary = len(startup.events)
    robot.connect(calibrate=False, home_lift=home)
    assert startup.home_calls == int(home)
    assert_exact_endpoint_start_and_live_hold(robot, startup.events[boundary:])
    robot.disconnect()
    assert_stopped(robot)


def test_corrected_rest_hands_off_in_normalized_units_without_a_jump(startup):
    """A double offset, lost inversion or different first live goal must fail."""
    robot = startup.make()
    robot.connect(calibrate=False, home_lift=False)
    observation = robot.get_observation()
    assert observation[f"{SHOULDER}.pos"] == -50.0
    assert observation["arm_left_gripper.pos"] == 50.0
    assert observation["arm_right_shoulder_lift.pos"] == 0.0
    hold = {key: value for key, value in observation.items() if key.endswith(".pos")}
    hold.update({"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0})
    sent = robot.send_action(hold)
    assert sent[f"{SHOULDER}.pos"] == -50.0
    assert robot.left_bus.present(SHOULDER) == 2500
    assert robot.left_bus.electrical[SHOULDER] == 2620
    # Three normalized units mean thirty physical ticks under this synthetic map.
    sent = robot.send_action({**hold, f"{SHOULDER}.pos": -60.0})
    assert sent[f"{SHOULDER}.pos"] == -53.0
    assert robot.left_bus.registers["Goal_Position", SHOULDER] == 2530
    assert robot.left_bus.present(SHOULDER) == 2530
    robot.disconnect()
    assert_stopped(robot)


def test_normal_stop_natural_settling_and_start_seed_the_new_rest(startup):
    """Reusing the preceding session's hold must fail after free settling."""
    robot = startup.make()
    robot.connect(calibrate=False, home_lift=False)
    robot.stop_motion()
    robot.disconnect()
    assert_stopped(robot)
    robot.left_bus.settle(SHOULDER, 2700)
    boundary = len(startup.events)
    robot.connect(calibrate=False, home_lift=False)
    new_goals = arm_goals(startup.events[boundary:])
    assert [row[4] for row in new_goals if row[3] == SHOULDER] == [2700]
    assert robot.left_bus.registers["Goal_Position", SHOULDER] == 2700
    assert robot.left_bus.present(SHOULDER) == 2700
    assert robot.left_bus.electrical[SHOULDER] == 2820
    assert robot.get_observation()[f"{SHOULDER}.pos"] == -70.0
    robot.disconnect()
    assert_stopped(robot)


@pytest.mark.parametrize("acceptance", ["clip", "refuse"])
def test_incomplete_calibration_refuses_normal_rest_before_any_goal_or_power(startup, acceptance):
    """Removing admission must expose a clamped endpoint or rejected hold."""
    robot = startup.make(shoulder_max=2001, acceptance=acceptance)
    with pytest.raises(RuntimeError, match="motor activation failed") as caught:
        robot.connect(calibrate=False)
    assert "outside verified calibration limits" in str(caught.value.__cause__)
    assert robot.left_bus.present(SHOULDER) == 2500
    assert robot.left_bus.electrical[SHOULDER] == 2620
    assert not arm_goals(startup.events) and not arm_enables(startup.events)
    assert startup.home_calls == 0
    assert_stopped(robot)


@pytest.mark.parametrize("acceptance", ["clip", "refuse"])
def test_device_cannot_accept_fresh_hold_refuses_before_arm_power(startup, acceptance):
    """Skipping Goal_Position qualification must expose a clamped first enable."""
    robot = startup.make(acceptance=acceptance)
    robot.left_bus.limit_change_on_goal = SHOULDER
    with pytest.raises(RuntimeError, match="motor activation failed") as caught:
        robot.connect(calibrate=False, home_lift=False)
    expected = "measured goal readback" if acceptance == "clip" else "servo rejected unrepresentable goal"
    assert expected in str(caught.value.__cause__)
    assert robot.left_bus.present(SHOULDER) == 2500
    assert not arm_enables(startup.events)
    assert_stopped(robot)


@pytest.mark.parametrize("problem", ["cache_identity", "device_limit", "device_offset"])
def test_wrong_cached_or_device_calibration_still_refuses_real_connect(startup, problem):
    """A blind calibration rewrite or missing cache/device comparison must fail."""
    robot = startup.make()
    if problem == "cache_identity":
        robot.left_bus.calibration[SHOULDER].id = 3
    elif problem == "device_limit":
        robot.left_bus.registers["Max_Position_Limit", SHOULDER] = 2999
    else:
        robot.left_bus.registers["Homing_Offset", SHOULDER] = 121
    with pytest.raises(RuntimeError, match="motor activation failed"):
        robot.connect(calibrate=False)
    assert not arm_goals(startup.events) and not arm_enables(startup.events)
    assert startup.home_calls == 0
    assert_stopped(robot)


def test_already_in_range_start_keeps_direct_stationary_fast_path(startup):
    """Adding compulsory acquisition or using a calibration endpoint must fail."""
    robot = startup.make(shoulder_max=2001, rest=1800)
    robot.connect(calibrate=False, home_lift=False)
    assert [row[4] for row in arm_goals(startup.events) if row[3] == SHOULDER] == [1800]
    assert robot.left_bus.present(SHOULDER) == 1800
    assert startup.home_calls == 0
    assert all(
        first["before"] == first["goal"]
        for bus in (robot.left_bus, robot.right_bus)
        for first in bus.first_enables
    )
    robot.disconnect()
    assert_stopped(robot)


# The existing operating fixture uses the real OperationalLift/TemperatureWindow
# and sole-owner grouped packet decoder with fake mechanics, not a fake monitor.
from tests.robots.test_alohamini_lift_operational import operating_robot


def protected_post_home_endpoint(operating_robot):
    from uuid import uuid4
    from types import SimpleNamespace
    from examples.alohamini.am1_session_contract import LIVE_ELBOW_SPECS
    from lerobot.motors import Motor, MotorNormMode
    robot, clock = operating_robot
    robot.connect(calibrate=False)
    operation = robot._lift_operation
    assert isinstance(operation, lift_operational.OperationalLift)
    robot.left_bus.motors[SHOULDER] = Motor(2, "sts3215", MotorNormMode.RANGE_M100_100)
    robot.left_bus.calibration[SHOULDER] = MotorCalibration(2, 0, 515, 848, 3223)
    static = {"ID": 2, "Model_Number": 777, "Firmware_Major_Version": 3, "Firmware_Minor_Version": 10,
              "Min_Position_Limit": 848, "Max_Position_Limit": 3223, "Homing_Offset": 515,
              "Phase": 12, "CW_Dead_Zone": 1, "CCW_Dead_Zone": 1, "P_Coefficient": 16,
              "I_Coefficient": 0, "D_Coefficient": 32, "Max_Temperature_Limit": 70,
              "Min_Voltage_Limit": 40, "Max_Voltage_Limit": 140, "Goal_Position": 3224,
              "Present_Position": 3224, "Operating_Mode": 0, "Torque_Enable": 0,
              "Present_Current": 0, "Status": 0, "Present_Temperature": 35,
              "Present_Velocity": 0, "Moving": 0, "Present_Voltage": 118}
    old_read = robot.left_bus.read
    robot.left_bus.read = lambda reg, name, **kw: static[reg] if name == SHOULDER else old_read(reg, name, **kw)
    spec = LIVE_ELBOW_SPECS["physical-live-right-elbow"]
    robot.right_bus = SimpleNamespace(motors={spec.joint.removesuffix(".pos"): Motor(3, "sts3215", MotorNormMode.RANGE_M100_100)})
    robot._am1_visible_control_binding = {"run_id": str(uuid4()), "host_incarnation": str(uuid4()), "recipe": spec.recipe}
    robot._am1_visible_control_evidence = SimpleNamespace(live_spec=spec)
    robot._am1_live_endpoint_evidence = []
    return robot, clock, operation


def test_protected_endpoint_post_home_services_real_original_lift_slots(operating_robot):
    robot, clock, operation = protected_post_home_endpoint(operating_robot)
    window, origin = operation.temperature, operation.temperature._origin_ns
    before = operation.last_record
    goal = robot._qualify_am1_live_endpoint(robot.left_bus, SHOULDER, (2, 0, 515, 848, 3223), phase="after_home")
    assert goal == 3223 and robot._am1_live_endpoint_evidence[-1]["window_s"] >= .4
    assert operation.temperature is window and window._origin_ns == origin
    assert operation.last_record is not before and operation.failure is None and window.failure is None
    assert all(clock.now - stamp <= window.MAX_AGE_S for stamp, _ in window.samples)
    assert len(window.samples) == 5 and window.ready


def test_protected_endpoint_post_home_missed_lift_poll_latches_original_fault(operating_robot, monkeypatch):
    robot, clock, operation = protected_post_home_endpoint(operating_robot)
    original = operation.poll
    def missed():
        clock.sleep(.6)
        return original()
    monkeypatch.setattr(operation, "poll", missed)
    with pytest.raises(RuntimeError) as caught:
        robot._qualify_am1_live_endpoint(robot.left_bus, SHOULDER, (2, 0, 515, 848, 3223), phase="after_home")
    assert operation.failure is caught.value
    assert "temperature" in str(caught.value) or "40 ms" in str(caught.value)
    assert not robot._am1_live_endpoint_evidence
    with pytest.raises(RuntimeError) as again:robot._refresh_am1_startup_lift_feedback()
    assert again.value is caught.value
