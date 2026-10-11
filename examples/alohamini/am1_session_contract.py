"""Immutable explicitly selected session recipes and JSON validation."""

import math
import uuid
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class Recipe:
    name: str
    mode: str
    trajectory_s: float | None
    live_s: float
    seed: int | None = 17


@dataclass(frozen=True)
class SignedElbowResponse:
    """One explicit signed diagnostic, shared by input, owner and completion."""

    recipe_name: str = "physical-left-elbow-response-characterize"
    profile: str = "LeftElbowResponseCharacterize"
    trajectory_s: float = 60.0
    live_s: float = 180.0
    joint: str = "arm_left_elbow_flex.pos"
    decision_s: float = 16.0

    def phase(self, progress_s, expanded):
        if progress_s < 2:
            return 0, "baseline", 0.0, -3.0
        final_at = 58.0 if expanded else 16.0
        if progress_s >= final_at:
            return (4 if expanded else 2), "final", 0.0, (10.0 if expanded else -3.0)
        if progress_s < 16:
            cycle = min(int((progress_s - 2) // 7) + 1, 2)
            within, ramp, delta = progress_s - 2 - (cycle - 1) * 7, 3.0, -3.0
        else:
            cycle = min(int((progress_s - 16) // 21) + 3, 4)
            within, ramp, delta = progress_s - 16 - (cycle - 3) * 21, 10.0, 10.0
        if within < ramp:
            return cycle, "outbound", within / ramp, delta
        if within < ramp + .5:
            return cycle, "endpoint", 1.0, delta
        if within < 2 * ramp + .5:
            return cycle, "return", 1 - (within - ramp - .5) / ramp, delta
        return cycle, "origin", 0.0, delta

    def context_valid(self, context):
        if not isinstance(context, dict) or context.get("profile") != self.profile:
            return False
        values = [context.get(k) for k in ("progress_s", "reference", "endpoint_target", "requested_target")]
        if not all(type(v) in (int, float) and math.isfinite(v) for v in values):
            return False
        progress, reference, endpoint, requested = values
        if not 0 <= progress <= self.trajectory_s or not -100 <= reference - 3 <= reference + 10 <= 100:
            return False
        for expanded in (False, True):
            duration = 60 if expanded else 18
            if progress > duration:
                continue
            cycle, phase, fraction, delta = self.phase(progress, expanded)
            key = self.sample_key(progress, expanded)
            if (
                type(context.get("cycle")) is int and context["cycle"] == cycle
                and context.get("phase") == phase and context.get("sample_key") == key
                and math.isclose(endpoint, reference + delta, abs_tol=1e-9, rel_tol=0)
                and math.isclose(requested, reference + fraction * delta, abs_tol=1e-9, rel_tol=0)
            ):
                return True
        return False

    def sample_key(self, progress_s, expanded):
        cycle, phase, _, _ = self.phase(progress_s, expanded)
        if phase == "baseline":
            return "baseline"
        if phase == "endpoint":
            return f"cycle{cycle}_endpoint"
        if phase == "origin" and progress_s >= {1: 9, 2: 16, 3: 37, 4: 58}[cycle] - .2 - 1e-9:
            return f"cycle{cycle}_origin"
        return None

    @staticmethod
    def response_threshold(excursion_raw, noise_raw):
        return max(4, math.ceil(abs(excursion_raw) / 2), 3 * noise_raw)

    @staticmethod
    def return_limit(noise_raw):
        return max(2, 3 * noise_raw)

    @staticmethod
    def encoded_goal(normalized, calibration):
        if (
            type(normalized) not in (int, float) or not math.isfinite(normalized) or not -100 <= normalized <= 100
            or not isinstance(calibration, dict) or calibration.get("drive_mode") != 0
            or not all(type(calibration.get(k)) is int for k in ("range_min", "range_max", "homing_offset", "drive_mode"))
            or not 0 <= calibration["range_min"] < calibration["range_max"] <= 4095
        ):
            return None
        return int(((normalized + 100) / 200) * (
            calibration["range_max"] - calibration["range_min"]
        ) + calibration["range_min"])

    def branch_expectation(self, outcome):
        if not isinstance(outcome, dict):
            return None
        cycles = outcome.get("cycles")
        branch = outcome.get("branch")
        expected = {"negative_distinguished": (18.0, 2, 1), "positive_control": (60.0, 4, 1)}.get(branch)
        if expected is None or not isinstance(cycles, list) or len(cycles) != expected[1]:
            return None
        baseline, noise = outcome.get("baseline_raw"), outcome.get("baseline_noise_raw")
        if type(baseline) is not int or type(noise) is not int or not 0 <= noise <= 4095:
            return None
        reference, calibration = outcome.get("reference_normalized"), outcome.get("calibration")
        if (
            type(reference) not in (int, float) or not math.isfinite(reference)
            or not -100 <= reference - 3 <= reference + 10 <= 100
            or self.encoded_goal(reference, calibration) != baseline
        ):
            return None
        useful = []
        for index, row in enumerate(cycles, 1):
            if not isinstance(row, dict):
                return None
            numeric = ("endpoint_goal_raw", "signed_response_raw", "return_residual_raw", "response_threshold_raw")
            if not all(type(row.get(k)) is int for k in numeric):
                return None
            excursion = row["endpoint_goal_raw"] - baseline
            threshold = self.response_threshold(excursion, noise)
            if (
                row.get("cycle") != index or type(row.get("cycle")) is not int
                or excursion == 0 or (excursion < 0) != (index <= 2)
                or row["endpoint_goal_raw"] != self.encoded_goal(reference + (-3 if index <= 2 else 10), calibration)
                or row["response_threshold_raw"] != threshold
                or row.get("return_qualified") is not True or row.get("adverse") is not False
                or not 0 <= row["return_residual_raw"] <= self.return_limit(noise)
            ):
                return None
            useful.append(row["signed_response_raw"] >= threshold)
        if (branch == "negative_distinguished") != all(useful[:2]):
            return None
        return expected

    def completion_expectation(self, execution):
        if not isinstance(execution, dict):
            return None
        expected = self.branch_expectation(execution.get("characterization"))
        if expected is None:
            return None
        outcome = execution["characterization"]
        residual = outcome.get("final_return_residual_raw")
        if type(residual) is not int or not 0 <= residual <= self.return_limit(outcome["baseline_noise_raw"]):
            return None
        seed = execution.get("original_seed")
        if not isinstance(seed, dict) or seed.get(self.joint) != execution["characterization"]["reference_normalized"]:
            return None
        if (
            execution.get("progress_s") != expected[0]
            or type(execution.get("cycles_completed")) is not int or execution["cycles_completed"] != expected[1]
            or type(execution.get("returns_qualified")) is not int or execution["returns_qualified"] != 1
        ):
            return None
        return expected


LEFT_ELBOW_RESPONSE = SignedElbowResponse()


RECIPES = MappingProxyType(
    {
        "fake-finite": Recipe("fake-finite", "finite", 352.0, 420.0),
        "fake-finite-short": Recipe("fake-finite-short", "finite", 1.0, 12.0),
        "sim-arm-smoke": Recipe("sim-arm-smoke", "finite", 88.0, 180.0),
        "sim-arm-smoke-repeat": Recipe("sim-arm-smoke-repeat", "finite", 352.0, 420.0),
        "sim-arm-hold-body": Recipe("sim-arm-hold-body", "finite", 12.0, 30.0),
        "physical-arm-smoke": Recipe("physical-arm-smoke", "finite", 88.0, 180.0, seed=None),
        "physical-arm-smoke-repeat": Recipe("physical-arm-smoke-repeat", "finite", 352.0, 420.0, seed=None),
        "physical-arm-hold-body": Recipe("physical-arm-hold-body", "finite", 12.0, 30.0, seed=None),
        "physical-left-elbow-compare": Recipe("physical-left-elbow-compare", "finite", 18.0, 60.0, seed=None),
        "physical-left-elbow-dwell-compare": Recipe(
            "physical-left-elbow-dwell-compare", "finite", 24.0, 60.0, seed=None
        ),
        LEFT_ELBOW_RESPONSE.recipe_name: Recipe(
            LEFT_ELBOW_RESPONSE.recipe_name, "finite", LEFT_ELBOW_RESPONSE.trajectory_s,
            LEFT_ELBOW_RESPONSE.live_s, seed=None,
        ),
        "fake-interactive": Recipe("fake-interactive", "interactive", None, 420.0),
    }
)


def uuid_text(value):
    if not isinstance(value, str):
        raise ValueError("UUID string required")
    return str(uuid.UUID(value))
