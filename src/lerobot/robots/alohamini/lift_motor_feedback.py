"""Standalone, opt-in AM1 lift-motor feedback comparison.

This diagnostic talks directly to the installed Feetech SDK. It does not
construct ``AlohaMini``, use ``LiftAxis``, home the carriage, calculate a
platform height, open ZMQ, or touch calibration. The only nonzero command is
one operator-gated, bounded positive raw-velocity pulse to lift motor ID 11.
"""

from __future__ import annotations

import argparse
import json
import signal
import time
from collections.abc import Callable
from typing import Any, Protocol

from lerobot.utils.import_utils import require_package


MOTOR_ID = 11
BAUD_RATE = 1_000_000
PORT_ALIAS = "/dev/am_arm_follower_left"
PROTOCOL_VERSION = 0
MODEL_NUMBER = 777
BAUD_RATE_REGISTER_VALUE = 0

OPERATING_MODE_ADDRESS = 33
TORQUE_ENABLE_ADDRESS = 40
ACCELERATION_ADDRESS = 41
GOAL_VELOCITY_ADDRESS = 46
LOCK_ADDRESS = 55
PRESENT_POSITION_ADDRESS = 56
PRESENT_VELOCITY_ADDRESS = 58
PRESENT_LOAD_ADDRESS = 60
PRESENT_VOLTAGE_ADDRESS = 62
PRESENT_TEMPERATURE_ADDRESS = 63
STATUS_ADDRESS = 65
MOVING_ADDRESS = 66
PRESENT_CURRENT_ADDRESS = 69

# Three disjoint grouped reads deliberately skip Phase at address 18.
CONFIG_GROUPS = ((0, 18), (19, 21), (40, 47))
FEEDBACK_START = OPERATING_MODE_ADDRESS
FEEDBACK_LENGTH = PRESENT_CURRENT_ADDRESS + 2 - FEEDBACK_START

COOL_START_C = 40
ABORT_C = 55
CURRENT_ABORT_MA = 200.0
CURRENT_MA_PER_RAW = 6.5
STILL_VELOCITY_RAW = 5
STATIONARY_REPORTED_VELOCITY_LIMIT_RAW = 50
STATIONARY_POSITION_TOLERANCE_RAW = 1
STATIONARY_WINDOW_S = 0.15
STATIONARY_MIN_SAMPLES = 4
STATIONARY_TIMEOUT_S = 0.6
STATIONARY_PERSISTENT_SAMPLES = 3
MOTION_VELOCITY_RAW = 100
MOTION_S = 0.3
POLL_S = 0.05
REPLY_TIMEOUT_MS = 100.0
MAX_COMMAND_S = MOTION_S + POLL_S + REPLY_TIMEOUT_MS / 1000
SETTLE_TIMEOUT_S = 1.0
MIN_TRAVEL_RAW = 5
TARGET_TRAVEL_RAW = 64
MAX_TRAVEL_RAW = 128


class ComparisonRefusal(RuntimeError):
    """Expected safety refusal; the utility never retries or restarts."""


class TransportRefusal(ComparisonRefusal):
    """Vendor transaction failed validation and retained bounded byte evidence."""

    def __init__(self, message: str, trace: dict[str, Any]):
        super().__init__(message)
        self.trace = trace


class FeedbackTransport(Protocol):
    motor_id: int

    def open(self) -> None: ...

    def close(self) -> None: ...

    def read_group(
        self, start: int, length: int, *, timeout_ms: float = REPLY_TIMEOUT_MS
    ) -> tuple[bytes, dict[str, Any]]: ...

    def write_register(self, address: int, width: int, value: int) -> dict[str, Any]: ...


def _checksum_ok(packet: bytes) -> bool | None:
    if len(packet) < 6:
        return None
    return packet[-1] == (~sum(packet[2:-1]) & 0xFF)


def _reply_details(response: bytes) -> dict[str, Any]:
    declared_length = response[3] if len(response) >= 4 else None
    expected_length = declared_length + 4 if declared_length is not None else None
    complete = expected_length == len(response)
    return {
        "response_id": response[2] if len(response) >= 3 else None,
        "response_declared_length": declared_length,
        "response_payload_length": declared_length - 2 if declared_length is not None else None,
        "response_error": response[4] if len(response) >= 5 else None,
        "response_complete": complete,
        "checksum_ok": _checksum_ok(response) if complete else None,
    }


