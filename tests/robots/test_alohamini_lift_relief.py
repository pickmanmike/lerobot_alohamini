"""Exercise the opt-in diagnostic with real lift/control/cleanup and fake serial I/O."""

from __future__ import annotations

import builtins
import json
import signal
import sys

import pytest

from lerobot.robots.alohamini import alohamini as robot_module
from lerobot.robots.alohamini import alohamini_host as host
from lerobot.robots.alohamini import lift_axis
from lerobot.robots.alohamini import lift_motor_feedback as feedback
from lerobot.robots.alohamini import lift_relief
from tests.robots.test_alohamini_safe_bringup import FakeBus


class Clock:
    now = 100.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class LiftBus(FakeBus):
    def __init__(self, clock, **kwargs):
        super().__init__("left", tuple(kwargs["motors"]), [])
        self.motors = kwargs["motors"]
        self.clock = clock
        self.is_connected = False
        self.position = 1000.0
        self.bottom = 1100.0
        self.last_update = clock.now
        self.refresh_count = 0
        self.up_factor = 1.0
        self.rest_current = 8
        self.raised = False
        self.setup_torque_side_effect: str | None = None
        self.ineffective_torque_off_numbers: set[int] = set()
        self.torque_off_failures: dict[int, tuple[BaseException, bool]] = {}
        self.torque_off_writes = 0
        self.hook = lambda register: None
        self.registers.update({
            (name, "lift_axis"): value for name, value in {
                "Model_Number": 777, "Firmware_Major_Version": 3, "Firmware_Minor_Version": 6,
                "Max_Temperature_Limit": 70, "Min_Voltage_Limit": 40, "Max_Voltage_Limit": 140,
                "Unloading_Condition": 44, "Present_Temperature": 30, "Present_Voltage": 120,
                "Operating_Mode": 1, "Angular_Resolution": 1,
            }.items()
        })

    def refresh_lift_state(self):
        self.refresh_count += 1
        goal = self.registers[("Goal_Velocity", "lift_axis")]
        torque = self.registers[("Torque_Enable", "lift_axis")]
        elapsed = self.clock.now - self.last_update
        self.position = min(
            self.bottom,
            self.position + goal * torque * elapsed * (self.up_factor if goal < 0 else 1.0),
        )
        self.last_update = self.clock.now
        if goal < 0:
            self.raised = True
        stopped = not goal or (goal > 0 and self.position >= self.bottom)
        self.registers[("Present_Position", "lift_axis")] = round(self.position)
        self.registers[("Present_Velocity", "lift_axis")] = 0 if stopped else goal
        self.registers[("Present_Current", "lift_axis")] = (
            self.rest_current if self.raised and stopped else 50 if stopped and torque else 5
        )

    def connect(self, *, handshake=True):
        self.events.append(("left", "connect"))
        self.is_connected = True
        if handshake:
            # The real SDK handshake also reads firmware. This diagnostic's own
            # explicit model/configuration preflight must be the sole such sweep.
            self.read("Firmware_Major_Version", "lift_axis", normalize=False)

    def read(self, register, motor, **kwargs):
        self.hook(register)
        if motor == "lift_axis":
            self.refresh_lift_state()
        return super().read(register, motor, **kwargs)

    def write(self, register, motor, value, **kwargs):
        int_value = int(value)
        conditional_failure = None
        if motor == "lift_axis" and register == "Torque_Enable" and int_value == 0:
            self.torque_off_writes += 1
            conditional_failure = self.torque_off_failures.get(self.torque_off_writes)
            if conditional_failure is not None:
                self.write_failures[(register, motor, int_value)] = conditional_failure
        try:
            super().write(register, motor, int_value, **kwargs)
        finally:
            if conditional_failure is not None:
                del self.write_failures[(register, motor, int_value)]
        if motor != "lift_axis":
            return
        if register == "Torque_Enable" and int_value == 0:
            if self.torque_off_writes in self.ineffective_torque_off_numbers:
                self.registers[("Torque_Enable", motor)] = 1
        # These are deliberately fake side-effect models, not claims about
        # which setup write changed the real servo's torque state.
        if register == self.setup_torque_side_effect and (
            register != "Goal_Velocity" or int_value == 0
        ):
            self.registers[("Torque_Enable", motor)] = 1


def _set_word(payload: bytearray, offset: int, value: int) -> None:
    encoded = abs(value) | (0x8000 if value < 0 else 0)
    payload[offset] = encoded & 0xFF
    payload[offset + 1] = (encoded >> 8) & 0xFF


