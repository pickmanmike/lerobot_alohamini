"""Installed role settings remain unchanged by Packet16 startup."""

from unittest.mock import patch
from uuid import uuid4

import pytest

from tests.robots.test_am1_pre_activation import activation as activation


def configured_robot(activation, *, initial_temperature=28):
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
        import scservo_sdk as sdk

        bus.port_handler = ReplyPort()
        bus.packet_handler = sdk.PacketHandler(0)
        bus.sync_reader = sdk.GroupSyncRead(bus.port_handler, bus.packet_handler, 0, 0)
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
                "Present_Temperature": initial_temperature,
                "Present_Voltage": 120,
                "Present_Current": 2,
            }.items():
                bus.registers[register, name] = value

        def grouped(register, names, *, normalize=True, _bus=bus, **kwargs):
            values = {name: _bus.read(register, name, normalize=normalize) for name in names}
            activation.clock.now += 0.000001  # Synthetic finite original transaction.
            return values

        bus.sync_read = grouped
    activation.clock.now = 99.599
    with patch("time.sleep", side_effect=lambda seconds: setattr(
        activation.clock, "now", activation.clock.now + seconds
    )):
        robot.configure()
    activation.clock.now = 100.0
    for bus in (robot.left_bus, robot.right_bus):
        from lerobot.motors.feetech.tables import MODEL_CONTROL_TABLE
        from lerobot.motors.motors_bus import MotorsBus

        bus._setup_sync_reader = MotorsBus._setup_sync_reader.__get__(bus)
        bus._sync_read = MotorsBus._sync_read.__get__(bus)
        bus._is_comm_success = lambda result: result == 0

        def respond(packet, _bus=bus):
            assert packet[4:7] == [130, 33, 38]
            _bus.events.append(("group_read", _bus.side, 33, 38, tuple(packet[7:-1])))
            if _bus.on_read is not None:
                _bus.on_read("Live_Health_Block", None)
            packets = []
            names = {motor.id: name for name, motor in _bus.motors.items()}
            for motor_id in packet[7:-1]:
                payload = [0] * 38
                for register, (address, width) in MODEL_CONTROL_TABLE["sts3215"].items():
                    if address >= 33 and address + width <= 71:
                        value = _bus.registers.get((register, names[motor_id]), 0)
                        payload[address - 33 : address - 33 + width] = [
                            (value >> (8 * index)) & 255 for index in range(width)
                        ]
                packets.append(reply_packet(motor_id, payload))
            return [byte for packet in packets for byte in packet]

        bus.port_handler.respond = respond
    return robot


class ReplyPort:
    """The real SDK reads synthetic bytes; no serial port is constructed."""

    def __init__(self):
        self.buffer, self.writes, self.clears = [], [], 0
        self.respond = None
        self.is_using = False

    def readPort(self, count):  # noqa: N802 - exact SDK port interface
        result, self.buffer = self.buffer[:count], self.buffer[count:]
        return result

    def writePort(self, packet):  # noqa: N802 - exact SDK port interface
        self.writes.append(list(packet))
        if self.respond is not None:
            self.buffer.extend(self.respond(packet))
        return len(packet)

    def clearPort(self):  # noqa: N802 - exact SDK port interface
        self.clears += 1

    def setPacketTimeout(self, _length):  # noqa: N802 - exact SDK port interface
        pass

    def isPacketTimeout(self):  # noqa: N802 - exact SDK port interface
        return not self.buffer


def reply_packet(motor_id, payload, error=0):
    packet = [255, 255, motor_id, len(payload) + 2, error, *payload]
    return [*packet, (~sum(packet[2:])) & 255]


def sdk_bus(robot, side, packets):
    from lerobot.motors.feetech.tables import MODEL_CONTROL_TABLE
    from lerobot.motors.motors_bus import MotorsBus

    bus = getattr(robot, side + "_bus")
    bus.port_handler.buffer = [byte for packet in packets for byte in packet]
    bus.port_handler.respond = None
    bus._setup_sync_reader = MotorsBus._setup_sync_reader.__get__(bus)
    bus._is_comm_success = lambda result: result == 0

    def read(register, names, *, normalize=False, num_retry=0):
        assert normalize is False and num_retry == 0
        address, width = MODEL_CONTROL_TABLE["sts3215"][register]
        ids = [bus.motors[name].id for name in names]
        values, _ = MotorsBus._sync_read(bus, address, width, ids, num_retry=num_retry)
        return {name: values[bus.motors[name].id] for name in names}

    bus.sync_read = read
    return bus


def prime_thermal_highs(activation, robot, name="arm_left_shoulder_pan", value=70):
    """Synthetic preceding high slots; the original wire fixture remains final."""
    from lerobot.robots.alohamini.am1_live_teleop import read_groups

    side = "left" if name.startswith("arm_left_") else "right"
    bus = getattr(robot, side + "_bus")
    bus.registers["Present_Temperature", name] = value
    for when in (100.1, 100.2):
        activation.clock.now = when
        read_groups(robot, ("Present_Temperature",))
    activation.clock.now = 100.3


@pytest.mark.parametrize("payload", [[], [28]])
def test_group_reply_device_error_precedes_sdk_checksum_as_data(activation, payload):
    robot = configured_robot(activation)
    packets = [reply_packet(1, payload, error=126), *[reply_packet(i, [28]) for i in range(2, 7)]]
    bus = sdk_bus(robot, "left", packets)
    original_rx = bus.packet_handler.rxPacket
    with pytest.raises(RuntimeError, match="device error") as caught:
        bus.sync_read("Present_Temperature", robot.left_arm_motors, normalize=False, num_retry=0)
    note = fault_note(caught.value)
    assert note["raw"]["packet"] == packets[0]
    assert note["raw"]["device_error"] == 126
    assert note["comparison"]["request"]["address"] == 63
    assert note["comparison"]["request"]["width"] == 1
    assert note["comparison"]["request"]["ids"] == [1, 2, 3, 4, 5, 6]
    assert note["binding"] == robot._am1_teleop_binding
    assert bus.packet_handler.rxPacket == original_rx
    assert len(bus.port_handler.writes) == bus.port_handler.clears == 1
    assert bus.sync_reader.last_result is False
    assert bus.sync_reader.data_dict[1] == []  # Failure occurs before SDK slicing.


def test_group_reply_real126_still_reaches_original_thermal_guard(activation):
    from lerobot.robots.alohamini.am1_live_teleop import read_groups

    robot = configured_robot(activation)
    prime_thermal_highs(activation, robot, value=126)
    bus = sdk_bus(robot, "left", [reply_packet(i, [126 if i == 1 else 28]) for i in range(1, 7)])
    original_rx = bus.packet_handler.rxPacket
    with pytest.raises(RuntimeError, match="actual configured temperature limit") as caught:
        read_groups(robot, ("Present_Temperature",))
    assert fault_note(caught.value)["raw"]["arm_left_shoulder_pan"] == 126
    assert fault_note(caught.value)["comparison"]["upper_exclusive"] == 70
    transaction = fault_note(caught.value)["group_reply"]
    assert transaction["request"]["address"] == 63 and transaction["request"]["width"] == 1
    assert transaction["comm_result"] == 0 and len(transaction["packets"]) == 6
    assert transaction["packets"][0]["packet"] == [255, 255, 1, 3, 0, 126, 125]
    assert transaction["packets"][0]["device_error"] == 0
    assert transaction["binding"] == robot._am1_teleop_binding
    assert transaction["group_call_started_at_monotonic_s"] == 100.3
    assert transaction["group_call_completed_at_monotonic_s"] == 100.3
    assert bus.packet_handler.rxPacket == original_rx
    assert len(bus.port_handler.writes) == bus.port_handler.clears == 1


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
    robot.left_bus.registers["Status", "arm_left_shoulder_lift"] = 1
    robot.left_bus.registers["Present_Temperature", "arm_left_shoulder_pan"] = 74
    robot.right_bus._sync_read = lambda *_, **__: (_ for _ in ()).throw(OSError("later right bus loss"))
    with pytest.raises(RuntimeError, match="status"):
        poll_health(robot)
    assert robot._am1_teleop_fault["register"] == "Status"
    assert len(robot.left_bus.port_handler.writes) == 1
    assert not robot.right_bus.port_handler.writes


@pytest.mark.parametrize(
    ("register", "raw", "reason"),
    [("Present_Temperature", 70, "temperature"), ("Present_Voltage", 39, "voltage"), ("Status", 1, "status")],
)
def test_installed_health_limits_remain_effective(activation, register, raw, reason):
    from lerobot.robots.alohamini.am1_live_teleop import poll_health

    robot = configured_robot(activation)
    if register == "Present_Temperature":
        prime_thermal_highs(activation, robot, "arm_left_gripper")
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


def fault_note(error):
    import json

    prefix = "[AM1 TELEOP FAULT] "
    notes = [note for note in getattr(error, "__notes__", []) if note.startswith(prefix)]
    assert len(notes) == 1, "original AM1 fault snapshot must survive the existing exception path"
    assert len(notes[0].encode("utf-8")) <= 16384
    return json.loads(notes[0][len(prefix) :])


