"""Exercise the opt-in diagnostic with real lift/control/cleanup and fake serial I/O."""

from __future__ import annotations

import builtins
import json
import signal
import sys
from collections import Counter

import pytest

from lerobot.robots.alohamini import alohamini as robot_module
from lerobot.robots.alohamini import alohamini_host as host
from lerobot.robots.alohamini import lift_axis
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
            goal = self.registers[("Goal_Velocity", motor)]
            torque = self.registers[("Torque_Enable", motor)]
            elapsed = self.clock.now - self.last_update
            self.position = min(self.bottom, self.position + goal * torque * elapsed * (
                self.up_factor if goal < 0 else 1.0
            ))
            self.last_update = self.clock.now
            if goal < 0:
                self.raised = True
            stopped = not goal or (goal > 0 and self.position >= self.bottom)
            self.registers[("Present_Position", motor)] = round(self.position)
            self.registers[("Present_Velocity", motor)] = 0 if stopped else goal
            self.registers[("Present_Current", motor)] = (
                self.rest_current if self.raised and stopped else 50 if stopped and torque else 5
            )
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
    assert robot.lift._z0_deg < 0  # Original bottom reference survives the relief move.
    records = reports(capsys)
    observations = [r for r in records if r.get("phase") == "rest"]
    assert len(observations) >= 45
    assert all(10 <= r["height_mm"] <= 12 for r in observations)
    assert observations[-1]["elapsed_s"] - observations[0]["elapsed_s"] >= 44
    assert all(r["goal_velocity_raw"] == r["present_velocity_raw"] == 0 for r in observations)
    cleanup = [r for r in records if r.get("phase") == "cleanup_readback"]
    assert len(cleanup) == 1
    assert cleanup[0]["torque_enable"] == cleanup[0]["goal_velocity_raw"] == 0
    assert 145 <= clock.now <= 165
    reads = Counter(e[2] for e in bus.events if e[1] == "read")
    for name in ("Firmware_Major_Version", "Firmware_Minor_Version", "Max_Temperature_Limit",
                 "Unloading_Condition", "Velocity_closed_loop_P_proportional_coefficient",
                 "Velocity_closed_loop_I_integral_coefficient", "Maximum_Velocity_Limit"):
        assert reads[name] == 1
    assert reads["Phase"] == 0


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
    assert "before_torque" in output and "unexpected torque" in output
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
    assert "cool" in output.lower() and '"phase":"before_torque"' in output
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
    ("hot_start", "cool"), ("missing", "telemetry"), ("fault_status", "status"),
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
    ("interrupt", "interrupted"),
])
def test_motion_and_rest_aborts_converge_on_zero_torque_off_and_close(rig, monkeypatch, capsys, fault, reason):
    clock, buses, _ = rig
    original = LiftBus.connect

    def connect(bus, **kwargs):
        original(bus, **kwargs)
        if fault in ("no_motion", "wrong_direction", "travel"):
            bus.up_factor = {"no_motion": 0, "wrong_direction": -1, "travel": 100}[fault]
        if fault == "high_idle_current":
            bus.rest_current = 31  # 201.5mA, below the earlier 300mA criterion.

        def hook(register):
            goal = bus.registers[("Goal_Velocity", "lift_axis")]
            if fault == "wrong_direction" and goal < 0 and register == "Present_Velocity":
                bus.read_sequences[(register, "lift_axis")] = [200]
            if fault == "hot_homing" and goal > 0 or fault == "hot_relief" and goal < 0:
                bus.registers[("Present_Temperature", "lift_axis")] = 55
            if bus.raised and register == "Present_Temperature":
                if fault == "telemetry_failure":
                    raise OSError("telemetry disconnected")
                if fault == "interrupt":
                    bus.port_handler.is_using = True
                    raise KeyboardInterrupt()

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
    assert rejected[-1]["phase"] == "homing"
    assert rejected[-1]["present_temperature_raw"] == 93
    assert rejected[-1]["temperature_c"] == 93
    assert "diagnostic ceiling 55 C" in rejected[-1]["rejection_reason"]
    normal_homing = [
        record
        for record in records
        if record.get("phase") == "homing" and not record.get("rejected")
    ]
    assert normal_homing[-1]["present_temperature_raw"] == 30
    assert rejected[-1]["elapsed_s"] - normal_homing[-1]["elapsed_s"] < 1.0
    assert not any(record.get("phase") == "home_complete" for record in records)
    assert_stopped(buses[0])
    assert clock.now < 103


