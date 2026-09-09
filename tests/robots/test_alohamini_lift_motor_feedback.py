"""Focused tests for the standalone AM1 lift-motor feedback comparison."""

from __future__ import annotations

import threading
from collections.abc import Iterable

import pytest
import scservo_sdk as scs

from lerobot.robots.alohamini import lift_motor_feedback as feedback


class ByteStreamPort:
    """Serial byte stream consumed by the installed vendor SDK packet parser."""

    def __init__(self, responses: Iterable[bytes] = (), *, pending: bytes = b"") -> None:
        self.responses = list(responses)
        self.incoming = bytearray(pending)
        self.writes: list[bytes] = []
        self.is_using = False
        self.is_open = True
        self.ser = self

    @property
    def in_waiting(self) -> int:
        return len(self.incoming)

    def flush(self) -> None:
        pass

    def reset_input_buffer(self) -> None:
        self.incoming.clear()

    def clearPort(self) -> None:  # noqa: N802 - vendor SDK API
        self.flush()

    def writePort(self, packet) -> int:  # noqa: N802 - vendor SDK API
        encoded = bytes(packet)
        self.writes.append(encoded)
        if self.responses:
            self.incoming.extend(self.responses.pop(0))
        return len(encoded)

    def readPort(self, length: int) -> bytes:  # noqa: N802 - vendor SDK API
        chunk = bytes(self.incoming[:length])
        del self.incoming[:length]
        return chunk

    def setPacketTimeout(self, _packet_length: int) -> None:  # noqa: N802 - vendor SDK API
        pass

    def setPacketTimeoutMillis(self, _milliseconds: float) -> None:  # noqa: N802 - vendor SDK API
        pass

    def isPacketTimeout(self) -> bool:  # noqa: N802 - vendor SDK API
        return not self.incoming


def status(payload: bytes, *, motor_id: int = 11, error: int = 0) -> bytes:
    packet = [0xFF, 0xFF, motor_id, len(payload) + 2, error, *payload, 0]
    packet[-1] = ~sum(packet[2:-1]) & 0xFF
    return bytes(packet)


def set_word(payload: bytearray, offset: int, value: int) -> None:
    payload[offset] = value & 0xFF
    payload[offset + 1] = (value >> 8) & 0xFF


def encode_sign_magnitude(value: int, sign_bit: int = 15) -> int:
    return abs(value) | ((1 << sign_bit) if value < 0 else 0)


def dynamic_payload(
    *,
    torque: int = 0,
    goal_velocity: int = 0,
    present_position: int = 1000,
    present_velocity: int = 0,
    temperature: int = 38,
    voltage: int = 118,
    current: int = 1,
    status_value: int = 0,
    moving: int | None = None,
) -> bytes:
    payload = bytearray(feedback.FEEDBACK_LENGTH)
    payload[feedback.OPERATING_MODE_ADDRESS - feedback.FEEDBACK_START] = 1
    payload[feedback.TORQUE_ENABLE_ADDRESS - feedback.FEEDBACK_START] = torque
    payload[feedback.ACCELERATION_ADDRESS - feedback.FEEDBACK_START] = 0
    set_word(
        payload,
        feedback.GOAL_VELOCITY_ADDRESS - feedback.FEEDBACK_START,
        encode_sign_magnitude(goal_velocity),
    )
    payload[feedback.LOCK_ADDRESS - feedback.FEEDBACK_START] = 0
    set_word(payload, feedback.PRESENT_POSITION_ADDRESS - feedback.FEEDBACK_START, present_position)
    set_word(
        payload,
        feedback.PRESENT_VELOCITY_ADDRESS - feedback.FEEDBACK_START,
        encode_sign_magnitude(present_velocity),
    )
    set_word(payload, feedback.PRESENT_LOAD_ADDRESS - feedback.FEEDBACK_START, 0)
    payload[feedback.PRESENT_VOLTAGE_ADDRESS - feedback.FEEDBACK_START] = voltage
    payload[feedback.PRESENT_TEMPERATURE_ADDRESS - feedback.FEEDBACK_START] = temperature
    payload[feedback.STATUS_ADDRESS - feedback.FEEDBACK_START] = status_value
    payload[feedback.MOVING_ADDRESS - feedback.FEEDBACK_START] = (
        int(abs(present_velocity) > 5) if moving is None else moving
    )
    set_word(payload, feedback.PRESENT_CURRENT_ADDRESS - feedback.FEEDBACK_START, current)
    return bytes(payload)


def test_vendor_group_reader_uses_one_sync_read_and_preserves_consistent_reply() -> None:
    payload = dynamic_payload(
        torque=1,
        goal_velocity=100,
        present_position=1012,
        present_velocity=90,
        temperature=77,
        current=1,
    )
    port = ByteStreamPort([status(payload)])
    transport = feedback.DirectSdkTransport(
        "fake-byte-stream",
        port_handler=port,
        packet_handler=scs.PacketHandler(0),
    )

    returned, trace = transport.read_group(feedback.FEEDBACK_START, feedback.FEEDBACK_LENGTH)
    sample = feedback.decode_feedback(returned)

    assert returned == payload
    assert sample["torque_enable"] == 1
    assert sample["goal_velocity_raw"] == 100
    assert sample["present_position_raw"] == 1012
    assert sample["present_velocity_raw"] == 90
    assert sample["temperature_c"] == 77
    assert sample["present_current_ma"] == 6.5
    assert port.writes == [bytes([0xFF, 0xFF, 0xFE, 5, scs.INST_SYNC_READ, 33, 38, 11, 0x28])]
    assert trace["response_bytes"] == list(status(payload))
    assert trace["response_payload_length"] == feedback.FEEDBACK_LENGTH
    assert trace["checksum_ok"] is True
    assert trace["request_correlation"] == "single_group_reply_without_address_or_sequence"


