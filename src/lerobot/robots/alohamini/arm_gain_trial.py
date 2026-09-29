"""Explicit, reversible AM1 right-elbow P16→20 experiment; no motion owner."""

from __future__ import annotations

import json
import time
from typing import Any

from .motor_safety import write_register


class RightElbowP20Trial:
    """One setup write plus verified restoration on the existing bus owner.

    P is an EEPROM field, not a process-local override. Abrupt process/power loss
    can prevent restoration; the next opted-in start refuses a non-16 original.
    There is no retry/rearm or automatic escalation to a different gain.
    """

    motor = "arm_right_elbow_flex"

    def __init__(self, robot: Any) -> None:
        self.bus = robot.right_bus
        self.pending = False
        self.restore_attempted = False
        self.restore_error: BaseException | None = None
        motor = self.bus.motors.get(self.motor) if self.bus is not None else None
        if robot.config.robot_model != "alohamini1" or motor is None or motor.id != 3 or motor.model != "sts3215":
            raise RuntimeError("P20 trial requires the AM1 right follower elbow, STS3215 ID 3.")

    def _read_expected(self, expected: dict[str, int]) -> dict[str, int]:
        actual = {}
        for register, value in expected.items():
            actual[register] = self.bus.read(register, self.motor, normalize=False, num_retry=0)
            if actual[register] != value:
                raise RuntimeError(f"P20 trial {register}: expected {value}, read {actual[register]}; refusing.")
        return actual

    def _emit(self, phase: str, values: dict[str, int]) -> None:
        print("[AM1 ELBOW GAIN] " + json.dumps({
            "event": "am1_right_elbow_gain_trial", "phase": phase, "motor": self.motor,
            "wall_time_ns": time.time_ns(), "monotonic": time.monotonic(), "registers": values,
            "original_p": 16, "trial_p": 20, "restore_required": self.pending,
        }, separators=(",", ":")), flush=True)

    def preflight(self) -> None:
        # Before ordinary configuration can overwrite an unexpected original.
        values = self._read_expected({"P_Coefficient": 16, "I_Coefficient": 0, "D_Coefficient": 32,
                                      "Torque_Enable": 0, "Lock": 0, "Operating_Mode": 0})
        self._emit("original", values)

    def apply(self) -> None:
        self._read_expected({"Torque_Enable": 0, "Lock": 0, "Operating_Mode": 0,
                             "P_Coefficient": 16, "I_Coefficient": 0, "D_Coefficient": 32})
        # An interrupted/failed acknowledgement may still have applied the write.
        self.pending = True
        write_register(self.bus, "P_Coefficient", self.motor, 20, num_retry=0)
        values = self._read_expected({"P_Coefficient": 20, "Torque_Enable": 0, "Lock": 0,
                                      "Operating_Mode": 0, "I_Coefficient": 0, "D_Coefficient": 32})
        self._emit("applied_pre_activation", values)

    def restore(self, *, recover_interrupted_bus_io: bool = False) -> None:
        if not self.pending:
            return
        if self.restore_attempted:
            raise RuntimeError("P20 restoration already failed; no automatic retry; verify P16 before reuse.") from self.restore_error
        self.restore_attempted = True
        try:
            if not self.bus.is_connected:
                raise RuntimeError("P20 restoration unavailable: owning bus is closed.")
            port = getattr(self.bus, "port_handler", None)
            if recover_interrupted_bus_io and port is not None and getattr(port, "is_using", False):
                # The interrupted transaction has unwound on this same owner.
                # This clears its abandoned SDK busy latch, not a motor fault.
                port.clearPort()
                port.is_using = False
            # One final off request also covers an interrupted earlier shutdown
            # write. Lock is written before the last Torque_Enable, then verified;
            # never write P while torque-off is uncertain.
            write_register(self.bus, "Torque_Enable", self.motor, 0, num_retry=0)
            write_register(self.bus, "Lock", self.motor, 0, num_retry=0)
            write_register(self.bus, "Torque_Enable", self.motor, 0, num_retry=0)
            self._read_expected({"Torque_Enable": 0, "Lock": 0})
            write_register(self.bus, "P_Coefficient", self.motor, 16, num_retry=0)
            values = self._read_expected({"P_Coefficient": 16, "Torque_Enable": 0, "Lock": 0,
                                          "I_Coefficient": 0, "D_Coefficient": 32, "Operating_Mode": 0})
            self.pending = False
            self._emit("restored", values)
        except BaseException as error:
            self.restore_error = error
            raise
