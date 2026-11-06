"""One reusable local orchestration of the existing interactive adapter input."""
import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

from examples.alohamini.am1_live_teleop import (
    CURRENT_REST_WORKSPACE_PROFILES, ORDINARY_AM1_ARMS_PROFILE, TELEOP_ARM_KEYS, ZERO_BODY, normalized_raw, validate_ordinary_arm_profile, validate_target, validate_workspace,
)
from examples.alohamini.am1_session_contract import PRACTICAL_ELBOW_COUNTS_PER_REVOLUTION
from tools.am1_leader_adapter import (
    EXTENDED_GRASP_CLOSING_RAW, SUPPORTED_GRASP_CLOSING_RAW, _qualified_command_reference, resolve_ordinary_config,
)

KEY = "arm_left_elbow_flex.pos"
GRIP = "arm_left_gripper.pos"
ROOT = Path(__file__).resolve().parents[1]


def finite(value, *, zero=False):
    value = float(value)
    if not math.isfinite(value) or value < 0 or not zero and value == 0:
        raise ValueError("finite positive value required")
    return value


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--side", choices=("left", "right"), default="left", help="selected elbow/gripper; RIGHT uses relative or admitted-span out-return")
    parser.add_argument("--plan", choices=("out-return", "bidirectional-midpoint", "lower-return-gripper", "grasp-return", "gripper-close-return", "gripper-open-finish", "ordinary-arms"), default="out-return")
    parser.add_argument("--workpose", choices=("relative", "midpoint", "admitted-span"), default="relative")
    parser.add_argument("--approach-from-below", action="store_true")
    parser.add_argument("--gripper-cycle", action="store_true", help="out-return: same elbow workpose, declared10-point gripper excursion then original")
    parser.add_argument("--gripper-direction", choices=("opening", "closing", "admitted"), default="closing")
    closing = parser.add_mutually_exclusive_group()
    closing.add_argument("--supported-grasp-closing", action="store_true",
                        help="grasp-return only: admit supported raw2799..3102 and require full10-point closing")
    closing.add_argument("--extended-grasp-closing", action="store_true",
                        help="explicit current-rest raw1886..3102 with a declared absolute closing/opening endpoint")
    parser.add_argument("--gripper-closed-opening", type=lambda v: finite(v, zero=True),
                        help="explicit absolute gripper opening percentage; requires extended grasp closing")
    parser.add_argument("--gripper-open-opening", type=lambda v: finite(v, zero=True),
                        help="explicit absolute opening percentage; requires gripper-open-finish and extended grasp closing")
    parser.add_argument("--degrees", type=lambda v: finite(v), help="relative out-return encoder degrees (default4)")
    parser.add_argument("--direction", choices=("decreasing", "increasing"), default=None,
                        help="normalized-coordinate direction; explicit for grasp-return, otherwise defaults to decreasing")
    for name, default in (("ramp-seconds", None), ("window-seconds", None), ("intent-ttl-seconds", 90.),
                          ("comparison-dwell-seconds", 6.), ("approach-room-degrees", 1.)):
        parser.add_argument("--"+name, type=lambda v: finite(v), default=default)
    parser.add_argument("--dwell-seconds", type=lambda v: finite(v, zero=True))
    parser.add_argument("--ordinary-config", help="same stable private ordinary-use JSON as the adapter")
    parser.add_argument("--operating-profile", choices=(ORDINARY_AM1_ARMS_PROFILE,))
    parser.add_argument("--right-elbow-p", type=int, choices=(16, 32))
    parser.add_argument("--bindings", type=Path, help="explicit current matched deployment binding")
    parser.add_argument("--url")
    parser.add_argument("--ca")
    parser.add_argument("--storage-state")
    parser.add_argument("--output-root")
    args = parser.parse_args(argv)
    args.duration_seconds = args.window_seconds
    try:
        resolve_ordinary_config(args)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    args.window_seconds = args.duration_seconds if args.duration_seconds is not None else 90.
    args.ramp_seconds = args.ramp_seconds if args.ramp_seconds is not None else 3. if args.plan == "ordinary-arms" else 10.
    if args.bindings is None:
        parser.error("--bindings or --ordinary-config required")
    args.bindings = Path(args.bindings)
    for name, default in (("url", "https://192.168.1.134:8443"), ("ca", ".cache/am1-session-pi-01/tls/ca.pem"),
                          ("storage_state", ".cache/am1-physical-adapter-01/physical-runs/browser-state.private.json"),
                          ("output_root", ".cache/am1-teleop-consolidation-16/physical-runs")):
        if getattr(args, name) is None:
            setattr(args, name, default)
    if args.plan == "ordinary-arms" and (args.ordinary_arm_profile is None or args.side != "left"
        or args.workpose != "relative" or args.degrees is not None or args.direction is not None
        or args.approach_from_below or args.gripper_cycle or args.supported_grasp_closing or args.extended_grasp_closing
        or args.gripper_closed_opening is not None or args.gripper_open_opening is not None
        or args.gripper_direction not in ("opening", "closing")):
        parser.error("ordinary-arms requires the ordinary AM1 policy and its full declared two-arm amplitudes, without legacy selectors")
    args.degrees_explicit, args.direction_explicit = args.degrees is not None, args.direction is not None
    if args.degrees is None:
        args.degrees = 4.
    if args.workpose == "admitted-span" and args.side != "right":
        parser.error("admitted-span is only supported for RIGHT out-return")
    if args.side == "right":
        try:
            _right_options(args)
        except ValueError as error:
            parser.error(str(error))
    opening_only = args.plan == "gripper-open-finish"
    if args.dwell_seconds is None:
        args.dwell_seconds = 2. if opening_only or args.plan == "ordinary-arms" else 3.
    if max(args.ramp_seconds, args.window_seconds, args.intent_ttl_seconds) > 300:
        parser.error("existing input/native durations are bounded by300seconds")
    if (args.workpose == "midpoint" or args.approach_from_below) and not (
        args.plan == "out-return" and args.gripper_cycle and args.workpose == "midpoint" and args.approach_from_below):
        parser.error("midpoint requires out-return, gripper-cycle and explicit approach-from-below")
    if args.gripper_cycle and (args.plan != "out-return" or args.side == "left" and args.workpose == "relative" and args.direction != "increasing"):
        parser.error("relative gripper-cycle requires out-return and explicit increasing direction")
    if args.plan == "grasp-return" and (
        args.gripper_direction != "admitted" or args.degrees != 4. or args.direction is None
        or args.ramp_seconds != 10. or args.comparison_dwell_seconds != 3. or args.dwell_seconds != 3.
    ):
        parser.error("grasp-return requires admitted gripper,4degrees with explicit direction,10second ramp and3second holds")
    if args.supported_grasp_closing and (args.plan != "grasp-return" or args.gripper_direction != "admitted"):
        parser.error("supported grasp closing requires grasp-return and explicit admitted gripper policy")
    absolute_endpoint = args.gripper_open_opening if opening_only else args.gripper_closed_opening
    if args.extended_grasp_closing and (
        args.plan not in ("grasp-return", "gripper-close-return", "gripper-open-finish") or args.gripper_direction != "admitted"
        or absolute_endpoint is None or absolute_endpoint > 100.
    ):
        parser.error("extended grasp interval requires admitted gripper, an explicit0..100endpoint and selected grasp/opening plan")
    if args.gripper_closed_opening is not None and not args.extended_grasp_closing:
        parser.error("absolute closing endpoint requires explicit extended grasp closing")
    if args.gripper_open_opening is not None and not (opening_only and args.extended_grasp_closing):
        parser.error("absolute opening endpoint requires gripper-open-finish and explicit extended grasp closing")
    if opening_only and (not args.extended_grasp_closing or args.gripper_closed_opening is not None
                         or args.dwell_seconds != 2.):
        parser.error("gripper-open-finish requires explicit extended scope, only an opening endpoint and2second dwell")
    if args.plan == "gripper-close-return" and not args.extended_grasp_closing:
        parser.error("gripper-close-return requires explicit extended grasp closing")
    if args.direction is None:
        args.direction = "decreasing"
    return args