@pytest.mark.parametrize(
    ("register", "value", "reason", "configured"),
    [
        ("Present_Temperature", 70, "temperature", {"Max_Temperature_Limit": 70}),
        ("Present_Temperature", -1, "temperature", {"Max_Temperature_Limit": 70}),
        ("Present_Voltage", 39, "voltage", {"Min_Voltage_Limit": 40, "Max_Voltage_Limit": 140}),
        ("Status", 1, "status", {}),
        ("Torque_Enable", 0, "torque/lock", {}),
        ("Lock", 0, "torque/lock", {}),
        ("Operating_Mode", 1, "operating mode", {}),
    ],
)
def test_fault_note_exports_original_row_interval_binding_and_installed_comparator(
    activation, register, value, reason, configured
):
    from lerobot.robots.alohamini.am1_live_teleop import HEALTH, poll_health, read_groups

    robot = configured_robot(activation)
    if register == "Present_Temperature" and value >= 70:
        prime_thermal_highs(activation, robot)
    elif register != "Present_Temperature":
        prime_thermal_highs(activation, robot, value=28)
    for bus in (robot.left_bus, robot.right_bus):
        for name in bus.calibration:
            bus.registers["Torque_Enable", name] = bus.registers["Lock", name] = 1
    motor = "arm_left_shoulder_pan"
    robot.left_bus.registers[register, motor] = value
    binding = dict(robot._am1_teleop_binding)
    activation.clock.now = 100.3
    robot._am1_teleop_startup_temperature = False
    robot.left_bus.on_read = lambda *_: setattr(activation.clock, "now", activation.clock.now + 0.001)
    robot.right_bus.sync_read = lambda *_, **__: pytest.fail("fault must precede later right-bus reads")
    boundary = len(activation.events)
    with pytest.raises(RuntimeError, match=reason) as caught:
        if value < 0:
            # Negative numeric values remain a narrow/raw validator regression;
            # a genuine unsigned one-byte wire value cannot encode negative1.
            read_groups(robot, (*HEALTH, "Operating_Mode"), active=True)
        else:
            poll_health(robot)
    snapshot = fault_note(caught.value)
    assert snapshot == robot._am1_teleop_fault
    assert snapshot["binding"] == binding
    assert snapshot["register"] == register and snapshot["offending_motor"] == motor
    assert snapshot["offending_value"] == snapshot["raw"][motor] == value
    assert set(snapshot["raw"]) == set(robot.left_arm_motors)
    order = (*HEALTH, "Operating_Mode")
    expected = order[: order.index(register) + 1]
    reads = activation.events[boundary:]
    if value < 0:
        assert len(reads) == 6 * len(expected) and all(event[0:2] == ("read", "left") for event in reads)
        assert [event[2] for event in reads] == [key for key in expected for _ in range(6)]
        assert snapshot["read_started_at_monotonic_s"] == pytest.approx(100.3 + 0.006 * (len(expected) - 1))
        assert snapshot["read_completed_at_monotonic_s"] == pytest.approx(100.3 + 0.006 * len(expected))
    else:
        assert reads == [("group_read", "left", 33, 38, (1, 2, 3, 4, 5, 6))]
        assert snapshot["read_started_at_monotonic_s"] == 100.3
        assert snapshot["read_completed_at_monotonic_s"] == pytest.approx(100.301)
        assert snapshot["group_reply"]["request"]["width"] == 38
        assert not robot.right_bus.port_handler.writes
    comparison = snapshot["comparison"]
    assert comparison["units"] == "raw_register"
    if register == "Present_Temperature":
        assert comparison["lower_inclusive"] == 0 and comparison["upper_exclusive"] == 70
    elif register == "Present_Voltage":
        assert comparison["lower_inclusive"] == 40 and comparison["upper_inclusive"] == 140
    else:
        assert comparison["expected"] == (1 if register in ("Torque_Enable", "Lock") else 0)
    assert {key: row["value"] for key, row in comparison.get("configured", {}).items()} == configured
    for key, row in comparison.get("configured", {}).items():
        assert {
            name: row[name] for name in robot._am1_teleop_settings["left"]["timing"][key]
        } == robot._am1_teleop_settings["left"]["timing"][key]


def test_fault_note_initial_settings_uses_original_current_capture_without_cached_baseline(activation):
    with pytest.raises(RuntimeError, match="temperature") as caught:
        configured_robot(activation, initial_temperature=70)
    limit = fault_note(caught.value)["comparison"]["configured"]["Max_Temperature_Limit"]
    assert limit["value"] == 70
    assert 99.599 <= limit["read_started_at_monotonic_s"] < limit["read_completed_at_monotonic_s"] < 99.6


def test_fault_note_first_snapshot_survives_mutation_and_actual_cleanup_export(activation, tmp_path):
    import copy
    import json
    import traceback
    from types import SimpleNamespace

    from lerobot.robots.alohamini.alohamini_host import write_am1_cleanup_receipt
    from lerobot.robots.alohamini.am1_live_teleop import poll_health

    robot = configured_robot(activation)
    prime_thermal_highs(activation, robot)
    motor = "arm_left_shoulder_pan"
    robot.left_bus.registers["Present_Temperature", motor] = 70
    with pytest.raises(RuntimeError, match="temperature") as caught:
        poll_health(robot)
    first = caught.value
    original = copy.deepcopy(fault_note(first))
    rendered = "".join(traceback.format_exception(first))
    assert str(first) in rendered and first.__notes__[0] in rendered
    robot.left_bus.registers["Status", motor] = 1
    robot._am1_teleop_binding["run_id"] = str(uuid4())
    robot._am1_teleop_settings["left"]["registers"]["Max_Temperature_Limit"][motor] = 99
    writes = len(robot.left_bus.port_handler.writes)
    with pytest.raises(RuntimeError, match="temperature") as later:
        poll_health(robot)
    assert later.value is first
    assert len(robot.left_bus.port_handler.writes) == writes
    assert fault_note(later.value) == fault_note(first) == robot._am1_teleop_fault == original
    robot._am1_shutdown_evidence = {"verified": False, "buses_closed": False}
    path = tmp_path / "cleanup.json"
    args = SimpleNamespace(
        am1_run_id=original["binding"]["run_id"],
        am1_host_incarnation=original["binding"]["host_incarnation"],
        am1_cleanup_receipt=path,
    )
    receipt = write_am1_cleanup_receipt(args, robot, first, [])
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == receipt
    assert receipt["first_cause"] == "RuntimeError: " + str(first)
    assert receipt["uncertain"] is True and receipt["cleanup"] == "physical_cleanup_unknown"
    assert receipt["first_error_chain"][0]["notes"] == first.__notes__
    assert json.loads(receipt["first_error_chain"][0]["notes"][0].split("] ", 1)[1]) == original


@pytest.mark.parametrize("problem", ["serialization", "oversized"])
def test_fault_note_failure_cannot_replace_original_guard(activation, monkeypatch, problem):
    from lerobot.robots.alohamini import am1_live_teleop as helper

    robot = configured_robot(activation)
    prime_thermal_highs(activation, robot)
    motor = "arm_left_shoulder_pan"
    robot.left_bus.registers["Present_Temperature", motor] = 70

    def encode(*args, **kwargs):
        if problem == "serialization":
            raise ValueError("diagnostic serialization failed")
        return "x" * 16385

    monkeypatch.setattr(helper.json, "dumps", encode)
    with pytest.raises(
        RuntimeError, match="AM1 teleop actual configured temperature limit: " + motor
    ) as caught:
        helper.poll_health(robot)
    assert robot._am1_teleop_fault["raw"][motor] == 70
    assert robot._am1_teleop_fault["offending_motor"] == motor
    assert not getattr(caught.value, "__notes__", [])


def test_fault_note_current_uses_actual_installed_raw_limit_without_later_io(activation):
    from lerobot.robots.alohamini.am1_live_teleop import check_sampled_current

    robot = configured_robot(activation)
    values = dict.fromkeys(robot.left_arm_motors, 2)
    values["arm_left_gripper"] = 311
    boundary = len(activation.events)
    with pytest.raises(RuntimeError, match="current") as caught:
        check_sampled_current(robot, "left", values, 122.0, 122.003)
    snapshot = fault_note(caught.value)
    assert snapshot["raw"] == values and snapshot["offending_motor"] == "arm_left_gripper"
    assert snapshot["comparison"]["absolute_upper_exclusive"] == 310
    assert snapshot["comparison"]["configured"]["Protection_Current"]["value"] == 310
    assert snapshot["comparison"]["units"] == "raw_register"
    assert snapshot["read_started_at_monotonic_s"] == 122.0
    assert snapshot["read_completed_at_monotonic_s"] == 122.003
    assert activation.events[boundary:] == []


@pytest.mark.parametrize("kind", ["temperature", "current"])
def test_fault_note_malformed_baseline_timing_cannot_replace_proven_guard(activation, kind):
    from lerobot.robots.alohamini.am1_live_teleop import check_sampled_current, poll_health

    robot = configured_robot(activation)
    if kind == "temperature":
        prime_thermal_highs(activation, robot)
    motor = "arm_left_shoulder_pan"
    robot._am1_teleop_settings["left"]["timing"] = None
    robot.left_bus.registers["Present_Temperature", motor] = 70
    with pytest.raises(RuntimeError, match="actual configured " + kind) as caught:
        if kind == "temperature":
            poll_health(robot)
        else:
            values = dict.fromkeys(robot.left_arm_motors, 2)
            values[motor] = 311
            check_sampled_current(robot, "left", values, 125.0, 125.002)
    snapshot = fault_note(caught.value)
    assert snapshot["offending_motor"] == motor
    assert snapshot["offending_value"] == (70 if kind == "temperature" else 311)
    assert snapshot["comparison"]["units"] == "raw_register"


