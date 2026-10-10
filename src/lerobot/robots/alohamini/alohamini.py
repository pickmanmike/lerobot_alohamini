#!/usr/bin/env python

# Copyright 2024 The HuggingFace Inc. team. All rights reserved.
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

import json
import logging
import math
import sys
import time
from collections.abc import Callable
from functools import cached_property
from itertools import chain
from typing import Any

import numpy as np

from lerobot.cameras.utils import make_cameras_from_configs
from lerobot.motors import Motor, MotorCalibration, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus, OperatingMode
from lerobot.processor import RobotAction, RobotObservation
from lerobot.utils.decorators import check_if_already_connected, check_if_not_connected

from ..robot import Robot
from ..utils import ensure_safe_goal_position
from .am1_shoulder_integral import AM1ShoulderIntegralTrial
from .config_alohamini import AlohaMiniConfig
from .lift_axis import LiftAxis, LiftAxisConfig, LiftHomeResult
from .model_specs import arm_state_keys_for_robot_model, validate_robot_model
from .motor_safety import REGISTER_RETRIES, set_torque_enabled, write_register

logger = logging.getLogger(__name__)


# Per-arm hardware profiles. Keep the profile name about the arm itself:
# role, DOF, and motor class. Whole-robot SKUs are mapped separately below.
_ARM_PROFILES: dict[str, tuple[tuple[str, int, str, MotorNormMode | None], ...]] = {
    "so-arm-5dof": (
        ("shoulder_pan", 1, "sts3215", None),
        ("shoulder_lift", 2, "sts3215", None),
        ("elbow_flex", 3, "sts3215", None),
        ("wrist_flex", 4, "sts3215", None),
        ("wrist_roll", 5, "sts3215", None),
        ("gripper", 6, "sts3215", MotorNormMode.RANGE_0_100),
    ),
    "am-leader-6dof": (
        ("shoulder_pan", 1, "sts3215", None),
        ("shoulder_lift", 2, "sts3215", None),
        ("elbow_flex", 3, "sts3215", None),
        ("wrist_flex", 4, "sts3215", None),
        ("wrist_yaw", 5, "sts3215", None),
        ("wrist_roll", 6, "sts3215", None),
        ("gripper", 7, "sts3215", MotorNormMode.RANGE_0_100),
    ),
    "am-follower-6dof": (
        ("shoulder_pan", 1, "sts3095", None),
        ("shoulder_lift", 2, "sts3095", None),
        ("elbow_flex", 3, "sts3095", None),
        ("wrist_flex", 4, "sts3215", None),
        ("wrist_yaw", 5, "sts3215", None),
        ("wrist_roll", 6, "sts3215", None),
        ("gripper", 7, "sts3215", MotorNormMode.RANGE_0_100),
    ),
    "am-follower-6dof-hd": (
        ("shoulder_pan", 1, "sts3250", None),
        ("shoulder_lift", 2, "sts3095", None),
        ("elbow_flex", 3, "sts3095", None),
        ("wrist_flex", 4, "sts3250", None),
        ("wrist_yaw", 5, "sts3250", None),
        ("wrist_roll", 6, "sts3250", None),
        ("gripper", 7, "sts3250", MotorNormMode.RANGE_0_100),
    ),
}


def _make_arm_motors(
    prefix: str, arm_profile: str, norm_mode_body: MotorNormMode
) -> dict[str, Motor]:
    if arm_profile not in _ARM_PROFILES:
        raise ValueError(
            f"Unknown arm_profile '{arm_profile}'. Expected one of: {list(_ARM_PROFILES.keys())}."
        )

    return {
        f"{prefix}_{joint}": Motor(motor_id, model, norm_mode or norm_mode_body)
        for joint, motor_id, model, norm_mode in _ARM_PROFILES[arm_profile]
    }