class GroupedLiftTransport:
    motor_id = 11

    def __init__(self, bus: LiftBus):
        self.bus = bus
        self.group_reads: list[tuple[int, int]] = []

    def _value(self, register: str):
        try:
            self.bus.hook(register)
        except KeyboardInterrupt:
            raise
        except BaseException as error:
            raise feedback.TransportRefusal(
                f"modeled grouped {register} failure: {error}",
                {
                    "operation": "sync_read",
                    "register": register,
                    "requested_id": self.motor_id,
                    "checksum_ok": None,
                },
            ) from error
        sequence = self.bus.read_sequences.get((register, "lift_axis"))
        if sequence:
            value = sequence.pop(0)
            if isinstance(value, KeyboardInterrupt):
                raise value
            if isinstance(value, BaseException):
                raise feedback.TransportRefusal(
                    f"modeled grouped {register} failure: {value}",
                    {
                        "operation": "sync_read",
                        "register": register,
                        "requested_id": self.motor_id,
                        "checksum_ok": None,
                    },
                ) from value
            if not isinstance(value, int):
                raise feedback.TransportRefusal(
                    f"modeled grouped {register} returned non-integer telemetry",
                    {
                        "operation": "sync_read",
                        "register": register,
                        "requested_id": self.motor_id,
                        "checksum_ok": True,
                    },
                )
            self.bus.registers[(register, "lift_axis")] = value
        return self.bus.registers[(register, "lift_axis")]

    def read_group(self, start, length, *, timeout_ms=feedback.REPLY_TIMEOUT_MS):
        self.group_reads.append((start, length))
        if (start, length) == (0, 18):
            payload = bytearray(length)
            payload[0:2] = bytes(
                (self._value("Firmware_Major_Version"), self._value("Firmware_Minor_Version"))
            )
            _set_word(payload, 3, self._value("Model_Number"))
            payload[5] = self.motor_id
            payload[6] = 0
            payload[13:16] = bytes(
                (
                    self._value("Max_Temperature_Limit"),
                    self._value("Max_Voltage_Limit"),
                    self._value("Min_Voltage_Limit"),
                )
            )
        elif (start, length) == (19, 21):
            payload = bytearray(length)
            payload[0] = 44
            payload[11] = 1
            payload[14] = 1
            payload[18:21] = bytes((10, 200, 200))
        elif (start, length) == (40, 47):
            payload = bytearray(length)
            payload[0] = int(self.bus.registers[("Torque_Enable", "lift_axis")])
            payload[1] = int(self.bus.registers[("Acceleration", "lift_axis")])
            _set_word(payload, 6, int(self.bus.registers[("Goal_Velocity", "lift_axis")]))
            _set_word(payload, 8, 1000)
            payload[15] = int(self.bus.registers[("Lock", "lift_axis")])
            payload[44:47] = bytes((65, 254, 1))
        elif (start, length) == (feedback.FEEDBACK_START, feedback.FEEDBACK_LENGTH):
            self.bus.refresh_lift_state()
            payload = bytearray(length)
            payload[feedback.OPERATING_MODE_ADDRESS - start] = int(
                self._value("Operating_Mode")
            )
            payload[feedback.TORQUE_ENABLE_ADDRESS - start] = int(
                self._value("Torque_Enable")
            )
            _set_word(
                payload,
                feedback.GOAL_VELOCITY_ADDRESS - start,
                int(self._value("Goal_Velocity")),
            )
            payload[feedback.LOCK_ADDRESS - start] = int(
                self._value("Lock")
            )
            self.bus.registers[("Present_Position", "lift_axis")] = round(self.bus.position)
            _set_word(
                payload,
                feedback.PRESENT_POSITION_ADDRESS - start,
                int(self._value("Present_Position")),
            )
            _set_word(
                payload,
                feedback.PRESENT_VELOCITY_ADDRESS - start,
                int(self._value("Present_Velocity")),
            )
            _set_word(payload, feedback.PRESENT_LOAD_ADDRESS - start, 0)
            payload[feedback.PRESENT_VOLTAGE_ADDRESS - start] = int(
                self._value("Present_Voltage")
            )
            payload[feedback.PRESENT_TEMPERATURE_ADDRESS - start] = int(
                self._value("Present_Temperature")
            )
            payload[feedback.STATUS_ADDRESS - start] = int(
                self._value("Status")
            )
            payload[feedback.MOVING_ADDRESS - start] = int(
                abs(self.bus.registers[("Present_Velocity", "lift_axis")]) > 5
            )
            _set_word(
                payload,
                feedback.PRESENT_CURRENT_ADDRESS - start,
                int(self._value("Present_Current")),
            )
        else:
            raise AssertionError(f"unexpected grouped read {(start, length)}")
        return bytes(payload), {
            "start_address": start,
            "requested_width": length,
            "response_payload_bytes": list(payload),
            "response_payload_length": len(payload),
            "checksum_ok": True,
            "request_correlation": "single_group_reply_without_address_or_sequence",
            "reply_timeout_ms": timeout_ms,
        }

    def write_register(self, *_args, **_kwargs):
        raise AssertionError("installed relief writes must remain owned by LiftAxis")


