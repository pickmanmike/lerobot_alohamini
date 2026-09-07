"""Opt-in AM1 relief comparison; one serial owner, no network or ordinary host loop.

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
from dataclasses import asdict
from typing import TYPE_CHECKING

from .motor_safety import REGISTER_RETRIES, set_torque_enabled, write_register

if TYPE_CHECKING:
    from .alohamini import AlohaMini


COOL_START_C = 40
ABORT_C = 55
RELIEF_MM = 10.0
MAX_RELIEF_MM = 12.0
RELIEF_TIMEOUT_S = 8.0
REST_S = 45.0
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


class ReliefCheck:
    def __init__(self, robot: AlohaMini):
        self.robot = robot
        self.start = time.monotonic()
        self.last_sample: float | None = None
        self.last_report = float("-inf")
        self.phase = ""
        self.config: dict[str, int] = {}
        self.home_started = 0.0

    def emit(self, record: dict) -> None:
        print("[LIFT RELIEF] " + json.dumps(record, allow_nan=False, separators=(",", ":")), flush=True)

    def raw(self, register: str) -> int:
        value = self.robot.left_bus.read(
            register, self.robot.lift.cfg.name, normalize=False, num_retry=REGISTER_RETRIES
        )
        if not isinstance(value, (int, float)) or not math.isfinite(value) or int(value) != value:
            raise ReliefRefusal(f"Unusable telemetry for {register}.")
        return int(value)

    def read_configuration(self) -> None:
        try:
            self.config = {name: self.raw(name) for name in CONFIG_REGISTERS}
        except Exception as error:
            raise ReliefRefusal(f"Configuration telemetry failed: {error}") from error
        self.emit({"phase": "configuration", "registers_raw": self.config,
                   "temperature_unit": "C", "current_unit": "mA", "voltage_unit": "V"})
        if self.config["Model_Number"] != 777 or self.config["Angular_Resolution"] != 1:
            raise ReliefRefusal("Expected STS3215 model 777 with angular resolution 1; conversion unverified.")
        if not COOL_START_C < self.config["Max_Temperature_Limit"] <= 100:
            raise ReliefRefusal("Unusable temperature limit telemetry; no limit will be changed.")
        if not 0 < self.config["Min_Voltage_Limit"] < self.config["Max_Voltage_Limit"] <= 255:
            raise ReliefRefusal("Unusable voltage limit telemetry.")

    def sample(self, phase: str, *, displacement_mm: float | None = None, force: bool = False) -> dict:
        started = time.monotonic()
        try:
            data = self.robot.read_lift_diagnostics()
            data["present_load_raw"] = self.raw("Present_Load")
            # Homing owns the position accumulator. Reading height inside its callback
            # would consume its delta and manufacture a false position-stall result.
            height = self.robot.lift.get_height_mm() if self.robot.lift.is_homed else None
        except Exception as error:
            raise ReliefRefusal(f"{phase}: lift telemetry failed: {error}") from error
        now = time.monotonic()
        if any(not math.isfinite(value) for value in data.values()) or (
            height is not None and not math.isfinite(height)
        ):
            raise ReliefRefusal(f"{phase}: nonfinite telemetry.")
        data.update(phase=phase, elapsed_s=round(now - self.start, 3), wall_time_ns=time.time_ns(),
                    height_mm=height, homing_displacement_mm=displacement_mm,
                    temperature_c=data["present_temperature_raw"],
                    voltage_v=data["present_voltage_raw"] / 10.0)
        if (
            force or phase != self.phase or now - self.last_report >= 0.99
            or (phase == "rest" and data["present_current_ma"] >= 200)
        ):
            self.emit(data)
            self.last_report = now
        self.phase = phase
        if now - started > MAX_SAMPLE_S or (
            self.last_sample is not None and now - self.last_sample > MAX_SAMPLE_S
        ):
            raise ReliefRefusal(f"{phase}: telemetry gap exceeded {MAX_SAMPLE_S}s.")
        self.last_sample = now
        if data["status"] != 0:
            raise ReliefRefusal(f"{phase}: servo status fault {data['status']}.")
        ceiling = min(ABORT_C, self.config["Max_Temperature_Limit"])
        if not 0 <= data["temperature_c"] < ceiling:
            raise ReliefRefusal(f"{phase}: temperature {data['temperature_c']} C reached diagnostic ceiling {ceiling} C.")
        if not self.config["Min_Voltage_Limit"] <= data["present_voltage_raw"] <= self.config["Max_Voltage_Limit"]:
            raise ReliefRefusal(f"{phase}: voltage outside the read-only configured limits.")
        if data["torque_enable"] not in (0, 1) or data["operating_mode"] != 1:
            raise ReliefRefusal(f"{phase}: unexpected torque/mode telemetry.")
        if abs(data["present_velocity_raw"]) > 400:
            raise ReliefRefusal(f"{phase}: unexpected velocity magnitude.")
        return data

    def preflight(self) -> None:
        self.last_sample = None  # The operator gate may take arbitrarily long, torque remains off.
        data = self.sample("preflight", force=True)
        if data["temperature_c"] > COOL_START_C:
            raise ReliefRefusal(f"Start must be cool: {data['temperature_c']} C exceeds {COOL_START_C} C.")
        if data["torque_enable"] != 0:
            raise ReliefRefusal("Start requires lift torque disabled.")
        if data["goal_velocity_raw"] != 0 or abs(data["present_velocity_raw"]) > STILL_VELOCITY_RAW:
            raise ReliefRefusal("Start requires zero goal velocity and a stationary carriage.")

    @staticmethod
    def expect(data: dict, *, torque: int, goal: int) -> None:
        if data["torque_enable"] != torque or data["goal_velocity_raw"] != goal:
            raise ReliefRefusal(f"{data['phase']}: unexpected torque/goal velocity readback.")

    @staticmethod
    def expect_stationary(data: dict) -> None:
        if data["goal_velocity_raw"] != 0 or abs(data["present_velocity_raw"]) > STILL_VELOCITY_RAW:
            raise ReliefRefusal(f"{data['phase']}: expected zero goal velocity and a stationary carriage.")

    def final_torque_off_before_motion(self, displacement_mm: float) -> None:
        # This must be the last setup write. set_torque_enabled(False) is not
        # suitable here because it writes Lock after Torque_Enable.
        try:
            write_register(self.robot.left_bus, "Torque_Enable", self.robot.lift.cfg.name, 0)
        except Exception as error:
            raise ReliefRefusal(f"before_torque: final torque-off request failed: {error}") from error
        data = self.sample("before_torque", displacement_mm=displacement_mm, force=True)
        self.expect(data, torque=0, goal=0)
        self.expect_stationary(data)
        if data["temperature_c"] > COOL_START_C:
            raise ReliefRefusal("Start must still be cool immediately before lift torque activation.")

    def cleanup_readback(self) -> None:
        # The ordinary safe shutdown writes Torque_Enable=0 and then Lock=0.
        # End this diagnostic with Torque_Enable itself, then prove torque and
        # velocity-target state before the owning bus is closed.
        write_register(self.robot.left_bus, "Torque_Enable", self.robot.lift.cfg.name, 0)
        record = {
            "phase": "cleanup_readback",
            "elapsed_s": round(time.monotonic() - self.start, 3),
            "wall_time_ns": time.time_ns(),
            "torque_enable": self.raw("Torque_Enable"),
            "goal_velocity_raw": self.raw("Goal_Velocity"),
            "present_velocity_raw": self.raw("Present_Velocity"),
        }
        self.emit(record)
        if record["torque_enable"] != 0 or record["goal_velocity_raw"] != 0:
            raise ReliefRefusal("cleanup_readback: final torque/goal velocity readback was not zero.")

    def home_guard(self, phase: str, displacement_mm: float) -> None:
        if not math.isfinite(displacement_mm) or not math.isfinite(self.robot.lift._last_tick):
            raise ReliefRefusal("homing: nonfinite position telemetry.")
        if phase == "before_torque":
            # Observe the state left by configure() and its final zero write,
            # without attributing any side effect to a particular register.
            data = self.sample("setup_after_writes", displacement_mm=displacement_mm, force=True)
            self.expect_stationary(data)
            if data["temperature_c"] > COOL_START_C:
                raise ReliefRefusal("Start must still be cool immediately before lift torque activation.")
            self.final_torque_off_before_motion(displacement_mm)
            self.home_started = time.monotonic()
            return

        data = self.sample(phase, displacement_mm=displacement_mm)
        self.expect(data, torque=1, goal=200)
        if time.monotonic() - self.home_started >= self.robot.lift.cfg.home_timeout_s:
            raise ReliefRefusal("homing: timed out during guarded telemetry.")
        if displacement_mm > 0.5 or data["present_velocity_raw"] < -STILL_VELOCITY_RAW:
            raise ReliefRefusal("homing: unexpected upward direction.")
        if displacement_mm < -self.robot.lift.cfg.soft_max_mm:
            raise ReliefRefusal("homing: maximum travel exceeded.")

    def raised_sample(self, phase: str, goal: int, *, force: bool = False) -> dict:
        data = self.sample(phase, force=force)
        self.expect(data, torque=1, goal=goal)
        if data["height_mm"] < -0.5 or (goal < 0 and data["present_velocity_raw"] > STILL_VELOCITY_RAW):
            raise ReliefRefusal(f"{phase}: unexpected downward direction.")
        if data["height_mm"] > MAX_RELIEF_MM:
            raise ReliefRefusal(f"{phase}: travel exceeded {MAX_RELIEF_MM} mm.")
        if phase in ("settle", "rest") and data["height_mm"] < RELIEF_MM - 0.5:
            raise ReliefRefusal(f"{phase}: unexpected downward direction after relief stopped.")
        return data

    def compare(self) -> None:
        self.read_configuration()
        self.preflight()
        print(
            "Authorize ONE home, logical +200 upward relief to 10 mm, then 45 s rest and torque-off. "
            "Clear the carriage path. Be ready to support it safely without entering the mechanism "
            "and remove motor power on a fault. No restart. Type RELIEF to proceed.", flush=True,
        )
        if input("Authorization: ").strip() != "RELIEF":
            raise ReliefRefusal("Operator did not authorize RELIEF.")
        self.preflight()  # Fresh after the gate, before any activation.
        for motor in self.robot.base_motors:
            write_register(self.robot.left_bus, "Goal_Velocity", motor, 0)
        set_torque_enabled(self.robot.left_bus, self.robot.base_motors, enabled=False)
        setup_before = self.sample("setup_before", force=True)
        self.expect(setup_before, torque=0, goal=0)
        self.expect_stationary(setup_before)
        result = self.robot.lift.home(safety_check=self.home_guard)
        if time.monotonic() - self.home_started >= self.robot.lift.cfg.home_timeout_s:
            raise ReliefRefusal("homing: timed out before guarded completion.")
        self.emit({"phase": "home_complete", "result": asdict(result), "zero_reference": "process-local, unchanged"})
        self.raised_sample("post_home", 0, force=True)

        self.robot.lift.apply_action({"lift_axis.vel": 200})
        started = time.monotonic()
        while True:
            time.sleep(POLL_S)
            data = self.raised_sample("relief", -200)
            elapsed = time.monotonic() - started
            if elapsed >= RELIEF_TIMEOUT_S:
                raise ReliefRefusal(f"relief: target not reached within {RELIEF_TIMEOUT_S}s.")
            if data["height_mm"] >= RELIEF_MM:
                break
            if elapsed >= 2.0 and data["height_mm"] < 0.5:
                raise ReliefRefusal("relief: no useful upward progress within 2 seconds.")
        self.robot.lift.stop()

        started = time.monotonic()
        stable = 0
        last_height = data["height_mm"]
        while stable < 3:
            time.sleep(POLL_S)
            data = self.raised_sample("settle", 0)
            still = abs(data["present_velocity_raw"]) <= STILL_VELOCITY_RAW and abs(data["height_mm"] - last_height) <= 0.1
            stable = stable + 1 if still else 0
            last_height = data["height_mm"]
            if time.monotonic() - started >= 1.0:
                raise ReliefRefusal("settle: motion did not stop within 1 second.")
        rest_height = data["height_mm"]
        started = time.monotonic()
        high_current = 0
        while True:
            data = self.raised_sample("rest", 0, force=time.monotonic() - started >= REST_S)
            if abs(data["height_mm"] - rest_height) > 0.5 or abs(data["present_velocity_raw"]) > STILL_VELOCITY_RAW:
                raise ReliefRefusal("rest: unexpected stationary motion.")
            high_current = high_current + 1 if data["present_current_ma"] >= 200 else 0
            if high_current >= 3:
                raise ReliefRefusal("rest: stationary current >=200 mA for three consecutive samples.")
            if time.monotonic() - started >= REST_S:
                break
            time.sleep(POLL_S)


def run_lift_relief(robot: AlohaMini) -> int:
    """Run once and always use the existing zero/torque-off/bus-close cleanup."""
    primary: BaseException | None = None
    check = ReliefCheck(robot)
    try:
        # Connect the owning bus only: ordinary robot.connect/configure would activate
        # before cold telemetry and overwrite the settings this comparison must read.
        # The diagnostic itself reads and validates the actual model/firmware below;
        # avoid a second firmware sweep by the ordinary multi-motor handshake.
        robot.left_bus.connect(handshake=False)
        check.compare()
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
                    motor_shutdown_check=check.cleanup_readback,
                )
            except BaseException as error:
                errors = [f"shutdown also failed: {type(error).__name__}: {error}"]
        finally:
            signal.signal(signal.SIGINT, previous_sigint)
        if errors:
            if primary is None:
                primary = RuntimeError("Relief comparison cleanup failed.")
            for error in errors:
                primary.add_note(error)
    if primary is not None:
        reason = "Operator interrupted the diagnostic." if isinstance(primary, KeyboardInterrupt) else str(primary)
        print(f"LIFT_RELIEF_REFUSED: {reason}", flush=True)
        for note in getattr(primary, "__notes__", ()):
            print(f"Cleanup detail: {note}", flush=True)
        print("Support the carriage safely; remove motor power. No automatic restart.", flush=True)
        return 130 if isinstance(primary, KeyboardInterrupt) else 2 if isinstance(primary, ReliefRefusal) else 1
    print("LIFT_RELIEF_PASS: bounded raised rest completed; zero/torque-off cleanup completed. Remove motor power.", flush=True)
    return 0
