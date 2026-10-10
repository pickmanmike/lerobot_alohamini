"""Explicitly simulated Pi executor. This module has no live backend factory."""

import argparse
import asyncio
import math
import os
import re
import time

from examples.alohamini.am1_finite_task import JOINT_KEYS, FiniteTask
from examples.alohamini.am1_session_runtime import acquire_session_admission


class SimulatedIO:
    """Deterministic in-memory plant; flags permit controlled fault injection."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.positions = dict.fromkeys(JOINT_KEYS, 0.0)
        self.positions["arm_left_shoulder_lift.pos"] = 99.0
        self.target = dict(self.positions)
        self.feedback_enabled = self.acknowledge = self.hold_acknowledge = True
        self.freeze_sequence = False
        self.fault = None
        self.sequence = 0
        self.acquired_at = clock()

    def feedback(self):
        now = self.clock()
        if self.feedback_enabled and not self.freeze_sequence and now - self.acquired_at >= 0.1 - 1e-9:
            self.positions = dict(self.target)
            self.sequence += 1
            self.acquired_at = now
        return {"positions": dict(self.positions), "sequence": self.sequence, "at": self.acquired_at}

    def send(self, action):
        if not self.acknowledge:
            return False
        self.target = {"arm_" + key: value for key, value in action.items()}
        return True

    def hold(self):
        if self.hold_acknowledge:
            self.target = dict(self.positions)
        return self.hold_acknowledge


class PiExecutor:
    source = "simulated-provider"
    restart_cleanup = "unknown_after_restart"
    recipes = frozenset({"sim-arm-smoke", "sim-arm-smoke-repeat", "sim-arm-hold-body"})

    def __init__(self, io, admission_directory, clock=time.monotonic, shoulder_amplitude=1.5):
        if not isinstance(io, SimulatedIO):
            raise TypeError("this trial accepts only simulated IO")
        if not 0 < shoulder_amplitude <= 3:
            raise ValueError("invalid simulated shoulder amplitude")
        self.io, self.clock = io, clock
        self.admission_directory = admission_directory
        self.shoulder_amplitude = shoulder_amplitude
        self.task = None
        self.admission = None
        self.fault = None
        self.sample = None
        self.hold_ack = False
        self.aligned_samples = []
        self.alignment_sequence = -1

    def supports(self, recipe):
        return recipe.name in self.recipes

    def evidence(self):
        try:
            self.sample = self.io.feedback()
        except (OSError, ValueError, RuntimeError) as exc:
            self.sample = None
            self.fault = self.fault or str(exc)
        valid = (
            isinstance(self.sample, dict)
            and set(self.sample) == {"positions", "sequence", "at"}
            and isinstance(self.sample["positions"], dict)
            and set(self.sample["positions"]) == set(JOINT_KEYS)
            and all(
                type(value) in (int, float) and math.isfinite(value)
                for value in self.sample["positions"].values()
            )
            and type(self.sample["sequence"]) is int
            and self.sample["sequence"] >= 0
            and type(self.sample["at"]) in (int, float)
            and math.isfinite(self.sample["at"])
            and self.sample["at"] <= self.clock()
        )
        age = max(0, self.clock() - self.sample["at"]) if valid else None
        if not self.hold_ack and (not self.task or not self.task.active):
            self.hold()
        feedback = self.io.feedback_enabled and valid and age <= 0.25
        if not feedback:
            self.aligned_samples.clear()
        elif self.sample["sequence"] != self.alignment_sequence:
            self.alignment_sequence = self.sample["sequence"]
            if all(abs(self.sample["positions"][key] - self.io.target[key]) <= 1 for key in JOINT_KEYS):
                self.aligned_samples.append(self.sample["at"])
                self.aligned_samples = self.aligned_samples[-3:]
            else:
                self.aligned_samples.clear()
        aligned = (
            self.hold_ack
            and len(self.aligned_samples) >= 3
            and self.aligned_samples[-1] - self.aligned_samples[0] >= 0.2 - 1e-9
        )
        if self.task:
            try:
                self.task.check(self.clock())
            except (ValueError, OSError, RuntimeError) as exc:
                self.fault = self.fault or str(exc)
        return {
            "source": self.source,
            "feedback": feedback,
            "required_observation": True,
            "optional_quality": True,
            "pose_aligned": aligned,
            "hold_acknowledged": self.hold_ack,
            "native_ack": self.io.acknowledge,
            "fault": self.fault or self.io.fault,
            "feedback_age_s": age,
            "observation_age_s": 0,
            "observation_provenance": "simulated-no-camera",
            "ack_provenance": "simulated-backend",
            "feedback_sequence": self.sample["sequence"] if valid else None,
        }

    def begin(self, recipe):
        if self.task and not self.task.finished:
            raise RuntimeError("previous finite task is still active")
        self.task = None
        self.fault = None
        return self.dispatch(recipe)

    def dispatch(self, recipe):
        e = self.evidence()
        if not self.supports(recipe) or not e["feedback"] or not e["native_ack"] or e["fault"]:
            return False
        if self.task is None:
            try:
                self.admission = acquire_session_admission(self.admission_directory)
            except RuntimeError:
                return False
            self.task = FiniteTask(recipe, self.sample["positions"], self.clock(), self.shoulder_amplitude)
        try:
            if not self.io.send(self.task.provider.get_action()):
                return False
            self.task.resume(self.clock())
        except (ValueError, OSError, RuntimeError) as exc:
            self.fault = self.fault or str(exc)
            self.task.hold()
            return False
        return True

    def advance(self, now, dt):
        if self.task:
            acknowledged = self.task.snapshot()
            try:
                if self.task.advance(now, self.sample) and not self.io.send(self.task.provider.get_action()):
                    raise RuntimeError("backend_command_unacknowledged")
            except (ValueError, OSError, RuntimeError) as exc:
                self.fault = self.fault or str(exc)
                self.task.hold()
                return dict(acknowledged, fault=self.fault)
            return dict(self.task.snapshot(), fault=self.fault)
        return {"progress_s": 0, "complete": False, "fault": self.fault}

    def hold(self):
        was_active = self.task is not None and self.task.active
        was_acknowledged = self.hold_ack
        previous_target = dict(self.io.target)
        if was_active:
            self.task.hold()
        self.hold_ack = self.io.hold()
        # Connect/release may repeat a protective hold immediately before Resume.
        # Preserve qualification only for the same acknowledged, still-fresh target.
        if (
            was_active
            or not was_acknowledged
            or not self.hold_ack
            or self.io.target != previous_target
            or not self.aligned_samples
            or self.clock() - self.aligned_samples[-1] > 0.25
        ):
            self.aligned_samples.clear()
            self.alignment_sequence = -1
        return self.hold_ack

    def finish(self, status):
        acknowledged = self.hold()
        if self.task:
            self.task.finish(status)
        if self.admission:
            self.admission.close()
            self.admission = None
        return {
            "cleanup": "simulated_hold_acknowledged" if acknowledged else "simulated_cleanup_unacknowledged",
            "uncertain": not acknowledged,
        }

    def close(self):
        self.finish("owner_shutdown")


def main():
    parser = argparse.ArgumentParser(description="Isolated simulated finite AM1 owner; no hardware backend")
    parser.add_argument("--state", required=True)
    parser.add_argument("--simulated-admission-state", required=True)
    args = parser.parse_args()
    from tools.am1_session_ipc import run_owner

    raw = os.environ.get("AM1_SCRIPTED_LEFT_SHOULDER_AMPLITUDE", "")
    if not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", raw):
        parser.error("private mapped shoulder amplitude environment value required")
    amplitude = float(raw)
    if not math.isfinite(amplitude) or not 0 < amplitude <= 3:
        parser.error("private mapped shoulder amplitude must be finite and greater than zero through 3")
    executor = PiExecutor(SimulatedIO(), args.simulated_admission_state, shoulder_amplitude=amplitude)
    asyncio.run(run_owner(args.state, executor))


if __name__ == "__main__":
    main()