def resting_robot(activation, monkeypatch, raw=3225):
    from lerobot.motors import MotorCalibration
    from lerobot.motors.feetech import FeetechMotorsBus

    robot, name = activation.robot, "arm_left_shoulder_lift"
    robot.left_bus.calibration[name] = MotorCalibration(2, 0, 515, 848, 3223)
    robot.left_bus.registers.update(
        {
            (register, name): value
            for register, value in {
                "Min_Position_Limit": 848,
                "Max_Position_Limit": 3223,
                "Homing_Offset": 515,
                "Present_Position": raw,
                "Goal_Position": 3223,
            }.items()
        }
    )
    robot = configured_robot(activation)
    robot.config.max_relative_target = 20.0
    robot._joint_hold_goal, robot._gripper_hold_goal = {}, {}
    for bus in (robot.left_bus, robot.right_bus):
        normalizer = FeetechMotorsBus("unused-rest16", bus.motors, bus.calibration)
        bus.apply_drive_mode = normalizer.apply_drive_mode
        bus.model_resolution_table = normalizer.model_resolution_table
        bus._normalize, bus._unnormalize = normalizer._normalize, normalizer._unnormalize
        for motor in bus.motors:
            for register in ("Present_Velocity", "Moving", "CW_Dead_Zone", "CCW_Dead_Zone"):
                bus.registers[register, motor] = 0 if register in ("Present_Velocity", "Moving") else 1
    monkeypatch.setattr(
        "lerobot.robots.alohamini.alohamini.time.sleep",
        lambda seconds: setattr(activation.clock, "now", activation.clock.now + seconds),
    )
    return robot


@pytest.mark.parametrize("raw", [3224, 3225, 3228])
def test_supported_rest_qualifies_fresh_series_and_only_seeds_inward(activation, monkeypatch, raw):
    robot = resting_robot(activation, monkeypatch, raw)
    robot.activate_motors()
    name = "arm_left_shoulder_lift"
    assert robot.left_bus.registers["Present_Position", name] == raw
    assert robot.left_bus.registers["Goal_Position", name] == 3223
    assert [
        row[4] for row in activation.events if row[0] == "write" and row[2:4] == ("Goal_Position", name)
    ] == [3223]
    assert robot._normalize_arm_feedback(robot.left_bus, {name: raw})[name] == pytest.approx(
        {3224: 100.08421052631579, 3225: 100.16842105263157, 3228: 100.42105263157896}[raw]
    )
    proof = robot._am1_teleop_rest_evidence
    assert [entry["phase"] for entry in proof] == ["before_home", "after_home"]
    assert all(entry["qualified"] and len(entry["samples"]) >= 9 for entry in proof)
    assert all(entry["window_s"] >= 0.4 and entry["seed_raw"] == 3223 for entry in proof)
    assert proof[0]["samples"][-1]["completed_at"] <= proof[1]["samples"][0]["started_at"]
    # Installed P24/D19 are deliberately different from the old selected diagnostic fingerprint.
    assert robot.left_bus.registers["P_Coefficient", name] == 24
    assert robot.left_bus.registers["D_Coefficient", name] == 19


@pytest.mark.parametrize("phase", ["before_home", "after_home"])
@pytest.mark.parametrize(
    "problem",
    ["moving", "status", "current", "velocity", "temperature", "voltage", "drifting", "cap", "settings"],
)
def test_rest_fault_or_unstable_series_refuses_without_arm_goals(activation, monkeypatch, phase, problem):
    robot = resting_robot(activation, monkeypatch)
    name, reads = "arm_left_shoulder_lift", [0]

    def mutate(register, motor):
        if motor != name or (phase == "after_home") != bool(activation.home_calls):
            return
        values = {
            "moving": ("Moving", 1),
            "status": ("Status", 1),
            "current": ("Present_Current", 24),
            "velocity": ("Present_Velocity", 1),
            "temperature": ("Present_Temperature", 70),
            "voltage": ("Present_Voltage", 39),
            "settings": ("P_Coefficient", 25),
        }
        if problem in values:
            reg, value = values[problem]
            robot.left_bus.registers[reg, name] = value
        elif problem == "drifting" and register == "Present_Position":
            reads[0] += 1
            robot.left_bus.registers[register, name] = 3224 + min(3, reads[0])
        elif problem == "cap":
            robot.config.max_relative_target = 0.01

    robot.left_bus.on_read = mutate
    with pytest.raises(RuntimeError, match="activation failed"):
        robot.activate_motors()
    assert not [row for row in activation.events if row[0] == "write" and row[2] == "Goal_Position"]
    assert not [
        row
        for row in activation.events
        if row[0] == "write" and row[2] == "Torque_Enable" and row[3].startswith("arm_") and row[4] == 1
    ]
    assert activation.home_calls == (phase == "after_home")


@pytest.mark.parametrize(
    "problem", ["over_half_degree", "lower_endpoint", "other_joint", "foreign_calibration", "foreign_binding"]
)
def test_rest_operating_bound_does_not_expand_calibration_or_other_roles(activation, monkeypatch, problem):
    robot = resting_robot(activation, monkeypatch)
    if problem == "over_half_degree":
        robot.left_bus.registers["Present_Position", "arm_left_shoulder_lift"] = 3229
    elif problem == "lower_endpoint":
        robot.left_bus.registers["Present_Position", "arm_left_shoulder_lift"] = 847
    elif problem == "other_joint":
        robot.left_bus.registers["Present_Position", "arm_left_wrist_flex"] = 2201
    elif problem == "foreign_calibration":
        robot.left_bus.calibration["arm_left_shoulder_lift"].drive_mode = 1
    else:
        robot._am1_teleop_binding["run_id"] = str(uuid4())
        robot._am1_teleop_binding["recipe"] = "physical-live-right-elbow"
    with pytest.raises(RuntimeError, match="activation failed"):
        robot.activate_motors()
    assert activation.home_calls == 0
    assert not [row for row in activation.events if row[0] == "write" and row[2] == "Goal_Position"]


def test_acquired_rest_native_workspace_and_hold_preserve_raw_reference_and_legal_goal(
    activation, monkeypatch
):
    from examples.alohamini.am1_live_teleop import validate_target, validate_workspace
    from lerobot.motors import MotorNormMode
    from lerobot.robots.alohamini.alohamini_host import AM1LocalControl
    from tests.robots.test_am1_live_teleop_native import frame

    robot = resting_robot(activation, monkeypatch)
    for bus, side, elbow in ((robot.left_bus, "left", 2124), (robot.right_bus, "right", 1847)):
        bus.registers["Present_Position", f"arm_{side}_elbow_flex"] = elbow
        bus.motors[f"arm_{side}_gripper"].norm_mode = MotorNormMode.RANGE_0_100
    robot.activate_motors()
    identity = {key: robot._am1_teleop_binding[key] for key in ("run_id", "host_incarnation")}
    owner = {
        key: str(uuid4()) for key in ("service_incarnation", "controller_generation", "connection_generation")
    }
    control = AM1LocalControl(
        **identity, live=True, live_recipe="physical-live-teleop", clock=lambda: activation.clock.now
    )
    robot.stop_motion = lambda: None
    robot.get_observation = lambda: {
        name + ".pos": value
        for bus, names in ((robot.left_bus, robot.left_arm_motors), (robot.right_bus, robot.right_arm_motors))
        for name, value in robot._normalize_arm_feedback(
            bus, {name: bus.registers["Present_Position", name] for name in names}
        ).items()
    }
    boundary = len(activation.events)
    assert control.apply(robot, frame(identity, owner, 1))
    snapshot = control.live_snapshot()
    assert validate_workspace(snapshot["workspace"]) == snapshot["workspace"]
    shoulder = "arm_left_shoulder_lift.pos"
    assert snapshot["reference"][shoulder] == pytest.approx(100.16842105263157)
    assert snapshot["workspace"]["reference"][shoulder] == snapshot["reference"][shoulder]
    assert snapshot["cursor"][shoulder] == snapshot["accepted_target"][shoulder] == 100
    assert validate_target(snapshot["cursor"], workspace=snapshot["workspace"]) == snapshot["cursor"]
    assert snapshot["held_goals_raw"]["left"]["arm_left_shoulder_lift"] == 3223
    assert not [
        event for event in activation.events[boundary:] if event[0] == "write" and event[2] == "Goal_Position"
    ]
    assert control.apply(robot, frame(identity, owner, 2, revision=1))
    assert robot.left_bus.registers["Goal_Position", "arm_left_shoulder_lift"] == 3223


def test_rest_last_settings_read_cannot_mutate_binding_before_goal_seed(activation, monkeypatch):
    robot = resting_robot(activation, monkeypatch)
    reads = [0]

    def change(register, name):
        if activation.home_calls and name == "arm_left_shoulder_lift" and register == "CCW_Dead_Zone":
            reads[0] += 1
            if reads[0] == 2:
                robot._am1_teleop_binding["run_id"] = str(uuid4())

    robot.left_bus.on_read = change
    with pytest.raises(RuntimeError, match="activation failed"):
        robot.activate_motors()
    assert not [row for row in activation.events if row[0] == "write" and row[2] == "Goal_Position"]


def test_rest_at_legal_endpoint_qualifies_before_home_then_keeps_fresh_actual_after_home(
    activation, monkeypatch
):
    robot = resting_robot(activation, monkeypatch, raw=3223)
    activation.home_change = lambda source: source.left_bus.registers.update(
        {("Present_Position", "arm_left_shoulder_lift"): 3225}
    )
    robot.activate_motors()
    assert [row["phase"] for row in robot._am1_teleop_rest_evidence] == ["before_home", "after_home"]
    assert [row["measured_raw"] for row in robot._am1_teleop_rest_evidence] == [3223, 3225]
    assert robot.left_bus.registers["Goal_Position", "arm_left_shoulder_lift"] == 3223