def test_vendor_group_reader_rejects_a_checksum_fault_without_retry() -> None:
    reply = bytearray(status(dynamic_payload()))
    reply[-1] ^= 1
    port = ByteStreamPort([bytes(reply)])
    transport = feedback.DirectSdkTransport(
        "fake-byte-stream",
        port_handler=port,
        packet_handler=scs.PacketHandler(0),
    )

    try:
        transport.read_group(feedback.FEEDBACK_START, feedback.FEEDBACK_LENGTH)
    except feedback.TransportRefusal as error:
        assert error.trace["checksum_ok"] is False
        assert error.trace["response_bytes"] == list(reply)
    else:
        raise AssertionError("checksum fault was accepted")

    assert len(port.writes) == 1


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeTransport:
    motor_id = feedback.MOTOR_ID

    def __init__(
        self,
        *,
        hot_during_motion: bool = False,
        reverse_velocity: bool = False,
        motion_read_delay_s: float = 0.0,
        clock: FakeClock | None = None,
    ) -> None:
        self.opened = False
        self.closed = False
        self.torque = 0
        self.goal_velocity = 0
        self.position = 1000
        self.temperature = 38
        self.hot_during_motion = hot_during_motion
        self.reverse_velocity = reverse_velocity
        self.motion_read_delay_s = motion_read_delay_s
        self.clock = clock
        self.operation_threads: list[int] = []
        self.group_reads: list[tuple[int, int]] = []
        self.writes: list[tuple[int, int, int]] = []
        self.write_times: list[tuple[float, int, int]] = []
        self.motion_read_count = 0

    def open(self) -> None:
        self.operation_threads.append(threading.get_ident())
        self.opened = True

    def close(self) -> None:
        self.operation_threads.append(threading.get_ident())
        self.closed = True

    def read_group(
        self, start: int, length: int, *, timeout_ms: float = feedback.REPLY_TIMEOUT_MS
    ) -> tuple[bytes, dict]:
        self.operation_threads.append(threading.get_ident())
        self.group_reads.append((start, length))
        if (start, length) == (0, 18):
            payload = bytearray(length)
            payload[0] = 3
            payload[1] = 10
            set_word(payload, 3, 777)
            payload[5] = feedback.MOTOR_ID
            payload[6] = 0
            payload[13] = 70
            payload[14] = 140
            payload[15] = 40
        elif (start, length) == (19, 21):
            payload = bytearray(length)
            payload[0] = 44
            payload[14] = 1
            payload[18] = 10
            payload[19] = 200
            payload[20] = 200
        elif (start, length) == (40, 47):
            payload = bytearray(length)
            payload[0] = self.torque
            payload[1] = 0
            set_word(payload, 6, self.goal_velocity)
            payload[15] = 0
            set_word(payload, 16, self.position)
            set_word(payload, 18, self.goal_velocity)
            payload[22] = 118
            payload[23] = self.temperature
            payload[25] = 0
            set_word(payload, 29, 1)
            payload[44] = 65
            payload[45] = 254
            payload[46] = 1
        elif (start, length) == (feedback.FEEDBACK_START, feedback.FEEDBACK_LENGTH):
            if self.goal_velocity:
                self.motion_read_count += 1
                if self.clock is not None:
                    self.clock.sleep(self.motion_read_delay_s)
                self.position += 8
                if self.hot_during_motion:
                    self.temperature = 77
            payload = bytearray(
                dynamic_payload(
                    torque=self.torque,
                    goal_velocity=self.goal_velocity,
                    present_position=self.position,
                    present_velocity=-50 if self.reverse_velocity and self.goal_velocity else self.goal_velocity,
                    temperature=self.temperature,
                    current=1,
                )
            )
        else:
            raise AssertionError(f"unexpected group read {(start, length)}")
        trace = {
            "start_address": start,
            "requested_width": length,
            "response_bytes": [0xFF, 0xFF, feedback.MOTOR_ID, length + 2, 0, *payload, 0],
            "checksum_ok": True,
            "request_correlation": "single_group_reply_without_address_or_sequence",
            "reply_timeout_ms": timeout_ms,
        }
        return bytes(payload), trace

    def write_register(self, address: int, width: int, value: int) -> dict:
        self.operation_threads.append(threading.get_ident())
        self.writes.append((address, width, value))
        self.write_times.append((self.clock.now if self.clock is not None else 0.0, address, value))
        if address == feedback.TORQUE_ENABLE_ADDRESS:
            self.torque = value
        elif address == feedback.GOAL_VELOCITY_ADDRESS:
            self.goal_velocity = value
        else:
            raise AssertionError(f"unexpected write address {address}")
        return {"address": address, "width": width, "value": value, "checksum_ok": True}