@pytest.fixture
def rig(monkeypatch, tmp_path):
    clock = Clock()
    buses = []
    robots = []
    monkeypatch.setattr(lift_axis.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(lift_axis.time, "sleep", clock.sleep)

    def make_bus(**kwargs):
        bus = LiftBus(clock, **kwargs)
        buses.append(bus)
        return bus

    original_robot = robot_module.AlohaMini

    def make_robot(config):
        config.calibration_dir = tmp_path
        robot = original_robot(config)
        robots.append(robot)
        return robot

    monkeypatch.setattr(robot_module, "FeetechMotorsBus", make_bus)
    monkeypatch.setattr(host, "AlohaMini", make_robot)
    monkeypatch.setattr(host, "AlohaMiniHost", lambda *_: pytest.fail("diagnostic opened ZMQ"))

    def make_grouped_transport(robot):
        transport = GroupedLiftTransport(robot.left_bus)
        robot.left_bus.grouped_transport = transport
        return transport

    monkeypatch.setattr(
        lift_relief,
        "make_grouped_transport",
        make_grouped_transport,
        raising=False,
    )
    monkeypatch.setattr(builtins, "input", lambda _: "RELIEF")
    monkeypatch.setattr(sys, "argv", [
        "alohamini_host", "--robot_model", "alohamini1", "--no_follower", "--no_cameras",
        "--lift_relief",
    ])
    return clock, buses, robots


def execute():
    with pytest.raises(SystemExit) as caught:
        host.main()
    return caught.value.code


def reports(capsys):
    return [json.loads(line.split("] ", 1)[1]) for line in capsys.readouterr().out.splitlines()
            if line.startswith("[LIFT RELIEF] ")]


def assert_stopped(bus):
    assert bus.registers[("Goal_Velocity", "lift_axis")] == 0
    assert bus.registers[("Torque_Enable", "lift_axis")] == 0
    assert not bus.is_connected


def test_relief_diagnostic_constructs_only_lift_id_11(rig, capsys):
    _, buses, _ = rig

    assert execute() == 0
    capsys.readouterr()

    assert len(buses) == 1
    assert set(buses[0].motors) == {"lift_axis"}
    assert buses[0].motors["lift_axis"].id == 11


def test_relief_uses_grouped_dynamic_feedback_instead_of_scalar_samples(rig, capsys):
    _, buses, _ = rig

    assert execute() == 0
    capsys.readouterr()

    assert hasattr(buses[0], "grouped_transport")
    assert buses[0].grouped_transport.group_reads.count(
        (feedback.FEEDBACK_START, feedback.FEEDBACK_LENGTH)
    ) > 0
    assert not [event for event in buses[0].events if event[1] == "read"]


def test_relief_homes_once_moves_up_ten_mm_then_observes_45_seconds_and_cleans_up(rig, capsys):
    clock, buses, robots = rig
    assert execute() == 0
    assert len(buses) == 1
    robot, bus = robots[0], buses[0]
    assert robot.right_bus is None and not robot.left_arm_motors and not robot.cameras
    writes = [e for e in bus.events if e[1] == "write"]
    assert sum(e[2:] == ("Goal_Velocity", "lift_axis", 200) for e in writes) == 1
    assert sum(e[2:] == ("Goal_Velocity", "lift_axis", -200) for e in writes) == 1
    assert all(e[-1] == 0 for e in writes if e[3].startswith("base_"))
    assert {e[2] for e in writes} <= {"Goal_Velocity", "Torque_Enable", "Lock", "Operating_Mode"}
    lift_writes = [event for event in writes if event[3] == "lift_axis"]
    assert lift_writes[-1][2:] == ("Torque_Enable", "lift_axis", 0)
    assert_stopped(bus)
    assert robot.lift.cfg.descent_floor_mm == 5.0
    records = reports(capsys)
    home_complete = next(r for r in records if r.get("phase") == "home_complete")
    assert home_complete["result"]["final_position_raw"] == round(bus.bottom)
    assert home_complete["zero_reference"] == "process-local, unchanged"
    observations = [r for r in records if r.get("phase") == "rest_height"]
    assert len(observations) >= 45
    assert all(10 <= r["height_mm"] <= 12 for r in observations)
    assert observations[-1]["elapsed_s"] - observations[0]["elapsed_s"] >= 44
    rest_feedback = [r for r in records if r.get("phase") == "rest"]
    assert all(r["goal_velocity_raw"] == r["present_velocity_raw"] == 0 for r in rest_feedback)
    cleanup = [r for r in records if r.get("phase") == "cleanup_readback"]
    assert len(cleanup) >= feedback.STATIONARY_MIN_SAMPLES
    assert all(r["torque_enable"] == r["goal_velocity_raw"] == 0 for r in cleanup)
    assert 148 <= clock.now <= 150
    assert bus.grouped_transport.group_reads.count((0, 18)) == 1
    assert bus.grouped_transport.group_reads.count((19, 21)) == 1
    assert bus.grouped_transport.group_reads.count((40, 47)) == 1
    assert not any(event[1:3] == ("read", "Phase") for event in bus.events)


@pytest.mark.parametrize("setup_register", ["Goal_Velocity", "Operating_Mode"])
def test_setup_side_effect_models_are_reasserted_off_after_all_setup_writes(
    rig, monkeypatch, capsys, setup_register
):
    _, buses, _ = rig
    original = LiftBus.connect

    def connect(bus, **kwargs):
        original(bus, **kwargs)
        bus.setup_torque_side_effect = setup_register

    monkeypatch.setattr(LiftBus, "connect", connect)
    assert execute() == 0
    bus = buses[0]
    phase = {record["phase"]: record for record in reports(capsys)}
    assert phase["setup_before"]["torque_enable"] == 0
    assert phase["setup_after_writes"]["torque_enable"] == 1
    assert phase["before_torque"]["torque_enable"] == 0

    lift_writes = [
        event for event in bus.events if event[1] == "write" and event[3] == "lift_axis"
    ]
    first_enable = next(
        i
        for i, event in enumerate(lift_writes)
        if event[2:] == ("Torque_Enable", "lift_axis", 1)
    )
    assert lift_writes[first_enable - 1][2:] == ("Torque_Enable", "lift_axis", 0)
    assert not any(event[2] == "Operating_Mode" for event in lift_writes[first_enable - 1:first_enable])
    assert not any(event[2] == "Goal_Velocity" for event in lift_writes[first_enable - 1:first_enable])


def test_ineffective_final_pre_motion_torque_off_refuses_without_nonzero_goal(
    rig, monkeypatch, capsys
):
    _, buses, _ = rig
    original = LiftBus.connect

    def connect(bus, **kwargs):
        original(bus, **kwargs)
        bus.setup_torque_side_effect = "Operating_Mode"
        bus.ineffective_torque_off_numbers = {2}

    monkeypatch.setattr(LiftBus, "connect", connect)
    assert execute() == 2
    output = capsys.readouterr().out
    phase = {
        record["phase"]: record
        for record in (
            json.loads(line.split("] ", 1)[1])
            for line in output.splitlines()
            if line.startswith("[LIFT RELIEF] ")
        )
    }
    assert phase["setup_after_writes"]["torque_enable"] == 1
    assert phase["before_torque"]["torque_enable"] == 1
    assert "before_torque" in output and "torque readback did not match 0" in output
    assert not any(
        event[1:4] == ("write", "Goal_Velocity", "lift_axis") and event[-1] != 0
        for event in buses[0].events
    )
    assert_stopped(buses[0])


def test_failed_final_pre_motion_torque_off_refuses_without_nonzero_goal(rig, monkeypatch, capsys):
    _, buses, _ = rig
    original = LiftBus.connect

    def connect(bus, **kwargs):
        original(bus, **kwargs)
        bus.setup_torque_side_effect = "Operating_Mode"
        bus.torque_off_failures = {2: (OSError("modeled torque-off write failure"), False)}

    monkeypatch.setattr(LiftBus, "connect", connect)
    assert execute() == 2
    output = capsys.readouterr().out
    assert "failed to verify torque_enable" in output.lower()
    assert not any(
        event[1:4] == ("write", "Goal_Velocity", "lift_axis") and event[-1] != 0
        for event in buses[0].events
    )
    assert_stopped(buses[0])


def test_final_pre_motion_sample_still_requires_cool_temperature(rig, monkeypatch, capsys):
    original = LiftBus.write

    def write(bus, register, motor, value, **kwargs):
        original(bus, register, motor, value, **kwargs)
        if motor == "lift_axis" and register == "Torque_Enable" and bus.torque_off_writes == 2:
            bus.registers[("Present_Temperature", motor)] = 41

    monkeypatch.setattr(LiftBus, "write", write)
    assert execute() == 2
    output = capsys.readouterr().out
    assert "starting temperature" in output.lower() and '"phase":"before_torque"' in output
    assert not any(
        event[1:4] == ("write", "Goal_Velocity", "lift_axis") and event[-1] != 0
        for event in rig[1][0].events
    )
    assert_stopped(rig[1][0])


def test_cleanup_readback_refuses_if_final_direct_torque_off_is_ineffective(
    rig, monkeypatch, capsys
):
    _, buses, _ = rig
    original = LiftBus.connect

    def connect(bus, **kwargs):
        original(bus, **kwargs)
        bus.ineffective_torque_off_numbers = {4}

    monkeypatch.setattr(LiftBus, "connect", connect)
    assert execute() == 1
    output = capsys.readouterr().out
    assert '"phase":"cleanup_readback"' in output
    assert "cleanup" in output.lower() and "torque" in output.lower()
    assert not buses[0].is_connected


@pytest.mark.parametrize("fault,reason", [
    ("hot_start", "temperature"), ("missing", "telemetry"), ("fault_status", "status"),
    ("wrong_model", "model"), ("nonfinite", "telemetry"), ("powered_start", "torque"),
])
def test_preflight_faults_refuse_before_any_torque_enable(rig, monkeypatch, capsys, fault, reason):
    _, buses, _ = rig
    original = LiftBus.connect

    def connect(bus, **kwargs):
        original(bus, **kwargs)
        register, value = {
            "hot_start": ("Present_Temperature", 41), "missing": ("Present_Voltage", None),
            "fault_status": ("Status", 4), "wrong_model": ("Model_Number", 2825),
            "nonfinite": ("Present_Temperature", float("nan")), "powered_start": ("Torque_Enable", 1),
        }[fault]
        if fault in ("missing", "nonfinite"):
            bus.read_sequences[(register, "lift_axis")] = [
                OSError("telemetry missing") if fault == "missing" else value
            ]
        else:
            bus.registers[(register, "lift_axis")] = value

    monkeypatch.setattr(LiftBus, "connect", connect)
    assert execute() == 2
    assert reason in capsys.readouterr().out.lower()
    assert not any(e[1:3] == ("write", "Torque_Enable") and e[-1] == 1 for e in buses[0].events)
    assert_stopped(buses[0])


def test_operator_gate_is_before_homing_and_requires_fresh_cool_telemetry(rig, monkeypatch, capsys):
    _, buses, _ = rig

    def confirm(_):
        assert not any(e[1] == "write" for e in buses[0].events)
        buses[0].registers[("Present_Temperature", "lift_axis")] = 55
        return "RELIEF"

    monkeypatch.setattr(builtins, "input", confirm)
    assert execute() == 2
    assert "temperature" in capsys.readouterr().out.lower()
    assert not any(e[1:3] == ("write", "Torque_Enable") and e[-1] == 1 for e in buses[0].events)
    assert_stopped(buses[0])


@pytest.mark.parametrize("fault,reason", [
    ("no_motion", "progress"), ("wrong_direction", "direction"), ("travel", "travel"),
    ("hot_homing", "temperature"), ("hot_relief", "temperature"),
    ("high_idle_current", "current"), ("telemetry_failure", "telemetry"),
    ("persistent_rest_velocity", "stationary"), ("interrupt", "interrupted"),
])
def test_motion_and_rest_aborts_converge_on_zero_torque_off_and_close(rig, monkeypatch, capsys, fault, reason):
    clock, buses, _ = rig
    original = LiftBus.connect

    def connect(bus, **kwargs):
        original(bus, **kwargs)
        interrupted = False
        stopped_feedback = 0
        if fault in ("no_motion", "wrong_direction", "travel"):
            bus.up_factor = {"no_motion": 0, "wrong_direction": -1, "travel": 100}[fault]
        if fault == "high_idle_current":
            bus.rest_current = 31  # 201.5mA, below the earlier 300mA criterion.

        def hook(register):
            nonlocal interrupted, stopped_feedback
            goal = bus.registers[("Goal_Velocity", "lift_axis")]
            if fault == "wrong_direction" and goal < 0 and register == "Present_Velocity":
                bus.read_sequences[(register, "lift_axis")] = [200]
            if fault == "hot_homing" and goal > 0 or fault == "hot_relief" and goal < 0:
                bus.registers[("Present_Temperature", "lift_axis")] = 55
            if bus.raised and register == "Present_Temperature":
                if goal == 0:
                    stopped_feedback += 1
                if fault == "telemetry_failure":
                    raise OSError("telemetry disconnected")
                if fault == "interrupt":
                    if not interrupted:
                        interrupted = True
                        bus.port_handler.is_using = True
                        raise KeyboardInterrupt()
            if (
                fault == "persistent_rest_velocity"
                and bus.raised
                and goal == 0
                and register == "Present_Velocity"
                and stopped_feedback >= 8
            ):
                bus.read_sequences[(register, "lift_axis")] = [50]

        bus.hook = hook

    monkeypatch.setattr(LiftBus, "connect", connect)
    assert execute() == (130 if fault == "interrupt" else 2)
    output = capsys.readouterr().out
    assert reason in output.lower()
    if fault == "high_idle_current":
        samples = [json.loads(line.split("] ", 1)[1]) for line in output.splitlines()
                   if line.startswith("[LIFT RELIEF] ")]
        high = [
            sample
            for sample in samples
            if sample.get("phase") == "rest" and sample["present_current_ma"] >= 200
        ]
        # The third physical sample is emitted once normally and once with its
        # explicit refusal reason; it is still exactly three sampled instants.
        assert len({sample["elapsed_s"] for sample in high}) == 3
        assert sum(sample.get("rejected", False) for sample in high) == 1
    assert clock.now < 112
    assert_stopped(buses[0])
    assert sum(e[1:] == ("write", "Goal_Velocity", "lift_axis", -200) for e in buses[0].events) <= 1


def test_rejected_temperature_sample_is_emitted_inside_report_throttle(rig, monkeypatch, capsys):
    clock, buses, _ = rig
    original = LiftBus.connect

    def connect(bus, **kwargs):
        original(bus, **kwargs)
        homing_temperature_reads = 0

        def hook(register):
            nonlocal homing_temperature_reads
            if register != "Present_Temperature":
                return
            if bus.registers[("Goal_Velocity", "lift_axis")] != 200:
                return
            homing_temperature_reads += 1
            if homing_temperature_reads == 2:
                bus.registers[("Present_Temperature", "lift_axis")] = 93

        bus.hook = hook

    monkeypatch.setattr(LiftBus, "connect", connect)

    assert execute() == 2
    records = reports(capsys)
    rejected = [record for record in records if record.get("rejected")]
    homing_rejection = next(record for record in rejected if record["phase"] == "homing")
    assert homing_rejection["temperature_c"] == 93
    assert "diagnostic ceiling 55 C" in homing_rejection["rejection_reason"]
    normal_homing = [
        record
        for record in records
        if record.get("phase") == "homing" and not record.get("rejected")
    ]
    assert normal_homing[-1]["temperature_c"] == 30
    assert homing_rejection["elapsed_s"] - normal_homing[-1]["elapsed_s"] < 1.0
    assert not any(record.get("phase") == "home_complete" for record in records)
    assert_stopped(buses[0])
    assert clock.now < 103


def test_rejected_sample_includes_grouped_request_reply_trace(rig, monkeypatch, capsys):
    _, buses, _ = rig
    original = LiftBus.connect

    def connect(bus, **kwargs):
        original(bus, **kwargs)
        homing_temperature_reads = 0

        def hook(register):
            nonlocal homing_temperature_reads
            if register == "Present_Temperature" and bus.registers[("Goal_Velocity", "lift_axis")] == 200:
                homing_temperature_reads += 1
                if homing_temperature_reads == 2:
                    bus.registers[(register, "lift_axis")] = 93

        bus.hook = hook

    monkeypatch.setattr(LiftBus, "connect", connect)

    assert execute() == 2
    rejected = [
        record
        for record in reports(capsys)
        if record.get("rejected") and record.get("phase") == "homing"
    ]
    assert rejected[-1]["temperature_c"] == 93
    trace = rejected[-1]["group_trace"]
    assert trace["start_address"] == feedback.FEEDBACK_START
    assert trace["requested_width"] == feedback.FEEDBACK_LENGTH
    assert trace["response_payload_bytes"][feedback.PRESENT_TEMPERATURE_ADDRESS - feedback.FEEDBACK_START] == 93
    assert trace["request_correlation"] == "single_group_reply_without_address_or_sequence"
    assert not any(e[1:3] == ("write", "Goal_Velocity") and e[-1] < 0 for e in buses[0].events)
    assert_stopped(buses[0])


def test_grouped_homing_goal_mismatch_is_preserved_and_refused(rig, monkeypatch, capsys):
    _, buses, _ = rig
    original = LiftBus.connect

    def connect(bus, **kwargs):
        original(bus, **kwargs)
        homing_goal_reads = 0

        def hook(register):
            nonlocal homing_goal_reads
            if register == "Goal_Velocity" and bus.registers[(register, "lift_axis")] == 200:
                homing_goal_reads += 1
                if homing_goal_reads == 2:
                    bus.read_sequences[(register, "lift_axis")] = [0]

        bus.hook = hook

    monkeypatch.setattr(LiftBus, "connect", connect)

    assert execute() == 2
    records = reports(capsys)
    rejected = next(
        record for record in records if record.get("rejected") and record.get("phase") == "homing"
    )
    assert rejected["goal_velocity_raw"] == 0
    assert "goal velocity did not match 200" in rejected["rejection_reason"]
    assert rejected["group_trace"]["requested_width"] == feedback.FEEDBACK_LENGTH
    assert not any(record.get("phase") == "home_complete" for record in records)
    assert_stopped(buses[0])


def test_configuration_transport_refusal_emits_grouped_trace(rig, monkeypatch, capsys):
    _, buses, _ = rig
    original = LiftBus.connect

    def connect(bus, **kwargs):
        original(bus, **kwargs)

        def hook(register):
            if register == "Model_Number":
                raise OSError("modeled checksum failure")

        bus.hook = hook

    monkeypatch.setattr(LiftBus, "connect", connect)

    assert execute() == 2
    rejected = [record for record in reports(capsys) if record.get("rejected")]
    assert rejected[-1]["phase"] == "configuration"
    assert "Configuration grouped read failed" in rejected[-1]["rejection_reason"]
    assert rejected[-1]["group_trace"]["register"] == "Model_Number"
    assert rejected[-1]["group_trace"]["checksum_ok"] is None
    assert_stopped(buses[0])


def test_each_grouped_feedback_sample_advances_the_fake_encoder_once(rig, capsys):
    _, buses, _ = rig

    assert execute() == 0
    capsys.readouterr()

    bus = buses[0]
    feedback_reads = bus.grouped_transport.group_reads.count(
        (feedback.FEEDBACK_START, feedback.FEEDBACK_LENGTH)
    )
    assert bus.refresh_count == feedback_reads > 0
    assert not [event for event in bus.events if event[1] == "read"]
    assert_stopped(bus)


def test_homing_group_transport_refusal_emits_trace_and_prevents_relief(rig, monkeypatch, capsys):
    _, buses, _ = rig
    original = LiftBus.connect

    def connect(bus, **kwargs):
        original(bus, **kwargs)

        def hook(register):
            if register == "Present_Position" and bus.registers[("Goal_Velocity", "lift_axis")] == 200:
                raise OSError("modeled grouped position payload failure")

        bus.hook = hook

    monkeypatch.setattr(LiftBus, "connect", connect)

    assert execute() == 2
    records = reports(capsys)
    rejected = next(
        record for record in records if record.get("rejected") and record.get("phase") == "homing"
    )
    assert rejected["group_trace"]["register"] == "Present_Position"
    assert rejected["group_trace"]["checksum_ok"] is None
    assert not any(record.get("phase") == "home_complete" for record in records)
    assert not any(event[1:3] == ("write", "Goal_Velocity") and event[-1] < 0 for event in buses[0].events)
    assert_stopped(buses[0])


def test_cleanup_group_trace_does_not_replace_primary_refusal(rig, monkeypatch, capsys):
    _, buses, _ = rig
    original_read_group = GroupedLiftTransport.read_group
    primary_triggered = False
    cleanup_failed = False

    def read_group(transport, start, length, **kwargs):
        nonlocal primary_triggered, cleanup_failed
        bus = transport.bus
        if (start, length) == (feedback.FEEDBACK_START, feedback.FEEDBACK_LENGTH):
            if primary_triggered and bus.registers[("Goal_Velocity", "lift_axis")] == 0 and not cleanup_failed:
                cleanup_failed = True
                raise feedback.TransportRefusal(
                    "modeled cleanup checksum failure",
                    {
                        "operation": "sync_read",
                        "start_address": start,
                        "requested_width": length,
                        "requested_id": 11,
                        "checksum_ok": False,
                    },
                )
            if bus.registers[("Goal_Velocity", "lift_axis")] == 200 and not primary_triggered:
                bus.registers[("Present_Temperature", "lift_axis")] = 93
                primary_triggered = True
        return original_read_group(transport, start, length, **kwargs)

    monkeypatch.setattr(GroupedLiftTransport, "read_group", read_group)

    assert execute() == 2
    output = capsys.readouterr().out
    records = [
        json.loads(line.split("] ", 1)[1])
        for line in output.splitlines()
        if line.startswith("[LIFT RELIEF] ")
    ]
    cleanup_rejection = next(
        record
        for record in records
        if record.get("phase") == "cleanup_readback" and record.get("rejected")
    )
    assert cleanup_rejection["group_trace"]["checksum_ok"] is False
    assert "LIFT_RELIEF_REFUSED: homing: temperature 93 C" in output
    assert "Cleanup detail:" in output
    assert_stopped(buses[0])


def test_torque_off_readback_check_never_homes_or_sends_nonzero_goal(rig, monkeypatch, capsys):
    clock, buses, robots = rig
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "alohamini_host",
            "--robot_model",
            "alohamini1",
            "--no_follower",
            "--no_cameras",
            "--lift_readback",
        ],
    )

    assert execute() == 0
    output = capsys.readouterr().out
    records = [
        json.loads(line.split("] ", 1)[1])
        for line in output.splitlines()
        if line.startswith("[LIFT RELIEF] ")
    ]
    readbacks = [record for record in records if record.get("phase") == "readback"]
    assert len(readbacks) >= 30
    assert readbacks[-1]["elapsed_s"] - readbacks[0]["elapsed_s"] >= 2.9
    assert all(record["torque_enable"] == 0 for record in readbacks)
    assert all(record["goal_velocity_raw"] == 0 for record in readbacks)
    assert all(abs(record["present_velocity_raw"]) <= 5 for record in readbacks)
    assert "LIFT_READBACK_PASS" in output
    assert not robots[0].lift.is_homed
    assert not any(
        event[1] == "write" and event[-1] != 0
        for event in buses[0].events
    )
    assert_stopped(buses[0])
    assert 103 <= clock.now < 104


