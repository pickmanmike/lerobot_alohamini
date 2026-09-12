"""Opt-in AM1 lift diagnostics; one serial owner, no network or ordinary host loop.

Diagnostic policies below are deliberately fixed, not servo ratings or settings.
STS3215 memory table V3.7: temperature registers 13/63 are degrees C (1 C/LSB),
voltage is 0.1 V/LSB. Current uses the existing AM1 6.5 mA/LSB conversion.
https://files.waveshare.com/upload/2/27/ST3215%20memory%20register%20map-EN.xls
"""

from __future__ import annotations

import json
import math
import signal
import time
from collections.abc import Callable
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from lerobot.motors.motors_bus import get_address

from . import lift_motor_feedback as grouped_feedback
from .lift_axis import LiftAxis
from .motor_safety import REGISTER_RETRIES, write_register

if TYPE_CHECKING:
    from .alohamini import AlohaMini


COOL_START_C = 40
ABORT_C = 55
RELIEF_MM = 10.0
MAX_RELIEF_MM = 12.0
RELIEF_TIMEOUT_S = 8.0
REST_S = 45.0
READBACK_S = 3.0
POLL_S = 0.1
MAX_SAMPLE_S = 0.5
STILL_VELOCITY_RAW = 5

# Read once, before any write. Never read Phase, nor copy/rewrite these settings.
CONFIG_REGISTERS = (
    "Model_Number", "Firmware_Major_Version", "Firmware_Minor_Version",
    "Max_Temperature_Limit", "Min_Voltage_Limit", "Max_Voltage_Limit",
    "Unloading_Condition", "Angular_Resolution", "Protection_Current",
    "Protective_Torque", "Protection_Time", "Overload_Torque", "Over_Current_Protection_Time",
    "Velocity_closed_loop_P_proportional_coefficient",
    "Velocity_closed_loop_I_integral_coefficient", "Acceleration", "Goal_Time", "Torque_Limit",
    "Moving_Velocity_Threshold", "DTs", "Velocity_Unit_factor", "Hts",
    "Maximum_Velocity_Limit", "Maximum_Acceleration", "Acceleration_Multiplier ",
)


class ReliefRefusal(RuntimeError):
    """Expected diagnostic refusal; never automatically retry or restart."""


class DiagnosticReadError(RuntimeError):
    """A diagnostic scalar read that cannot be trusted, with bounded byte evidence."""

    def __init__(self, message: str, trace: dict[str, Any]):
        super().__init__(message)
        self.trace = trace


def _status_packets(response: bytes) -> list[dict[str, Any]]:
    packets: list[dict[str, Any]] = []
    offset = 0
    while offset + 6 <= len(response):
        header = response.find(b"\xff\xff", offset)
        if header < 0 or header + 4 > len(response):
            break
        declared_length = response[header + 3]
        packet_length = declared_length + 4
        if packet_length < 6 or header + packet_length > len(response):
            packets.append(
                {
                    "response_id": response[header + 2],
                    "declared_length": declared_length,
                    "payload_length": max(declared_length - 2, 0),
                    "error": response[header + 4] if header + 4 < len(response) else None,
                    "checksum_ok": None,
                    "complete": False,
                }
            )
            break
        packet = response[header : header + packet_length]
        packets.append(
            {
                "response_id": packet[2],
                "declared_length": declared_length,
                "payload_length": max(declared_length - 2, 0),
                "error": packet[4],
                "checksum_ok": packet[-1] == (~sum(packet[2:-1]) & 0xFF),
                "complete": True,
            }
        )
        offset = header + packet_length
    return packets