class TimeBasedMotionTransport(FakeTransport):
    """Fake whose position follows elapsed command time, not feedback reads."""

    def __init__(
        self,
        *,
        clock: FakeClock,
        travel_rate_raw_per_s: float,
        reported_motion_velocity_raw: int | None = None,
    ) -> None:
        super().__init__(clock=clock)
        self.travel_rate_raw_per_s = travel_rate_raw_per_s
        self.reported_motion_velocity_raw = reported_motion_velocity_raw
        self.motion_started_at: float | None = None
        self.motion_origin = self.position

    def _advance_to_now(self) -> None:
        if self.goal_velocity == 0 or self.motion_started_at is None:
            return
        elapsed = self.clock.now - self.motion_started_at
        self.position = (self.motion_origin + int(elapsed * self.travel_rate_raw_per_s)) % 4096

    def read_group(
        self, start: int, length: int, *, timeout_ms: float = feedback.REPLY_TIMEOUT_MS
    ) -> tuple[bytes, dict]:
        if (start, length) != (feedback.FEEDBACK_START, feedback.FEEDBACK_LENGTH):
            return super().read_group(start, length, timeout_ms=timeout_ms)

        self.operation_threads.append(threading.get_ident())
        self.group_reads.append((start, length))
        self._advance_to_now()
        if self.goal_velocity:
            self.motion_read_count += 1
        present_velocity = self.reported_motion_velocity_raw
        if present_velocity is None:
            present_velocity = self.goal_velocity if self.travel_rate_raw_per_s >= 0 else -self.goal_velocity
        payload = dynamic_payload(
            torque=self.torque,
            goal_velocity=self.goal_velocity,
            present_position=self.position,
            present_velocity=present_velocity if self.goal_velocity else 0,
            temperature=self.temperature,
            current=1,
        )
        trace = {
            "start_address": start,
            "requested_width": length,
            "response_bytes": [0xFF, 0xFF, feedback.MOTOR_ID, length + 2, 0, *payload, 0],
            "checksum_ok": True,
            "request_correlation": "single_group_reply_without_address_or_sequence",
            "reply_timeout_ms": timeout_ms,
        }
        return payload, trace

    def write_register(self, address: int, width: int, value: int) -> dict:
        if address == feedback.GOAL_VELOCITY_ADDRESS:
            self._advance_to_now()
            if value != 0:
                self.motion_origin = self.position
                self.motion_started_at = self.clock.now
        return super().write_register(address, width, value)


class ScriptedFeedbackTransport(FakeTransport):
    def __init__(self, *, clock: FakeClock, samples: Iterable[dict] = ()) -> None:
        super().__init__(clock=clock)
        self.samples = list(samples)

    def read_group(
        self, start: int, length: int, *, timeout_ms: float = feedback.REPLY_TIMEOUT_MS
    ) -> tuple[bytes, dict]:
        if (start, length) != (feedback.FEEDBACK_START, feedback.FEEDBACK_LENGTH) or not self.samples:
            return super().read_group(start, length, timeout_ms=timeout_ms)

        self.operation_threads.append(threading.get_ident())
        self.group_reads.append((start, length))
        sample = self.samples.pop(0)
        if "at_s" in sample:
            self.clock.now = float(sample["at_s"])
        if error := sample.get("error"):
            raise error
        payload = sample.get("payload")
        if payload is None:
            payload = dynamic_payload(
                torque=int(sample.get("torque", self.torque)),
                goal_velocity=int(sample.get("goal_velocity", self.goal_velocity)),
                present_position=int(sample.get("present_position", self.position)),
                present_velocity=int(sample.get("present_velocity", 0)),
                temperature=int(sample.get("temperature", self.temperature)),
                voltage=int(sample.get("voltage", 118)),
                current=int(sample.get("current", 1)),
                status_value=int(sample.get("status", 0)),
                moving=sample.get("moving"),
            )
        trace = {
            "start_address": start,
            "requested_width": length,
            "response_bytes": [0xFF, 0xFF, feedback.MOTOR_ID, length + 2, 0, *payload, 0],
            "checksum_ok": True,
            "request_correlation": "single_group_reply_without_address_or_sequence",
            "reply_timeout_ms": timeout_ms,
        }
        return bytes(payload), trace


def make_comparison(
    transport: FakeTransport,
    clock: FakeClock,
    records: list[dict],
) -> feedback.MotorFeedbackComparison:
    comparison = feedback.MotorFeedbackComparison(
        transport,
        input_fn=lambda _prompt: "ROTATE",
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        emit=records.append,
    )
    comparison.config = {
        "max_temperature_limit_c": 70,
        "min_voltage_limit_raw": 40,
        "max_voltage_limit_raw": 140,
    }
    comparison.opened = True
    return comparison