def test_rejected_sample_includes_diagnostic_sdk_request_reply_trace(rig, monkeypatch, capsys):
    _, buses, _ = rig
    original = LiftBus.connect
    homing_temperature_reads = 0

    def connect(bus, **kwargs):
        original(bus, **kwargs)
        bus.packet_handler = object()

    def verified_read(bus, register, motor):
        nonlocal homing_temperature_reads
        value = bus.read(register, motor, normalize=False, num_retry=0)
        if register == "Present_Temperature" and bus.registers[("Goal_Velocity", motor)] == 200:
            homing_temperature_reads += 1
            if homing_temperature_reads == 2:
                value = 93
        return int(value), {
            "register": register,
            "address": 63 if register == "Present_Temperature" else 0,
            "requested_width": 1,
            "requested_id": 11,
            "response_bytes": [255, 255, 11, 3, 0, int(value), 0],
            "response_length": 7,
            "response_id": 11,
            "sdk_servo_error": 0,
            "response_error": 0,
            "checksum_ok": True,
            "elapsed_ms": 0.1,
            "request_correlation": "id_length_checksum_only",
        }

    monkeypatch.setattr(LiftBus, "connect", connect)
    monkeypatch.setattr(lift_relief, "read_diagnostic_scalar", verified_read)

    assert execute() == 2
    rejected = [record for record in reports(capsys) if record.get("rejected")]
    temperature_trace = [
        trace for trace in rejected[-1]["read_traces"] if trace["register"] == "Present_Temperature"
    ]
    assert rejected[-1]["present_temperature_raw"] == 93
    assert temperature_trace[-1]["response_bytes"][5] == 93
    assert temperature_trace[-1]["request_correlation"] == "id_length_checksum_only"
    assert not any(e[1:3] == ("write", "Goal_Velocity") and e[-1] < 0 for e in buses[0].events)
    assert_stopped(buses[0])


def test_post_sample_goal_refusal_is_emitted_inside_report_throttle(rig, monkeypatch, capsys):
    _, buses, _ = rig
    original = LiftBus.connect
    homing_goal_reads = 0

    def connect(bus, **kwargs):
        original(bus, **kwargs)
        bus.packet_handler = object()

    def verified_read(bus, register, motor):
        nonlocal homing_goal_reads
        value = bus.read(register, motor, normalize=False, num_retry=0)
        if register == "Goal_Velocity" and bus.registers[(register, motor)] == 200:
            homing_goal_reads += 1
            if homing_goal_reads == 2:
                value = 0
        return int(value), {
            "register": register,
            "address": 46 if register == "Goal_Velocity" else 0,
            "requested_width": 2,
            "requested_id": 11,
            "response_bytes": [255, 255, 11, 4, 0, int(value) & 0xFF, int(value) >> 8, 0],
            "response_length": 8,
            "response_id": 11,
            "response_error": 0,
            "sdk_servo_error": 0,
            "checksum_ok": True,
            "elapsed_ms": 0.1,
            "request_correlation": "id_length_checksum_only",
        }

    monkeypatch.setattr(LiftBus, "connect", connect)
    monkeypatch.setattr(lift_relief, "read_diagnostic_scalar", verified_read)

    assert execute() == 2
    records = reports(capsys)
    rejected = [record for record in records if record.get("rejected")]
    normal_homing = [
        record for record in records if record.get("phase") == "homing" and not record.get("rejected")
    ]
    assert rejected[-1]["phase"] == "homing"
    assert rejected[-1]["goal_velocity_raw"] == 0
    assert "unexpected torque/goal velocity" in rejected[-1]["rejection_reason"]
    goal_trace = [trace for trace in rejected[-1]["read_traces"] if trace["register"] == "Goal_Velocity"]
    assert goal_trace[-1]["response_bytes"][5] == 0
    assert rejected[-1]["elapsed_s"] - normal_homing[-1]["elapsed_s"] < 1.0
    assert_stopped(buses[0])


