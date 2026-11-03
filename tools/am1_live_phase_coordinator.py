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
    CURRENT_REST_WORKSPACE_PROFILES, normalized_raw, validate_target, validate_workspace,
)
from examples.alohamini.am1_session_contract import PRACTICAL_ELBOW_COUNTS_PER_REVOLUTION
from tools.am1_leader_adapter import _qualified_command_reference

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
    parser.add_argument("--plan", choices=("out-return", "bidirectional-midpoint", "lower-return-gripper", "grasp-return"), default="out-return")
    parser.add_argument("--workpose", choices=("relative", "midpoint"), default="relative")
    parser.add_argument("--approach-from-below", action="store_true")
    parser.add_argument("--gripper-cycle", action="store_true", help="out-return: same elbow workpose, declared10-point gripper excursion then original")
    parser.add_argument("--gripper-direction", choices=("opening", "closing", "admitted"), default="closing")
    parser.add_argument("--degrees", type=lambda v: finite(v), default=4., help="out-return encoder degrees")
    parser.add_argument("--direction", choices=("decreasing", "increasing"), default="decreasing",
                        help="out-return normalized-coordinate direction")
    for name, default in (("ramp-seconds", 10.), ("window-seconds", 90.), ("intent-ttl-seconds", 90.),
                          ("comparison-dwell-seconds", 6.), ("approach-room-degrees", 1.)):
        parser.add_argument("--"+name, type=lambda v: finite(v), default=default)
    parser.add_argument("--dwell-seconds", type=lambda v: finite(v, zero=True), default=3.)
    parser.add_argument("--bindings", type=Path, required=True, help="explicit current matched deployment binding")
    parser.add_argument("--url", default="https://192.168.1.134:8443")
    parser.add_argument("--ca", default=".cache/am1-session-pi-01/tls/ca.pem")
    parser.add_argument("--storage-state", default=".cache/am1-physical-adapter-01/physical-runs/browser-state.private.json")
    parser.add_argument("--output-root", default=".cache/am1-teleop-consolidation-16/physical-runs")
    args = parser.parse_args(argv)
    if max(args.ramp_seconds, args.window_seconds, args.intent_ttl_seconds) > 300:
        parser.error("existing input/native durations are bounded by300seconds")
    if (args.workpose == "midpoint" or args.approach_from_below) and not (
        args.plan == "out-return" and args.gripper_cycle and args.workpose == "midpoint" and args.approach_from_below):
        parser.error("midpoint requires out-return, gripper-cycle and explicit approach-from-below")
    if args.gripper_cycle and (args.plan != "out-return" or args.workpose == "relative" and args.direction != "increasing"):
        parser.error("relative gripper-cycle requires out-return and explicit increasing direction")
    if args.plan == "grasp-return" and (
        args.gripper_direction != "admitted" or args.degrees != 4. or args.direction != "increasing"
        or args.ramp_seconds != 10. or args.comparison_dwell_seconds != 3. or args.dwell_seconds != 3.
    ):
        parser.error("grasp-return requires admitted gripper,4degrees increasing,10second ramp and3second holds")
    return args


def console_path(root):
    return root / ".cache/am1-knowledge-reuse-18" / ("live-phase-"+str(uuid4())+".console.jsonl")


def live(snapshot):
    return snapshot["run"].get("live_input") or snapshot["evidence"]["live_input"]


def fresh_target(ready, admitted, *, degrees, direction, ramp_seconds):
    snapshot = admitted["snapshot"]
    workspace = validate_workspace(live(snapshot)["workspace"])
    commands = validate_target(ready["command_reference"], workspace=workspace)
    if (admitted.get("event") != "native_admitted" or ready["run_id"] != snapshot["run"]["run_id"]
        or ready["bounds"] != workspace["bounds"] or ready["units"] != workspace["units"]
        or live(snapshot)["reference"] != workspace["reference"]
        or commands != live(snapshot)["cursor"]
        or workspace.get("workspace_profile") != CURRENT_REST_WORKSPACE_PROFILES["ordinary-arms"]
        or direction not in ("decreasing", "increasing")):
        raise ValueError("fresh ready/native workspace, run, units and legal command reference must match")
    cal = workspace["calibration"][KEY[:-4]]
    delta = abs(normalized_raw(cal[3]+finite(degrees, zero=True)*PRACTICAL_ELBOW_COUNTS_PER_REVOLUTION/360, cal)
                - normalized_raw(cal[3], cal))
    q0 = commands[KEY]
    target = q0 + (delta if direction == "increasing" else -delta)
    targets = validate_target(dict(commands, **{KEY: target}), workspace=workspace)
    if delta/finite(ramp_seconds) > workspace["bounds"][KEY]["rate_per_s"]:
        raise ValueError("declared excursion/ramp exceeds the existing admitted rate")
    return dict(q0=q0, target=target, targets=targets, origin=dict(commands), calibration=list(cal), workspace=workspace)