def console_path(root):
    return root / ".cache/am1-knowledge-reuse-18" / ("live-phase-"+str(uuid4())+".console.jsonl")


def live(snapshot):
    return snapshot["run"].get("live_input") or snapshot["evidence"]["live_input"]


def _right_options(options):
    if (options.plan != "out-return" or options.workpose not in ("relative", "admitted-span") or options.approach_from_below
        or options.supported_grasp_closing or options.extended_grasp_closing
        or options.gripper_closed_opening is not None or options.gripper_open_opening is not None
        or options.gripper_cycle and options.gripper_direction not in ("opening", "closing")):
        raise ValueError("RIGHT requires out-return without LEFT-only selections and an explicit opening/closing gripper policy")
    if options.workpose == "admitted-span" and (
        getattr(options, "degrees_explicit", False) or getattr(options, "direction_explicit", False)
        or options.gripper_cycle and options.gripper_direction != "closing"
    ):
        raise ValueError("RIGHT admitted-span uses fixed admitted bounds, no relative degrees/direction, and optional closing/reopen")


def fresh_target(ready, admitted, *, degrees, direction, ramp_seconds, side="left", workpose="relative"):
    if side not in ("left", "right") or workpose == "admitted-span" and side != "right":
        raise ValueError("selected side/workpose is unsupported")
    key = f"arm_{side}_elbow_flex.pos"
    snapshot = admitted["snapshot"]
    workspace = validate_workspace(live(snapshot)["workspace"])
    commands = validate_target(ready["command_reference"], workspace=workspace)
    if (admitted.get("event") != "native_admitted" or ready["run_id"] != snapshot["run"]["run_id"]
        or ready["bounds"] != workspace["bounds"] or ready["units"] != workspace["units"]
        or live(snapshot)["reference"] != workspace["reference"]
        or commands != live(snapshot)["cursor"]
        or workspace.get("workspace_profile") not in (CURRENT_REST_WORKSPACE_PROFILES["ordinary-arms"], ORDINARY_AM1_ARMS_PROFILE)
        or direction not in ("decreasing", "increasing")):
        raise ValueError("fresh ready/native workspace, run, units and legal command reference must match")
    cal = workspace["calibration"][key[:-4]]
    if side == "right":
        owner = snapshot["controller"]
        origin = _qualified_command_reference(SimpleNamespace(snapshot=snapshot, device=owner["device_id"],
            generation=owner["controller_generation"], transport_error=None), workspace)
        point = snapshot["evidence"]["accepted_host_action"]["arm_feedback"]["positions"][key]
        if (origin != commands or commands[key] != workspace["reference"][key]
            or type(point["raw"]) is not int or not cal[3] <= point["raw"] <= cal[4]
            or not math.isfinite(point["normalized"])
            or abs(normalized_raw(point["raw"], cal)-point["normalized"]) > 1e-7):
            raise ValueError("fresh qualified RIGHT original mapping required")
    if workpose == "admitted-span":
        bound = workspace["bounds"][key]
        lower, upper = bound["min"], bound["max"]
        raw_bounds = workspace["raw_bounds"][key]
        if not lower < upper or (upper-lower)/finite(ramp_seconds) > bound["rate_per_s"]:
            raise ValueError("nonzero admitted RIGHT span/ramp must fit the existing rate")
        targets = validate_target(dict(commands, **{key: upper}), workspace=workspace)
        validate_target(dict(commands, **{key: lower}), workspace=workspace)
        return dict(q0=commands[key], target=upper, targets=targets, origin=dict(commands),
            calibration=list(cal), workspace=workspace, side=side, workpose=workpose,
            admitted_span=dict(raw_bounds=list(raw_bounds), bounds=[lower, upper],
                encoder_degrees=(raw_bounds[1]-raw_bounds[0])*360/PRACTICAL_ELBOW_COUNTS_PER_REVOLUTION))
    delta = abs(normalized_raw(cal[3]+finite(degrees, zero=side == "left")*PRACTICAL_ELBOW_COUNTS_PER_REVOLUTION/360, cal)
                - normalized_raw(cal[3], cal))
    q0 = commands[key]
    target = q0 + (delta if direction == "increasing" else -delta)
    targets = validate_target(dict(commands, **{key: target}), workspace=workspace)
    if delta/finite(ramp_seconds) > workspace["bounds"][key]["rate_per_s"]:
        raise ValueError("declared excursion/ramp exceeds the existing admitted rate")
    return dict(q0=q0, target=target, targets=targets, origin=dict(commands), calibration=list(cal), workspace=workspace, side=side)