def test_rest_home_boundary_cannot_replace_original_protected_run(activation, monkeypatch):
    robot = resting_robot(activation, monkeypatch)
    original = dict(robot._am1_teleop_binding)
    activation.home_change = lambda source: source._am1_teleop_binding.update(run_id=str(uuid4()))
    with pytest.raises(RuntimeError, match="activation failed"):
        robot.activate_motors()
    assert robot._am1_teleop_rest_evidence[0]["binding"] == original
    assert not [row for row in activation.events if row[0] == "write" and row[2] == "Goal_Position"]
    assert not [
        row
        for row in activation.events
        if row[0] == "write" and row[2] == "Torque_Enable" and row[3].startswith("arm_") and row[4] == 1
    ]


@pytest.mark.parametrize("problem", ["slow_read", "first_status_then_transport"])
def test_rest_original_read_budget_and_first_cause_survive_partial_acquisition(
    activation, monkeypatch, problem
):
    robot = resting_robot(activation, monkeypatch)

    def disturb(register, name):
        if name != "arm_left_shoulder_lift":
            return
        if register == "Status":
            if problem == "slow_read":
                activation.clock.now += 0.041
            else:
                robot.left_bus.registers[register, name] = 1
                robot.left_bus.read_faults["Present_Temperature", name] = OSError("later transport fault")

    robot.left_bus.on_read = disturb
    with pytest.raises(RuntimeError) as caught:
        robot.activate_motors()
    cause = caught.value.__cause__
    assert ("40ms" if problem == "slow_read" else "Status") in str(cause)
    proof = robot._am1_teleop_rest_evidence[0]
    assert proof["qualified"] is False
    if problem == "slow_read":
        original = next(
            row for row in reversed(robot._am1_activation_readbacks) if row["register"] == "Status"
        )
        assert original["raw"] == 0
        assert original["read_completed_at"] - original["read_started_at"] == pytest.approx(0.041)
    else:
        assert proof["samples"][0]["Status"] == 1
    assert proof["first_cause"] in cause.__notes__[0]
    assert activation.home_calls == 0


@pytest.mark.parametrize("problem", ["expired", "illegal_goal", "changed_calibration"])
def test_teleop_actual_hold_refuses_bad_or_expired_goal_acquisition_without_reseeding(
    activation, monkeypatch, problem
):
    robot = resting_robot(activation, monkeypatch)
    robot.activate_motors()
    boundary = len(activation.events)
    if problem == "illegal_goal":
        robot.right_bus.registers["Goal_Position", "arm_right_wrist_roll"] = 2201
    else:

        def disturb(register, name):
            if register == "Goal_Position" and name == "arm_right_gripper":
                if problem == "expired":
                    activation.clock.now += 1.001
                else:
                    robot.right_bus.calibration[name].drive_mode = 1

        robot.right_bus.on_read = disturb
    with pytest.raises(RuntimeError, match="teleop"):
        robot.hold_follower_arms()
    assert not [row for row in activation.events[boundary:] if row[0] == "write"]


@pytest.mark.parametrize(
    ("packet", "reason"),
    [
        (reply_packet(1, []), "payload width"),
        (reply_packet(1, [28, 29]), "payload width"),
        (reply_packet(7, [28]), "unexpected"),
    ],
)
def test_group_reply_malformed_success_never_becomes_data(activation, packet, reason):
    robot = configured_robot(activation)
    bus = sdk_bus(robot, "left", [packet])
    original_rx = bus.packet_handler.rxPacket
    with pytest.raises(RuntimeError, match=reason) as caught:
        bus.sync_read("Present_Temperature", robot.left_arm_motors, normalize=False, num_retry=0)
    assert fault_note(caught.value)["raw"]["packet"] == packet
    assert bus.packet_handler.rxPacket == original_rx
    assert bus.sync_reader.data_dict[1] == []
    assert len(bus.port_handler.writes) == bus.port_handler.clears == 1


def test_group_reply_duplicate_id_refuses_before_second_sdk_slice(activation):
    robot = configured_robot(activation)
    bus = sdk_bus(robot, "left", [reply_packet(1, [28]), reply_packet(1, [29])])
    with pytest.raises(RuntimeError, match="duplicate"):
        bus.sync_read("Present_Temperature", robot.left_arm_motors, normalize=False, num_retry=0)
    assert bus.sync_reader.data_dict[1] == [28] and bus.sync_reader.data_dict[2] == []


@pytest.mark.parametrize(
    ("side", "register", "payload", "value", "count"),
    [
        ("left", "Present_Temperature", [28], 28, 6),
        ("right", "Present_Voltage", [120], 120, 6),
        ("left", "Present_Position", [214, 8], 2262, 6),
        ("right", "Present_Current", [54, 1], 310, 6),
        ("left", "Present_Current", [54, 1], 310, 10),
    ],
)
def test_group_reply_healthy_actual_sdk_values_requests_and_io_are_unchanged(
    activation, side, register, payload, value, count
):
    robot = configured_robot(activation)
    names = list(getattr(robot, side + "_bus").motors)[:count]
    ids = [getattr(robot, side + "_bus").motors[name].id for name in names]
    packets = [reply_packet(motor_id, payload) for motor_id in ids]
    bus = sdk_bus(robot, side, packets)
    original_rx = bus.packet_handler.rxPacket
    assert bus.sync_read(register, names, normalize=False, num_retry=0) == dict.fromkeys(names, value)
    assert len(bus.port_handler.writes) == bus.port_handler.clears == 1
    assert bus.port_handler.writes[0][7:-1] == ids
    assert bus.packet_handler.rxPacket == original_rx
    receipt = robot._am1_teleop_group_receipts[side]
    assert receipt["request"]["ids"] == ids and len(receipt["packets"]) == count
    assert receipt["comm_result"] == 0


@pytest.mark.parametrize("failure", ["timeout", "read_exception"])
def test_group_reply_original_transport_failure_and_restore_are_unchanged(activation, failure):
    robot = configured_robot(activation)
    bus = sdk_bus(robot, "left", [])
    original_rx = bus.packet_handler.rxPacket
    original = OSError("original port read failure")
    if failure == "read_exception":
        bus.port_handler.readPort = lambda _count: (_ for _ in ()).throw(original)
    with pytest.raises(ConnectionError if failure == "timeout" else OSError) as caught:
        bus.sync_read("Present_Position", robot.left_arm_motors, normalize=False, num_retry=0)
    if failure == "read_exception":
        assert caught.value is original
    assert bus.packet_handler.rxPacket == original_rx
    assert len(bus.port_handler.writes) == bus.port_handler.clears == 1
    assert not getattr(robot, "_am1_teleop_fault", None)


@pytest.mark.parametrize("change", ["binding", "model", "bus", "mapping", "foreign_rx", "disconnected"])
def test_group_reply_owner_cannot_change_after_install_or_dispatch(activation, change):
    robot = configured_robot(activation)
    bus = sdk_bus(robot, "left", [reply_packet(i, [28]) for i in range(1, 7)])
    if change == "binding":
        robot._am1_teleop_binding["run_id"] = str(uuid4())
    elif change == "model":
        robot.config.robot_model = "alohamini2"
    elif change == "bus":
        robot.left_bus = robot.right_bus
    elif change == "mapping":
        bus.motors["arm_left_shoulder_pan"].id = 21
    elif change == "disconnected":
        bus.is_connected = False
    else:
        bus.packet_handler.rxPacket = lambda *_: pytest.fail("foreign decoder called")
    with pytest.raises(RuntimeError, match="owner/binding"):
        bus.sync_read("Present_Temperature", robot.left_arm_motors, normalize=False, num_retry=0)
    assert bus.port_handler.writes == [] and bus.port_handler.clears == 0
    assert "left" not in robot._am1_teleop_group_receipts


def test_group_reply_install_idempotent_but_not_foreign_or_new_binding(activation):
    from lerobot.robots.alohamini.am1_live_teleop import prepare_settings

    robot = configured_robot(activation)
    installed = robot.left_bus.sync_reader.txRxPacket
    prepare_settings(robot)
    assert robot.left_bus.sync_reader.txRxPacket is installed
    robot._am1_teleop_binding["run_id"] = str(uuid4())
    with pytest.raises(RuntimeError, match="guard owner changed"):
        prepare_settings(robot)
    assert robot.left_bus.sync_reader.txRxPacket is installed


def test_group_reply_scope_does_not_change_sdk_or_unowned_manual_reader(activation):
    import scservo_sdk as sdk

    robot = configured_robot(activation)
    bus = sdk_bus(robot, "left", [reply_packet(1, [], 126)])
    original_rx = bus.packet_handler.rxPacket
    with pytest.raises(RuntimeError, match="device error"):
        bus.sync_read("Present_Temperature", robot.left_arm_motors, normalize=False, num_retry=0)
    assert bus.packet_handler.rxPacket == original_rx
    port, handler = ReplyPort(), sdk.PacketHandler(0)
    port.buffer = reply_packet(1, [], 126)
    group = sdk.GroupSyncRead(port, handler, 63, 1)
    group.addParam(1)
    assert group.rxPacket() == 0 and group.getData(1, 63, 1) == 126  # Upstream untouched.