def _read_trace(
    *,
    register: str,
    address: int,
    width: int,
    motor_id: int,
    pending: bytes = b"",
    pending_length: int = 0,
    request: bytes = b"",
    request_length: int | None = None,
    response: bytes = b"",
    response_length: int | None = None,
    comm_result: int | None = None,
    servo_error: int | None = None,
    elapsed_ms: float = 0.0,
) -> dict[str, Any]:
    packets = _status_packets(response)
    last = packets[-1] if packets else {}
    full_request_length = len(request) if request_length is None else request_length
    full_response_length = len(response) if response_length is None else response_length
    request_checksum_ok = (
        request[-1] == (~sum(request[2:-1]) & 0xFF)
        if len(request) >= 6 and full_request_length == len(request)
        else None
    )
    return {
        "register": register,
        "address": address,
        "requested_width": width,
        "requested_id": motor_id,
        "pending_bytes": list(pending),
        "pending_length": pending_length,
        "pending_truncated": pending_length > len(pending),
        "request_bytes": list(request),
        "request_length": full_request_length,
        "request_truncated": full_request_length > len(request),
        "request_checksum_ok": request_checksum_ok,
        "response_bytes": list(response),
        "response_length": full_response_length,
        "response_truncated": full_response_length > len(response),
        "response_id": last.get("response_id"),
        "response_declared_length": last.get("declared_length"),
        "response_payload_length": last.get("payload_length"),
        "sdk_servo_error": servo_error,
        "response_error": last.get("error"),
        "checksum_ok": last.get("checksum_ok"),
        "comm_result": comm_result,
        "elapsed_ms": round(elapsed_ms, 3),
        "response_fields": ["id", "declared_length", "error", "payload", "checksum"],
        # Protocol-1 status packets do not return the requested register address
        # or a transaction sequence, so even a valid reply has this bounded limit.
        "request_correlation": "id_length_checksum_only",
    }


def read_diagnostic_scalar(bus: Any, register: str, motor: str) -> tuple[int, dict[str, Any]]:
    """Read one raw scalar through the SDK with diagnostic-only transaction checks.

    A timeout is a refusal, not an automatic retry: a delayed same-ID reply could
    otherwise satisfy the retry. Pending input is preserved in the error trace and
    discarded before returning. Exact payload length is checked in addition to the
    SDK's ID/checksum/error validation. Same-ID, same-length correspondence remains
    unprovable because the status packet carries no address or sequence field.
    """
    motor_config = bus.motors[motor]
    motor_id = motor_config.id
    address, width = get_address(bus.model_ctrl_table, motor_config.model, register)
    port = bus.port_handler
    serial_port = getattr(port, "ser", None)
    if serial_port is None or not hasattr(serial_port, "reset_input_buffer"):
        trace = _read_trace(register=register, address=address, width=width, motor_id=motor_id)
        raise DiagnosticReadError("Diagnostic input-buffer control is unavailable.", trace)
    if getattr(port, "is_using", False):
        trace = _read_trace(register=register, address=address, width=width, motor_id=motor_id)
        raise DiagnosticReadError("Diagnostic serial port is already in use.", trace)

    started_ns = time.monotonic_ns()
    pending_length = int(getattr(serial_port, "in_waiting", 0))
    if pending_length:
        pending = bytes(serial_port.read(min(pending_length, 250)))
        serial_port.reset_input_buffer()
        trace = _read_trace(
            register=register,
            address=address,
            width=width,
            motor_id=motor_id,
            pending=pending,
            pending_length=pending_length,
            elapsed_ms=(time.monotonic_ns() - started_ns) / 1e6,
        )
        raise DiagnosticReadError("Unexpected pending input before diagnostic request.", trace)
    serial_port.reset_input_buffer()

    read_functions = {
        1: bus.packet_handler.read1ByteTxRx,
        2: bus.packet_handler.read2ByteTxRx,
        4: bus.packet_handler.read4ByteTxRx,
    }
    if width not in read_functions:
        trace = _read_trace(register=register, address=address, width=width, motor_id=motor_id)
        raise DiagnosticReadError(f"Unsupported diagnostic register width {width}.", trace)

    request = bytearray()
    request_length = 0
    response = bytearray()
    original_write_port = port.writePort
    original_read_port = port.readPort

    def capture_write(packet):
        nonlocal request_length
        encoded = bytes(packet)
        request_length += len(encoded)
        remaining = max(250 - len(request), 0)
        request.extend(encoded[:remaining])
        return original_write_port(packet)

    def capture_read(length: int):
        chunk = original_read_port(length)
        response.extend(chunk)
        return chunk

    port.writePort = capture_write
    port.readPort = capture_read
    sdk_error: Exception | None = None
    value = 0
    comm_result: int | None = None
    servo_error: int | None = None
    try:
        value, comm_result, servo_error = read_functions[width](port, motor_id, address)
    except Exception as error:
        sdk_error = error
    finally:
        port.writePort = original_write_port
        port.readPort = original_read_port

    elapsed_ms = (time.monotonic_ns() - started_ns) / 1e6
    trace = _read_trace(
        register=register,
        address=address,
        width=width,
        motor_id=motor_id,
        request=bytes(request),
        request_length=request_length,
        response=bytes(response[:250]),
        response_length=len(response),
        comm_result=comm_result,
        servo_error=servo_error,
        elapsed_ms=elapsed_ms,
    )
    if sdk_error is not None:
        raise DiagnosticReadError(
            f"SDK read raised {type(sdk_error).__name__}: {sdk_error}", trace
        ) from sdk_error
    if comm_result is None or servo_error is None:
        raise DiagnosticReadError("SDK read returned incomplete status metadata.", trace)
    if not bus._is_comm_success(comm_result):
        raise DiagnosticReadError(bus.packet_handler.getTxRxResult(comm_result), trace)
    if bus._is_error(servo_error):
        raise DiagnosticReadError(bus.packet_handler.getRxPacketError(servo_error), trace)
    if trace["response_id"] != motor_id:
        raise DiagnosticReadError(
            f"Response ID {trace['response_id']} did not match requested motor ID {motor_id}.", trace
        )
    if trace["checksum_ok"] is not True:
        raise DiagnosticReadError("Response checksum could not be verified.", trace)
    if trace["response_payload_length"] != width:
        raise DiagnosticReadError(
            f"Response payload length {trace['response_payload_length']} did not match requested {width}.",
            trace,
        )
    decoded = bus._decode_sign(register, {motor_id: value})
    return int(decoded[motor_id]), trace