class DirectSdkTransport:
    """One-owner Feetech Protocol-0 transport using the vendor sync-read path."""

    def __init__(
        self,
        port: str,
        *,
        motor_id: int = MOTOR_ID,
        baud_rate: int = BAUD_RATE,
        sdk_module: Any | None = None,
        port_handler: Any | None = None,
        packet_handler: Any | None = None,
    ) -> None:
        if sdk_module is None:
            require_package("feetech-servo-sdk", extra="feetech", import_name="scservo_sdk")
            import scservo_sdk as sdk_module

        self.sdk = sdk_module
        self.port_name = port
        self.motor_id = motor_id
        self.baud_rate = baud_rate
        self.port_handler = port_handler or self.sdk.PortHandler(port)
        self.packet_handler = packet_handler or self.sdk.PacketHandler(PROTOCOL_VERSION)
        self.opened = bool(getattr(self.port_handler, "is_open", False))

    def open(self) -> None:
        if self.opened:
            return
        opened = False
        try:
            opened = bool(self.port_handler.setBaudRate(self.baud_rate))
        finally:
            self.opened = bool(getattr(self.port_handler, "is_open", False))
        if not opened:
            raise TransportRefusal(
                f"Unable to open {self.port_name} at {self.baud_rate} baud.",
                {"phase": "open", "port": self.port_name, "baud_rate": self.baud_rate},
            )
        self.opened = True

    def close(self) -> None:
        if self.opened:
            self.port_handler.closePort()
            self.opened = False

    def _prepare_input(self, *, operation: str, address: int, width: int) -> None:
        serial_port = getattr(self.port_handler, "ser", None)
        if serial_port is None or not hasattr(serial_port, "reset_input_buffer"):
            raise TransportRefusal(
                "Receive-buffer control is unavailable.",
                {"operation": operation, "address": address, "requested_width": width},
            )
        if getattr(self.port_handler, "is_using", False):
            raise TransportRefusal(
                "Serial port is already in use.",
                {"operation": operation, "address": address, "requested_width": width},
            )
        pending_length = int(getattr(serial_port, "in_waiting", 0))
        if pending_length:
            reader = getattr(serial_port, "read", None) or self.port_handler.readPort
            pending = bytes(reader(min(pending_length, 250)))
            serial_port.reset_input_buffer()
            raise TransportRefusal(
                "Unexpected pending input before request.",
                {
                    "operation": operation,
                    "address": address,
                    "requested_width": width,
                    "pending_bytes": list(pending),
                    "pending_length": pending_length,
                },
            )
        serial_port.reset_input_buffer()

    def _capture(self, operation: Callable[[], tuple[Any, int | None, int | None]]) -> tuple[
        Any, int | None, int | None, bytes, bytes, float, BaseException | None
    ]:
        request = bytearray()
        response = bytearray()
        original_write = self.port_handler.writePort
        original_read = self.port_handler.readPort

        def capture_write(packet):
            request.extend(bytes(packet))
            return original_write(packet)

        def capture_read(length: int):
            chunk = original_read(length)
            response.extend(bytes(chunk))
            return chunk

        self.port_handler.writePort = capture_write
        self.port_handler.readPort = capture_read
        started_ns = time.monotonic_ns()
        value: Any = None
        comm: int | None = None
        error: int | None = None
        caught: BaseException | None = None
        try:
            value, comm, error = operation()
        except BaseException as exception:
            caught = exception
        finally:
            self.port_handler.writePort = original_write
            self.port_handler.readPort = original_read
        elapsed_ms = (time.monotonic_ns() - started_ns) / 1e6
        return value, comm, error, bytes(request), bytes(response), elapsed_ms, caught

    def read_group(
        self, start: int, length: int, *, timeout_ms: float = REPLY_TIMEOUT_MS
    ) -> tuple[bytes, dict[str, Any]]:
        self._prepare_input(operation="sync_read", address=start, width=length)

        def operation() -> tuple[list[int], int, int]:
            comm = self.packet_handler.syncReadTx(
                self.port_handler, start, length, [self.motor_id], 1
            )
            if comm != self.sdk.COMM_SUCCESS:
                return [], comm, 0
            # Override the SDK's byte-count timeout with one explicit bounded
            # diagnostic deadline. This does not retry a missing response.
            self.port_handler.setPacketTimeoutMillis(timeout_ms)
            return self.packet_handler.readRx(self.port_handler, self.motor_id, length)

        data, comm, error, request, response, elapsed_ms, caught = self._capture(operation)
        details = _reply_details(response)
        expected_request = bytearray(
            [0xFF, 0xFF, self.sdk.BROADCAST_ID, 5, self.sdk.INST_SYNC_READ, start, length, self.motor_id, 0]
        )
        expected_request[-1] = ~sum(expected_request[2:-1]) & 0xFF
        trace = {
            "operation": "sync_read",
            "start_address": start,
            "requested_width": length,
            "requested_id": self.motor_id,
            "request_bytes": list(request),
            "request_valid": request == bytes(expected_request),
            "request_checksum_ok": _checksum_ok(request),
            "response_bytes": list(response),
            "response_length": len(response),
            "sdk_servo_error": error,
            "comm_result": comm,
            "elapsed_ms": round(elapsed_ms, 3),
            "reply_timeout_ms": timeout_ms,
            "request_correlation": "single_group_reply_without_address_or_sequence",
            **details,
        }
        if caught is not None:
            self.port_handler.is_using = False
            if isinstance(caught, KeyboardInterrupt):
                raise caught
            raise TransportRefusal(
                f"SDK grouped read raised {type(caught).__name__}: {caught}", trace
            ) from caught
        if comm != self.sdk.COMM_SUCCESS:
            raise TransportRefusal(self.packet_handler.getTxRxResult(comm), trace)
        if error != 0:
            raise TransportRefusal(self.packet_handler.getRxPacketError(error), trace)
        if trace["request_valid"] is not True:
            raise TransportRefusal("Grouped-read request frame was not the expected frame.", trace)
        if trace["response_id"] != self.motor_id:
            raise TransportRefusal("Grouped-read response ID did not match motor ID 11.", trace)
        if trace["response_complete"] is not True or trace["response_payload_length"] != length:
            raise TransportRefusal("Grouped-read response length did not match the request.", trace)
        if trace["checksum_ok"] is not True:
            raise TransportRefusal("Grouped-read response checksum failed.", trace)
        payload = bytes(data)
        if len(payload) != length or payload != response[5:-1]:
            raise TransportRefusal("SDK grouped payload did not match the captured response.", trace)
        return payload, trace

    def write_register(self, address: int, width: int, value: int) -> dict[str, Any]:
        if address not in (GOAL_VELOCITY_ADDRESS, TORQUE_ENABLE_ADDRESS):
            raise ValueError(f"Diagnostic write address {address} is not allowed.")
        if (address, width) not in ((GOAL_VELOCITY_ADDRESS, 2), (TORQUE_ENABLE_ADDRESS, 1)):
            raise ValueError(f"Unexpected diagnostic write width {width} at address {address}.")
        allowed_values = (
            (0, 1) if address == TORQUE_ENABLE_ADDRESS else (0, MOTION_VELOCITY_RAW)
        )
        if value not in allowed_values:
            raise ValueError(f"Diagnostic write value {value} is not allowed at address {address}.")
        self._prepare_input(operation="write", address=address, width=width)

        def operation() -> tuple[None, int, int]:
            writer = (
                self.packet_handler.write1ByteTxRx
                if width == 1
                else self.packet_handler.write2ByteTxRx
            )
            comm, error = writer(self.port_handler, self.motor_id, address, value)
            return None, comm, error

        _, comm, error, request, response, elapsed_ms, caught = self._capture(operation)
        details = _reply_details(response)
        trace = {
            "operation": "write",
            "address": address,
            "width": width,
            "value": value,
            "request_bytes": list(request),
            "request_checksum_ok": _checksum_ok(request),
            "response_bytes": list(response),
            "response_length": len(response),
            "sdk_servo_error": error,
            "comm_result": comm,
            "elapsed_ms": round(elapsed_ms, 3),
            **details,
        }
        if caught is not None:
            self.port_handler.is_using = False
            if isinstance(caught, KeyboardInterrupt):
                raise caught
            raise TransportRefusal(
                f"SDK write raised {type(caught).__name__}: {caught}", trace
            ) from caught
        if comm != self.sdk.COMM_SUCCESS:
            raise TransportRefusal(self.packet_handler.getTxRxResult(comm), trace)
        if error != 0:
            raise TransportRefusal(self.packet_handler.getRxPacketError(error), trace)
        if trace["response_id"] != self.motor_id or trace["response_payload_length"] != 0:
            raise TransportRefusal("Write acknowledgement shape did not match motor ID 11.", trace)
        if trace["response_complete"] is not True or trace["checksum_ok"] is not True:
            raise TransportRefusal("Write acknowledgement checksum or length failed.", trace)
        return trace