def fresh_ordinary_targets(ready, admitted, *, ramp_seconds, gripper_direction):
    """Plan full prospective amplitudes from the original qualified measured reference."""
    # Reuse the same held ownership/ACK/workspace/reference/deadline qualifications.
    base = fresh_target(ready, admitted, degrees=0., direction="increasing", ramp_seconds=ramp_seconds)
    state, workspace, origin = admitted["snapshot"], base["workspace"], base["origin"]
    if workspace.get("workspace_profile") != ORDINARY_AM1_ARMS_PROFILE or gripper_direction not in ("opening", "closing"):
        raise ValueError("ordinary two-arm activity requires its exact native profile and explicit gripper direction")
    policy = validate_ordinary_arm_profile(state["run"].get("ordinary_arm_profile"))
    if (policy["profile"] != workspace["workspace_profile"] or state["run"].get("bench_continuity") is not False
        or state["run"].get("source") != "protected-physical-provider"
        or state["evidence"].get("source") != "protected-physical-provider"
        or live(state).get("required_view", {}).get("required") is not True):
        raise ValueError("ordinary activity requires normal native owned source and required view")
    owner = state["controller"]
    commands = _qualified_command_reference(SimpleNamespace(snapshot=state, device=owner["device_id"],
        generation=owner["controller_generation"], transport_error=None), workspace)
    if commands != origin:
        raise ValueError("ordinary activity requires its original qualified held command")
    points = state["evidence"]["accepted_host_action"]["arm_feedback"]["positions"]
    targets, excursions = dict(origin), {}
    for key in TELEOP_ARM_KEYS:
        point, cal = points[key], workspace["calibration"][key[:-4]]
        grip = key.endswith("gripper.pos")
        calibrated = type(point["raw"]) is int and cal[3] <= point["raw"] <= cal[4]
        acquisition = workspace.get("startup_acquisition")
        if (not calibrated and key == "arm_left_shoulder_lift.pos" and type(point["raw"]) is int
            and point["raw"] > cal[4] and acquisition is not None
            and acquisition["command_reference"] == origin and point["normalized"] == workspace["reference"][key]):
            calibrated = True  # validate_workspace already binds this exact normal-start reference/Goal envelope.
        if (not calibrated
            or not math.isfinite(point["normalized"])
            or abs(normalized_raw(point["raw"], cal, gripper=grip)-point["normalized"]) > 1e-7):
            raise ValueError("ordinary original measured mapping differs")
        amount = 20. if grip else 3. if "wrist_flex" in key else 4.
        delta = amount if grip else amount*PRACTICAL_ELBOW_COUNTS_PER_REVOLUTION/360*200/(cal[4]-cal[3])
        bounds = workspace["bounds"][key]
        if grip:
            direction = gripper_direction
        else:
            direction = "increasing" if origin[key]+delta <= bounds["max"]+1e-9 else "decreasing"
        endpoint = origin[key]+(delta if direction in ("increasing", "opening") else -delta)
        validate_target(dict(origin, **{key: endpoint}), workspace=workspace)
        if delta/finite(ramp_seconds) > bounds["rate_per_s"]+1e-9:
            raise ValueError("full ordinary excursion/ramp exceeds its existing admitted rate")
        targets[key] = endpoint
        excursions[key] = dict(amount=amount, units="opening_points" if grip else "encoder_degrees", direction=direction,
                               original=origin[key], target=endpoint)
    return dict(base, targets=targets, excursions=excursions, initial_feedback=points, ordinary_arms=True)


