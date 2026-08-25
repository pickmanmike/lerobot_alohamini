#!/usr/bin/env python

from __future__ import annotations

from collections.abc import Callable

import pytest

from tools.check_am1_leader_buses import JOINT_NAMES, main, run_check


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class ReadOnlyFakeBus:
    def __init__(self, port: str, samples: list[object]) -> None:
        self.port = port
        self.samples = list(samples)
        self.connect_calls: list[bool] = []
        self.read_calls: list[tuple[str, object, bool, int]] = []
        self.disconnect_calls: list[bool] = []

    def connect(self, *, handshake: bool = True) -> None:
        self.connect_calls.append(handshake)

    def sync_read(self, register: str, motors=None, *, normalize: bool = True, num_retry: int = 3):
        self.read_calls.append((register, motors, normalize, num_retry))
        if not self.samples:
            raise ConnectionError(f"{self.port} dropped a packet")
        sample = self.samples.pop(0)
        if isinstance(sample, BaseException):
            raise sample
        return sample

    def disconnect(self, *, disable_torque: bool = True) -> None:
        self.disconnect_calls.append(disable_torque)

    def __getattr__(self, name: str):
        raise AssertionError(f"forbidden bus method requested: {name}")


def raw_sample(offset: int = 0) -> dict[str, int]:
    return {name: 1000 + offset + index for index, name in enumerate(JOINT_NAMES)}


def make_factory(
    samples: dict[str, list[object]],
) -> tuple[Callable[..., ReadOnlyFakeBus], dict[str, ReadOnlyFakeBus], list[dict[str, object]]]:
    buses: dict[str, ReadOnlyFakeBus] = {}
    constructions: list[dict[str, object]] = []

    def factory(*, port: str, motors: dict[str, object], calibration=None) -> ReadOnlyFakeBus:
        constructions.append({"port": port, "motors": motors, "calibration": calibration})
        bus = ReadOnlyFakeBus(port, samples[port])
        buses[port] = bus
        return bus

    return factory, buses, constructions


def test_clean_check_reads_both_raw_six_motor_buses_without_writes() -> None:
    clock = FakeClock()
    factory, buses, constructions = make_factory(
        {
            "COM8": [raw_sample(0), raw_sample(10), raw_sample(20)],
            "COM7": [raw_sample(100), raw_sample(110), raw_sample(120)],
        }
    )
    lines: list[str] = []

    result = run_check(
        bus_factory=factory,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        port_present=lambda port: port in {"COM8", "COM7"},
        duration_s=0.21,
        sample_hz=10.0,
        out=lines.append,
    )

    assert result.sample_count == 3
    assert [item["port"] for item in constructions] == ["COM8", "COM7"]
    for construction in constructions:
        assert construction["calibration"] is None
        motors = construction["motors"]
        assert list(motors) == list(JOINT_NAMES)
        assert [motor.id for motor in motors.values()] == [1, 2, 3, 4, 5, 6]
        assert {motor.model for motor in motors.values()} == {"sts3215"}
    for bus in buses.values():
        assert bus.connect_calls == [False]
        assert bus.disconnect_calls == [False]
        assert bus.read_calls == [("Present_Position", None, False, 0)] * 3
    text = "\n".join(lines)
    assert "LEFT_PORT=COM8" in text
    assert "RIGHT_PORT=COM7" in text
    assert "SAMPLE_COUNT=3" in text
    assert "FIRST_LEFT=" in text and "LAST_RIGHT=" in text
    assert "MIN_MAX_LEFT=" in text and "MIN_MAX_RIGHT=" in text
    assert text.rstrip().endswith("LEADER_BUS_CHECK=PASS")


@pytest.mark.parametrize(
    ("side", "bad_sample", "message"),
    [
        ("COM8", {name: 1000 for name in JOINT_NAMES[:-1]}, "exactly six"),
        ("COM7", {**raw_sample(), "gripper": True}, "integral non-boolean"),
        ("COM8", {**raw_sample(), "wrist_roll": 70000}, "raw register range"),
        ("COM7", ConnectionError("missing packet"), "missing packet"),
    ],
)
def test_any_drop_or_malformed_sample_fails_immediately_and_disconnects(side, bad_sample, message) -> None:
    clock = FakeClock()
    samples = {"COM8": [raw_sample()], "COM7": [raw_sample()]}
    samples[side] = [bad_sample]
    factory, buses, _ = make_factory(samples)

    with pytest.raises(Exception, match=message):
        run_check(
            bus_factory=factory,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            port_present=lambda port: True,
            duration_s=0.01,
            sample_hz=10.0,
            out=lambda _: None,
        )

    assert all(bus.disconnect_calls == [False] for bus in buses.values())