def test_group_reply_note_preserves_first_packet_through_actual_cleanup(activation, tmp_path):
    import json
    from types import SimpleNamespace

    from lerobot.robots.alohamini.alohamini_host import write_am1_cleanup_receipt

    robot = configured_robot(activation)
    bus = sdk_bus(robot, "left", [reply_packet(1, [], 126)])
    with pytest.raises(RuntimeError, match="device error") as caught:
        bus.sync_read("Present_Temperature", robot.left_arm_motors, normalize=False, num_retry=0)
    error = caught.value
    original = fault_note(error)
    bus.port_handler.buffer = reply_packet(1, [], 32)
    with pytest.raises(RuntimeError, match="device error") as later:
        bus.sync_read("Present_Temperature", robot.left_arm_motors, normalize=False, num_retry=0)
    assert fault_note(later.value) == original
    robot._am1_shutdown_evidence = {"verified": False, "buses_closed": False}
    args = SimpleNamespace(
        am1_run_id=robot._am1_teleop_binding["run_id"],
        am1_host_incarnation=robot._am1_teleop_binding["host_incarnation"],
        am1_cleanup_receipt=tmp_path / "cleanup.json",
    )
    result = write_am1_cleanup_receipt(args, robot, error, [])
    assert json.loads(result["first_error_chain"][0]["notes"][0].split("] ", 1)[1]) == original
    assert result["first_cause"] == "RuntimeError: " + str(error)


@pytest.mark.parametrize("change", ["binding", "request"])
def test_group_reply_mid_read_context_change_fails_before_slice(activation, change):
    robot = configured_robot(activation)
    bus = sdk_bus(robot, "left", [reply_packet(i, [28]) for i in range(1, 7)])
    original_rx, read_port = bus.packet_handler.rxPacket, bus.port_handler.readPort

    def changed(count):
        if change == "binding":
            robot._am1_teleop_binding["host_incarnation"] = str(uuid4())
        else:
            bus.sync_reader.start_address = 62
        return read_port(count)

    bus.port_handler.readPort = changed
    with pytest.raises(RuntimeError, match="owner/request changed"):
        bus.sync_read("Present_Temperature", robot.left_arm_motors, normalize=False, num_retry=0)
    assert bus.packet_handler.rxPacket == original_rx
    assert bus.sync_reader.data_dict[1] == []
    assert len(bus.port_handler.writes) == 1


@pytest.mark.parametrize("change", ["foreign_tx", "foreign_rx", "reader", "port"])
def test_group_reply_install_refuses_foreign_replacement(activation, change):
    import scservo_sdk as sdk

    from lerobot.robots.alohamini.am1_live_teleop import prepare_settings

    robot = configured_robot(activation)
    bus = robot.left_bus
    if change == "foreign_tx":
        bus.sync_reader.txRxPacket = lambda: pytest.fail("foreign group called")
    elif change == "foreign_rx":
        bus.packet_handler.rxPacket = lambda *_: pytest.fail("foreign decoder called")
    elif change == "reader":
        bus.sync_reader = sdk.GroupSyncRead(bus.port_handler, bus.packet_handler, 0, 0)
    else:
        bus.port_handler = ReplyPort()
    boundary = len(activation.events)
    with pytest.raises(RuntimeError, match="guard owner changed"):
        prepare_settings(robot)
    assert activation.events[boundary:] == []


@pytest.mark.parametrize("change", ["header", "checksum", "boolean_byte", "length"])
def test_group_reply_checks_success_frame_before_sdk_slice(activation, monkeypatch, change):
    import scservo_sdk as sdk

    packet = reply_packet(1, [28])
    if change == "header":
        packet[0] = 254
    elif change == "checksum":
        packet[-1] ^= 1
    elif change == "boolean_byte":
        packet[2] = True
    else:
        packet[3] = 4
        packet[-1] = (~sum(packet[2:-1])) & 255
    # Controlled SDK return boundary covers defense beyond the real-parser replay tests above.
    handler_type = type(sdk.PacketHandler(0))
    monkeypatch.setattr(handler_type, "rxPacket", lambda self, port: (packet, 0))
    robot = configured_robot(activation)
    bus = sdk_bus(robot, "left", [])
    original_rx = bus.packet_handler.rxPacket
    with pytest.raises(
        RuntimeError, match="payload width" if change == "length" else "frame/header/checksum"
    ):
        bus.sync_read("Present_Temperature", robot.left_arm_motors, normalize=False, num_retry=0)
    assert bus.sync_reader.data_dict[1] == []
    assert bus.packet_handler.rxPacket == original_rx


@pytest.mark.parametrize("mismatch", ["register", "interval", "binding"])
def test_group_reply_fault_note_does_not_attribute_stale_transaction(activation, mismatch):
    from lerobot.robots.alohamini.am1_live_teleop import _fault

    robot = configured_robot(activation)
    bus = sdk_bus(robot, "left", [reply_packet(i, [28]) for i in range(1, 7)])
    bus.sync_read("Present_Temperature", robot.left_arm_motors, normalize=False, num_retry=0)
    register, started, completed = "Present_Temperature", 100.0, 100.0
    if mismatch == "register":
        register = "Present_Current"
    elif mismatch == "interval":
        started, completed = 101.0, 101.001
    else:
        robot._am1_teleop_binding["run_id"] = str(uuid4())
    with pytest.raises(RuntimeError) as caught:
        _fault(
            robot,
            "original guard",
            "left",
            register,
            {"arm_left_shoulder_pan": 70},
            {"read_started_at_monotonic_s": started, "read_completed_at_monotonic_s": completed},
        )
    assert "group_reply" not in fault_note(caught.value)


def test_group_reply_new_failed_request_clears_old_packet_provenance(activation):
    robot = configured_robot(activation)
    bus = sdk_bus(robot, "left", [reply_packet(i, [28]) for i in range(1, 7)])
    bus.sync_read("Present_Temperature", robot.left_arm_motors, normalize=False, num_retry=0)
    with pytest.raises(ConnectionError):
        bus.sync_read("Present_Current", robot.left_arm_motors, normalize=False, num_retry=0)
    current = robot._am1_teleop_group_receipts["left"]
    assert current["request"]["register"] == "Present_Current"
    assert current["comm_result"] != 0
    assert all(packet["packet"] == [] for packet in current["packets"])


def test_group_reply_capture_failure_cannot_replace_device_fault(activation, monkeypatch):
    from lerobot.robots.alohamini import am1_live_teleop as helper

    robot = configured_robot(activation)
    bus = sdk_bus(robot, "left", [reply_packet(1, [], 126)])
    original_rx = bus.packet_handler.rxPacket
    monkeypatch.setattr(
        helper.json, "dumps", lambda *_, **__: (_ for _ in ()).throw(ValueError("note failed"))
    )
    with pytest.raises(RuntimeError, match="actual device error: 126") as caught:
        bus.sync_read("Present_Temperature", robot.left_arm_motors, normalize=False, num_retry=0)
    assert not getattr(caught.value, "__notes__", [])
    assert bus.packet_handler.rxPacket == original_rx
    assert bus.sync_reader.data_dict[1] == []


def test_group_reply_packet_snapshot_does_not_follow_later_mutation(activation):
    from lerobot.robots.alohamini.am1_live_teleop import read_groups

    robot = configured_robot(activation)
    prime_thermal_highs(activation, robot, value=126)
    sdk_bus(robot, "left", [reply_packet(i, [126 if i == 1 else 28]) for i in range(1, 7)])
    with pytest.raises(RuntimeError, match="temperature") as caught:
        read_groups(robot, ("Present_Temperature",))
    packet = fault_note(caught.value)["group_reply"]["packets"][0]["packet"]
    robot._am1_teleop_group_receipts["left"]["packets"][0]["packet"][5] = 35
    assert (
        packet
        == fault_note(caught.value)["group_reply"]["packets"][0]["packet"]
        == [255, 255, 1, 3, 0, 126, 125]
    )


@pytest.mark.parametrize("later_expiry", [False, True])
@pytest.mark.parametrize("fault_direction", ["rx", "tx"])
def test_group_reply_swallowed_force_limiter_fault_still_fences_actual_send_action(
    activation, monkeypatch, later_expiry, fault_direction
):
    from types import SimpleNamespace

    from lerobot.robots.alohamini.am1_live_teleop import before_write

    robot = resting_robot(activation, monkeypatch, raw=3223)
    bus = sdk_bus(robot, "left", [reply_packet(1, [], 126)])
    if fault_direction == "tx":
        original_sync = bus.packet_handler.syncReadTx
        bus.packet_handler.syncReadTx = lambda port, address, width, ids, count: original_sync(
            port, address + 1, width, ids, count
        )
    right = sdk_bus(robot, "right", [reply_packet(1, [2, 0]), reply_packet(1, [64, 6])])
    robot.config.max_relative_target = None
    robot.logs = {}
    for name, value in {
        "_gripper_current_limit_ma": 500.0,
        "_gripper_hold_close_step": 3,
        "_gripper_release_margin": 1.0,
        "_gripper_open_direction": {},
        "_gripper_hold_direction": {},
        "_joint_current_limit_ma": 1800.0,
        "_joint_release_margin": 1.0,
        "_joint_hold_direction": {},
    }.items():
        setattr(robot, name, value)
    events = []
    robot.lift.apply_action = lambda action: events.append("lift_before_fault")
    robot._body_to_wheel_raw = lambda *_: dict.fromkeys(robot.base_motors, 0)
    for target_bus in (robot.left_bus, robot.right_bus):
        target_bus.sync_write = lambda *args, **kwargs: events.append("motor_write_after_fault")

    def authority(_):
        if later_expiry and getattr(robot, "_am1_teleop_fault", None):
            from lerobot.robots.alohamini.alohamini_host import AM1LiveInputExpired

            raise AM1LiveInputExpired("later original input expiry")

    control = SimpleNamespace(
        binding={key: robot._am1_teleop_binding[key] for key in ("run_id", "host_incarnation")},
        before_live_write=authority,
        _live_summary={
            "workspace": {
                "calibration": {
                    name: list(robot._am1_activation_calibration(target_bus, name))
                    for target_bus in (robot.left_bus, robot.right_bus)
                    for name in target_bus.calibration
                }
            }
        },
    )
    robot._am1_live_before_write = lambda: before_write(control, robot)
    original_rx = bus.packet_handler.rxPacket
    action = {
        "arm_left_shoulder_pan.pos": 0.0,
        "arm_right_shoulder_pan.pos": 0.0,
        "x.vel": 0.0,
        "y.vel": 0.0,
        "theta.vel": 0.0,
        "lift_axis.vel": 0.0,
    }
    reason = "actual device error: 126" if fault_direction == "rx" else "transmit"
    with pytest.raises(RuntimeError, match=reason) as caught:
        robot.send_action(action)
    assert events == ["lift_before_fault"]
    assert caught.value is robot._am1_teleop_first_error
    assert right.port_handler.writes == []  # The next ordinary limiter cannot acquire again.
    expected = (
        [255, 255, 1, 2, 126, 126] if fault_direction == "rx" else [255, 255, 254, 5, 130, 70, 2, 1, 49]
    )
    assert fault_note(caught.value)["raw"]["packet"] == expected
    assert bus.packet_handler.rxPacket == original_rx
    assert bus.port_handler.clears == 1
    assert len(bus.port_handler.writes) == (1 if fault_direction == "rx" else 0)


