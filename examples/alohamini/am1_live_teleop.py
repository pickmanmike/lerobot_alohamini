"""Closed AM1 leader-frame contract shared by producer, authority and native owner.

PC monotonic acquisition clocks are compared only with PC clocks. The original
Pi grant supplies the conservative native deadline; reception never renews it.
"""

from __future__ import annotations

import copy
import hashlib
import json
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


LEFT_ENTRY_WORKSPACE_PROFILE = "ordinary-arms-left-entry-p16-1108-1268"
LEFT_ENTRY_ENDPOINT_RAW = 1108
LEFT_ENTRY_START_MIN_RAW = 1165
LEFT_ENTRY_START_MAX_RAW = 1268
# Old geometry remains readable as evidence; fresh requests use only the current profile above.
_LEFT_ENTRY_WORKSPACE_DESCRIPTORS = {
    "ordinary-arms-left-entry-p16-1164-1324": (1164, 1165, 1324),
    LEFT_ENTRY_WORKSPACE_PROFILE: (LEFT_ENTRY_ENDPOINT_RAW, LEFT_ENTRY_START_MIN_RAW, LEFT_ENTRY_START_MAX_RAW),
}

CURRENT_REST_WORKSPACE_PROFILES = {
    "left-reach": "left-current-rest-1051-1164",
    "ordinary-arms": "ordinary-arms-left-current-rest-1051-1164",
}

# Fixed initial ordinary region, expressed in each installed calibrated encoder.
# The non-elbow arm rows include a declared three-count normal rest margin;
# endpoints at calibration limits remain literal limits, never runtime clipping.
ORDINARY_AM1_ARMS_PROFILE = "ordinary-am1-arms-v1"
ORDINARY_AM1_ARM_CALIBRATION = {
    "arm_left_shoulder_pan": (1, 0, -1761, 731, 3438),
    "arm_left_shoulder_lift": (2, 0, 515, 848, 3223),
    "arm_left_elbow_flex": (3, 0, 1845, 864, 3073),
    "arm_left_wrist_flex": (4, 0, -1939, 1314, 3208),
    "arm_left_wrist_roll": (5, 0, 879, 0, 4095),
    "arm_left_gripper": (6, 0, 933, 1886, 3401),
    "arm_right_shoulder_pan": (1, 0, -1719, 752, 3456),
    "arm_right_shoulder_lift": (2, 0, -1852, 1331, 3264),
    "arm_right_elbow_flex": (3, 0, -658, 827, 3039),
    "arm_right_wrist_flex": (4, 0, -1609, 1461, 3167),
    "arm_right_wrist_roll": (5, 0, 1869, 0, 4095),
    "arm_right_gripper": (6, 0, -1811, 1657, 3191),
}
ORDINARY_AM1_RAW_BOUNDS = {
    "arm_left_shoulder_pan.pos": (2674, 2793),
    "arm_left_shoulder_lift.pos": (3106, 3223),
    "arm_left_elbow_flex.pos": (1051, 1164),
    "arm_left_wrist_flex.pos": (2762, 2881),
    "arm_left_wrist_roll.pos": (2421, 2540),
    "arm_left_gripper.pos": (1886, 3102),
    "arm_right_shoulder_pan.pos": (2876, 2995),
    "arm_right_shoulder_lift.pos": (3136, 3255),
    "arm_right_elbow_flex.pos": (1847, 1960),
    "arm_right_wrist_flex.pos": (3127, 3167),
    "arm_right_wrist_roll.pos": (3140, 3259),
    # RIGHT-specific practical qualification region (about30.052..87.875 opening%).
    # This is not a claim that every closure or combined pose has been qualified.
    "arm_right_gripper.pos": (2118, 3005),
}


def validate_ordinary_arm_profile(value):
    """Closed session policy; authority still comes from ordinary owned Start."""
    if (type(value) is not dict or set(value) != {"profile", "right_elbow_p"}
            or value["profile"] != ORDINARY_AM1_ARMS_PROFILE
            or type(value["right_elbow_p"]) is not int or value["right_elbow_p"] not in (16, 32)):
        raise ValueError("exact ordinary AM1 arm profile and RIGHT P16/P32 required")
    return dict(value)