def _word(payload: bytes, address: int, start: int) -> int:
    offset = address - start
    return payload[offset] | (payload[offset + 1] << 8)


def _decode_sign_magnitude(value: int, sign_bit: int) -> int:
    sign = 1 << sign_bit
    magnitude = value & (sign - 1)
    return -magnitude if value & sign else magnitude


def decode_feedback(payload: bytes) -> dict[str, int | float]:
    if len(payload) != FEEDBACK_LENGTH:
        raise ComparisonRefusal(
            f"Grouped feedback had {len(payload)} bytes; expected {FEEDBACK_LENGTH}."
        )
    return {
        "operating_mode": payload[OPERATING_MODE_ADDRESS - FEEDBACK_START],
        "torque_enable": payload[TORQUE_ENABLE_ADDRESS - FEEDBACK_START],
        "acceleration_raw": payload[ACCELERATION_ADDRESS - FEEDBACK_START],
        "goal_velocity_raw": _decode_sign_magnitude(
            _word(payload, GOAL_VELOCITY_ADDRESS, FEEDBACK_START), 15
        ),
        "lock": payload[LOCK_ADDRESS - FEEDBACK_START],
        "present_position_raw": _decode_sign_magnitude(
            _word(payload, PRESENT_POSITION_ADDRESS, FEEDBACK_START), 15
        ),
        "present_velocity_raw": _decode_sign_magnitude(
            _word(payload, PRESENT_VELOCITY_ADDRESS, FEEDBACK_START), 15
        ),
        "present_load_raw": _decode_sign_magnitude(
            _word(payload, PRESENT_LOAD_ADDRESS, FEEDBACK_START), 10
        ),
        "voltage_raw": payload[PRESENT_VOLTAGE_ADDRESS - FEEDBACK_START],
        "voltage_v": payload[PRESENT_VOLTAGE_ADDRESS - FEEDBACK_START] / 10,
        "temperature_c": payload[PRESENT_TEMPERATURE_ADDRESS - FEEDBACK_START],
        "status": payload[STATUS_ADDRESS - FEEDBACK_START],
        "moving": payload[MOVING_ADDRESS - FEEDBACK_START],
        "present_current_raw": _word(payload, PRESENT_CURRENT_ADDRESS, FEEDBACK_START),
        "present_current_ma": _word(payload, PRESENT_CURRENT_ADDRESS, FEEDBACK_START)
        * CURRENT_MA_PER_RAW,
    }