def test_configuration_transport_refusal_emits_the_rejected_reply_trace(rig, monkeypatch, capsys):
    _, buses, _ = rig
    original = LiftBus.connect
    failed = False

    def connect(bus, **kwargs):
        original(bus, **kwargs)
        bus.packet_handler = object()

    def verified_read(bus, register, motor):
        nonlocal failed
        if register == "Model_Number" and not failed:
            failed = True
            raise lift_relief.DiagnosticReadError(
                "Response checksum could not be verified.",
                {
                    "register": register,
                    "address": 0,
                    "requested_width": 2,
                    "requested_id": 11,
                    "response_bytes": [255, 255, 11, 4, 0, 9, 3, 0],
                    "response_length": 8,
                    "response_id": 11,
                    "sdk_servo_error": 0,
                    "response_error": 0,
                    "checksum_ok": False,
                    "elapsed_ms": 0.1,
                    "request_correlation": "id_length_checksum_only",
                },
            )
        value = bus.read(register, motor, normalize=False, num_retry=0)
        return int(value), {"register": register, "checksum_ok": True}

    monkeypatch.setattr(LiftBus, "connect", connect)
    monkeypatch.setattr(lift_relief, "read_diagnostic_scalar", verified_read)

    assert execute() == 2
    rejected = [record for record in reports(capsys) if record.get("rejected")]
    assert rejected[-1]["phase"] == "configuration"
    assert "Configuration telemetry failed" in rejected[-1]["rejection_reason"]
    assert rejected[-1]["read_traces"][-1]["register"] == "Model_Number"
    assert rejected[-1]["read_traces"][-1]["checksum_ok"] is False
    assert_stopped(buses[0])


def test_guarded_diagnostic_routes_lift_position_and_current_through_verified_reader(
    rig, monkeypatch, capsys
):
    _, buses, _ = rig
    original = LiftBus.connect
    verified_reads = Counter()

    def connect(bus, **kwargs):
        original(bus, **kwargs)
        bus.packet_handler = object()

    def verified_read(bus, register, motor):
        verified_reads[register] += 1
        value = bus.read(register, motor, normalize=False, num_retry=0)
        return int(value), {
            "register": register,
            "address": 0,
            "requested_width": 1,
            "requested_id": 11,
            "response_bytes": [],
            "response_length": 0,
            "response_id": 11,
            "sdk_servo_error": 0,
            "response_error": 0,
            "checksum_ok": True,
            "elapsed_ms": 0.1,
            "request_correlation": "id_length_checksum_only",
        }

    monkeypatch.setattr(LiftBus, "connect", connect)
    monkeypatch.setattr(lift_relief, "read_diagnostic_scalar", verified_read)

    assert execute() == 0
    capsys.readouterr()
    bus_reads = Counter(event[2] for event in buses[0].events if event[1] == "read")
    assert verified_reads["Present_Position"] == bus_reads["Present_Position"] > 0
    assert verified_reads["Present_Current"] == bus_reads["Present_Current"] > 0
    assert_stopped(buses[0])


def test_homing_position_transport_refusal_emits_the_rejected_reply_trace(rig, monkeypatch, capsys):
    _, buses, _ = rig
    original = LiftBus.connect

    def connect(bus, **kwargs):
        original(bus, **kwargs)
        bus.packet_handler = object()

    def verified_read(bus, register, motor):
        if register == "Present_Position" and bus.registers[("Goal_Velocity", motor)] == 200:
            raise lift_relief.DiagnosticReadError(
                "Response payload length 1 did not match requested 2.",
                {
                    "register": register,
                    "address": 56,
                    "requested_width": 2,
                    "requested_id": 11,
                    "response_bytes": [255, 255, 11, 3, 0, 93, 147],
                    "response_length": 7,
                    "response_id": 11,
                    "response_payload_length": 1,
                    "sdk_servo_error": 0,
                    "response_error": 0,
                    "checksum_ok": True,
                    "elapsed_ms": 0.1,
                    "request_correlation": "id_length_checksum_only",
                },
            )
        value = bus.read(register, motor, normalize=False, num_retry=0)
        return int(value), {"register": register, "checksum_ok": True}

    monkeypatch.setattr(LiftBus, "connect", connect)
    monkeypatch.setattr(lift_relief, "read_diagnostic_scalar", verified_read)

    assert execute() == 2
    records = reports(capsys)
    rejected = [record for record in records if record.get("rejected")]
    assert rejected[-1]["phase"] == "homing_read"
    assert rejected[-1]["read_traces"][-1]["register"] == "Present_Position"
    assert rejected[-1]["read_traces"][-1]["response_payload_length"] == 1
    assert not any(record.get("phase") == "home_complete" for record in records)
    assert_stopped(buses[0])


