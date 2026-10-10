"""Existing AM1 finite providers with one prepared seed and portable timing."""

from functools import partial

from .am1_scripted_prepare import PreparedScriptedInput
from .scripted_leader import ArmHoldBodyInput, ScriptedLeaderInput
from .scripted_leader_repeat import ArmSmokeRepeatInput

JOINT_KEYS = tuple(
    f"arm_{side}_{joint}.pos"
    for side in ("left", "right")
    for joint in ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
)


class FiniteTask:
    def __init__(self, recipe, positions, now, shoulder_amplitude, *, start_held=False):
        profile = {
            "sim-arm-smoke": ("ArmSmoke", ScriptedLeaderInput),
            "sim-arm-smoke-repeat": ("ArmSmokeRepeat", ArmSmokeRepeatInput),
            "sim-arm-hold-body": ("ArmHoldBody", ArmHoldBodyInput),
        }[recipe.name]
        self.profile = profile[0]
        self.admitted_at = now
        self.admissions = 1
        self.cycles_completed = self.returns_qualified = 0
        self.original_seed = None
        self.joint_amplitudes = dict.fromkeys(JOINT_KEYS, 3.0)
        if self.profile == "ArmHoldBody":
            self.provider = profile[1](positions, joint_keys=JOINT_KEYS, fps=10, emit=self.record)
            self.original_seed = dict(positions)
        else:
            self.joint_amplitudes["arm_left_shoulder_lift.pos"] = shoulder_amplitude
            provider = partial(profile[1], joint_amplitudes=self.joint_amplitudes)
            self.provider = PreparedScriptedInput(
                positions,
                joint_keys=JOINT_KEYS,
                fps=10,
                provider=provider,
                motion_profile=self.profile,
                emit=self.record,
            )
        # Real observation can take time to qualify. Keep this admission/reference and its
        # original 20-second wall ceiling, but begin boundary acquisition only when dispatched.
        if not start_held:
            self.provider.admit(now)
        self.last_frame = now
        self.sequence = -1
        self.active = not start_held
        self.finished = False
        self.return_first = None
        self.return_count = 0
        self.complete = False

    def record(self, row):
        if row["event"] == "am1_scripted_seed":
            self.original_seed = dict(row["origin"])
        elif row["event"] == "am1_scripted_cycle_complete":
            self.cycles_completed += 1
        elif row["event"] == "am1_scripted_return_qualified":
            self.returns_qualified += 1

    def check(self, now):
        if not self.finished and getattr(self.provider, "preparing", False) and now - self.admitted_at >= 20:
            raise ValueError("20-second preparation wall deadline expired without a qualified seed")

    def resume(self, now):
        self.check(now)
        if not self.active and not self.finished:
            self.provider.admit(now)
            self.last_frame = now
            self.active = True

    def hold(self):
        self.provider.freeze()
        self.active = False
        self.return_first = None
        self.return_count = 0

    def advance(self, now, sample):
        self.check(now)
        if not self.active or self.finished or now - self.last_frame < 0.1 - 1e-9:
            return False
        if sample["sequence"] <= self.sequence:
            return False
        self.provider.advance(now, sample["positions"], sample["sequence"])
        if self.provider.complete:
            if self.profile == "ArmSmokeRepeat":
                self.complete = True
            else:
                for key, origin in self.original_seed.items():
                    limit = 1.0 if self.profile == "ArmHoldBody" else self.joint_amplitudes[key]
                    if abs(sample["positions"][key] - origin) > limit:
                        raise ValueError(f"{self.profile} return exceeds the original envelope for {key}")
                self.return_first = now if self.return_first is None else self.return_first
                self.return_count += 1
                if self.return_count >= 3 and now - self.return_first >= 0.2 - 1e-9:
                    self.returns_qualified = 1
                    self.cycles_completed = int(self.profile == "ArmSmoke")
                    self.complete = True
        self.sequence = sample["sequence"]
        self.last_frame = now
        return True

    def finish(self, reason):
        self.provider.finish(reason)
        self.finished = True
        self.active = False

    def snapshot(self):
        return {
            "progress_s": self.provider.elapsed_s,
            "complete": self.complete,
            "preparing": getattr(self.provider, "preparing", False),
            "preparation_s": getattr(self.provider, "preparation_elapsed_s", 0),
            "original_seed": self.original_seed,
            "admissions": self.admissions,
            "cycles_completed": self.cycles_completed,
            "returns_qualified": self.returns_qualified,
            "joint_amplitudes": self.joint_amplitudes,
            "feedback_sequence": self.sequence,
            "profile": self.profile,
            "provenance": "existing-provider/simulated-feedback",
        }