def _decode_configuration(groups: dict[int, bytes]) -> dict[str, int | str]:
    low = groups[0]
    middle = groups[19]
    high = groups[40]
    return {
        "firmware": f"{low[0]}.{low[1]}",
        "model_number": _word(low, 3, 0),
        "id": low[5],
        "baud_rate_register": low[6],
        "max_temperature_limit_c": low[13],
        "max_voltage_limit_raw": low[14],
        "min_voltage_limit_raw": low[15],
        "unloading_condition": middle[0],
        "operating_mode": middle[OPERATING_MODE_ADDRESS - 19],
        "velocity_p": middle[37 - 19],
        "over_current_protection_time": middle[38 - 19],
        "velocity_i": middle[39 - 19],
        "torque_enable": high[TORQUE_ENABLE_ADDRESS - 40],
        "acceleration": high[ACCELERATION_ADDRESS - 40],
        "goal_velocity_raw": _decode_sign_magnitude(_word(high, GOAL_VELOCITY_ADDRESS, 40), 15),
        "torque_limit": _word(high, 48, 40),
        "lock": high[LOCK_ADDRESS - 40],
        "maximum_velocity_limit": high[84 - 40],
        "maximum_acceleration": high[85 - 40],
        "acceleration_multiplier": high[86 - 40],
    }


def _position_delta(start: int, current: int) -> int:
    return (current - start + 2048) % 4096 - 2048


def _default_emit(record: dict[str, Any]) -> None:
    print("[LIFT MOTOR FEEDBACK] " + json.dumps(record, separators=(",", ":")), flush=True)