def test_cleanup_transport_trace_is_retained_without_replacing_primary_refusal(rig, monkeypatch, capsys):
    _, buses, _ = rig
    original = LiftBus.connect
    hot_sample_finished = False
    injected_high = False
    cleanup_failed = False

    def connect(bus, **kwargs):
        original(bus, **kwargs)
        bus.packet_handler = object()

    def verified_read(bus, register, motor):
        nonlocal cleanup_failed, hot_sample_finished, injected_high
        if hot_sample_finished and register == "Torque_Enable" and not cleanup_failed:
            cleanup_failed = True
            raise lift_relief.DiagnosticReadError(
                "Response checksum could not be verified.",
                {
                    "register": register,
                    "address": 40,
                    "requested_width": 1,
                    "requested_id": 11,
                    "response_bytes": [255, 255, 11, 3, 0, 0, 1],
                    "response_length": 7,
                    "response_id": 11,
                    "response_error": 0,
                    "sdk_servo_error": 0,
                    "checksum_ok": False,
                    "elapsed_ms": 0.1,
                    "request_correlation": "id_length_checksum_only",
                },
            )
        value = bus.read(register, motor, normalize=False, num_retry=0)
        if register == "Present_Temperature" and bus.registers[("Goal_Velocity", motor)] == 200:
            value = 93
            injected_high = True
        if register == "Present_Load" and injected_high:
            hot_sample_finished = True
        return int(value), {"register": register, "checksum_ok": True}

    monkeypatch.setattr(LiftBus, "connect", connect)
    monkeypatch.setattr(lift_relief, "read_diagnostic_scalar", verified_read)

    assert execute() == 2
    output = capsys.readouterr().out
    records = [
        json.loads(line.split("] ", 1)[1])
        for line in output.splitlines()
        if line.startswith("[LIFT RELIEF] ")
    ]
    cleanup_rejections = [
        record
        for record in records
        if record.get("phase") == "cleanup_readback" and record.get("rejected")
    ]
    assert cleanup_rejections[-1]["read_traces"][-1]["register"] == "Torque_Enable"
    assert cleanup_rejections[-1]["read_traces"][-1]["checksum_ok"] is False
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
    assert "cool" in capsys.readouterr().out.lower()
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
    ("slow_relief", "8.0s"), ("slow_telemetry", "gap"),
    ("late_target", "8.0s"),
    ("late_home", "timed out"),
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
        if fault in ("home_timeout", "late_home"):
            bus.bottom = 100000
        stopped_reads = 0

        def hook(register):
            nonlocal stopped_reads
            if fault == "late_home" and not bus.raised:
                if register == "Present_Current" and clock.now >= 119.89:
                    bus.read_sequences[(register, "lift_axis")] = [50]
                if register == "Present_Temperature" and clock.now >= 119.94:
                    clock.sleep(0.1)
            if register != "Present_Temperature" or not bus.raised:
                return
            if fault == "slow_telemetry":
                clock.sleep(0.6)
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

    def connect(bus, **kwargs):
        original_connect(bus, **kwargs)

        def hook(register):
            if bus.raised and register == "Present_Temperature":
                raise OSError("primary relief telemetry failure")

        bus.hook = hook

    def write(bus, register, motor, value, **kwargs):
        nonlocal interrupted
        if bus.raised and motor.startswith("base_") and not interrupted:
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