def test_torque_off_readback_preserves_anomalous_sample_and_refuses(rig, monkeypatch, capsys):
    _, buses, robots = rig
    original = LiftBus.connect

    def connect(bus, **kwargs):
        original(bus, **kwargs)
        temperature_reads = 0

        def hook(register):
            nonlocal temperature_reads
            if register != "Present_Temperature":
                return
            temperature_reads += 1
            if temperature_reads == 3:
                bus.registers[("Present_Temperature", "lift_axis")] = 93

        bus.hook = hook

    monkeypatch.setattr(LiftBus, "connect", connect)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "alohamini_host",
            "--robot_model",
            "alohamini1",
            "--no_follower",
            "--no_cameras",
            "--lift_readback",
        ],
    )

    assert execute() == 2
    output = capsys.readouterr().out
    records = [
        json.loads(line.split("] ", 1)[1])
        for line in output.splitlines()
        if line.startswith("[LIFT RELIEF] ")
    ]
    rejected = [record for record in records if record.get("rejected")]
    assert rejected[-1]["phase"] == "readback"
    assert rejected[-1]["present_temperature_raw"] == 93
    assert "diagnostic ceiling 55 C" in rejected[-1]["rejection_reason"]
    assert "LIFT_READBACK_REFUSED" in output
    assert not robots[0].lift.is_homed
    assert not any(event[1] == "write" and event[-1] != 0 for event in buses[0].events)
    assert_stopped(buses[0])