def phases_for(plan, options, *, admitted=None):
    ramp, dwell = options.ramp_seconds, options.dwell_seconds
    if options.plan == "ordinary-arms":
        if not plan.get("ordinary_arms"):
            raise ValueError("ordinary-arms requires its full fresh qualified plan")
        origin, all_targets = plan["origin"], plan["targets"]
        arm_targets = {k: origin[k] if k.endswith("gripper.pos") else all_targets[k] for k in TELEOP_ARM_KEYS}
        phases = []
        def move(name, targets):
            validate_target(targets, workspace=plan["workspace"])
            return dict(name=name, op="move", targets=dict(targets), ramp=ramp, dwell=dwell, complete_before_advance=True)
        def original(name):
            return dict(name=name, op="return", ramp=ramp, dwell=dwell, complete_before_advance=True)
        for side in ("left", "right"):
            key = f"arm_{side}_elbow_flex.pos"
            phases += [move(side+"_elbow", dict(origin, **{key: all_targets[key]})), original(side+"_return_original")]
        phases += [move("ordinary_both_arms", arm_targets),
                   dict(name="ordinary_pause", op="pause", dwell=2.), dict(name="ordinary_resume", op="resume"),
                   move("ordinary_reengaged_arms", arm_targets), move("ordinary_both_grippers", all_targets),
                   move("ordinary_grippers_return", arm_targets), original("return_original"), dict(name="finish", op="finish")]
        previous = origin
        for phase in phases[:-1]:
            if phase["op"] in {"pause", "resume"}:
                continue
            target = origin if phase["op"] == "return" else phase["targets"]
            if any(abs(target[k]-previous[k])/ramp > plan["workspace"]["bounds"][k]["rate_per_s"]+1e-9 for k in TELEOP_ARM_KEYS):
                raise ValueError("ordinary phase/return exceeds its admitted rate")
            previous = target
        return phases
    side = getattr(options, "side", "left")
    if side != plan.get("side", "left"):
        raise ValueError("selected side differs from the fresh original plan")
    if side == "right":
        _right_options(options)
        key, grip = "arm_right_elbow_flex.pos", "arm_right_gripper.pos"
        origin, workspace = plan["origin"], plan["workspace"]
        span = options.workpose == "admitted-span"
        if span:
            if plan.get("workpose") != "admitted-span":
                raise ValueError("admitted-span requires its fresh fixed-bound plan")
            lower, upper = plan["admitted_span"]["bounds"]
            phases = [dict(name=name, op="move", target=target, targets={key: target},
                           ramp=ramp, dwell=dwell, complete_before_advance=True)
                      for name, target in (("prepare_lower", lower), ("span_upper", upper), ("span_lower", lower))]
            phases[0]["timed_preparation"] = True
            phases.append(dict(name="return_original", op="return", ramp=ramp, dwell=dwell, complete_before_advance=True))
            gripper_elbow = origin[key]
        else:
            outward = dict(name="move", op="move", target=plan["target"], targets={key: plan["target"]},
                           ramp=ramp, dwell=dwell, complete_before_advance=True)
            phases = [outward]
            gripper_elbow = plan["target"]
        if options.gripper_cycle:
            if origin[grip] != workspace["reference"][grip]:
                raise ValueError("RIGHT gripper requires its original fresh reference")
            endpoint = origin[grip]+(10. if options.gripper_direction == "opening" else -10.)
            validate_target(dict(plan["targets"], **{grip: endpoint}), workspace=workspace)
            if 10./3. > workspace["bounds"][grip]["rate_per_s"]:
                raise ValueError("RIGHT gripper excursion/return exceeds admitted rate")
            if not span:
                outward.update(name="elbow_workpose", targets={key: plan["target"], grip: origin[grip]},
                               dwell=options.comparison_dwell_seconds)
            phases += [dict(name=name, op="move", target=gripper_elbow, targets={key: gripper_elbow, grip: target},
                            ramp=3., dwell=2., complete_before_advance=True)
                       for name, target in (("gripper_open" if options.gripper_direction == "opening" else "gripper_close", endpoint),
                                            ("gripper_return_original", origin[grip]))]
            phases[-2]["gripper_direction"] = options.gripper_direction
        if not span:
            phases.append(dict(name="return_original", op="return", ramp=ramp, dwell=dwell, complete_before_advance=True))
        return phases+[dict(name="finish", op="finish")]
    lower_return = options.plan == "lower-return-gripper"
    grasp_return = options.plan == "grasp-return"
    opening_only = options.plan == "gripper-open-finish"
    gripper_only = options.plan == "gripper-close-return" or opening_only
    extended_closing = getattr(options, "extended_grasp_closing", False)
    supported_closing = getattr(options, "supported_grasp_closing", False)
    if supported_closing and (not grasp_return or options.gripper_direction != "admitted"
        or plan["workspace"]["raw_bounds"][GRIP] != list(SUPPORTED_GRASP_CLOSING_RAW)):
        raise ValueError("supported grasp closing requires its exact admitted raw interval")
    if extended_closing and (supported_closing or not (grasp_return or gripper_only)
        or options.gripper_direction != "admitted"
        or plan["workspace"]["raw_bounds"][GRIP] != list(EXTENDED_GRASP_CLOSING_RAW)):
        raise ValueError("extended grasp closing requires its exact admitted raw interval")
    midpoint_workpose = options.plan == "out-return" and getattr(options, "workpose", "relative") == "midpoint"
    if midpoint_workpose and not (getattr(options, "gripper_cycle", False) and getattr(options, "approach_from_below", False)):
        raise ValueError("explicit midpoint activity requires gripper-cycle and approach-from-below")
    if getattr(options, "gripper_cycle", False) or lower_return or grasp_return or gripper_only:
        snapshot = admitted["snapshot"]
        owner = snapshot["controller"]
        origin = _qualified_command_reference(SimpleNamespace(snapshot=snapshot, device=owner["device_id"],
            generation=owner["controller_generation"], transport_error=None), plan["workspace"])
        cal = plan["calibration"]
        point = snapshot["evidence"]["accepted_host_action"]["arm_feedback"]["positions"][KEY]
        room = abs(normalized_raw(cal[3]+options.approach_room_degrees*PRACTICAL_ELBOW_COUNTS_PER_REVOLUTION/360, cal)-normalized_raw(cal[3], cal))
        if (origin != plan["origin"] or type(point["raw"]) is not int or not cal[3] <= point["raw"] <= cal[4]
            or not math.isfinite(point["normalized"])
            or abs(normalized_raw(point["raw"], cal)-point["normalized"]) > 1e-7):
            raise ValueError("fresh qualified measured original mapping required")
        bound = plan["workspace"]["bounds"][KEY]
        elbow = origin[KEY] if lower_return or gripper_only else (bound["min"]+bound["max"])/2 if midpoint_workpose else plan["target"]
        held = validate_target(dict(origin, **{KEY: elbow}), workspace=plan["workspace"])
        grip_direction = getattr(options, "gripper_direction", "closing")
        if grip_direction not in ("opening", "closing", "admitted"):
            raise ValueError("explicit opening/closing/admitted gripper policy required")
        grip_ramp = 3.
        if extended_closing:
            if opening_only:
                endpoint = finite(options.gripper_open_opening, zero=True)
                if endpoint > 100. or endpoint <= origin[GRIP]:
                    raise ValueError("absolute opening endpoint must be above the fresh gripper origin")
                grip_direction = "opening"
                grip_ramp = max(ramp, (endpoint-origin[GRIP])*3./10.)
            else:
                endpoint = finite(options.gripper_closed_opening, zero=True)
                if endpoint > 100. or endpoint >= origin[GRIP]:
                    raise ValueError("absolute closing endpoint must be below the fresh gripper origin")
                grip_direction = "closing"
                grip_ramp = max(3., (origin[GRIP]-endpoint)*3./10.)
            grip_target = validate_target(dict(held, **{GRIP: endpoint}), workspace=plan["workspace"])
        elif grip_direction == "admitted":
            candidates = {}
            for candidate, delta in (("opening", 10.), ("closing", -10.)):
                try:
                    candidates[candidate] = validate_target(dict(held, **{GRIP: origin[GRIP]+delta}), workspace=plan["workspace"])
                except ValueError:
                    pass  # Only this explicitly declared policy resolves a full legal endpoint.
            if len(candidates) != 1:
                raise ValueError("admitted gripper policy requires exactly one full legal10-point endpoint")
            grip_direction, grip_target = next(iter(candidates.items()))
        else:
            grip_target = validate_target(dict(held, **{GRIP: origin[GRIP]+(10. if grip_direction == "opening" else -10.)}),
                                          workspace=plan["workspace"])
        if abs(grip_target[GRIP]-origin[GRIP])/grip_ramp > plan["workspace"]["bounds"][GRIP]["rate_per_s"]:
            raise ValueError("full gripper excursion/return exceeds admitted rate")
        if supported_closing and grip_direction != "closing":
            raise ValueError("supported grasp closing requires the full closing endpoint; no opening substitute")
        if grasp_return or gripper_only:
            if (grip_direction not in ("opening", "closing") or options.gripper_direction != "admitted"
                or any(origin[key] != plan["workspace"]["reference"][key] for key in (KEY, GRIP))):
                raise ValueError("grasp-return requires the original selected references and admitted interval")
            if opening_only:
                return [dict(name="gripper_open", op="move", target=origin[KEY], targets={KEY: origin[KEY], GRIP: grip_target[GRIP]},
                             ramp=grip_ramp, dwell=2., complete_before_advance=True, gripper_direction="opening",
                             gripper_interval=dict(origin=origin[GRIP], open=grip_target[GRIP], opening_only=True)),
                        dict(name="finish", op="finish")]
            opening = grip_target[GRIP] if grip_direction == "opening" else origin[GRIP]
            closed = origin[GRIP] if grip_direction == "opening" else grip_target[GRIP]

            def grasp_phase(name, q, grip, *, ramp, dwell):
                validate_target(dict(origin, **{KEY: q, GRIP: grip}), workspace=plan["workspace"])
                return dict(name=name, op="move", target=q, targets={KEY: q, GRIP: grip},
                            ramp=ramp, dwell=dwell, complete_before_advance=True)

            phases = ([grasp_phase("grasp_prepare_open", origin[KEY], opening, ramp=3., dwell=2.)]
                      if grip_direction == "opening" else [])
            phases += [grasp_phase("grasp_close", origin[KEY], closed, ramp=grip_ramp, dwell=2.)]
            if not gripper_only:
                phases += [
                    grasp_phase("grasp_carry", elbow, closed, ramp=ramp, dwell=options.comparison_dwell_seconds),
                    grasp_phase("grasp_return_original_closed", origin[KEY], closed, ramp=ramp, dwell=dwell),
                ]
            phases += [
                grasp_phase("grasp_release", origin[KEY], opening, ramp=grip_ramp, dwell=2.),
                dict(name="finish", op="finish"),
            ]
            phases[0]["gripper_direction"] = grip_direction
            phases[0]["gripper_interval"] = dict(open=opening, closed=closed,
                                                 opening_preparation=grip_direction == "opening")
            return phases
        grip_phases = [dict(name=name, op="move", target=elbow, targets={KEY: elbow, GRIP: grip},
                           ramp=3., dwell=2.) for name,grip in (
                               ("gripper_open" if grip_direction == "opening" else "gripper_close", grip_target[GRIP]),
                               ("gripper_return_original" if grip_direction == "opening" else "gripper_reopen_original", origin[GRIP]))]
        grip_phases[0]["gripper_direction"] = grip_direction
        def lower_preparation(name, endpoint):
            lower = bound["min"]
            validate_target(dict(origin, **{KEY: lower}), workspace=plan["workspace"])
            if endpoint-lower < room or (endpoint-lower)/ramp > bound["rate_per_s"]:
                raise ValueError("fixed endpoint lacks full admitted lower preparation/rate/approach room")
            return dict(name=name, op="move", target=lower, targets={KEY: lower, GRIP: origin[GRIP]},
                        ramp=ramp, dwell=0., preparation=True,
                        approach_endpoint=endpoint, approach_side="below")
        if midpoint_workpose:
            return [lower_preparation("prepare_below_midpoint", elbow),
                    dict(name="midpoint_workpose", op="move", target=elbow, targets={KEY: elbow, GRIP: origin[GRIP]},
                         ramp=ramp, dwell=options.comparison_dwell_seconds)] + grip_phases + [
                    lower_preparation("prepare_below_original", origin[KEY]),
                    dict(name="return_original", op="return", target=origin[KEY], ramp=ramp, dwell=dwell),
                    dict(name="finish", op="finish")]
        if lower_return:
            return [lower_preparation("prepare_lower", origin[KEY]),
                    dict(name="return_original", op="return", target=origin[KEY], approach="below",
                         ramp=ramp, dwell=options.comparison_dwell_seconds)] + grip_phases + [dict(name="finish", op="finish")]
        if (options.plan != "out-return" or elbow <= origin[KEY]
            or elbow-point["normalized"] < room):
            raise ValueError("fresh qualified measured origin must support the increasing workpose")
        return [dict(name="elbow_workpose", op="move", target=elbow, targets={KEY: elbow, GRIP: origin[GRIP]},
                     ramp=ramp, dwell=options.comparison_dwell_seconds)] + grip_phases + [
                dict(name="return_original", op="return", ramp=ramp, dwell=dwell), dict(name="finish", op="finish")]
    if options.plan == "out-return":
        return [dict(name="move", op="move", target=plan["target"], ramp=ramp, dwell=dwell),
                dict(name="return_original", op="return", ramp=ramp, dwell=dwell), dict(name="finish", op="finish")]
    bound = plan["workspace"]["bounds"][KEY]
    upper, lower = bound["max"], bound["min"]
    midpoint = (upper+lower)/2
    if (upper-lower)/ramp > bound["rate_per_s"]:
        raise ValueError("declared preparation ramp exceeds existing admitted rate")
    return [dict(name=name, op="move", target=target, ramp=ramp, dwell=hold, approach=side,
                 preparation=side is None,
                 approach_endpoint=midpoint if side is None else None,
                 approach_side=("above" if name == "prepare_upper" else "below") if side is None else None)
            for name,target,hold,side in (
                ("prepare_upper", upper, 0., None),
                ("midpoint_decreasing", midpoint, options.comparison_dwell_seconds, "above"),
                ("prepare_lower", lower, 0., None),
                ("midpoint_increasing", midpoint, options.comparison_dwell_seconds, "below"))] + [
            dict(name="return_original", op="return", ramp=ramp, dwell=dwell), dict(name="finish", op="finish")]