class MotorFeedbackComparison:
    def __init__(
        self,
        transport: FeedbackTransport,
        *,
        input_fn: Callable[[str], str],
        monotonic: Callable[[], float],
        sleep: Callable[[float], None],
        emit: Callable[[dict[str, Any]], None],
    ) -> None:
        self.transport = transport
        self.input_fn = input_fn
        self.monotonic = monotonic
        self.sleep = sleep
        self.emit = emit
        self.started = monotonic()
        self.config: dict[str, Any] = {}
        self.opened = False

    def record(self, phase: str, **values: Any) -> dict[str, Any]:
        record = {"phase": phase, "elapsed_s": round(self.monotonic() - self.started, 3), **values}
        self.emit(record)
        return record

    def refuse(self, record: dict[str, Any], reason: str) -> None:
        rejected = {**record, "rejected": True, "rejection_reason": reason}
        self.emit(rejected)
        raise ComparisonRefusal(reason)

    def read_configuration(self) -> None:
        groups: dict[int, bytes] = {}
        traces = []
        for start, length in CONFIG_GROUPS:
            payload, trace = self.transport.read_group(start, length)
            groups[start] = payload
            traces.append(trace)
        self.config = _decode_configuration(groups)
        record = self.record(
            "configuration",
            registers_raw=self.config,
            group_ranges=[{"start": start, "length": length} for start, length in CONFIG_GROUPS],
            group_traces=traces,
            phase_address_18_read=False,
        )
        if self.config["model_number"] != MODEL_NUMBER:
            self.refuse(record, f"Expected STS3215 model {MODEL_NUMBER}.")
        if self.config["id"] != MOTOR_ID or self.transport.motor_id != MOTOR_ID:
            self.refuse(record, "Expected only lift motor ID 11.")
        if self.config["baud_rate_register"] != BAUD_RATE_REGISTER_VALUE:
            self.refuse(record, "Lift motor baud-rate register is not the established 1,000,000-baud value.")
        if self.config["operating_mode"] != 1:
            self.refuse(record, "Lift motor is not already in velocity mode 1.")
        if not ABORT_C <= self.config["max_temperature_limit_c"] <= 100:
            self.refuse(record, "Configured temperature limit is unusable for this comparison.")
        if not 0 < self.config["min_voltage_limit_raw"] < self.config["max_voltage_limit_raw"]:
            self.refuse(record, "Configured voltage limits are invalid.")

    def sample(
        self,
        phase: str,
        *,
        expected_torque: int,
        expected_goal: int,
        cold_start: bool = False,
        start_position: int | None = None,
        timeout_ms: float = REPLY_TIMEOUT_MS,
    ) -> dict[str, Any]:
        try:
            payload, trace = self.transport.read_group(
                FEEDBACK_START, FEEDBACK_LENGTH, timeout_ms=timeout_ms
            )
            values = decode_feedback(payload)
        except TransportRefusal as error:
            record = {
                "phase": phase,
                "elapsed_s": round(self.monotonic() - self.started, 3),
                "group_trace": error.trace,
            }
            self.refuse(record, f"{phase}: grouped feedback failed: {error}")
        record = {
            "phase": phase,
            "elapsed_s": round(self.monotonic() - self.started, 3),
            **values,
            "group_trace": trace,
        }
        if start_position is not None:
            record["position_delta_raw"] = _position_delta(
                start_position, int(record["present_position_raw"])
            )
        ceiling = min(ABORT_C, int(self.config["max_temperature_limit_c"]))
        if int(record["temperature_c"]) >= ceiling:
            self.refuse(
                record,
                f"{phase}: temperature {record['temperature_c']} C reached diagnostic ceiling {ceiling} C.",
            )
        if cold_start and int(record["temperature_c"]) > COOL_START_C:
            self.refuse(record, f"{phase}: starting temperature exceeds {COOL_START_C} C.")
        if not (
            int(self.config["min_voltage_limit_raw"])
            <= int(record["voltage_raw"])
            <= int(self.config["max_voltage_limit_raw"])
        ):
            self.refuse(record, f"{phase}: voltage is outside the motor's configured limits.")
        if int(record["status"]) != 0:
            self.refuse(record, f"{phase}: servo status is nonzero.")
        if int(record["operating_mode"]) != 1:
            self.refuse(record, f"{phase}: operating mode changed from 1.")
        if int(record["torque_enable"]) != expected_torque:
            self.refuse(record, f"{phase}: torque readback did not match {expected_torque}.")
        if int(record["goal_velocity_raw"]) != expected_goal:
            self.refuse(record, f"{phase}: goal velocity did not match {expected_goal}.")
        if float(record["present_current_ma"]) >= CURRENT_ABORT_MA:
            self.refuse(record, f"{phase}: current reached the {CURRENT_ABORT_MA:g} mA diagnostic ceiling.")
        self.emit(record)
        return record

    def qualify_stationary(
        self,
        phase: str,
        *,
        expected_torque: int,
        expected_goal: int,
        cold_start: bool = False,
        start_position: int | None = None,
        max_position_delta_raw: int | None = None,
        allow_settling: bool = False,
        timeout_s: float = STATIONARY_TIMEOUT_S,
    ) -> dict[str, Any]:
        """Require one bounded window of fresh, mutually consistent feedback."""
        qualification_started = self.monotonic()
        deadline = qualification_started + timeout_s
        candidate: list[tuple[float, dict[str, Any]]] = []
        last_record: dict[str, Any] | None = None

        def reject_or_reset(record: dict[str, Any], reason: str) -> None:
            nonlocal candidate
            if allow_settling:
                candidate = []
                return
            self.refuse(record, reason)

        while True:
            if last_record is not None:
                now = self.monotonic()
                if now >= deadline:
                    self.refuse(
                        last_record,
                        f"{phase}: no complete stationary window within {timeout_s:g} second.",
                    )
                self.sleep(min(POLL_S, deadline - now))

            record = self.sample(
                phase,
                expected_torque=expected_torque,
                expected_goal=expected_goal,
                cold_start=cold_start,
                start_position=start_position,
            )
            sampled_at = self.monotonic()
            last_record = record

            if sampled_at > deadline:
                self.refuse(
                    record,
                    f"{phase}: stationary feedback exceeded the {timeout_s:g}-second qualification limit.",
                )
            if max_position_delta_raw is not None and abs(
                int(record["position_delta_raw"])
            ) > max_position_delta_raw:
                self.refuse(
                    record,
                    f"{phase}: travel exceeded {max_position_delta_raw} raw ticks.",
                )

            velocity = int(record["present_velocity_raw"])
            moving = int(record["moving"])
            if moving not in (0, 1):
                self.refuse(record, f"{phase}: Moving readback was not 0 or 1.")
            if abs(velocity) > STATIONARY_REPORTED_VELOCITY_LIMIT_RAW:
                reject_or_reset(
                    record,
                    f"{phase}: reported velocity exceeded the stationary evidence bound "
                    f"of {STATIONARY_REPORTED_VELOCITY_LIMIT_RAW} raw units.",
                )
                continue

            proposed = [*candidate, (sampled_at, record)]
            origin = int(proposed[0][1]["present_position_raw"])
            offsets = [
                _position_delta(origin, int(sample["present_position_raw"]))
                for _, sample in proposed
            ]
            excursion = max(offsets) - min(offsets)
            drift = offsets[-1]
            if (
                excursion > STATIONARY_POSITION_TOLERANCE_RAW
                or abs(drift) > STATIONARY_POSITION_TOLERANCE_RAW
            ):
                reject_or_reset(
                    record,
                    f"{phase}: position excursion or drift exceeded the "
                    f"{STATIONARY_POSITION_TOLERANCE_RAW}-tick stationary tolerance.",
                )
                if allow_settling:
                    candidate = [(sampled_at, record)]
                continue

            candidate = proposed
            if len(candidate) >= STATIONARY_PERSISTENT_SAMPLES:
                recent = [sample for _, sample in candidate[-STATIONARY_PERSISTENT_SAMPLES:]]
                velocities = [int(sample["present_velocity_raw"]) for sample in recent]
                moving_without_velocity = all(
                    int(sample["moving"]) == 1
                    and abs(int(sample["present_velocity_raw"])) <= STILL_VELOCITY_RAW
                    for sample in recent
                )
                same_positive = all(value > STILL_VELOCITY_RAW for value in velocities)
                same_negative = all(value < -STILL_VELOCITY_RAW for value in velocities)
                if moving_without_velocity or same_positive or same_negative:
                    reject_or_reset(
                        record,
                        f"{phase}: velocity or Moving reported persistent motion during "
                        "the stationary window.",
                    )
                    if allow_settling:
                        candidate = [(sampled_at, record)]
                    continue

            window_s = candidate[-1][0] - candidate[0][0]
            if len(candidate) < STATIONARY_MIN_SAMPLES or window_s < STATIONARY_WINDOW_S:
                continue

            evidence = {
                "sample_count": len(candidate),
                "window_s": round(window_s, 3),
                "position_resolution_raw": 4096,
                "position_tolerance_raw": STATIONARY_POSITION_TOLERANCE_RAW,
                "position_origin_raw": origin,
                "position_offsets_raw": offsets,
                "position_excursion_raw": excursion,
                "position_drift_raw": drift,
                "present_velocity_raw": [
                    int(sample["present_velocity_raw"]) for _, sample in candidate
                ],
                "moving": [int(sample["moving"]) for _, sample in candidate],
                "expected_torque": expected_torque,
                "expected_goal_velocity_raw": expected_goal,
            }
            self.record(f"{phase}_stationary_qualified", **evidence)
            return {**candidate[-1][1], "stationary_qualification": evidence}

    def write(self, phase: str, address: int, width: int, value: int) -> None:
        try:
            trace = self.transport.write_register(address, width, value)
        except TransportRefusal as error:
            self.refuse(
                {
                    "phase": phase,
                    "elapsed_s": round(self.monotonic() - self.started, 3),
                    "write_trace": error.trace,
                },
                f"{phase}: write failed: {error}",
            )
        self.record(phase, address=address, width=width, value=value, write_trace=trace)

    def compare(self) -> None:
        self.read_configuration()
        baseline = self.qualify_stationary(
            "baseline", expected_torque=0, expected_goal=0, cold_start=True
        )
        authorization = self.input_fn(
            "Type ROTATE to authorize one raw +100 pulse with a nominal 0.3-second "
            "software timing envelope (the power disconnect is the hard stop): "
        )
        if authorization != "ROTATE":
            self.refuse(baseline, "Exact ROTATE authorization was not supplied.")
        pre_motion = self.qualify_stationary(
            "pre_motion", expected_torque=0, expected_goal=0, cold_start=True
        )
        start_position = int(pre_motion["present_position_raw"])

        self.write("zero_before_torque", GOAL_VELOCITY_ADDRESS, 2, 0)
        self.write("torque_enable", TORQUE_ENABLE_ADDRESS, 1, 1)
        self.qualify_stationary("armed_zero", expected_torque=1, expected_goal=0)

        motion_started = self.monotonic()
        self.write("motion_command", GOAL_VELOCITY_ADDRESS, 2, MOTION_VELOCITY_RAW)
        early_motion_samples: list[dict[str, Any]] = []
        motion_error: BaseException | None = None
        stop_early = False
        try:
            # The main thread remains the only SDK/serial owner. Two early
            # reads have explicit 40 ms SDK deadlines; any anomaly skips the
            # remaining wait and reaches the zero request immediately.
            for _ in range(2):
                if self.monotonic() - motion_started >= MOTION_S - 0.1:
                    break
                self.sleep(POLL_S)
                motion_sample = self.sample(
                    "motion",
                    expected_torque=1,
                    expected_goal=MOTION_VELOCITY_RAW,
                    start_position=start_position,
                    timeout_ms=40.0,
                )
                early_motion_samples.append(motion_sample)
                velocity = int(motion_sample["present_velocity_raw"])
                delta = int(motion_sample["position_delta_raw"])
                if velocity < -STILL_VELOCITY_RAW:
                    self.refuse(
                        motion_sample,
                        "motion: velocity changed opposite the positive raw command.",
                    )
                if delta < -2:
                    self.refuse(
                        motion_sample,
                        "motion: position changed opposite the positive raw command.",
                    )
                if delta > MAX_TRAVEL_RAW:
                    self.refuse(motion_sample, f"motion: travel exceeded {MAX_TRAVEL_RAW} raw ticks.")
                if delta >= TARGET_TRAVEL_RAW:
                    stop_early = True
                    break
            if not stop_early:
                remaining = MOTION_S - (self.monotonic() - motion_started)
                if remaining > 0:
                    self.sleep(remaining)
        except BaseException as error:
            motion_error = error

        try:
            self.write("motion_zero", GOAL_VELOCITY_ADDRESS, 2, 0)
        except BaseException as zero_error:
            if motion_error is None:
                motion_error = zero_error
            else:
                motion_error.add_note(
                    f"motion_zero also failed: {type(zero_error).__name__}: {zero_error}"
                )
        command_duration = self.monotonic() - motion_started
        self.record(
            "motion_bound",
            command_duration_s=round(command_duration, 3),
            nominal_limit_s=MOTION_S,
            bounded_sdk_deadline_s=MAX_COMMAND_S,
            observed_stop_target_raw=TARGET_TRAVEL_RAW,
            refusal_travel_raw=MAX_TRAVEL_RAW,
            single_serial_owner=True,
        )
        if command_duration > MAX_COMMAND_S:
            bound_error = ComparisonRefusal(
                f"motion: zero command exceeded the {MAX_COMMAND_S:g}-second SDK timing envelope."
            )
            if motion_error is None:
                motion_error = bound_error
            else:
                motion_error.add_note(str(bound_error))
        if motion_error is not None:
            raise motion_error

        endpoint = self.qualify_stationary(
            "stopped",
            expected_torque=1,
            expected_goal=0,
            start_position=start_position,
            max_position_delta_raw=MAX_TRAVEL_RAW,
            allow_settling=True,
            timeout_s=SETTLE_TIMEOUT_S,
        )
        endpoint_delta = int(endpoint["position_delta_raw"])
        self.record(
            "motion_endpoint",
            start_position_raw=start_position,
            early_samples=[
                {
                    "sample_elapsed_s": sample["elapsed_s"],
                    "present_position_raw": int(sample["present_position_raw"]),
                    "position_delta_raw": int(sample["position_delta_raw"]),
                }
                for sample in early_motion_samples
            ],
            sample_elapsed_s=endpoint["elapsed_s"],
            present_position_raw=int(endpoint["present_position_raw"]),
            position_delta_raw=endpoint_delta,
        )
        if endpoint_delta < -2:
            self.refuse(
                endpoint,
                "motion endpoint: position changed opposite the positive raw command.",
            )
        if endpoint_delta < MIN_TRAVEL_RAW:
            self.refuse(
                endpoint,
                f"motion: travel did not reach {MIN_TRAVEL_RAW} raw ticks.",
            )

    def cleanup(self) -> list[str]:
        errors: list[str] = []
        if not self.opened:
            return errors
        for phase, address, width, value in (
            ("cleanup_zero", GOAL_VELOCITY_ADDRESS, 2, 0),
            ("cleanup_torque_off", TORQUE_ENABLE_ADDRESS, 1, 0),
        ):
            try:
                trace = self.transport.write_register(address, width, value)
                self.record(phase, address=address, width=width, value=value, write_trace=trace)
            except BaseException as error:
                errors.append(f"{phase}: {type(error).__name__}: {error}")
        try:
            self.qualify_stationary(
                "cleanup_readback",
                expected_torque=0,
                expected_goal=0,
                allow_settling=True,
                timeout_s=SETTLE_TIMEOUT_S,
            )
        except BaseException as error:
            errors.append(f"cleanup_readback: {type(error).__name__}: {error}")
        try:
            self.transport.close()
            self.opened = False
        except BaseException as error:
            errors.append(f"close: {type(error).__name__}: {error}")
        return errors


