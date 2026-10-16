"""Installed role settings remain unchanged by Packet16 startup."""

from uuid import uuid4

import pytest

from tests.robots.test_am1_pre_activation import activation as activation


def configured_robot(activation):
    robot = activation.robot
    robot.config.no_follower = False
    robot.config.use_degrees = False
    robot._am1_teleop_binding = {
        "recipe": "physical-live-teleop",
        "run_id": str(uuid4()),
        "host_incarnation": str(uuid4()),
    }
    robot.lift.configure = lambda **kwargs: None
    for bus in (robot.left_bus, robot.right_bus):
        for name in bus.motors:
            for register, value in {
                "ID": bus.motors[name].id,
                "Model_Number": 777,
                "Firmware_Major_Version": 3,
                "Firmware_Minor_Version": 10,
                "Operating_Mode": 0,
                "Phase": 12,
                "P_Coefficient": 24,
                "I_Coefficient": 0,
                "D_Coefficient": 19,
                "Acceleration": 73,
                "Maximum_Acceleration": 100,
                "Protection_Current": 310,
                "Max_Temperature_Limit": 70,
                "Min_Voltage_Limit": 40,
                "Max_Voltage_Limit": 140,
                "Status": 0,
                "Present_Temperature": 28,
                "Present_Voltage": 120,
                "Present_Current": 2,
            }.items():
                bus.registers[register, name] = value

        def grouped(register, names, *, normalize=True, _bus=bus, **kwargs):
            return {name: _bus.read(register, name, normalize=normalize) for name in names}

        bus.sync_read = grouped
    robot.configure()
    return robot


def test_teleop_configure_preserves_installed_arm_settings(activation):
    robot = configured_robot(activation)
    case = activation
    forbidden = {
        "P_Coefficient",
        "I_Coefficient",
        "D_Coefficient",
        "Acceleration",
        "Maximum_Acceleration",
        "Phase",
        "Homing_Offset",
        "Min_Position_Limit",
        "Max_Position_Limit",
    }
    assert not [row for row in case.events if row[0] == "write" and row[2] in forbidden]
    assert robot._am1_teleop_settings["left"]["registers"]["P_Coefficient"]["arm_left_wrist_flex"] == 24


def test_actual_status_fault_is_retained_before_a_later_transport_failure(activation):
    from lerobot.robots.alohamini.am1_live_teleop import poll_health

    robot = configured_robot(activation)
    original = robot.left_bus.sync_read

    def fault(register, names, **kwargs):
        if register == "Status":
            return dict.fromkeys(names, 1)
        if register == "Present_Temperature":
            raise OSError("later transport loss")
        return original(register, names, **kwargs)

    robot.left_bus.sync_read = fault
    with pytest.raises(RuntimeError, match="status"):
        poll_health(robot)
    assert robot._am1_teleop_fault["register"] == "Status"


@pytest.mark.parametrize(
    ("register", "raw", "reason"),
    [("Present_Temperature", 70, "temperature"), ("Present_Voltage", 39, "voltage"), ("Status", 1, "status")],
)
def test_installed_health_limits_remain_effective(activation, register, raw, reason):
    from lerobot.robots.alohamini.am1_live_teleop import poll_health

    robot = configured_robot(activation)
    for bus in (robot.left_bus, robot.right_bus):
        for name in bus.calibration:
            bus.registers["Torque_Enable", name] = bus.registers["Lock", name] = 1
    robot.left_bus.registers[register, "arm_left_gripper"] = raw
    with pytest.raises(RuntimeError, match=reason):
        poll_health(robot)


def test_actual_hardware_current_limit_precedes_other_bus_failure(activation):
    robot = configured_robot(activation)
    robot._last_currents_log_t, robot._overcurrent_count, robot._overcurrent_trip_n = 0.0, {}, 20
    robot.left_bus.registers["Present_Current", "arm_left_gripper"] = 311
    robot.right_bus.sync_read = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("later right bus loss"))
    with pytest.raises(RuntimeError, match="current"):
        robot.read_and_check_currents(limit_ma=2000, print_currents=False)
    assert robot._am1_teleop_fault["raw"]["arm_left_gripper"] == 311