def phase_command(phase, ttl):
    if phase["op"] in {"pause", "resume"}:
        return f'{phase["op"]} {ttl}'
    if phase["op"] == "move":
        targets = phase["targets"] if "targets" in phase else {KEY: phase["target"]}
        named = " ".join(key.removeprefix("arm_").removesuffix(".pos")+"="+repr(value)
                         for key,value in sorted(targets.items()))
        return f'move {phase["ramp"]} {ttl} '+named
    return f'return {phase["ramp"]} {ttl}' if phase["op"] == "return" else "finish"


def next_phase(status, *, phases, index, pending, accepted, supported, approach_ready=False, control_elapsed_s=None):
    if pending is not None or status["input_state"] == "finished":
        return None
    remaining = finite(status["window_remaining_s"], zero=True)
    if not supported or status["input_state"] == "expired" or remaining < 8:
        return len(phases)-1 if index != len(phases)-1 else None
    if index < 0:
        if phases[0].get("complete_before_advance") and remaining < sum(
            phase.get("ramp", 0)+phase.get("dwell", 0) for phase in phases
        )+8:
            return len(phases)-1
        return 0
    if phases[index]["op"] == "pause":
        if control_elapsed_s is None:
            return None
        reserve = sum(p.get("ramp", 0)+p.get("dwell", 0) for p in phases[index+1:])+8
        if remaining < reserve:
            return len(phases)-1
        return index+1 if control_elapsed_s >= phases[index]["dwell"] else None
    if phases[index]["op"] == "resume":
        return None  # Only its explicit active ACK dispatches a fresh intention.
    if accepted != status["current_accepted_intent_seq"] or phases[index]["op"] == "finish":
        return None
    reserve = sum(p.get("ramp", 0)+p.get("dwell", 0) for p in phases[index+1:])+8
    if phases[index].get("complete_before_advance"):
        duration = phases[index]["ramp"]+phases[index]["dwell"]
        if remaining < reserve+max(0., duration-status["ramp_progress_active_s"]):
            return len(phases)-1
        return index+1 if status["input_state"] == "current" and status["ramp_progress_active_s"] >= duration else None
    if phases[index]["op"] != "return":
        reserve = max(30., reserve)
    if phases[index].get("preparation"):
        if approach_ready is True and status["input_state"] == "current":
            return index+1
        if status["ramp_progress_active_s"] >= phases[index]["ramp"] or remaining < reserve:
            return len(phases)-1  # No qualified achieved room at the preparation maximum/budget.
        return None  # Keep the existing fresh preparation input; never add endpoint dwell.
    if status["ramp_progress_active_s"] >= phases[index]["ramp"]+phases[index]["dwell"] or remaining < reserve:
        return index+1
    return None


class FrameTail:
    """Two retained records, one forward-only reader; never a hardware reader."""
    def __init__(self, path):
        self.stream = path.open("r", encoding="utf-8")
        self.admitted = self.latest = None

    def refresh(self):
        while True:
            at = self.stream.tell()
            line = self.stream.readline()
            if not line or not line.endswith("\n"):
                self.stream.seek(at)
                return
            value = json.loads(line)
            if value.get("event") == "native_admitted":
                if self.admitted is not None:
                    raise ValueError("one original native admission required")
                self.admitted = value
            elif value.get("event") == "leader_frame":
                self.latest = value