@pytest.mark.parametrize(("address", "width", "ids"), [(222, 1, [1]), (56, 1, [1]), (63, 1, [21])])
def test_group_reply_rejects_foreign_request_before_transmit_with_original_context(
    activation, address, width, ids
):
    robot = configured_robot(activation)
    bus = robot.left_bus
    reader = bus.sync_reader
    reader.clearParam()
    reader.start_address, reader.data_length = address, width
    for motor_id in ids:
        reader.addParam(motor_id)
    with pytest.raises(RuntimeError, match="request differs") as caught:
        reader.txRxPacket()
    request = fault_note(caught.value)["comparison"]["request"]
    assert request["address"] == address and request["width"] == width and request["ids"] == ids
    assert request["binding"] == robot._am1_teleop_binding and request["side"] == "left"
    assert bus.port_handler.writes == [] and bus.port_handler.clears == 0


def test_group_reply_actual_ten_id_current_fault_retains_original_complete_transaction(activation):
    from lerobot.robots.alohamini.am1_live_teleop import check_sampled_current

    robot = configured_robot(activation)
    names = list(robot.left_bus.motors)
    packets = [reply_packet(robot.left_bus.motors[name].id, [54, 1]) for name in names]
    bus = sdk_bus(robot, "left", packets)
    values = bus.sync_read("Present_Current", names, normalize=False, num_retry=0)
    with pytest.raises(RuntimeError, match="current protection") as caught:
        check_sampled_current(robot, "left", values, 100.0, 100.0)
    note = fault_note(caught.value)
    assert note["comparison"]["absolute_upper_exclusive"] == 310
    assert len(note["group_reply"]["packets"]) == 10
    assert [packet["packet"] for packet in note["group_reply"]["packets"]] == packets
    assert note["group_reply"]["comm_result"] == 0


def test_group_reply_original_sdk_corrupt_transport_result_is_not_reclassified(activation):
    import scservo_sdk as sdk

    robot = configured_robot(activation)
    packet = reply_packet(1, [], 126)
    packet[-1] ^= 1
    bus = sdk_bus(robot, "left", [packet])
    original_rx = bus.packet_handler.rxPacket
    with pytest.raises(ConnectionError):
        bus.sync_read("Present_Temperature", robot.left_arm_motors, normalize=False, num_retry=0)
    assert not getattr(robot, "_am1_teleop_fault", None)
    assert robot._am1_teleop_group_receipts["left"]["comm_result"] == sdk.COMM_RX_CORRUPT
    assert bus.packet_handler.rxPacket == original_rx
    assert len(bus.port_handler.writes) == bus.port_handler.clears == 1


def test_group_reply_missing_install_state_cannot_duplicate_an_existing_wrapper(activation):
    from lerobot.robots.alohamini.am1_live_teleop import prepare_settings

    robot = configured_robot(activation)
    del robot.left_bus._am1_teleop_reply_guard
    with pytest.raises(RuntimeError, match="already wrapped"):
        prepare_settings(robot)
    assert robot.left_bus.port_handler.writes == []


def test_group_reply_real98_thermal_fault_retains_exact_original_tx_and_rx(activation):
    from lerobot.robots.alohamini.am1_live_teleop import read_groups

    robot = configured_robot(activation)
    prime_thermal_highs(activation, robot, "arm_left_elbow_flex", value=98)
    packets = [reply_packet(i, [98 if i == 3 else 28]) for i in range(1, 7)]
    bus = sdk_bus(robot, "left", packets)
    original_write = bus.port_handler.writePort
    with pytest.raises(RuntimeError, match="actual configured temperature limit") as caught:
        read_groups(robot, ("Present_Temperature",))
    note = fault_note(caught.value)
    assert note["offending_value"] == 98 and note["comparison"]["upper_exclusive"] == 70
    receipt = note["group_reply"]
    assert receipt["transmit"]["packet"] == [255, 255, 254, 10, 130, 63, 1, 1, 2, 3, 4, 5, 6, 32]
    assert receipt["transmit"]["result"] == 14
    assert receipt["transmit"]["write_started_at_monotonic_s"] == 100.3
    assert receipt["transmit"]["write_completed_at_monotonic_s"] == 100.3
    assert receipt["packets"][2]["packet"] == [255, 255, 3, 3, 0, 98, 151]
    assert bus.port_handler.writePort == original_write
    assert bus.port_handler.writes == [receipt["transmit"]["packet"]]
    assert bus.port_handler.clears == 1


@pytest.mark.parametrize("change", ["address", "width", "ids"])
def test_group_reply_wrong_actual_sdk_transmit_refuses_before_port_write(activation, change):
    robot = configured_robot(activation)
    bus = sdk_bus(robot, "left", [reply_packet(i, [28]) for i in range(1, 7)])
    original_write, original_rx = bus.port_handler.writePort, bus.packet_handler.rxPacket
    original_sync = bus.packet_handler.syncReadTx

    def wrong(port, address, width, ids, count):
        if change == "address":
            address = 62
        elif change == "width":
            width = 2
        else:
            ids = [1, 2, 4, 3, 5, 6]
        return original_sync(port, address, width, ids, count)

    bus.packet_handler.syncReadTx = wrong
    with pytest.raises(RuntimeError, match="transmit") as caught:
        bus.sync_read("Present_Temperature", robot.left_arm_motors, normalize=False, num_retry=0)
    note = fault_note(caught.value)
    assert note["comparison"]["request"]["address"] == 63
    assert note["comparison"]["request"]["width"] == 1
    assert note["comparison"]["request"]["ids"] == [1, 2, 3, 4, 5, 6]
    assert note["group_reply"]["transmit"]["packet"] == note["raw"]["packet"]
    assert note["group_reply"]["transmit"]["write_started_at_monotonic_s"] is None
    assert bus.port_handler.writes == [] and bus.port_handler.clears == 1
    assert bus.sync_reader.data_dict[1] == []
    assert bus.port_handler.writePort == original_write and bus.packet_handler.rxPacket == original_rx


@pytest.mark.parametrize("change", ["header", "checksum", "boolean_byte", "extra_byte"])
def test_group_reply_malformed_final_transmit_is_not_forwarded(activation, monkeypatch, change):
    import scservo_sdk as sdk

    packet = [255, 255, 254, 10, 130, 63, 1, 1, 2, 3, 4, 5, 6, 32]
    if change == "header":
        packet[0] = 254
    elif change == "checksum":
        packet[-1] ^= 1
    elif change == "boolean_byte":
        packet[6] = True
    else:
        packet.append(0)
    # Controlled final SDK transmit boundary: no synthetic serial port is opened.
    monkeypatch.setattr(
        type(sdk.PacketHandler(0)), "txPacket", lambda self, port, request: port.writePort(packet)
    )
    robot = configured_robot(activation)
    bus = sdk_bus(robot, "left", [reply_packet(i, [28]) for i in range(1, 7)])
    original_write, original_rx = bus.port_handler.writePort, bus.packet_handler.rxPacket
    with pytest.raises(RuntimeError, match="transmit"):
        bus.sync_read("Present_Temperature", robot.left_arm_motors, normalize=False, num_retry=0)
    assert bus.port_handler.writes == [] and bus.port_handler.clears == 0
    assert bus.port_handler.writePort == original_write and bus.packet_handler.rxPacket == original_rx


@pytest.mark.parametrize("failure", ["short_write", "write_exception"])
def test_group_reply_original_write_object_return_and_failure_are_preserved(activation, monkeypatch, failure):
    original = OSError("original write failure")
    calls = []

    def write(port, packet):
        calls.append(packet)
        if failure == "write_exception":
            raise original
        return 0

    monkeypatch.setattr(ReplyPort, "writePort", write)
    robot = configured_robot(activation)
    bus = sdk_bus(robot, "left", [])
    original_write, original_rx = bus.port_handler.writePort, bus.packet_handler.rxPacket
    with pytest.raises(OSError if failure == "write_exception" else ConnectionError) as caught:
        bus.sync_read("Present_Temperature", robot.left_arm_motors, normalize=False, num_retry=0)
    if failure == "write_exception":
        assert caught.value is original
    assert len(calls) == 1 and type(calls[0]) is list
    assert calls[0] == [255, 255, 254, 10, 130, 63, 1, 1, 2, 3, 4, 5, 6, 32]
    assert bus.port_handler.writePort == original_write and bus.packet_handler.rxPacket == original_rx
    assert not getattr(robot, "_am1_teleop_fault", None)
    receipt = robot._am1_teleop_group_receipts["left"]
    assert receipt["transmit"]["write_completed_at_monotonic_s"] == 100
    assert receipt["transmit"]["result"] == (0 if failure == "short_write" else None)