class ReliefCheck:
    def __init__(self, robot: AlohaMini):
        self.robot = robot
        self.start = time.monotonic()
        self.last_sample: float | None = None
        self.last_report = float("-inf")
        self.phase = ""
        self.config: dict[str, int] = {}
        self.read_traces: list[dict[str, Any]] = []

    def emit(self, record: dict) -> None:
        print("[LIFT RELIEF] " + json.dumps(record, allow_nan=False, separators=(",", ":")), flush=True)

    def refuse_sample(self, data: dict, reason: str) -> None:
        self.emit(
            {
                **data,
                "rejected": True,
                "rejection_reason": reason,
                "read_traces": list(self.read_traces),
            }
        )
        raise ReliefRefusal(reason)

    def raw(self, register: str) -> int:
        bus = self.robot.left_bus
        if hasattr(bus, "packet_handler"):
            try:
                value, trace = read_diagnostic_scalar(bus, register, self.robot.lift.cfg.name)
            except DiagnosticReadError as error:
                self.read_traces.append(error.trace)
                raise ReliefRefusal(f"Unusable telemetry for {register}: {error}") from error
            self.read_traces.append(trace)
        else:
            # Focused FakeBus tests do not emulate the vendor packet handler. The
            # production diagnostic always uses FeetechMotorsBus and the path above.
            value = bus.read(
                register, self.robot.lift.cfg.name, normalize=False, num_retry=REGISTER_RETRIES
            )
        if not isinstance(value, (int, float)) or not math.isfinite(value) or int(value) != value:
            raise ReliefRefusal(f"Unusable telemetry for {register}.")
        return int(value)

    def lift_snapshot(self) -> dict[str, int | float | bool]:
        present_current_raw = self.raw("Present_Current")
        return {
            "is_homed": self.robot.lift.is_homed,
            "present_current_raw": present_current_raw,
            "present_current_ma": round(abs(present_current_raw) * 6.5, 1),
            "present_temperature_raw": self.raw("Present_Temperature"),
            "present_voltage_raw": self.raw("Present_Voltage"),
            "goal_velocity_raw": self.raw("Goal_Velocity"),
            "present_velocity_raw": self.raw("Present_Velocity"),
            "torque_enable": self.raw("Torque_Enable"),
            "operating_mode": self.raw("Operating_Mode"),
            "status": self.raw("Status"),
        }

    def read_configuration(self) -> None:
        self.read_traces = []
        try:
            self.config = {name: self.raw(name) for name in CONFIG_REGISTERS}
        except Exception as error:
            reason = f"Configuration telemetry failed: {error}"
            self.emit(
                {
                    "phase": "configuration",
                    "elapsed_s": round(time.monotonic() - self.start, 3),
                    "wall_time_ns": time.time_ns(),
                    "rejected": True,
                    "rejection_reason": reason,
                    "read_traces": list(self.read_traces),
                }
            )
            raise ReliefRefusal(reason) from error
        self.emit(
            {
                "phase": "configuration",
                "registers_raw": self.config,
                "temperature_unit": "C",
                "current_unit": "mA",
                "voltage_unit": "V",
            }
        )
        if self.config["Model_Number"] != 777 or self.config["Angular_Resolution"] != 1:
            raise ReliefRefusal(
                "Expected STS3215 model 777 with angular resolution 1; conversion unverified."
            )
        if not COOL_START_C < self.config["Max_Temperature_Limit"] <= 100:
            raise ReliefRefusal("Unusable temperature limit telemetry; no limit will be changed.")
        if not 0 < self.config["Min_Voltage_Limit"] < self.config["Max_Voltage_Limit"] <= 255:
            raise ReliefRefusal("Unusable voltage limit telemetry.")

    def sample(self, phase: str, *, displacement_mm: float | None = None, force: bool = False) -> dict:
        started = time.monotonic()
        self.read_traces = []
        try:
            data = self.lift_snapshot()
            data["present_load_raw"] = self.raw("Present_Load")
            # Homing owns the position accumulator. Reading height inside its callback
            # would consume its delta and manufacture a false position-stall result.
            height = self.robot.lift.get_height_mm(read_raw=self.raw) if self.robot.lift.is_homed else None
        except Exception as error:
            reason = f"{phase}: lift telemetry failed: {error}"
            self.emit(
                {
                    "phase": phase,
                    "elapsed_s": round(time.monotonic() - self.start, 3),
                    "wall_time_ns": time.time_ns(),
                    "rejected": True,
                    "rejection_reason": reason,
                    "read_traces": list(self.read_traces),
                }
            )
            raise ReliefRefusal(reason) from error
        now = time.monotonic()
        if any(not math.isfinite(value) for value in data.values()) or (
            height is not None and not math.isfinite(height)
        ):
            raise ReliefRefusal(f"{phase}: nonfinite telemetry.")
        data.update(phase=phase, elapsed_s=round(now - self.start, 3), wall_time_ns=time.time_ns(),
                    height_mm=height, homing_displacement_mm=displacement_mm,
                    temperature_c=data["present_temperature_raw"],
                    voltage_v=data["present_voltage_raw"] / 10.0)
        should_report = (
            force or phase != self.phase or now - self.last_report >= 0.99
            or (phase == "rest" and data["present_current_ma"] >= 200)
        )
        self.phase = phase
        if now - started > MAX_SAMPLE_S or (
            self.last_sample is not None and now - self.last_sample > MAX_SAMPLE_S
        ):
            self.refuse_sample(data, f"{phase}: telemetry gap exceeded {MAX_SAMPLE_S}s.")
        self.last_sample = now
        if data["status"] != 0:
            self.refuse_sample(data, f"{phase}: servo status fault {data['status']}.")
        ceiling = min(ABORT_C, self.config["Max_Temperature_Limit"])
        if not 0 <= data["temperature_c"] < ceiling:
            self.refuse_sample(
                data,
                f"{phase}: temperature {data['temperature_c']} C reached diagnostic ceiling {ceiling} C.",
            )
        if not (
            self.config["Min_Voltage_Limit"]
            <= data["present_voltage_raw"]
            <= self.config["Max_Voltage_Limit"]
        ):
            self.refuse_sample(data, f"{phase}: voltage outside the read-only configured limits.")
        if data["torque_enable"] not in (0, 1) or data["operating_mode"] != 1:
            self.refuse_sample(data, f"{phase}: unexpected torque/mode telemetry.")
        if abs(data["present_velocity_raw"]) > 400:
            self.refuse_sample(data, f"{phase}: unexpected velocity magnitude.")
        if should_report:
            self.emit(data)
            self.last_report = now
        return data

    def preflight(self) -> None:
        self.last_sample = None  # The operator gate may take arbitrarily long, torque remains off.
        data = self.sample("preflight", force=True)
        if data["temperature_c"] > COOL_START_C:
            self.refuse_sample(
                data,
                f"Start must be cool: {data['temperature_c']} C exceeds {COOL_START_C} C.",
            )
        if data["torque_enable"] != 0:
            self.refuse_sample(data, "Start requires lift torque disabled.")
        if data["goal_velocity_raw"] != 0 or abs(data["present_velocity_raw"]) > STILL_VELOCITY_RAW:
            self.refuse_sample(data, "Start requires zero goal velocity and a stationary carriage.")

    def expect(self, data: dict, *, torque: int, goal: int) -> None:
        if data["torque_enable"] != torque or data["goal_velocity_raw"] != goal:
            self.refuse_sample(data, f"{data['phase']}: unexpected torque/goal velocity readback.")

    def expect_stationary(self, data: dict) -> None:
        if data["goal_velocity_raw"] != 0 or abs(data["present_velocity_raw"]) > STILL_VELOCITY_RAW:
            self.refuse_sample(
                data,
                f"{data['phase']}: expected zero goal velocity and a stationary carriage.",
            )

    def cleanup_readback(self) -> None:
        # The ordinary safe shutdown writes Torque_Enable=0 and then Lock=0.
        # End this diagnostic with Torque_Enable itself, then prove torque and
        # velocity-target state before the owning bus is closed.
        self.read_traces = []
        record: dict[str, Any] = {
            "phase": "cleanup_readback",
            "elapsed_s": round(time.monotonic() - self.start, 3),
            "wall_time_ns": time.time_ns(),
        }
        try:
            write_register(self.robot.left_bus, "Torque_Enable", self.robot.lift.cfg.name, 0)
            record.update(
                torque_enable=self.raw("Torque_Enable"),
                goal_velocity_raw=self.raw("Goal_Velocity"),
                present_velocity_raw=self.raw("Present_Velocity"),
            )
        except Exception as error:
            reason = f"cleanup_readback: lift telemetry failed: {error}"
            self.emit(
                {
                    **record,
                    "rejected": True,
                    "rejection_reason": reason,
                    "read_traces": list(self.read_traces),
                }
            )
            raise ReliefRefusal(reason) from error
        self.emit(record)
        if record["torque_enable"] != 0 or record["goal_velocity_raw"] != 0:
            self.refuse_sample(
                record,
                "cleanup_readback: final torque/goal velocity readback was not zero.",
            )

    def readback_only(self) -> None:
        """Collect a short torque-off snapshot series without homing or motion."""
        self.read_configuration()
        self.preflight()
        print(
            f"Collecting {READBACK_S:g} seconds of torque-off lift telemetry. "
            "No homing, torque-enable, or nonzero velocity command will be sent.",
            flush=True,
        )
        started = time.monotonic()
        while True:
            data = self.sample("readback", force=True)
            self.expect(data, torque=0, goal=0)
            self.expect_stationary(data)
            if time.monotonic() - started >= READBACK_S:
                break
            time.sleep(POLL_S)


