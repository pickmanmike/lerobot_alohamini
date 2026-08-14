#!/usr/bin/env python3
"""Bounded, SSH-safe smoke tests for the Aloha Mini 1 body motors.

This utility is intentionally conservative:
- it works from a plain SSH terminal (no pynput/X display required);
- every motion is time-bounded;
- Goal_Velocity is written as zero before torque is enabled;
- Feetech writes that report a missing status packet are verified by readback;
- all selected motors are stopped and torque-disabled in ``finally``.

Aloha Mini 1 body mapping:
    8  left wheel
    9  back wheel
    10 right wheel
    11 lift axis

Run only with the chassis supported, wheels clear, lift supported near mid-travel,
and an accessible 12 V motor-power disconnect.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from dataclasses import dataclass

from lerobot.motors import Motor, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus, OperatingMode

MODEL = "sts3215"
DEFAULT_PORT = "/dev/am_arm_follower_left"
RETRIES = 5
STEPS_PER_DEG = 4096.0 / 360.0
WHEEL_RADIUS_M = 0.05
BASE_RADIUS_M = 0.125

BODY_MOTORS = {
    "left_wheel": 8,
    "back_wheel": 9,
    "right_wheel": 10,
    "lift_axis": 11,
}


@dataclass(frozen=True)
class MotionPlan:
    names: tuple[str, ...]
    raw_velocity: dict[str, int]
    current_limit_ma: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bounded, SSH-safe Aloha Mini 1 body motor smoke test"
    )
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument(
        "--action",
        required=True,
        choices=(
            "probe-wheel8",
            "wheel8-positive",
            "wheel8-negative",
            "wheel9-positive",
            "wheel9-negative",
            "wheel10-positive",
            "wheel10-negative",
            "forward",
            "backward",
            "strafe-left",
            "strafe-right",
            "rotate-left",
            "rotate-right",
            "lift-positive",
            "lift-negative",
        ),
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0.20,
        help="Motion duration in seconds (default 0.20, maximum 1.00)",
    )
    parser.add_argument(
        "--raw",
        type=int,
        default=180,
        help="Magnitude for one-motor/lift pulses (default 180, maximum 600)",
    )
    parser.add_argument(
        "--countdown",
        type=float,
        default=3.0,
        help="Seconds before motion begins (default 3.0)",
    )
    args = parser.parse_args()

    if not 0.05 <= args.duration <= 1.0:
        parser.error("--duration must be between 0.05 and 1.00 seconds")
    if not 1 <= abs(args.raw) <= 600:
        parser.error("--raw magnitude must be between 1 and 600")
    if not 0.0 <= args.countdown <= 10.0:
        parser.error("--countdown must be between 0 and 10 seconds")
    return args


def make_motor(name: str) -> Motor:
    norm = MotorNormMode.DEGREES if name == "lift_axis" else MotorNormMode.RANGE_M100_100
    return Motor(BODY_MOTORS[name], MODEL, norm)


def read_raw(bus: FeetechMotorsBus, register: str, name: str) -> int:
    return int(bus.read(register, name, normalize=False))


def write_verified(
    bus: FeetechMotorsBus,
    register: str,
    name: str,
    value: int,
    *,
    verify: bool = True,
) -> None:
    """Write a register; accept a missing ACK only when readback proves success."""
    try:
        bus.write(register, name, int(value), normalize=False, num_retry=RETRIES)
    except Exception as exc:
        if not verify:
            raise
        try:
            actual = read_raw(bus, register, name)
        except Exception as read_exc:
            raise RuntimeError(
                f"{register} write failed for {name}, and readback also failed: "
                f"write={exc!r}; read={read_exc!r}"
            ) from exc
        if actual != int(value):
            raise RuntimeError(
                f"{register} write failed for {name}; readback={actual}, expected={value}: {exc!r}"
            ) from exc
        print(
            f"[WARN] {register} write for {name} reported no acknowledgement, "
            f"but readback verified {actual}. Continuing."
        )


def configure_velocity_motor(bus: FeetechMotorsBus, name: str) -> None:
    # Unlock EEPROM, remove torque, select velocity mode, and seed a zero goal.
    write_verified(bus, "Lock", name, 0)
    write_verified(bus, "Torque_Enable", name, 0)
    write_verified(bus, "Operating_Mode", name, OperatingMode.VELOCITY.value)
    write_verified(bus, "Goal_Velocity", name, 0)

    # Enable only after the zero goal is confirmed. Lock is EEPROM write protection;
    # a missing status packet is tolerated only when readback confirms the value.
    write_verified(bus, "Torque_Enable", name, 1)
    write_verified(bus, "Lock", name, 1)


def safe_stop(bus: FeetechMotorsBus, names: tuple[str, ...]) -> bool:
    cleanup_ok = True
    try:
        bus.sync_write(
            "Goal_Velocity",
            {name: 0 for name in names},
            normalize=False,
            num_retry=RETRIES,
        )
    except Exception as exc:
        print(f"[WARN] Could not sync-write zero velocity: {exc}", file=sys.stderr)
    for name in names:
        try:
            actual = read_raw(bus, "Goal_Velocity", name)
            if actual != 0:
                write_verified(bus, "Goal_Velocity", name, 0)
        except Exception as exc:
            try:
                write_verified(bus, "Goal_Velocity", name, 0)
            except Exception as one_exc:
                cleanup_ok = False
                print(
                    f"[WARN] Could not verify or write zero velocity to {name}: "
                    f"read={exc}; write={one_exc}",
                    file=sys.stderr,
                )
    time.sleep(0.05)
    for name in names:
        try:
            write_verified(bus, "Torque_Enable", name, 0)
        except Exception as exc:
            cleanup_ok = False
            print(f"[WARN] Could not disable torque on {name}: {exc}", file=sys.stderr)
        try:
            write_verified(bus, "Lock", name, 0)
        except Exception as exc:
            cleanup_ok = False
            print(f"[WARN] Could not unlock {name}: {exc}", file=sys.stderr)
    return cleanup_ok


def raw_from_deg_per_second(deg_per_second: float) -> int:
    return int(round(deg_per_second * STEPS_PER_DEG))


def base_plan(action: str) -> MotionPlan:
    # Same Aloha Mini 1 kinematics as the repository, but deliberately slow.
    x_mps = 0.0
    y_mps = 0.0
    theta_degps = 0.0
    if action == "forward":
        x_mps = 0.025
    elif action == "backward":
        x_mps = -0.025
    elif action == "strafe-left":
        y_mps = 0.025
    elif action == "strafe-right":
        y_mps = -0.025
    elif action == "rotate-left":
        theta_degps = 12.0
    elif action == "rotate-right":
        theta_degps = -12.0
    else:
        raise ValueError(action)

    theta_radps = math.radians(theta_degps)
    # Repository convention: velocity vector [-x, -y, theta].
    vx, vy, omega = -x_mps, -y_mps, theta_radps
    angles = [math.radians(v - 90.0) for v in (240.0, 0.0, 120.0)]
    names = ("left_wheel", "back_wheel", "right_wheel")
    raw: dict[str, int] = {}
    for name, angle in zip(names, angles, strict=True):
        linear = math.cos(angle) * vx + math.sin(angle) * vy + BASE_RADIUS_M * omega
        wheel_radps = linear / WHEEL_RADIUS_M
        raw[name] = raw_from_deg_per_second(math.degrees(wheel_radps))
    return MotionPlan(names, raw, current_limit_ma=1400.0)


def build_plan(action: str, raw_magnitude: int) -> MotionPlan:
    if action == "probe-wheel8":
        return MotionPlan(("left_wheel",), {"left_wheel": 0}, current_limit_ma=1400.0)

    one_motor = {
        "wheel8-positive": ("left_wheel", +raw_magnitude),
        "wheel8-negative": ("left_wheel", -raw_magnitude),
        "wheel9-positive": ("back_wheel", +raw_magnitude),
        "wheel9-negative": ("back_wheel", -raw_magnitude),
        "wheel10-positive": ("right_wheel", +raw_magnitude),
        "wheel10-negative": ("right_wheel", -raw_magnitude),
        "lift-positive": ("lift_axis", +raw_magnitude),
        "lift-negative": ("lift_axis", -raw_magnitude),
    }
    if action in one_motor:
        name, value = one_motor[action]
        limit = 1000.0 if name == "lift_axis" else 1400.0
        return MotionPlan((name,), {name: value}, current_limit_ma=limit)

    return base_plan(action)


def print_state(bus: FeetechMotorsBus, names: tuple[str, ...], prefix: str) -> None:
    for name in names:
        voltage_raw = read_raw(bus, "Present_Voltage", name)
        current_raw = read_raw(bus, "Present_Current", name)
        print(
            f"[{prefix}] {name} id={BODY_MOTORS[name]} "
            f"firmware={read_raw(bus, 'Firmware_Major_Version', name)}."
            f"{read_raw(bus, 'Firmware_Minor_Version', name)} "
            f"response_level={read_raw(bus, 'Response_Status_Level', name)} "
            f"status={read_raw(bus, 'Status', name)} "
            f"mode={read_raw(bus, 'Operating_Mode', name)} "
            f"torque={read_raw(bus, 'Torque_Enable', name)} "
            f"lock={read_raw(bus, 'Lock', name)} "
            f"goal_vel={read_raw(bus, 'Goal_Velocity', name)} "
            f"present_vel={read_raw(bus, 'Present_Velocity', name)} "
            f"voltage={voltage_raw / 10.0:.1f}V "
            f"current={current_raw * 6.5:.1f}mA "
            f"temperature={read_raw(bus, 'Present_Temperature', name)}C"
        )


def main() -> int:
    args = parse_args()
    plan = build_plan(args.action, abs(args.raw))
    motors = {name: make_motor(name) for name in plan.names}
    bus = FeetechMotorsBus(port=args.port, motors=motors)

    print("Aloha Mini 1 bounded body smoke test")
    print(f"Port: {args.port}")
    print(f"Action: {args.action}")
    print(f"Selected motors: {', '.join(f'{n}=ID{BODY_MOTORS[n]}' for n in plan.names)}")
    print("Required: chassis supported, selected moving parts clear, motor disconnect reachable.")

    try:
        bus.connect(handshake=True)
        print("[OK] Bus handshake passed.")

        for name in plan.names:
            configure_velocity_motor(bus, name)
        print_state(bus, plan.names, "READY")

        if args.action == "probe-wheel8":
            print("[OK] Zero-velocity torque/lock probe completed; no motion requested.")
            return 0

        countdown_end = time.monotonic() + args.countdown
        last_printed = None
        while True:
            remaining = countdown_end - time.monotonic()
            if remaining <= 0:
                break
            whole = int(math.ceil(remaining))
            if whole != last_printed:
                print(f"Motion begins in {whole}...")
                last_printed = whole
            time.sleep(min(0.05, remaining))

        print(f"[MOVE] Commanding {plan.raw_velocity} for at most {args.duration:.2f}s")
        start = time.monotonic()
        # A sync write starts multi-wheel plans together and, by protocol design,
        # does not wait for per-servo status packets.
        bus.sync_write("Goal_Velocity", plan.raw_velocity, normalize=False, num_retry=RETRIES)

        while time.monotonic() - start < args.duration:
            for name in plan.names:
                current_ma = read_raw(bus, "Present_Current", name) * 6.5
                if current_ma >= plan.current_limit_ma:
                    raise RuntimeError(
                        f"Overcurrent on {name}: {current_ma:.1f}mA >= {plan.current_limit_ma:.1f}mA"
                    )
            time.sleep(0.02)

        if not safe_stop(bus, plan.names):
            raise RuntimeError("Motion completed, but verified safe-stop cleanup failed.")
        print_state(bus, plan.names, "STOPPED")
        print("[OK] Bounded motion completed and torque was disabled.")
        return 0
    except KeyboardInterrupt:
        print("\n[STOP] Keyboard interrupt received.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if bus.is_connected:
            cleanup_ok = safe_stop(bus, plan.names)
            try:
                bus.disconnect(disable_torque=False)
            except Exception as exc:
                cleanup_ok = False
                print(f"[WARN] Could not close serial port: {exc}", file=sys.stderr)
            if cleanup_ok and not bus.is_connected:
                print("[SAFE] Zero velocity verified, torque disabled, serial port closed.")
            else:
                print("[WARN] Safe cleanup was attempted but could not be fully verified.", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
