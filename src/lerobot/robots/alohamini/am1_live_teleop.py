"""Packet16 helpers on the existing AM1 motor-owner thread.

This module owns no device, socket, thread or alternate execution path. The
shared frame contract is imported only by the explicitly selected mode.
"""

import copy
import math
import time
from uuid import UUID

RECIPE = "physical-live-teleop"
STATIC = (
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
HEALTH = ("Status", "Present_Temperature", "Present_Voltage", "Torque_Enable", "Lock")


def _integer(value):
    # SDK integer scalars are accepted, bools and fractional raw values are not.
    import numbers

    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        raise RuntimeError("AM1 teleop raw register is not an integer")
    return int(value)


def binding_valid(robot):
    binding = getattr(robot, "_am1_teleop_binding", None)
    if binding is None:
        return False
    if (
        not isinstance(binding, dict)
        or set(binding) != {"run_id", "host_incarnation", "recipe"}
        or binding["recipe"] != RECIPE
        or robot.config.robot_model != "alohamini1"
        or getattr(robot.config, "no_follower", False)
        or getattr(robot.config, "use_degrees", False)
    ):
        raise RuntimeError("AM1 teleop protected identity/model/units differ")
    if any(
        type(binding[key]) is not str or str(UUID(binding[key])) != binding[key]
        for key in ("run_id", "host_incarnation")
    ):
        raise RuntimeError("AM1 teleop protected identity is invalid")
    return True


def _groups(robot):
    return (
        ("left", robot.left_bus, robot.left_arm_motors),
        ("right", robot.right_bus, robot.right_arm_motors),
    )


def _fault(robot, reason, side, register, raw, timing):
    if getattr(robot, "_am1_teleop_fault", None) is None:
        robot._am1_teleop_fault = {
            "reason": reason,
            "side": side,
            "register": register,
            "raw": copy.deepcopy(raw),
            **timing,
        }
    raise RuntimeError("AM1 teleop " + reason)


def read_groups(robot, registers, *, active=None):
    records = {}
    for side, bus, names in _groups(robot):
        if bus is None or len(names) != 6:
            raise RuntimeError("AM1 teleop requires both exact six-motor follower chains")
        rows, timing = {}, {}
        for register in registers:
            started = time.monotonic()
            values = bus.sync_read(register, names, normalize=False, num_retry=0)
            completed = time.monotonic()
            if set(values) != set(names):
                raise RuntimeError("AM1 teleop incomplete original register acquisition")
            rows[register] = {name: _integer(value) for name, value in values.items()}
            timing[register] = {
                "read_started_at_monotonic_s": started,
                "read_completed_at_monotonic_s": completed,
            }
            baseline = getattr(robot, "_am1_teleop_settings", {}).get(side, {}).get("registers", rows)
            for name, value in rows[register].items():
                reason = None
                if register == "Status" and value != 0:
                    reason = "actual status fault: " + name
                elif register in ("Torque_Enable", "Lock") and active is not None and value != int(active):
                    reason = "actual torque/lock fault: " + name
                elif register == "Operating_Mode" and value != 0:
                    reason = "actual follower operating mode changed: " + name
                elif (
                    register == "Present_Temperature"
                    and not 0 <= value < baseline["Max_Temperature_Limit"][name]
                ):
                    reason = "actual configured temperature limit: " + name
                elif (
                    register == "Present_Voltage"
                    and not baseline["Min_Voltage_Limit"][name]
                    <= value
                    <= baseline["Max_Voltage_Limit"][name]
                ):
                    reason = "actual configured voltage limit: " + name
                if reason is not None:
                    _fault(robot, reason, side, register, rows[register], timing[register])
        records[side] = {"registers": rows, "timing": timing}
    return records


def check_health(robot, records, *, active):
    baseline = robot._am1_teleop_settings
    for side, _bus, names in _groups(robot):
        rows, limits = records[side]["registers"], baseline[side]["registers"]
        for name in names:
            if (
                rows["Status"][name] != 0
                or rows["Torque_Enable"][name] != int(active)
                or rows["Lock"][name] != int(active)
            ):
                raise RuntimeError("AM1 teleop actual status/torque/lock fault: " + name)
            if not 0 <= rows["Present_Temperature"][name] < limits["Max_Temperature_Limit"][name]:
                raise RuntimeError("AM1 teleop actual configured temperature limit: " + name)
            if (
                not limits["Min_Voltage_Limit"][name]
                <= rows["Present_Voltage"][name]
                <= limits["Max_Voltage_Limit"][name]
            ):
                raise RuntimeError("AM1 teleop actual configured voltage limit: " + name)


def prepare_settings(robot):
    """Capture installed position-role settings without writing a tuning profile."""
    if not binding_valid(robot):
        raise RuntimeError("AM1 teleop settings need the protected binding")
    records = read_groups(robot, (*STATIC, *HEALTH), active=False)
    robot._am1_teleop_settings = records
    for side, bus, names in _groups(robot):
        rows = records[side]["registers"]
        for name in names:
            cal = robot._am1_activation_calibration(bus, name)
            expected = {
                "ID": cal[0],
                "Model_Number": 777,
                "Min_Position_Limit": cal[3],
                "Max_Position_Limit": cal[4],
                "Homing_Offset": cal[2],
                "Operating_Mode": 0,
            }
            if any(rows[key][name] != value for key, value in expected.items()):
                raise RuntimeError("AM1 teleop installed identity/calibration/mode differs: " + name)
            if (
                rows["Protection_Current"][name] <= 0
                or not 0 < rows["Max_Temperature_Limit"][name] <= 100
                or not 0 < rows["Min_Voltage_Limit"][name] < rows["Max_Voltage_Limit"][name]
            ):
                raise RuntimeError("AM1 teleop installed protection settings are invalid: " + name)
    check_health(robot, records, active=False)


def verify_settings(robot):
    records = read_groups(robot, (*STATIC, *HEALTH), active=True)
    if any(
        records[side]["registers"][register] != robot._am1_teleop_settings[side]["registers"][register]
        for side, _, _ in _groups(robot)
        for register in STATIC
    ):
        raise RuntimeError("AM1 teleop installed settings changed during startup")
    check_health(robot, records, active=True)
    robot._am1_teleop_active_settings = records


def poll_health(robot):
    """Actual installed device limits; contact/nonresponse keep their role semantics."""
    records = read_groups(robot, (*HEALTH, "Operating_Mode"), active=True)
    check_health(robot, records, active=True)
    for side, _, names in _groups(robot):
        if any(records[side]["registers"]["Operating_Mode"][name] != 0 for name in names):
            raise RuntimeError("AM1 teleop actual follower operating mode changed")
    robot._am1_teleop_health = records


def check_sampled_current(robot, side, values, started, completed):
    limits = robot._am1_teleop_settings[side]["registers"]["Protection_Current"]
    sample = {
        "raw": {name: _integer(values[name]) for name in limits},
        "read_started_at_monotonic_s": started,
        "read_completed_at_monotonic_s": completed,
    }
    samples = getattr(robot, "_am1_teleop_currents", {})
    samples[side] = sample
    robot._am1_teleop_currents = samples
    for name, raw in sample["raw"].items():
        if abs(raw) >= limits[name]:
            _fault(
                robot,
                "actual configured current protection reached: " + name,
                side,
                "Present_Current",
                sample["raw"],
                {
                    key: sample[key]
                    for key in ("read_started_at_monotonic_s", "read_completed_at_monotonic_s")
                },
            )


def before_write(control, robot):
    # Expiry is checked again after local validation and at every real write.
    control.before_live_write(robot)
    if not binding_valid(robot) or robot._am1_teleop_binding != {**control.binding, "recipe": RECIPE}:
        raise RuntimeError("AM1 teleop protected write identity changed")
    for _, bus, names in _groups(robot):
        for name in names:
            if (
                list(robot._am1_activation_calibration(bus, name))
                != control._live_summary["workspace"]["calibration"][name]
            ):
                raise RuntimeError("AM1 teleop calibration changed before write: " + name)
    control.before_live_write(robot)


def workspace(robot, identity, measured, *, goals=None):
    from examples.alohamini.am1_live_teleop import TELEOP_ARM_KEYS, normalized_raw, validate_workspace

    if not binding_valid(robot) or robot._am1_teleop_binding != {**identity, "recipe": RECIPE}:
        raise RuntimeError("AM1 teleop workspace binding differs")
    if set(TELEOP_ARM_KEYS) - set(measured):
        raise RuntimeError("AM1 teleop complete actual feedback is unavailable")
    result = {"version": 1, "units": {}, "bounds": {}, "reference": {}, "calibration": {}, "raw_bounds": {}}
    if goals is None:
        goals = read_groups(robot, ("Goal_Position",))
    for side, bus, names in _groups(robot):
        for name in names:
            key, gripper = name + ".pos", name.endswith("_gripper")
            cal = robot._am1_activation_calibration(bus, name)
            if not bus.apply_drive_mode:
                raise RuntimeError("AM1 teleop calibrated direction application changed")
            seed = goals[side]["registers"]["Goal_Position"][name]
            if not cal[3] <= seed <= cal[4]:
                raise RuntimeError("AM1 teleop actual held goal is outside calibration")
            q0 = measured[key]
            if type(q0) not in (int, float) or not math.isfinite(q0):
                raise RuntimeError("AM1 teleop actual resting pose is invalid")
            if name.endswith("_elbow_flex"):
                low, high = (2011, 2124) if side == "left" else (1847, 1960)
                if not low <= seed <= high:
                    raise RuntimeError("AM1 teleop actual elbow rest is outside supported bench workspace")
            else:
                distance = math.floor((20 * (cal[4] - cal[3]) / 100) if gripper else 10 * 4096 / 360)
                # Reuse the known-clear smoke direction, reversed at an upper endpoint.
                direction = (1 if q0 <= (80 if gripper else 97) else -1) * (-1 if cal[1] else 1)
                endpoint = max(cal[3], min(cal[4], seed + direction * distance))
                low, high = sorted((seed, endpoint))
            if not cal[3] <= low <= high <= cal[4]:
                raise RuntimeError("AM1 teleop bench workspace exceeds actual calibration")
            endpoints = sorted(normalized_raw(raw, cal, gripper=gripper) for raw in (low, high))
            rate = 20.0 if gripper else 5 * 4096 / 360 * 200 / (cal[4] - cal[3])
            result["units"][key] = "gripper_opening_percent" if gripper else "normalized_arm"
            result["bounds"][key] = {"min": endpoints[0], "max": endpoints[1], "rate_per_s": rate}
            result["reference"][key] = q0
            result["calibration"][name] = list(cal)
            result["raw_bounds"][key] = [low, high]
    return validate_workspace(result)


def apply_frame(control, robot, command, marker_key):
    """Validate one original frame, then reuse the ordinary protected executor."""
    from examples.alohamini.am1_live_teleop import (
        TELEOP_ARM_KEYS,
        ZERO_BODY,
        validate_body,
        validate_source,
        validate_target,
    )

    marker = command.get(marker_key)
    base = {"version", "mode", "epoch", "run_id", "host_incarnation", "intent_revision", "action_sequence"}
    if (
        not isinstance(marker, dict)
        or set(marker) != base | {"live_binding", "live_input", "live_admitted"}
        or marker.get("version") != 2
        or type(marker.get("live_admitted")) is not bool
        or any(marker.get(key) != value for key, value in (control.binding or {}).items())
        or any(
            type(marker.get(key)) is not int or marker[key] < 0
            for key in ("epoch", "intent_revision", "action_sequence")
        )
        or marker.get("mode") not in {"active", "pause"}
        or set(command) != {marker_key, *ZERO_BODY}
        or any(type(command[key]) not in (int, float) or command[key] != 0 for key in ZERO_BODY)
    ):
        return False
    if (
        marker["action_sequence"] <= control.accepted_action_sequence
        or marker["intent_revision"] < control.intent_revision
    ):
        return False
    owner = marker["live_binding"]
    if not isinstance(owner, dict) or set(owner) != {
        "service_incarnation",
        "controller_generation",
        "connection_generation",
    }:
        return False
    try:
        if any(type(value) is not str or str(UUID(value)) != value for value in owner.values()):
            return False
    except ValueError:
        return False
    if owner != control.live_binding and (
        marker["mode"] != "pause"
        or control.live_binding is not None
        and marker["intent_revision"] <= control.intent_revision
    ):
        return False
    if marker["mode"] == "active" and (
        marker["intent_revision"] != control.intent_revision
        or not (
            control.state == "active"
            and marker["epoch"] == control.epoch
            or control.state == "paused"
            and marker["epoch"] == control.epoch + 1
            and marker["epoch"] % 2 == 0
        )
    ):
        return False
    original = marker["live_input"]
    if marker["mode"] == "pause" and original is not None:
        return False
    if not control.check_live(robot):
        return False
    if original is not None:
        if (
            not isinstance(original, dict)
            or set(original) != {"target", "body", "source", "grant_id", "seq", "valid_until_monotonic_s"}
            or type(original["seq"]) is not int
            or original["seq"] < 0
            or type(original["valid_until_monotonic_s"]) not in (int, float)
            or not math.isfinite(original["valid_until_monotonic_s"])
            or not control.clock() < original["valid_until_monotonic_s"] <= control.clock() + 0.25 + 1e-9
            or original["seq"] < control._live_summary["last_input_seq"]
            or original["seq"] == control._live_summary["last_input_seq"]
            and original != control.live_sample
        ):
            return False
        try:
            validate_target(original["target"], workspace=control._live_summary["workspace"])
            validate_body(original["body"])
            if original != control.live_sample:
                validate_source(
                    original["source"], grant_id=original["grant_id"], previous=control._teleop_source
                )
        except (ValueError, KeyError):
            return False
    standard = {key: marker[key] for key in base}
    standard["version"] = 1
    action = {**ZERO_BODY, marker_key: standard}
    if marker["mode"] == "pause":
        accepted = control._apply_standard(robot, action)
        if not accepted:
            return False
        changed = owner != control.live_binding
        control.live_binding, control.live_sample = copy.deepcopy(owner), None
        if changed:
            control._live_summary["last_input_seq"] = -1
        measured = robot.get_observation()
        goals = read_groups(robot, ("Goal_Position",))
        held = {
            name + ".pos": value
            for side, bus, names in _groups(robot)
            for name, value in robot._normalize_arm_feedback(
                bus, goals[side]["registers"]["Goal_Position"]
            ).items()
        }
        literal = {side: dict(goals[side]["registers"]["Goal_Position"]) for side, _, _ in _groups(robot)}
        if (
            getattr(control, "_teleop_hold_epoch", None) == control.epoch
            and control._teleop_held_goals != literal
        ):
            raise RuntimeError("AM1 teleop exact raw held goals changed during hold")
        control._teleop_hold_epoch, control._teleop_held_goals = control.epoch, literal
        if "workspace" not in control._live_summary:
            control._live_summary["workspace"] = workspace(robot, control.binding, measured, goals=goals)
            control._live_summary["reference"] = dict(control._live_summary["workspace"]["reference"])
        if marker["live_admitted"] and "live_admitted_at_monotonic_s" not in control._live_summary:
            now = control.clock()
            control._live_summary.update(
                live_admitted_at_monotonic_s=now, live_deadline_monotonic_s=now + 300
            )
        control._live_summary.update(
            state="held",
            cursor=held,
            accepted_target=dict(held),
            body=dict(ZERO_BODY),
            held_goals_raw=literal,
            held_goal_acquisition=goals,
            cursor_feedback_sequence=control.observation_id,
            cursor_intent_revision=control.intent_revision,
        )
        control.live_cursor_at = control.clock()
        return True
    if "live_deadline_monotonic_s" not in control._live_summary:
        return False
    if original is None:
        # Readiness acknowledges an existing literal raw hold, never a synthetic frame.
        return control._apply_standard(robot, action)
    if control.state == "paused" and control._live_summary["state"] == "expired":
        return False
    if control.state == "paused" and (
        any(original["body"].get(key, 0) != 0 for key in ZERO_BODY)
        or "lift_axis.height_mm" in original["body"]
    ):
        return False
    if original != control.live_sample:
        control.live_sample = copy.deepcopy(original)
        control._teleop_source = copy.deepcopy(original["source"])
        control._live_summary.update(
            last_input_seq=original["seq"],
            source=copy.deepcopy(original["source"]),
            grant_id=original["grant_id"],
            valid_until_monotonic_s=original["valid_until_monotonic_s"],
            accepted_at_monotonic_s=control.clock(),
        )
    elapsed = max(0, control.clock() - control.live_cursor_at)
    cursor = control._live_summary["cursor"]
    following = {
        key: cursor[key]
        + max(
            -elapsed * control._live_summary["workspace"]["bounds"][key]["rate_per_s"],
            min(
                elapsed * control._live_summary["workspace"]["bounds"][key]["rate_per_s"],
                original["target"][key] - cursor[key],
            ),
        )
        for key in TELEOP_ARM_KEYS
    }
    action.update(following)
    for key in ZERO_BODY:
        action.pop(key, None)
    action.update(original["body"])
    robot._am1_live_before_write = lambda: before_write(control, robot)
    try:
        accepted = control._apply_standard(robot, action)
    except Exception as error:
        from .alohamini_host import AM1LiveInputExpired

        if isinstance(error, AM1LiveInputExpired):
            return False
        raise
    if accepted:
        control._live_summary.update(
            state="active",
            cursor=following,
            accepted_target={key: (control.accepted_action or {}).get(key) for key in TELEOP_ARM_KEYS},
            body=copy.deepcopy(original["body"]),
            write_acknowledged=False,
        )
        control.live_cursor_at = control.clock()
    return accepted