def make_grouped_transport(robot: AlohaMini) -> grouped_feedback.DirectSdkTransport:
    """Reuse the already-open ID-11 bus as the comparator's sole SDK owner."""
    return grouped_feedback.DirectSdkTransport(
        robot.config.left_port,
        motor_id=robot.lift.cfg.motor_id,
        port_handler=robot.left_bus.port_handler,
        packet_handler=robot.left_bus.packet_handler,
    )


class _ObservedLiftBus:
    """Track acknowledged lift state while delegating writes to the owning bus."""

    def __init__(self, delegate: Any, motor: str):
        self.delegate = delegate
        self.motor = motor
        self.motors = delegate.motors
        self.expected_torque = 0
        self.expected_goal = 0

    def read(self, register: str, motor: str, **kwargs) -> int | float:
        value = self.delegate.read(register, motor, **kwargs)
        if motor == self.motor and register == "Torque_Enable":
            self.expected_torque = int(value)
        elif motor == self.motor and register == "Goal_Velocity":
            self.expected_goal = int(value)
        return value

    def write(self, register: str, motor: str, value: int | float, **kwargs) -> None:
        self.delegate.write(register, motor, value, **kwargs)
        if motor == self.motor and register == "Torque_Enable":
            self.expected_torque = int(value)
        elif motor == self.motor and register == "Goal_Velocity":
            self.expected_goal = int(value)