def approach_pp(frame, *, run_id, accepted, now, workspace, midpoint, side, room):
    try:
        snapshot, intent = frame["native"], frame["virtual_intent"]
        run, evidence = snapshot["run"], snapshot["evidence"]
        ack, current = evidence["accepted_host_action"], live(snapshot)
        source = current["source"]
        point = ack["arm_feedback"]["positions"][KEY]
        pi_now = run["deadline"]-run["remaining_s"]
        cal = workspace["calibration"][KEY[:-4]]
        if (current["workspace"] != workspace or side not in ("above", "below")
            or frame["event"] != "leader_frame" or frame["receipt"].get("accepted") is not True
            or frame["receipt"].get("effect_admitted") is not True or intent["seq"] != accepted
            or run["run_id"] != run_id or run["status"] != "running" or run.get("uncertain") is not False
            or run.get("recovery") is not None or current.get("state") != "active"
            or (run.get("first_cause") is not None and not (
                run["first_cause"] == "feedback_loss" and type(run.get("recovery_episodes")) is int
                and 1 <= run["recovery_episodes"] <= 3))
            or run.get("native_first_cause") or evidence.get("fault")
            or not all(evidence.get(k) is True for k in ("feedback", "required_observation", "native_ack", "active_acknowledged"))
            or ack["run_id"] != run_id or ack["host_incarnation"] != run["backend_incarnation"]
            or ack["state"] != "active" or ack["intent_revision"] != run["intent_revision"]
            or evidence["acknowledged_intent_revision"] != run["intent_revision"]
            or current["cursor_intent_revision"] != run["intent_revision"]
            or current["required_view"].get("qualified") is not True
            or current["required_view"].get("required") is not True or current["required_view"].get("role") != "chest"
            or not 0 <= now-frame["local_monotonic_s"] <= .5
            or not point["read_started_at_monotonic_s"] <= point["read_completed_at_monotonic_s"] <= ack["acquired_at_monotonic_s"] <= pi_now
            or not pi_now < current["valid_until_monotonic_s"]
            or source["source_id"] != intent["intent_channel"]
            or not intent["issued_at_perf_counter_s"] <= source["acquired_at_monotonic_s"] < intent["expires_at_perf_counter_s"]
            or type(point["raw"]) is not int or not cal[3] <= point["raw"] <= cal[4]
            or not math.isfinite(point["normalized"])
            or abs(normalized_raw(point["raw"], cal)-point["normalized"]) > 1e-7):
            raise ValueError("current same-intention qualified measured PP unavailable")
        signed = point["normalized"]-midpoint
        if (signed if side == "above" else -signed) < finite(room):
            raise ValueError("measured preparation did not reach declared midpoint approach room")
        return dict(raw=point["raw"], normalized=point["normalized"],
                    acquired_at_pi_monotonic_s=ack["acquired_at_monotonic_s"])
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("current same-intention qualified measured PP unavailable") from error


def phase_feedback(frame, *, run_id, accepted, now, workspace, targets):
    """Retained active sole-owner measurements; never infer arrival from a timer or ACK target."""
    try:
        snapshot, intent = frame["native"], frame["virtual_intent"]
        run, evidence = snapshot["run"], snapshot["evidence"]
        ack, current = evidence["accepted_host_action"], live(snapshot)
        source, owner = current["source"], snapshot["controller"]
        pi_now = run["deadline"]-run["remaining_s"]
        if (frame["event"] != "leader_frame" or frame["receipt"].get("accepted") is not True
            or frame["receipt"].get("effect_admitted") is not True or intent["seq"] != accepted
            or run["run_id"] != run_id or run["status"] != "running" or run.get("uncertain") is not False
            or run.get("recovery") is not None or current.get("state") != "active"
            or run.get("source") != "protected-physical-provider" or evidence.get("source") != run["source"]
            or not owner.get("device_id") or not owner.get("controller_generation")
            or run.get("bench_continuity") is not False or current["workspace"] != workspace
            or current["reference"] != workspace["reference"] or current["body"] != ZERO_BODY
            or (run.get("first_cause") is not None and not (run["first_cause"] == "feedback_loss"
                and type(run.get("recovery_episodes")) is int and 1 <= run["recovery_episodes"] <= 3))
            or run.get("native_first_cause") or evidence.get("fault")
            or not all(evidence.get(k) is True for k in ("feedback", "required_observation", "native_ack", "active_acknowledged"))
            or ack["run_id"] != run_id or ack["host_incarnation"] != run["backend_incarnation"]
            or ack["state"] != "active" or ack["intent_revision"] != run["intent_revision"]
            or evidence["acknowledged_intent_revision"] != run["intent_revision"]
            or current["cursor_intent_revision"] != run["intent_revision"]
            or current["required_view"].get("qualified") is not True or current["required_view"].get("required") is not True
            or current["required_view"].get("role") != "chest" or not 0 <= now-frame["local_monotonic_s"] <= .5
            or not 0 < run["remaining_s"] <= run["deadline"]-run["native_live_at"] <= 300.
            or not run["native_live_at"] <= ack["acquired_at_monotonic_s"] <= pi_now
            or pi_now-ack["acquired_at_monotonic_s"] > .5 or not pi_now < current["valid_until_monotonic_s"]
            or source["source_id"] != intent["intent_channel"]
            or not intent["issued_at_perf_counter_s"] <= source["acquired_at_monotonic_s"] < intent["expires_at_perf_counter_s"]):
            raise ValueError("current same-intention qualified measured endpoint unavailable")
        result = {}
        points = ack["arm_feedback"]["positions"]
        for key in TELEOP_ARM_KEYS:
            point, cal = points[key], workspace["calibration"][key[:-4]]
            if (type(point["raw"]) is not int or not cal[3] <= point["raw"] <= cal[4]
                or not math.isfinite(point["normalized"])
                or abs(normalized_raw(point["raw"], cal, gripper=key.endswith("gripper.pos"))-point["normalized"]) > 1e-7
                or not point["read_started_at_monotonic_s"] <= point["read_completed_at_monotonic_s"] <= ack["acquired_at_monotonic_s"]):
                raise ValueError("current measured endpoint mapping/read interval differs")
            result[key] = dict(point, requested_target=targets[key], target_error=point["normalized"]-targets[key])
        return result
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("current same-intention qualified measured endpoint unavailable") from error


def adapter_command(options):
    args = [sys.executable, "-u", "-B", "-m", "tools.am1_leader_adapter"]
    if options.ordinary_config:
        args += ["--ordinary-config", options.ordinary_config]
    else:
        args += ["--url", options.url, "--ca", options.ca, "--storage-state", options.storage_state,
                 "--bindings", str(options.bindings), "--output-root", options.output_root]
        if options.ordinary_arm_profile is None:
            args.append("--prepare-current-rest")
        else:
            args += ["--operating-profile", options.ordinary_arm_profile["profile"]]
    if options.ordinary_arm_profile is not None:
        args += ["--right-elbow-p", str(options.ordinary_arm_profile["right_elbow_p"])]
    args += ["--virtual-input", "interactive", "--duration-seconds", str(options.window_seconds)]
    if options.supported_grasp_closing:
        args.append("--supported-grasp-closing")
    elif options.extended_grasp_closing:
        args.append("--extended-grasp-closing")
    return args