def test_primary_refusal_survives_a_real_cleanup_write_failure(rig, monkeypatch, capsys):
    _, buses, _ = rig
    original = LiftBus.connect

    def connect(bus, **kwargs):
        original(bus, **kwargs)
        bus.registers[("Present_Temperature", "lift_axis")] = 55
        bus.write_failures[("Goal_Velocity", "lift_axis", 0)] = (OSError("zero write failed"), False)

    monkeypatch.setattr(LiftBus, "connect", connect)
    assert execute() == 2
    output = capsys.readouterr().out.lower()
    assert "temperature" in output and "zero write failed" in output
    assert not buses[0].is_connected


def test_temperature_is_still_cool_immediately_before_torque(rig, monkeypatch, capsys):
    original = LiftBus.write

    def write(bus, register, motor, value, **kwargs):
        original(bus, register, motor, value, **kwargs)
        if register == "Operating_Mode":
            bus.registers[("Present_Temperature", "lift_axis")] = 41

    monkeypatch.setattr(LiftBus, "write", write)
    assert execute() == 2
    assert "starting temperature" in capsys.readouterr().out.lower()
    assert not any(e[1:3] == ("write", "Torque_Enable") and e[-1] == 1 for e in rig[1][0].events)


def test_nonfinite_homing_position_aborts_before_relief(rig, monkeypatch, capsys):
    original = LiftBus.connect

    def connect(bus, **kwargs):
        original(bus, **kwargs)

        def hook(register):
            if register == "Present_Position" and bus.registers[("Goal_Velocity", "lift_axis")] > 0:
                bus.read_sequences[(register, "lift_axis")] = [float("nan")]

        bus.hook = hook

    monkeypatch.setattr(LiftBus, "connect", connect)
    assert execute() == 2
    assert "telemetry" in capsys.readouterr().out.lower()
    assert_stopped(rig[1][0])
    assert not any(e[1:] == ("write", "Goal_Velocity", "lift_axis", -200) for e in rig[1][0].events)


