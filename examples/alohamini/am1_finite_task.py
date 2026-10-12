"""Existing AM1 finite providers with one prepared seed and portable timing."""

from functools import partial

from .am1_scripted_prepare import SHOULDER, PreparedScriptedInput
from .am1_session_contract import (
    ELBOW_RECOVERY_DEFINITIONS,
    LEFT_ELBOW_ORIGINAL_RETURN,
    LEFT_ELBOW_POSITIVE_CONTROL,
    LEFT_ELBOW_RESPONSE,
    SelectedElbowExperiment,
)
from .scripted_leader import (
    ArmHoldBodyInput,
    LeftElbowCompareInput,
    LeftElbowDwellCompareInput,
    LeftElbowOriginalReturnInput,
    LeftElbowPositiveControlInput,
    LeftElbowRecoveryInput,
    LeftElbowResponseCharacterizeInput,
    ScriptedLeaderInput,
)
from .scripted_leader_repeat import ArmSmokeRepeatInput

JOINT_KEYS = tuple(
    f"arm_{side}_{joint}.pos"
    for side in ("left", "right")
    for joint in ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
)


class FiniteTask:
    def __init__(
        self,
        recipe,
        positions,
        now,
        shoulder_amplitude,
        *,
        start_held=False,
        feedback_provenance="simulated-feedback",
        return_reference=None,
        reference_sha256=None,
        recovery_comparator=None,
        positive_comparator=None,
        selected_experiment=None,
    ):
        if recipe.name == SelectedElbowExperiment.recipe_name:
            if not isinstance(selected_experiment, SelectedElbowExperiment):
                raise ValueError("selected experiment recipe requires its validated definition")
        elif selected_experiment is not None:
            raise ValueError("selected experiment definition forbidden for ordinary recipes")
        self.selected_experiment = selected_experiment
        self.feedback_provenance = feedback_provenance
        self.initial_measured_reference = dict(positions)
        self.recovery_definition = ELBOW_RECOVERY_DEFINITIONS.get(recipe.name)
        profile = {
            "sim-arm-smoke": ("ArmSmoke", ScriptedLeaderInput),
            "sim-arm-smoke-repeat": ("ArmSmokeRepeat", ArmSmokeRepeatInput),
            "sim-arm-hold-body": ("ArmHoldBody", ArmHoldBodyInput),
            "physical-arm-smoke": ("ArmSmoke", ScriptedLeaderInput),
            "physical-arm-smoke-repeat": ("ArmSmokeRepeat", ArmSmokeRepeatInput),
            "physical-arm-hold-body": ("ArmHoldBody", ArmHoldBodyInput),
            "physical-left-elbow-compare": ("LeftElbowCompare", LeftElbowCompareInput),
            "physical-left-elbow-dwell-compare": ("LeftElbowDwellCompare", LeftElbowDwellCompareInput),
            LEFT_ELBOW_POSITIVE_CONTROL.recipe_name: (LEFT_ELBOW_POSITIVE_CONTROL.profile, LeftElbowPositiveControlInput),
            SelectedElbowExperiment.recipe_name: (SelectedElbowExperiment.profile, LeftElbowPositiveControlInput),
            LEFT_ELBOW_RESPONSE.recipe_name: (
                LEFT_ELBOW_RESPONSE.profile,
                LeftElbowResponseCharacterizeInput,
            ),
            LEFT_ELBOW_ORIGINAL_RETURN.recipe_name: (
                LEFT_ELBOW_ORIGINAL_RETURN.profile,
                LeftElbowOriginalReturnInput,
            ),
            **{
                name: (d.profile, partial(LeftElbowRecoveryInput, definition=d))
                for name, d in ELBOW_RECOVERY_DEFINITIONS.items()
            },
        }[recipe.name]
        self.profile = profile[0]
        self.positive_definition = (selected_experiment if selected_experiment is not None
                                    else LEFT_ELBOW_POSITIVE_CONTROL if self.profile == LEFT_ELBOW_POSITIVE_CONTROL.profile else None)
        if positive_comparator is not None and self.positive_definition is None:
            raise ValueError("positive comparator forbidden for ordinary recipes")
        if self.recovery_definition is not None:
            self.recovery_definition.validate_artifact(return_reference)
        elif self.profile == LEFT_ELBOW_ORIGINAL_RETURN.profile:
            LEFT_ELBOW_ORIGINAL_RETURN.validate_artifact(return_reference)
        elif return_reference is not None or reference_sha256 is not None:
            raise ValueError("retained return reference forbidden for ordinary recipes")
        if recovery_comparator is not None and self.recovery_definition is None:
            raise ValueError("recovery comparator forbidden for ordinary recipes")
        self.admitted_at = now
        self.admissions = 1
        self.cycles_completed = self.returns_qualified = 0
        self.original_seed = None
        self.joint_amplitudes = dict.fromkeys(JOINT_KEYS, 3.0)
        if self.profile in {LEFT_ELBOW_RESPONSE.profile, LEFT_ELBOW_POSITIVE_CONTROL.profile}:
            self.joint_amplitudes[LEFT_ELBOW_RESPONSE.joint] = 10.0
        if selected_experiment is not None:
            self.joint_amplitudes[selected_experiment.joint] = abs(selected_experiment.excursion_norm)
        prepare_body = (
            recipe.name == "physical-arm-hold-body" and not -100.0 <= positions.get(SHOULDER, 0.0) <= 100.0
        )
        if self.profile == "ArmHoldBody" and prepare_body:
            self.provider = PreparedScriptedInput(
                positions,
                joint_keys=JOINT_KEYS,
                fps=10,
                provider=ArmHoldBodyInput,
                motion_profile=self.profile,
                emit=self.record,
            )
        elif self.profile == "ArmHoldBody":
            self.provider = profile[1](positions, joint_keys=JOINT_KEYS, fps=10, emit=self.record)
            self.original_seed = dict(positions)
        else:
            if self.positive_definition is not None:
                provider = partial(profile[1], positive_comparator=positive_comparator,
                                   **({"definition":selected_experiment} if selected_experiment is not None else {}))
            elif self.recovery_definition is not None:
                provider = partial(
                    profile[1],
                    return_reference=return_reference,
                    reference_sha256=reference_sha256,
                    recovery_comparator=recovery_comparator,
                )
            elif self.profile == LEFT_ELBOW_ORIGINAL_RETURN.profile:
                provider = partial(
                    profile[1], return_reference=return_reference, reference_sha256=reference_sha256
                )
            elif self.profile in {"LeftElbowCompare", "LeftElbowDwellCompare", LEFT_ELBOW_RESPONSE.profile}:
                provider = profile[1]
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
                **({"duration_s":selected_experiment.nominal_trajectory_s} if selected_experiment is not None else {}),
            )
        if self.positive_definition is not None and positive_comparator is not None:
            self.provider.duration_s = self.positive_definition.nominal_trajectory_s + 2.0
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
        if (
            not self.finished
            and (self.recovery_definition is not None or self.positive_definition is not None)
            and now - self.admitted_at >= (self.positive_definition.live_s if self.positive_definition is not None else 60)
        ):
            raise ValueError("60-second recovery Live deadline expired")
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
        if not self.active or self.finished:
            return False
        if sample["sequence"] <= self.sequence:
            return False
        selected = (
            self.recovery_definition is not None
            or self.profile
            in {
                LEFT_ELBOW_RESPONSE.profile,
                LEFT_ELBOW_ORIGINAL_RETURN.profile,
                LEFT_ELBOW_POSITIVE_CONTROL.profile,
                SelectedElbowExperiment.profile,
            }
        ) and not self.provider.preparing
        if (
            selected
            and sample.get("selected_joint") is None
            and self.provider.awaiting_initial_feedback
            and now - self.last_frame < 0.1 - 1e-9
        ):
            return False
        if selected and not self.provider.collect_feedback(
            now, sample["positions"], sample["sequence"], selected_feedback=sample.get("selected_joint")
        ):
            return False
        if now - self.last_frame < 0.1 - 1e-9:
            return False
        was_complete = self.provider.complete
        kwargs = (
            {"selected_feedback": sample.get("selected_joint")}
            if self.recovery_definition is not None
            or self.profile
            in {
                LEFT_ELBOW_RESPONSE.profile,
                LEFT_ELBOW_ORIGINAL_RETURN.profile,
                LEFT_ELBOW_POSITIVE_CONTROL.profile,
                SelectedElbowExperiment.profile,
            }
            else {}
        )
        self.provider.advance(now, sample["positions"], sample["sequence"], **kwargs)
        if was_complete and (
            self.recovery_definition is not None
            or self.profile
            in {
                LEFT_ELBOW_RESPONSE.profile,
                LEFT_ELBOW_ORIGINAL_RETURN.profile,
                LEFT_ELBOW_POSITIVE_CONTROL.profile,
                SelectedElbowExperiment.profile,
            }
        ):
            self.provider.validate_final_feedback(now, sample["positions"], sample.get("selected_joint"))
        if self.positive_definition is not None and not self.provider.preparing:
            self.original_seed = dict(self.provider.origin)
        if self.provider.complete:
            if self.positive_definition is not None:
                self.cycles_completed, self.returns_qualified = 1, int(self.provider.positive_control["coarse_functional_pass"])
                self.complete = True
            elif self.recovery_definition is not None:
                outcome = self.provider.recovery
                self.cycles_completed = len(outcome["cycles"])
                self.returns_qualified = (
                    self.cycles_completed
                    if self.recovery_definition.mode == "confirm"
                    else int(outcome["target_attained"])
                )
                self.complete = True
            elif self.profile == LEFT_ELBOW_ORIGINAL_RETURN.profile:
                # The provider has already proven the original raw reference
                # using both real acquisition windows and the same-intent readback.
                self.returns_qualified, self.cycles_completed, self.complete = 1, 0, True
            elif self.profile == "ArmSmokeRepeat":
                self.complete = True
            else:
                for key, origin in self.original_seed.items():
                    limit = (
                        1.0
                        if self.profile in {"ArmHoldBody", LEFT_ELBOW_RESPONSE.profile}
                        else self.joint_amplitudes[key]
                    )
                    if abs(sample["positions"][key] - origin) > limit:
                        raise ValueError(f"{self.profile} return exceeds the original envelope for {key}")
                self.return_first = now if self.return_first is None else self.return_first
                self.return_count += 1
                if self.return_count >= 3 and now - self.return_first >= 0.2 - 1e-9:
                    self.returns_qualified = 1
                    self.cycles_completed = (
                        2
                        if self.profile in {"LeftElbowCompare", "LeftElbowDwellCompare"}
                        else len(self.provider.characterization["cycles"])
                        if self.profile == LEFT_ELBOW_RESPONSE.profile
                        else int(self.profile == "ArmSmoke")
                    )
                    self.complete = True
        self.sequence = sample["sequence"]
        self.last_frame = now
        return True

    def finish(self, reason):
        self.provider.finish(reason)
        self.finished = True
        self.active = False

    def snapshot(self):
        result = {
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
            "provenance": "existing-provider/" + self.feedback_provenance,
            "initial_measured_reference": self.initial_measured_reference,
        }
        if self.profile == LEFT_ELBOW_RESPONSE.profile:
            result["characterization"] = self.provider.characterization
        if self.profile == LEFT_ELBOW_ORIGINAL_RETURN.profile:
            result["retained_return"] = self.provider.retained_return
        if self.positive_definition is not None:
            result["positive_control"] = self.provider.positive_control
        if self.selected_experiment is not None:
            result["experiment"] = self.selected_experiment.summary()
        if self.recovery_definition is not None:
            result["recovery"] = self.provider.recovery
        return result