CAPTURED_RUN_1_BASELINE_REPLY = bytes(
    [
        255, 255, 11, 40, 0, 1, 20, 200, 80, 10, 200, 200, 0, 0, 0, 0, 0, 0, 0, 0,
        232, 3, 0, 0, 0, 0, 0, 0, 235, 1, 0, 0, 0, 0, 118, 37, 0, 0, 0, 0, 0, 0, 0,
        147,
    ]
)
CAPTURED_RUN_1_PRE_MOTION_REPLY = bytes(
    [
        255, 255, 11, 40, 0, 1, 20, 200, 80, 10, 200, 200, 0, 0, 0, 0, 0, 0, 0, 0,
        232, 3, 0, 0, 0, 0, 0, 0, 236, 1, 50, 0, 0, 0, 118, 37, 0, 0, 1, 0, 0, 0, 0,
        95,
    ]
)
CAPTURED_RUN_1_CLEANUP_REPLY = bytes(
    [
        255, 255, 11, 40, 0, 1, 20, 200, 80, 10, 200, 200, 0, 0, 0, 0, 0, 0, 0, 0,
        232, 3, 0, 0, 0, 0, 0, 0, 235, 1, 50, 128, 0, 0, 118, 37, 0, 0, 1, 0, 0, 0,
        0, 224,
    ]
)
CAPTURED_RUN_2_BASELINE_REPLY = bytes(
    [
        255, 255, 11, 40, 0, 1, 20, 200, 80, 10, 200, 200, 0, 0, 0, 0, 0, 0, 0, 0,
        232, 3, 0, 0, 0, 0, 0, 0, 235, 1, 50, 128, 0, 0, 118, 37, 0, 0, 0, 0, 0, 0,
        0, 225,
    ]
)
CAPTURED_RUN_2_CLEANUP_REPLY = bytes(
    [
        255, 255, 11, 40, 0, 1, 20, 200, 80, 10, 200, 200, 0, 0, 0, 0, 0, 0, 0, 0,
        232, 3, 0, 0, 0, 0, 0, 0, 236, 1, 0, 0, 0, 0, 118, 37, 0, 0, 0, 0, 0, 0, 0,
        146,
    ]
)


def captured_payload(reply: bytes) -> bytes:
    assert feedback._checksum_ok(reply) is True
    return reply[5:-1]


def test_captured_sparse_payloads_reproduce_the_single_sample_refusal_boundary() -> None:
    # These timestamps and payloads remain separate sparse phase snapshots. They
    # are deliberately not treated as a continuous stationary observation.
    run_1 = [
        (0.006, CAPTURED_RUN_1_BASELINE_REPLY),
        (6.973, CAPTURED_RUN_1_PRE_MOTION_REPLY),
        (6.977, CAPTURED_RUN_1_CLEANUP_REPLY),
    ]
    run_2 = [
        (0.007, CAPTURED_RUN_2_BASELINE_REPLY),
        (0.011, CAPTURED_RUN_2_CLEANUP_REPLY),
    ]

    decoded_1 = [(at_s, feedback.decode_feedback(captured_payload(reply))) for at_s, reply in run_1]
    decoded_2 = [(at_s, feedback.decode_feedback(captured_payload(reply))) for at_s, reply in run_2]

    decoded_motion_1 = [
        (at_s, sample["present_position_raw"], sample["present_velocity_raw"])
        for at_s, sample in decoded_1
    ]
    decoded_motion_2 = [
        (at_s, sample["present_position_raw"], sample["present_velocity_raw"])
        for at_s, sample in decoded_2
    ]
    assert decoded_motion_1 == [
        (0.006, 491, 0),
        (6.973, 492, 50),
        (6.977, 491, -50),
    ]
    assert decoded_motion_2 == [
        (0.007, 491, -50),
        (0.011, 492, 0),
    ]
    assert all(
        sample["torque_enable"] == 0
        and sample["goal_velocity_raw"] == 0
        and sample["present_current_raw"] == 0
        and sample["temperature_c"] == 37
        and sample["voltage_v"] == 11.8
        and sample["status"] == 0
        for _, sample in [*decoded_1, *decoded_2]
    )
    assert abs(decoded_1[1][1]["present_velocity_raw"]) > feedback.STILL_VELOCITY_RAW
    assert abs(decoded_1[2][1]["present_velocity_raw"]) > feedback.STILL_VELOCITY_RAW
    assert abs(decoded_2[0][1]["present_velocity_raw"]) > feedback.STILL_VELOCITY_RAW


def test_fresh_stationary_window_accepts_adjacent_count_jitter_and_observed_speeds() -> None:
    clock = FakeClock()
    records: list[dict] = []
    transport = ScriptedFeedbackTransport(
        clock=clock,
        samples=[
            {"present_position": 491, "present_velocity": 0, "moving": 0},
            {"present_position": 492, "present_velocity": 50, "moving": 1},
            {"present_position": 491, "present_velocity": -50, "moving": 1},
            {"present_position": 492, "present_velocity": 0, "moving": 0},
        ],
    )
    comparison = make_comparison(transport, clock, records)

    qualified = comparison.qualify_stationary(
        "pre_motion", expected_torque=0, expected_goal=0, cold_start=True
    )

    assert qualified["stationary_qualification"]["sample_count"] == 4
    assert qualified["stationary_qualification"]["position_excursion_raw"] == 1
    assert qualified["stationary_qualification"]["position_drift_raw"] == 1
    assert qualified["stationary_qualification"]["present_velocity_raw"] == [0, 50, -50, 0]
    assert qualified["stationary_qualification"]["moving"] == [0, 1, 1, 0]