class AlohaMini(Robot):
    """
    The robot includes a three omniwheel mobile base and a remote follower arm.
    The leader arm is connected locally (on the laptop) and its joint positions are recorded and then
    forwarded to the remote follower arm (after applying a safety clamp).
    In parallel, keyboard teleoperation is used to generate raw velocity commands for the wheels.
    """

    config_class = AlohaMiniConfig
    name = "alohamini"

    def __init__(self, config: AlohaMiniConfig):
        super().__init__(config)
        self.config = config
        self.logs: dict[str, Any] = {}
        norm_mode_body = MotorNormMode.DEGREES if config.use_degrees else MotorNormMode.RANGE_M100_100

        specs = validate_robot_model(config.robot_model)
        if config.diagnostic_lift_only and (
            config.robot_model != "alohamini1" or not config.no_follower
        ):
            raise ValueError(
                "diagnostic_lift_only requires Aloha Mini 1 with follower arms disabled."
            )
        arm_profile = specs["arm_profile"]
        bm = specs["base_motor"]
        lm = specs["lift_motor"]
        self.wheel_radius = specs["wheel_radius"]
        self.base_radius = specs["base_radius"]

        left_arm_motors_cfg = _make_arm_motors("arm_left", arm_profile, norm_mode_body)
        right_arm_motors_cfg = _make_arm_motors("arm_right", arm_profile, norm_mode_body)
        self._left_arm_state_keys, self._right_arm_state_keys = arm_state_keys_for_robot_model(
            config.robot_model
        )

        left_bus_motors = {
            **(left_arm_motors_cfg if not config.no_follower else {}),
            **(
                {
                    "base_left_wheel": Motor(8, bm, MotorNormMode.RANGE_M100_100),
                    "base_back_wheel": Motor(9, bm, MotorNormMode.RANGE_M100_100),
                    "base_right_wheel": Motor(10, bm, MotorNormMode.RANGE_M100_100),
                }
                if not config.diagnostic_lift_only
                else {}
            ),
            "lift_axis": Motor(11, lm, MotorNormMode.DEGREES),
        }
        left_bus_calibration = {
            name: calibration
            for name, calibration in self.calibration.items()
            if name in left_bus_motors
        }
        self.left_bus = FeetechMotorsBus(
            port=self.config.left_port,
            motors=left_bus_motors,
            calibration=left_bus_calibration,
        )

        if not config.no_follower:
            right_bus_calibration = {
                name: calibration
                for name, calibration in self.calibration.items()
                if name in right_arm_motors_cfg
            }
            self.right_bus = FeetechMotorsBus(
                port=self.config.right_port,
                motors=right_arm_motors_cfg,
                calibration=right_bus_calibration,
            )
        else:
            self.right_bus = None

        if config.no_follower:
            self.left_arm_motors = []
            self.right_arm_motors = []
            self._left_arm_state_keys = ()
            self._right_arm_state_keys = ()
        else:
            self.left_arm_motors  = [m for m in self.left_bus.motors        if m.startswith("arm_left_")]
            self.right_arm_motors = [m for m in self.right_bus.motors if m.startswith("arm_right_")]

        self.base_motors = [m for m in self.left_bus.motors if m.startswith("base_")]

        # self.arm_motors = [motor for motor in self.left_bus.motors if motor.startswith("arm")]
        # self.base_motors = [motor for motor in self.left_bus.motors if motor.startswith("base")]

        self.cameras = make_cameras_from_configs(config.cameras)


        lift_config = LiftAxisConfig(
            lead_mm_per_rev=specs["lead_mm_per_rev"],
            motor_model=lm,
            leave_torque_enabled_after_home=config.robot_model == "alohamini1",
        )
        if config.robot_model == "alohamini1":
            lift_config.home_down_speed = 200
            lift_config.home_timeout_s = 20.0
        self.lift = LiftAxis(
            lift_config,
            bus_left=self.left_bus,
            bus_right=self.right_bus,
        )
        # Overcurrent debounce: require N consecutive over-limit reads
        self._overcurrent_count: dict[str, int] = {}
        self._overcurrent_trip_n = 20
        self._last_currents_log_t = 0.0
        self._gripper_current_limit_ma = 500.0
        # Nudge the held position slightly further closed than present, so the gripper
        # keeps a bit of squeeze (a small resting current) instead of fully relaxing to 0mA.
        self._gripper_hold_close_step = 3
        # How far the caller's own goal must move past the held position, in the open
        # direction, before we treat it as "the user wants to open" and let go.
        self._gripper_release_margin = 1.0
        # Fixed by mechanical installation, not measured at runtime: +1.0 means increasing
        # raw position opens the gripper. Flip a motor's sign here if it holds/releases
        # the wrong way.
        self._gripper_open_direction: dict[str, float] = {
            "arm_left_gripper": 1.0,
            "arm_right_gripper": 1.0,
        }
        self._gripper_hold_goal: dict[str, float] = {}
        self._gripper_hold_direction: dict[str, float] = {}

        # Same idea as the gripper protection above, applied to the rest of the arm
        # joints (shoulder/elbow/wrist, in degrees). Unlike the gripper there's no fixed
        # "open direction" -- a joint can be pushed into an obstacle from either side
        # depending on the motion, so the retreat direction is inferred per contact
        # episode instead of configured. And unlike the gripper, we don't want to keep
        # nudging force into whatever it hit -- this is collision protection, not grip
        # force, so it just freezes at the held position.
        self._joint_current_limit_ma = 1800.0
        self._joint_release_margin = 1.0
        self._joint_hold_goal: dict[str, float] = {}
        self._joint_hold_direction: dict[str, float] = {}

    @property
    def _state_ft(self) -> dict[str, type]:
        return dict.fromkeys(
            (
                *self._left_arm_state_keys,
                *self._right_arm_state_keys,
                "x.vel",
                "y.vel",
                "theta.vel",
                "lift_axis.height_mm",   # new
                #"lift_axis.vel",         # new (optional, for debugging)
            ),
            float,
        )

    @property
    def _cameras_ft(self) -> dict[str, tuple]:
        return {
            cam: (self.config.cameras[cam].height, self.config.cameras[cam].width, 3) for cam in self.cameras
        }

    @cached_property
    def observation_features(self) -> dict[str, type | tuple]:
        return {**self._state_ft, **self._cameras_ft}

    @cached_property
    def action_features(self) -> dict[str, type]:
        return self._state_ft

    # @property
    # def is_connected(self) -> bool:
    #     return self.left_bus.is_connected and all(cam.is_connected for cam in self.cameras.values())
    
    @property
    def is_connected(self) -> bool:
        cams_ok = all(cam.is_connected for cam in self.cameras.values())
        return self.left_bus.is_connected and (self.right_bus.is_connected if self.right_bus else True) and cams_ok

    @check_if_already_connected
    def connect(
        self,
        calibrate: bool = True,
        *,
        activate: bool = True,
        home_lift: bool = True,
    ) -> None:
        """Connect, configure with torque off, then explicitly activate safe goals."""
        try:
            self.left_bus.connect()
            if self.right_bus:
                self.right_bus.connect()

            # Configuration is deliberately separate from activation. It leaves every
            # motor torque-disabled while modes, gains, and acceleration are changed.
            self.configure()
            if calibrate and not self.is_calibrated:
                logger.info(
                    "Mismatch between calibration values in the motor and the calibration file "
                    "or no calibration file found"
                )
                self.calibrate()
                self.configure()

            for cam in self.cameras.values():
                cam.connect()

            if activate:
                should_home_lift = home_lift and self.is_calibrated
                if home_lift and not should_home_lift:
                    logger.info("Skipping lift homing because AlohaMini is not calibrated.")
                if self.config.robot_model == "alohamini1":
                    self.activate_motors(home_lift=should_home_lift)
                elif should_home_lift:
                    # Keep Aloha Mini 2/2 Pro's legacy arm/base activation behavior.
                    self.lift.home()
            elif home_lift:
                logger.info("Motor activation is disabled; lift homing was not run.")

            logger.info("%s connected.", self)
        except BaseException:
            self._safe_shutdown(close_buses=True, recover_interrupted_bus_io=True)
            raise

    @property
    def is_calibrated(self) -> bool:
        return self.left_bus.is_calibrated and (
            self.right_bus.is_calibrated if self.right_bus else True
        )

    def calibrate(self) -> None:
        """
        Dual-arm calibration (left arm + chassis on self.left_bus, right arm on self.right_bus):
        - Left arm: position mode → half-turn homing → collect ROM
        - Chassis: no homing; ROM fixed to 0–4095
        - Right arm (if present): position mode → half-turn homing → collect ROM
        - Merge into a single self.calibration, split by bus, write back to both buses, and save
        """
        # If a calibration file already exists: load it and write back, filtering for each bus separately
        if self.calibration:
            user_input = input(
                f"Press ENTER to use provided calibration file associated with the id {self.id}, "
                f"or type 'c' and press ENTER to run calibration: "
            )
            if user_input.strip().lower() != "c":
                logger.info("Writing existing calibration to both buses (trim per-bus caches)")

                calib_left = {k: v for k, v in self.calibration.items() if k in self.left_bus.motors}
                self.left_bus.write_calibration(calib_left, cache=False)
                self.left_bus.calibration = calib_left

                if getattr(self, "right_bus", None):
                    calib_right = {k: v for k, v in self.calibration.items() if k in self.right_bus.motors}
                    self.right_bus.write_calibration(calib_right, cache=False)
                    self.right_bus.calibration = calib_right

                return

        logger.info(f"\nRunning calibration of {self} (dual-bus if right_bus present)")

        if self.config.no_follower:
            logger.info("no_follower mode: writing default base/lift calibration.")
            self.calibration = {}
            for name, motor in self.left_bus.motors.items():
                self.calibration[name] = MotorCalibration(
                    id=motor.id,
                    drive_mode=0,
                    homing_offset=0,
                    range_min=0,
                    range_max=4095,
                )

            calib_left = {k: v for k, v in self.calibration.items() if k in self.left_bus.motors}
            self.left_bus.write_calibration(calib_left, cache=False)
            self.left_bus.calibration = calib_left
            self._save_calibration()
            print("Calibration saved to", self.calibration_fpath)
            return

        if not getattr(self, "left_arm_motors", None):
            raise RuntimeError("left_arm_motors is empty; expected names starting with 'left_arm_'")

        set_torque_enabled(self.left_bus, self.left_arm_motors, enabled=False)
        for name in self.left_arm_motors:
            self.left_bus.write("Operating_Mode", name, OperatingMode.POSITION.value)

        input("Move LEFT arm to the middle of its range of motion, then press ENTER...")
        left_homing = self.left_bus.set_half_turn_homings(self.left_arm_motors)  # left arm only

        for wheel in self.base_motors:
            left_homing[wheel] = 0

        motors_left_all = self.left_arm_motors + self.base_motors
        left_full_turn_motor = "arm_left_wrist_roll"
        full_turn_left = [m for m in motors_left_all if m.startswith("base_")]  # three base wheels
        if left_full_turn_motor in motors_left_all:
            full_turn_left.append(left_full_turn_motor)
        unknown_left = [m for m in motors_left_all if m not in full_turn_left]

        print(
            f"Move LEFT arm joints sequentially through full ROM (except '{left_full_turn_motor}'). "
            "Press ENTER to stop..."
        )
        l_mins, l_maxs = self.left_bus.record_ranges_of_motion(unknown_left)
        for m in full_turn_left:
            l_mins[m] = 0
            l_maxs[m] = 4095

        right_homing = {}
        r_mins, r_maxs = {}, {}

        if getattr(self, "right_bus", None) and getattr(self, "right_arm_motors", None):
            set_torque_enabled(self.right_bus, self.right_arm_motors, enabled=False)
            for name in self.right_arm_motors:
                self.right_bus.write("Operating_Mode", name, OperatingMode.POSITION.value)

            input("Move RIGHT arm to the middle of its range of motion, then press ENTER...")
            right_homing = self.right_bus.set_half_turn_homings(self.right_arm_motors)

            right_full_turn_motor = "arm_right_wrist_roll"
            full_turn_right = [right_full_turn_motor] if right_full_turn_motor in self.right_arm_motors else []
            unknown_right = [m for m in self.right_arm_motors if m not in full_turn_right]

            print(
                f"Move RIGHT arm joints sequentially through full ROM (except '{right_full_turn_motor}'). "
                "Press ENTER to stop..."
            )
            r_mins, r_maxs = self.right_bus.record_ranges_of_motion(unknown_right)
            for m in full_turn_right:
                r_mins[m] = 0
                r_maxs[m] = 4095

        # Merge → filter by bus and write back → save as a single file
        self.calibration = {}

        for name, motor in self.left_bus.motors.items():
            self.calibration[name] = MotorCalibration(
                id=motor.id,
                drive_mode=0,
                homing_offset=left_homing.get(name, 0),
                range_min=l_mins.get(name, 0),
                range_max=l_maxs.get(name, 4095),
            )

        if getattr(self, "right_bus", None):
            for name, motor in self.right_bus.motors.items():
                self.calibration[name] = MotorCalibration(
                    id=motor.id,
                    drive_mode=0,
                    homing_offset=right_homing.get(name, 0),
                    range_min=r_mins.get(name, 0),
                    range_max=r_maxs.get(name, 4095),
                )

        # Write back: each bus only writes its own entries to avoid KeyError
        calib_left = {k: v for k, v in self.calibration.items() if k in self.left_bus.motors}
        self.left_bus.write_calibration(calib_left, cache=False)
        self.left_bus.calibration = calib_left

        if getattr(self, "right_bus", None):
            calib_right = {k: v for k, v in self.calibration.items() if k in self.right_bus.motors}
            self.right_bus.write_calibration(calib_right, cache=False)
            self.right_bus.calibration = calib_right

        self._save_calibration()
        print("Calibration saved to", self.calibration_fpath)





    def _configure_bus_defaults(self, bus: FeetechMotorsBus) -> None:
        if self.config.robot_model != "alohamini1":
            bus.configure_motors()
            return

        # FeetechMotorsBus.configure_motors() may clear a Phase bit on STS3215.
        # Aloha Mini 1's established motor Phase must remain untouched, so apply the
        # same runtime delay/acceleration profile explicitly without reading or writing Phase.
        for name in bus.motors:
            write_register(bus, "Return_Delay_Time", name, 0)
            write_register(bus, "Maximum_Acceleration", name, 254)
            write_register(bus, "Acceleration", name, 254)

    def configure(self) -> None:
        """Configure motor modes and gains while leaving all torque disabled."""
        set_torque_enabled(self.left_bus, self.left_bus.motors, enabled=False)
        previous_trial = getattr(self, "_am1_shoulder_integral_trial", None)
        if previous_trial is not None:
            previous_trial.restore(self)
        self._configure_bus_defaults(self.left_bus)
        for name in self.left_arm_motors:
            write_register(self.left_bus, "Operating_Mode", name, OperatingMode.POSITION.value)
            # Preserve the existing Aloha Mini follower-arm PID profile.
            write_register(self.left_bus, "P_Coefficient", name, 16)
            write_register(self.left_bus, "I_Coefficient", name, 0)
            write_register(self.left_bus, "D_Coefficient", name, 32)

        for name in self.base_motors:
            write_register(self.left_bus, "Operating_Mode", name, OperatingMode.VELOCITY.value)

        if self.right_bus:
            set_torque_enabled(self.right_bus, self.right_bus.motors, enabled=False)
            self._configure_bus_defaults(self.right_bus)
            for name in self.right_arm_motors:
                write_register(self.right_bus, "Operating_Mode", name, OperatingMode.POSITION.value)
                write_register(self.right_bus, "P_Coefficient", name, 16)
                write_register(self.right_bus, "I_Coefficient", name, 0)
                write_register(self.right_bus, "D_Coefficient", name, 32)

        self.lift.configure(force=True)

        # Retain the trial before its first write, including partially applied failures.
        self._am1_shoulder_integral_trial = AM1ShoulderIntegralTrial()
        self._am1_shoulder_integral_trial.configure(self)

    def _seed_arm_goals(self, bus: FeetechMotorsBus, motors: list[str]) -> None:
        present_positions = {
            name: int(
                bus.read(
                    "Present_Position",
                    name,
                    normalize=False,
                    num_retry=REGISTER_RETRIES,
                )
            )
            for name in motors
        }
        for name, present_raw in present_positions.items():
            write_register(bus, "Goal_Position", name, present_raw)

    def _seed_activation_goals(self) -> None:
        self._seed_arm_goals(self.left_bus, self.left_arm_motors)
        if self.right_bus:
            self._seed_arm_goals(self.right_bus, self.right_arm_motors)

        for name in (*self.base_motors, self.lift.cfg.name):
            write_register(self.left_bus, "Goal_Velocity", name, 0)

    AM1_ACTIVATION_MAX_RAW_AGE_S = 1.0

    def _require_am1_activation_fresh(self, phase: str) -> None:
        expected = len(self.left_arm_motors) + len(self.right_arm_motors)
        if not expected:
            return
        samples = [row for row in self._am1_activation_readbacks
                   if row["phase"] == phase and row["register"] == "Present_Position"]
        now = time.monotonic()
        if len(samples) != expected or any("unavailable" in row for row in samples):
            raise RuntimeError("AM1 arm activation refused: complete raw position vector is unavailable.")
        earliest = min(row["read_started_at"] for row in samples)
        if (not math.isfinite(now) or not math.isfinite(earliest)
            or now < max(row["read_completed_at"] for row in samples)
            or now - earliest >= self.AM1_ACTIVATION_MAX_RAW_AGE_S):
            raise RuntimeError("AM1 arm activation refused: original raw position vector expired (>=1 s).")

    def _am1_activation_calibration(self, bus: FeetechMotorsBus, name: str) -> tuple[int, int, int, int, int]:
        calibration = getattr(bus, "calibration", {}).get(name)
        if calibration is None or name not in bus.motors:
            raise RuntimeError(f"AM1 arm activation refused: missing calibration or motor mapping for '{name}'.")
        values = (calibration.id, calibration.drive_mode, calibration.homing_offset,
                  calibration.range_min, calibration.range_max)
        if any(isinstance(value, bool) or not isinstance(value, (int, np.integer)) for value in values):
            raise RuntimeError(f"AM1 arm activation refused: malformed calibration for '{name}'.")
        identity = tuple(int(value) for value in values)
        if identity[0] != bus.motors[name].id or identity[1] not in (0, 1) or identity[3] >= identity[4]:
            raise RuntimeError(f"AM1 arm activation refused: invalid calibration or motor mapping for '{name}'.")
        return identity

    def _read_am1_activation_raw(self, bus: FeetechMotorsBus, register: str, name: str, *, phase: str) -> int:
        started_at = time.monotonic()
        record = {"phase": phase, "motor": name, "register": register, "read_started_at": started_at}
        try:
            value = bus.read(register, name, normalize=False, num_retry=REGISTER_RETRIES)
            record["raw"] = value
            if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
                raise RuntimeError(f"AM1 arm activation refused: malformed raw {register} for '{name}'.")
            return int(value)
        except BaseException as error:
            record["unavailable"] = type(error).__name__
            raise
        finally:
            record["read_completed_at"] = time.monotonic()
            # Original startup acquisitions are diagnostic evidence only. Native
            # admission and live feedback continue to require their own new reads.
            self._am1_activation_readbacks.append(record)

    def _qualify_am1_activation_ranges(
        self,
    ) -> list[tuple[FeetechMotorsBus, dict[str, tuple[int, int, int, int, int]]]]:
        qualified = []
        for bus, names in ((self.left_bus, self.left_arm_motors), (self.right_bus, self.right_arm_motors)):
            if bus is None:
                continue
            limits = {}
            for name in names:
                calibration = self._am1_activation_calibration(bus, name)
                for register, expected in (("Min_Position_Limit", calibration[3]),
                                           ("Max_Position_Limit", calibration[4]),
                                           ("Homing_Offset", calibration[2])):
                    actual = self._read_am1_activation_raw(bus, register, name, phase="before_home")
                    if actual != expected:
                        raise RuntimeError(
                            f"AM1 arm activation refused: '{name}' {register} differs from cached calibration."
                        )
                self._qualify_am1_activation_position(bus, name, calibration, phase="before_home")
                limits[name] = calibration
            qualified.append((bus, limits))
        return qualified

    def _qualify_am1_activation_position(
        self, bus: FeetechMotorsBus, name: str, calibration: tuple[int, int, int, int, int], *, phase: str,
    ) -> int:
        torque = self._read_am1_activation_raw(bus, "Torque_Enable", name, phase=phase)
        if torque != 0:
            raise RuntimeError(f"AM1 arm activation refused: '{name}' torque must be disabled before qualification.")
        present = self._read_am1_activation_raw(bus, "Present_Position", name, phase=phase)
        if not calibration[3] <= present <= calibration[4]:
            raise RuntimeError(
                f"AM1 arm activation refused: '{name}' raw position {present} is outside verified "
                f"calibration limits {calibration[3]}..{calibration[4]}."
            )
        return present

    def _seed_qualified_am1_activation_goals(
        self, qualified: list[tuple[FeetechMotorsBus, dict[str, tuple[int, int, int, int, int]]]],
    ) -> None:
        all_goals = []
        for bus, limits in qualified:
            present_positions = {}
            for name, calibration in limits.items():
                if self._am1_activation_calibration(bus, name) != calibration:
                    raise RuntimeError(f"AM1 arm activation refused: '{name}' calibration changed during lift home.")
                present_positions[name] = self._qualify_am1_activation_position(
                    bus, name, calibration, phase="after_home",
                )
            all_goals.append((bus, present_positions))
        self._require_am1_activation_fresh("after_home")
        # Both buses qualify before the first goal write, then every seeded goal
        # is verified before the first arm/base torque enable. Do not command an
        # EEPROM boundary as a substitute for an unrepresentable measured hold.
        for bus, present_positions in all_goals:
            for name, present in present_positions.items():
                write_register(bus, "Goal_Position", name, present)
        for bus, present_positions in all_goals:
            for name, expected in present_positions.items():
                actual = self._read_am1_activation_raw(bus, "Goal_Position", name, phase="seed_readback")
                if actual != expected:
                    raise RuntimeError(
                        f"AM1 arm activation refused: '{name}' measured goal readback {actual} differs from {expected}."
                    )
        for name in (*self.base_motors, self.lift.cfg.name):
            write_register(self.left_bus, "Goal_Velocity", name, 0)

    def activate_motors(self, *, home_lift: bool = True) -> LiftHomeResult | None:
        """Seed stationary goals, optionally home the lift, and enable normal motors."""
        home_result = None
        try:
            qualified = None
            if self.config.robot_model == "alohamini1":
                self._am1_activation_readbacks = []
                # Reject an unrepresentable arm rest before even powered lift
                # home. This owner's home does not change any arm calibration.
                qualified = self._qualify_am1_activation_ranges()
                self._require_am1_activation_fresh("before_home")
            if home_lift:
                if self.config.robot_model == "alohamini1":
                    from .lift_operational import OperationalLift

                    self._lift_operation = OperationalLift(self)
                    # The same single lift encoder owner is used from home through
                    # ordinary control; other motors retain their existing bus.
                    self.lift = self._lift_operation.lift
                    home_result = self._lift_operation.start()
                else:
                    home_result = self.lift.home()

            # Read arm positions after homing, while the arms are still torque-free, so
            # their hold goals cannot become stale during the bounded lift movement.
            if qualified is None:
                self._seed_activation_goals()
            else:
                self._seed_qualified_am1_activation_goals(qualified)
            if qualified is None:
                set_torque_enabled(
                    self.left_bus,
                    (*self.left_arm_motors, *self.base_motors),
                    enabled=True,
                )
                if self.right_bus:
                    set_torque_enabled(self.right_bus, self.right_arm_motors, enabled=True)
            else:
                for bus, names in ((self.left_bus, (*self.left_arm_motors, *self.base_motors)),
                                   (self.right_bus, self.right_arm_motors)):
                    if bus is None:
                        continue
                    for name in names:
                        self._require_am1_activation_fresh("after_home")
                        set_torque_enabled(bus, (name,), enabled=True)
        except BaseException as error:
            cleanup_errors = self._safe_shutdown(close_buses=True, recover_interrupted_bus_io=True)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            detail = f" Cleanup issues: {'; '.join(cleanup_errors)}" if cleanup_errors else ""
            raise RuntimeError(f"AlohaMini motor activation failed.{detail}") from error

        return home_result

    def _safe_shutdown(
        self,
        *,
        close_buses: bool,
        recover_interrupted_bus_io: bool = False,
        motor_shutdown_check: Callable[[], None] | None = None,
    ) -> list[str]:
        """Best-effort zero, torque-off, camera close, and optional bus close.

        ``recover_interrupted_bus_io`` is only for a caller that has already left
        normal motor I/O after an exception. The Feetech SDK can otherwise retain
        its single-transaction busy flag when ``KeyboardInterrupt`` escapes a read.
        """
        self._am1_shutdown_in_progress = True
        errors: list[str] = []
        previous_evidence = getattr(self, "_am1_shutdown_evidence", None)
        already_closed = not any(bus is not None and bus.is_connected for bus in (self.left_bus, self.right_bus))
        if getattr(self, "_am1_protected_cleanup", False) and already_closed and previous_evidence is not None:
            errors.extend(previous_evidence.get("cleanup_errors", []))

        if recover_interrupted_bus_io and self.config.robot_model == "alohamini1":
            for bus_name, bus in (("left", self.left_bus), ("right", self.right_bus)):
                if bus is None or not bus.is_connected:
                    continue
                port_handler = getattr(bus, "port_handler", None)
                if port_handler is None or not getattr(port_handler, "is_using", False):
                    continue
                try:
                    port_handler.clearPort()
                    port_handler.is_using = False
                    logger.warning(
                        "Recovered abandoned %s Feetech transaction before AM1 shutdown.",
                        bus_name,
                    )
                except Exception as error:
                    errors.append(f"recover {bus_name} bus transaction: {error}")

        if self.left_bus.is_connected:
            for name in (*self.base_motors, self.lift.cfg.name):
                try:
                    write_register(self.left_bus, "Goal_Velocity", name, 0)
                except Exception as error:
                    errors.append(f"zero {name}: {error}")

        for bus_name, bus in (("left", self.left_bus), ("right", self.right_bus)):
            if bus is None or not bus.is_connected:
                continue
            for name in bus.motors:
                try:
                    set_torque_enabled(bus, (name,), enabled=False)
                except Exception as error:
                    errors.append(f"disable {bus_name}/{name}: {error}")

        integral_trial = getattr(self, "_am1_shoulder_integral_trial", None)
        if integral_trial is not None:
            try:
                integral_trial.restore(self)
            except BaseException as error:
                errors.append(f"restore AM1 left shoulder integral: {type(error).__name__}: {error}")

        operation = getattr(self, "_lift_operation", None)
        if motor_shutdown_check is None and operation is not None and self.left_bus.is_connected:
            motor_shutdown_check = operation.cleanup_readback
        if motor_shutdown_check is not None:
            try:
                motor_shutdown_check()
            except Exception as error:
                errors.append(f"verify final motor shutdown state: {error}")

        if getattr(self, "_am1_protected_cleanup", False) and not (already_closed and previous_evidence is not None):
            evidence = {"verified": False, "readbacks": [], "started_at_monotonic_s": time.monotonic()}
            self._am1_shutdown_evidence = evidence
            for bus_name, bus in (("left", self.left_bus), ("right", self.right_bus)):
                if bus is None and bus_name == "right" and getattr(self.config, "no_follower", False):
                    continue  # This legacy body/lift owner never configured right-arm motors.
                if bus is None or not bus.is_connected:
                    errors.append(f"AM1 cleanup readback: {bus_name} bus unavailable")
                    continue
                registers = [("Torque_Enable", name) for name in bus.motors]
                if bus is self.left_bus:
                    registers += [("Goal_Velocity", name) for name in (*self.base_motors, self.lift.cfg.name)
                                  if name in bus.motors]
                for register, name in registers:
                    row = {"bus": bus_name, "motor": name, "register": register,
                           "read_started_at_monotonic_s": time.monotonic()}
                    evidence["readbacks"].append(row)
                    try:
                        value = bus.read(register, name, normalize=False, num_retry=REGISTER_RETRIES)
                        if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
                            raise RuntimeError("shutdown readback was not a raw integer")
                        row["value"] = int(value)
                        if value != 0:
                            raise RuntimeError(f"shutdown {register} remained {value}; expected zero")
                    except Exception as error:
                        row["error"] = f"{type(error).__name__}: {error}"
                        errors.append(f"AM1 cleanup readback {bus_name}/{name}/{register}: {error}")
                    finally:
                        row["read_completed_at_monotonic_s"] = time.monotonic()
            evidence["lift_readback"] = "qualified" if operation is not None and not errors else "unavailable-or-failed"
            evidence["selected_gain_restore_required"] = bool(
                integral_trial is not None and integral_trial.restore_required
            )
            evidence["completed_at_monotonic_s"] = time.monotonic()
            evidence["verified"] = not errors

        for name, camera in self.cameras.items():
            if not camera.is_connected:
                continue
            try:
                camera.disconnect()
            except Exception as error:
                errors.append(f"close camera {name}: {error}")

        if close_buses:
            for bus_name, bus in (("right", self.right_bus), ("left", self.left_bus)):
                if bus is None or not bus.is_connected:
                    continue
                try:
                    bus.disconnect(disable_torque=False)
                except Exception as error:
                    errors.append(f"close {bus_name} bus: {error}")

        self.lift.mark_unhomed()
        if getattr(self, "_am1_protected_cleanup", False):
            self._am1_shutdown_evidence["cleanup_errors"] = list(errors)
            self._am1_shutdown_evidence["buses_closed"] = close_buses and not any(
                bus is not None and bus.is_connected for bus in (self.left_bus, self.right_bus)
            )
            self._am1_shutdown_evidence["verified"] = not errors
        for error in errors:
            logger.error("AlohaMini shutdown issue: %s", error)
        return errors


    def setup_motors(self) -> None:
        for motor in chain(reversed(self.arm_motors), reversed(self.base_motors)):
            input(f"Connect the controller board to the '{motor}' motor only and press enter.")
            self.left_bus.setup_motor(motor)
            print(f"'{motor}' motor id set to {self.left_bus.motors[motor].id}")

    @staticmethod
    def _degps_to_raw(degps: float) -> int:
        steps_per_deg = 4096.0 / 360.0
        speed_in_steps = degps * steps_per_deg
        speed_int = int(round(speed_in_steps))
        # Cap the value to fit within signed 16-bit range (-32768 to 32767)
        if speed_int > 0x7FFF:
            speed_int = 0x7FFF  # 32767 -> maximum positive value
        elif speed_int < -0x8000:
            speed_int = -0x8000  # -32768 -> minimum negative value
        return speed_int

    @staticmethod
    def _raw_to_degps(raw_speed: int) -> float:
        steps_per_deg = 4096.0 / 360.0
        magnitude = raw_speed
        degps = magnitude / steps_per_deg
        return degps

    def _body_to_wheel_raw(
        self,
        x: float,
        y: float,
        theta: float,
        wheel_radius: float | None = None,
        base_radius: float | None = None,
        max_raw: int = 3000,
    ) -> dict:
        """
        Convert desired body-frame velocities into wheel raw commands.

        Parameters:
          x_cmd      : Linear velocity in x (m/s).
          y_cmd      : Linear velocity in y (m/s).
          theta_cmd  : Rotational velocity (deg/s).
          wheel_radius: Radius of each wheel (meters).
          base_radius : Distance from the center of rotation to each wheel (meters).
          max_raw    : Maximum allowed raw command (ticks) per wheel.

        Returns:
          A dictionary with wheel raw commands:
             {"base_left_wheel": value, "base_back_wheel": value, "base_right_wheel": value}.

        Notes:
          - Internally, the method converts theta_cmd to rad/s for the kinematics.
          - The raw command is computed from the wheels angular speed in deg/s
            using _degps_to_raw(). If any command exceeds max_raw, all commands
            are scaled down proportionally.
        """
        wheel_radius = self.wheel_radius if wheel_radius is None else wheel_radius
        base_radius = self.base_radius if base_radius is None else base_radius

        # Convert rotational velocity from deg/s to rad/s.
        theta_rad = theta * (np.pi / 180.0)
        # Create the body velocity vector [x, y, theta_rad].
        velocity_vector = np.array([-x, -y, theta_rad])

        # Define the wheel mounting angles with a -90° offset.
        angles = np.radians(np.array([240, 0, 120]) - 90)
        # Build the kinematic matrix: each row maps body velocities to a wheel’s linear speed.
        # The third column (base_radius) accounts for the effect of rotation.
        m = np.array([[np.cos(a), np.sin(a), base_radius] for a in angles])

        # Compute each wheel’s linear speed (m/s) and then its angular speed (rad/s).
        wheel_linear_speeds = m.dot(velocity_vector)
        wheel_angular_speeds = wheel_linear_speeds / wheel_radius

        # Convert wheel angular speeds from rad/s to deg/s.
        wheel_degps = wheel_angular_speeds * (180.0 / np.pi)

        # Scaling
        steps_per_deg = 4096.0 / 360.0
        raw_floats = [abs(degps) * steps_per_deg for degps in wheel_degps]
        max_raw_computed = max(raw_floats)
        if max_raw_computed > max_raw:
            scale = max_raw / max_raw_computed
            wheel_degps = wheel_degps * scale

        # Convert each wheel’s angular speed (deg/s) to a raw integer.
        wheel_raw = [self._degps_to_raw(deg) for deg in wheel_degps]

        return {
            "base_left_wheel": wheel_raw[0],
            "base_back_wheel": wheel_raw[1],
            "base_right_wheel": wheel_raw[2],
        }

    def _wheel_raw_to_body(
        self,
        left_wheel_speed,
        back_wheel_speed,
        right_wheel_speed,
        wheel_radius: float | None = None,
        base_radius: float | None = None,
    ) -> dict[str, Any]:
        """
        Convert wheel raw command feedback back into body-frame velocities.

        Parameters:
          wheel_raw   : Vector with raw wheel commands ("base_left_wheel", "base_back_wheel", "base_right_wheel").
          wheel_radius: Radius of each wheel (meters).
          base_radius : Distance from the robot center to each wheel (meters).

        Returns:
          A dict (x.vel, y.vel, theta.vel) all in m/s
        """
        wheel_radius = self.wheel_radius if wheel_radius is None else wheel_radius
        base_radius = self.base_radius if base_radius is None else base_radius

        # Convert each raw command back to an angular speed in deg/s.
        wheel_degps = np.array(
            [
                self._raw_to_degps(left_wheel_speed),
                self._raw_to_degps(back_wheel_speed),
                self._raw_to_degps(right_wheel_speed),
            ]
        )

        # Convert from deg/s to rad/s.
        wheel_radps = wheel_degps * (np.pi / 180.0)
        # Compute each wheel’s linear speed (m/s) from its angular speed.
        wheel_linear_speeds = wheel_radps * wheel_radius

        # Define the wheel mounting angles with a -90° offset.
        angles = np.radians(np.array([240, 0, 120]) - 90)
        m = np.array([[np.cos(a), np.sin(a), base_radius] for a in angles])

        # Solve the inverse kinematics: body_velocity = M⁻¹ · wheel_linear_speeds.
        m_inv = np.linalg.inv(m)
        velocity_vector = m_inv.dot(wheel_linear_speeds)
        x, y, theta_rad = velocity_vector
        
        theta = theta_rad * (180.0 / np.pi)
        return {
            "x.vel": -x,
            "y.vel": -y,
            "theta.vel": theta,
        }  # m/s and deg/s
    
    def _raw_to_ma(raw):
        try:
            return float(raw) * 6.5
        except Exception:
            return 0.0
        
    def _normalize_arm_feedback(self, bus: FeetechMotorsBus, raw: dict[str, int]) -> dict[str, float]:
        """Keep measured AM1 positions truthful beyond the calibrated command range."""
        ids = {bus.motors[name].id: value for name, value in raw.items()}
        normalized = bus._normalize(ids)  # retain calibration validation and degree units
        for name, value in raw.items():
            motor = bus.motors[name]
            calibration = bus.calibration[name]
            drive_mode = bus.apply_drive_mode and calibration.drive_mode
            if motor.norm_mode is MotorNormMode.RANGE_M100_100:
                position = (value - calibration.range_min) / (
                    calibration.range_max - calibration.range_min
                ) * 200 - 100
                normalized[motor.id] = -position if drive_mode else position
            elif motor.norm_mode is MotorNormMode.RANGE_0_100:
                position = (value - calibration.range_min) / (
                    calibration.range_max - calibration.range_min
                ) * 100
                normalized[motor.id] = 100 - position if drive_mode else position
        return {name: normalized[bus.motors[name].id] for name in raw}

    def _read_arm_positions(
        self, bus: FeetechMotorsBus, motors: list[str], *, raw_positions: dict[str, int] | None = None,
    ) -> dict[str, float]:
        if getattr(self.config, "robot_model", None) != "alohamini1":
            return bus.sync_read("Present_Position", motors)
        raw = bus.sync_read("Present_Position", motors, normalize=False)
        if raw_positions is not None:
            raw_positions.update(raw)
        return self._normalize_arm_feedback(bus, raw)

    def _encode_am1_arm_goals(
        self, bus: FeetechMotorsBus, goals: dict[str, float], present_raw: dict[str, int],
    ) -> tuple[dict[str, float], dict[str, int] | None]:
        """Encode once, rounding inward so existing protections survive conversion."""
        if getattr(self.config, "robot_model", None) != "alohamini1" or not goals:
            return goals, None
        raw_goals = bus._unnormalize({
            bus.motors[key.removesuffix(".pos")].id: value for key, value in goals.items()
        })
        for key, goal in goals.items():
            motor = key.removesuffix(".pos")
            model = bus.motors[motor]
            calibration = bus.calibration[motor]
            mode = model.norm_mode
            bounds = (-100.0, 100.0) if mode is MotorNormMode.RANGE_M100_100 else (
                (0.0, 100.0) if mode is MotorNormMode.RANGE_0_100 else None
            )
            holds = self._gripper_hold_goal if motor.endswith("_gripper") else self._joint_hold_goal
            protected_hold = motor in holds and goal == holds[motor]
            if protected_hold and bounds and not bounds[0] <= goal <= bounds[1]:
                raise RuntimeError(f"AM1 current-limit hold for {motor} cannot be represented within its command range")
            if protected_hold and not motor.endswith("_gripper"):
                # A joint hold is an integer Present_Position sample. Recover that
                # exact encoder tick; a float round trip must not move its hold.
                value = -goal if bus.apply_drive_mode and calibration.drive_mode else goal
                if mode is MotorNormMode.RANGE_M100_100:
                    raw_hold = (value + 100) / 200 * (calibration.range_max - calibration.range_min) + calibration.range_min
                elif mode is MotorNormMode.RANGE_0_100:
                    value = 100 - goal if bus.apply_drive_mode and calibration.drive_mode else goal
                    raw_hold = value / 100 * (calibration.range_max - calibration.range_min) + calibration.range_min
                else:
                    raw_hold = goal * (bus.model_resolution_table[model.model] - 1) / 360 + (
                        calibration.range_min + calibration.range_max
                    ) / 2
                raw_goals[model.id] = round(raw_hold)
            if self.config.max_relative_target is None:
                continue
            cap = self.config.max_relative_target
            if isinstance(cap, dict):
                cap = cap[key]
            if mode is MotorNormMode.DEGREES:
                ticks_per_unit = (bus.model_resolution_table[model.model] - 1) / 360
            else:
                ticks_per_unit = (calibration.range_max - calibration.range_min) / (
                    200 if mode is MotorNormMode.RANGE_M100_100 else 100
                )
            measured_raw = present_raw[motor]
            raw_cap = cap * ticks_per_unit
            # Integer distances avoid affine subtraction roundoff. No tick margin.
            distance_cap = math.floor(raw_cap)
            low, high = measured_raw - distance_cap, measured_raw + distance_cap
            if bounds:
                low, high = max(low, calibration.range_min), min(high, calibration.range_max)
            if low > high:
                raise RuntimeError(
                    f"AM1 encoded goal for {motor} cannot meet relative target limit "
                    f"inside the calibrated command range: present_raw={measured_raw}, limit={cap}"
                )
            if protected_hold and not low <= raw_goals[model.id] <= high:
                raise RuntimeError(f"AM1 current-limit hold for {motor} conflicts with the relative target limit")
            raw_goals[model.id] = min(high, max(low, raw_goals[model.id]))
        named_raw = {
            key.removesuffix(".pos"): raw_goals[bus.motors[key.removesuffix(".pos")].id]
            for key in goals
        }
        wire_positions = self._normalize_arm_feedback(bus, named_raw)
        return {key: wire_positions[key.removesuffix(".pos")] for key in goals}, named_raw

    @check_if_not_connected
    def get_observation(self) -> RobotObservation:
        # Read actuators position for arm and vel for base
        observation_start_t = time.perf_counter()
        # arm_pos = self.left_bus.sync_read("Present_Position", self.arm_motors)

        #print(f"Left arm motors: {self.left_arm_motors}, Right arm motors: {self.right_arm_motors}")  # debug
        left_pos = (
            self._read_arm_positions(self.left_bus, self.left_arm_motors)
            if self.left_arm_motors
            else {}
        )
        left_arm_done_t = time.perf_counter()


        base_wheel_vel = self.left_bus.sync_read("Present_Velocity", self.base_motors)

        base_vel = self._wheel_raw_to_body(
            base_wheel_vel["base_left_wheel"],
            base_wheel_vel["base_back_wheel"],
            base_wheel_vel["base_right_wheel"],
        )
        base_done_t = time.perf_counter()

        right_pos = (
            self._read_arm_positions(self.right_bus, self.right_arm_motors)
            if self.right_bus and self.right_arm_motors
            else {}
        )
        right_arm_done_t = time.perf_counter()

        left_arm_state = {f"{k}.pos": v for k, v in left_pos.items()}
        right_arm_state = {f"{k}.pos": v for k, v in right_pos.items()}

        obs_dict = {**left_arm_state, **right_arm_state,**base_vel}
        operation = getattr(self, "_lift_operation", None)
        if operation is not None:
            try:
                operation.contribute_observation(obs_dict)
            except BaseException as error:
                # Fault-only in-memory evidence: do not print or perform another
                # serial read here. Preserve the original refusal through cleanup.
                sample = getattr(operation, "last_record", None) or {}
                sampled_at = sample.get("sample_monotonic_s")
                error.add_note("AM1 observation freshness context: " + json.dumps({
                    "lift_sample_age_ms": None if sampled_at is None else round(
                        (time.monotonic() - sampled_at) * 1000, 3
                    ),
                    "lift_emit_ms": round(getattr(operation, "last_emit_ms", 0.0), 3),
                    "left_arm_ms": round((left_arm_done_t - observation_start_t) * 1000, 3),
                    "base_ms": round((base_done_t - left_arm_done_t) * 1000, 3),
                    "right_arm_ms": round((right_arm_done_t - base_done_t) * 1000, 3),
                }, separators=(",", ":")))
                raise
        else:
            self.lift.contribute_observation(obs_dict)
        lift_done_t = time.perf_counter()
        #print(f"Observation dict so far: {obs_dict}")  # debug

        dt_ms = (lift_done_t - observation_start_t) * 1e3
        logger.debug(f"{self} read state: {dt_ms:.1f}ms")

        # currents protection
        self.read_and_check_currents(limit_ma=2000, print_currents=True)
        currents_done_t = time.perf_counter()

        # Capture images from cameras
        camera_timings_ms = {}
        for cam_key, cam in self.cameras.items():
            camera_start_t = time.perf_counter()
            obs_dict[cam_key] = cam.async_read()
            camera_done_t = time.perf_counter()
            dt_ms = (camera_done_t - camera_start_t) * 1e3
            camera_timings_ms[f"camera_{cam_key}"] = dt_ms
            logger.debug(f"{self} read {cam_key}: {dt_ms:.1f}ms")

        observation_done_t = time.perf_counter()
        self.logs["observation_timing_ms"] = {
            "left_arm": (left_arm_done_t - observation_start_t) * 1e3,
            "base": (base_done_t - left_arm_done_t) * 1e3,
            "right_arm": (right_arm_done_t - base_done_t) * 1e3,
            "lift": (lift_done_t - right_arm_done_t) * 1e3,
            "currents": (currents_done_t - lift_done_t) * 1e3,
            **camera_timings_ms,
            "robot_observation_total": (observation_done_t - observation_start_t) * 1e3,
        }

        return obs_dict

    @check_if_not_connected
    def send_action(self, action: RobotAction) -> RobotAction:
        """Command AlohaMini to move to a target joint configuration.

        The relative action magnitude may be clipped depending on the configuration parameter
        `max_relative_target`. In this case, the action sent differs from original action.
        Thus, this function always returns the action actually sent.

        Raises:
            RobotDeviceNotConnectedError: if robot is not connected.

        Returns:
            np.ndarray: the action sent to the motors, potentially clipped.
        """
        action_start_t = time.perf_counter()
        # arm_goal_pos = {k: v for k, v in action.items() if k.endswith(".pos")}
        left_pos  = {k: v for k, v in action.items() if k.endswith(".pos") and k.startswith("arm_left_") and k.replace(".pos", "") in self.left_bus.motors}
        right_pos = {k: v for k, v in action.items() if k.endswith(".pos") and k.startswith("arm_right_") and self.right_bus is not None and k.replace(".pos", "") in self.right_bus.motors}
        requested_arm_pos = {**left_pos, **right_pos}
        right_wrist_observed = None


        base_goal_vel = {k: v for k, v in action.items() if k.endswith(".vel")}

        base_wheel_goal_vel = self._body_to_wheel_raw(
            base_goal_vel["x.vel"], base_goal_vel["y.vel"], base_goal_vel["theta.vel"]
        )
        prepare_done_t = time.perf_counter()

        # Cap goal position when too far away from present position.
        # /!\ Slower fps expected due to reading from the follower.
        # if self.config.max_relative_target is not None:
        #     present_pos = self.left_bus.sync_read("Present_Position", self.arm_motors)
        #     goal_present_pos = {key: (g_pos, present_pos[key]) for key, g_pos in arm_goal_pos.items()}
        #     arm_safe_goal_pos = ensure_safe_goal_position(goal_present_pos, self.config.max_relative_target)
        #     arm_goal_pos = arm_safe_goal_pos

        operation = getattr(self, "_lift_operation", None)
        if operation is not None:
            operation.apply_action(action)
        else:
            self.lift.apply_action(action)
        lift_action_done_t = time.perf_counter()

        present_left_raw, present_right_raw = {}, {}
        if left_pos and self.config.max_relative_target is not None:
            present_left = self._read_arm_positions(
                self.left_bus, self.left_arm_motors, raw_positions=present_left_raw,
            )
            gp_left = {k: (v, present_left[k.replace(".pos", "")]) for k, v in left_pos.items()}
            left_pos = ensure_safe_goal_position(gp_left, self.config.max_relative_target)

        if self.right_bus and right_pos and self.config.max_relative_target is not None:
            present_right = self._read_arm_positions(
                self.right_bus, self.right_arm_motors, raw_positions=present_right_raw,
            )
            right_wrist_observed = present_right.get("arm_right_wrist_flex")
            gp_right = {k: (v, present_right[k.replace(".pos", "")]) for k, v in right_pos.items()}
            right_pos = ensure_safe_goal_position(gp_right, self.config.max_relative_target)
        relative_limit_done_t = time.perf_counter()

        left_pos = self._limit_gripper_goal_by_current(self.left_bus, left_pos)
        left_gripper_limit_done_t = time.perf_counter()
        left_pos = self._limit_joint_goal_by_current(self.left_bus, left_pos)
        left_joint_limit_done_t = time.perf_counter()
        if self.right_bus and right_pos:
            right_pos = self._limit_gripper_goal_by_current(self.right_bus, right_pos)
        right_gripper_limit_done_t = time.perf_counter()
        if self.right_bus and right_pos:
            right_pos = self._limit_joint_goal_by_current(self.right_bus, right_pos)
        right_joint_limit_done_t = time.perf_counter()

        left_pos, left_raw_goals = self._encode_am1_arm_goals(self.left_bus, left_pos, present_left_raw)
        right_raw_goals = None
        if self.right_bus and right_pos:
            right_pos, right_raw_goals = self._encode_am1_arm_goals(self.right_bus, right_pos, present_right_raw)

        # Send goal position to the actuators
        # arm_goal_pos_raw = {k.replace(".pos", ""): v for k, v in arm_goal_pos.items()}
        # self.left_bus.sync_write("Goal_Position", arm_goal_pos_raw)
        # self.left_bus.sync_write("Goal_Velocity", base_wheel_goal_vel)

        # return {**arm_goal_pos, **base_goal_vel}

        #print(f"[{filename}:{lineno}]Sending left_pos:{left_pos}, right_pos:{right_pos}, base_wheel_goal_vel:{base_wheel_goal_vel}")  # debug
    
        if left_pos:
            if left_raw_goals is not None:
                self.left_bus.sync_write("Goal_Position", left_raw_goals, normalize=False)
            else:
                self.left_bus.sync_write("Goal_Position", {k.replace(".pos", ""): v for k, v in left_pos.items()})
        left_write_done_t = time.perf_counter()
        if self.right_bus and right_pos:
            if right_raw_goals is not None:
                self.right_bus.sync_write("Goal_Position", right_raw_goals, normalize=False)
            else:
                self.right_bus.sync_write("Goal_Position", {k.replace(".pos", ""): v for k, v in right_pos.items()})
        right_write_done_t = time.perf_counter()
        self.left_bus.sync_write("Goal_Velocity", base_wheel_goal_vel)
        base_write_done_t = time.perf_counter()

        self.logs["action_timing_ms"] = {
            "action_prepare": (prepare_done_t - action_start_t) * 1e3,
            "action_lift": (lift_action_done_t - prepare_done_t) * 1e3,
            "action_relative_limit": (relative_limit_done_t - lift_action_done_t) * 1e3,
            "action_left_gripper_limit": (left_gripper_limit_done_t - relative_limit_done_t) * 1e3,
            "action_left_joint_limit": (left_joint_limit_done_t - left_gripper_limit_done_t) * 1e3,
            "action_right_gripper_limit": (right_gripper_limit_done_t - left_joint_limit_done_t) * 1e3,
            "action_right_joint_limit": (right_joint_limit_done_t - right_gripper_limit_done_t) * 1e3,
            "action_left_write": (left_write_done_t - right_joint_limit_done_t) * 1e3,
            "action_right_write": (right_write_done_t - left_write_done_t) * 1e3,
            "action_base_write": (base_write_done_t - right_write_done_t) * 1e3,
            "action_total": (base_write_done_t - action_start_t) * 1e3,
        }

        final_arm_pos = {**left_pos, **right_pos}
        self.logs["action_diagnostics"] = {
            "target_limited": any(
                float(final_arm_pos[key]) != float(requested)
                for key, requested in requested_arm_pos.items()
            ),
            "right_wrist_requested": requested_arm_pos.get("arm_right_wrist_flex.pos"),
            "right_wrist_final": final_arm_pos.get("arm_right_wrist_flex.pos"),
            "right_wrist_observed": right_wrist_observed,
        }
        shoulder = "arm_right_shoulder_lift.pos"
        if getattr(self.config, "robot_model", None) == "alohamini1" and shoulder in requested_arm_pos:
            self.logs["action_diagnostics"]["right_shoulder"] = {
                "requested": requested_arm_pos[shoulder], "final": final_arm_pos[shoulder],
                # Broadcast sync-write completion is not a servo acknowledgement.
                "sync_write_returned": True, "write_acknowledged": False,
            }

        left_shoulder = "arm_left_shoulder_lift.pos"
        if (
            getattr(self, "_am1_left_shoulder_evidence_enabled", False)
            and getattr(self.config, "robot_model", None) == "alohamini1"
            and left_shoulder in requested_arm_pos
        ):
            record = {
                **getattr(self, "_am1_left_shoulder_feedback", {"feedback_available": False}),
                "requested": requested_arm_pos[left_shoulder],
                "final": final_arm_pos[left_shoulder],
                "sync_write_returned": True, "write_acknowledged": False,
                "expected_goal_raw": None,
            }
            try:
                record["expected_goal_raw"] = left_raw_goals["arm_left_shoulder_lift"]
            except Exception as error:
                record["expected_goal_error"] = f"{type(error).__name__}: {error}"
            self.logs["action_diagnostics"]["left_shoulder"] = record

        lift_sent = {k: v for k, v in action.items() if k.startswith("lift_axis.")}
        return {**left_pos, **right_pos, **base_goal_vel, **lift_sent}

    def _limit_gripper_goal_by_current(self, bus, goal_pos: dict[str, float]) -> dict[str, float]:
        """Stop pushing a gripper harder once its measured current exceeds the force limit."""
        return self._limit_goal_by_current(
            bus,
            goal_pos,
            is_target_motor=lambda key: key.endswith("_gripper.pos"),
            current_limit_ma=self._gripper_current_limit_ma,
            release_margin=self._gripper_release_margin,
            hold_goal_state=self._gripper_hold_goal,
            hold_direction_state=self._gripper_hold_direction,
            fixed_direction=self._gripper_open_direction,
            hold_close_step=self._gripper_hold_close_step,
            log_tag="GripperCurrentLimit",
        )

    def _limit_joint_goal_by_current(self, bus, goal_pos: dict[str, float]) -> dict[str, float]:
        """Freeze an arm joint (not the gripper) once its measured current exceeds the limit."""
        return self._limit_goal_by_current(
            bus,
            goal_pos,
            is_target_motor=lambda key: not key.endswith("_gripper.pos"),
            current_limit_ma=self._joint_current_limit_ma,
            release_margin=self._joint_release_margin,
            hold_goal_state=self._joint_hold_goal,
            hold_direction_state=self._joint_hold_direction,
            fixed_direction=None,
            hold_close_step=0.0,
            log_tag="JointCurrentLimit",
        )

    def _limit_goal_by_current(
        self,
        bus,
        goal_pos: dict[str, float],
        *,
        is_target_motor,
        current_limit_ma: float,
        release_margin: float,
        hold_goal_state: dict[str, float],
        hold_direction_state: dict[str, float],
        fixed_direction: dict[str, float] | None,
        hold_close_step: float,
        log_tag: str,
    ) -> dict[str, float]:
        """Freeze a motor's goal once its current exceeds the limit, holding there until the
        caller's own goal asks to move back past the held position.

        fixed_direction is a per-motor mechanical constant (e.g. the gripper's open/close
        sense, fixed at install time). Pass None for joints that can be pushed into an
        obstacle from either side -- the retreat direction is then inferred per contact
        episode from which way the caller was commanding it to move.
        """
        target_keys = [key for key in goal_pos if is_target_motor(key) and key.replace(".pos", "") in bus.motors]
        if not target_keys:
            return goal_pos

        target_motors = [key.replace(".pos", "") for key in target_keys]
        selected = "arm_left_shoulder_lift"
        trace_shoulder = (
            getattr(self, "_am1_left_shoulder_evidence_enabled", False)
            and getattr(self.config, "robot_model", None) == "alohamini1"
            and bus is self.left_bus and selected in target_motors
        )
        if trace_shoulder:
            self._am1_left_shoulder_feedback = {"feedback_available": False}
        try:
            if trace_shoulder:
                current_started = time.monotonic()
                current_wall_started = time.time_ns()
            currents_raw = bus.sync_read("Present_Current", target_motors)
            if trace_shoulder:
                current_completed = time.monotonic()
                position_started = time.monotonic()
                position_wall_started = time.time_ns()
                # Reuse the existing force-limit transaction and retain its raw
                # sample; AM1 feedback must not hide positions past a command limit.
                raw_present = bus.sync_read("Present_Position", target_motors, normalize=False)
                position_completed = time.monotonic()
                present_pos = self._normalize_arm_feedback(bus, raw_present)
                self._am1_left_shoulder_feedback = {
                    "feedback_available": True,
                    "present_position_raw": raw_present[selected],
                    "present_position_normalized": present_pos[selected],
                    "present_current_raw": currents_raw[selected],
                    "present_current_ma": float(currents_raw[selected]) * 6.5,
                    "current_read_started_at": current_started,
                    "current_read_completed_at": current_completed,
                    "current_read_started_wall_time_ns": current_wall_started,
                    "position_read_started_at": position_started,
                    "position_read_completed_at": position_completed,
                    "position_read_started_wall_time_ns": position_wall_started,
                }
            else:
                present_pos = self._read_arm_positions(bus, target_motors)
        except Exception as e:
            if trace_shoulder:
                self._am1_left_shoulder_feedback = {
                    "feedback_available": False,
                    "read_started_at": current_started,
                    "read_completed_at": time.monotonic(),
                    "read_started_wall_time_ns": current_wall_started,
                    "read_error": f"{type(e).__name__}: {e}",
                }
            logger.warning("Failed to read %s current/position for force limiting: %s", log_tag, e)
            return goal_pos

        limited_goal_pos = dict(goal_pos)
        for goal_key in target_keys:
            motor = goal_key.replace(".pos", "")
            goal = float(limited_goal_pos[goal_key])
            present = float(present_pos[motor])
            current_ma = abs(float(currents_raw.get(motor, 0.0)) * 6.5)

            if motor not in hold_goal_state:
                if current_ma < current_limit_ma:
                    continue
                if fixed_direction is not None:
                    # Nudge a small step further in the "closing" sense so a bit of
                    # position error -- and thus a bit of squeeze current -- remains,
                    # instead of fully relaxing to 0mA.
                    direction = fixed_direction.get(motor, 1.0)
                    hold_goal = min(100.0, max(0.0, present - direction * hold_close_step))
                else:
                    # No fixed mechanical sense: infer which way it was being pushed and
                    # hold back away from that direction instead.
                    command_delta = goal - present
                    direction = -1.0 if command_delta > 0 else 1.0
                    hold_goal = present
                hold_goal_state[motor] = hold_goal
                hold_direction_state[motor] = direction
                print(
                    f"[{log_tag}] {motor}: {current_ma:.1f} mA >= {current_limit_ma:.1f} mA; "
                    f"holding at position {hold_goal:.2f} (present={present:.2f})"
                )

            hold_goal = hold_goal_state[motor]
            direction = hold_direction_state[motor]

            # Only hand control back once the caller's own goal asks to move past where
            # we're holding, in the direction away from what triggered the hold. Current
            # alone can't signal "let go": holding at/near present already drops the
            # current toward ~0 regardless of whether the object/obstacle is still there.
            if (goal - hold_goal) * direction >= release_margin:
                hold_goal_state.pop(motor, None)
                hold_direction_state.pop(motor, None)
                print(
                    f"[{log_tag}] {motor}: goal {goal:.2f} past held position {hold_goal:.2f}; releasing hold."
                )
                continue

            limited_goal_pos[goal_key] = hold_goal

        return limited_goal_pos


    def stop_base(self) -> None:
        failures = []
        for name in self.base_motors:
            try:
                write_register(self.left_bus, "Goal_Velocity", name, 0)
            except Exception as error:
                failures.append(f"{name}: {error}")
        if failures:
            raise RuntimeError(f"Failed to stop all base motors: {'; '.join(failures)}")
        logger.info("Base motors stopped")

    def stop_lift(self) -> None:
        self.lift.stop()
        logger.info("Lift motor stopped")

    def stop_motion(self) -> None:
        failures = []
        for stop in (self.stop_base, self.stop_lift):
            try:
                stop()
            except Exception as error:
                failures.append(str(error))
        if failures:
            raise RuntimeError(f"Failed to stop all AlohaMini motion: {'; '.join(failures)}")

    def hold_follower_arms(self) -> None:
        """AM1 Local pause: replace old goals with measured raw arm positions."""
        if self.config.robot_model != "alohamini1" or self.config.no_follower:
            raise RuntimeError("AM1 Local arm hold requires connected AM1 follower arms")
        failures = []
        for bus, motors in ((self.left_bus, self.left_arm_motors), (self.right_bus, self.right_arm_motors)):
            try:
                self._seed_arm_goals(bus, motors)
            except Exception as error:
                failures.append(f"{motors[0].split('_')[1]} arm: {error}")
        if failures:
            raise RuntimeError(f"Failed to hold AM1 follower arms: {'; '.join(failures)}")

    def read_and_check_currents(self, limit_ma, print_currents):
        """Read left/right bus currents (mA), print them, and enforce overcurrent protection"""
        scale = 6.5  # sts3215 current unit conversion factor
        left_curr_raw = {}
        left_curr_raw = self.left_bus.sync_read("Present_Current", list(self.left_bus.motors.keys()))
        right_curr_raw = {}
        if getattr(self, "right_bus", None):
            right_curr_raw = self.right_bus.sync_read("Present_Current", list(self.right_bus.motors.keys()))

        now = time.monotonic()
        if print_currents and (now - self._last_currents_log_t >= 1.0):
            left_arr = [int(float(raw) * scale) for raw in left_curr_raw.values()]
            print(f"[Currents][left_bus] {left_arr}")
            if right_curr_raw:
                right_arr = [int(float(raw) * scale) for raw in right_curr_raw.values()]
                print(f"[Currents][right_bus] {right_arr}")
            self._last_currents_log_t = now

        tripped = None
        for name, raw in {**left_curr_raw, **right_curr_raw}.items():
            current_ma = float(raw) * scale

            if current_ma > limit_ma:
                self._overcurrent_count[name] = self._overcurrent_count.get(name, 0) + 1
                print(f"[Overcurrent] {name}: {current_ma:.1f} mA > {limit_ma:.1f} mA ")
            else:
                # reset when it goes back to normal -> "consecutive" semantics
                self._overcurrent_count[name] = 0

            if self._overcurrent_count[name] >= self._overcurrent_trip_n:
                tripped = (name, current_ma, self._overcurrent_count[name])
                break

        if tripped is not None:
            name, current_ma, n = tripped
            print(
                f"[Overcurrent] {name}: {current_ma:.1f} mA > {limit_ma:.1f} mA "
                f"for {n} consecutive reads, disconnecting!"
            )
            try:
                self.stop_motion()
            except Exception:
                pass
            try:
                self.disconnect()
            except Exception as e:
                print(f"[Overcurrent] disconnect error: {e}")
            sys.exit(1)


        return {k: round(v * scale, 1) for k, v in {**left_curr_raw, **right_curr_raw}.items()}

    @check_if_not_connected
    def read_lift_diagnostics(self) -> dict[str, int | float | bool]:
        """Read a compact AM1 lift snapshot without changing any servo register."""
        if self.config.robot_model != "alohamini1":
            raise RuntimeError("Lift diagnostics are supported only for Aloha Mini 1.")

        motor = self.lift.cfg.name

        def read_raw(register: str) -> int:
            return int(
                self.left_bus.read(
                    register,
                    motor,
                    normalize=False,
                    num_retry=REGISTER_RETRIES,
                )
            )

        present_current_raw = read_raw("Present_Current")
        return {
            "is_homed": self.lift.is_homed,
            "present_current_raw": present_current_raw,
            "present_current_ma": round(abs(present_current_raw) * 6.5, 1),
            "present_temperature_raw": read_raw("Present_Temperature"),
            "present_voltage_raw": read_raw("Present_Voltage"),
            "goal_velocity_raw": read_raw("Goal_Velocity"),
            "present_velocity_raw": read_raw("Present_Velocity"),
            "torque_enable": read_raw("Torque_Enable"),
            "operating_mode": read_raw("Operating_Mode"),
            "status": read_raw("Status"),
        }

    @check_if_not_connected
    def disconnect(self, *, recover_interrupted_bus_io: bool = False) -> None:
        errors = self._safe_shutdown(
            close_buses=True,
            recover_interrupted_bus_io=recover_interrupted_bus_io,
        )
        if errors:
            raise RuntimeError(f"AlohaMini disconnected with cleanup issues: {'; '.join(errors)}")
        logger.info("%s disconnected.", self)