@pytest.mark.parametrize("fault,reason", [
    ("slow_relief", "8.0s"),
    ("late_target", "8.0s"),
    ("settle_descent", "direction"), ("home_timeout", "timed out"),
    ("stationary_motion", "motion"), ("fault_in_rest", "status"),
])
def test_remaining_time_and_stationary_bounds(rig, monkeypatch, capsys, fault, reason):
    clock, buses, _ = rig
    original = LiftBus.connect

    def connect(bus, **kwargs):
        original(bus, **kwargs)
        if fault == "slow_relief":
            bus.up_factor = 0.2
        if fault == "late_target":
            bus.up_factor = 0.302
        if fault == "home_timeout":
            bus.bottom = 100000
        stopped_reads = 0

        def hook(register):
            nonlocal stopped_reads
            if register != "Present_Temperature" or not bus.raised:
                return
            if bus.registers[("Goal_Velocity", "lift_axis")] != 0:
                return
            stopped_reads += 1
            if fault == "settle_descent" and stopped_reads == 1:
                bus.position += 100  # Two mm loss after zero, before rest.
            if fault == "stationary_motion" and stopped_reads == 8:
                bus.position -= 30
            if fault == "fault_in_rest" and stopped_reads == 8:
                bus.registers[("Status", "lift_axis")] = 4

        bus.hook = hook

    monkeypatch.setattr(LiftBus, "connect", connect)
    assert execute() in (1, 2)
    assert reason in capsys.readouterr().out.lower()
    assert_stopped(buses[0])
    assert clock.now < 122