def main(argv=None):
    options = parse_args(argv)
    ordinary = options.plan == "ordinary-arms"
    opening_only = options.plan == "gripper-open-finish"
    timed_span = options.side == "right" and options.workpose == "admitted-span"
    grip = f"arm_{options.side}_gripper.pos"
    uses_gripper = ordinary or options.gripper_cycle or options.plan in ("lower-return-gripper", "grasp-return", "gripper-close-return", "gripper-open-finish")
    relative_workpose = options.plan == "grasp-return" or options.plan == "out-return" and options.workpose == "relative"
    has_preparation = options.plan in ("bidirectional-midpoint", "lower-return-gripper") or options.approach_from_below
    console = console_path(ROOT)
    args = adapter_command(options)
    ready = plan = tail = process = output = None
    phases, index, pending, accepted, last_report = [dict(name="finish", op="finish")], -1, None, None, -100.
    supported = False
    control_started = None
    external_pause = False
    with console.open("x", encoding="utf-8") as record:
        def local(event, **fields):
            line = json.dumps(dict(event=event, pc_monotonic_s=time.monotonic(), **fields), sort_keys=True, allow_nan=False)
            record.write(line+"\n"); record.flush(); print(line, flush=True)
        def send(next_index, reason, measured=None):
            nonlocal pending
            if pending is not None:
                raise RuntimeError("previous phase acceptance still pending")
            phase = phases[next_index]
            command = phase_command(phase, options.intent_ttl_seconds)
            process.stdin.write(command+"\n"); process.stdin.flush()
            pending = next_index
            local("local_phase_dispatched", phase=phase["name"], command=command, reason=reason, measured_start=measured)
        def qualified_approach(preparation_index):
            tail.refresh()
            cal = plan["calibration"]
            room = abs(normalized_raw(cal[3]+options.approach_room_degrees*PRACTICAL_ELBOW_COUNTS_PER_REVOLUTION/360, cal)-normalized_raw(cal[3], cal))
            return approach_pp(tail.latest, run_id=ready["run_id"], accepted=accepted, now=time.monotonic(),
                               workspace=plan["workspace"], midpoint=phases[preparation_index]["approach_endpoint"],
                               side=phases[preparation_index]["approach_side"], room=room)
        local("local_activity_declared", console=str(console), plan=options.plan, side="both" if ordinary else options.side, gripper_cycle=uses_gripper and not opening_only,
              gripper_direction="opening" if opening_only else options.gripper_direction if uses_gripper else None,
              workpose=options.workpose if options.plan == "out-return" else None,
              approach_from_below=options.approach_from_below,
              degrees=options.degrees if relative_workpose else None,
              direction=options.direction if relative_workpose else None, ramp_seconds=options.ramp_seconds, dwell_seconds=options.dwell_seconds,
              window_elapsed_seconds=options.window_seconds, intent_ttl_seconds=options.intent_ttl_seconds,
              comparison_dwell_seconds=options.comparison_dwell_seconds if not timed_span else None,
              **(dict(preparation_policy="ordinary-timed; no measured endpoint gate",
                      timed_preparation_active_seconds=options.ramp_seconds+options.dwell_seconds) if timed_span else {}),
              preparation_max_active_seconds=options.ramp_seconds if has_preparation else None,
              preparation_dwell_seconds=0. if has_preparation else None,
              approach_room_degrees=options.approach_room_degrees,
              view="normal required Chest", other_joints="fresh admitted holds", body_lift_intent="zero",
              **({"gripper_activity": "opening only; Finish without closed-reference return",
                  "elbow_excursion_degrees": 0.,
                  "gripper_interval_policy": "explicit absolute opening endpoint; no closing substitute",
                  "selected_gripper_raw_interval": list(EXTENDED_GRASP_CLOSING_RAW),
                  "gripper_open_opening": options.gripper_open_opening,
                  "gripper_max_opening_points_per_second": 10./3.,
                  "gripper_ramp_policy": "max(declared ramp seconds, absolute fresh-origin excursion *3/10)",
                  "gripper_opening_preparation_active_seconds": 0.}
                 if opening_only else {}),
              **({"gripper_interval_policy": "explicit absolute closing endpoint; no opening substitute",
                  "selected_gripper_raw_interval": list(EXTENDED_GRASP_CLOSING_RAW),
                  "gripper_closed_opening": options.gripper_closed_opening,
                  "gripper_max_opening_points_per_second": 10./3.,
                  "gripper_ramp_policy": "max(3seconds, absolute fresh-origin excursion *3/10)",
                  "gripper_opening_preparation_active_seconds": 0.}
                 if options.extended_grasp_closing and not opening_only else {}),
              **({"gripper_interval_policy": ("supported closing10-point interval; refuse opening substitute"
                                              if options.supported_grasp_closing else
                                              "one legal10-point interval; prepare opening when only opening is admitted"),
                  "planned_active_seconds_options": [36.] if options.supported_grasp_closing else [36., 41.],
                  "gripper_opening_preparation_active_seconds": 0. if options.supported_grasp_closing else 5.,
                  **({"selected_gripper_raw_interval": list(SUPPORTED_GRASP_CLOSING_RAW)}
                     if options.supported_grasp_closing else {})}
                 if options.plan == "grasp-return" and not options.extended_grasp_closing else {}))
        try:
            process = subprocess.Popen(args, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, encoding="utf-8", errors="replace", bufsize=1)
            for line in process.stdout:
                record.write(line); record.flush()
                try: value = json.loads(line)
                except (ValueError, TypeError):
                    print(line.rstrip(), flush=True); continue
                if not isinstance(value, dict): continue
                event = value.get("event")
                if event == "virtual_input_opened":
                    output = value["output"]; tail = FrameTail(Path(output)/"frames.jsonl")
                    local("local_output_identity", output=output)
                elif event == "virtual_input_ready":
                    ready = value
                    try:
                        tail.refresh()
                        if (options.ordinary_arm_profile is not None
                            and tail.admitted["snapshot"]["run"].get("ordinary_arm_profile") != options.ordinary_arm_profile):
                            raise ValueError("native admitted ordinary policy differs from the requested immutable policy")
                        plan = (fresh_ordinary_targets(ready, tail.admitted, ramp_seconds=options.ramp_seconds,
                                                       gripper_direction=options.gripper_direction) if ordinary else
                                fresh_target(ready, tail.admitted, degrees=options.degrees if relative_workpose else 0.,
                                             direction=options.direction, ramp_seconds=options.ramp_seconds,
                                             side=options.side, workpose=options.workpose))
                        phases = phases_for(plan, options, admitted=tail.admitted); supported = True
                        local("local_fresh_targets", run_id=ready["run_id"], original_q0=plan["q0"],
                              calibration=plan["calibration"], counts_per_revolution=PRACTICAL_ELBOW_COUNTS_PER_REVOLUTION,
                              gripper_origin=plan["origin"][grip] if uses_gripper else None,
                              gripper_direction_requested=options.gripper_direction if uses_gripper else None,
                              gripper_direction_resolved=next((p["gripper_direction"] for p in phases if "gripper_direction" in p), None),
                              gripper_target=next((p["targets"][grip] for p in phases if "gripper_direction" in p), None),
                              gripper_calibration=plan["workspace"]["calibration"][grip[:-4]] if uses_gripper else None,
                              **(dict(ordinary_excursions=plan["excursions"], original_command_reference=plan["origin"],
                                      original_measured_reference=plan["workspace"]["reference"],
                                      original_measured_feedback=plan["initial_feedback"],
                                      phase_completion_policy="accepted intention and active ramp/dwell; actual endpoints reported separately") if ordinary else {}),
                              phases=phases, planned_active_seconds=sum(p.get("ramp", 0)+p.get("dwell", 0) for p in phases if p["op"] not in {"pause", "resume"}),
                              **({"planned_paused_seconds": sum(p.get("dwell", 0) for p in phases if p["op"] == "pause")} if ordinary else {}),
                              **(dict(admitted_span=plan["admitted_span"]) if timed_span else {}),
                              **({"gripper_interval": phases[0]["gripper_interval"]}
                                 if options.plan in ("grasp-return", "gripper-close-return", "gripper-open-finish") else {}))
                    except (ValueError, KeyError, TypeError, AttributeError, OSError) as error:
                        local("local_excursion_unsupported", reason=str(error), action="Finish without substitute")
                elif event == "virtual_intent_accepted":
                    print(line.rstrip(), flush=True)
                    if (pending is None or value["op"] != phases[pending]["op"] or type(value["seq"]) is not int
                        or value["seq"] <= (accepted or 0)):
                        raise RuntimeError("unexpected intention acceptance")
                    index, pending, accepted = pending, None, value["seq"]
                    local("local_phase_acceptance_observed", phase=phases[index]["name"], op=value["op"], seq=accepted)
                elif event in {"virtual_control_paused", "virtual_control_resumed"}:
                    print(line.rstrip(), flush=True)
                    expected = "pause" if event == "virtual_control_paused" else "resume"
                    if (pending is None or phases[pending]["op"] != expected or type(value.get("seq")) is not int
                            or value["seq"] <= (accepted or 0)):
                        external_pause = True
                        local("local_external_pause_preserved", reason="no matching requested control phase; no automatic Resume")
                        continue
                    index, pending, accepted = pending, None, value["seq"]
                    control_started = time.monotonic()
                    local("local_control_phase_acknowledged", phase=phases[index]["name"], op=expected, seq=accepted)
                    if expected == "resume":
                        send(index+1, "explicit Resume ACK; acquire a new movement intention")
                elif event == "virtual_control_rejected":
                    print(line.rstrip(), flush=True)
                    external_pause = True
                    pending = None
                    local("local_control_refused_held", reason=value.get("reason"), action="preserve Pause until original window ends")
                elif event in ("virtual_intent_discarded", "virtual_intent_rejected"):
                    print(line.rstrip(), flush=True); pending = None; supported = False
                elif event == "virtual_input_status":
                    if value["window_elapsed_s"]-last_report >= 4 or value["input_state"] in ("finished", "expired"):
                        print(line.rstrip(), flush=True); last_report = value["window_elapsed_s"]
                    if ready is None: continue
                    if external_pause: continue
                    measured, approach_error = None, None
                    preparation = index >= 0 and phases[index].get("preparation") is True
                    if preparation and pending is None and accepted == value["current_accepted_intent_seq"]:
                        try:
                            measured = qualified_approach(index)
                        except ValueError as error:
                            approach_error = str(error)  # Before the maximum, keep streaming this fresh preparation.
                    following = next_phase(value, phases=phases, index=index, pending=pending, accepted=accepted,
                                           supported=supported, approach_ready=measured is not None,
                                           control_elapsed_s=(time.monotonic()-control_started
                                               if control_started is not None and index >= 0 and phases[index]["op"] == "pause" else None))
                    if following is not None:
                        if ordinary and index >= 0 and phases[index]["op"] not in {"finish", "pause", "resume"}:
                            tail.refresh()
                            target = plan["origin"] if phases[index]["op"] == "return" else phases[index]["targets"]
                            try:
                                actual = phase_feedback(tail.latest, run_id=ready["run_id"], accepted=accepted,
                                                        now=time.monotonic(), workspace=plan["workspace"], targets=target)
                                qualification = dict(measurement_qualified=True, measured_endpoint=actual)
                            except ValueError as error:
                                qualification = dict(measurement_qualified=False, measurement_reason=str(error))
                            local("local_phase_endpoint_observed", phase=phases[index]["name"], requested_targets=target,
                                  active_seconds=value["ramp_progress_active_s"],
                                  full_phase_active_completed=value["ramp_progress_active_s"] >= phases[index]["ramp"]+phases[index]["dwell"],
                                  arrival_claimed=False, **qualification)
                        if preparation:
                            if following == len(phases)-1:
                                local("local_approach_unqualified", reason=approach_error or "original preparation/window budget ended",
                                      declared_preparation_target=phases[index]["target"],
                                      approach_endpoint=phases[index]["approach_endpoint"], approach_side=phases[index]["approach_side"],
                                      full_bound_achievement_claimed=False, outcome="partial; Finish")
                            else:
                                local("local_preparation_ended", phase=phases[index]["name"],
                                      declared_preparation_target=phases[index]["target"],
                                      approach_endpoint=phases[index]["approach_endpoint"], approach_side=phases[index]["approach_side"],
                                      accepted_preparation_target=live(tail.latest["native"])["accepted_target"][KEY],
                                      measured_start=measured, active_seconds=value["ramp_progress_active_s"],
                                      early_cancel=value["ramp_progress_active_s"] < phases[index]["ramp"],
                                      full_bound_achievement_claimed=False)
                        send(following, "qualified achieved approach" if measured is not None else
                             "acknowledged active phase/window decision", measured)
                elif event == "virtual_input_result":
                    print(line.rstrip(), flush=True)
            code = process.wait()
            local("local_adapter_exited", exit_code=code, output=output, last_accepted_phase=phases[index]["name"] if index >= 0 else None)
            return code
        except BaseException as error:
            local("local_coordinator_error", error_type=type(error).__name__, reason=str(error))
            if process is not None and process.poll() is None:
                try: process.stdin.write("finish\n"); process.stdin.flush(); process.stdin.close()
                except (OSError, ValueError): pass
                for line in process.stdout: record.write(line); record.flush()
                local("local_adapter_exited_after_error", exit_code=process.wait(), output=output)
            raise
        finally:
            if tail is not None: tail.stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