def test_sparse_captured_phase_samples_cannot_form_a_fresh_stationary_window() -> None:
    clock = FakeClock()
    records: list[dict] = []
    transport = ScriptedFeedbackTransport(
        clock=clock,
        samples=[
            {"at_s": 0.006, "payload": captured_payload(CAPTURED_RUN_1_BASELINE_REPLY)},
            {"at_s": 6.973, "payload": captured_payload(CAPTURED_RUN_1_PRE_MOTION_REPLY)},
        ],
    )
    comparison = make_comparison(transport, clock, records)

    with pytest.raises(feedback.ComparisonRefusal, match="qualification limit"):
        comparison.qualify_stationary(
            "captured_sparse", expected_torque=0, expected_goal=0, cold_start=True
        )

    rejected = [record for record in records if record.get("rejected")]
    assert rejected[-1]["elapsed_s"] == 6.973
    assert rejected[-1]["present_velocity_raw"] == 50


@pytest.mark.parametrize(
    "positions",
    ([1000, 1001, 1002, 1003], [1000, 999, 998, 997]),
    ids=("forward", "reverse"),
)
def test_stationary_window_rejects_accumulated_one_tick_drift(positions: list[int]) -> None:
    clock = FakeClock()
    records: list[dict] = []
    transport = ScriptedFeedbackTransport(
        clock=clock,
        samples=[{"present_position": position} for position in positions],
    )
    comparison = make_comparison(transport, clock, records)

    with pytest.raises(feedback.ComparisonRefusal, match="position excursion or drift"):
        comparison.qualify_stationary("drift", expected_torque=0, expected_goal=0)


def test_settling_window_cannot_pass_continuous_one_tick_creep() -> None:
    clock = FakeClock()
    records: list[dict] = []
    transport = ScriptedFeedbackTransport(
        clock=clock,
        samples=[{"present_position": 1000 + offset} for offset in range(8)],
    )
    comparison = make_comparison(transport, clock, records)

    with pytest.raises(feedback.ComparisonRefusal, match="no complete stationary window"):
        comparison.qualify_stationary(
            "settling_creep",
            expected_torque=0,
            expected_goal=0,
            allow_settling=True,
            timeout_s=0.3,
        )


def test_stationary_window_handles_encoder_wrap_without_false_drift() -> None:
    clock = FakeClock()
    records: list[dict] = []
    transport = ScriptedFeedbackTransport(
        clock=clock,
        samples=[
            {"present_position": 4095, "present_velocity": 0, "moving": 0},
            {"present_position": 0, "present_velocity": 50, "moving": 1},
            {"present_position": 4095, "present_velocity": -50, "moving": 1},
            {"present_position": 0, "present_velocity": 0, "moving": 0},
        ],
    )
    comparison = make_comparison(transport, clock, records)

    qualified = comparison.qualify_stationary("wrap", expected_torque=0, expected_goal=0)

    assert qualified["stationary_qualification"]["position_offsets_raw"] == [0, 1, 0, 1]
    assert qualified["stationary_qualification"]["position_excursion_raw"] == 1


@pytest.mark.parametrize(
    ("samples", "reason"),
    (
        ([{"present_velocity": 51, "moving": 1}], "reported velocity exceeded"),
        (
            [
                {"present_velocity": 50, "moving": 1},
                {"present_velocity": 50, "moving": 1},
                {"present_velocity": 50, "moving": 1},
            ],
            "persistent motion",
        ),
        (
            [
                {"present_velocity": 0, "moving": 1},
                {"present_velocity": 0, "moving": 1},
                {"present_velocity": 0, "moving": 1},
            ],
            "persistent motion",
        ),
    ),
    ids=("large_velocity", "persistent_velocity", "persistent_moving_flag"),
)
def test_stationary_window_rejects_material_speed_position_contradictions(
    samples: list[dict], reason: str
) -> None:
    clock = FakeClock()
    records: list[dict] = []
    transport = ScriptedFeedbackTransport(clock=clock, samples=samples)
    comparison = make_comparison(transport, clock, records)

    with pytest.raises(feedback.ComparisonRefusal, match=reason):
        comparison.qualify_stationary("contradiction", expected_torque=0, expected_goal=0)


@pytest.mark.parametrize(
    ("sample", "reason"),
    (
        ({"torque": 1}, "torque readback did not match 0"),
        ({"goal_velocity": 50}, "goal velocity did not match 0"),
    ),
    ids=("wrong_torque", "nonzero_goal"),
)
def test_every_stationary_sample_requires_expected_torque_and_zero_goal(
    sample: dict, reason: str
) -> None:
    clock = FakeClock()
    records: list[dict] = []
    transport = ScriptedFeedbackTransport(clock=clock, samples=[sample])
    comparison = make_comparison(transport, clock, records)

    with pytest.raises(feedback.ComparisonRefusal, match=reason):
        comparison.qualify_stationary("state", expected_torque=0, expected_goal=0)