def validate_workspace(workspace):
    fields = {"version", "units", "bounds", "reference", "calibration", "raw_bounds"}
    if isinstance(workspace, dict) and "workspace_profile" in workspace:
        if workspace["workspace_profile"] not in (*CURRENT_REST_WORKSPACE_PROFILES.values(), *_LEFT_ENTRY_WORKSPACE_DESCRIPTORS, ORDINARY_AM1_ARMS_PROFILE):
            raise ValueError("unknown native workspace profile")
        fields.add("workspace_profile")
    if (
        not isinstance(workspace, dict)
        or set(workspace) != fields
        or type(workspace["version"]) is not int
        or workspace["version"] != 1
    ):
        raise ValueError("closed native workspace required")
    entry_descriptor = _LEFT_ENTRY_WORKSPACE_DESCRIPTORS.get(workspace.get("workspace_profile"))
    entry = entry_descriptor is not None
    ordinary = workspace.get("workspace_profile") == ORDINARY_AM1_ARMS_PROFILE
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
        if ordinary:
            if (tuple(cal) != ORDINARY_AM1_ARM_CALIBRATION[key.removesuffix(".pos")]
                    or tuple(raw) != ORDINARY_AM1_RAW_BOUNDS[key]
                    or not bounds["min"] <= workspace["reference"][key] <= bounds["max"]):
                raise ValueError("ordinary AM1 fixed region/calibration/reference differs")
        elif entry:
            if key == "arm_left_elbow_flex.pos":
                if (list(cal) != [3, 0, 1845, 864, 3073] or raw[0] != entry_descriptor[0]
                        or not entry_descriptor[1] <= raw[1] <= entry_descriptor[2]
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
        extended_left_closing = (workspace.get("workspace_profile") == CURRENT_REST_WORKSPACE_PROFILES["ordinary-arms"]
                                 and key == "arm_left_gripper.pos" and list(cal) == [6, 0, 933, 1886, 3401]
                                 and list(raw) == [1886, 3102])
        maximum_span = (ORDINARY_AM1_RAW_BOUNDS[key][1] - ORDINARY_AM1_RAW_BOUNDS[key][0] if ordinary else
                        160 if entry and key == "arm_left_elbow_flex.pos" else
                        3102 - 1886 if extended_left_closing else
                        0.2 * (cal[4] - cal[3]) if gripper else math.floor(10 * 4096 / 360))
        maximum_rate = 20.0 if gripper else 5 * 4096 / 360 * 200 / (cal[4] - cal[3])
        if raw[1] - raw[0] > maximum_span + 1e-9 or bounds["rate_per_s"] > maximum_rate + 1e-9:
            raise ValueError("native workspace exceeds role-specific bench travel/rate")
    return copy.deepcopy(workspace)


# Ordinary settings proof is shared by the native owner and HTTP-only readers.
ORDINARY_ARM_STATIC = (
    "ID",
    "Model_Number",
    "Firmware_Major_Version",
    "Firmware_Minor_Version",
    "Min_Position_Limit",
    "Max_Position_Limit",
    "Homing_Offset",
    "Phase",
    "P_Coefficient",
    "I_Coefficient",
    "D_Coefficient",
    "Acceleration",
    "Maximum_Acceleration",
    "Protection_Current",
    "Max_Temperature_Limit",
    "Min_Voltage_Limit",
    "Max_Voltage_Limit",
    "Operating_Mode",
)


def _ordinary_settings_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def validate_ordinary_arm_profile_binding(value):
    fields = {"version", "profile", "workload_segment", "right_elbow_p", "run_id", "host_incarnation", "recipe"}
    if (type(value) is not dict or set(value) != fields or type(value["version"]) is not int
            or value["version"] != 1 or value["workload_segment"] != "ordinary-arms"
            or value["recipe"] != "physical-live-teleop"):
        raise ValueError("exact ordinary arm run/profile binding required")
    validate_ordinary_arm_profile({key: value[key] for key in ("profile", "right_elbow_p")})
    try:
        if any(type(value[key]) is not str or str(UUID(value[key])) != value[key]
               for key in ("run_id", "host_incarnation")):
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        raise ValueError("ordinary arm run/host identity differs") from None
    return dict(value)


_ORDINARY_EXTRA = ("CW_Dead_Zone", "CCW_Dead_Zone", "Max_Torque_Limit", "Torque_Limit")
_ORDINARY_STATE = ("Torque_Enable", "Lock", "Present_Position", "Goal_Position", "Present_Current", "Present_Voltage", "Status")
_ORDINARY_SOURCE_PATHS = (
    "examples/alohamini/am1_live_teleop.py", "src/lerobot/robots/alohamini/am1_live_teleop.py",
    "src/lerobot/robots/alohamini/am1_elbow_integral.py", "src/lerobot/robots/alohamini/alohamini.py",
    "src/lerobot/robots/alohamini/alohamini_host.py",
)


def validate_ordinary_arm_profile_receipt(value, *, run_id, host_incarnation, restored=False):
    """Pure common proof for the settings emitter, cleanup reader and private audit."""
    STATIC = ORDINARY_ARM_STATIC

    fields = {"version", "kind", "binding", "calibration", "source_files_sha256", "original_registers",
              "active_registers", "restored_registers", "original_settings_sha256", "prepared_at_monotonic_s",
              "configured_at_monotonic_s", "restored_at_monotonic_s", "restore_required", "state"}
    def require(condition):
        if not condition:
            raise ValueError("ordinary arm settings/profile/restoration proof differs")
    require(type(value) is dict and set(value) == fields and type(value["version"]) is int
            and value["version"] == 1 and value["kind"] == "ordinary-arm-profile")
    binding = validate_ordinary_arm_profile_binding(value["binding"])
    require(binding["run_id"] == run_id and binding["host_incarnation"] == host_incarnation)
    require(type(value["calibration"]) is dict
            and value["calibration"] == {key: list(cal) for key, cal in ORDINARY_AM1_ARM_CALIBRATION.items()}
            and all(type(cal) is list and all(type(raw) is int for raw in cal) for cal in value["calibration"].values()))
    hashes = value["source_files_sha256"]
    require(type(hashes) is dict and set(hashes) == set(_ORDINARY_SOURCE_PATHS)
            and all(type(h) is str and len(h) == 64 and set(h) <= set("0123456789abcdef") for h in hashes.values()))
    originals = value["original_registers"]
    require(type(originals) is dict and set(originals) == {"left", "right"})
    registers = {*STATIC, *_ORDINARY_EXTRA, *_ORDINARY_STATE}
    def check_rows(rows, phase):
        require(type(rows) is dict and set(rows) == {"left", "right"})
        for side in ("left", "right"):
            row, original = rows[side], originals[side]
            require(type(row) is dict and set(row) == registers
                    and all(type(raw) is int for raw in row.values()))
            cal = ORDINARY_AM1_ARM_CALIBRATION["arm_" + side + "_elbow_flex"]
            require(row["ID"] == 3 and row["Model_Number"] == 777
                    and row["Firmware_Major_Version"] == 3 and row["Firmware_Minor_Version"] == 10 and row["Phase"] == 12
                    and row["Min_Position_Limit"] == cal[3] and row["Max_Position_Limit"] == cal[4]
                    and row["Homing_Offset"] == cal[2] and row["Operating_Mode"] == 0
                    and row["I_Coefficient"] == 0 and row["D_Coefficient"] == 32
                    and row["Protection_Current"] > 0 and 0 < row["Max_Temperature_Limit"] <= 100
                    and 0 < row["Min_Voltage_Limit"] <= row["Present_Voltage"] <= row["Max_Voltage_Limit"]
                    and abs(row["Present_Current"]) < row["Protection_Current"] and row["Status"] == 0
                    and 0 <= row["CW_Dead_Zone"] <= 255 and 0 <= row["CCW_Dead_Zone"] <= 255
                    and 0 < row["Torque_Limit"] <= row["Max_Torque_Limit"] <= 1000)
            if phase == "original":
                require(row["P_Coefficient"] == 16 and row["Torque_Enable"] == 0 and row["Lock"] in (0, 1)
                        and row["Maximum_Acceleration"] == 254
                        and row["Acceleration"] in ((0, 254) if side == "left" else (0,)))
            else:
                expected = dict(original)
                if phase == "active":
                    expected.update(P_Coefficient=32 if side == "left" else binding["right_elbow_p"],
                                    Acceleration=254 if side == "left" else 0)
                require(all(row[key] == expected[key] for key in (*STATIC, *_ORDINARY_EXTRA)))
                require(row["Torque_Enable"] == (1 if phase == "active" else 0)
                        and row["Lock"] == (1 if phase == "active" else original["Lock"]))
    check_rows(originals, "original")
    require(value["original_settings_sha256"] == hashlib.sha256(_ordinary_settings_json(originals)).hexdigest())
    prepared, configured, finished = (value[key] for key in
        ("prepared_at_monotonic_s", "configured_at_monotonic_s", "restored_at_monotonic_s"))
    require(type(prepared) in (int, float) and math.isfinite(prepared) and prepared >= 0)
    require(configured is None or type(configured) in (int, float) and math.isfinite(configured) and configured >= prepared)
    if value["active_registers"] is not None:
        require(configured is not None)
        check_rows(value["active_registers"], "active")
    if restored:
        require(value["state"] == "restored" and value["restore_required"] is False
                and type(finished) in (int, float) and math.isfinite(finished)
                and finished >= (prepared if configured is None else configured))
        check_rows(value["restored_registers"], "restored")
    else:
        require(value["state"] == "active" and value["restore_required"] is True
                and value["active_registers"] is not None and value["restored_registers"] is None and finished is None)
    return copy.deepcopy(value)