class _GroupedLiftReader:
    """Give LiftAxis one fresh grouped position/current sample per homing poll."""

    def __init__(self, check: InstalledLiftCheck):
        self.check = check
        self.phase = "setup_position"
        self.last_record: dict[str, Any] | None = None
        self._current_available = False

    def set_phase(self, phase: str) -> None:
        self.phase = phase
        self._current_available = False

    def __call__(self, register: str) -> int:
        if register == "Present_Position":
            expected_torque = None if self.phase == "setup_position" else self.check.bus.expected_torque
            self.last_record = self.check.monitor.sample(
                self.phase,
                expected_torque=expected_torque,
                expected_goal=self.check.bus.expected_goal,
            )
            self._current_available = True
            return int(self.last_record["present_position_raw"])
        if register == "Present_Current" and self._current_available and self.last_record is not None:
            self._current_available = False
            return int(self.last_record["present_current_raw"])
        raise ReliefRefusal(
            f"{self.phase}: {register} was requested without its fresh grouped position sample."
        )


class InstalledLiftCheck:
    """ID-11-only installed home/relief/rest comparison using grouped feedback."""

    def __init__(
        self,
        robot: AlohaMini,
        *,
        input_fn: Callable[[str], str] | None = None,
    ) -> None:
        self.robot = robot
        self.input_fn = input if input_fn is None else input_fn
        self.transport = make_grouped_transport(robot)
        self.monitor = grouped_feedback.MotorFeedbackComparison(
            self.transport,
            input_fn=self.input_fn,
            monotonic=time.monotonic,
            sleep=time.sleep,
            emit=self.emit,
            # The installed comparison treats finite home/relief current as a
            # transient. Its >=200 mA rule applies only to raised rest.
            immediate_current_abort_ma=None,
        )
        self.bus = _ObservedLiftBus(robot.left_bus, robot.lift.cfg.name)
        self.lift = LiftAxis(robot.lift.cfg, bus_left=self.bus, bus_right=None)
        self.reader = _GroupedLiftReader(self)
        self.home_started = 0.0

    def emit(self, record: dict[str, Any]) -> None:
        evidence = {**record, "wall_time_ns": time.time_ns()}
        print(
            "[LIFT RELIEF] "
            + json.dumps(evidence, allow_nan=False, separators=(",", ":")),
            flush=True,
        )

    def refuse(self, record: dict[str, Any], reason: str) -> None:
        self.monitor.refuse(record, reason)

    def home_guard(self, phase: str, displacement_mm: float) -> None:
        if not math.isfinite(displacement_mm) or not math.isfinite(self.lift._last_tick):
            raise ReliefRefusal("homing: nonfinite position telemetry.")
        if phase == "before_torque":
            # LiftAxis has completed every setup/mode/zero write. Make torque-off
            # the final setup request, then prove a fresh stationary window.
            setup_record = self.reader.last_record
            if setup_record is None:
                raise ReliefRefusal("before_torque: grouped setup evidence was unavailable.")
            self.monitor.record(
                "setup_after_writes",
                torque_enable=int(setup_record["torque_enable"]),
                goal_velocity_raw=int(setup_record["goal_velocity_raw"]),
                operating_mode=int(setup_record["operating_mode"]),
                group_trace=setup_record["group_trace"],
            )
            try:
                write_register(self.bus, "Torque_Enable", self.lift.cfg.name, 0)
            except Exception as error:
                raise ReliefRefusal(
                    f"before_torque: final torque-off request failed: {error}"
                ) from error
            self.bus.expected_torque = 0
            self.monitor.qualify_stationary(
                "before_torque",
                expected_torque=0,
                expected_goal=0,
                cold_start=True,
            )
            self.home_started = time.monotonic()
            self.reader.set_phase("homing")
            return

        record = self.reader.last_record
        if record is None:
            raise ReliefRefusal("homing: grouped position evidence was unavailable.")
        evidence = {
            **record,
            "phase": "homing_position",
            "sample_elapsed_s": record["elapsed_s"],
            "homing_displacement_mm": displacement_mm,
        }
        self.emit(evidence)
        if time.monotonic() - self.home_started >= self.lift.cfg.home_timeout_s:
            self.refuse(evidence, "homing: timed out during guarded telemetry.")
        if displacement_mm > 0.5 or int(record["present_velocity_raw"]) < -STILL_VELOCITY_RAW:
            self.refuse(evidence, "homing: unexpected upward direction.")
        if displacement_mm < -self.lift.cfg.soft_max_mm:
            self.refuse(evidence, "homing: maximum travel exceeded.")

    def _height_from_record(self, record: dict[str, Any]) -> float:
        position = int(record["present_position_raw"])
        return self.lift.get_height_mm(read_raw=lambda _register: position)

    def _moving_height(self, phase: str) -> tuple[dict[str, Any], float]:
        self.reader.set_phase(phase)
        height = self.lift.get_height_mm(read_raw=self.reader)
        record = self.reader.last_record
        if record is None:
            raise ReliefRefusal(f"{phase}: grouped position evidence was unavailable.")
        self.monitor.record(
            f"{phase}_height",
            sample_elapsed_s=record["elapsed_s"],
            present_position_raw=int(record["present_position_raw"]),
            height_mm=round(height, 4),
        )
        return record, height

    def _check_raised(self, phase: str, record: dict[str, Any], height: float) -> None:
        if height < -0.5 or (
            self.bus.expected_goal < 0
            and int(record["present_velocity_raw"]) > STILL_VELOCITY_RAW
        ):
            self.refuse(record, f"{phase}: unexpected downward direction.")
        if height > MAX_RELIEF_MM:
            self.refuse(record, f"{phase}: travel exceeded {MAX_RELIEF_MM} mm.")

    def compare(self) -> None:
        if set(self.robot.left_bus.motors) != {self.robot.lift.cfg.name}:
            raise ReliefRefusal("Installed lift comparison requires an ID-11-only bus.")
        self.monitor.read_configuration()
        baseline = self.monitor.qualify_stationary(
            "baseline", expected_torque=0, expected_goal=0, cold_start=True
        )
        authorization = self.input_fn(
            "Authorize ONE installed home, logical +200 upward relief to 10 mm, then "
            "45 seconds of raised rest. Type RELIEF to proceed: "
        )
        if authorization.strip() != "RELIEF":
            self.refuse(baseline, "Operator did not authorize RELIEF.")
        pre_motion = self.monitor.qualify_stationary(
            "pre_motion", expected_torque=0, expected_goal=0, cold_start=True
        )
        self.monitor.record(
            "setup_before",
            torque_enable=int(pre_motion["torque_enable"]),
            goal_velocity_raw=int(pre_motion["goal_velocity_raw"]),
            operating_mode=int(pre_motion["operating_mode"]),
            group_trace=pre_motion["group_trace"],
        )
        self.bus.expected_torque = int(pre_motion["torque_enable"])
        self.bus.expected_goal = int(pre_motion["goal_velocity_raw"])

        self.reader.set_phase("setup_position")
        result = self.lift.home(safety_check=self.home_guard, read_raw=self.reader)
        if time.monotonic() - self.home_started >= self.lift.cfg.home_timeout_s:
            raise ReliefRefusal("homing: timed out before guarded completion.")
        self.monitor.record(
            "home_complete",
            result=asdict(result),
            zero_reference="process-local, unchanged",
        )

        post_home = self.monitor.qualify_stationary(
            "post_home",
            expected_torque=1,
            expected_goal=0,
            allow_settling=True,
            timeout_s=grouped_feedback.SETTLE_TIMEOUT_S,
        )
        post_home_height = self._height_from_record(post_home)
        self.monitor.record("post_home_height", height_mm=round(post_home_height, 4))
        if abs(post_home_height) > 0.5:
            self.refuse(post_home, "post_home: process-local zero was not stationary at the stop.")

        self.reader.set_phase("relief_setup")
        self.lift.apply_action({"lift_axis.vel": 200}, read_raw=self.reader)
        started = time.monotonic()
        while True:
            remaining = RELIEF_TIMEOUT_S - (time.monotonic() - started)
            if remaining <= 0:
                raise ReliefRefusal(f"relief: target not reached within {RELIEF_TIMEOUT_S}s.")
            time.sleep(min(POLL_S, remaining))
            record, height = self._moving_height("relief")
            elapsed = time.monotonic() - started
            self._check_raised("relief", record, height)
            if elapsed >= RELIEF_TIMEOUT_S:
                self.refuse(record, f"relief: target not reached within {RELIEF_TIMEOUT_S}s.")
            if height >= RELIEF_MM:
                break
            if elapsed >= 2.0 and height < 0.5:
                self.refuse(record, "relief: no useful upward progress within 2 seconds.")
        self.lift.stop()

        stopped = self.monitor.qualify_stationary(
            "settle",
            expected_torque=1,
            expected_goal=0,
            allow_settling=True,
            timeout_s=grouped_feedback.SETTLE_TIMEOUT_S,
        )
        rest_height = self._height_from_record(stopped)
        self._check_raised("settle", stopped, rest_height)
        if rest_height < RELIEF_MM - 0.5:
            self.refuse(stopped, "settle: unexpected downward direction after relief stopped.")
        self.monitor.record("settle_height", height_mm=round(rest_height, 4))

        started = time.monotonic()
        high_current = 0

        def check_rest_current(record: dict[str, Any]) -> None:
            nonlocal high_current
            high_current = high_current + 1 if float(record["present_current_ma"]) >= 200 else 0
            if high_current >= 3:
                self.refuse(
                    record,
                    "rest: stationary current >=200 mA for three consecutive samples.",
                )

        while True:
            record = self.monitor.qualify_stationary(
                "rest",
                expected_torque=1,
                expected_goal=0,
                on_sample=check_rest_current,
            )
            height = self._height_from_record(record)
            self.monitor.record(
                "rest_height",
                sample_elapsed_s=record["elapsed_s"],
                present_position_raw=int(record["present_position_raw"]),
                height_mm=round(height, 4),
            )
            self._check_raised("rest", record, height)
            if abs(height - rest_height) > 0.5:
                self.refuse(record, "rest: unexpected stationary motion.")
            elapsed = time.monotonic() - started
            if elapsed >= REST_S:
                break

        rest_end = self.monitor.qualify_stationary(
            "rest_end",
            expected_torque=1,
            expected_goal=0,
            allow_settling=True,
            timeout_s=grouped_feedback.SETTLE_TIMEOUT_S,
        )
        final_height = self._height_from_record(rest_end)
        self._check_raised("rest_end", rest_end, final_height)
        if abs(final_height - rest_height) > 0.5:
            self.refuse(rest_end, "rest_end: raised position did not remain stable.")
        self.monitor.record("rest_complete", height_mm=round(final_height, 4), duration_s=REST_S)

    def cleanup_readback(self) -> None:
        # AlohaMini._safe_shutdown has already requested ID-11 zero, torque-off,
        # and Lock=0. End with Torque_Enable itself and prove grouped stopped state.
        self.bus.expected_goal = 0
        self.bus.expected_torque = 0
        write_register(self.bus, "Torque_Enable", self.lift.cfg.name, 0)
        self.monitor.qualify_stationary(
            "cleanup_readback",
            expected_torque=0,
            expected_goal=0,
            allow_settling=True,
            timeout_s=grouped_feedback.SETTLE_TIMEOUT_S,
        )
        self.lift.mark_unhomed()