@pytest.mark.parametrize(
    ("sample", "reason"),
    (
        (
            {"error": feedback.TransportRefusal("missing feedback", {"operation": "sync_read"})},
            "grouped feedback failed",
        ),
        ({"temperature": 55}, "temperature 55 C"),
        ({"status": 1}, "servo status is nonzero"),
    ),
    ids=("missing", "thermal_fault", "servo_fault"),
)
def test_stationary_window_refuses_missing_or_faulted_feedback(sample: dict, reason: str) -> None:
    clock = FakeClock()
    records: list[dict] = []
    transport = ScriptedFeedbackTransport(clock=clock, samples=[sample])
    comparison = make_comparison(transport, clock, records)

    with pytest.raises(feedback.ComparisonRefusal, match=reason):
        comparison.qualify_stationary("fault", expected_torque=0, expected_goal=0)


def test_cleanup_zeroes_and_disables_torque_before_qualifying_adjacent_jitter() -> None:
    clock = FakeClock()
    records: list[dict] = []
    transport = ScriptedFeedbackTransport(
        clock=clock,
        samples=[
            {"present_position": 491, "present_velocity": -50, "moving": 1},
            {"present_position": 492, "present_velocity": 50, "moving": 1},
            {"present_position": 491, "present_velocity": -50, "moving": 1},
            {"present_position": 492, "present_velocity": 0, "moving": 0},
        ],
    )
    comparison = make_comparison(transport, clock, records)

    errors = comparison.cleanup()

    assert errors == []
    assert transport.writes[:2] == [
        (feedback.GOAL_VELOCITY_ADDRESS, 2, 0),
        (feedback.TORQUE_ENABLE_ADDRESS, 1, 0),
    ]
    assert transport.closed
    assert any(record["phase"] == "cleanup_readback_stationary_qualified" for record in records)


def test_cleanup_failure_does_not_replace_the_primary_refusal() -> None:
    class CleanupReadFailureTransport(FakeTransport):
        fail_feedback = False

        def read_group(
            self, start: int, length: int, *, timeout_ms: float = feedback.REPLY_TIMEOUT_MS
        ) -> tuple[bytes, dict]:
            if self.fail_feedback and (start, length) == (
                feedback.FEEDBACK_START,
                feedback.FEEDBACK_LENGTH,
            ):
                raise feedback.TransportRefusal("cleanup feedback missing", {"operation": "sync_read"})
            return super().read_group(start, length, timeout_ms=timeout_ms)

    clock = FakeClock()
    records: list[dict] = []
    transport = CleanupReadFailureTransport(clock=clock)

    def reject_authorization(_prompt: str) -> str:
        transport.fail_feedback = True
        return "rotate"

    result = feedback.run_comparison(
        transport,
        input_fn=reject_authorization,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        emit=records.append,
    )

    assert result == 2
    assert records[-1]["reason"] == "Exact ROTATE authorization was not supplied."
    assert records[-1]["cleanup_errors"]
    assert "cleanup feedback missing" in records[-1]["cleanup_errors"][0]


def test_pre_motion_must_finish_the_full_stationary_window_before_activation() -> None:
    clock = FakeClock()
    records: list[dict] = []
    transport = ScriptedFeedbackTransport(clock=clock)

    def authorize(_prompt: str) -> str:
        transport.samples = [
            {"present_position": 1000},
            {"present_position": 1000},
            {"present_position": 1000},
            {"present_position": 1000, "goal_velocity": 50},
        ]
        return "ROTATE"

    result = feedback.run_comparison(
        transport,
        input_fn=authorize,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        emit=records.append,
    )

    assert result == 2
    assert not any(
        (address == feedback.TORQUE_ENABLE_ADDRESS and value == 1)
        or (address == feedback.GOAL_VELOCITY_ADDRESS and value == feedback.MOTION_VELOCITY_RAW)
        for address, _, value in transport.writes
    )
    assert "goal velocity did not match 0" in records[-1]["reason"]


def test_comparison_is_operator_gated_bounded_and_never_reads_phase() -> None:
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    records: list[dict] = []

    result = feedback.run_comparison(
        transport,
        input_fn=lambda _prompt: "ROTATE",
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        emit=records.append,
    )

    assert result == 0
    assert transport.closed
    assert all(not (start <= 18 < start + length) for start, length in transport.group_reads)
    assert transport.group_reads[:3] == [(0, 18), (19, 21), (40, 47)]
    assert set(address for address, _, _ in transport.writes) == {
        feedback.TORQUE_ENABLE_ADDRESS,
        feedback.GOAL_VELOCITY_ADDRESS,
    }
    assert [value for address, _, value in transport.writes if address == feedback.GOAL_VELOCITY_ADDRESS].count(
        feedback.MOTION_VELOCITY_RAW
    ) == 1
    assert transport.goal_velocity == 0
    assert transport.torque == 0
    assert clock.now <= 2.0
    motion = [record for record in records if record.get("phase") == "motion"]
    assert motion
    assert 5 <= motion[-1]["position_delta_raw"] <= feedback.MAX_TRAVEL_RAW
    assert records[-1]["phase"] == "result"
    assert records[-1]["status"] == "pass"
    motion_index = next(
        index
        for index, (_, address, value) in enumerate(transport.write_times)
        if address == feedback.GOAL_VELOCITY_ADDRESS and value == feedback.MOTION_VELOCITY_RAW
    )
    motion_started = transport.write_times[motion_index][0]
    first_zero = next(
        timestamp
        for timestamp, address, value in transport.write_times[motion_index + 1 :]
        if address == feedback.GOAL_VELOCITY_ADDRESS and value == 0
    )
    assert first_zero - motion_started < 0.5
    assert set(transport.operation_threads) == {threading.get_ident()}