def complete_health(robot):
    from lerobot.robots.alohamini.am1_live_teleop import poll_health

    for bus in (robot.left_bus, robot.right_bus):
        for name in bus.motors:
            bus.registers["Torque_Enable", name] = bus.registers["Lock", name] = 1
    poll_health(robot)


def test_fault_note_prior_successful_health_keeps_original_binding_value_and_times(activation):
    from lerobot.robots.alohamini.am1_live_teleop import poll_health

    robot = configured_robot(activation)
    complete_health(robot)
    binding = dict(robot._am1_teleop_binding)
    prime_thermal_highs(activation, robot, "arm_left_elbow_flex", value=98)
    robot.left_bus.registers["Present_Temperature", "arm_left_elbow_flex"] = 98
    boundary = len(activation.events)
    with pytest.raises(RuntimeError, match="actual configured temperature limit") as caught:
        poll_health(robot)
    note = fault_note(caught.value)
    prior = note["prior_successful_health"]
    assert prior == {
        "binding": binding,
        "register": "Present_Temperature",
        "motor": "arm_left_elbow_flex",
        "value": 28,
        "read_started_at_monotonic_s": 100,
        "read_completed_at_monotonic_s": 100,
    }
    assert note["offending_value"] == 98 and note["read_started_at_monotonic_s"] == 100.3
    assert activation.events[boundary:] == [("group_read", "left", 33, 38, (1, 2, 3, 4, 5, 6))]
    assert note["group_reply"]["request"]["width"] == 38
    assert robot._am1_teleop_health["binding"] == binding
    assert robot._am1_teleop_health["left"]["registers"]["Present_Temperature"]["arm_left_elbow_flex"] == 28
    robot._am1_teleop_health["left"]["registers"]["Present_Temperature"]["arm_left_elbow_flex"] = 99
    assert fault_note(caught.value)["prior_successful_health"] == prior


@pytest.mark.parametrize(
    "problem", ["missing_binding", "foreign_binding", "future_interval", "invalid_interval"]
)
def test_fault_note_does_not_attribute_unproven_prior_health(activation, problem):
    from lerobot.robots.alohamini.am1_live_teleop import read_groups

    robot = configured_robot(activation)
    complete_health(robot)
    prime_thermal_highs(activation, robot, "arm_left_elbow_flex", value=98)
    # Historical untagged records are explicitly unavailable, never guessed as current.
    if problem == "missing_binding":
        robot._am1_teleop_health.pop("binding", None)
    elif problem == "foreign_binding":
        robot._am1_teleop_health["binding"]["run_id"] = str(uuid4())
    else:
        timing = robot._am1_teleop_health["left"]["timing"]["Present_Temperature"]
        timing["read_completed_at_monotonic_s"] = 101 if problem == "future_interval" else float("nan")
    robot.left_bus.registers["Present_Temperature", "arm_left_elbow_flex"] = 98
    with pytest.raises(RuntimeError, match="actual configured temperature limit") as caught:
        read_groups(robot, ("Present_Temperature",))
    assert fault_note(caught.value)["prior_successful_health"] is None


def test_group_reply_prewrite_refusal_allows_existing_actual_sdk_shutdown(activation, monkeypatch):
    from lerobot.motors.feetech.tables import MODEL_CONTROL_TABLE

    original_port_write = ReplyPort.writePort

    def write_with_ack(port, packet):
        result = original_port_write(port, packet)
        if packet[4] == 3:
            port.buffer.extend(reply_packet(packet[2], []))
        return result

    monkeypatch.setattr(ReplyPort, "writePort", write_with_ack)
    robot = configured_robot(activation)
    bus = sdk_bus(robot, "left", [])
    original_sync = bus.packet_handler.syncReadTx
    bus.packet_handler.syncReadTx = lambda port, address, width, ids, count: original_sync(
        port, address + 1, width, ids, count
    )
    with pytest.raises(RuntimeError, match="transmit") as caught:
        bus.sync_read("Present_Temperature", robot.left_arm_motors, normalize=False, num_retry=0)
    assert bus.port_handler.is_using  # The original SDK exited before resetting its busy flag.
    first = caught.value
    for target in (robot.left_bus, robot.right_bus):
        original_bus_write = target.write

        def sdk_write(
            register, name, value, *, normalize=True, num_retry=3, _bus=target, _write=original_bus_write
        ):
            address, width = MODEL_CONTROL_TABLE["sts3215"][register]
            data = [(int(value) >> (8 * index)) & 255 for index in range(width)]
            comm, error = _bus.packet_handler.writeTxRx(
                _bus.port_handler, _bus.motors[name].id, address, width, data
            )
            assert comm == 0 and error == 0
            _write(register, name, value, normalize=normalize, num_retry=num_retry)

        target.write = sdk_write
    robot._am1_protected_cleanup = True
    assert robot._safe_shutdown(close_buses=True, recover_interrupted_bus_io=True) == []
    assert robot._am1_teleop_first_error is first
    assert not robot.left_bus.is_connected and not robot.right_bus.is_connected
    assert all(row["value"] == 0 for row in robot._am1_shutdown_evidence["readbacks"])
    assert len(robot._am1_shutdown_evidence["readbacks"]) == 20
    assert all(
        packet[4] == 3
        for target in (robot.left_bus, robot.right_bus)
        for packet in target.port_handler.writes
    )


def test_group_reply_missing_transmit_cannot_consume_valid_cached_reply(activation, monkeypatch):
    import scservo_sdk as sdk

    # Controlled SDK success without its required send must not credit buffered RX.
    monkeypatch.setattr(type(sdk.PacketHandler(0)), "syncReadTx", lambda *args: 0)
    robot = configured_robot(activation)
    packets = [reply_packet(i, [28]) for i in range(1, 7)]
    bus = sdk_bus(robot, "left", packets)
    before = list(bus.port_handler.buffer)
    with pytest.raises(RuntimeError, match="missing original transmit"):
        bus.sync_read("Present_Temperature", robot.left_arm_motors, normalize=False, num_retry=0)
    assert bus.port_handler.buffer == before
    assert bus.port_handler.writes == [] and bus.port_handler.clears == 0


def test_group_reply_forwards_same_original_packet_and_write_result(activation, monkeypatch):
    import scservo_sdk as sdk

    calls, requests = [], []

    class WriteResult(int):
        pass

    result = WriteResult(14)
    original_tx = type(sdk.PacketHandler(0)).txPacket

    def tx(handler, port, packet):
        requests.append(packet)
        return original_tx(handler, port, packet)

    def write(port, packet):
        calls.append(packet)
        return result

    monkeypatch.setattr(type(sdk.PacketHandler(0)), "txPacket", tx)
    monkeypatch.setattr(ReplyPort, "writePort", write)
    robot = configured_robot(activation)
    bus = sdk_bus(robot, "left", [reply_packet(i, [28]) for i in range(1, 7)])
    assert set(
        bus.sync_read("Present_Temperature", robot.left_arm_motors, normalize=False, num_retry=0).values()
    ) == {28}
    assert len(calls) == len(requests) == bus.port_handler.clears == 1
    assert calls[0] is requests[0]
    assert robot._am1_teleop_group_receipts["left"]["transmit"]["result"] is result


def live_health_bus(robot, side, packets):
    from lerobot.motors.motors_bus import MotorsBus

    bus = sdk_bus(robot, side, packets)
    bus._sync_read = MotorsBus._sync_read.__get__(bus)
    return bus


def live_health_payload(*, temperature=34, voltage=118, status=0):
    # Synthetic complete 33..70 payload. Position low byte74 deliberately differs
    # from temperature34: the consumer must use the register's actual offset.
    payload = [17] * 38
    for offset, value in {
        0: 0,
        7: 1,
        22: 1,
        23: 74,
        24: 12,
        29: voltage,
        30: temperature,
        32: status,
        36: 2,
        37: 0,
    }.items():
        payload[offset] = value
    return payload


def test_live_health_acquires_one_original_wide_group_per_exact_arm_chain(activation):
    from lerobot.robots.alohamini.am1_live_teleop import poll_health

    robot = configured_robot(activation)
    for side in ("left", "right"):
        live_health_bus(
            robot,
            side,
            [reply_packet(i, live_health_payload(temperature=28 + i, voltage=117 + i)) for i in range(1, 7)],
        )
    poll_health(robot)
    health = robot._am1_teleop_health
    assert health["binding"] == robot._am1_teleop_binding
    for side in ("left", "right"):
        bus = getattr(robot, side + "_bus")
        assert bus.port_handler.writes == [[255, 255, 254, 10, 130, 33, 38, 1, 2, 3, 4, 5, 6, 25]]
        assert bus.port_handler.clears == 1
        rows, times = health[side]["registers"], health[side]["timing"]
        names = getattr(robot, side + "_arm_motors")
        assert rows["Present_Temperature"] == dict(zip(names, (29, 30, 31, 32, 33, 34), strict=True))
        assert rows["Present_Voltage"] == dict(zip(names, (118, 119, 120, 121, 122, 123), strict=True))
        assert rows["Status"] == rows["Operating_Mode"] == dict.fromkeys(names, 0)
        assert rows["Torque_Enable"] == rows["Lock"] == dict.fromkeys(names, 1)
        assert all(
            timing == {"read_started_at_monotonic_s": 100, "read_completed_at_monotonic_s": 100}
            for timing in times.values()
        )
        receipt = robot._am1_teleop_group_receipts[side]
        assert receipt["request"]["address"] == 33 and receipt["request"]["width"] == 38
        assert len(receipt["packets"]) == 6 and receipt["comm_result"] == 0