def phases_for(plan, options, *, admitted=None):
    ramp, dwell = options.ramp_seconds, options.dwell_seconds
    lower_return = options.plan == "lower-return-gripper"
    grasp_return = options.plan == "grasp-return"
    midpoint_workpose = options.plan == "out-return" and getattr(options, "workpose", "relative") == "midpoint"
    if midpoint_workpose and not (getattr(options, "gripper_cycle", False) and getattr(options, "approach_from_below", False)):
        raise ValueError("explicit midpoint activity requires gripper-cycle and approach-from-below")
    if getattr(options, "gripper_cycle", False) or lower_return or grasp_return:
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
        elbow = origin[KEY] if lower_return else (bound["min"]+bound["max"])/2 if midpoint_workpose else plan["target"]
        held = validate_target(dict(origin, **{KEY: elbow}), workspace=plan["workspace"])
        grip_direction = getattr(options, "gripper_direction", "closing")
        if grip_direction not in ("opening", "closing", "admitted"):
            raise ValueError("explicit opening/closing/admitted gripper policy required")
        if grip_direction == "admitted":
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
        if 10./3. > plan["workspace"]["bounds"][GRIP]["rate_per_s"]:
            raise ValueError("full gripper excursion/return exceeds admitted rate")
        if grasp_return:
            if (grip_direction not in ("opening", "closing") or options.gripper_direction != "admitted"
                or any(origin[key] != plan["workspace"]["reference"][key] for key in (KEY, GRIP))):
                raise ValueError("grasp-return requires the original selected references and admitted interval")
            opening = grip_target[GRIP] if grip_direction == "opening" else origin[GRIP]
            closed = origin[GRIP] if grip_direction == "opening" else grip_target[GRIP]

            def grasp_phase(name, q, grip, *, ramp, dwell):
                validate_target(dict(origin, **{KEY: q, GRIP: grip}), workspace=plan["workspace"])
                return dict(name=name, op="move", target=q, targets={KEY: q, GRIP: grip},
                            ramp=ramp, dwell=dwell, complete_before_advance=True)

            phases = ([grasp_phase("grasp_prepare_open", origin[KEY], opening, ramp=3., dwell=2.)]
                      if grip_direction == "opening" else [])
            phases += [
                grasp_phase("grasp_close", origin[KEY], closed, ramp=3., dwell=2.),
                grasp_phase("grasp_carry", elbow, closed, ramp=ramp, dwell=options.comparison_dwell_seconds),
                grasp_phase("grasp_return_original_closed", origin[KEY], closed, ramp=ramp, dwell=dwell),
                grasp_phase("grasp_release", origin[KEY], opening, ramp=3., dwell=2.),
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
    if phase["op"] == "move":
        targets = phase.get("targets", {KEY: phase["target"]})
        named = " ".join(key.removeprefix("arm_").removesuffix(".pos")+"="+repr(value)
                         for key,value in sorted(targets.items()))
        return f'move {phase["ramp"]} {ttl} '+named
    return f'return {phase["ramp"]} {ttl}' if phase["op"] == "return" else "finish"


def next_phase(status, *, phases, index, pending, accepted, supported, approach_ready=False):
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


def main(argv=None):
    options = parse_args(argv)
    uses_gripper = options.gripper_cycle or options.plan in ("lower-return-gripper", "grasp-return")
    relative_workpose = options.plan == "grasp-return" or options.plan == "out-return" and options.workpose == "relative"
    has_preparation = options.plan in ("bidirectional-midpoint", "lower-return-gripper") or options.approach_from_below
    console = console_path(ROOT)
    args = [sys.executable, "-u", "-B", "-m", "tools.am1_leader_adapter",
            "--url", options.url, "--ca", options.ca, "--storage-state", options.storage_state,
            "--bindings", str(options.bindings), "--output-root", options.output_root,
            "--prepare-current-rest", "--virtual-input", "interactive", "--duration-seconds", str(options.window_seconds)]
    ready = plan = tail = process = output = None
    phases, index, pending, accepted, last_report = [dict(name="finish", op="finish")], -1, None, None, -100.
    supported = False
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
        local("local_activity_declared", console=str(console), plan=options.plan, gripper_cycle=uses_gripper,
              gripper_direction=options.gripper_direction if uses_gripper else None,
              workpose=options.workpose if options.plan == "out-return" else None,
              approach_from_below=options.approach_from_below,
              degrees=options.degrees if relative_workpose else None,
              direction=options.direction if relative_workpose else None, ramp_seconds=options.ramp_seconds, dwell_seconds=options.dwell_seconds,
              window_elapsed_seconds=options.window_seconds, intent_ttl_seconds=options.intent_ttl_seconds,
              comparison_dwell_seconds=options.comparison_dwell_seconds,
              preparation_max_active_seconds=options.ramp_seconds if has_preparation else None,
              preparation_dwell_seconds=0. if has_preparation else None,
              approach_room_degrees=options.approach_room_degrees,
              view="normal required Chest", other_joints="fresh admitted holds", body_lift_intent="zero",
              **({"gripper_interval_policy": "one legal10-point interval; prepare opening when only opening is admitted",
                  "planned_active_seconds_options": [36., 41.], "gripper_opening_preparation_active_seconds": 5.}
                 if options.plan == "grasp-return" else {}))
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
                        plan = fresh_target(ready, tail.admitted, degrees=options.degrees if relative_workpose else 0.,
                                            direction=options.direction,
                                            ramp_seconds=options.ramp_seconds)
                        phases = phases_for(plan, options, admitted=tail.admitted); supported = True
                        local("local_fresh_targets", run_id=ready["run_id"], original_q0=plan["q0"],
                              calibration=plan["calibration"], counts_per_revolution=PRACTICAL_ELBOW_COUNTS_PER_REVOLUTION,
                              gripper_origin=plan["origin"][GRIP] if uses_gripper else None,
                              gripper_direction_requested=options.gripper_direction if uses_gripper else None,
                              gripper_direction_resolved=next((p["gripper_direction"] for p in phases if "gripper_direction" in p), None),
                              gripper_target=next((p["targets"][GRIP] for p in phases if "gripper_direction" in p), None),
                              gripper_calibration=plan["workspace"]["calibration"][GRIP[:-4]] if uses_gripper else None,
                              phases=phases, planned_active_seconds=sum(p.get("ramp", 0)+p.get("dwell", 0) for p in phases),
                              **({"gripper_interval": phases[0]["gripper_interval"]} if options.plan == "grasp-return" else {}))
                    except (ValueError, KeyError, TypeError, AttributeError, OSError) as error:
                        local("local_excursion_unsupported", reason=str(error), action="Finish without substitute")
                elif event == "virtual_intent_accepted":
                    print(line.rstrip(), flush=True)
                    if (pending is None or value["op"] != phases[pending]["op"] or type(value["seq"]) is not int
                        or value["seq"] <= (accepted or 0)):
                        raise RuntimeError("unexpected intention acceptance")
                    index, pending, accepted = pending, None, value["seq"]
                    local("local_phase_acceptance_observed", phase=phases[index]["name"], op=value["op"], seq=accepted)
                elif event in ("virtual_intent_discarded", "virtual_intent_rejected"):
                    print(line.rstrip(), flush=True); pending = None; supported = False
                elif event == "virtual_input_status":
                    if value["window_elapsed_s"]-last_report >= 4 or value["input_state"] in ("finished", "expired"):
                        print(line.rstrip(), flush=True); last_report = value["window_elapsed_s"]
                    if ready is None: continue
                    measured, approach_error = None, None
                    preparation = index >= 0 and phases[index].get("preparation") is True
                    if preparation and pending is None and accepted == value["current_accepted_intent_seq"]:
                        try:
                            measured = qualified_approach(index)
                        except ValueError as error:
                            approach_error = str(error)  # Before the maximum, keep streaming this fresh preparation.
                    following = next_phase(value, phases=phases, index=index, pending=pending, accepted=accepted,
                                           supported=supported, approach_ready=measured is not None)
                    if following is not None:
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
