#!/usr/bin/env python

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from numbers import Integral
from typing import Any, Protocol

from lerobot.motors import Motor, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus

LEFT_PORT = "COM8"
RIGHT_PORT = "COM7"
JOINT_NAMES = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
)
RAW_POSITION_MIN = 0
RAW_POSITION_MAX = 0xFFFF


class Bus(Protocol):
    def connect(self, *, handshake: bool = True) -> None: ...

    def sync_read(
        self, data_name: str, motors=None, *, normalize: bool = True, num_retry: int = 3
    ) -> dict[str, int]: ...

    def disconnect(self, *, disable_torque: bool = True) -> None: ...


@dataclass(frozen=True)
class CheckResult:
    sample_count: int
    first: dict[str, dict[str, int]]
    last: dict[str, dict[str, int]]
    min_max: dict[str, dict[str, dict[str, int]]]


def _motors() -> dict[str, Motor]:
    return {
        name: Motor(index, "sts3215", MotorNormMode.DEGREES)
        for index, name in enumerate(JOINT_NAMES, start=1)
    }


def _default_bus_factory(*, port: str, motors: dict[str, Motor], calibration=None) -> Bus:
    return FeetechMotorsBus(port=port, motors=motors, calibration=calibration)


def _default_port_present(port: str) -> bool:
    from serial.tools import list_ports

    return any(info.device.upper() == port for info in list_ports.comports())


def _validate_sample(side: str, port: str, sample: object) -> dict[str, int]:
    if not isinstance(sample, Mapping) or len(sample) != len(JOINT_NAMES) or set(sample) != set(JOINT_NAMES):
        raise RuntimeError(f"{side} {port} sample must contain exactly six named motor values")
    validated: dict[str, int] = {}
    for name in JOINT_NAMES:
        value = sample[name]
        if isinstance(value, bool) or not isinstance(value, Integral):
            raise RuntimeError(f"{side} {port} {name} must be an integral non-boolean raw value")
        raw = int(value)
        if not RAW_POSITION_MIN <= raw <= RAW_POSITION_MAX:
            raise RuntimeError(
                f"{side} {port} {name}={raw} is outside the raw register range "
                f"{RAW_POSITION_MIN}..{RAW_POSITION_MAX}"
            )
        validated[name] = raw
    return validated


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def run_check(
    *,
    bus_factory: Callable[..., Bus] = _default_bus_factory,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    port_present: Callable[[str], bool] = _default_port_present,
    duration_s: float = 30.0,
    sample_hz: float = 10.0,
    out: Callable[[str], Any] = print,
) -> CheckResult:
    if duration_s <= 0 or duration_s > 30.0:
        raise ValueError("duration_s must be greater than zero and no more than 30 seconds")
    if sample_hz <= 0 or sample_hz > 10.0:
        raise ValueError("sample_hz must be greater than zero and no more than 10 Hz")

    specs = (("left", LEFT_PORT), ("right", RIGHT_PORT))
    buses = {
        side: bus_factory(port=port, motors=_motors(), calibration=None)
        for side, port in specs
    }
    connected: list[tuple[str, Bus]] = []
    samples: dict[str, list[dict[str, int]]] = {"left": [], "right": []}
    primary: BaseException | None = None
    result: CheckResult | None = None

    out(f"LEFT_PORT={LEFT_PORT}")
    out(f"RIGHT_PORT={RIGHT_PORT}")
    try:
        for side, port in specs:
            if not port_present(port):
                raise RuntimeError(f"{side} leader port {port} is missing before connect")
            buses[side].connect(handshake=False)
            connected.append((side, buses[side]))

        started = monotonic()
        deadline = started + duration_s
        interval = 1.0 / sample_hz
        next_sample = started
        while True:
            for side, port in specs:
                if not port_present(port):
                    raise RuntimeError(f"{side} leader port {port} disappeared during the check")
            for side, port in specs:
                try:
                    raw = buses[side].sync_read(
                        "Present_Position", normalize=False, num_retry=0
                    )
                except BaseException as exc:
                    if isinstance(exc, KeyboardInterrupt):
                        raise
                    raise RuntimeError(f"{side} leader {port} read failed: {exc}") from exc
                samples[side].append(_validate_sample(side, port, raw))

            next_sample += interval
            if next_sample > deadline:
                break
            remaining = next_sample - monotonic()
            if remaining > 0:
                sleep(remaining)

        count = len(samples["left"])
        if count == 0 or len(samples["right"]) != count:
            raise RuntimeError("both leader buses must produce the same nonzero sample count")
        first = {side: side_samples[0] for side, side_samples in samples.items()}
        last = {side: side_samples[-1] for side, side_samples in samples.items()}
        min_max = {
            side: {
                name: {
                    "min": min(sample[name] for sample in side_samples),
                    "max": max(sample[name] for sample in side_samples),
                }
                for name in JOINT_NAMES
            }
            for side, side_samples in samples.items()
        }
        result = CheckResult(count, first, last, min_max)
        out(f"SAMPLE_COUNT={result.sample_count}")
        out(f"FIRST_LEFT={_json(result.first['left'])}")
        out(f"FIRST_RIGHT={_json(result.first['right'])}")
        out(f"LAST_LEFT={_json(result.last['left'])}")
        out(f"LAST_RIGHT={_json(result.last['right'])}")
        out(f"MIN_MAX_LEFT={_json(result.min_max['left'])}")
        out(f"MIN_MAX_RIGHT={_json(result.min_max['right'])}")
    except BaseException as exc:
        primary = exc
        out(f"LEADER_BUS_CHECK_FAILURE={type(exc).__name__}: {exc}")
        raise
    finally:
        cleanup_failures: list[str] = []
        for side, bus in reversed(connected):
            try:
                bus.disconnect(disable_torque=False)
            except BaseException as exc:
                cleanup_failures.append(f"{side}: {type(exc).__name__}: {exc}")
        if cleanup_failures:
            out(f"LEADER_BUS_CHECK_CLEANUP_FAILURE={'; '.join(cleanup_failures)}")
            if primary is None:
                raise RuntimeError(f"leader bus cleanup failed: {'; '.join(cleanup_failures)}")
    if result is None:
        raise RuntimeError("leader bus check completed without a result")
    out("LEADER_BUS_CHECK=PASS")
    return result


def main(
    argv: list[str] | None = None,
    *,
    run: Callable[[], CheckResult] = run_check,
) -> int:
    parser = argparse.ArgumentParser(
        description="Run the read-only AM1 COM8/COM7 raw leader-bus stability check."
    )
    parser.parse_args(argv)
    try:
        run()
    except KeyboardInterrupt:
        print("LEADER_BUS_CHECK=INTERRUPTED")
        return 130
    except Exception as exc:
        print(f"LEADER_BUS_CHECK=FAIL: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
