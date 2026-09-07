"""Exercise AM1 diagnostic scalar reads through the real Feetech SDK packet parser."""

from __future__ import annotations

from collections.abc import Iterable

import pytest
import scservo_sdk as scs

from lerobot.motors import Motor, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus
from lerobot.robots.alohamini import lift_relief


class ByteStreamPort:
    """Minimal serial byte stream consumed by the installed SDK's real read path."""

    def __init__(self, responses: Iterable[bytes] = (), *, pending: bytes = b"") -> None:
        self.responses = list(responses)
        self.incoming = bytearray(pending)
        self.writes: list[bytes] = []
        self.is_using = False
        self.is_open = True
        self.ser = self
        self.flush_calls = 0
        self.reset_input_calls = 0

    @property
    def in_waiting(self) -> int:
        return len(self.incoming)

    def flush(self) -> None:
        self.flush_calls += 1

    def reset_input_buffer(self) -> None:
        self.reset_input_calls += 1
        self.incoming.clear()

    def clearPort(self) -> None:  # noqa: N802 - SDK API
        self.flush()

    def writePort(self, packet) -> int:  # noqa: N802 - SDK API
        encoded = bytes(packet)
        self.writes.append(encoded)
        if self.responses:
            self.incoming.extend(self.responses.pop(0))
        return len(encoded)

    def read(self, length: int) -> bytes:
        chunk = bytes(self.incoming[:length])
        del self.incoming[:length]
        return chunk

    def readPort(self, length: int) -> bytes:  # noqa: N802 - SDK API
        return self.read(length)

    def setPacketTimeout(self, _packet_length: int) -> None:  # noqa: N802 - SDK API
        pass

    def isPacketTimeout(self) -> bool:  # noqa: N802 - SDK API
        return not self.incoming


def status(value: int, *, width: int = 1, motor_id: int = 11, error: int = 0) -> bytes:
    params = [(value >> (8 * index)) & 0xFF for index in range(width)]
    packet = [0xFF, 0xFF, motor_id, width + 2, error, *params, 0]
    packet[-1] = ~sum(packet[2:-1]) & 0xFF
    return bytes(packet)


def make_bus(port: ByteStreamPort) -> FeetechMotorsBus:
    bus = FeetechMotorsBus(
        port="fake-byte-stream",
        motors={"lift_axis": Motor(11, "sts3215", MotorNormMode.RANGE_M100_100)},
    )
    bus.port_handler = port
    return bus


def test_installed_sdk_clear_port_flushes_output_without_discarding_input() -> None:
    sdk_port = scs.PortHandler("fake-byte-stream")
    serial = ByteStreamPort(pending=status(93))
    sdk_port.ser = serial

    sdk_port.clearPort()

    assert serial.flush_calls == 1
    assert serial.in_waiting == len(status(93))


def test_installed_sdk_silently_slices_oversized_same_id_reply() -> None:
    port = ByteStreamPort([status(349, width=2)])
    bus = make_bus(port)

    value, comm, error = bus.packet_handler.read1ByteTxRx(port, 11, 63)

    assert (value, comm, error) == (93, scs.COMM_SUCCESS, 0)


def test_diagnostic_scalar_read_accepts_valid_temperature_with_raw_trace() -> None:
    port = ByteStreamPort([status(37)])
    bus = make_bus(port)

    value, trace = lift_relief.read_diagnostic_scalar(bus, "Present_Temperature", "lift_axis")

    assert value == 37
    assert trace["register"] == "Present_Temperature"
    assert trace["address"] == 63
    assert trace["requested_width"] == 1
    assert trace["request_bytes"] == [0xFF, 0xFF, 11, 4, scs.INST_READ, 63, 1, 0xAE]
    assert trace["request_length"] == 8
    assert trace["request_checksum_ok"] is True
    assert trace["response_bytes"] == list(status(37))
    assert trace["response_length"] == 7
    assert trace["response_id"] == 11
    assert trace["sdk_servo_error"] == 0
    assert trace["response_error"] == 0
    assert trace["checksum_ok"] is True
    assert trace["request_correlation"] == "id_length_checksum_only"


@pytest.mark.parametrize(
    ("reply", "expected_id", "checksum_ok"),
    [
        (bytes([*status(37)[:-1], status(37)[-1] ^ 0x01]), 11, False),
        (status(37, motor_id=12), 12, True),
    ],
    ids=["bad-checksum", "wrong-id"],
)
def test_diagnostic_scalar_read_rejects_bad_checksum_and_wrong_id(
    reply: bytes, expected_id: int, checksum_ok: bool
) -> None:
    port = ByteStreamPort([reply])
    bus = make_bus(port)

    with pytest.raises(lift_relief.DiagnosticReadError) as caught:
        lift_relief.read_diagnostic_scalar(bus, "Present_Temperature", "lift_axis")

    assert caught.value.trace["response_id"] == expected_id
    assert caught.value.trace["checksum_ok"] is checksum_ok
    assert caught.value.trace["comm_result"] != scs.COMM_SUCCESS


def test_diagnostic_scalar_read_rejects_same_id_reply_with_wrong_payload_length() -> None:
    reply = status(349, width=2)
    port = ByteStreamPort([reply])
    bus = make_bus(port)

    with pytest.raises(lift_relief.DiagnosticReadError, match="payload length") as caught:
        lift_relief.read_diagnostic_scalar(bus, "Present_Temperature", "lift_axis")

    assert caught.value.trace["response_id"] == 11
    assert caught.value.trace["response_payload_length"] == 2
    assert caught.value.trace["requested_width"] == 1