def test_disappearing_port_fails_before_another_read_and_disconnects_both() -> None:
    clock = FakeClock()
    factory, buses, _ = make_factory({"COM8": [raw_sample(), raw_sample()], "COM7": [raw_sample(), raw_sample()]})
    checks = 0

    def port_present(_: str) -> bool:
        nonlocal checks
        checks += 1
        return checks <= 4

    with pytest.raises(RuntimeError, match="disappeared"):
        run_check(
            bus_factory=factory,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            port_present=port_present,
            duration_s=0.2,
            sample_hz=10.0,
            out=lambda _: None,
        )

    assert len(buses["COM8"].read_calls) == 1
    assert len(buses["COM7"].read_calls) == 1
    assert buses["COM8"].disconnect_calls == [False]
    assert buses["COM7"].disconnect_calls == [False]


def test_single_transient_packet_drop_after_clean_sample_is_not_tolerated() -> None:
    clock = FakeClock()
    factory, buses, _ = make_factory(
        {
            "COM8": [raw_sample(), ConnectionError("transient packet drop")],
            "COM7": [raw_sample(), raw_sample()],
        }
    )

    with pytest.raises(RuntimeError, match="transient packet drop"):
        run_check(
            bus_factory=factory,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            port_present=lambda port: True,
            duration_s=0.2,
            sample_hz=10.0,
            out=lambda _: None,
        )

    assert len(buses["COM8"].read_calls) == 2
    assert len(buses["COM7"].read_calls) == 1
    assert buses["COM8"].disconnect_calls == [False]
    assert buses["COM7"].disconnect_calls == [False]


def test_keyboard_interrupt_is_primary_and_cleanup_never_writes() -> None:
    clock = FakeClock()
    factory, buses, _ = make_factory({"COM8": [KeyboardInterrupt()], "COM7": [raw_sample()]})

    with pytest.raises(KeyboardInterrupt):
        run_check(
            bus_factory=factory,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            port_present=lambda port: True,
            duration_s=0.1,
            sample_hz=10.0,
            out=lambda _: None,
        )

    assert buses["COM8"].disconnect_calls == [False]
    assert buses["COM7"].disconnect_calls == [False]


def test_disconnect_failure_exits_nonzero_without_emitting_pass(capsys) -> None:
    clock = FakeClock()
    buses: dict[str, ReadOnlyFakeBus] = {}

    class DisconnectFailBus(ReadOnlyFakeBus):
        def disconnect(self, *, disable_torque: bool = True) -> None:
            self.disconnect_calls.append(disable_torque)
            raise RuntimeError("disconnect failed")

    def factory(*, port: str, motors: dict[str, object], calibration=None) -> ReadOnlyFakeBus:
        bus_type = DisconnectFailBus if port == "COM7" else ReadOnlyFakeBus
        bus = bus_type(port, [raw_sample()])
        buses[port] = bus
        return bus

    lines: list[str] = []

    exit_code = main(
        [],
        run=lambda: run_check(
            bus_factory=factory,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            port_present=lambda port: True,
            duration_s=0.01,
            sample_hz=10.0,
            out=lines.append,
        ),
    )

    assert exit_code == 1
    assert "LEADER_BUS_CHECK=PASS" not in lines
    assert "LEADER_BUS_CHECK=PASS" not in capsys.readouterr().out
    assert any("LEADER_BUS_CHECK_CLEANUP_FAILURE=" in line for line in lines)
    assert buses["COM8"].disconnect_calls == [False]
    assert buses["COM7"].disconnect_calls == [False]


def test_help_exits_without_constructing_a_bus(capsys) -> None:
    def forbidden_run() -> None:
        raise AssertionError("help must not run the check")

    with pytest.raises(SystemExit) as raised:
        main(["--help"], run=forbidden_run)

    assert raised.value.code == 0
    assert "read-only" in capsys.readouterr().out.lower()