def test_gate_refusal_never_homes_or_enables_torque(rig, monkeypatch, capsys):
    monkeypatch.setattr(builtins, "input", lambda _: "no")
    assert execute() == 2
    assert "authorize" in capsys.readouterr().out
    assert not any(e[1:3] == ("write", "Torque_Enable") and e[-1] == 1 for e in rig[1][0].events)
    assert_stopped(rig[1][0])


def test_initial_position_baseline_must_be_finite_before_torque(rig, monkeypatch, capsys):
    original = LiftBus.connect

    def connect(bus, **kwargs):
        original(bus, **kwargs)
        bus.read_sequences[("Present_Position", "lift_axis")] = [float("nan")]

    monkeypatch.setattr(LiftBus, "connect", connect)
    assert execute() == 2
    assert "telemetry" in capsys.readouterr().out.lower()
    assert not any(e[1:3] == ("write", "Torque_Enable") and e[-1] == 1 for e in rig[1][0].events)
    assert_stopped(rig[1][0])


def test_unexpected_shutdown_exception_cannot_replace_primary_refusal(rig, monkeypatch, capsys):
    monkeypatch.setattr(builtins, "input", lambda _: "no")
    original = robot_module.AlohaMini._safe_shutdown

    def shutdown(robot, **kwargs):
        original(robot, **kwargs)
        raise OSError("secondary shutdown failure")

    monkeypatch.setattr(robot_module.AlohaMini, "_safe_shutdown", shutdown)
    assert execute() == 2
    output = capsys.readouterr().out
    assert "authorize" in output and "secondary shutdown failure" in output
    assert_stopped(rig[1][0])