def test_diagnostic_scalar_read_preserves_undersized_same_id_reply() -> None:
    reply = status(93, width=1)
    port = ByteStreamPort([reply])
    bus = make_bus(port)

    with pytest.raises(lift_relief.DiagnosticReadError, match="payload length") as caught:
        lift_relief.read_diagnostic_scalar(bus, "Present_Position", "lift_axis")

    assert caught.value.trace["response_bytes"] == list(reply)
    assert caught.value.trace["response_id"] == 11
    assert caught.value.trace["response_payload_length"] == 1
    assert caught.value.trace["requested_width"] == 2


def test_same_id_same_length_reply_remains_protocol_ambiguous() -> None:
    port = ByteStreamPort([status(93)])
    bus = make_bus(port)

    value, trace = lift_relief.read_diagnostic_scalar(bus, "Present_Temperature", "lift_axis")

    assert value == 93
    assert trace["request_correlation"] == "id_length_checksum_only"
    assert "address" not in trace["response_fields"]


def test_corrupt_reply_keeps_sdk_and_packet_error_bytes_separate() -> None:
    reply = bytearray(status(37, error=scs.ERRBIT_OVERHEAT))
    reply[-1] ^= 0x01
    port = ByteStreamPort([bytes(reply)])
    bus = make_bus(port)

    with pytest.raises(lift_relief.DiagnosticReadError) as caught:
        lift_relief.read_diagnostic_scalar(bus, "Present_Temperature", "lift_axis")

    assert caught.value.trace["sdk_servo_error"] == 0
    assert caught.value.trace["response_error"] == scs.ERRBIT_OVERHEAT
    assert caught.value.trace["checksum_ok"] is False


def test_diagnostic_scalar_read_refuses_pending_reply_before_new_request() -> None:
    pending = status(93)
    port = ByteStreamPort([status(37)], pending=pending)
    bus = make_bus(port)

    with pytest.raises(lift_relief.DiagnosticReadError, match="pending input") as caught:
        lift_relief.read_diagnostic_scalar(bus, "Present_Temperature", "lift_axis")

    assert not port.writes
    assert port.reset_input_calls == 1
    assert caught.value.trace["pending_bytes"] == list(pending)
    assert caught.value.trace["pending_length"] == len(pending)


def test_diagnostic_scalar_timeout_is_not_retried_into_a_delayed_reply() -> None:
    port = ByteStreamPort([b"", status(93)])
    bus = make_bus(port)

    with pytest.raises(lift_relief.DiagnosticReadError, match="status packet") as caught:
        lift_relief.read_diagnostic_scalar(bus, "Present_Temperature", "lift_axis")

    assert len(port.writes) == 1
    assert caught.value.trace["response_bytes"] == []


def test_reply_arriving_after_timeout_is_refused_before_another_request() -> None:
    delayed = status(93)
    port = ByteStreamPort([b""])
    bus = make_bus(port)

    with pytest.raises(lift_relief.DiagnosticReadError):
        lift_relief.read_diagnostic_scalar(bus, "Present_Temperature", "lift_axis")

    port.incoming.extend(delayed)
    with pytest.raises(lift_relief.DiagnosticReadError, match="pending input") as caught:
        lift_relief.read_diagnostic_scalar(bus, "Present_Temperature", "lift_axis")

    assert len(port.writes) == 1
    assert caught.value.trace["pending_bytes"] == list(delayed)
    assert caught.value.trace["pending_length"] == len(delayed)


def test_genuine_high_temperature_is_returned_for_the_safety_layer_to_refuse() -> None:
    port = ByteStreamPort([status(93)])
    bus = make_bus(port)

    value, trace = lift_relief.read_diagnostic_scalar(bus, "Present_Temperature", "lift_axis")

    assert value == 93
    assert trace["checksum_ok"] is True
    assert trace["sdk_servo_error"] == 0
    assert trace["response_error"] == 0


def test_genuine_servo_fault_is_rejected_with_raw_trace() -> None:
    reply = status(37, error=scs.ERRBIT_OVERHEAT)
    port = ByteStreamPort([reply])
    bus = make_bus(port)

    with pytest.raises(lift_relief.DiagnosticReadError, match="Overheat") as caught:
        lift_relief.read_diagnostic_scalar(bus, "Present_Temperature", "lift_axis")

    assert caught.value.trace["response_bytes"] == list(reply)
    assert caught.value.trace["response_id"] == 11
    assert caught.value.trace["sdk_servo_error"] == scs.ERRBIT_OVERHEAT
    assert caught.value.trace["response_error"] == scs.ERRBIT_OVERHEAT
    assert caught.value.trace["checksum_ok"] is True


def test_sdk_read_exception_is_preserved_as_a_traced_diagnostic_refusal() -> None:
    class FailingReadPort(ByteStreamPort):
        def readPort(self, length: int) -> bytes:  # noqa: N802 - SDK API
            raise OSError(f"fake receive failure for {length} bytes")

    port = FailingReadPort([status(37)])
    bus = make_bus(port)

    with pytest.raises(lift_relief.DiagnosticReadError, match="fake receive failure") as caught:
        lift_relief.read_diagnostic_scalar(bus, "Present_Temperature", "lift_axis")

    assert isinstance(caught.value.__cause__, OSError)
    assert caught.value.trace["register"] == "Present_Temperature"
    assert caught.value.trace["requested_id"] == 11
    assert caught.value.trace["response_bytes"] == []