def test_live_health_valid_wide74_still_faults_with_original_tx_rx_and_limit(activation):
    from lerobot.robots.alohamini.am1_live_teleop import poll_health

    robot = configured_robot(activation)
    prime_thermal_highs(activation, robot, "arm_left_shoulder_lift", value=74)
    packets = [reply_packet(i, live_health_payload(temperature=74 if i == 2 else 34)) for i in range(1, 7)]
    left = live_health_bus(robot, "left", packets)
    right = live_health_bus(robot, "right", [])
    original_rx, original_write = left.packet_handler.rxPacket, left.port_handler.writePort
    with pytest.raises(RuntimeError, match="actual configured temperature limit") as caught:
        poll_health(robot)
    note = fault_note(caught.value)
    assert note["register"] == "Present_Temperature"
    assert note["offending_motor"] == "arm_left_shoulder_lift" and note["offending_value"] == 74
    assert note["comparison"]["upper_exclusive"] == 70
    receipt = note["group_reply"]
    assert receipt["request"]["address"] == 33 and receipt["request"]["width"] == 38
    assert receipt["transmit"]["packet"] == [255, 255, 254, 10, 130, 33, 38, 1, 2, 3, 4, 5, 6, 25]
    assert receipt["packets"][1]["packet"] == packets[1]
    assert len(receipt["packets"]) == 6
    assert left.packet_handler.rxPacket == original_rx and left.port_handler.writePort == original_write
    assert len(left.port_handler.writes) == 1 and not right.port_handler.writes


@pytest.mark.parametrize(
    "problem", ["one_byte", "two_bytes", "foreign_id", "duplicate_id", "checksum", "device_error"]
)
def test_live_health_rejects_foreign_or_invalid_reply_before_sdk_decode(activation, problem):
    from lerobot.robots.alohamini.am1_live_teleop import poll_health

    robot = configured_robot(activation)
    packets = [reply_packet(i, live_health_payload()) for i in range(1, 7)]
    reason = "payload width"
    index = 0
    if problem in ("one_byte", "two_bytes"):
        packets[0] = reply_packet(1, [74] if problem == "one_byte" else [74, 12])
    elif problem == "foreign_id":
        packets[0], reason = reply_packet(11, live_health_payload()), "unexpected or duplicate"
    elif problem == "duplicate_id":
        packets[1], index, reason = packets[0][:], 1, "unexpected or duplicate"
    elif problem == "checksum":
        packets[0][-1] ^= 1
        reason = None  # Existing SDK COMM_RX_CORRUPT remains a transport failure.
    elif problem == "device_error":
        packets[0], reason = reply_packet(1, [], error=126), "actual device error"
    left = live_health_bus(robot, "left", packets)
    right = live_health_bus(robot, "right", [])
    if reason is None:
        with pytest.raises(ConnectionError):
            poll_health(robot)
        assert not getattr(robot, "_am1_teleop_fault", None)
    else:
        with pytest.raises(RuntimeError, match=reason) as caught:
            poll_health(robot)
        note = fault_note(caught.value)
        assert note["comparison"]["request"]["width"] == 38
        assert note["raw"]["packet"] == packets[index]
        assert left.sync_reader.data_dict[2 if problem == "duplicate_id" else 1] == []
    assert len(left.port_handler.writes) == 1 and not right.port_handler.writes


@pytest.mark.parametrize(
    ("address", "width", "ids"),
    [
        (34, 38, [1, 2, 3, 4, 5, 6]),
        (33, 37, [1, 2, 3, 4, 5, 6]),
        (33, 38, [1, 2, 3, 4, 5]),
        (33, 38, [1, 2, 3, 4, 5, 8]),
        (33, 38, [6, 5, 4, 3, 2, 1]),
        (33, 38, [1, 2, 3, 4, 5, 6, 8, 9, 10, 11]),
    ],
)
def test_live_health_wide_allowance_cannot_expand_to_other_requests(activation, address, width, ids):
    robot = configured_robot(activation)
    bus = live_health_bus(robot, "left", [])
    with pytest.raises(RuntimeError, match="bound AM1 register/IDs"):
        bus._sync_read(address, width, ids, num_retry=0)
    assert not bus.port_handler.writes and bus.port_handler.clears == 0


def test_live_health_wrong_actual_wide_transmit_is_fenced_and_hooks_restore(activation):
    from lerobot.robots.alohamini.am1_live_teleop import poll_health

    robot = configured_robot(activation)
    bus = live_health_bus(robot, "left", [reply_packet(i, live_health_payload()) for i in range(1, 7)])
    original_rx, original_write = bus.packet_handler.rxPacket, bus.port_handler.writePort
    original = bus.packet_handler.syncReadTx
    bus.packet_handler.syncReadTx = lambda port, start, width, ids, count: original(
        port, start + 1, width, ids, count
    )
    with pytest.raises(RuntimeError, match="transmit differs") as caught:
        poll_health(robot)
    assert fault_note(caught.value)["group_reply"]["transmit"]["packet"][5:7] == [34, 38]
    assert not bus.port_handler.writes
    assert bus.packet_handler.rxPacket == original_rx and bus.port_handler.writePort == original_write


@pytest.mark.parametrize("change", ["reverse", "body"])
def test_live_health_arm_roles_are_frozen_at_guard_install(activation, change):
    from lerobot.robots.alohamini.am1_live_teleop import poll_health

    robot = configured_robot(activation)
    if change == "reverse":
        robot.left_arm_motors = list(reversed(robot.left_arm_motors))
    else:
        robot.left_arm_motors = [*robot.left_arm_motors[:5], "base_left_wheel"]
    bus = robot.left_bus
    with pytest.raises(RuntimeError, match="owner/binding changed"):
        poll_health(robot)
    assert not bus.port_handler.writes


@pytest.mark.parametrize("change", ["last_result", "missing_id"])
def test_live_health_incomplete_sdk_window_cannot_decode_missing_data_as_zero(
    activation, monkeypatch, change
):
    import scservo_sdk as sdk

    from lerobot.robots.alohamini.am1_live_teleop import poll_health

    robot = configured_robot(activation)
    bus = live_health_bus(robot, "left", [reply_packet(i, live_health_payload()) for i in range(1, 7)])
    original = sdk.GroupSyncRead.rxPacket

    def incomplete(reader):
        result = original(reader)
        if change == "last_result":
            reader.last_result = False
        else:
            del reader.data_dict[6]
        return result

    monkeypatch.setattr(sdk.GroupSyncRead, "rxPacket", incomplete)
    bus.sync_reader.getData = lambda *_: pytest.fail("incomplete window must be rejected BEFORE any decode")
    with pytest.raises(RuntimeError, match="incomplete original live health"):
        poll_health(robot)
    assert len(bus.port_handler.writes) == 1


def test_live_health_same_reader_rebinds_current10_then_health6_then_position6(activation):
    from lerobot.robots.alohamini.am1_live_teleop import poll_health

    robot = configured_robot(activation)
    left = robot.left_bus
    all_names = list(left.motors)
    all_ids = [left.motors[name].id for name in all_names]
    live_health_bus(robot, "left", [reply_packet(i, [2, 0]) for i in all_ids])
    assert left.sync_read("Present_Current", all_names, normalize=False, num_retry=0) == dict.fromkeys(
        all_names, 2
    )
    left.port_handler.buffer = [byte for i in range(1, 7) for byte in reply_packet(i, live_health_payload())]
    live_health_bus(robot, "right", [reply_packet(i, live_health_payload()) for i in range(1, 7)])
    poll_health(robot)
    assert tuple(left.sync_reader.data_dict) == (1, 2, 3, 4, 5, 6)
    left.port_handler.buffer = [byte for i in range(1, 7) for byte in reply_packet(i, [74, 12])]
    assert left.sync_read(
        "Present_Position", robot.left_arm_motors, normalize=False, num_retry=0
    ) == dict.fromkeys(robot.left_arm_motors, 3146)
    assert [(packet[5:7], packet[7:-1]) for packet in left.port_handler.writes] == [
        ([69, 2], all_ids),
        ([33, 38], [1, 2, 3, 4, 5, 6]),
        ([56, 2], [1, 2, 3, 4, 5, 6]),
    ]
    assert left.port_handler.clears == 3


def test_live_health_all_fields_keep_their_original_per_bus_interval(activation):
    from lerobot.robots.alohamini.am1_live_teleop import LIVE_HEALTH, poll_health

    robot = configured_robot(activation)
    for bus in (robot.left_bus, robot.right_bus):
        for name in bus.motors:
            bus.registers["Torque_Enable", name] = bus.registers["Lock", name] = 1
        bus.on_read = lambda *_: setattr(activation.clock, "now", activation.clock.now + 0.004)
    poll_health(robot)
    for side, start, end in (("left", 100, 100.004), ("right", 100.004, 100.008)):
        row = robot._am1_teleop_health[side]
        assert set(row["timing"]) == set(LIVE_HEALTH)
        for timing in row["timing"].values():
            assert timing["read_started_at_monotonic_s"] == pytest.approx(start)
            assert timing["read_completed_at_monotonic_s"] == pytest.approx(end)
        receipt = robot._am1_teleop_group_receipts[side]
        assert receipt["group_call_started_at_monotonic_s"] == pytest.approx(start)
        assert receipt["group_call_completed_at_monotonic_s"] == pytest.approx(end)
        assert len(getattr(robot, side + "_bus").port_handler.writes) == 1