def test_minimum_travel_uses_a_fresh_endpoint_after_the_scheduled_zero() -> None:
    clock = FakeClock()
    transport = TimeBasedMotionTransport(clock=clock, travel_rate_raw_per_s=25.0)
    records: list[dict] = []

    result = feedback.run_comparison(
        transport,
        input_fn=lambda _prompt: "ROTATE",
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        emit=records.append,
    )

    early = [record for record in records if record.get("phase") == "motion"]
    assert early
    assert all(record["position_delta_raw"] < feedback.MIN_TRAVEL_RAW for record in early)
    assert result == 0
    endpoint = next(record for record in records if record.get("phase") == "motion_endpoint")
    assert endpoint["position_delta_raw"] >= feedback.MIN_TRAVEL_RAW
    assert endpoint["sample_elapsed_s"] > early[-1]["elapsed_s"]
    assert endpoint["early_samples"] == [
        {
            "sample_elapsed_s": sample["elapsed_s"],
            "present_position_raw": sample["present_position_raw"],
            "position_delta_raw": sample["position_delta_raw"],
        }
        for sample in early
    ]


def test_fresh_endpoint_displacement_is_signed_and_wrap_aware() -> None:
    clock = FakeClock()
    transport = TimeBasedMotionTransport(clock=clock, travel_rate_raw_per_s=25.0)
    transport.position = 4093
    records: list[dict] = []

    result = feedback.run_comparison(
        transport,
        input_fn=lambda _prompt: "ROTATE",
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        emit=records.append,
    )

    assert result == 0
    endpoint = next(record for record in records if record.get("phase") == "motion_endpoint")
    assert endpoint["start_position_raw"] == 4093
    assert endpoint["present_position_raw"] < endpoint["start_position_raw"]
    assert endpoint["position_delta_raw"] >= feedback.MIN_TRAVEL_RAW


def test_fresh_endpoint_still_refuses_genuinely_insufficient_travel() -> None:
    clock = FakeClock()
    transport = TimeBasedMotionTransport(clock=clock, travel_rate_raw_per_s=10.0)
    records: list[dict] = []

    result = feedback.run_comparison(
        transport,
        input_fn=lambda _prompt: "ROTATE",
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        emit=records.append,
    )

    assert result == 2
    endpoint = next(record for record in records if record.get("phase") == "motion_endpoint")
    assert 0 < endpoint["position_delta_raw"] < feedback.MIN_TRAVEL_RAW
    assert f"did not reach {feedback.MIN_TRAVEL_RAW}" in records[-1]["reason"]


def test_fresh_endpoint_refuses_opposite_total_travel_not_seen_in_early_samples() -> None:
    clock = FakeClock()
    transport = TimeBasedMotionTransport(
        clock=clock,
        travel_rate_raw_per_s=-25.0,
        reported_motion_velocity_raw=0,
    )
    records: list[dict] = []

    result = feedback.run_comparison(
        transport,
        input_fn=lambda _prompt: "ROTATE",
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        emit=records.append,
    )

    early = [record for record in records if record.get("phase") == "motion"]
    assert early[-1]["position_delta_raw"] == -2
    assert result == 2
    assert "motion endpoint: position changed opposite" in records[-1]["reason"]


def test_fresh_endpoint_refuses_total_travel_over_the_existing_maximum() -> None:
    clock = FakeClock()
    transport = TimeBasedMotionTransport(clock=clock, travel_rate_raw_per_s=450.0)
    records: list[dict] = []

    result = feedback.run_comparison(
        transport,
        input_fn=lambda _prompt: "ROTATE",
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        emit=records.append,
    )

    assert result == 2
    assert transport.goal_velocity == 0
    assert f"travel exceeded {feedback.MAX_TRAVEL_RAW}" in records[-1]["reason"]


@pytest.mark.parametrize("endpoint_fault", ("missing", "thermal"))
def test_missing_or_faulted_fresh_endpoint_refuses_after_zero(endpoint_fault: str) -> None:
    class EndpointFaultTransport(TimeBasedMotionTransport):
        fault_emitted = False

        def read_group(
            self, start: int, length: int, *, timeout_ms: float = feedback.REPLY_TIMEOUT_MS
        ) -> tuple[bytes, dict]:
            at_endpoint = (
                (start, length) == (feedback.FEEDBACK_START, feedback.FEEDBACK_LENGTH)
                and self.motion_started_at is not None
                and self.goal_velocity == 0
                and not self.fault_emitted
            )
            if not at_endpoint:
                return super().read_group(start, length, timeout_ms=timeout_ms)

            self.fault_emitted = True
            if endpoint_fault == "missing":
                raise feedback.TransportRefusal(
                    "endpoint feedback missing",
                    {"operation": "sync_read", "requested_width": length},
                )
            original_temperature = self.temperature
            self.temperature = feedback.ABORT_C
            try:
                return super().read_group(start, length, timeout_ms=timeout_ms)
            finally:
                self.temperature = original_temperature

    clock = FakeClock()
    transport = EndpointFaultTransport(clock=clock, travel_rate_raw_per_s=25.0)
    records: list[dict] = []

    result = feedback.run_comparison(
        transport,
        input_fn=lambda _prompt: "ROTATE",
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        emit=records.append,
    )

    assert result == 2
    assert transport.goal_velocity == 0
    assert transport.torque == 0
    assert not any(record.get("phase") == "motion_endpoint" for record in records)
    if endpoint_fault == "missing":
        assert "endpoint feedback missing" in records[-1]["reason"]
    else:
        assert "temperature 55 C" in records[-1]["reason"]


