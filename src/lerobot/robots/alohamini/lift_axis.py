#!/usr/bin/env python

# Copyright 2026 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Protocol

from lerobot.motors import Motor, MotorNormMode
from lerobot.motors.feetech import OperatingMode

from .motor_safety import REGISTER_RETRIES, set_torque_enabled, write_register

logger = logging.getLogger(__name__)


class BusLike(Protocol):
    motors: dict[str, object]

    def read(
        self,
        data_name: str,
        motor: str,
        *,
        normalize: bool = True,
        num_retry: int = 3,
    ) -> int | float: ...

    def write(
        self,
        data_name: str,
        motor: str,
        value: int | float,
        *,
        normalize: bool = True,
        num_retry: int = 3,
    ) -> None: ...


@dataclass
class LiftAxisConfig:
    enabled: bool = True
    name: str = "lift_axis"
    bus: str = "left"
    motor_id: int = 11
    motor_model: str = "sts3215"

    # Mechanical conversion (one motor revolution is 360 degrees / 4096 ticks).
    lead_mm_per_rev: float = 84.0
    output_gear_ratio: float = 1.0
    soft_min_mm: float = 0.0
    soft_max_mm: float = 600.0
    descent_floor_mm: float = 5.0

    # Positive raw velocity is physically downward on Aloha Mini 1.
    home_down_speed: int = 1300
    home_timeout_s: float = 30.0
    home_stall_current_ma: float = 300.0
    home_stall_samples: int = 2
    home_min_motion_ticks: float = 2.0
    home_poll_interval_s: float = 0.05
    leave_torque_enabled_after_home: bool = True
    home_backoff_deg: float = 5.0  # Retained for configuration compatibility; no backoff is commanded.

    # Velocity closed-loop gains.
    kp_vel: float = 300.0
    v_max: int = 1300
    on_target_mm: float = 1.0

    # Height increases upward, while positive raw velocity moves the Aloha Mini 1 lift downward.
    dir_sign: int = -1
    step_mm: float = 2.0


@dataclass(frozen=True)
class LiftHomeResult:
    elapsed_s: float
    stop_reason: str
    final_position_raw: int
    peak_current_ma: float