def _run_lift_diagnostic(
    robot: AlohaMini,
    operation,
    *,
    label: str,
    success_message: str,
    check_factory=ReliefCheck,
) -> int:
    primary: BaseException | None = None
    check = None
    try:
        # Connect the owning bus only: ordinary robot.connect/configure would activate
        # before cold telemetry and overwrite the settings this comparison must read.
        # The diagnostic itself reads and validates the actual model/firmware below;
        # avoid a second firmware sweep by the ordinary multi-motor handshake.
        robot.left_bus.connect(handshake=False)
        check = check_factory(robot)
        operation(check)
    except BaseException as error:
        primary = error
    finally:
        # This entrypoint runs on the owning process's main thread. A second Ctrl+C
        # must not skip lift zero/torque-off after interrupting an earlier bus read.
        # The existing serial retries remain bounded; physical power removal remains
        # the operator's fallback if I/O itself becomes unresponsive.
        previous_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            try:
                errors = robot._safe_shutdown(
                    close_buses=True,
                    recover_interrupted_bus_io=primary is not None,
                    motor_shutdown_check=(check.cleanup_readback if check is not None else None),
                )
            except BaseException as error:
                errors = [f"shutdown also failed: {type(error).__name__}: {error}"]
        finally:
            signal.signal(signal.SIGINT, previous_sigint)
        if errors:
            if primary is None:
                primary = RuntimeError("Lift diagnostic cleanup failed.")
            for error in errors:
                primary.add_note(error)
    if primary is not None:
        reason = (
            "Operator interrupted the diagnostic."
            if isinstance(primary, KeyboardInterrupt)
            else str(primary)
        )
        print(f"{label}_REFUSED: {reason}", flush=True)
        for note in getattr(primary, "__notes__", ()):
            print(f"Cleanup detail: {note}", flush=True)
        print("Support the carriage safely; remove motor power. No automatic restart.", flush=True)
        if isinstance(primary, KeyboardInterrupt):
            return 130
        return 2 if isinstance(
            primary, (ReliefRefusal, grouped_feedback.ComparisonRefusal)
        ) else 1
    print(f"{label}_PASS: {success_message}", flush=True)
    return 0


def run_lift_relief(robot: AlohaMini) -> int:
    """Run once and always use the existing zero/torque-off/bus-close cleanup."""
    return _run_lift_diagnostic(
        robot,
        InstalledLiftCheck.compare,
        label="LIFT_RELIEF",
        success_message=(
            "bounded raised rest completed; zero/torque-off cleanup completed. Remove motor power."
        ),
        check_factory=InstalledLiftCheck,
    )


def run_lift_readback(robot: AlohaMini) -> int:
    """Run a short torque-off-only telemetry check and never activate the lift."""
    return _run_lift_diagnostic(
        robot,
        ReliefCheck.readback_only,
        label="LIFT_READBACK",
        success_message=(
            "short torque-off telemetry remained valid; cleanup completed. "
            "This does not authorize homing or relief. Remove motor power."
        ),
    )