def run_comparison(
    transport: FeedbackTransport,
    *,
    input_fn: Callable[[str], str] = input,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    emit: Callable[[dict[str, Any]], None] = _default_emit,
) -> int:
    comparison = MotorFeedbackComparison(
        transport,
        input_fn=input_fn,
        monotonic=monotonic,
        sleep=sleep,
        emit=emit,
    )
    primary: BaseException | None = None
    try:
        try:
            transport.open()
        finally:
            comparison.opened = bool(getattr(transport, "opened", False))
        comparison.compare()
    except BaseException as error:
        primary = error
    previous_sigint = None
    cleanup_errors: list[str] = []
    try:
        try:
            previous_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
        except ValueError:
            pass
        try:
            cleanup_errors = comparison.cleanup()
        except BaseException as error:
            cleanup_errors = [f"cleanup: {type(error).__name__}: {error}"]
    finally:
        if previous_sigint is not None:
            signal.signal(signal.SIGINT, previous_sigint)

    if primary is None and cleanup_errors:
        primary = RuntimeError(cleanup_errors[0])
    if primary is not None:
        for detail in cleanup_errors:
            primary.add_note(detail)
        status = "interrupted" if isinstance(primary, KeyboardInterrupt) else "refused"
        comparison.record(
            "result",
            status=status,
            reason=str(primary),
            cleanup_errors=cleanup_errors,
            no_automatic_restart=True,
        )
        if isinstance(primary, KeyboardInterrupt):
            return 130
        return 2 if isinstance(primary, ComparisonRefusal) else 1
    comparison.record(
        "result",
        status="pass",
        reason="bounded pulse and verified stopped cleanup completed",
        cleanup_errors=[],
        no_automatic_restart=True,
    )
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run one standalone, operator-gated AM1 lift-motor feedback comparison; "
            "no homing, platform-height control, ZMQ, cameras, or calibration."
        )
    )
    parser.add_argument("--port", default="/dev/am_arm_follower_left")
    parser.add_argument("--motor-id", type=int, default=MOTOR_ID)
    parser.add_argument("--baud-rate", type=int, default=BAUD_RATE)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    if args.motor_id != MOTOR_ID:
        parser.error("this comparison is fixed to AM1 lift motor ID 11")
    if args.baud_rate != BAUD_RATE:
        parser.error("this comparison is fixed to the established 1,000,000 baud")
    if args.port != PORT_ALIAS:
        parser.error(f"this comparison is fixed to the left follower/body alias {PORT_ALIAS}")
    print(
        "Standalone AM1 lift-motor comparison: WCH USB serial interface 1a86:55d3, "
        f"{args.port}, STS3215 ID {args.motor_id}, {args.baud_rate} baud.\n"
        "Keep the platform mechanically unloaded, the servo securely mounted, and power removal accessible.",
        flush=True,
    )
    transport = DirectSdkTransport(args.port, motor_id=args.motor_id, baud_rate=args.baud_rate)
    result = run_comparison(transport)
    if result == 0:
        print("LIFT_MOTOR_FEEDBACK_PASS", flush=True)
    else:
        print("LIFT_MOTOR_FEEDBACK_REFUSED", flush=True)
        print("Remove motor power; do not retry automatically.", flush=True)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