def test_second_sigint_cannot_interrupt_zero_torque_off_or_close(rig, monkeypatch, capsys):
    original_connect = LiftBus.connect
    original_write = LiftBus.write
    previous_handler = signal.getsignal(signal.SIGINT)
    interrupted = False
    primary_failed = False

    def connect(bus, **kwargs):
        original_connect(bus, **kwargs)

        def hook(register):
            nonlocal primary_failed
            if bus.raised and register == "Present_Temperature":
                primary_failed = True
                raise OSError("primary relief telemetry failure")

        bus.hook = hook

    def write(bus, register, motor, value, **kwargs):
        nonlocal interrupted
        if (
            primary_failed
            and register == "Goal_Velocity"
            and motor == "lift_axis"
            and int(value) == 0
            and not interrupted
        ):
            interrupted = True
            signal.raise_signal(signal.SIGINT)
        original_write(bus, register, motor, value, **kwargs)

    monkeypatch.setattr(LiftBus, "connect", connect)
    monkeypatch.setattr(LiftBus, "write", write)
    assert execute() == 2
    assert "primary relief telemetry failure" in capsys.readouterr().out
    assert interrupted and signal.getsignal(signal.SIGINT) is previous_handler
    assert_stopped(rig[1][0])


@pytest.mark.parametrize("failed_read", [OSError("secondary homing current read failed"), float("nan")])
def test_homing_cannot_hide_a_fault_in_its_own_current_read(rig, monkeypatch, capsys, failed_read):
    original_connect = LiftBus.connect

    def connect(bus, **kwargs):
        original_connect(bus, **kwargs)
        current_reads = 0

        def hook(register):
            nonlocal current_reads
            if register == "Present_Current" and bus.registers[("Goal_Velocity", "lift_axis")] > 0:
                current_reads += 1
                if current_reads % 2 == 0:
                    bus.read_sequences[(register, "lift_axis")] = [failed_read]

        bus.hook = hook

    monkeypatch.setattr(LiftBus, "connect", connect)
    assert execute() != 0
    assert "current" in capsys.readouterr().out.lower()
    assert not any(e[1:] == ("write", "Goal_Velocity", "lift_axis", -200) for e in rig[1][0].events)
    assert_stopped(rig[1][0])


@pytest.mark.parametrize("model", ["alohamini2", "alohamini2pro"])
@pytest.mark.parametrize("flag", ["--lift_relief", "--lift_readback"])
def test_opt_in_cannot_construct_am2_or_am2pro(rig, monkeypatch, model, flag):
    _, buses, _ = rig
    monkeypatch.setattr(sys, "argv", ["host", "--robot_model", model, flag])
    assert execute() == 2
    assert not buses
