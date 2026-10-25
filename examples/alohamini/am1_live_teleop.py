"""Closed AM1 leader-frame contract shared by producer, authority and native owner.

PC monotonic acquisition clocks are compared only with PC clocks. The original
Pi grant supplies the conservative native deadline; reception never renews it.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from uuid import UUID

TELEOP_RECIPE = "physical-live-teleop"
TELEOP_ARM_KEYS = tuple(
    f"arm_{side}_{joint}.pos"
    for side in ("left", "right")
    for joint in ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
)
ZERO_BODY = {"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 0}


@dataclass(frozen=True)
class TeleopSpec:
    recipe: str = TELEOP_RECIPE
    profile: str = "WholeRobotTeleop"
    live_s: float = 300.0

    def summary(self):
        return {
            "recipe": self.recipe,
            "profile": self.profile,
            "live_s": self.live_s,
            "label": "USB or virtual leaders",
            "input_fields": list(TELEOP_ARM_KEYS),
        }


TELEOP_SPEC = TeleopSpec()


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _uuid(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError("canonical source/grant UUID required")


def map_leader_action(values):
    """Use the existing exact prefix mapping, without raw-value fallbacks."""
    if not isinstance(values, dict):
        raise ValueError("complete leader-format object required")
    mapped = {key if key.startswith("arm_") else "arm_" + key: value for key, value in values.items()}
    if len(mapped) != len(values):
        raise ValueError("duplicate mapped leader joint")
    return validate_target(mapped)


def validate_target(target, *, workspace=None):
    if not isinstance(target, dict) or set(target) != set(TELEOP_ARM_KEYS):
        raise ValueError("exact twelve named AM1 arm/gripper targets required")
    for key, value in target.items():
        low = 0 if key.endswith("gripper.pos") else -100
        if not number(value) or not low <= value <= 100:
            raise ValueError("invalid normalized AM1 target: " + key)
        if workspace is not None:
            bound = workspace["bounds"][key]
            if not bound["min"] - 1e-9 <= value <= bound["max"] + 1e-9:
                raise ValueError("target left native bench workspace: " + key)
    return dict(target)


def validate_body(body):
    if not isinstance(body, dict) or set(body) - {*ZERO_BODY, "lift_axis.height_mm"}:
        raise ValueError("closed named AM1 body/lift intent required")
    required = {"x.vel", "y.vel", "theta.vel"}
    if not required <= set(body) or not ({"lift_axis.vel", "lift_axis.height_mm"} & set(body)):
        raise ValueError("complete AM1 body/lift intent required")
    for key, limit in (("x.vel", 0.15), ("y.vel", 0.15), ("theta.vel", 45), ("lift_axis.vel", 200)):
        if key in body and (not number(body[key]) or abs(body[key]) > limit):
            raise ValueError("invalid body/lift units or limit: " + key)
    if "lift_axis.height_mm" in body:
        height = body["lift_axis.height_mm"]
        if not number(height) or not -1000 <= height <= 1000 or body.get("lift_axis.vel", 0) != 0:
            raise ValueError("lift height requires finite millimeters and zero/absent velocity")
    return dict(body)


def validate_source(source, *, grant_id, previous=None):
    fields = {
        "kind",
        "source_id",
        "sample_seq",
        "acquired_at_monotonic_s",
        "grant_received_at_monotonic_s",
        "sent_at_monotonic_s",
        "grant_id",
    }
    if not isinstance(source, dict) or set(source) != fields:
        raise ValueError("closed original acquisition evidence required")
    _uuid(source["source_id"])
    _uuid(source["grant_id"])
    if source["kind"] not in {"usb-leaders", "virtual-leaders"} or source["grant_id"] != grant_id:
        raise ValueError("source kind or original grant differs")
    if type(source["sample_seq"]) is not int or not 0 <= source["sample_seq"] < 2**63:
        raise ValueError("source acquisition sequence invalid")
    acquired, received, sent = (
        source[k] for k in ("acquired_at_monotonic_s", "grant_received_at_monotonic_s", "sent_at_monotonic_s")
    )
    if (
        not all(number(v) for v in (acquired, received, sent))
        or not 0 <= received <= acquired <= sent
        or sent - received >= 0.25
    ):
        raise ValueError("acquisition must follow original grant receipt and remain fresh")
    if previous is not None and (
        source["kind"] != previous["kind"]
        or source["source_id"] != previous["source_id"]
        or source["sample_seq"] <= previous["sample_seq"]
        or source["grant_id"] == previous["grant_id"]
        or acquired <= previous["acquired_at_monotonic_s"]
        or received < previous["sent_at_monotonic_s"]
    ):
        raise ValueError("old capture, changed source or sequence cannot receive a new grant")
    return copy.deepcopy(source)


def normalized_raw(raw, calibration, *, gripper=False):
    _, direction, _, low, high = calibration
    value = (raw - low) / (high - low) * (100 if gripper else 200)
    if not gripper:
        value -= 100
    return (100 - value if gripper else -value) if direction else value


LEFT_ENTRY_WORKSPACE_PROFILE = "ordinary-arms-left-entry-p16-1164-1324"

CURRENT_REST_WORKSPACE_PROFILES = {
    "left-reach": "left-current-rest-1051-1164",
    "ordinary-arms": "ordinary-arms-left-current-rest-1051-1164",
}


def validate_workspace(workspace):
    fields = {"version", "units", "bounds", "reference", "calibration", "raw_bounds"}
    if isinstance(workspace, dict) and "workspace_profile" in workspace:
        if workspace["workspace_profile"] not in (*CURRENT_REST_WORKSPACE_PROFILES.values(), LEFT_ENTRY_WORKSPACE_PROFILE):
            raise ValueError("unknown native workspace profile")
        fields.add("workspace_profile")
    if (
        not isinstance(workspace, dict)
        or set(workspace) != fields
        or type(workspace["version"]) is not int
        or workspace["version"] != 1
    ):
        raise ValueError("closed native workspace required")
    entry = workspace.get("workspace_profile") == LEFT_ENTRY_WORKSPACE_PROFILE
    keys = set(TELEOP_ARM_KEYS)
    for field in ("units", "bounds", "reference", "raw_bounds"):
        if not isinstance(workspace[field], dict) or set(workspace[field]) != keys:
            raise ValueError("complete native workspace " + field + " required")
    calibrations = workspace["calibration"]
    if not isinstance(calibrations, dict) or set(calibrations) != {k.removesuffix(".pos") for k in keys}:
        raise ValueError("complete native calibration required")
    for key in TELEOP_ARM_KEYS:
        gripper = key.endswith("gripper.pos")
        cal = calibrations[key.removesuffix(".pos")]
        if (
            not isinstance(cal, (list, tuple))
            or len(cal) != 5
            or any(type(v) is not int for v in cal)
            or cal[1] not in (0, 1)
            or not 0 <= cal[3] < cal[4] <= 4095
        ):
            raise ValueError("invalid actual calibration")
        expected_id = (
            "shoulder_pan",
            "shoulder_lift",
            "elbow_flex",
            "wrist_flex",
            "wrist_roll",
            "gripper",
        ).index(key.split("_", 2)[2].removesuffix(".pos")) + 1
        if cal[0] != expected_id:
            raise ValueError("native motor mapping differs")
        bounds, raw = workspace["bounds"][key], workspace["raw_bounds"][key]
        if (
            workspace["units"][key] != ("gripper_opening_percent" if gripper else "normalized_arm")
            or not isinstance(bounds, dict)
            or set(bounds) != {"min", "max", "rate_per_s"}
            or not all(number(v) for v in bounds.values())
            or not 0 < bounds["rate_per_s"] <= 100
            or not (0 if gripper else -100) <= bounds["min"] <= bounds["max"] <= 100
            or not number(workspace["reference"][key])
            or not isinstance(raw, (tuple, list))
            or len(raw) != 2
            or any(type(v) is not int for v in raw)
            or not cal[3] <= raw[0] <= raw[1] <= cal[4]
        ):
            raise ValueError("invalid native workspace geometry/units")
        endpoints = sorted(normalized_raw(v, cal, gripper=gripper) for v in raw)
        if any(abs(a - b) > 1e-7 for a, b in zip(endpoints, (bounds["min"], bounds["max"]), strict=True)):
            raise ValueError("native raw/normalized workspace roundtrip differs")
        if entry:
            if key == "arm_left_elbow_flex.pos":
                if (list(cal) != [3, 0, 1845, 864, 3073] or raw[0] != 1164
                        or not 1164 < raw[1] <= 1324
                        or abs(workspace["reference"][key] - normalized_raw(raw[1], cal)) > 1e-7
                        or bounds["rate_per_s"] > 4096 / 360 * 200 / (3073-864) + 1e-9):
                    raise ValueError("entry workspace requires its original finite inward seed/rate")
            elif raw[0] != raw[1]:
                raise ValueError("entry workspace must hold every other admitted Goal exactly")
        elif key.endswith("elbow_flex.pos"):
            profiled = "workspace_profile" in workspace and key.startswith("arm_left_")
            expected = ([1051, 1164] if profiled else [2011, 2124]) if key.startswith("arm_left_") else [1847, 1960]
            if profiled and (list(cal) != [3, 0, 1845, 864, 3073]
                             or not bounds["min"] <= workspace["reference"][key] <= bounds["max"]):
                raise ValueError("current-rest workspace requires its actual calibrated reference")
            if list(raw) != expected:
                raise ValueError("native elbow workspace differs from demonstrated command bounds")
        maximum_span = (160 if entry and key == "arm_left_elbow_flex.pos" else
                        0.2 * (cal[4] - cal[3]) if gripper else math.floor(10 * 4096 / 360))
        maximum_rate = 20.0 if gripper else 5 * 4096 / 360 * 200 / (cal[4] - cal[3])
        if raw[1] - raw[0] > maximum_span + 1e-9 or bounds["rate_per_s"] > maximum_rate + 1e-9:
            raise ValueError("native workspace exceeds role-specific bench travel/rate")
    return copy.deepcopy(workspace)