def test_a_late_motion_read_zeroes_then_refuses_the_timing_envelope() -> None:
    clock = FakeClock()
    transport = FakeTransport(clock=clock, motion_read_delay_s=0.5)
    records: list[dict] = []

    result = feedback.run_comparison(
        transport,
        input_fn=lambda _prompt: "ROTATE",
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        emit=records.append,
    )

    assert result == 2
    motion_index = next(
        index
        for index, (_, address, value) in enumerate(transport.write_times)
        if address == feedback.GOAL_VELOCITY_ADDRESS and value == feedback.MOTION_VELOCITY_RAW
    )
    motion_started = transport.write_times[motion_index][0]
    first_zero = next(
        timestamp
        for timestamp, address, value in transport.write_times[motion_index + 1 :]
        if address == feedback.GOAL_VELOCITY_ADDRESS and value == 0
    )
    assert first_zero - motion_started > feedback.MAX_COMMAND_S
    assert transport.goal_velocity == 0
    assert transport.torque == 0
    assert set(transport.operation_threads) == {threading.get_ident()}
    assert "timing envelope" in records[-1]["reason"]


def test_suspicious_temperature_stops_once_and_cleanup_cannot_erase_refusal() -> None:
    transport = FakeTransport(hot_during_motion=True)
    clock = FakeClock()
    records: list[dict] = []

    result = feedback.run_comparison(
        transport,
        input_fn=lambda _prompt: "ROTATE",
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        emit=records.append,
    )

    assert result == 2
    rejected = [
        record for record in records if record.get("rejected") and record.get("phase") == "motion"
    ]
    assert rejected[-1]["temperature_c"] == 77
    assert rejected[-1]["group_trace"]["requested_width"] == feedback.FEEDBACK_LENGTH
    assert "diagnostic ceiling 55 C" in rejected[-1]["rejection_reason"]
    assert transport.goal_velocity == 0
    assert transport.torque == 0
    assert transport.closed
    assert sum(
        address == feedback.GOAL_VELOCITY_ADDRESS and value == feedback.MOTION_VELOCITY_RAW
        for address, _, value in transport.writes
    ) == 1
    assert records[-1]["phase"] == "result"
    assert records[-1]["status"] == "refused"
    assert records[-1]["reason"] == rejected[-1]["rejection_reason"]
    assert not any(record.get("phase") == "motion_endpoint" for record in records)


def test_wrong_authorization_refuses_before_any_nonzero_write() -> None:
    transport = FakeTransport()
    records: list[dict] = []

    result = feedback.run_comparison(transport, input_fn=lambda _prompt: "rotate", emit=records.append)

    assert result == 2
    assert not any(value != 0 for _, _, value in transport.writes)
    assert transport.goal_velocity == 0
    assert transport.torque == 0
    assert transport.closed


def test_reverse_velocity_refuses_on_the_first_motion_sample() -> None:
    transport = FakeTransport(reverse_velocity=True)
    clock = FakeClock()
    records: list[dict] = []

    result = feedback.run_comparison(
        transport,
        input_fn=lambda _prompt: "ROTATE",
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        emit=records.append,
    )

    assert result == 2
    motion = [
        record
        for record in records
        if record.get("phase") == "motion" and record.get("rejected")
    ]
    assert transport.motion_read_count == 1
    assert motion[0]["present_velocity_raw"] == -50
    assert "opposite" in motion[0]["rejection_reason"]
    assert transport.goal_velocity == 0
    assert transport.torque == 0


def test_direct_cli_refuses_any_port_other_than_the_left_alias(monkeypatch) -> None:
    def must_not_construct(*_args, **_kwargs):
        raise AssertionError("transport constructed for an unapproved serial path")

    monkeypatch.setattr(feedback, "DirectSdkTransport", must_not_construct)

    try:
        feedback.main(["--port", "/dev/am_arm_follower_right"])
    except SystemExit as error:
        assert error.code == 2
    else:
        raise AssertionError("right-bus path was accepted")


def test_partial_open_failure_still_requests_zero_torque_off_and_close() -> None:
    class PartialOpenTransport(FakeTransport):
        def open(self) -> None:
            self.opened = True
            raise feedback.TransportRefusal("fake failure after open", {"phase": "open"})

    transport = PartialOpenTransport()
    records: list[dict] = []

    result = feedback.run_comparison(transport, input_fn=lambda _prompt: "ROTATE", emit=records.append)

    assert result == 2
    assert (feedback.GOAL_VELOCITY_ADDRESS, 2, 0) in transport.writes
    assert (feedback.TORQUE_ENABLE_ADDRESS, 1, 0) in transport.writes
    assert transport.closed
    assert records[-1]["status"] == "refused"