class LiftAxis:
    """Velocity-mode lift controller with a process-local zero reference."""

    def __init__(
        self,
        cfg: LiftAxisConfig,
        bus_left: BusLike | None,
        bus_right: BusLike | None,
    ):
        self.cfg = cfg
        self._bus = bus_left if cfg.bus == "left" else bus_right
        self.enabled = bool(cfg.enabled and self._bus is not None)
        self._ticks_per_rev = 4096.0
        self._deg_per_tick = 360.0 / self._ticks_per_rev
        self._mm_per_deg = (cfg.lead_mm_per_rev * cfg.output_gear_ratio) / 360.0

        self._last_tick = 0.0
        self._extended_ticks = 0.0
        self._z0_deg = 0.0
        self._configured = False
        self._warned_unhomed_action = False

        # This state and its zero reference deliberately do not persist across processes.
        self.is_homed = False

    def attach(self) -> None:
        if not self.enabled:
            return
        if self.cfg.name not in self._bus.motors:
            self._bus.motors[self.cfg.name] = Motor(
                self.cfg.motor_id,
                self.cfg.motor_model,
                MotorNormMode.DEGREES,
            )

    def _write_zero_velocity(self) -> None:
        write_register(
            self._bus,
            "Goal_Velocity",
            self.cfg.name,
            0,
            num_retry=REGISTER_RETRIES,
        )

    def _reset_tick_tracking(self) -> None:
        self._last_tick = float(
            self._bus.read(
                "Present_Position",
                self.cfg.name,
                normalize=False,
                num_retry=REGISTER_RETRIES,
            )
        )
        self._extended_ticks = 0.0

    def configure(self, *, force: bool = False) -> None:
        """Configure velocity mode while torque remains disabled."""
        if not self.enabled or (self._configured and not force):
            return

        self.is_homed = False
        set_torque_enabled(self._bus, (self.cfg.name,), enabled=False)
        self._write_zero_velocity()
        write_register(
            self._bus,
            "Operating_Mode",
            self.cfg.name,
            OperatingMode.VELOCITY.value,
        )
        self._reset_tick_tracking()
        self._configured = True

    def _update_extended_ticks(self) -> float:
        if not self.enabled:
            return 0.0
        cur = float(
            self._bus.read(
                "Present_Position",
                self.cfg.name,
                normalize=False,
                num_retry=REGISTER_RETRIES,
            )
        )
        delta = cur - self._last_tick
        half = self._ticks_per_rev * 0.5
        if delta > half:
            delta -= self._ticks_per_rev
        elif delta < -half:
            delta += self._ticks_per_rev
        self._extended_ticks += delta
        self._last_tick = cur
        return delta

    def _extended_deg(self) -> float:
        return self.cfg.dir_sign * self._extended_ticks * self._deg_per_tick

    def get_height_mm(self) -> float:
        if not self.enabled:
            return 0.0
        self._update_extended_ticks()
        return (self._extended_deg() - self._z0_deg) * self._mm_per_deg

    def home(
        self,
        *,
        speed_raw: int | None = None,
        timeout_s: float | None = None,
        use_current: bool = True,
    ) -> LiftHomeResult:
        """Move downward to the hard stop and create a process-local zero reference.

        A successful home leaves zero velocity commanded and torque enabled for later
        height commands. Every unsuccessful exit attempts zero velocity and torque-off.
        """
        if not self.enabled:
            raise RuntimeError("Cannot home a disabled lift axis.")

        speed = self.cfg.home_down_speed if speed_raw is None else int(speed_raw)
        timeout = self.cfg.home_timeout_s if timeout_s is None else float(timeout_s)
        self.is_homed = False
        failure: BaseException | None = None
        stop_reason = ""
        elapsed_s = 0.0
        peak_current_ma = 0.0

        try:
            if not 1 <= speed <= self.cfg.v_max:
                raise ValueError(
                    f"Lift homing speed_raw must be in 1..{self.cfg.v_max} "
                    "(positive raw is downward)."
                )
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("Lift homing timeout_s must be a finite positive number.")

            # force=True guarantees torque-off before the velocity-mode write and resets
            # the process-local tick accumulator for this homing attempt.
            self.configure(force=True)
            self._write_zero_velocity()
            set_torque_enabled(self._bus, (self.cfg.name,), enabled=True)
            write_register(self._bus, "Goal_Velocity", self.cfg.name, speed)

            start = time.monotonic()
            stalled_samples = 0
            while True:
                elapsed_s = time.monotonic() - start
                if elapsed_s >= timeout:
                    raise TimeoutError(f"Lift homing timed out after {timeout:.2f}s.")

                time.sleep(min(self.cfg.home_poll_interval_s, timeout - elapsed_s))
                moved_ticks = abs(self._update_extended_ticks())

                current_ma: float | None = None
                if use_current:
                    try:
                        raw_current = float(
                            self._bus.read(
                                "Present_Current",
                                self.cfg.name,
                                normalize=False,
                                num_retry=REGISTER_RETRIES,
                            )
                        )
                        current_ma = abs(raw_current * 6.5)
                        peak_current_ma = max(peak_current_ma, current_ma)
                    except Exception as error:
                        logger.debug("Lift homing current read failed; using motion stall fallback: %s", error)

                current_stall = current_ma is not None and current_ma >= self.cfg.home_stall_current_ma
                position_stall = moved_ticks < self.cfg.home_min_motion_ticks
                if current_stall or position_stall:
                    stalled_samples += 1
                    stop_reason = "current threshold" if current_stall else "position stall"
                else:
                    stalled_samples = 0
                    stop_reason = ""

                if stalled_samples >= self.cfg.home_stall_samples:
                    elapsed_s = time.monotonic() - start
                    break

            # Stop before capturing the final hard-stop position used as this
            # process's zero reference. The unconditional cleanup below writes zero again.
            self._write_zero_velocity()
            self._update_extended_ticks()
        except BaseException as error:
            failure = error

        zero_failure: Exception | None = None
        try:
            # This is deliberately unconditional: success, timeout, Ctrl+C, and all
            # configuration/read/write exceptions pass through this stop attempt.
            self._write_zero_velocity()
        except Exception as error:
            zero_failure = error

        if failure is not None or zero_failure is not None:
            self.is_homed = False
            torque_failure: Exception | None = None
            try:
                set_torque_enabled(self._bus, (self.cfg.name,), enabled=False)
            except Exception as error:
                torque_failure = error

            cleanup_notes = []
            if zero_failure is not None:
                cleanup_notes.append(f"zero-velocity cleanup also failed: {zero_failure}")
            if torque_failure is not None:
                cleanup_notes.append(f"torque-disable cleanup also failed: {torque_failure}")
            if failure is not None:
                for note in cleanup_notes:
                    failure.add_note(note)
                raise failure
            raise RuntimeError("Lift homing could not verify the final zero-velocity command.") from zero_failure

        if not self.cfg.leave_torque_enabled_after_home:
            try:
                set_torque_enabled(self._bus, (self.cfg.name,), enabled=False)
            except Exception as error:
                self.is_homed = False
                raise RuntimeError("Lift homed but could not restore the legacy torque-off state.") from error

        self._z0_deg = self._extended_deg()
        self.is_homed = True
        self._warned_unhomed_action = False
        return LiftHomeResult(
            elapsed_s=elapsed_s,
            stop_reason=stop_reason,
            final_position_raw=int(self._last_tick),
            peak_current_ma=peak_current_ma,
        )

    def contribute_observation(self, obs: dict[str, float]) -> None:
        if not self.enabled:
            return
        obs[f"{self.cfg.name}.height_mm"] = self.get_height_mm()
        try:
            obs[f"{self.cfg.name}.vel"] = int(
                self._bus.read("Present_Velocity", self.cfg.name, normalize=False)
            )
        except Exception:
            pass

    def apply_action(self, action: dict[str, float]) -> None:
        """Apply an ordinary height or velocity command only after this process homes."""
        if not self.enabled:
            return
        key_h = f"{self.cfg.name}.height_mm"
        key_v = f"{self.cfg.name}.vel"
        if key_h not in action and key_v not in action:
            return

        if not self.is_homed:
            self._write_zero_velocity()
            if not self._warned_unhomed_action:
                logger.warning("Ignoring lift motion command because the lift is not homed in this process.")
                self._warned_unhomed_action = True
            return

        if key_h in action:
            target_mm = float(action[key_h])
            cur_mm = self.get_height_mm()
            err = target_mm - cur_mm
            if abs(err) <= self.cfg.on_target_mm:
                v_cmd = 0.0
            else:
                v_cmd = max(-self.cfg.v_max, min(self.cfg.v_max, self.cfg.kp_vel * err))
            if v_cmd < 0 and cur_mm <= self.cfg.descent_floor_mm:
                logger.warning(
                    "Lift descent blocked at %.1fmm (floor guard %.1fmm).",
                    cur_mm,
                    self.cfg.descent_floor_mm,
                )
                v_cmd = 0.0
            if (cur_mm >= self.cfg.soft_max_mm and v_cmd > 0) or (
                cur_mm <= self.cfg.soft_min_mm and v_cmd < 0
            ):
                v_cmd = 0.0
            write_register(
                self._bus,
                "Goal_Velocity",
                self.cfg.name,
                int(self.cfg.dir_sign * v_cmd),
            )

        if key_v in action:
            velocity = max(-self.cfg.v_max, min(self.cfg.v_max, int(action[key_v])))
            cur_mm = self.get_height_mm()
            if velocity < 0 and cur_mm <= self.cfg.descent_floor_mm:
                logger.warning(
                    "Lift descent blocked at %.1fmm (floor guard %.1fmm).",
                    cur_mm,
                    self.cfg.descent_floor_mm,
                )
                velocity = 0
            elif (cur_mm >= self.cfg.soft_max_mm and velocity > 0) or (
                cur_mm <= self.cfg.soft_min_mm and velocity < 0
            ):
                velocity = 0
            write_register(
                self._bus,
                "Goal_Velocity",
                self.cfg.name,
                velocity * self.cfg.dir_sign,
            )

    def stop(self) -> None:
        if not self.enabled:
            return
        self._write_zero_velocity()

    def mark_unhomed(self) -> None:
        """Invalidate the process-local zero after a bus disconnect."""
        self.is_homed = False
