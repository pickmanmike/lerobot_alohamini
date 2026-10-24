"""Authenticated PC leader producer for the existing persistent AM1 owner.

USB is the ordinary source. Virtual input enters before the identical mapping,
clutch, serialization, grant and native-expiry boundaries. No motor transport is
created here. An enrolled viewer is independent and never claims this input.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import hashlib
import json
import math
import ssl
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

from examples.alohamini.am1_live_teleop import (
    TELEOP_ARM_KEYS,
    TELEOP_RECIPE,
    ZERO_BODY,
    map_leader_action,
    normalized_raw,
    validate_body,
    validate_source,
    validate_target,
    validate_workspace,
)


class LeaderClutch:
    """Explicit acquisition reference, separate from unchanged motor calibration."""

    def __init__(self):
        self.reference = self.cursor = None
        self.last_clamps = {}

    def acquire(self, leader, cursor):
        self.reference, self.cursor = dict(leader), dict(cursor)

    def map(self, leader, workspace, *, absolute=False):
        if self.reference is None:
            raise ValueError("qualified current leader/cursor acquisition required")
        requested = dict(leader) if absolute else {
            k: self.cursor[k] + leader[k] - self.reference[k] for k in TELEOP_ARM_KEYS
        }
        result = {
            k: max(workspace["bounds"][k]["min"], min(workspace["bounds"][k]["max"], requested[k]))
            for k in TELEOP_ARM_KEYS
        }
        self.last_clamps = {
            k: dict(requested=requested[k], applied=result[k], **workspace["bounds"][k])
            for k in TELEOP_ARM_KEYS
            if result[k] != requested[k]
        }
        return result


def _peer_options(segment, side, direction, raw_step, *, bench=False, next_start=None, active_p=None):
    fixed = segment in {"left-peer-fixed", "right-peer-fixed"}
    peer = fixed or segment in {"left-peer-maintained", "right-peer-maintained"}
    if not peer:
        if any(value is not None for value in (side, direction, raw_step, active_p)):
            raise ValueError("peer options require an explicitly selected peer segment")
        return None
    if (side != segment.split("-", 1)[0] or type(direction) is not int or direction not in (-1, 1)
            or type(raw_step) is not int or not 1 <= raw_step <= 79 or next_start is not None):
        raise ValueError("peer profile requires matching side, raw direction, explicit1..79tick excursion and finite mode")
    result = {"side": side, "direction_raw": direction, "raw_step": raw_step}
    if fixed:
        start, end = (2067, 2015) if side == "left" else (1855, 1907)
        if (direction * raw_step != end-start or active_p is not None
                and (type(active_p) is not int or active_p not in (16, 32))):
            raise ValueError("fixed peer interval and explicit baseline/temporary P comparison required")
        result.update(fixed_start_raw=start, fixed_endpoint_raw=end, active_p=16 if active_p is None else active_p)
    elif active_p is not None:
        raise ValueError("peer active P selection requires the fixed comparison profile")
    return result


def _left_reach_cycles(args):
    count = getattr(args, "left_reach_cycles", 1)
    if type(count) is not int or not 1 <= count <= 3:
        raise ValueError("left-reach cycles must be an integer from1 through3")
    if count > 1 and (
        args.source != "virtual-leaders" or getattr(args, "workload_segment", "full") != "left-reach"
        or getattr(args, "bench_continuity", False) or getattr(args, "next_start_seconds", None) is not None
        or getattr(args, "interrupt_at_seconds", None) is not None or getattr(args, "interrupt_controller", False)
        or getattr(args, "duration_seconds", 180.) < 20. * count
    ):
        raise ValueError("repeated left-reach cycles require normal virtual left-reach without diagnostic interruptions")
    return count


class SelectedLeftWorkload:
    """Finite current input with fixed original targets, not a native trajectory."""

    kind = "virtual-leaders"
    DURATIONS = {"left-maintained": 16., "left-reach": 20., "left-supported-reach": 28., "left-opposite-first": 18.,
                 "left-peer-maintained": 16., "right-peer-maintained": 16., "left-peer-fixed": 20., "right-peer-fixed": 20.}
    elbow = "arm_left_elbow_flex.pos"
    shoulder = "arm_left_shoulder_lift.pos"
    gripper = "arm_left_gripper.pos"

    def __init__(self, workspace, *, segment="left-maintained", peer_side=None, peer_direction=None, peer_raw_step=None, peer_active_p=None,
                 command_reference=None, left_reach_cycles=1):
        if segment not in self.DURATIONS:
            raise ValueError("Unknown selected left finite profile")
        if (type(left_reach_cycles) is not int or not 1 <= left_reach_cycles <= 3
                or left_reach_cycles > 1 and segment != "left-reach"):
            raise ValueError("left-reach cycles require integer1..3 and the left-reach profile")
        self.left_reach_cycles = left_reach_cycles
        self.segment, self.duration_s = segment, self.DURATIONS[segment]
        self.peer = _peer_options(segment, peer_side, peer_direction, peer_raw_step, active_p=peer_active_p)
        self.fixed = self.peer is not None and "fixed_start_raw" in self.peer
        self.peer_baseline = None
        self.comparison_start_observation = None
        if self.peer:
            self.elbow = f"arm_{peer_side}_elbow_flex.pos"
            self.gripper = f"arm_{peer_side}_gripper.pos"
        self.workspace = validate_workspace(workspace)
        self.measured_reference = dict(self.workspace["reference"])
        self.command_reference = command_reference is not None
        # A measured resting pose can differ from its already qualified legal
        # hold Goal. Keep that measurement immutable, and never substitute the
        # actual selected q0 with a different command or a clamped measurement.
        self.origin = validate_target(self.measured_reference if command_reference is None else command_reference,
                                      workspace=self.workspace)
        selected = {self.elbow}
        if segment in {"left-reach", "left-supported-reach"}:
            selected.add(self.gripper)
        if segment == "left-supported-reach":
            selected.add(self.shoulder)
        self.origin.update({key: self.measured_reference[key] for key in selected})
        validate_target(self.origin, workspace=self.workspace)
        bound = self.workspace["bounds"][self.elbow]
        self.endpoint = (bound["max"] if segment == "left-opposite-first" else
                         max((bound["min"], bound["max"]), key=lambda value: abs(value-self.origin[self.elbow])))
        if self.peer:
            cal = self.workspace["calibration"][self.elbow[:-4]]
            value = -self.origin[self.elbow] if cal[1] else self.origin[self.elbow]
            reference = (value + 100.) / 200. * (cal[4] - cal[3]) + cal[3]
            self.reference_raw = round(reference)
            if abs(reference - self.reference_raw) > 1e-7:
                raise ValueError("peer reference must be the actual admitted encoder pose")
            self.endpoint_raw = self.peer["fixed_endpoint_raw"] if self.fixed else self.reference_raw + peer_direction * peer_raw_step
            low, high = self.workspace["raw_bounds"][self.elbow]
            if not low <= self.reference_raw <= high or not low <= self.endpoint_raw <= high:
                raise ValueError("peer endpoint is outside the unchanged supported workspace")
            self.endpoint = normalized_raw(self.endpoint_raw, cal)
            if self.fixed:
                self.comparison_start = normalized_raw(self.peer["fixed_start_raw"], cal)
                validate_target({**self.origin, self.elbow: self.comparison_start}, workspace=self.workspace)
            validate_target({**self.origin, self.elbow: self.endpoint}, workspace=self.workspace)
        if segment == "left-opposite-first":
            self.first_endpoint = bound["min"]
            ramps = ((self.origin[self.elbow], self.first_endpoint, 2.),
                     (self.first_endpoint, self.endpoint, 4.), (self.endpoint, self.origin[self.elbow], 4.))
            if any(abs(end-start) > duration * bound["rate_per_s"] + 1e-9 for start, end, duration in ramps):
                raise ValueError("opposite-first endpoint exceeds an unchanged ramp rate")
        elif self.fixed:
            if any(abs(end-start) > 4 * bound["rate_per_s"] + 1e-9 for start, end in (
                    (self.origin[self.elbow], self.comparison_start), (self.comparison_start, self.endpoint))):
                raise ValueError("fixed peer preparation/comparison exceeds unchanged four-second ramp rate")
        elif abs(self.endpoint-self.origin[self.elbow]) > 4 * bound["rate_per_s"] + 1e-9:
            raise ValueError("selected endpoint exceeds the unchanged four-second ramp rate")
        opening = self.workspace["bounds"][self.gripper]
        self.opening = (min(self.origin[self.gripper] + 20., opening["max"],
                            self.origin[self.gripper] + 2 * opening["rate_per_s"])
                        if segment in {"left-reach", "left-supported-reach"} else self.origin[self.gripper])
        if segment == "left-supported-reach":
            shoulder = self.workspace["bounds"][self.shoulder]
            self.shoulder_endpoint = shoulder["max"]
            if self.shoulder_endpoint-self.origin[self.shoulder] > 4 * shoulder["rate_per_s"] + 1e-9:
                raise ValueError("selected shoulder endpoint exceeds the unchanged four-second ramp rate")
        self.schedule = [("left_baseline", 0., 2.), ("left_outward", 2., 6.),
                         ("left_endpoint_hold", 6., 10. if segment == "left-maintained" or self.peer else 8.)]
        if segment == "left-reach":
            self.schedule += [("left_gripper_open", 8., 10.), ("left_gripper_return", 10., 12.)]
        retract = 10. if segment == "left-maintained" or self.peer else 12.
        self.schedule += [("left_retract", retract, retract + 4.),
                          ("left_final_hold", retract + 4., self.duration_s)]
        if segment == "left-supported-reach":
            self.schedule = [("left_baseline", 0., 2.), ("left_shoulder_outward", 2., 6.),
                             ("left_shoulder_hold", 6., 8.), ("left_outward", 8., 12.),
                             ("left_endpoint_hold", 12., 14.), ("left_gripper_open", 14., 16.),
                             ("left_gripper_return", 16., 18.), ("left_retract", 18., 22.),
                             ("left_shoulder_retract", 22., 26.), ("left_final_hold", 26., 28.)]
        if segment == "left-opposite-first":
            self.schedule = [("left_baseline", 0., 2.), ("left_opposite", 2., 4.),
                             ("left_opposite_hold", 4., 6.), ("left_outward", 6., 10.),
                             ("left_endpoint_hold", 10., 12.), ("left_retract", 12., 16.),
                             ("left_final_hold", 16., 18.)]
        if self.peer:
            self.schedule = [(name.replace("left_", peer_side + "_", 1), start, end) for name, start, end in self.schedule]
        if self.fixed:
            self.schedule = [(peer_side+"_prepare", 0., 4.), (peer_side+"_baseline", 4., 6.),
                             (peer_side+"_outward", 6., 10.), (peer_side+"_endpoint_hold", 10., 14.),
                             (peer_side+"_retract", 14., 18.), (peer_side+"_final_hold", 18., 20.)]
        self.cycle_duration_s = self.duration_s
        self.duration_s *= left_reach_cycles
        self.elapsed_s, self._last = 0., None
        self.phases_completed, self.interrupted_phases = set(), set()

    def observe_peer_baseline(self, state):
        """Use one current Native ACK and cached startup reads, never new settings I/O."""
        if not self.peer:
            return False
        run, evidence = state.get("run") or {}, state.get("evidence") or {}
        marker = evidence.get("accepted_host_action") or {}
        identity = (run.get("run_id"), run.get("backend_incarnation"), state.get("service_incarnation"))
        try:
            valid_identity = all(type(value) is str and str(UUID(value)) == value for value in identity)
        except (TypeError, ValueError, AttributeError):
            valid_identity = False
        if not valid_identity:
            return False
        if self.peer_baseline is not None and identity != self.peer_baseline["identity"]:
            raise RuntimeError("peer baseline original operation identity changed")
        revision = run.get("intent_revision")
        if (not evidence.get("native_ack") or not evidence.get("active_acknowledged")
                or not evidence.get("feedback") or evidence.get("fault")
                or marker.get("run_id") != identity[0] or marker.get("host_incarnation") != identity[1]
                or evidence.get("host_incarnation") != identity[1]
                or type(revision) is not int or marker.get("intent_revision") != revision
                or evidence.get("acknowledged_intent_revision") != revision
                or any(type(marker.get(k)) is not int or marker[k] < 0 for k in ("epoch", "accepted_action_sequence"))):
            return False
        proof = marker.get("teleop_elbow_settings")
        if proof is None:
            return False
        if (not isinstance(proof, dict) or type(proof.get("version")) is not int
                or proof["version"] not in (1, 2)
                or type(proof.get("sha256")) is not str or len(proof["sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in proof["sha256"])):
            raise RuntimeError("peer baseline closed cached proof required")
        compact_trial = proof["version"] == 2
        keys = {"version", "sha256", "left", "right"}
        if set(proof) != (keys | {"trial"} if compact_trial else keys) or compact_trial and not self.fixed:
            raise RuntimeError("peer baseline exact ordinary or fixed-trial proof required")
        trial, expected_p = proof.get("trial"), self.peer.get("active_p", 16)
        if compact_trial:
            if (type(trial) is not list or len(trial) != 4
                    or trial[0] != self.peer["side"]+"-response-p"
                    or type(trial[1]) is not str or len(trial[1]) != 64
                    or any(c not in "0123456789abcdef" for c in trial[1])
                    or type(trial[3]) is not int or trial[3] != expected_p):
                raise RuntimeError("peer baseline exact selected fixed trial binding required")
            try:
                valid_request = type(trial[2]) is str and str(UUID(trial[2])) == trial[2]
            except (TypeError, ValueError, AttributeError):
                valid_request = False
            if not valid_request:
                raise RuntimeError("peer baseline original trial identity required")
        elif expected_p != 16:
            raise RuntimeError("peer baseline temporary P requires an explicit matching current trial")
        if self.peer_baseline is not None and trial != self.peer_baseline.get("trial"):
            raise RuntimeError("peer baseline original trial request/source changed")
        if self.peer_baseline is not None and proof["sha256"] != self.peer_baseline["sha256"]:
            raise RuntimeError("peer baseline original cached settings changed")
        acquired = marker.get("acquired_at_monotonic_s")
        if type(acquired) not in (int, float) or not math.isfinite(acquired) or acquired < 0:
            raise RuntimeError("peer baseline original ACK acquisition unavailable")
        expected = [3, 777, 3, 10, 16, 0, 32, 0]
        for side in ("left", "right"):
            row = proof[side]
            if compact_trial:
                if (type(row) is not list or len(row) != 4
                        or type(row[3]) is not list or len(row[3]) != 4):
                    raise RuntimeError("peer baseline exact compact side row required")
                row = {"motor": f"arm_{side}_elbow_flex", "motor_id": 3,
                       "calibration": row[0], "original": row[1], "active": row[2],
                       "original_read_interval": row[3][:2], "active_read_interval": row[3][2:]}
            motor = f"arm_{side}_elbow_flex"
            if (not isinstance(row, dict) or set(row) != {"motor", "motor_id", "calibration", "original", "active",
                                                        "original_read_interval", "active_read_interval"}
                    or row["motor"] != motor or type(row["motor_id"]) is not int or row["motor_id"] != 3
                    or type(row["calibration"]) is not list or len(row["calibration"]) != 5
                    or any(type(value) is not int for value in row["calibration"])
                    or row["calibration"] != self.workspace["calibration"][motor]
                    or row["calibration"] != ([3, 0, 1845, 864, 3073] if side == "left" else [3, 0, -658, 827, 3039])):
                raise RuntimeError("peer baseline actual side/mapping/calibration differs")
            for phase in ("original", "active"):
                wanted = list(expected)
                if self.fixed and phase == "active" and side == self.peer["side"]:
                    wanted[4] = expected_p
                if (type(row[phase]) is not list or len(row[phase]) != len(expected)
                        or any(type(v) is not int for v in row[phase]) or row[phase] != wanted):
                    raise RuntimeError("peer baseline both elbows require original P16/role-specific active P and verified I0/D32 profile")
            intervals = [row[phase + "_read_interval"] for phase in ("original", "active")]
            if (any(type(v) is not list or len(v) != 2 or any(type(t) not in (int, float) or not math.isfinite(t) for t in v) for v in intervals)
                    or not 0 <= intervals[0][0] <= intervals[0][1] <= intervals[1][0] <= intervals[1][1] <= acquired):
                raise RuntimeError("peer baseline original startup read intervals differ")
        if self.peer_baseline is None:
            self.peer_baseline = {"identity": identity, "sha256": proof["sha256"],
                                  "epoch": marker["epoch"], "intent_revision": revision,
                                  "accepted_action_sequence": marker["accepted_action_sequence"],
                                  "acquired_at_monotonic_s": acquired}
            if self.fixed:
                self.peer_baseline["trial"] = json.loads(json.dumps(trial))
        if self.fixed and self.elapsed_s >= 6. and self.comparison_start_observation is None:
            self.comparison_start_observation = json.loads(json.dumps({
                "commanded_start_raw": self.peer["fixed_start_raw"], "progress_s": self.elapsed_s,
                "measured_positions": evidence.get("measured_positions"), "accepted_host_action": marker,
                "feedback_age_s": evidence.get("feedback_age_s"), "position_qualified": False,
                "basis": "existing current Native feedback before first outward input; no precision gate"}))
        return True

    def require_peer_baseline(self):
        if self.peer and self.elapsed_s > (0. if self.fixed else 2.) and self.peer_baseline is None:
            raise RuntimeError("peer baseline BOTH P16 settings were not verified before outward input")


    @property
    def complete(self):
        return self.elapsed_s >= self.duration_s

    @property
    def cycle_index(self):
        return min(self.left_reach_cycles, int(self.elapsed_s // self.cycle_duration_s) + 1)

    @property
    def cycle_elapsed_s(self):
        if self.left_reach_cycles == 1:
            return self.elapsed_s
        return self.cycle_duration_s if self.complete else round(self.elapsed_s % self.cycle_duration_s, 9)

    def cycle_metadata(self):
        return {"left_reach_cycles": self.left_reach_cycles, "cycle_index": self.cycle_index} if self.left_reach_cycles > 1 else {}

    @property
    def phase(self):
        return next((name for name, start, end in self.schedule if start <= self.cycle_elapsed_s < end), "complete")

    @property
    def body_phase_elapsed(self):
        return None

    def admit(self, now):
        self._last = now

    def freeze(self):
        if self._last is not None and 0 < self.elapsed_s < self.duration_s:
            self.interrupted_phases.add((f"cycle-{self.cycle_index}/" if self.left_reach_cycles > 1 else "") + self.phase)
        self._last = None

    def advance(self, now):
        if self._last is None or self.complete:
            return
        if now < self._last:
            raise ValueError("workload acquisition clock regressed")
        self.elapsed_s = min(self.duration_s, round(self.elapsed_s + min(.1, now-self._last), 9))
        self._last = now
        self.phases_completed.update(
            (f"cycle-{cycle+1}/" if self.left_reach_cycles > 1 else "") + name
            for cycle in range(self.left_reach_cycles) for name, _start, end in self.schedule
            if self.elapsed_s >= cycle * self.cycle_duration_s + end
        )

    def get_action(self):
        self.require_peer_baseline()
        target = dict(self.origin)
        if self.fixed:
            elapsed = self.elapsed_s
            if elapsed <= 4.:
                start, end, fraction = self.origin[self.elbow], self.comparison_start, elapsed/4.
            elif elapsed <= 14.:
                start, end, fraction = self.comparison_start, self.endpoint, (elapsed-6.)/4.
            else:
                start, end, fraction = self.endpoint, self.comparison_start, (elapsed-14.)/4.
            target[self.elbow] = start + max(0., min(1., fraction)) * (end-start)
            return {key.removeprefix("arm_"): value for key, value in target.items()}
        if self.segment == "left-opposite-first":
            elapsed = self.elapsed_s
            if elapsed <= 6.:
                start, end, fraction = self.origin[self.elbow], self.first_endpoint, (elapsed-2.) / 2.
            elif elapsed <= 12.:
                start, end, fraction = self.first_endpoint, self.endpoint, (elapsed-6.) / 4.
            else:
                start, end, fraction = self.endpoint, self.origin[self.elbow], (elapsed-12.) / 4.
            target[self.elbow] = start + max(0., min(1., fraction)) * (end-start)
            return {key.removeprefix("arm_"): value for key, value in target.items()}
        if self.segment == "left-supported-reach":
            elapsed = self.elapsed_s
            shoulder = (max(0., min(1., (elapsed-2.) / 4.)) if elapsed <= 22.
                        else max(0., 1. - (elapsed-22.) / 4.))
            elbow = (max(0., min(1., (elapsed-8.) / 4.)) if elapsed <= 18.
                     else max(0., 1. - (elapsed-18.) / 4.))
            opening = ((elapsed-14.) / 2. if 14. < elapsed <= 16.
                       else (18.-elapsed) / 2. if 16. < elapsed < 18. else 0.)
            target[self.shoulder] += shoulder * (self.shoulder_endpoint-self.origin[self.shoulder])
            target[self.elbow] += elbow * (self.endpoint-self.origin[self.elbow])
            target[self.gripper] += opening * (self.opening-self.origin[self.gripper])
            return {key.removeprefix("arm_"): value for key, value in target.items()}
        retract = 10. if self.segment == "left-maintained" or self.peer else 12.
        elapsed = self.cycle_elapsed_s if self.left_reach_cycles > 1 else self.elapsed_s
        fraction = (max(0., min(1., (elapsed-2.) / 4.)) if elapsed <= retract
                    else max(0., 1. - (elapsed-retract) / 4.))
        target[self.elbow] += fraction * (self.endpoint-self.origin[self.elbow])
        if self.segment == "left-reach":
            opening = ((elapsed-8.) / 2. if 8. < elapsed <= 10.
                       else (12.-elapsed) / 2. if 10. < elapsed < 12. else 0.)
            target[self.gripper] += opening * (self.opening-self.origin[self.gripper])
        return {key.removeprefix("arm_"): value for key, value in target.items()}

    def plan(self):
        bound, cal = self.workspace["bounds"][self.elbow], self.workspace["calibration"][self.elbow.removesuffix(".pos")]
        raw = min(self.workspace["raw_bounds"][self.elbow], key=lambda value: abs(normalized_raw(value, cal)-self.endpoint))
        result = {"segment": self.segment, "program_active_seconds": self.duration_s,
                "reference": dict(self.measured_reference), "endpoint_normalized": self.endpoint, "endpoint_raw_bound": raw,
                "direction_normalized": 1 if self.endpoint > self.origin[self.elbow] else -1,
                "travel_normalized": self.endpoint-self.origin[self.elbow], "rate_limit_per_s": bound["rate_per_s"],
                "gripper_opening_delta": self.opening-self.origin[self.gripper],
                "phases": [{"phase": name, "start_s": start, "end_s": end} for name, start, end in self.schedule],
                "active_progress_s": self.elapsed_s, "interrupted_phases": sorted(self.interrupted_phases),
                "dwell_basis": "producer active intent; native delivered dwell assessed separately",
                "target_policy": "fixed original admitted references and supported endpoint across qualified recovery"}
        if self.left_reach_cycles > 1:
            result.update(**self.cycle_metadata(), cycles_completed=int(self.elapsed_s // self.cycle_duration_s),
                          phases=[{"phase": name, "start_s": cycle * self.cycle_duration_s + start,
                                   "end_s": cycle * self.cycle_duration_s + end, "cycle_index": cycle + 1}
                                  for cycle in range(self.left_reach_cycles) for name, start, end in self.schedule])
        if self.command_reference:
            result["command_reference"] = dict(self.origin)
            result["command_reference_basis"] = "current native qualified HOLD cursor; selected measured q0 unchanged"
        if self.peer:
            result.pop("endpoint_raw_bound")
            result.update(peer_side=self.peer["side"], direction_raw=self.peer["direction_raw"], raw_step=self.peer["raw_step"],
                          reference_raw=self.reference_raw, endpoint_raw=self.endpoint_raw,
                          travel_encoder_degrees=self.peer["direction_raw"] * self.peer["raw_step"] * 360. / 4096,
                          baseline_settings_verified=self.peer_baseline is not None,
                          baseline_settings_binding=dict(self.peer_baseline) if self.peer_baseline else None)
        if self.fixed:
            result.update(comparison_start_raw=self.peer["fixed_start_raw"], comparison_start_normalized=self.comparison_start,
                          comparison_active_p=self.peer["active_p"],
                          comparison_start_qualification="commanded preparation; achieved pose assessed separately",
                          comparison_start_observation=json.loads(json.dumps(self.comparison_start_observation)))
        if self.segment == "left-opposite-first":
            raw = min(self.workspace["raw_bounds"][self.elbow],
                      key=lambda value: abs(normalized_raw(value, cal)-self.first_endpoint))
            result.update(first_endpoint_normalized=self.first_endpoint, first_endpoint_raw_bound=raw,
                          first_travel_normalized=self.first_endpoint-self.origin[self.elbow])
        if self.segment == "left-supported-reach":
            shoulder = self.workspace["bounds"][self.shoulder]
            cal = self.workspace["calibration"][self.shoulder.removesuffix(".pos")]
            raw = min(self.workspace["raw_bounds"][self.shoulder],
                      key=lambda value: abs(normalized_raw(value, cal)-self.shoulder_endpoint))
            result.update(shoulder_endpoint_normalized=self.shoulder_endpoint, shoulder_endpoint_raw_bound=raw,
                          shoulder_travel_normalized=self.shoulder_endpoint-self.origin[self.shoulder],
                          shoulder_rate_limit_per_s=shoulder["rate_per_s"])
        return result


class FrameProducer:
    def __init__(self, source, *, clock=time.perf_counter, source_id=None):
        self.input = source
        self.clock = clock
        self.source_id = source_id or str(uuid4())
        self.sequence = 0
        self.previous = None
        self.workspace = self.native_cursor = None
        self.clutch = LeaderClutch()
        self.reacquire = True
        self.last_clutch = None

    def capture(self, grant, *, body=None, grant_received_at=None):
        received = self.clock() if grant_received_at is None else grant_received_at
        acquired = self.clock()  # Earliest read start, before either leader bus.
        leader = map_leader_action(self.input.get_action())
        target = leader
        if self.workspace is not None:
            if self.reacquire:
                self.clutch.acquire(leader, self.native_cursor)
                self.last_clutch = {
                    "event": "leader_clutch_acquired",
                    "leader_reference": leader,
                    "native_cursor": dict(self.native_cursor),
                    "meaning": "explicit relative acquisition; calibration unchanged",
                }
                self.reacquire = False
            fixed = isinstance(self.input, SelectedLeftWorkload)
            if fixed and self.last_clutch:
                self.last_clutch["meaning"] = "fresh binding; declared original absolute programme targets remain fixed"
            target = self.clutch.map(leader, self.workspace, absolute=True) if fixed else self.clutch.map(leader, self.workspace)
        self.sequence += 1
        source = {
            "kind": self.input.kind,
            "source_id": self.source_id,
            "sample_seq": self.sequence,
            "acquired_at_monotonic_s": acquired,
            "grant_received_at_monotonic_s": received,
            "sent_at_monotonic_s": self.clock(),
            "grant_id": grant["grant_id"],
        }
        validate_source(source, grant_id=grant["grant_id"], previous=self.previous)
        self.previous = dict(source)
        return {
            "target": validate_target(target, workspace=self.workspace),
            "body": validate_body(body or ZERO_BODY),
            "source": source,
        }

    def serialize(self, frame):
        if not 0 <= self.clock() - frame["source"]["grant_received_at_monotonic_s"] < 0.25:
            raise ValueError("original capture/grant expired before serialization")
        return json.loads(json.dumps(frame, allow_nan=False))


class USBLeaderSource:
    kind = "usb-leaders"

    def __init__(self, *, left_port, right_port, leader_id, calibration_dir):
        self.options = {
            "left_port": left_port,
            "right_port": right_port,
            "leader_id": leader_id,
            "calibration_dir": calibration_dir,
        }
        self.leader = None
        self.connected = []
        self.last_raw = None

    def open(self):
        from examples.alohamini.leader_client_utils import make_normalized_bi_leader_config
        from lerobot.teleoperators.bi_so_leader import BiSOLeader

        config = make_normalized_bi_leader_config(
            left_port=self.options["left_port"],
            right_port=self.options["right_port"],
            leader_id=self.options["leader_id"],
            arm_profile="so-arm-5dof",
        )
        config.calibration_dir = Path(self.options["calibration_dir"])
        self.leader = BiSOLeader(config)
        try:
            for side in ("left", "right"):
                arm = getattr(self.leader, side + "_arm")
                if not arm.calibration or set(arm.calibration) != set(arm.bus.motors):
                    raise ValueError("existing complete " + side + " leader calibration required")
                arm.bus.connect(handshake=False)
                self.connected.append(arm.bus)
                if not arm.bus.is_calibrated:
                    raise ValueError(
                        side + " leader hardware calibration differs; recalibration is outside this adapter"
                    )
                torque = arm.bus.sync_read("Torque_Enable", normalize=False, num_retry=0)
                if set(torque) != set(arm.bus.motors) or any(v != 0 for v in torque.values()):
                    raise ValueError("leader sampling requires existing torque-off state")
        except BaseException:
            self.close()
            raise

    def get_action(self):
        if self.leader is None:
            raise ValueError("USB leaders are not connected")
        result, raw_frame = {}, {}
        # Same BiSOLeader/SOLeader named sync-read acquisition, retaining unclipped
        # raw values instead of SOLeader's legacy missing-calibration fallback.
        for side in ("left", "right"):
            bus = getattr(self.leader, side + "_arm").bus
            raw = bus.sync_read("Present_Position", normalize=False, num_retry=0)
            if set(raw) != set(bus.motors):
                raise ValueError("incomplete " + side + " leader frame")
            raw_frame[side] = dict(raw)
            for joint, value in raw.items():
                cal = bus.calibration[joint]
                identity = (cal.id, cal.drive_mode, cal.homing_offset, cal.range_min, cal.range_max)
                if type(value) is not int or not 0 <= value <= 4095 or cal.id != bus.motors[joint].id:
                    raise ValueError("invalid original leader encoder/mapping")
                result[side + "_" + joint + ".pos"] = normalized_raw(
                    value, identity, gripper=joint == "gripper"
                )
        self.last_raw = raw_frame
        return result

    def close(self):
        for bus in reversed(self.connected):
            bus.disconnect(disable_torque=False)
        self.connected.clear()


class CommandRefusedError(RuntimeError):
    """Original bounded WSS refusal, separate from transport uncertainty."""

    def __init__(self, response, *, timing=None):
        raw = json.dumps(response, allow_nan=False, separators=(",", ":"))
        if not isinstance(response, dict) or len(raw.encode()) > 65536:
            raise ValueError("bounded original command refusal required")
        self.response = json.loads(raw)
        self.reason = response.get("reason", "session command refused")
        self.timing = dict(timing or {})
        super().__init__(self.reason)


class _ResumePausedError(RuntimeError):
    """A known accepted Resume was superseded by a new ordinary pause."""

    def __init__(self, identity, event):
        super().__init__("accepted Resume superseded by a new ordinary pause")
        self.identity, self.event = json.loads(json.dumps([identity, event], allow_nan=False))
        self.episode = None


class _NativeRecoveryPendingError(RuntimeError):
    """Source-qualified native recovery interrupts unfinished held qualification."""

    def __init__(self, context, recovery, ceiling, original_error, pending_episode=None):
        super().__init__("new native recovery interrupted held qualification")
        self.context, self.recovery = json.loads(json.dumps([context, recovery], allow_nan=False))
        self.ceiling, self.original_error, self.episode = ceiling, original_error, None
        self.pending_episode = json.loads(json.dumps(pending_episode, allow_nan=False))


def _latest_run_event(snapshot, identity):
    return next((event for event in reversed(snapshot.get("events", []))
                 if event.get("run_id") == identity["run_id"]
                 and event.get("service_incarnation") == identity["service_incarnation"]), {})


def _paused_binding_diagnostic(state, fresh, identity, original_event, release_loss,
                               connection, native_remaining, ceiling):
    """Fault-only compared scalars; never an alternative qualification policy."""
    def scalar(value):
        if value is None or type(value) is bool:
            return value
        if type(value) is str:
            return value[:128]
        if type(value) is int and -(2 ** 63) <= value < 2 ** 63:
            return value
        if type(value) is float and math.isfinite(value):
            return value
        return "<invalid>"

    def fields(value, keys):
        value = value if isinstance(value, dict) else {}
        return {key: scalar(value.get(key)) for key in keys}

    run, evidence, live = fresh["run"], fresh.get("evidence", {}), _live(fresh)
    ack = evidence.get("accepted_host_action") or {}
    revision, stamp = run.get("intent_revision"), ack.get("acquired_at_monotonic_s")
    old_stamp = (state.get("evidence", {}).get("accepted_host_action") or {}).get("acquired_at_monotonic_s")
    current_remaining = run.get("remaining_s")
    event = _latest_run_event(fresh, identity)
    flags = ("feedback", "required_observation", "hold_acknowledged", "pose_aligned", "native_ack")
    checks = (
        ("status", lambda: run.get("status") != "paused"),
        ("recovery", lambda: run.get("recovery") is not None),
        ("pause_event", lambda: event != original_event),
        ("intent_revision", lambda: type(revision) is not int or revision not in
         ((identity["intent_revision"],) if release_loss else (identity["intent_revision"], identity["intent_revision"] + 1))),
        ("connection", lambda: connection != identity["connection_generation"]),
        ("readiness", lambda: not all(evidence.get(k) is True for k in flags)),
        ("active_ack", lambda: evidence.get("active_acknowledged") is not False),
        ("ack_run", lambda: ack.get("run_id") != run["run_id"]),
        ("ack_host", lambda: ack.get("host_incarnation") != run["backend_incarnation"]),
        ("ack_state", lambda: ack.get("state") != "paused"),
        ("ack_revision", lambda: type(ack.get("intent_revision")) is not int or ack["intent_revision"] != revision),
        ("acknowledged_revision", lambda: type(evidence.get("acknowledged_intent_revision")) is not int
         or evidence["acknowledged_intent_revision"] != revision),
        ("cursor_revision", lambda: type(live.get("cursor_intent_revision")) is not int or live["cursor_intent_revision"] != revision),
        ("required_view", lambda: not _view_admitted(fresh)),
        ("clock_types", lambda: any(type(v) not in (int, float) or not math.isfinite(v)
         for v in (stamp, old_stamp, current_remaining))),
        ("remaining_clock", lambda: not 0 < current_remaining <= native_remaining),
        ("ack_clock", lambda: not old_stamp <= stamp <= run["deadline"] - current_remaining),
    )
    failed = []
    for label, predicate in checks:
        try:
            if predicate():
                failed.append(label)
        except Exception:
            failed.append(label + ":unavailable")
    detail = {
        "authority": {"sent_identity": fields(identity, identity),
                      "fresh_identity": fields({"run_id": run.get("run_id"), "service_incarnation": fresh.get("service_incarnation"),
                          "host_incarnation": run.get("backend_incarnation"), "intent_revision": revision,
                          "controller_generation": (fresh.get("controller") or {}).get("controller_generation"),
                          "connection_generation": connection}, identity),
                      "status": scalar(run.get("status")), "recovery_present": run.get("recovery") is not None,
                      "original_event": fields(original_event, ("kind", "at", "run_id", "service_incarnation")),
                      "fresh_event": fields(event, ("kind", "at", "run_id", "service_incarnation"))},
        "proof": {"flags": fields(evidence, flags), "active": scalar(evidence.get("active_acknowledged")),
                  "ack": fields(ack, ("run_id", "host_incarnation", "state", "intent_revision", "acquired_at_monotonic_s")),
                  "acknowledged_revision": scalar(evidence.get("acknowledged_intent_revision")),
                  "cursor_revision": scalar(live.get("cursor_intent_revision")),
                  "view": fields(live.get("required_view"), ("required", "qualified", "role", "cause"))},
        "clocks": {"old_ack_at": scalar(old_stamp), "fresh_ack_at": scalar(stamp),
                   "original_remaining_s": scalar(native_remaining), "fresh_remaining_s": scalar(current_remaining),
                   "native_deadline": scalar(run.get("deadline")), "local_ceiling": scalar(ceiling),
                   "observed_at_monotonic_s": scalar(time.monotonic())},
        "failed": failed,
    }
    if len(json.dumps(detail, allow_nan=False).encode()) > 8192:
        raise ValueError("bounded paused binding diagnostic required")
    return detail


class HTTPOutcomeError(RuntimeError):
    """A bounded original HTTP outcome; refusal and ambiguity remain distinct."""

    def __init__(self, status, response, *, detail="unqualified authenticated HTTP outcome"):
        self.http_status = status
        self.response, self.response_error = None, None
        try:
            raw = json.dumps(response, allow_nan=False, separators=(",", ":"))
            if len(raw.encode()) > 65536:
                raise ValueError("HTTP outcome exceeds64KiB")
            self.response = json.loads(raw)
        except (TypeError, ValueError) as error:
            self.response_error = str(error)
        self.non_admitted = isinstance(self.response, dict) and all(
            (
                self.response.get("accepted") is False,
                self.response.get("effect_admitted") is False,
                self.response.get("status") == "refused",
                self.response.get("uncertain", False) is False,
            )
        )
        reason = self.response.get("reason") if isinstance(self.response, dict) else None
        super().__init__(reason if isinstance(reason, str) else detail)


def expired_input_refusal(error):
    return isinstance(error, CommandRefusedError) and all(
        error.response.get(key) == value and type(error.response.get(key)) is type(value)
        for key, value in {
            "accepted": False,
            "status": "refused",
            "reason": "expired grant",
            "effect_admitted": False,
        }.items()
    )


def mailbox_input_refusal(error):
    identity = getattr(error, "identity", None)
    keys = {"run_id", "service_incarnation", "controller_generation", "connection_generation",
            "host_incarnation", "intent_revision"}
    return (
        isinstance(error, CommandRefusedError)
        and getattr(error, "operation", None) == "input"
        and isinstance(identity, dict) and set(identity) == keys
        and type(identity["intent_revision"]) is int and identity["intent_revision"] >= 0
        and all(type(identity[key]) is str and identity[key] for key in keys - {"intent_revision"})
        and error.response.get("uncertain", False) is False
        and all(
            error.response.get(key) == value and type(error.response.get(key)) is type(value)
            for key, value in {"accepted": False, "status": "refused",
                               "reason": "physical input mailbox refused", "effect_admitted": False}.items()
        )
    )


def recovery_grant_refusal(error):
    return (
        isinstance(error, CommandRefusedError)
        and getattr(error, "operation", None) == "grant"
        and all(
            error.response.get(key) == value and type(error.response.get(key)) is type(value)
            for key, value in {
                "accepted": False,
                "status": "refused",
                "reason": "interactive running required",
                "effect_admitted": False,
            }.items()
        )
    )


def selected_view_refusal(error):
    identity = getattr(error, "identity", None)
    keys = {"run_id", "service_incarnation", "controller_generation", "connection_generation",
            "host_incarnation", "intent_revision"}
    return (isinstance(error, CommandRefusedError) and getattr(error, "operation", None) in {"grant", "input"}
            and isinstance(error.response, dict)
            and isinstance(identity, dict) and set(identity) == keys
            and type(identity["intent_revision"]) is int and identity["intent_revision"] >= 0
            and all(type(identity[key]) is str and identity[key] for key in keys - {"intent_revision"})
            and error.response.get("uncertain", False) is False
            and all(type(error.response.get(key)) is type(value) and error.response[key] == value
                    for key, value in {"accepted": False, "status": "refused", "effect_admitted": False,
                                       "reason": "required selected view expired"}.items()))


def _resume_send_failure(error, identity, operation_id, ws):
    """Only the original current-channel send site can establish this unknown outcome."""
    from aiohttp import ClientConnectionResetError

    detail = getattr(error, "resume_send_failure", None)
    keys = {"run_id", "service_incarnation", "controller_generation", "connection_generation",
            "host_incarnation", "intent_revision"}
    if (type(error) is not ClientConnectionResetError or str(error) != "Cannot write to closing transport"
        or ws is None or getattr(error, "_resume_send_ws", None) is not ws
        or not isinstance(detail, dict) or set(detail) != {"operation_id", "request_id", "identity", "timing", "error_type", "message"}
        or detail["error_type"] != "ClientConnectionResetError" or detail["message"] != str(error)
        or detail["operation_id"] != operation_id or type(operation_id) is not str
        or type(detail["request_id"]) is not str
        or not isinstance(detail["identity"], dict) or set(detail["identity"]) != keys
        or not isinstance(identity, dict) or set(identity) != keys
        or any(type(detail["identity"][key]) is not type(value) or detail["identity"][key] != value
               for key, value in identity.items())):
        return None
    try:
        if str(UUID(operation_id)) != operation_id or str(UUID(detail["request_id"])) != detail["request_id"]:
            return None
    except ValueError:
        return None
    stamps = detail["timing"]
    if (not isinstance(stamps, dict)
        or set(stamps) != {"request_started_at_monotonic_s", "send_started_at_monotonic_s",
                          "send_failed_at_monotonic_s"}
        or any(type(value) not in (int, float) or not math.isfinite(value) for value in stamps.values())
        or not stamps["request_started_at_monotonic_s"] <= stamps["send_started_at_monotonic_s"]
        <= stamps["send_failed_at_monotonic_s"] <= time.perf_counter()):
        return None
    return detail



def _startup_run_reference(state):
    """Only the existing essential-status run reference can omit startup clocks."""
    projection = state.get("status_projection")
    if projection != {"kind": "am1-essential-status", "version": 1}:
        return None
    if type(projection["version"]) is not int:
        raise RuntimeError("typed original startup projection required")
    run = state.get("run") or {}
    reference = run.get("details_reference")
    keys = {"kind", "version", "service_incarnation", "run_id", "operation_id", "host_incarnation",
            "field", "sha256", "bytes"}
    if (not isinstance(reference, dict) or set(reference) != keys
        or reference["kind"] != "am1-status-field-reference" or type(reference["version"]) is not int
        or reference["version"] != 1 or reference["field"] != "run"
        or type(reference["bytes"]) is not int or not 0 < reference["bytes"] <= 8 * 1024**2
        or type(reference["sha256"]) is not str or len(reference["sha256"]) != 64
        or any(c not in "0123456789abcdef" for c in reference["sha256"])
        or any(key not in run for key in ("run_id", "operation_id", "startup_deadline"))
        or reference["service_incarnation"] != state.get("service_incarnation")
        or reference["run_id"] != run.get("run_id") or reference["operation_id"] != run.get("operation_id")
        or reference["host_incarnation"] != run.get("backend_incarnation")):
        raise RuntimeError("original startup run detail reference unavailable or changed")
    if run.get("backend_incarnation") is None and (
        run.get("status") != "starting"
        or any(key not in run or run[key] is not None for key in ("native_live_at", "deadline"))
    ):
        raise RuntimeError("original startup pending backend identity unavailable or changed")
    for key in ("service_incarnation", "run_id", "operation_id", "host_incarnation"):
        value = reference[key]
        if value is None and key == "host_incarnation":
            continue
        if type(value) is not str or str(UUID(value)) != value:
            raise RuntimeError("canonical original startup run detail identity required")
    return reference


async def _startup_run_detail(client, state, remaining):
    """Read one immutable startup run using the existing bounded detail contract."""
    reference = _startup_run_reference(state)
    if reference is None:
        return state.get("run") or {}
    raw = bytearray()
    while len(raw) < reference["bytes"]:
        query = urlencode({"reference": json.dumps(reference, separators=(",", ":")),
                           "offset": len(raw), "limit": 8192})
        budget = remaining()
        reply = await asyncio.wait_for(client.get("state?" + query), budget)
        remaining()
        keys = {"accepted", "status", "reference", "offset", "length", "total_bytes", "encoding", "data"}
        if (not isinstance(reply, dict) or set(reply) != keys or reply["accepted"] is not True
            or reply["status"] != "detail" or reply["reference"] != reference
            or json.dumps(reply["reference"], sort_keys=True) != json.dumps(reference, sort_keys=True)
            or type(reply["offset"]) is not int or reply["offset"] != len(raw)
            or type(reply["total_bytes"]) is not int or reply["total_bytes"] != reference["bytes"]
            or type(reply["length"]) is not int or reply["encoding"] != "base64"
            or type(reply["data"]) is not str or len(reply["data"]) > 4 * ((8192 + 2) // 3)):
            raise RuntimeError("original startup run detail chunk unqualified")
        block = base64.b64decode(reply["data"], validate=True)
        if not 0 < len(block) <= 8192 or len(block) != reply["length"] or len(raw) + len(block) > reference["bytes"]:
            raise RuntimeError("original startup run detail chunk length changed")
        raw.extend(block)
    if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
        raise RuntimeError("original startup run detail hash changed")
    run = json.loads(raw)
    if (not isinstance(run, dict)
        or json.dumps(run, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() != raw
        or ("backend_incarnation" in run) != ("backend_incarnation" in state["run"])
        or any(type(run.get(key)) is not type(state["run"].get(key))
               or run.get(key) != state["run"].get(key)
               for key in ("run_id", "operation_id", "recipe", "backend_incarnation", "startup_deadline"))):
        raise RuntimeError("original startup run detail identity or clocks changed")
    return run


class SessionClient:
    def __init__(self, origin, ca, storage_state):
        import aiohttp
        from yarl import URL

        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.username
        ):
            raise ValueError("exact trusted HTTPS origin required")
        self.origin = origin.rstrip("/")
        self.tls = ssl.create_default_context(cafile=str(ca))
        self.tls.minimum_version = ssl.TLSVersion.TLSv1_2
        state = json.loads(Path(storage_state).read_text(encoding="utf-8"))
        cookies = [
            c
            for c in state.get("cookies", [])
            if c.get("name") == "__Host-am1-device"
            and c.get("domain", "").lstrip(".") == parsed.hostname
            and c.get("path") == "/"
            and c.get("secure")
        ]
        if len(cookies) != 1:
            raise ValueError("one existing enrolled device cookie for exact trusted origin required")
        # Exact secure origin/domain validation above precedes private LAN IP use.
        jar = aiohttp.CookieJar(unsafe=True)
        jar.update_cookies({"__Host-am1-device": cookies[0]["value"]}, response_url=URL(self.origin))
        self.http = aiohttp.ClientSession(
            cookie_jar=jar,
            headers={"Origin": self.origin},
            timeout=aiohttp.ClientTimeout(total=3),
            connector=aiohttp.TCPConnector(ssl=self.tls),
        )
        self.ws = self.reader = None
        self.pending = {}
        self.snapshot = None
        self.csrf = self.device = self.generation = self.connection = None
        self.sequence = 0
        self.changed = asyncio.Event()
        self.transport_error = None
        self.transport_close = None
        self._detaching = False
        self._ack_task = self._ack_slot = self._ack_ws = None
        self._ack_revision = self._acked_revision = self._ack_error = None
        self._ack_changed = asyncio.Event()

    async def get(self, path):
        async with self.http.get(self.origin + "/api/" + path, allow_redirects=False) as response:
            result = await response.json()
            if response.status != 200:
                raise RuntimeError(result.get("reason", "authenticated session read refused"))
            return result

    async def post(self, path, body):
        from aiohttp import ContentTypeError

        async with self.http.post(
            self.origin + "/api/" + path,
            json=body,
            headers={"X-AM1-CSRF": self.csrf or ""},
            allow_redirects=False,
        ) as response:
            try:
                result = await response.json()
            except (ValueError, UnicodeError, ContentTypeError) as error:
                raise HTTPOutcomeError(response.status, None, detail="invalid HTTP JSON outcome") from error
            if (
                response.status != 200
                or not isinstance(result, dict)
                or result.get("accepted") is False
                or result.get("status") == "unknown"
                or result.get("accepted") is None
                and result.get("uncertain") is True
            ):
                raise HTTPOutcomeError(response.status, result)
            if path == "start":
                run_id = result.get("run_id")
                try:
                    owned = (
                        result.get("accepted") is True
                        and type(run_id) is str
                        and str(UUID(run_id)) == run_id
                        and result.get("operation_id") == body["operation_id"]
                    )
                except (TypeError, ValueError):
                    owned = False
                if not owned:
                    raise HTTPOutcomeError(
                        response.status, result, detail="unqualified Start receipt identity"
                    )
            return result

    async def attach(self):
        self.transport_error = None
        self.transport_close = None
        self._detaching = False
        device = await self.get("session")
        if device.get("control") is not True:
            raise ValueError("existing enrolled PC control capability required")
        self.csrf, self.device = device["csrf"], device["device_id"]
        attached = await self.post("attach", {})
        self.ws = await self.http.ws_connect(
            self.origin.replace("https:", "wss:") + "/api/ws",
            ssl=self.tls,
            heartbeat=2,
            max_msg_size=65536,
            compress=0,
        )
        self._ack_ws = self.ws
        self._ack_task = self._ack_slot = None
        self._ack_revision = self._acked_revision = self._ack_error = None
        self._ack_changed = asyncio.Event()
        await self.ws.send_json({"ticket": attached["ticket"]})
        self.reader = asyncio.create_task(self._read())
        try:
            await self.wait(lambda: self.snapshot is not None, 3)
        except ConnectionError as error:
            # Only this first-snapshot phase can qualify a transport-only
            # replacement. Capture the failed stream BEFORE detach changes it.
            error.initial_snapshot_transport_close = dict(self.transport_close or {})
            raise

    async def _read(self):
        import aiohttp

        ws = self.ws
        reason, reader_error = "stream-ended", None
        try:
            async for message in ws:
                if self.ws is not ws:
                    break  # A replaced stream cannot publish current proof or receipts.
                if message.type != aiohttp.WSMsgType.TEXT:
                    reason = "message-" + message.type.name.lower()
                    break
                value = json.loads(message.data)
                if value.get("kind") == "snapshot":
                    self.snapshot = value["snapshot"]
                    if "revision" in value:
                        if value["revision"] != self._ack_revision:
                            self._ack_revision = value["revision"]
                            self._ack_slot = (value["revision"], time.monotonic() + 2)
                            self._ack_changed.clear()
                        if self._ack_slot is not None and (self._ack_task is None or self._ack_task.done()):
                            self._ack_task = asyncio.create_task(SessionClient._send_acks(self, ws))
                    self.changed.set()
                elif value.get("kind") == "result":
                    future = self.pending.pop(value["id"], None)
                    if future and not future.done():
                        future.set_result(value)
                elif value.get("kind") == "unavailable":
                    raise RuntimeError("owner reporting unavailable")
        except asyncio.CancelledError:
            reason = "reader-cancelled"
            raise
        except Exception as error:
            reason = "reader-error"
            reader_error = {"error_type": type(error).__name__, "message": str(error)}
            if self.ws is ws:
                self.transport_error = self.transport_error or str(error)
        finally:
            if self.ws is ws:
                task = getattr(self, "_ack_task", None)
                if task is not None and task is not asyncio.current_task():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                self._ack_slot = None
                ack_changed = getattr(self, "_ack_changed", None)
                if ack_changed is not None:
                    ack_changed.set()
                exception = ws.exception()
                self.transport_close = {
                    "reason": reason,
                    "expected": self._detaching,
                    "close_code": ws.close_code,
                    "exception_type": type(exception).__name__ if exception else None,
                    "exception": str(exception) if exception else None,
                    "reader_error": reader_error,
                    "observed_at_monotonic_s": time.monotonic(),
                }
                if not self._detaching and self.transport_error is None:
                    self.transport_error = "current WSS connection ended: " + reason
                for future in self.pending.values():
                    if not future.done():
                        future.set_exception(ConnectionError("current WSS connection lost"))
                self.pending.clear()
                self.changed.set()

    async def _ack_failed(self, ws, error):
        if self.ws is not ws or self._ack_ws is not ws:
            return
        self._ack_error = self._ack_error or error
        self.transport_error = self.transport_error or "current WSS snapshot acknowledgement failed"
        self._ack_changed.set()
        self.changed.set()
        # Quarantine this authenticated stream immediately; no control is replayed.
        ws._response.close()
        task = self._ack_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _send_acks(self, ws):
        try:
            while self.ws is ws and self._ack_ws is ws and self._ack_slot is not None:
                revision, ceiling = self._ack_slot
                self._ack_slot = None
                remaining = ceiling - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("current WSS snapshot acknowledgement deadline expired")
                await asyncio.wait_for(ws.send_json({"ack": revision}), remaining)
                if self.ws is ws and self._ack_ws is ws and self._ack_revision == revision:
                    self._acked_revision = revision
                    self._ack_changed.set()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await SessionClient._ack_failed(self, ws, error)

    async def _wait_current_ack(self, ws, ceiling):
        while True:
            if self.ws is not ws or getattr(self, "_ack_ws", ws) is not ws:
                raise ConnectionError("current WSS snapshot acknowledgement stream changed")
            if getattr(self, "_ack_error", None) is not None:
                raise self._ack_error
            if self.transport_error or ws.closed:
                raise ConnectionError("current WSS snapshot acknowledgement connection ended")
            revision = getattr(self, "_ack_revision", None)
            initialized = getattr(self, "_ack_changed", None) is not None
            if (revision is None and not initialized
                    or revision is not None and getattr(self, "_acked_revision", None) == revision):
                return
            remaining = ceiling - time.monotonic()
            if remaining <= 0:
                error = TimeoutError("current WSS snapshot acknowledgement deadline expired")
                await SessionClient._ack_failed(self, ws, error)
                raise self._ack_error
            try:
                await asyncio.wait_for(self._ack_changed.wait(), remaining)
            except TimeoutError as error:
                await SessionClient._ack_failed(self, ws, error)
                if self._ack_error is error:
                    raise
                raise self._ack_error from error

    async def command(self, command):
        if (
            self.transport_error
            or self.ws is None
            or self.ws.closed
            or self.reader is not None
            and self.reader.done()
            or len(self.pending) >= 4
        ):
            raise ConnectionError("bounded current WSS connection required")
        identity = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self.pending[identity] = future
        timing = {"request_started_at_monotonic_s": time.perf_counter()}
        ack_ceiling, ws = time.monotonic() + 2, self.ws
        try:
            if command.get("op") not in {"pause", "stop"}:
                await SessionClient._wait_current_ack(self, ws, ack_ceiling)
            if self.ws is not ws or self.transport_error or ws.closed:
                raise ConnectionError("bounded current WSS connection required")
            timing["send_started_at_monotonic_s"] = time.perf_counter()
            try:
                await ws.send_json({"id": identity, "command": command})
            except ConnectionError as error:
                from aiohttp import ClientConnectionResetError

                if (command.get("op") == "resume" and type(error) is ClientConnectionResetError
                    and str(error) == "Cannot write to closing transport" and self.ws is ws):
                    # A send failure never establishes nonadmission. Retain the
                    # exact discarded operation/channel; qualification requires
                    # a new Connect, held proof and a different Resume UUID.
                    error._resume_send_ws = ws
                    error.resume_send_failure = {
                        "operation_id": command.get("operation_id"), "request_id": identity,
                        "error_type": type(error).__name__, "message": str(error),
                        "identity": {key: command.get(key) for key in ("run_id", "service_incarnation",
                            "controller_generation", "connection_generation", "host_incarnation", "intent_revision")},
                        "timing": dict(timing, send_failed_at_monotonic_s=time.perf_counter())}
                raise
            timing["send_completed_at_monotonic_s"] = time.perf_counter()
            try:
                # A connect fence can take effect before its receipt arrives.
                # Allow its bounded gateway call (1.5 s) to return; callers keep
                # their original phase ceilings and input/grant waits stay 1 s.
                result = await asyncio.wait_for(future, 2.0 if command.get("op") == "connect" else 1.0)
            except TimeoutError as error:
                if command.get("op") == "resume":
                    # Only the actual reply future's timeout is unknown admission;
                    # send failures and outer phase cancellation cannot set this.
                    error.resume_reply_timeout = {"operation_id": command.get("operation_id"),
                        "request_id": identity, "timing": dict(timing,
                            response_timeout_observed_at_monotonic_s=time.perf_counter())}
                raise
            timing["response_observed_at_monotonic_s"] = time.perf_counter()
            if result.get("accepted") is not True:
                raise CommandRefusedError(result, timing=timing)
            return result
        except (TimeoutError, ConnectionError) as error:
            if command.get("op") == "input":
                error.input_command_timing = dict(timing, outcome_observed_at_monotonic_s=time.perf_counter())
            raise
        finally:
            self.pending.pop(identity, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()  # A failed ACK barrier may leave an unread stream-close result.

    async def wait(self, predicate, timeout):
        deadline = time.monotonic() + timeout
        while True:
            if self.transport_error:
                raise ConnectionError(self.transport_error)
            if self.reader is not None and self.reader.done() or self.ws is not None and self.ws.closed:
                raise ConnectionError("current WSS connection is closed")
            if predicate():
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("qualified current native state did not arrive")
            self.changed.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.changed.wait(), 0.1)

    def identities(self):
        run = self.snapshot["run"]
        return {
            "run_id": run["run_id"],
            "service_incarnation": self.snapshot["service_incarnation"],
            "controller_generation": self.generation,
            "connection_generation": self.connection,
            "host_incarnation": run["backend_incarnation"],
            "intent_revision": run["intent_revision"],
        }

    async def claim(self):
        entered = time.monotonic()
        ceiling = entered + 1.0
        before = self.snapshot or {}
        device, service = self.device, before.get("service_incarnation")
        old_run, old_controller = before.get("run"), before.get("controller")
        run_keys = ("run_id", "operation_id", "recipe", "backend_incarnation", "status", "uncertain",
                    "cleanup", "first_cause", "native_first_cause", "native_live_at", "deadline",
                    "accepted_at", "startup_deadline", "intent_revision", "execution")
        run_anchor = {key: old_run[key] for key in run_keys if key in old_run} if isinstance(old_run, dict) else None
        initial = (self.generation is None and self.connection is None and self.transport_error is None
                   and self.transport_close is None and self.ws is not None and not self.ws.closed
                   and (self.reader is None or not self.reader.done()))

        def remaining():
            budget = ceiling - time.monotonic()
            if budget <= 0:
                raise TimeoutError("original initial Claim ceiling expired")
            return budget

        def owned(snapshot, generation=None):
            run = snapshot.get("run")
            controller = snapshot.get("controller") or {}
            if ("run" not in before or "run" not in snapshot
                or type(device) is not str or not device or type(service) is not str or not service
                or self.device != device or snapshot.get("service_incarnation") != service
                or self.generation is not None or self.connection is not None):
                raise RuntimeError("initial Claim authority changed")
            if run_anchor is None:
                if old_run is not None or run is not None:
                    raise RuntimeError("initial idle run changed")
            elif (not set(run_keys) <= set(run_anchor)
                  or any(type(run_anchor.get(key)) is not str or not run_anchor[key]
                         for key in ("run_id", "operation_id", "recipe", "backend_incarnation"))
                  or type(run_anchor.get("intent_revision")) is not int
                  or not isinstance(run_anchor.get("execution"), dict)
                  or type(run_anchor["execution"].get("complete")) is not bool
                  or any(value is not None and (type(value) not in (int, float) or not math.isfinite(value))
                         for value in (run_anchor.get(key) for key in
                                       ("native_live_at", "deadline", "accepted_at", "startup_deadline")))
                  or run_anchor.get("status") not in {"stopped", "faulted", "interrupted"}
                  or run_anchor.get("cleanup") != "physical_readback_verified"
                  or run_anchor.get("uncertain") is not False or not isinstance(run, dict)
                  or {key: run[key] for key in run_keys if key in run} != run_anchor
                  or any(type(run[key]) is not type(value) for key, value in run_anchor.items())
                  or type(run["execution"].get("complete")) is not bool):
                raise RuntimeError("initial known-cleanup terminal run changed")
            current = controller.get("controller_generation")
            expiry = controller.get("presence_expires")
            if (controller.get("device_id") != device or type(current) is not str
                or str(UUID(current)) != current or type(expiry) not in (int, float)
                or not math.isfinite(expiry) or expiry <= 0
                or generation is not None and current != generation
                or old_controller is not None and (old_controller.get("device_id") != device
                    or old_controller.get("controller_generation") != current)):
                raise RuntimeError("current initial Claim generation unavailable")
            return current

        try:
            budget = remaining()
            result = await asyncio.wait_for(self.command({"op": "claim"}), budget)
        except ConnectionError as original:
            close = dict(self.transport_close or {})  # Capture before quarantine changes the stream.
            stamp = close.get("observed_at_monotonic_s")
            if (not initial or close.get("expected") is not False or close.get("close_code") != 1006
                or close.get("reason") != "stream-ended" or close.get("reader_error") is not None
                or type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < entered):
                raise
            try:
                budget = remaining()
                await asyncio.wait_for(self.detach(), budget)
                budget = remaining()
                fresh = (await asyncio.wait_for(self.get("state"), budget))["snapshot"]
                generation = owned(fresh)
                budget = remaining()
                renewed = await asyncio.wait_for(
                    self.post("claim", {"controller_generation": generation}), budget)
                if (renewed.get("accepted") is not True or renewed.get("status") != "claimed"
                    or renewed.get("controller_generation") != generation
                    or type(renewed.get("presence_expires")) not in (int, float)
                    or not math.isfinite(renewed["presence_expires"])):
                    raise RuntimeError("initial Claim fenced renewal was not acknowledged")
                budget = remaining()
                await asyncio.wait_for(self.attach(), budget)
                owned(self.snapshot, generation)
                budget = remaining()
                fresh = (await asyncio.wait_for(self.get("state"), budget))["snapshot"]
                owned(fresh, generation)
                if fresh["controller"]["presence_expires"] != renewed["presence_expires"]:
                    raise RuntimeError("initial Claim renewed authority changed")
                remaining()
                result = renewed
            except (Exception, asyncio.CancelledError) as qualification_error:
                raise original from qualification_error
        self.generation = result["controller_generation"]

    async def renew(self):
        due = time.monotonic() + 0.45
        while True:
            await asyncio.sleep(max(0, due - time.monotonic()))
            # Include receipt time in the cadence; one overdue renewal is
            # current presence, not a burst of missed ticks or a new Claim.
            due = time.monotonic() + 0.45
            started = time.monotonic()
            detail = {"event": "controller_renewal", "controller_generation": self.generation,
                      "dispatch_at_monotonic_s": started}
            try:
                result = await self.post("claim", {"controller_generation": self.generation})
            except asyncio.CancelledError:
                detail["outcome"] = "cancelled"
                raise
            except Exception as error:
                detail.update(outcome="failed", error_type=type(error).__name__, message=str(error))
                raise
            else:
                detail.update(outcome="accepted", presence_expires=result.get("presence_expires"),
                              lease_seconds=result.get("lease_seconds"), service_timing=result.get("timing"))
            finally:
                detail["receipt_at_monotonic_s"] = time.monotonic()
                detail["roundtrip_seconds"] = detail["receipt_at_monotonic_s"] - started
                record = getattr(self, "record", None)
                if record is not None:
                    record(detail)

    async def resume(self, *, check=None, refusal_record=None, qualification_ceiling=None):
        if check:
            check(self.snapshot)
        identity = self.identities()
        try:
            if qualification_ceiling is None:
                connected = await self.command(dict(op="connect", **identity))
            else:
                budget = qualification_ceiling - time.monotonic()
                if budget <= 0:
                    raise TimeoutError("original held/view qualification ceiling exhausted")
                connected = await asyncio.wait_for(self.command(dict(op="connect", **identity)), budget)
        except ConnectionError as error:
            # Lost connect results are ambiguous, never nonadmission receipts.
            # This tag is only for the initial connect result.
            error.connect_identity = dict(identity)
            raise
        self.connection = connected["connection_generation"]
        self.snapshot = connected["snapshot"]
        self.sequence = 0
        if check:
            check(self.snapshot)
        await SessionClient._resume_connected(self, check=check, connect_identity=identity,
                                              refusal_record=refusal_record, qualification_ceiling=qualification_ceiling)

    async def _resume_connected(self, *, check=None, connect_identity=None, qualified_check=None,
                                refusal_record=None, qualification_ceiling=None):
        phase_ceiling = qualification_ceiling

        def phase_remaining():
            remaining = phase_ceiling - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("original held/view qualification ceiling exhausted")
            return remaining

        identity = self.identities()
        release_context = None if connect_identity is None else json.loads(json.dumps({
            "identity": identity, "connect_identity": connect_identity, "snapshot": self.snapshot,
        }, allow_nan=False))
        try:
            if qualification_ceiling is None:
                released = await self.command(dict(op="release_input", **identity))
            else:
                budget = phase_remaining()
                released = await asyncio.wait_for(self.command(dict(op="release_input", **identity)), budget)
        except (ConnectionError, TimeoutError) as error:
            if connect_identity is not None:
                # Only this empty release follows a known connect ACK. Its
                # outcome stays unknown; later waits/Resume/input are untagged.
                error.empty_release_context = release_context
            raise

        def qualified():
            if check:
                check(self.snapshot)
            evidence, run = self.snapshot.get("evidence", {}), self.snapshot.get("run", {})
            live = run.get("live_input") or evidence.get("live_input") or {}
            return (
                all(
                    evidence.get(k) is True
                    for k in (
                        "feedback",
                        "required_observation",
                        "hold_acknowledged",
                        "pose_aligned",
                        "native_ack",
                    )
                )
                and live.get("cursor_intent_revision") == run.get("intent_revision")
                and _view_admitted(self.snapshot)
                and (qualified_check is None or qualified_check(self.snapshot))
            )

        if phase_ceiling is None:
            phase_ceiling = time.monotonic() + 8
        first_refusal, refused_identity, refused_event, refused_state, refused_context = None, None, None, None, None

        def record_qualification(phase, current):
            if refusal_record is None:
                return
            try:
                detail = {"event": "resume_qualification_" + phase,
                          "proof_source": "pc_sent_snapshot" if phase == "refused" else "authenticated_owner_snapshot",
                          "identity": refused_identity, "operation_id": refused_context["operation_id"],
                          "receipt": refused_context["receipt"], "timing": refused_context["timing"],
                          "qualification": _paused_binding_diagnostic(
                              refused_state, current, refused_identity, refused_event, True,
                              self.connection, refused_state["run"]["remaining_s"], phase_ceiling)}
                if len(json.dumps(detail, allow_nan=False).encode("utf-8")) > 8192:
                    raise ValueError("bounded nonadmitted Resume proof required")
                refusal_record(detail)
            except (Exception, asyncio.CancelledError) as reporting_error:
                raise first_refusal from reporting_error

        def record_transport_phase(error, context):
            close = dict(getattr(self, "transport_close", None) or {})
            held = context.get("phase") == "held_qualification"
            release = context["release"]
            reply = getattr(error, "resume_reply_timeout", None)
            send_failure = (None if held or getattr(self, "_detaching", False) else
                            _resume_send_failure(error, context["identity"], context["operation_id"], getattr(self, "ws", None)))
            stamps = reply.get("timing", {}) if isinstance(reply, dict) else {}
            reply_timeout = (type(error) is TimeoutError and not held and isinstance(reply, dict)
                and set(reply) == {"operation_id", "request_id", "timing"}
                and reply["operation_id"] == context["operation_id"]
                and type(reply["request_id"]) is str and str(UUID(reply["request_id"])) == reply["request_id"]
                and isinstance(stamps, dict)
                and set(stamps) == {"request_started_at_monotonic_s", "send_started_at_monotonic_s",
                                    "send_completed_at_monotonic_s", "response_timeout_observed_at_monotonic_s"}
                and all(type(v) in (int, float) and math.isfinite(v) for v in stamps.values())
                and stamps["request_started_at_monotonic_s"] <= stamps["send_started_at_monotonic_s"]
                <= stamps["send_completed_at_monotonic_s"] <= stamps["response_timeout_observed_at_monotonic_s"]
                and not close and getattr(self, "transport_error", None) is None
                and self.ws is not None and not self.ws.closed and self.ws.exception() is None
                and (self.reader is None or not self.reader.done()) and not getattr(self, "_detaching", False))
            closed_transport = (str(error) == ("current WSS connection ended: stream-ended" if held else "current WSS connection lost")
                and close.get("expected") is False and close.get("close_code") == 1006
                and close.get("reason") == "stream-ended" and close.get("reader_error") is None
                and close.get("exception_type") == "ClientConnectionResetError"
                and close.get("exception") == "Cannot write to closing transport")
            if (context["connect_identity"] is not None and isinstance(release, dict)
                and all(type(release.get(k)) is type(v) and release[k] == v for k, v in
                        {"accepted": True, "status": "released", "effect_admitted": False}.items())
                and (closed_transport or reply_timeout or send_failure is not None)):
                context["transport_close"] = {key: close.get(key) for key in
                    ("expected", "close_code", "reason", "reader_error", "exception_type", "exception")} if close else {}
                if reply_timeout:
                    context["transport_close"], context["reply_timeout"] = {}, reply
                if send_failure is not None:
                    context["send_failure"] = json.loads(json.dumps(send_failure, allow_nan=False))
                error.resume_result_context = context
                if refusal_record:
                    try:
                        proof = context["snapshot"]
                        detail = {"event": "held_qualification_transport_closed" if held else "resume_result_unknown",
                                  "outcome": "resume_not_requested" if held else "unknown",
                                  "operation_id": context["operation_id"], "identity": context["identity"],
                                  "phase_ceiling_monotonic_s": context["phase_ceiling"], "native_remaining_s": proof["run"].get("remaining_s"),
                                  "sent_flags": {key: proof.get("evidence", {}).get(key) for key in
                                      ("feedback", "required_observation", "hold_acknowledged", "pose_aligned", "native_ack", "active_acknowledged")},
                                  "sent_ack": {key: (proof.get("evidence", {}).get("accepted_host_action") or {}).get(key)
                                               for key in ("state", "intent_revision", "acquired_at_monotonic_s")},
                                  "transport_close": context["transport_close"]}
                        if reply_timeout:
                            detail["reply_timeout"] = reply
                        if send_failure is not None:
                            detail["send_failure"] = send_failure
                        if len(json.dumps(detail, allow_nan=False).encode("utf-8")) > 2048:
                            raise ValueError("bounded owned qualification context required")
                        refusal_record(detail)
                    except (Exception, asyncio.CancelledError) as reporting_error:
                        del error.resume_result_context
                        raise error from reporting_error

        waiting_held = None
        try:
            while True:
                held_identity = self.identities()
                held_run = self.snapshot["run"]
                held_remaining = held_run.get("remaining_s")
                waiting_held = json.loads(json.dumps({
                    "phase": "held_qualification", "identity": held_identity, "snapshot": self.snapshot,
                    "operation_id": None, "prior_event": _latest_run_event(self.snapshot, held_identity),
                    "pi_lower_bound": (held_run["deadline"] - held_remaining
                                       if all(type(v) in (int, float) and math.isfinite(v)
                                              for v in (held_remaining, held_run.get("deadline"))) else None),
                    "phase_ceiling": phase_ceiling, "requested": time.monotonic(),
                    "connect_identity": connect_identity, "release": released,
                }, allow_nan=False))
                await self.wait(qualified, phase_remaining())
                waiting_held = None
                if first_refusal is not None:
                    # Only the authenticated owner can prove current held/view
                    # qualification after an explicitly nonadmitted Resume.
                    budget = phase_remaining()
                    self.snapshot = (await asyncio.wait_for(self.get("state"), budget))["snapshot"]
                    if first_refusal.response.get("reason") == "qualified release/alignment required":
                        record_qualification("observed", self.snapshot)
                    if check:
                        check(self.snapshot)
                    run, evidence, live = self.snapshot["run"], self.snapshot.get("evidence", {}), _live(self.snapshot)
                    actual = self.identities()
                    ack = evidence.get("accepted_host_action") or {}
                    stamp = ack.get("acquired_at_monotonic_s")
                    current_remaining = run.get("remaining_s")
                    old_run = refused_state["run"]
                    if (
                        set(actual) != set(refused_identity)
                        or any(type(actual[k]) is not type(v) or actual[k] != v for k, v in refused_identity.items())
                        or any(type(run.get(k)) is not type(v) or run.get(k) != v for k, v in old_run.items()
                               if k in {"run_id", "recipe", "operation_id", "backend_incarnation", "native_live_at", "deadline",
                                        "first_cause", "native_first_cause", "recovery_episodes"})
                        or any(type((self.snapshot.get("controller") or {}).get(k)) is not type(v)
                               or (self.snapshot.get("controller") or {}).get(k) != v
                               for k, v in (refused_state.get("controller") or {}).items()
                               if k in {"device_id", "controller_generation"})
                        or run.get("status") != "paused" or run.get("uncertain") is not False
                        or run.get("recovery") is not None or run.get("native_first_cause") or evidence.get("fault")
                        or _latest_run_event(self.snapshot, refused_identity) != refused_event
                        or live.get("reference") != _live(refused_state).get("reference")
                        or live.get("workspace") != _live(refused_state).get("workspace")
                        or type(current_remaining) not in (int, float) or not math.isfinite(current_remaining)
                        or not 0 < current_remaining <= old_run["remaining_s"]
                        or evidence.get("active_acknowledged") is not False
                        or type(evidence.get("acknowledged_intent_revision")) is not int
                        or evidence["acknowledged_intent_revision"] != refused_identity["intent_revision"]
                        or ack.get("run_id") != refused_identity["run_id"]
                        or ack.get("host_incarnation") != refused_identity["host_incarnation"]
                        or ack.get("state") != "paused" or type(ack.get("intent_revision")) is not int
                        or ack["intent_revision"] != refused_identity["intent_revision"]
                        or type(stamp) not in (int, float) or not math.isfinite(stamp)
                        or not run["deadline"] <= stamp + old_run["remaining_s"]
                        or stamp + current_remaining > run["deadline"]
                    ):
                        raise RuntimeError("fresh authority for nonadmitted view Resume changed")
                    if not qualified():
                        continue
                identity = self.identities()
                prior_event = json.loads(json.dumps(_latest_run_event(self.snapshot, identity), allow_nan=False))
                run = self.snapshot["run"]
                remaining = run.get("remaining_s")
                pi_lower_bound = (run["deadline"] - remaining
                                  if type(remaining) in (int, float) and math.isfinite(remaining) else None)
                sent_state = json.loads(json.dumps(self.snapshot, allow_nan=False))
                command = dict(op="resume", **identity, expected_intent_revision=run["intent_revision"],
                               operation_id=str(uuid4()))
                # Freeze the actual sent authority/proof BEFORE awaiting an
                # unknown command result. This never constitutes nonadmission.
                unknown_resume = json.loads(json.dumps({
                    "identity": identity, "snapshot": sent_state, "operation_id": command["operation_id"],
                    "prior_event": prior_event, "pi_lower_bound": pi_lower_bound,
                    "phase_ceiling": phase_ceiling, "requested": time.monotonic(), "connect_identity": connect_identity,
                    "release": released,
                }, allow_nan=False))
                try:
                    budget = phase_remaining()
                    resumed = await asyncio.wait_for(self.command(command), budget)
                    break
                except (ConnectionError, TimeoutError) as error:
                    try:
                        record_transport_phase(error, unknown_resume)
                    except (Exception, asyncio.CancelledError) as reporting_error:
                        if reporting_error is error:
                            raise
                        raise error from reporting_error
                    raise
                except CommandRefusedError as refusal:
                    reason = refusal.response.get("reason")
                    if (reason not in ("current required selected view must advance before Resume", "qualified release/alignment required")
                        or refusal.response.get("uncertain", False) is not False or not all(
                            type(refusal.response.get(k)) is type(v) and refusal.response[k] == v
                               for k, v in {"accepted": False, "status": "refused", "effect_admitted": False,
                                            "reason": reason}.items())):
                        raise
                    if reason == "qualified release/alignment required" and (
                        release_context is None or not isinstance(released, dict)
                        or any(type(released.get(k)) is not type(v) or released[k] != v for k, v in
                               {"accepted": True, "status": "released", "effect_admitted": False}.items())
                    ):
                        raise  # Only this known connect + empty-release phase is eligible.
                    first = first_refusal is None
                    if first:
                        first_refusal = refusal
                    if (
                        type(identity["intent_revision"]) is not int or identity["intent_revision"] < 0
                        or any(type(v) is not str or not v for k, v in identity.items() if k != "intent_revision")
                        or run.get("status") != "paused" or run.get("uncertain") is not False
                        or run.get("recovery") is not None or run.get("native_first_cause")
                        or self.snapshot.get("evidence", {}).get("fault")
                        or set(prior_event) != {"kind", "at", "run_id", "service_incarnation"}
                        or prior_event.get("kind") not in {"input_expired", "native_input_expired", "required_view_expired"}
                        or any(type(v) not in (int, float) or not math.isfinite(v)
                               for v in (prior_event.get("at"), pi_lower_bound))
                        or not run["native_live_at"] <= prior_event["at"] < run["deadline"]
                        or not run["native_live_at"] <= pi_lower_bound < run["deadline"]
                    ):
                        raise RuntimeError("original ordinary pause for nonadmitted view Resume unavailable") from None
                    refused_identity, refused_event, refused_state = json.loads(json.dumps(
                        [identity, prior_event, sent_state], allow_nan=False))
                    context = {"identity": refused_identity, "pause_event": refused_event,
                               "operation_id": command["operation_id"], "receipt": refusal.response,
                               "timing": refusal.timing, "remaining_s": phase_remaining()}
                    refused_context = context
                    if reason == "qualified release/alignment required":
                        if first:
                            first_refusal.resume_qualification_context = context
                        record_qualification("refused", sent_state)
                    else:
                        if first:
                            first_refusal.resume_view_context = context
                        if refusal_record:
                            refusal_record(dict(event="resume_view_refused", **context))
        except (Exception, asyncio.CancelledError) as error:
            if isinstance(error, ConnectionError) and waiting_held is not None:
                try:
                    record_transport_phase(error, waiting_held)
                except (Exception, asyncio.CancelledError) as reporting_error:
                    if first_refusal is not None:
                        raise first_refusal from reporting_error
                    raise
            if isinstance(error, _NativeRecoveryPendingError):
                error.held_view_ceiling = phase_ceiling
            if first_refusal is not None and error is not first_refusal:
                raise first_refusal from error
            raise

        active_ceiling = time.monotonic() + 5
        try:
            await self.wait(lambda: SessionClient._resume_active(
                self, identity, prior_event, pi_lower_bound, resumed, check), 5)
        except _ResumePausedError as outcome:
            if first_refusal is not None and first_refusal.response.get("reason") == "qualified release/alignment required":
                outcome.transport_error = first_refusal  # Preserve it through the existing +2 continuation.
            raise  # The positively accepted Resume uses the unchanged +2 path.
        except (Exception, asyncio.CancelledError) as error:
            if first_refusal is not None:
                raise first_refusal from error
            if isinstance(error, ConnectionError):
                from aiohttp import ServerTimeoutError

                ws = getattr(self, "ws", None)
                stream_error = ws.exception() if ws is not None else None
                close = dict(getattr(self, "transport_close", None) or {})
                if (resumed.get("accepted") is True and resumed.get("status") in {"running", "resuming"}
                    and type(stream_error) is ServerTimeoutError
                    and str(stream_error) == f"No PONG received after {ws._pong_heartbeat} seconds"
                    and close.get("expected") is False and close.get("reason") == "message-error"
                    and close.get("close_code") == 1006 and close.get("reader_error") is None):
                    # This is the known accepted Resume's active-observation
                    # phase, never an unknown command or nonadmission receipt.
                    error.accepted_resume_context = {
                        "identity": dict(identity), "operation_id": command["operation_id"],
                        "prior_event": prior_event, "pi_lower_bound": pi_lower_bound,
                        "native_remaining": remaining, "active_ceiling": active_ceiling,
                        "transport_close": close, "receipt": resumed,
                    }
            raise

    def _resume_active(self, identity, prior_event, pi_lower_bound, resumed, check=None):
        if check:
            check(self.snapshot)
        run = self.snapshot["run"]
        if run.get("status") == "paused" and run.get("intent_revision") != identity["intent_revision"]:
            event = _latest_run_event(self.snapshot, identity)
            at, prior_at = event.get("at"), prior_event.get("at")
            if (resumed.get("accepted") is True and resumed.get("status") in {"running", "resuming"}
                and type(identity["intent_revision"]) is int
                and all(type(value) is str and value for key, value in identity.items() if key != "intent_revision")
                and all(type(self.identities()[key]) is type(value) and self.identities()[key] == value
                        for key, value in dict(identity, intent_revision=identity["intent_revision"] + 2).items())
                and type(run.get("intent_revision")) is int
                and set(event) == {"kind", "at", "run_id", "service_incarnation"}
                and event.get("kind") in {"required_view_expired", "input_expired", "native_input_expired"}
                and all(type(value) in (int, float) and math.isfinite(value)
                        for value in (at, prior_at, pi_lower_bound))
                and prior_at < at and pi_lower_bound <= at < run["deadline"]):
                raise _ResumePausedError(identity, event)
            raise RuntimeError("accepted Resume superseded by an unqualified pause")
        return run.get("status") == "running" and self.snapshot["evidence"].get("active_acknowledged") is True

    async def detach(self):
        self._detaching = True
        ws, reader = self.ws, self.reader
        close_error, close_started = None, time.monotonic()
        self._ack_ws = self._ack_slot = None
        self._ack_revision = self._acked_revision = None
        if getattr(self, "_ack_changed", None) is not None:
            self._ack_changed.set()
        task = getattr(self, "_ack_task", None)
        try:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if ws is not None:
                try:
                    await asyncio.wait_for(ws.close(), 2)
                except (TimeoutError, ConnectionError) as error:
                    # The polite handshake may be unavailable. Definite local
                    # quarantine below still precedes every fresh attachment.
                    close_error = {"error_type": type(error).__name__, "message": str(error)}
        finally:
            if ws is not None:
                ws._response.close()
            self.ws = self.reader = self._ack_task = None
            self.snapshot = None
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("current WSS connection detached"))
            self.pending.clear()
            retired = {item for item in (task, reader) if item is not None and item is not asyncio.current_task()}
            for item in retired:
                item.cancel()
            if retired:
                await asyncio.gather(*retired, return_exceptions=True)
            if close_error is not None and getattr(self, "record", None) is not None:
                self.record({"event": "session_detach_close_interrupted", **close_error,
                             "started_at_monotonic_s": close_started,
                             "quarantined_at_monotonic_s": time.monotonic(),
                             "reader_drained": reader is None or reader.done(),
                             "ack_task_drained": task is None or task.done(), "pending_requests": len(self.pending)})

    async def close(self):
        try:
            await self.detach()
        finally:
            await self.http.close()


def _live(snapshot):
    return (
        (snapshot.get("run") or {}).get("live_input") or snapshot.get("evidence", {}).get("live_input") or {}
    )


def _view_admitted(snapshot):
    view = _live(snapshot).get("required_view") or {}
    return (view.get("qualified") is True or
            (snapshot.get("run") or {}).get("bench_continuity") is True and view.get("required") is False)


def _qualified_command_reference(client, workspace):
    """Read a current HOLD origin as data; required view still gates Resume/input."""
    state = client.snapshot or {}
    run, evidence, live = state.get("run") or {}, state.get("evidence") or {}, _live(state)
    ack, owner = evidence.get("accepted_host_action") or {}, state.get("controller") or {}
    revision, stamp = run.get("intent_revision"), ack.get("acquired_at_monotonic_s")
    start, deadline, remaining = (run.get(key) for key in ("native_live_at", "deadline", "remaining_s"))
    event = _latest_run_event(state, {"run_id": run.get("run_id"),
                                    "service_incarnation": state.get("service_incarnation")})
    if (run.get("recipe") != TELEOP_RECIPE or run.get("status") != "paused"
        or any(type(value) is not str or not value for value in
               (run.get("run_id"), run.get("backend_incarnation"), state.get("service_incarnation")))
        or run.get("uncertain") is not False or run.get("recovery") is not None
        or any(run.get(key) for key in ("first_cause", "native_first_cause")) or evidence.get("fault")
        or getattr(client, "transport_error", None)
        or not client.device or not client.generation
        or owner.get("device_id") != client.device or owner.get("controller_generation") != client.generation
        or not all(evidence.get(key) is True for key in
                   ("feedback", "required_observation", "hold_acknowledged", "pose_aligned", "native_ack"))
        or evidence.get("active_acknowledged") is not False
        or type(revision) is not int or revision < 0
        or type(ack.get("intent_revision")) is not int or ack["intent_revision"] != revision
        or type(evidence.get("acknowledged_intent_revision")) is not int
        or evidence["acknowledged_intent_revision"] != revision
        or type(live.get("cursor_intent_revision")) is not int or live["cursor_intent_revision"] != revision
        or ack.get("run_id") != run.get("run_id") or ack.get("host_incarnation") != run.get("backend_incarnation")
        or ack.get("state") != "paused" or type(ack.get("epoch")) is not int or ack["epoch"] < 0
        or ack.get("accepted_action") != {"mode": "raw_goal_hold", "body_velocity": 0.}
        or live.get("body") != ZERO_BODY
        or any(type(value) not in (int, float) for value in (live.get("body") or {}).values())
        or live.get("workspace") != workspace
        or live.get("reference") != workspace["reference"]
        or live.get("cursor") != live.get("accepted_target")
        or event.get("kind") in {"pause", "stop", "handoff", "released", "revoked"}
        or any(type(value) not in (int, float) or not math.isfinite(value)
               for value in (start, deadline, remaining, stamp))
        or not 0 < remaining <= deadline-start <= 300.
        or not start <= stamp <= deadline-remaining or deadline-remaining-stamp >= 1.):
        raise RuntimeError("current same-owner qualified native held command unavailable")
    return validate_target(live.get("cursor"), workspace=workspace)


async def _acquisition_delay(requested_at):
    # Receipt cost consumes this cadence; an overdue tick acquires one NEW
    # sample, rather than sleeping again or replaying missed samples.
    await asyncio.sleep(max(0, requested_at + .06 - time.monotonic()))


class SameRunRecovery:
    """Observe the existing Core episode before permitting explicit rejoin."""

    def __init__(self, client, record):
        self.client, self.record = client, record
        state, live = client.snapshot, _live(client.snapshot)
        self.original = json.loads(
            json.dumps(
                {
                    "run": {
                        key: state["run"][key]
                        for key in ("run_id", "recipe", "backend_incarnation", "native_live_at", "deadline")
                    } | ({"operation_id": state["run"]["operation_id"]} if "operation_id" in state["run"] else {}),
                    "service": state["service_incarnation"],
                    "controller": {"device_id": client.device, "controller_generation": client.generation},
                    "workspace": live["workspace"],
                    "reference": live["reference"],
                },
                allow_nan=False,
            )
        )
        self.episodes, self.completed_episodes, self.cause, self.pause_recoveries = 0, 0, None, 0
        self.connect_rebindings = 0
        self.check(state)

    async def reacquire(self, *, until=None):
        """Obtain a new generation for the persisted, still uncancelled bench task."""
        import aiohttp

        entered = time.monotonic()
        state = (await self.client.get("state"))["snapshot"]
        run, live = state.get("run") or {}, _live(state)
        event = _latest_run_event(state, {"run_id": self.original["run"]["run_id"],
                                         "service_incarnation": self.original["service"]})
        if (run.get("bench_continuity") is not True or run.get("status") != "paused"
            or state.get("controller") is not None or run.get("uncertain") is not False
            or state.get("service_incarnation") != self.original["service"]
            or any(run.get(k) != v for k, v in self.original["run"].items())
            or live.get("reference") != self.original["reference"]
            or live.get("workspace") != self.original["workspace"]
            or self.client.device != self.original["controller"]["device_id"]
            or self.client.generation != self.original["controller"]["controller_generation"]
            or not self.history_qualified(state) or run.get("recovery") is not None
            or run.get("native_first_cause") or state.get("evidence", {}).get("fault")
            or event.get("kind") in {"pause", "stop", "handoff", "revoked", "released"}
            or type(run.get("remaining_s")) not in (int, float)
            or not math.isfinite(run["remaining_s"]) or run["remaining_s"] <= 0):
            raise RuntimeError("same-operation reacquisition original task or intent unavailable")
        ceiling = min(entered + min(12., run["remaining_s"]), until if until is not None else float("inf"))
        if ceiling <= time.monotonic():
            raise TimeoutError("original same-operation reacquisition ceiling exhausted")
        old = self.original["controller"]["controller_generation"]
        request = {"operation_id": str(uuid4()), "reacquire": {
            "run_id": run["run_id"], "service_incarnation": self.original["service"],
            "start_operation_id": self.original["run"]["operation_id"], "previous_generation": old}}
        self.record({"event": "same_operation_reacquisition_requested", **request})
        for attempt in range(2):
            try:
                result = await asyncio.wait_for(self.client.post("claim", request), max(0, ceiling - time.monotonic()))
                break
            except (OSError, aiohttp.ClientError, HTTPOutcomeError) as error:
                if attempt or isinstance(error, HTTPOutcomeError) and error.non_admitted or time.monotonic() >= ceiling:
                    raise
                self.record({"event": "same_operation_claim_receipt_unknown", "operation_id": request["operation_id"],
                             "error_type": type(error).__name__, "message": str(error),
                             "meaning": "one exact operation replay; no new Claim or deadline extension"})
        generation = result.get("controller_generation")
        try:
            valid = type(generation) is str and str(UUID(generation)) == generation and generation != old
        except ValueError:
            valid = False
        if not valid or result.get("accepted") is not True or result.get("status") != "claimed":
            raise RuntimeError("fresh same-operation Claim receipt unavailable")
        for attempt in range(2):
            try:
                fresh = (await asyncio.wait_for(self.client.get("state"), max(0, ceiling - time.monotonic())))["snapshot"]
                break
            except (OSError, aiohttp.ClientError) as error:
                if attempt or time.monotonic() >= ceiling:
                    raise
                self.record({"event": "same_operation_state_read_interrupted", "operation_id": request["operation_id"],
                             "error_type": type(error).__name__, "message": str(error)})
        current = fresh.get("controller") or {}
        before = self.original["controller"]
        replacement = {"device_id": self.client.device, "controller_generation": generation}
        intent = (fresh.get("run") or {}).get("control_intent") or {}
        expected_intent = dict(replacement, eligible=True, service_incarnation=self.original["service"],
                               start_operation_id=self.original["run"]["operation_id"])
        if (any(current.get(k) != v for k, v in replacement.items())
            or any(type(intent.get(k)) is not type(v) or intent.get(k) != v for k, v in expected_intent.items())):
            raise RuntimeError("fresh same-operation generation was superseded")
        self.original["controller"] = replacement
        self.client.generation = generation
        try:
            self.check(fresh)
        except BaseException:
            self.original["controller"] = before
            self.client.generation = old
            raise
        self.client.snapshot, self.client.connection = fresh, None
        self.record({"event": "same_operation_reacquired", "receipt": result,
                     "previous_generation": old, "elapsed_seconds": time.monotonic() - entered,
                     "snapshot": fresh})

    async def finish_owned(self, *, bench, wall_deadline, renewal_check):
        """End finite owned input; a Pause receipt alone never proves Native HOLD."""
        label = "bench" if bench else "normal"
        entered = time.monotonic()
        async with asyncio.timeout(max(0, wall_deadline - entered)):
            before = (await self.client.get("state"))["snapshot"]
            self.client.snapshot = before
            self.check(before)
            run, live = before["run"], _live(before)
            intent = run.get("control_intent")
            view = live.get("required_view") or {}
            owned_intent = (
                isinstance(intent, dict) and intent.get("eligible") is True
                and intent.get("device_id") == self.client.device
                and intent.get("controller_generation") == self.client.generation
                and intent.get("service_incarnation") == self.original["service"]
                and intent.get("start_operation_id") == run.get("operation_id")
            ) if bench else intent is None and view.get("required") is True and view.get("qualified") is True
            remaining = run.get("remaining_s")
            if ((run.get("bench_continuity") is True) != bench or run.get("status") not in {"running", "paused"}
                or run.get("recovery") is not None or not owned_intent
                or type(run.get("intent_revision")) is not int
                or type(remaining) not in (int, float) or not math.isfinite(remaining) or remaining <= 0):
                raise RuntimeError(f"current uncancelled {label} finish authority unavailable")
            prior_event = _latest_run_event(before, self.client.identities())
            if prior_event and prior_event.get("kind") not in {"resumed", "input_expired", "native_input_expired", "required_view_expired"}:
                raise RuntimeError(f"current {label} intent was superseded before finishing")
            ceiling = min(wall_deadline, entered + remaining)
            revision, lower = run["intent_revision"] + 1, run["deadline"] - remaining
            old_epoch = (before.get("evidence", {}).get("accepted_host_action") or {}).get("epoch")
            request = {"run_id": run["run_id"], "operation_id": str(uuid4())}
            expected_intent = dict(intent, eligible=False, invalidated_by="pause") if bench else None
            def budget():
                renewal_check()
                value = ceiling - time.monotonic()
                if value <= 0:
                    raise TimeoutError(f"original {label} finishing ceiling exhausted")
                return value
            self.record({"event": f"{label}_finish_pause_requested", "operation_id": request["operation_id"],
                         "identity": self.client.identities(), "expected_intent_revision": revision,
                         "original_ceiling_monotonic_s": ceiling})
            receipt = await asyncio.wait_for(self.client.post("pause", request), budget())
            if (receipt.get("accepted") is not True or receipt.get("status") != "paused"
                or receipt.get("uncertain", False) is not False or receipt.get("effect_admitted") is not False):
                raise RuntimeError(f"own {label} Pause did not receive a definite paused acknowledgement")
            self.record({"event": f"{label}_finish_pause_acknowledged", "operation_id": request["operation_id"],
                         "receipt": receipt, "expected_intent_revision": revision})
            while True:
                state = (await asyncio.wait_for(self.client.get("state"), budget()))["snapshot"]
                self.client.snapshot = state
                self.check(state)
                current, evidence, live = state["run"], state.get("evidence", {}), _live(state)
                event, ack = _latest_run_event(state, self.client.identities()), evidence.get("accepted_host_action") or {}
                native_remaining = current.get("remaining_s")
                if (current.get("status") != "paused" or current.get("recovery") is not None
                    or type(current.get("intent_revision")) is not int or current["intent_revision"] != revision
                    or current.get("control_intent") != expected_intent
                    or set(event) != {"kind", "at", "run_id", "service_incarnation"} or event.get("kind") != "pause"
                    or type(event.get("at")) not in (int, float) or not math.isfinite(event["at"])
                    or not lower <= event["at"] < run["deadline"]
                    or type(native_remaining) not in (int, float) or not math.isfinite(native_remaining)
                    or not 0 < native_remaining <= remaining):
                    raise RuntimeError(f"own {label} Pause authority/revision was superseded")
                remaining = native_remaining
                current_ack = (ack.get("run_id") == run["run_id"] and ack.get("host_incarnation") == run["backend_incarnation"]
                    and type(ack.get("intent_revision")) is int and ack["intent_revision"] == revision
                    and ack.get("state") == "paused" and type(ack.get("epoch")) is int
                    and type(old_epoch) is int and ack["epoch"] > old_epoch
                    and type(ack.get("acquired_at_monotonic_s")) in (int, float)
                    and event["at"] <= ack["acquired_at_monotonic_s"] <= run["deadline"] - native_remaining
                    and live.get("cursor_intent_revision") == revision
                    and type(evidence.get("acknowledged_intent_revision")) is int
                    and evidence["acknowledged_intent_revision"] == revision)
                if current_ack:
                    body = live.get("body") or {}
                    if (set(body) != set(ZERO_BODY)
                        or not all(type(body[key]) in (int, float) and body[key] == 0 for key in ZERO_BODY)
                        or ack.get("accepted_action") != {"mode": "raw_goal_hold", "body_velocity": 0.0}):
                        raise RuntimeError("current native held zero body command unavailable")
                    if (all(evidence.get(key) is True for key in
                            ("feedback", "native_ack", "hold_acknowledged", "pose_aligned", "required_observation"))
                        and evidence.get("active_acknowledged") is False and _view_admitted(state)
                        and (bench or (_live(state).get("required_view") or {}).get("required") is True)):
                        measured = evidence.get("measured_body") or {}
                        if not all(type(measured.get(key)) in (int, float) and math.isfinite(measured[key]) for key in ZERO_BODY):
                            raise RuntimeError("current native held body feedback unavailable")
                        proof = {"snapshot": state, "pause_operation_id": request["operation_id"], "pause_receipt": receipt,
                                 "native_held_qualified": True, "zero_body_command": True,
                                 "measured_body_exact_zero": all(measured[key] == 0 for key in ZERO_BODY)}
                        self.record({"event": f"{label}_finish_hold_qualified", **proof,
                                     "original_ceiling_monotonic_s": ceiling})
                        return proof
                await asyncio.sleep(min(.03, budget()))

    def history_qualified(self, state):
        run = state.get("run") or {}
        return run.get("first_cause") == self.cause and run.get("recovery_episodes") == self.episodes

    def check(self, state, *, history=True):
        run, evidence, live = state.get("run") or {}, state.get("evidence", {}), _live(state)
        if (
            any(run.get(k) != v for k, v in self.original["run"].items())
            or state.get("service_incarnation") != self.original["service"]
            or any(
                (state.get("controller") or {}).get(k) != v for k, v in self.original["controller"].items()
            )
            or self.client.device != self.original["controller"]["device_id"]
            or self.client.generation != self.original["controller"]["controller_generation"]
            or live.get("workspace") != self.original["workspace"]
            or live.get("reference") != self.original["reference"]
        ):
            raise RuntimeError("original run ownership/reference/workspace/Live/deadline changed")
        count = run.get("recovery_episodes")
        if type(count) is not int or not self.episodes <= count <= 3:
            raise RuntimeError("original native recovery episode budget changed/exhausted")
        if (
            run.get("uncertain") is not False
            or evidence.get("fault")
            or run.get("native_first_cause")
            or run.get("status") in {"stopped", "faulted", "interrupted", "completed", "finishing"}
        ):
            raise RuntimeError(
                "current native recovery unsafe: "
                + str(evidence.get("fault") or run.get("native_first_cause") or run.get("status"))
            )
        if history and not self.history_qualified(state):
            raise RuntimeError("native recovery history has not qualified")
        if history and run.get("status") == "recovering":
            raise RuntimeError("native recovery must qualify before explicit Resume")

    def _grant_episode(self, state, context, *, pending=None, resume_unknown=False):
        """Use only the original Core required_hold descriptor, never infer it."""
        run, identity = state["run"], context["identity"]
        own = [event for event in state.get("events", [])
               if event.get("run_id") == identity["run_id"]
               and event.get("service_incarnation") == identity["service_incarnation"]]
        hold = next((event for event in reversed(own) if event.get("kind") == "required_hold"), {})
        episode = hold.get("recovery")
        continuing = pending is not None
        if continuing and (
            not isinstance(pending, dict) or set(pending) != {"recovery", "count", "cause"}
            or pending["count"] != self.episodes or not self.completed_episodes < self.episodes
            or pending["cause"] != self.cause or run.get("first_cause") != pending["cause"]
            or not isinstance(episode, dict)
            or any(episode.get(k) != pending["recovery"].get(k) for k in ("started", "ceiling"))
        ):
            raise RuntimeError("same pending native recovery original episode changed or closed")
        if (set(hold) != {"kind", "at", "run_id", "service_incarnation", "recovery"}
            or not isinstance(episode, dict) or set(episode) != {"started", "ceiling", "revision"}
            or any(type(episode[k]) not in (int, float) or not math.isfinite(episode[k])
                   for k in ("started", "ceiling"))
            or type(episode["revision"]) is not int
            or episode["revision"] not in (identity["intent_revision"], identity["intent_revision"] + 1)
            or type(hold.get("at")) not in (int, float) or not math.isfinite(hold["at"])
            or not self.original["run"]["native_live_at"] <= episode["started"] <= hold["at"] < episode["ceiling"]
            or not episode["ceiling"] <= min(episode["started"] + 10, run["deadline"])
            or run.get("recovery_episodes") != self.episodes + (0 if continuing else 1)
            or run.get("first_cause") not in {"feedback_loss", "native_active_ack_loss", "required_proof_loss"}
            or self.cause is not None and run.get("first_cause") != self.cause):
            raise RuntimeError("source-retained native recovery descriptor unavailable/invalid")
        index = own.index(hold)
        prior = context["snapshot"]["run"]
        if resume_unknown:
            previous = own[index - 1] if index else {}
            if (previous != _latest_run_event(context["snapshot"], identity)
                and (set(previous) != {"kind", "at", "run_id", "service_incarnation"}
                     or previous.get("kind") != "resumed"
                     or type(previous.get("at")) not in (int, float) or not math.isfinite(previous["at"])
                     or not context["pi_lower_bound"] <= previous["at"] <= hold["at"])):
                raise RuntimeError("unknown Resume native recovery preceding source event changed")
        if (not continuing and (episode["started"] + prior["remaining_s"] < prior["deadline"]
            or not resume_unknown and episode["revision"] == identity["intent_revision"] + 1
            and (index == 0 or own[index - 1].get("kind") not in
                 {"required_view_expired", "input_expired", "native_input_expired"}
                 or type(own[index - 1].get("at")) not in (int, float)
                 or own[index - 1]["at"] + prior["remaining_s"] < prior["deadline"]
                 or own[index - 1]["at"] > hold["at"]))):
            raise RuntimeError("source-retained native recovery preceding pause changed")
        remaining = run.get("remaining_s")
        if (type(remaining) not in (int, float) or not math.isfinite(remaining)
            or not episode["started"] <= hold["at"] or hold["at"] + remaining > run["deadline"]):
            raise RuntimeError("source-retained native recovery clock unavailable")
        if run["status"] == "paused":
            event = own[-1]
            if (run.get("recovery") is not None or type(run.get("intent_revision")) is not int
                or run["intent_revision"] != episode["revision"] + 1
                or event.get("kind") != "live_sensing_requalified"
                or type(event.get("at")) not in (int, float)
                # Compare sums to avoid cancellation in deadline - remaining;
                # no tolerance or timestamp replacement is introduced.
                or not hold["at"] <= event["at"] or event["at"] + remaining > run["deadline"]
                or not event["at"] < episode["ceiling"]
                or own[index + 1:] != [event]):
                raise RuntimeError("source-retained native recovery is closed or changed")
        elif run["status"] != "recovering" or run.get("recovery") != episode or own[-1] != hold:
            raise RuntimeError("source-retained native recovery current phase changed")
        return dict(episode)

    async def resume(
        self, *, wall_deadline=None, require_episode=False, refusal_identity=None, expected_pause=None,
        renewal_check=None, grant_error=None,
    ):
        clock = {"ceiling": wall_deadline, "anchored": False}
        interrupted, deferred, first_transport_error = None, [], grant_error
        grant_context, pending_recovery, resume_context = None, None, None
        consumed_resumes = set()
        if grant_error is not None:
            try:
                grant_context = getattr(grant_error, "grant_context", None)
                if (not isinstance(grant_error, (TimeoutError, ConnectionError))
                    or not isinstance(grant_context, dict)
                    or set(grant_context) != {"identity", "snapshot", "requested"} or renewal_check is None):
                    raise RuntimeError("original pre-capture grant ambiguity unavailable")
                before, identity = grant_context["snapshot"], grant_context["identity"]
                self.check(before)
                run = before["run"]
                binding = {"run_id": self.original["run"]["run_id"], "service_incarnation": self.original["service"],
                           "host_incarnation": self.original["run"]["backend_incarnation"],
                           "controller_generation": self.client.generation,
                           "connection_generation": self.client.connection,
                           "intent_revision": run.get("intent_revision")}
                remaining, requested = run.get("remaining_s"), grant_context["requested"]
                if (not isinstance(identity, dict) or set(identity) != set(binding)
                    or any(type(identity[k]) is not type(v) or identity[k] != v for k, v in binding.items())
                    or type(identity["intent_revision"]) is not int or identity["intent_revision"] < 0
                    or run.get("status") != "running" or run.get("recovery") is not None
                    or any(type(value) not in (int, float) or not math.isfinite(value)
                           for value in (remaining, requested))
                    or not 0 < remaining <= run["deadline"] - run["native_live_at"]
                    or not 0 <= requested <= time.monotonic()):
                    raise RuntimeError("original running grant identity/clock unavailable")
                clock["ceiling"] = min(requested + remaining, wall_deadline if wall_deadline is not None else float("inf"))
                clock["native_ceiling"] = requested + remaining
                clock["held_view_ceiling"] = min(requested + 8, clock["ceiling"])
                def remaining_budget():
                    renewal_check()
                    value = min(clock["ceiling"], clock["held_view_ceiling"]) - time.monotonic()
                    if value <= 0:
                        raise TimeoutError("original grant rejoin ceiling exhausted")
                    return value
                self.record({"event": "leader_grant_ambiguous", "identity": identity,
                             "outcome": "unknown_discarded_before_capture", "remaining_s": remaining_budget()})
                await self._attach_transport(before, identity, remaining_budget, renewal_check)
            except (Exception, asyncio.CancelledError) as error:
                raise grant_error from error
        while True:
            try:
                try:
                    recovered = await self._resume_once(
                        wall_deadline=clock["ceiling"], require_episode=require_episode,
                        refusal_identity=refusal_identity, expected_pause=expected_pause,
                        renewal_check=renewal_check, clock=clock, interrupted=interrupted,
                        grant_context=grant_context, pending_recovery=pending_recovery, resume_context=resume_context)
                except _NativeRecoveryPendingError as outcome:
                    first_transport_error = first_transport_error or outcome.original_error
                    if outcome.episode is not None:
                        deferred.append(outcome.episode)
                    clock["ceiling"] = min(clock["ceiling"], outcome.ceiling)
                    if hasattr(outcome, "held_view_ceiling"):
                        clock["held_view_ceiling"] = min(clock.get("held_view_ceiling", float("inf")),
                                                        outcome.held_view_ceiling)
                        clock["ceiling"] = min(clock["ceiling"], clock["held_view_ceiling"])
                    pending_recovery, interrupted, grant_context, resume_context = outcome, None, None, None
                    require_episode, refusal_identity, expected_pause = False, None, None
                    self.record({"event": "native_recovery_handoff", "original_recovery": outcome.recovery,
                                 "identity": outcome.context["identity"], "local_ceiling_monotonic_s": outcome.ceiling})
                    continue
                except _ResumePausedError as outcome:
                    first_transport_error = first_transport_error or getattr(outcome, "transport_error", None)
                    if outcome.episode is not None:
                        deferred.append(outcome.episode)
                    interrupted = outcome
                    grant_context, pending_recovery, resume_context = None, None, None
                    require_episode, refusal_identity, expected_pause = False, None, outcome.event["kind"]
                    self.record({"event": "accepted_resume_superseded", "identity": outcome.identity,
                                 "pause_event": outcome.event, "remaining_s": clock["ceiling"] - time.monotonic()})
                    continue
                except (ConnectionError, TimeoutError) as original:
                    context = getattr(original, "resume_result_context", None)
                    if not isinstance(context, dict) or renewal_check is None:
                        raise
                    first_transport_error = first_transport_error or original
                    before, identity = context["snapshot"], context["identity"]
                    self.check(before)
                    actual = {"run_id": self.original["run"]["run_id"], "service_incarnation": self.original["service"],
                              "host_incarnation": self.original["run"]["backend_incarnation"],
                              "controller_generation": self.client.generation, "connection_generation": self.client.connection,
                              "intent_revision": before["run"].get("intent_revision")}
                    known, close = context["connect_identity"], context["transport_close"]
                    held = context.get("phase") == "held_qualification"
                    reply_timeout = (type(original) is TimeoutError and not held
                        and isinstance(context.get("reply_timeout"), dict)
                        and context["reply_timeout"] == getattr(original, "resume_reply_timeout", None)
                        and context["reply_timeout"].get("operation_id") == context["operation_id"])
                    send_failure = (not held and context.get("send_failure") is not None
                        and context["send_failure"] == _resume_send_failure(
                            original, identity, context["operation_id"], getattr(self.client, "ws", None)))
                    if isinstance(original, TimeoutError) and (not reply_timeout or resume_context is not None):
                        raise original
                    phase_key = ((identity["connection_generation"], identity["intent_revision"], "held")
                                 if held else context["operation_id"])
                    event, remaining, ceiling = context["prior_event"], before["run"].get("remaining_s"), context["phase_ceiling"]
                    if (set(identity) != set(actual) or any(type(identity[k]) is not type(v) or identity[k] != v for k, v in actual.items())
                        or type(identity["intent_revision"]) is not int or identity["intent_revision"] < 1
                        or any(type(v) is not str or not v for k, v in identity.items() if k != "intent_revision")
                        or not isinstance(known, dict) or set(known) != set(identity)
                        or any(type(known[k]) is not type(v) or known[k] != v for k, v in identity.items()
                               if k not in {"intent_revision", "connection_generation"})
                        or known.get("intent_revision") != identity["intent_revision"] - 1
                        or str(UUID(identity["connection_generation"])) != identity["connection_generation"]
                        or (context["operation_id"] is not None if held else str(UUID(context["operation_id"])) != context["operation_id"])
                        or phase_key in consumed_resumes
                        or before["run"].get("status") != "paused" or before["run"].get("recovery") is not None
                        or _latest_run_event(before, identity) != event
                        or event.get("kind") not in {"input_expired", "native_input_expired", "required_view_expired", "live_sensing_requalified"}
                        or any(type(v) not in (int, float) or not math.isfinite(v)
                               for v in (remaining, ceiling, context["requested"], context["pi_lower_bound"], event.get("at")))
                        or not 0 < remaining <= clock["remaining"]
                        or not 0 <= context["requested"] <= time.monotonic()
                        or not ceiling <= context["requested"] + 8
                        or not before["run"]["native_live_at"] <= event["at"] <= context["pi_lower_bound"] < before["run"]["deadline"]
                        or not send_failure and (close != {} if reply_timeout else close != {
                            "expected": False, "close_code": 1006, "reason": "stream-ended", "reader_error": None,
                            "exception_type": "ClientConnectionResetError", "exception": "Cannot write to closing transport"})):
                        raise original
                    first_transport_error = first_transport_error or original
                    clock["ceiling"] = min(clock["ceiling"], getattr(original, "resume_original_ceiling", float("inf")))
                    clock["held_view_ceiling"] = min(clock.get("held_view_ceiling", float("inf")), ceiling)
                    def remaining_budget():
                        renewal_check()
                        budget = min(clock["ceiling"], clock["held_view_ceiling"]) - time.monotonic()
                        if budget <= 0:
                            raise TimeoutError("original unknown Resume qualification ceiling exhausted")
                        return budget
                    consumed_resumes.add(phase_key)
                    await self._attach_transport(before, identity, remaining_budget, renewal_check)
                    resume_context = context | {"pending_episode": getattr(original, "resume_pending_episode", None)}
                    grant_context, pending_recovery, interrupted = None, None, None
                    require_episode, refusal_identity, expected_pause = False, None, None
                    continue
                for episode in deferred:
                    self.record({"event": "qualified_native_recovery", "original_recovery": episode,
                                 "snapshot": self.client.snapshot})
                if (self.client.snapshot["run"].get("status") == "running"
                    and self.client.snapshot.get("evidence", {}).get("active_acknowledged") is True):
                    self.completed_episodes = self.episodes
                return recovered or bool(deferred)

            except (Exception, asyncio.CancelledError) as error:
                if first_transport_error is not None and error is not first_transport_error:
                    raise first_transport_error from error
                raise

    async def _attach_transport(self, state, identity, remaining, renewal_check):
        attachment_attempt = 0
        while True:
            if renewal_check:
                renewal_check()
            budget = remaining()
            await asyncio.wait_for(self.client.detach(), budget)
            budget = remaining()
            attachment_attempt += 1
            try:
                await asyncio.wait_for(self.client.attach(), budget)
            except ConnectionError as interrupted_attach:
                failed_close = getattr(interrupted_attach, "initial_snapshot_transport_close", None)
                if (not isinstance(failed_close, dict) or failed_close.get("expected") is not False
                    or failed_close.get("reason") != "stream-ended" or failed_close.get("close_code") != 1006
                    or failed_close.get("reader_error") is not None):
                    raise
                self.check(state)
                if self.client.connection != identity["connection_generation"]:
                    raise RuntimeError("original connection changed during transport attachment") from interrupted_attach
                if renewal_check:
                    renewal_check()
                close_evidence = {key: failed_close[key] for key in
                                  ("expected", "reason", "close_code", "reader_error")}
                for key in ("exception_type", "exception"):
                    value = failed_close.get(key)
                    # 32 UTF8 bytes also bound JSON's escaped representation.
                    close_evidence[key] = (value.encode("utf-8", errors="replace")[:32].decode(
                        "utf-8", errors="ignore") if type(value) is str else None)
                stamp = failed_close.get("observed_at_monotonic_s")
                close_evidence["observed_at_monotonic_s"] = (stamp if type(stamp) in (int, float)
                    and math.isfinite(stamp) else None)
                self.record({"event": "ordinary_attachment_interrupted", "phase": "initial_snapshot",
                             "transport_close": close_evidence, "attachment_attempt": attachment_attempt,
                             "remaining_s": remaining()})
                continue  # Fresh ticket/stream only; no control command has been retried.
            break

    async def _wait_current_hold(self, state, qualify, remaining, renewal_check, view_refusal=None):
        # Shared non-actuating qualification for an uncertain binding/Resume.
        # Only stable-HOLD alignment and canonical selected-view expiry may wait;
        # each authenticated read must retain all other current Native/owner proof.
        waiting_view, pending_stamp = False, None
        while True:
            if renewal_check:
                renewal_check()
            aligned = qualify(state)
            stamp = state["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"]
            if not aligned and pending_stamp is None:
                pending_stamp = stamp
            if aligned and pending_stamp is not None and stamp <= pending_stamp:
                raise RuntimeError("current HOLD alignment lacks a newer Native ACK")
            budget = remaining()
            view = _live(state).get("required_view") or {}
            admitted = _view_admitted(state)
            if admitted and not waiting_view:
                if aligned:
                    return state
            else:
                if (set(view) != {"required", "qualified", "role", "cause"}
                    or view.get("required") is not True or type(view.get("qualified")) is not bool
                    or view.get("role") not in {"forward", "backward", "chest", "wrist_left", "wrist_right"}
                    or view.get("cause") != (None if view["qualified"] else "required_view_expired")):
                    if view_refusal:
                        raise view_refusal(state)
                    raise RuntimeError("current selected view deficit is unavailable or malformed")
                if view["qualified"] and aligned:
                    return state
                waiting_view = waiting_view or not view["qualified"]
            await asyncio.sleep(min(.05, budget))
            requested = time.monotonic()
            budget = remaining()
            if renewal_check:
                renewal_check()
            state = (await asyncio.wait_for(self.client.get("state"), budget))["snapshot"]
            self.client.snapshot = state
            self.record({"event": "owner_rejoin_state", "snapshot": state,
                         "request_started_at_monotonic_s": requested,
                         "response_observed_at_monotonic_s": time.monotonic()})

    async def _resume_once(
        self, *, wall_deadline, require_episode, refusal_identity, expected_pause,
        renewal_check, clock, interrupted, grant_context=None, pending_recovery=None, resume_context=None,
    ):
        # This is the existing authenticated owner read, not a WSS freshness
        # guess. Anchor BEFORE the read so HTTP time cannot extend the ceiling.
        mailbox_refusal = expected_pause == "native_input_expired" and refusal_identity is not None
        view_refusal = expected_pause == "required_view_expired" and refusal_identity is not None
        bound_refusal = mailbox_refusal or view_refusal
        if bound_refusal:
            binding = {"run_id": self.original["run"]["run_id"], "service_incarnation": self.original["service"],
                       "controller_generation": self.original["controller"]["controller_generation"],
                       "connection_generation": self.client.connection,
                       "host_incarnation": self.original["run"]["backend_incarnation"]}
            if (
                not isinstance(refusal_identity, dict)
                or set(refusal_identity) != {*binding, "intent_revision"}
                or any(type(refusal_identity[k]) is not type(v) or refusal_identity[k] != v
                       for k, v in binding.items())
                or type(refusal_identity["intent_revision"]) is not int
                or refusal_identity["intent_revision"] < 0
            ):
                raise RuntimeError("original mailbox input refusal identity changed")
        requested = time.monotonic()
        if view_refusal:
            clock["held_view_ceiling"] = min(clock.get("held_view_ceiling", float("inf")), requested + 8,
                                              wall_deadline if wall_deadline is not None else float("inf"))
        request_ceiling = min(wall_deadline if wall_deadline is not None else float("inf"),
                              clock.get("held_view_ceiling", float("inf")))
        state = (
            await asyncio.wait_for(
                self.client.get("state"), None if math.isinf(request_ceiling) else max(0, request_ceiling - requested)
            )
        )["snapshot"]
        self.client.snapshot = state
        native_remaining = (state.get("run") or {}).get("remaining_s")
        if not clock["anchored"]:
            if (type(native_remaining) not in (int, float) or not math.isfinite(native_remaining)
                or not 0 < native_remaining <= self.original["run"]["deadline"] - self.original["run"]["native_live_at"]):
                raise RuntimeError("original native rejoin clock unavailable")
            clock["ceiling"] = min(requested + native_remaining,
                                   wall_deadline if wall_deadline is not None else float("inf"))
            clock["anchored"] = True
            clock["remaining"] = native_remaining
        elif (type(native_remaining) not in (int, float) or not math.isfinite(native_remaining)
              or not 0 < native_remaining <= clock["remaining"]):
            raise RuntimeError("original native rejoin remaining clock increased or became invalid")
        clock["remaining"] = native_remaining
        clock["native_ceiling"] = min(clock.get("native_ceiling", float("inf")), requested + native_remaining)
        wall_deadline = clock["ceiling"]
        self.record(
            {
                "event": "owner_rejoin_state",
                "snapshot": state,
                "request_started_at_monotonic_s": requested,
                "response_observed_at_monotonic_s": time.monotonic(),
            }
        )
        self.check(state, history=False)
        if view_refusal:
            while state["run"]["status"] == "running":
                self.check(state)
                current, evidence = state["run"], state.get("evidence", {})
                if (self.client.connection != refusal_identity["connection_generation"]
                    or current.get("intent_revision") != refusal_identity["intent_revision"]
                    or not all(evidence.get(key) is True for key in ("feedback", "required_observation", "native_ack"))
                    or _latest_run_event(state, refusal_identity).get("kind") in {"pause", "stop", "handoff", "released", "revoked"}):
                    raise RuntimeError("original selected-view refusal binding changed before ordinary HOLD")
                budget = min(clock["held_view_ceiling"], clock["ceiling"], clock["native_ceiling"]) - time.monotonic()
                if budget <= 0:
                    raise TimeoutError("original selected-view ordinary HOLD ceiling exhausted")
                if renewal_check:
                    renewal_check()
                await asyncio.sleep(min(.05, budget))
                read_at = time.monotonic()
                budget = min(clock["held_view_ceiling"], clock["ceiling"], clock["native_ceiling"]) - read_at
                if budget <= 0:
                    raise TimeoutError("original selected-view ordinary HOLD ceiling exhausted")
                state = (await asyncio.wait_for(self.client.get("state"), budget))["snapshot"]
                self.client.snapshot = state
                self.check(state)
                native_remaining = state["run"].get("remaining_s")
                if (type(native_remaining) not in (int, float) or not math.isfinite(native_remaining)
                    or not 0 < native_remaining <= clock["remaining"]):
                    raise RuntimeError("original selected-view native remaining clock changed")
                clock["remaining"] = native_remaining
                clock["native_ceiling"] = min(clock["native_ceiling"], read_at + native_remaining)
                self.record({"event": "owner_rejoin_state", "snapshot": state,
                             "request_started_at_monotonic_s": read_at,
                             "response_observed_at_monotonic_s": time.monotonic()})
        binding_context = grant_context or resume_context
        if binding_context is not None and native_remaining > binding_context["snapshot"]["run"]["remaining_s"]:
            raise RuntimeError("original grant native remaining clock increased")
        if interrupted is not None:
            identity = interrupted.identity
            actual = self.client.identities()
            self.check(state)
            if (set(actual) != set(identity)
                or any(type(actual[k]) is not type(v) or actual[k] != v
                       for k, v in identity.items() if k != "intent_revision")
                or state["run"].get("status") != "paused" or state["run"].get("recovery") is not None
                or type(state["run"].get("intent_revision")) is not int
                or state["run"]["intent_revision"] != identity["intent_revision"] + 2
                or _latest_run_event(state, identity) != interrupted.event):
                raise RuntimeError("fresh authority for superseding ordinary pause changed")
        if bound_refusal and (
            self.client.connection != refusal_identity["connection_generation"]
            or state["run"]["status"] != "paused"
            or type(state["run"].get("intent_revision")) is not int
            or state["run"]["intent_revision"] != refusal_identity["intent_revision"] + 1
        ):
            raise RuntimeError("original mailbox input refusal/current ordinary pause unavailable")
        recovered, local_ceiling, episode = False, wall_deadline, None
        retained = None
        if pending_recovery is not None:
            context = pending_recovery.context
            if self.client.connection != context["identity"]["connection_generation"]:
                raise RuntimeError("pending native recovery binding changed")
            retained = self._grant_episode(state, context, pending=pending_recovery.pending_episode)
            if retained != pending_recovery.recovery:
                raise RuntimeError("pending native recovery original source descriptor changed")
        if resume_context is not None:
            identity, run = resume_context["identity"], state["run"]
            if self.client.connection != identity["connection_generation"]:
                raise RuntimeError("original unknown Resume binding changed")
            if (not self.history_qualified(state) or run.get("status") == "recovering"
                or resume_context["pending_episode"] is not None
                and _latest_run_event(state, identity) != resume_context["prior_event"]):
                retained = self._grant_episode(state, resume_context, pending=resume_context["pending_episode"],
                                               resume_unknown=resume_context.get("phase") != "held_qualification")
            else:
                event = _latest_run_event(state, identity)
                same = run.get("intent_revision") == identity["intent_revision"] and event == resume_context["prior_event"]
                new_pause = (resume_context.get("phase") != "held_qualification"
                             and type(run.get("intent_revision")) is int and run["intent_revision"] == identity["intent_revision"] + 2
                             and set(event) == {"kind", "at", "run_id", "service_incarnation"}
                             and event.get("kind") in {"input_expired", "native_input_expired", "required_view_expired"}
                             and type(event.get("at")) in (int, float) and math.isfinite(event["at"])
                             and resume_context["prior_event"]["at"] < event["at"]
                             and resume_context["pi_lower_bound"] <= event["at"] < run["deadline"])
                if run.get("status") != "paused" or run.get("recovery") is not None or not (same or new_pause):
                    raise RuntimeError("fresh unknown Resume own paused phase unavailable")
        if grant_context is not None:
            if self.client.connection != grant_context["identity"]["connection_generation"]:
                raise RuntimeError("original ambiguous grant connection changed")
            if not self.history_qualified(state):
                retained = self._grant_episode(state, grant_context)
        if state["run"]["status"] != "recovering" and retained is None and (require_episode or expected_pause or grant_context is not None):
            run = state["run"]
            event = next(
                (
                    v
                    for v in reversed(state.get("events", []))
                    if v.get("run_id") == self.original["run"]["run_id"]
                    and v.get("service_incarnation") == self.original["service"]
                ),
                {},
            )
            allowed = {"required_view_expired", "input_expired", "native_input_expired"}
            if (
                run["status"] != "paused"
                or run.get("recovery") is not None
                or not self.history_qualified(state)
                or grant_context is not None and run.get("intent_revision") != grant_context["identity"]["intent_revision"] + 1
                or event.get("kind") not in allowed
                or expected_pause is not None
                and event.get("kind") != expected_pause
                or require_episode
                and (
                    not refusal_identity
                    or run["intent_revision"] != refusal_identity.get("intent_revision", -2) + 1
                )
            ):
                raise RuntimeError(
                    "original native recovery descriptor/current cause-specific pause unavailable"
                )
            self.pause_recoveries += 1
            self.record({"event": "cause_specific_hold", "cause": event["kind"], "snapshot": state})
        if state["run"]["status"] == "recovering" or retained is not None:
            run = state["run"]
            episode = retained if retained is not None else run.get("recovery")
            if (
                not isinstance(episode, dict)
                or set(episode) != {"started", "ceiling", "revision"}
                or any(
                    type(episode[k]) not in (int, float) or not math.isfinite(episode[k])
                    for k in ("started", "ceiling")
                )
                or type(episode["revision"]) is not int
                or episode["revision"] != run["intent_revision"] - (1 if run["status"] == "paused" else 0)
                or not episode["started"]
                < episode["ceiling"]
                <= min(episode["started"] + 10, run["deadline"])
                or run["recovery_episodes"] != self.episodes + (0 if (pending_recovery is not None and pending_recovery.pending_episode is not None
                    or resume_context is not None and resume_context["pending_episode"] is not None) else 1)
                or run.get("first_cause")
                not in {"feedback_loss", "native_active_ack_loss", "required_proof_loss"}
            ):
                raise RuntimeError("original native recovery descriptor unavailable/invalid")
            episode = dict(episode)
            remaining = run.get("remaining_s")
            if type(remaining) not in (int, float) or not math.isfinite(remaining):
                raise RuntimeError("original native recovery clock unavailable")
            pi_now = run["deadline"] - remaining
            if not episode["started"] <= pi_now < episode["ceiling"]:
                raise TimeoutError("original native recovery ceiling exhausted")
            local_ceiling = min(
                requested + episode["ceiling"] - pi_now,
                wall_deadline if wall_deadline is not None else float("inf"),
            )
            clock["ceiling"] = min(clock["ceiling"], local_ceiling)
            qualifying_ceiling = min(local_ceiling, clock.get("held_view_ceiling", float("inf")))
            cause, count, connection = run["first_cause"], run["recovery_episodes"], self.client.connection
            self.record(
                {
                    "event": "native_recovery_wait",
                    "original_recovery": episode,
                    "snapshot": state,
                    "local_ceiling_monotonic_s": local_ceiling,
                }
            )
            while True:
                self.check(state, history=False)
                current, evidence, live = state["run"], state.get("evidence", {}), _live(state)
                if (
                    self.client.connection != connection
                    or current.get("first_cause") != cause
                    or current["recovery_episodes"] != count
                ):
                    raise RuntimeError("original native recovery ownership/episode changed")
                if time.monotonic() >= qualifying_ceiling:
                    raise TimeoutError("original native recovery ceiling exhausted")
                if current["status"] == "paused":
                    event = next(
                        (
                            v
                            for v in reversed(state.get("events", []))
                            if v.get("run_id") == self.original["run"]["run_id"]
                            and v.get("service_incarnation") == self.original["service"]
                        ),
                        {},
                    )
                    qualified_event = (
                        event.get("kind") == "live_sensing_requalified"
                        and type(event.get("at")) in (int, float)
                        and episode["started"] <= event["at"] < episode["ceiling"]
                    )
                    revision = episode["revision"] + 1
                    if (
                        not qualified_event
                        or current.get("recovery") is not None
                        or current["intent_revision"] != revision
                    ):
                        raise RuntimeError("current native recovery qualification transition missing")
                    ack = evidence.get("accepted_host_action") or {}
                    if (
                        all(
                            evidence.get(k) is True
                            for k in (
                                "feedback",
                                "required_observation",
                                "hold_acknowledged",
                                "pose_aligned",
                                "native_ack",
                            )
                        )
                        and evidence.get("acknowledged_intent_revision") == revision
                        and ack.get("run_id") == self.original["run"]["run_id"]
                        and ack.get("host_incarnation") == self.original["run"]["backend_incarnation"]
                        and ack.get("intent_revision") == revision
                        and ack.get("state") == "paused"
                        and live.get("cursor_intent_revision") == revision
                        and _view_admitted(state)
                    ):
                        self.episodes, self.cause, recovered = count, cause, True
                        break
                elif current["status"] != "recovering" or current.get("recovery") != episode:
                    raise RuntimeError("original native recovery descriptor changed")
                await asyncio.sleep(min(0.05, max(0, qualifying_ceiling - time.monotonic())))
                state = (
                    await asyncio.wait_for(self.client.get("state"), max(0, qualifying_ceiling - time.monotonic()))
                )["snapshot"]
                self.client.snapshot = state
                self.record(
                    {
                        "event": "native_recovery_observed",
                        "snapshot": state,
                        "original_recovery": episode,
                        "local_ceiling_monotonic_s": local_ceiling,
                    }
                )
        self.check(state)  # A cleared descriptor alone never proves a closed episode.
        timeout = None if local_ceiling is None else max(0, local_ceiling - time.monotonic())
        if bound_refusal and self.client.connection != refusal_identity["connection_generation"]:
            raise RuntimeError("original mailbox input refusal connection changed before Resume")
        if renewal_check:
            renewal_check()
        qualification_context = None
        def checked(snapshot):
            nonlocal qualification_context
            if time.monotonic() >= local_ceiling:
                raise TimeoutError("original rejoin ceiling exhausted")
            if renewal_check:
                renewal_check()
            if view_refusal:
                event = _latest_run_event(snapshot, refusal_identity)
                if (event.get("kind") in {"pause", "stop", "handoff", "released", "revoked", "presence_expired"}
                    or snapshot["run"].get("status") == "paused"
                    and event == _latest_run_event(state, refusal_identity)
                    and snapshot["run"].get("intent_revision") != refusal_identity["intent_revision"] +
                        (1 if self.client.connection == refusal_identity["connection_generation"] else 2)):
                    raise RuntimeError("original selected-view ordinary HOLD intent superseded")
            try:
                self.check(snapshot)
            except RuntimeError as original:
                # Keep the offending current proof bounded and fault-only.
                # Reporting never replaces this original guard refusal.
                with contextlib.suppress(Exception):
                    identity = (qualification_context or {}).get("identity") or self.client.identities()
                    detail = {"event": "native_rejoin_qualification_changed", "guard": str(original)[:128],
                              "prior_qualified_count": self.episodes,
                              "current_count": snapshot["run"].get("recovery_episodes"),
                              "qualification": _paused_binding_diagnostic(
                                  state, snapshot, identity, _latest_run_event(state, identity), False,
                                  self.client.connection, native_remaining, local_ceiling)}
                    if len(json.dumps(detail, allow_nan=False).encode()) <= 8192:
                        self.record(detail)
                try:
                    self.check(snapshot, history=False)
                    same_pending = (recovered and episode is not None
                                    and self.completed_episodes < self.episodes
                                    and snapshot["run"].get("recovery_episodes") == self.episodes)
                    if (qualification_context is None or not same_pending and (
                        self.history_qualified(snapshot)
                        or snapshot["run"].get("recovery_episodes") != self.episodes + 1)):
                        raise original
                    pending = ({"recovery": (pending_recovery.pending_episode["recovery"]
                                             if pending_recovery is not None and pending_recovery.pending_episode is not None
                                             else episode), "count": self.episodes, "cause": self.cause}
                               if same_pending else None)
                    context = json.loads(json.dumps(qualification_context, allow_nan=False))
                    identity, actual = context["identity"], self.client.identities()
                    if (set(actual) != set(identity)
                        or any(type(actual[k]) is not type(v) or actual[k] != v for k, v in identity.items()
                               if k not in {"intent_revision", "connection_generation"})):
                        raise RuntimeError("native recovery current authority changed")
                    if self.client.connection != identity["connection_generation"]:
                        generation = self.client.connection
                        if (type(generation) is not str or str(UUID(generation)) != generation
                            or type(identity["intent_revision"]) is not int or self.client.sequence != 0):
                            raise RuntimeError("native recovery acknowledged connect binding unavailable")
                        # Only the known connect ACK moves this local binding;
                        # no ambiguous command or authority can be adopted.
                        identity["connection_generation"] = generation
                        identity["intent_revision"] += 1
                    recovery = self._grant_episode(snapshot, context, pending=pending)
                    own = [v for v in snapshot.get("events", []) if v.get("run_id") == identity["run_id"]
                           and v.get("service_incarnation") == identity["service_incarnation"]]
                    hold_index = max(i for i, v in enumerate(own) if v.get("kind") == "required_hold")
                    if hold_index < 1 or own[hold_index - 1] != _latest_run_event(context["snapshot"], identity):
                        raise RuntimeError("native recovery preceding own qualification event changed")
                    ceiling = min(local_ceiling, clock["native_ceiling"] - snapshot["run"]["deadline"] + recovery["ceiling"])
                    if time.monotonic() >= ceiling:
                        raise TimeoutError("original pending native recovery ceiling exhausted")
                except (Exception, asyncio.CancelledError) as refusal:
                    if refusal is original:
                        raise
                    raise original from refusal
                raise _NativeRecoveryPendingError(context, recovery, ceiling, original, pending) from original
            if snapshot["run"].get("status") == "paused" and snapshot["run"].get("recovery") is None:
                identities = getattr(self.client, "identities", None)
                # Stateless supported callers keep their old behavior; without
                # current binding proof they cannot enter this new handoff.
                qualification_context = (json.loads(json.dumps(
                    {"identity": identities(), "snapshot": snapshot}, allow_nan=False))
                    if callable(identities) else None)
        try:
            if binding_context is not None:
                identity = self.client.identities()
                event = json.loads(json.dumps(_latest_run_event(state, binding_context["identity"]), allow_nan=False))
                previous_remaining, previous_stamp = state["run"]["remaining_s"], None
                if resume_context is not None and resume_context.get("phase") == "held_qualification":
                    def current_held():
                        checked(self.client.snapshot)
                        current = self.client.snapshot
                        actual = self.client.identities()
                        if (set(actual) != set(identity) or any(type(actual[k]) is not type(v) or actual[k] != v for k, v in identity.items())
                            or current["run"].get("status") != "paused" or current["run"].get("recovery") is not None
                            or _latest_run_event(current, identity) != event):
                            raise RuntimeError("fresh held qualification authority changed")
                        evidence, live = current.get("evidence", {}), _live(current)
                        return (all(evidence.get(k) is True for k in
                                    ("feedback", "required_observation", "hold_acknowledged", "pose_aligned", "native_ack"))
                                and live.get("cursor_intent_revision") == current["run"]["intent_revision"]
                                and _view_admitted(current))
                    budget = min(local_ceiling, clock["held_view_ceiling"]) - time.monotonic()
                    if budget <= 0:
                        raise TimeoutError("original held qualification ceiling exhausted")
                    await self.client.wait(current_held, budget)
                    requested = time.monotonic()
                    budget = min(local_ceiling, clock["held_view_ceiling"]) - requested
                    if budget <= 0:
                        raise TimeoutError("original held qualification ceiling exhausted")
                    if renewal_check:
                        renewal_check()
                    state = (await asyncio.wait_for(self.client.get("state"), budget))["snapshot"]
                    self.client.snapshot = state
                    self.record({"event": "owner_rejoin_state", "snapshot": state,
                                 "request_started_at_monotonic_s": requested,
                                 "response_observed_at_monotonic_s": time.monotonic()})
                def qualify_current_hold(state):
                    nonlocal previous_remaining, previous_stamp
                    checked(state)
                    run, evidence, live = state["run"], state.get("evidence", {}), _live(state)
                    revision, ack = run.get("intent_revision"), evidence.get("accepted_host_action") or {}
                    stamp, remaining = ack.get("acquired_at_monotonic_s"), run.get("remaining_s")
                    actual = self.client.identities()
                    if (set(actual) != set(identity)
                        or any(type(actual[k]) is not type(v) or actual[k] != v for k, v in identity.items())
                        or _latest_run_event(state, identity) != event
                        or run.get("status") != "paused" or run.get("recovery") is not None
                        or type(revision) is not int
                        or not all(evidence.get(k) is True for k in
                                   ("feedback", "required_observation", "hold_acknowledged", "native_ack"))
                        or type(evidence.get("pose_aligned")) is not bool
                        or evidence.get("active_acknowledged") is not False
                        or type(evidence.get("acknowledged_intent_revision")) is not int
                        or evidence["acknowledged_intent_revision"] != revision
                        or ack.get("run_id") != run["run_id"] or ack.get("host_incarnation") != run["backend_incarnation"]
                        or ack.get("state") != "paused" or type(ack.get("intent_revision")) is not int
                        or ack["intent_revision"] != revision
                        or type(live.get("cursor_intent_revision")) is not int or live["cursor_intent_revision"] != revision
                        or any(type(value) not in (int, float) or not math.isfinite(value)
                               for value in (stamp, remaining, event.get("at")))
                        or not 0 < remaining <= previous_remaining
                        or previous_stamp is not None and stamp < previous_stamp
                        or not run["native_live_at"] <= event["at"] <= stamp
                        or stamp + remaining > run["deadline"]):
                        raise RuntimeError("fresh grant rejoin held/current-view proof unavailable")
                    clock["remaining"] = remaining
                    previous_remaining, previous_stamp = remaining, stamp
                    return evidence["pose_aligned"]
                def held_remaining():
                    budget = min(local_ceiling, clock["held_view_ceiling"]) - time.monotonic()
                    if budget <= 0:
                        raise TimeoutError("original grant held/view ceiling exhausted")
                    return budget
                state = await self._wait_current_hold(state, qualify_current_hold, held_remaining, renewal_check)
                checked(state)
                budget = min(local_ceiling, clock["held_view_ceiling"]) - time.monotonic()
                if budget <= 0:
                    raise TimeoutError("original grant rejoin ceiling exhausted")
                connected = await asyncio.wait_for(self.client.command(dict(op="connect", **identity)), budget)
                generation, replacement = connected.get("connection_generation"), connected.get("snapshot")
                self.check(replacement)
                if (type(generation) is not str or str(UUID(generation)) != generation
                    or generation == identity["connection_generation"]
                    or replacement["run"].get("status") != "paused"
                    or type(replacement["run"].get("intent_revision")) is not int
                    or replacement["run"]["intent_revision"] != state["run"]["intent_revision"] + 1
                    or replacement["run"].get("recovery") is not None
                    or _latest_run_event(replacement, identity) != _latest_run_event(state, identity)):
                    raise RuntimeError("new grant connection generation/revision ACK unavailable")
                self.client.connection, self.client.snapshot, self.client.sequence = generation, replacement, 0
                checked(replacement)
                self.record({"event": "leader_grant_connection_fenced", "connection_generation": generation,
                             "previous_connection_generation": identity["connection_generation"],
                             "intent_revision": replacement["run"]["intent_revision"]})
                budget = local_ceiling - time.monotonic()
                if budget <= 0:
                    raise TimeoutError("original grant rejoin ceiling exhausted")
                await asyncio.wait_for(SessionClient._resume_connected(
                    self.client, check=checked, connect_identity=identity, refusal_record=self.record,
                    qualification_ceiling=clock["held_view_ceiling"]), budget)
            else:
                options = ({"qualification_ceiling": clock["held_view_ceiling"]}
                           if "held_view_ceiling" in clock else {})
                await asyncio.wait_for(self.client.resume(check=checked, refusal_record=self.record, **options), timeout)
        except _NativeRecoveryPendingError as outcome:
            outcome.episode = episode if recovered and outcome.pending_episode is None else None
            raise
        except _ResumePausedError as outcome:
            self.check(self.client.snapshot)
            outcome.episode = episode if recovered else None
            if renewal_check:
                renewal_check()
            raise
        except (ConnectionError, TimeoutError) as original:
            if getattr(original, "resume_result_context", None) is not None:
                original.resume_original_ceiling = local_ceiling
                original.resume_pending_episode = ({"recovery": episode, "count": self.episodes, "cause": self.cause}
                                                   if recovered and self.completed_episodes < self.episodes else None)
                raise
            if "held_view_ceiling" in clock:
                raise  # A nested ambiguous phase never starts another replacement.
            accepted = getattr(original, "accepted_resume_context", None)
            if accepted is not None:
                try:
                    identity = accepted["identity"]
                    expected = {"run_id": self.original["run"]["run_id"], "service_incarnation": self.original["service"],
                                "host_incarnation": self.original["run"]["backend_incarnation"],
                                "controller_generation": self.original["controller"]["controller_generation"],
                                "connection_generation": self.client.connection,
                                "intent_revision": state["run"]["intent_revision"] + 1}
                    prior, lower = accepted["prior_event"], accepted["pi_lower_bound"]
                    if (recovered or state["run"].get("recovery") is not None
                        or not isinstance(identity, dict) or set(identity) != set(expected)
                        or any(type(identity[k]) is not type(v) or identity[k] != v for k, v in expected.items())
                        or type(identity["intent_revision"]) is not int
                        or any(type(v) is not str or not v for k, v in identity.items() if k != "intent_revision")
                        or str(UUID(accepted["operation_id"])) != accepted["operation_id"]
                        or set(prior) != {"kind", "at", "run_id", "service_incarnation"}
                        or prior != _latest_run_event(state, identity)
                        or prior.get("kind") not in {"input_expired", "native_input_expired", "required_view_expired"}
                        or any(type(v) not in (int, float) or not math.isfinite(v)
                               for v in (prior.get("at"), lower, accepted["native_remaining"], accepted["active_ceiling"]))
                        or not self.original["run"]["native_live_at"] <= prior["at"] < self.original["run"]["deadline"]
                        or not self.original["run"]["native_live_at"] <= lower < self.original["run"]["deadline"]
                        or prior["at"] + accepted["native_remaining"] > self.original["run"]["deadline"]
                        or not 0 < accepted["native_remaining"] <= native_remaining):
                        raise RuntimeError("known accepted ordinary Resume phase unavailable")
                    clock["ceiling"] = min(clock["ceiling"], accepted["active_ceiling"])
                    def remaining():
                        value = clock["ceiling"] - time.monotonic()
                        if value <= 0:
                            raise TimeoutError("original accepted Resume observation ceiling exhausted")
                        if renewal_check:
                            renewal_check()
                        return value
                    # Only fixed fields from this failed phase are emitted.
                    # Full owner snapshots and successful traffic stay unchanged.
                    self.record({"event": "accepted_resume_transport_closed", "phase": "active_ack",
                                 "identity": {k: (v.encode("utf-8")[:32].decode("utf-8", errors="ignore")
                                                   if type(v) is str else v) for k, v in identity.items()},
                                 "operation_id": accepted["operation_id"], "remaining_s": remaining(),
                                 "transport_close": {k: accepted["transport_close"].get(k) for k in
                                                     ("expected", "reason", "close_code", "exception_type", "reader_error")}})
                    await self._attach_transport(state, identity, remaining, renewal_check)
                    budget = remaining()
                    fresh = (await asyncio.wait_for(self.client.get("state"), budget))["snapshot"]
                    self.client.snapshot = fresh
                    self.check(fresh)
                    run, evidence, live = fresh["run"], fresh.get("evidence", {}), _live(fresh)
                    ack = evidence.get("accepted_host_action") or {}
                    stamp, current_remaining = ack.get("acquired_at_monotonic_s"), run.get("remaining_s")
                    revision = identity["intent_revision"] + 2
                    if (self.client.connection != identity["connection_generation"]
                        or run.get("status") != "paused" or run.get("recovery") is not None
                        or not all(evidence.get(k) is True for k in
                                   ("feedback", "required_observation", "hold_acknowledged", "pose_aligned", "native_ack"))
                        or evidence.get("active_acknowledged") is not False
                        or type(evidence.get("acknowledged_intent_revision")) is not int
                        or evidence["acknowledged_intent_revision"] != revision
                        or ack.get("run_id") != identity["run_id"] or ack.get("host_incarnation") != identity["host_incarnation"]
                        or ack.get("state") != "paused" or type(ack.get("intent_revision")) is not int
                        or ack["intent_revision"] != revision
                        or type(live.get("cursor_intent_revision")) is not int or live["cursor_intent_revision"] != revision
                        or not _view_admitted(fresh)
                        or any(type(v) not in (int, float) or not math.isfinite(v) for v in (stamp, current_remaining))
                        or not 0 < current_remaining <= accepted["native_remaining"]
                        or not run["deadline"] <= stamp + accepted["native_remaining"]
                        or stamp + current_remaining > run["deadline"]):
                        raise RuntimeError("fresh accepted Resume ordinary paused proof unavailable")
                    remaining()
                    try:
                        SessionClient._resume_active(self.client, identity, prior, lower, accepted["receipt"], checked)
                    except _ResumePausedError as outcome:
                        if outcome.event["at"] > stamp:
                            raise RuntimeError("current held ACK predates the new ordinary pause") from outcome
                        outcome.transport_error = original
                        raise
                    raise RuntimeError("fresh accepted Resume did not prove a new ordinary pause")
                except _ResumePausedError:
                    raise
                except (Exception, asyncio.CancelledError) as qualification_error:
                    raise original from qualification_error
            release = getattr(original, "empty_release_context", None)
            release_loss = isinstance(release, dict) and set(release) == {"identity", "connect_identity", "snapshot"}
            close = dict(getattr(self.client, "transport_close", None) or {})
            identity = release["identity"] if release_loss else getattr(original, "connect_identity", None)
            expected = dict(
                run_id=self.original["run"]["run_id"], service_incarnation=self.original["service"],
                host_incarnation=self.original["run"]["backend_incarnation"],
                controller_generation=self.original["controller"]["controller_generation"],
                connection_generation=self.client.connection,
                intent_revision=state["run"]["intent_revision"] + (1 if release_loss else 0),
            )
            if (
                recovered
                or close.get("reader_error") is not None
                or not release_loss and (close.get("expected") is not False or close.get("reason") != "stream-ended"
                                         or close.get("close_code") != 1006)
                or not isinstance(identity, dict) or set(identity) != set(expected)
                or any(type(identity[k]) is not type(v) or identity[k] != v for k, v in expected.items())
                or type(identity["intent_revision"]) is not int
            ):
                raise
            self.connect_rebindings += 1
            try:
                if (
                    type(self.original["run"].get("operation_id")) is not str
                    or not self.original["run"]["operation_id"]
                    or any(type(identity[k]) is not str or not identity[k] for k in identity if k != "intent_revision")
                    or type(native_remaining) not in (int, float) or not math.isfinite(native_remaining)
                    or not 0 < native_remaining <= self.original["run"]["deadline"] - self.original["run"]["native_live_at"]
                ):
                    raise RuntimeError("original native rejoin clock/operation unavailable")
                def pause_event(snapshot):
                    return next((event for event in reversed(snapshot.get("events", []))
                                 if event.get("run_id") == self.original["run"]["run_id"]
                                 and event.get("service_incarnation") == self.original["service"]), {})
                original_event = pause_event(state)
                if release_loss:
                    before = release["connect_identity"]
                    if not isinstance(before, dict):
                        raise RuntimeError("known accepted connect identity unavailable")
                    expected_before = dict(expected, connection_generation=before.get("connection_generation"),
                                           intent_revision=state["run"]["intent_revision"])
                    if (not isinstance(before, dict) or set(before) != set(expected_before)
                        or any(type(before[k]) is not type(v) or before[k] != v for k, v in expected_before.items())
                        or type(before["connection_generation"]) is not str or not before["connection_generation"]
                        or before["connection_generation"] == identity["connection_generation"]):
                        raise RuntimeError("known accepted connect identity unavailable")
                    self.check(release["snapshot"])
                    if (release["snapshot"]["run"].get("status") != "paused"
                        or type(release["snapshot"]["run"].get("intent_revision")) is not int
                        or release["snapshot"]["run"]["intent_revision"] != identity["intent_revision"]
                        or pause_event(release["snapshot"]) != original_event):
                        raise RuntimeError("known accepted connect revision/pause changed")
                if (
                    state["run"].get("status") != "paused" or state["run"].get("recovery") is not None
                    or set(original_event) != {"kind", "at", "run_id", "service_incarnation"}
                    or original_event.get("kind") not in {"input_expired", "native_input_expired", "required_view_expired"}
                    or expected_pause is not None and original_event["kind"] != expected_pause
                    or type(original_event.get("at")) not in (int, float) or not math.isfinite(original_event["at"])
                    or not self.original["run"]["native_live_at"] <= original_event["at"] < self.original["run"]["deadline"]
                ):
                    raise RuntimeError("original cause-specific ordinary pause unavailable")
                # Anchor before the FIRST authenticated read; its latency and
                # all subsequent replacement work consume this same ceiling.
                ceiling = min(requested + native_remaining,
                              local_ceiling if local_ceiling is not None else float("inf"))
                clock["held_view_ceiling"] = min(clock.get("held_view_ceiling", float("inf")), requested + 8, ceiling)
                ceiling = min(ceiling, clock["held_view_ceiling"])
                def remaining():
                    value = ceiling - time.monotonic()
                    if value <= 0:
                        raise TimeoutError("original rejoin ceiling exhausted")
                    return value
                if renewal_check:
                    renewal_check()
                self.record({"event": "ordinary_empty_release_ambiguous" if release_loss else "ordinary_connect_transport_closed", "transport_close": close,
                             "identity": identity, "pause_event": original_event,
                             "remaining_s": remaining(), "outcome": "ambiguous"})
                await self._attach_transport(state, identity, remaining, renewal_check)
                budget = remaining()
                fresh_requested = time.monotonic()
                fresh = (await asyncio.wait_for(self.client.get("state"), budget))["snapshot"]
                self.client.snapshot = fresh
                self.record({"event": "owner_rejoin_state", "snapshot": fresh,
                             "request_started_at_monotonic_s": fresh_requested,
                             "response_observed_at_monotonic_s": time.monotonic()})
                previous_remaining = native_remaining
                previous_stamp = (state.get("evidence", {}).get("accepted_host_action") or {}).get("acquired_at_monotonic_s")
                previous_revision = None
                def unavailable(snapshot):
                    error = RuntimeError("current ordinary paused binding/held feedback unavailable")
                    # Diagnostics never replace the original guard refusal.
                    with contextlib.suppress(Exception):
                        error.qualification = _paused_binding_diagnostic(
                            state, snapshot, identity, original_event, release_loss,
                            self.client.connection, native_remaining, ceiling)
                    return error
                def qualify_current_hold(snapshot):
                    nonlocal previous_remaining, previous_stamp, previous_revision
                    self.check(snapshot)
                    run, evidence, live = snapshot["run"], snapshot.get("evidence", {}), _live(snapshot)
                    revision = run.get("intent_revision")
                    ack = evidence.get("accepted_host_action") or {}
                    stamp, current_remaining = ack.get("acquired_at_monotonic_s"), run.get("remaining_s")
                    if (
                        run.get("status") != "paused" or run.get("recovery") is not None
                        or pause_event(snapshot) != original_event
                        or type(revision) is not int or revision not in ((identity["intent_revision"],) if release_loss else
                                                                      (identity["intent_revision"], identity["intent_revision"] + 1))
                        or previous_revision is not None and revision != previous_revision
                        or self.client.connection != identity["connection_generation"]
                        or not all(evidence.get(k) is True for k in
                                   ("feedback", "required_observation", "hold_acknowledged", "native_ack"))
                        or type(evidence.get("pose_aligned")) is not bool
                        or evidence.get("active_acknowledged") is not False
                        or ack.get("run_id") != run["run_id"]
                        or ack.get("host_incarnation") != run["backend_incarnation"]
                        or ack.get("state") != "paused" or type(ack.get("intent_revision")) is not int
                        or ack["intent_revision"] != revision
                        or type(evidence.get("acknowledged_intent_revision")) is not int
                        or evidence["acknowledged_intent_revision"] != revision
                        or type(live.get("cursor_intent_revision")) is not int or live["cursor_intent_revision"] != revision
                        or any(type(v) not in (int, float) or not math.isfinite(v)
                               for v in (stamp, previous_stamp, current_remaining))
                        or not 0 < current_remaining <= previous_remaining
                        or not previous_stamp <= stamp <= run["deadline"] - current_remaining
                    ):
                        raise unavailable(snapshot)
                    previous_remaining, previous_stamp, previous_revision = current_remaining, stamp, revision
                    clock["remaining"] = current_remaining
                    return evidence["pose_aligned"]
                fresh = await self._wait_current_hold(fresh, qualify_current_hold, remaining, renewal_check, unavailable)
                revision = fresh["run"]["intent_revision"]
                stamp = fresh["evidence"]["accepted_host_action"]["acquired_at_monotonic_s"]
                if renewal_check:
                    renewal_check()
                remaining()
                self.record({"event": "ordinary_empty_release_requalified" if release_loss else "ordinary_connect_requalified", "intent_revision": revision,
                             "remaining_s": remaining()})
                # One deliberate NEW binding, after current held qualification.
                # The old command/result and every acquired input stay discarded.
                budget = remaining()
                if release_loss:
                    # A NEW acknowledged generation fences an old release that
                    # may still be queued in Gateway/IPC. Never replay its ID.
                    connected = await asyncio.wait_for(
                        self.client.command(dict(op="connect", **self.client.identities())), budget)
                    generation = connected.get("connection_generation")
                    replacement = connected.get("snapshot")
                    self.check(replacement)
                    if (type(generation) is not str or not generation or generation == identity["connection_generation"]
                        or type(replacement["run"].get("intent_revision")) is not int
                        or replacement["run"]["intent_revision"] != revision + 1
                        or replacement["run"].get("status") != "paused"
                        or pause_event(replacement) != original_event):
                        raise RuntimeError("replacement connect generation/revision ACK unavailable")
                    self.client.connection, self.client.snapshot, self.client.sequence = generation, replacement, 0
                    self.record({"event": "ordinary_empty_release_fenced", "connection_generation": generation,
                                 "previous_connection_generation": identity["connection_generation"],
                                 "intent_revision": revision + 1, "remaining_s": remaining()})
                    held_stamp = stamp
                    if renewal_check:
                        renewal_check()
                    def current_held(snapshot):
                        self.check(snapshot)
                        run, evidence, live = snapshot["run"], snapshot.get("evidence", {}), _live(snapshot)
                        ack = evidence.get("accepted_host_action") or {}
                        stamp, current_remaining = ack.get("acquired_at_monotonic_s"), run.get("remaining_s")
                        if (self.client.connection != generation or pause_event(snapshot) != original_event
                            or type(run.get("intent_revision")) is not int or run["intent_revision"] != revision + 1
                            or run.get("status") != "paused" or run.get("recovery") is not None):
                            raise RuntimeError("replacement current authority changed")
                        return (evidence.get("active_acknowledged") is False
                                and type(evidence.get("acknowledged_intent_revision")) is int
                                and evidence["acknowledged_intent_revision"] == revision + 1
                                and ack.get("run_id") == run["run_id"]
                                and ack.get("host_incarnation") == run["backend_incarnation"]
                                and ack.get("state") == "paused" and type(ack.get("intent_revision")) is int
                                and ack["intent_revision"] == revision + 1
                                and type(live.get("cursor_intent_revision")) is int
                                and live["cursor_intent_revision"] == revision + 1
                                and all(type(v) in (int, float) and math.isfinite(v) for v in (stamp, current_remaining))
                                and 0 < current_remaining <= native_remaining
                                and held_stamp <= stamp <= run["deadline"] - current_remaining)
                    budget = remaining()
                    await asyncio.wait_for(SessionClient._resume_connected(
                        self.client, check=checked, qualified_check=current_held, refusal_record=self.record,
                        qualification_ceiling=clock["held_view_ceiling"]), budget)
                else:
                    await asyncio.wait_for(self.client.resume(
                        check=checked, refusal_record=self.record, qualification_ceiling=clock["held_view_ceiling"]), budget)
                if renewal_check:
                    renewal_check()
                remaining()
            except Exception as qualification_error:
                try:
                    failure = {"event": "ordinary_empty_release_requalification_failed" if release_loss else "ordinary_connect_requalification_failed",
                               "error_type": type(qualification_error).__name__, "message": str(qualification_error)}
                    detail = getattr(qualification_error, "qualification", None)
                    if isinstance(detail, dict):
                        failure["qualification"] = detail
                    self.record(failure)
                finally:
                    raise original from qualification_error
        self.check(self.client.snapshot)
        if renewal_check:
            renewal_check()
        if recovered:
            self.record(
                {
                    "event": "qualified_native_recovery",
                    "original_recovery": (pending_recovery.pending_episode["recovery"]
                                          if pending_recovery is not None and pending_recovery.pending_episode is not None
                                          else resume_context["pending_episode"]["recovery"]
                                          if resume_context is not None and resume_context["pending_episode"] is not None
                                          else episode),
                    "snapshot": self.client.snapshot,
                }
            )
        return recovered


def native_frame_ack(snapshot, sequence, source, *, body_zero=False):
    live, evidence = _live(snapshot), snapshot.get("evidence", {})
    actual = live.get("source") or {}
    if (
        live.get("last_input_seq") != sequence
        or any(actual.get(k) != source.get(k) for k in ("source_id", "sample_seq", "grant_id"))
        or not all(evidence.get(k) is True for k in ("active_acknowledged", "feedback", "native_ack"))
    ):
        return False
    if body_zero:
        body, measured = live.get("body") or {}, evidence.get("measured_body") or {}
        return (
            set(body) == set(ZERO_BODY)
            and all(body[k] == 0 for k in ZERO_BODY)
            and all(type(measured.get(k)) in (int, float) and measured[k] == 0 for k in ZERO_BODY)
        )
    return True


async def send_leader_sample(client, producer, body, record, *, phase, progress_s):
    """One original grant/acquisition/send. A refused sample is never resent."""
    identity = client.identities()
    grant_started = time.perf_counter()
    requested, grant_context = time.monotonic(), None
    snapshot = getattr(client, "snapshot", None)
    if isinstance(snapshot, dict):
        # Stateless sender callers keep their original API. Recovery is eligible
        # only when this original immutable owner snapshot was available.
        with contextlib.suppress(TypeError, ValueError):
            grant_context = {"identity": dict(identity), "snapshot": json.loads(json.dumps(snapshot, allow_nan=False)),
                             "requested": requested}
    try:
        grant = await client.command(dict(op="grant", **identity))
    except CommandRefusedError as error:
        error.operation = "grant"
        error.identity = dict(identity)
        record(
            {
                "event": "leader_grant_rejected",
                "identity": identity,
                "phase": phase,
                "progress_s": progress_s,
                "receipt": error.response,
                "grant_timing": error.timing,
                "grant_request_started_at_monotonic_s": grant_started,
            }
        )
        raise
    except (TimeoutError, ConnectionError) as error:
        if grant_context is not None:
            error.grant_context = grant_context
        raise
    received = time.perf_counter()
    frame = await asyncio.to_thread(producer.capture, grant, body=body, grant_received_at=received)
    frame = producer.serialize(frame)
    client.sequence += 1
    attempt = {
        "identity": identity,
        "network_seq": client.sequence,
        "grant": grant,
        "frame": frame,
        "phase": phase,
        "progress_s": progress_s,
        "raw_leaders": getattr(producer.input, "last_raw", None),
        "command_clamps": producer.clutch.last_clamps,
        "clutch": producer.last_clutch,
        "timing": {
            "grant_request_started_at_monotonic_s": grant_started,
            "grant_received_at_monotonic_s": received,
            "serialized_at_monotonic_s": time.perf_counter(),
        },
    }
    if isinstance(producer.input, SelectedLeftWorkload):
        attempt.update(producer.input.cycle_metadata())
    record({"event": "leader_frame_attempt", **attempt})
    current_identity = client.identities()
    if current_identity != identity:
        original = grant_context["snapshot"] if grant_context is not None else {}
        current = getattr(client, "snapshot", None) or {}
        before, after = original.get("run") or {}, current.get("run") or {}
        old_live, new_live = _live(original), _live(current)
        previous_event, pause = (
            next((event for event in reversed(snapshot.get("events", []))
                  if event.get("run_id") == identity.get("run_id")
                  and event.get("service_incarnation") == identity.get("service_incarnation")), {})
            for snapshot in (original, current)
        )
        revision = identity.get("intent_revision")
        remaining = (before.get("remaining_s"), after.get("remaining_s"))
        clocks = (*remaining, before.get("native_live_at"), before.get("deadline"), pause.get("at"), previous_event.get("at"))
        run_pins = ("run_id", "operation_id", "backend_incarnation", "native_live_at", "deadline",
                    "first_cause", "native_first_cause")
        live_pins = ("reference", "workspace", "live_admitted_at_monotonic_s", "live_deadline_monotonic_s")
        ordinary_pause = (
            set(identity) == {"run_id", "service_incarnation", "controller_generation", "connection_generation",
                              "host_incarnation", "intent_revision"}
            and set(current_identity) == set(identity)
            and type(revision) is int and type(current_identity.get("intent_revision")) is int
            and current_identity["intent_revision"] == revision + 1
            and all(type(identity[key]) is str and identity[key]
                    and current_identity[key] == identity[key] for key in identity if key != "intent_revision")
            and before.get("status") == "running" and after.get("status") == "paused"
            and before.get("intent_revision") == revision and after.get("intent_revision") == revision + 1
            and all(key in before and key in after and before[key] == after[key] for key in run_pins)
            and type(before.get("operation_id")) is str and bool(before["operation_id"])
            and all(run.get("uncertain", True) is False and run.get("recovery") is None
                    and run.get("native_first_cause") is None for run in (before, after))
            and type(before.get("recovery_episodes")) is int and 0 <= before["recovery_episodes"] <= 3
            and type(after.get("recovery_episodes")) is int
            and after["recovery_episodes"] == before["recovery_episodes"]
            and all(snapshot.get("service_incarnation") == identity["service_incarnation"]
                    and snapshot.get("evidence", {}).get("fault") is None
                    and (snapshot.get("controller") or {}).get("controller_generation") == identity["controller_generation"]
                    for snapshot in (original, current))
            and (original.get("controller") or {}).get("device_id") == getattr(client, "device", None)
            and (current.get("controller") or {}).get("device_id") == getattr(client, "device", None)
            and before.get("run_id") == identity["run_id"] and before.get("backend_incarnation") == identity["host_incarnation"]
            and all(key in old_live and key in new_live and old_live[key] == new_live[key] for key in live_pins)
            and isinstance(old_live.get("reference"), dict) and isinstance(old_live.get("workspace"), dict)
            and old_live.get("live_admitted_at_monotonic_s") == before.get("native_live_at")
            and old_live.get("live_deadline_monotonic_s") == before.get("deadline")
            and pause.get("kind") in {"input_expired", "native_input_expired", "required_view_expired"}
            and all(type(value) in (int, float) and math.isfinite(value) for value in clocks)
            and 0 < remaining[1] <= remaining[0]
            and before["native_live_at"] < before["deadline"]
            and previous_event["at"] < pause["at"]
            and before["deadline"] - remaining[0] <= pause["at"] <= before["deadline"] - remaining[1]
        )
        if ordinary_pause:
            record({"event": "leader_acquisition_discarded", **attempt,
                    "current_identity": {key: value[:128] if isinstance(value, str) else value
                                         for key, value in current_identity.items()},
                    "pause_event": {key: value[:128] if isinstance(value, str) else value
                                    for key, value in pause.items() if key in {"kind", "at", "run_id", "service_incarnation"}},
                    "reason": "same-run ordinary pause during acquisition", "discarded": True,
                    "fresh_acquisition_allowed": False})
            return None  # Existing outer authority/rejoin checks precede any new grant/capture.
        raise RuntimeError("current input identity changed during acquisition")
    try:
        result = await client.command(
            dict(op="input", **identity, grant_id=grant["grant_id"], seq=client.sequence, **frame)
        )
    except (TimeoutError, ConnectionError) as error:
        # The input may already have reached the owner. Its target, grant and
        # original expiry are consumed, never replayed as a recovery command.
        if grant_context is not None:
            error.input_context = {"identity": dict(identity), "snapshot": grant_context["snapshot"],
                                   "requested": requested, "network_seq": client.sequence,
                                   "source": dict(frame["source"])}
        record({"event": "leader_frame_receipt_unknown", **attempt,
                "error_type": type(error).__name__, "message": str(error),
                "input_timing": getattr(error, "input_command_timing", None),
                "discarded": True, "fresh_acquisition_allowed": False})
        raise
    except CommandRefusedError as error:
        error.operation, error.identity = "input", dict(identity)
        record(
            {
                "event": "leader_frame_rejected",
                **attempt,
                "receipt": error.response,
                "input_timing": error.timing,
                "discarded": True,
                "fresh_acquisition_allowed": expired_input_refusal(error),
            }
        )
        if not expired_input_refusal(error):
            raise
        return None
    return grant, frame, result


async def run(args):
    from examples.alohamini.am1_teleop_workload import TeleopWorkload

    left_reach_cycles = _left_reach_cycles(args)
    bench = getattr(args, "bench_continuity", False)
    segment = getattr(args, "workload_segment", "full")
    controller_gap = getattr(args, "interrupt_controller", False)
    terminal_budget = 12. if bench else 2.
    peer = _peer_options(segment, getattr(args, "peer_side", None), getattr(args, "peer_direction", None),
                         getattr(args, "peer_raw_step", None), bench=bench, next_start=getattr(args, "next_start_seconds", None), active_p=getattr(args, "peer_active_p", None))
    if (bench or segment != "full") and args.source != "virtual-leaders" or controller_gap and not bench:
        raise ValueError("predetermined virtual bench mode required")

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    log = (output / "frames.jsonl").open("x", encoding="utf-8")

    def record(value):
        log.write(
            json.dumps(dict(local_monotonic_s=time.monotonic(), **value), sort_keys=True, allow_nan=False)
            + "\n"
        )
        log.flush()

    client = None
    source = producer = workload = renewal = keyboard = None
    frame_count, recovery = 0, None
    started = False
    owned_run_id = None
    first_error = None
    cleanup_errors = []
    summary = {
        "source": args.source,
        "producer_program_complete": False,
        "native_execution_complete": False,
        "physical_human_movement_validated": False,
        "bench_continuity": bench,
        "workload_segment": segment,
        **({"left_reach_cycles": left_reach_cycles} if left_reach_cycles > 1 else {}),
        **({"peer_selection": peer} if peer else {}),
        "view_policy": "advisory_presentation" if bench else "required_presentation",
        "terminal_qualification_budget_seconds": terminal_budget,
        "terminal_completion_policy": "owned_pause_native_hold_after_program_complete" if bench else "owned_pause_native_hold_after_finite_input",
        "clean_input_end": None,
        "finish_qualified": False,
        "native_held_qualified": False,
        "zero_body_command": None,
        "measured_body_exact_zero": None,
    }
    renewal_counts = {"accepted": 0, "failed": 0, "cancelled": 0, "max_roundtrip_seconds": 0.0}

    def renewal_record(value):
        if value.get("event") == "controller_renewal":
            renewal_counts[value["outcome"]] += 1
            renewal_counts["max_roundtrip_seconds"] = max(renewal_counts["max_roundtrip_seconds"], value["roundtrip_seconds"])
        record(value)
    try:
        client = SessionClient(args.url, args.ca, args.storage_state)
        client.record = renewal_record
        # Missing/mismatched USB inputs refuse before creating a robot motor task.
        if args.source == "usb-leaders":
            source = USBLeaderSource(
                left_port=args.left_port,
                right_port=args.right_port,
                leader_id=args.leader_id,
                calibration_dir=args.calibration_dir,
            )
            await asyncio.to_thread(source.open)
        from lerobot.robots.alohamini.alohamini_client import AlohaMiniClient
        from lerobot.robots.alohamini.config_alohamini import AlohaMiniClientConfig

        body_mapper = AlohaMiniClient(
            AlohaMiniClientConfig(remote_ip="127.0.0.1", robot_model="alohamini1", cameras={})
        )
        from tools.am1_physical_executor import body_recipe_action

        if args.source == "usb-leaders" and not args.no_keyboard:
            from lerobot.teleoperators.keyboard.teleop_keyboard import KeyboardTeleop, KeyboardTeleopConfig

            keyboard = KeyboardTeleop(KeyboardTeleopConfig(id="am1-persistent-leader-keyboard"))
            keyboard.connect()
            if not keyboard.is_connected:
                raise RuntimeError("ordinary body keyboard failed to connect")
        # Cold mapper imports and local source setup must finish before the
        # current WSS reader/heartbeat starts on this asyncio thread.
        await client.attach()
        await client.claim()
        renewal = asyncio.create_task(client.renew())
        admission_owner = {
            "service": (client.snapshot or {}).get("service_incarnation"),
            "device": client.device,
            "generation": client.generation,
        }
        import aiohttp

        start_body = {
            "operation_id": str(uuid4()),
            "recipe": TELEOP_RECIPE,
            "controller_generation": client.generation,
        }
        if bench:
            start_body["bench_continuity"] = True
        summary.update(
            start_operation_id=start_body["operation_id"],
            start_outcome_uncertain=True,
            native_cleanup_uncertain=True,
            start_ambiguous_attempts=[],
        )
        record(
            {"event": "start_requested", "operation_id": start_body["operation_id"], "recipe": TELEOP_RECIPE,
             "bench_continuity": bench, "view_policy": summary["view_policy"], "workload_segment": segment,
             **({"left_reach_cycles": left_reach_cycles} if left_reach_cycles > 1 else {}),
             **({"peer_selection": peer} if peer else {}),
             **({"peer_baseline_qualification_budget_seconds": 2.} if peer and "fixed_start_raw" in peer else {}),
             "terminal_qualification_budget_seconds": terminal_budget,
             "terminal_completion_policy": summary["terminal_completion_policy"],
             "program_active_seconds": {"full": 172.5, "combined": 12., "body": 4.5, "continuity": 24., "combined-body": 64.5, **SelectedLeftWorkload.DURATIONS}[segment] * left_reach_cycles if args.source == "virtual-leaders" else None,
             "input_duration_seconds": args.duration_seconds,
             "controller_interruption": controller_gap, "interruption_at_seconds": getattr(args, "interrupt_at_seconds", None),
             "interruption_seconds": getattr(args, "interruption_seconds", .65)}
        )

        def start_error(error, attempt):
            detail = {"attempt": attempt, "error_type": type(error).__name__, "message": str(error)}
            if isinstance(error, HTTPOutcomeError):
                detail.update(
                    http_status=error.http_status,
                    response=error.response,
                    response_error=error.response_error,
                    non_admitted=error.non_admitted,
                )
            return detail

        # HTTP Start and every detail/attachment phase consume one original budget.
        admission_until = time.monotonic() + 180
        try:
            start_receipt = await client.post("start", start_body)
        except (OSError, TimeoutError, aiohttp.ClientError, HTTPOutcomeError) as original:
            attempt = start_error(original, 1)
            if isinstance(original, HTTPOutcomeError) and original.non_admitted:
                summary.update(
                    start_outcome_uncertain=False, native_cleanup_uncertain=False, start_refusal=attempt
                )
                record(dict(event="start_refused", operation_id=start_body["operation_id"], **attempt))
                raise
            summary["start_ambiguous_attempts"].append(attempt)
            record(dict(event="start_outcome_ambiguous", operation_id=start_body["operation_id"], **attempt))
            try:
                # Existing durable operation identity is retried once unchanged.
                start_receipt = await client.post("start", start_body)
            except (OSError, TimeoutError, aiohttp.ClientError, HTTPOutcomeError) as retry_error:
                retry = start_error(retry_error, 2)
                summary["start_ambiguous_attempts"].append(retry)
                record(
                    dict(event="start_outcome_ambiguous", operation_id=start_body["operation_id"], **retry)
                )
                raise original from retry_error
        owned_run_id = start_receipt["run_id"]
        summary["owned_run_id"] = owned_run_id
        summary["start_outcome_uncertain"] = False
        started = True
        startup_anchor = None

        def admission_identity(state):
            run, controller = state.get("run") or {}, state.get("controller") or {}
            return (
                bool(admission_owner["service"])
                and state.get("service_incarnation") == admission_owner["service"]
                and client.device == controller.get("device_id") == admission_owner["device"]
                and client.generation
                == controller.get("controller_generation")
                == admission_owner["generation"]
                and run.get("run_id") == owned_run_id
                and run.get("operation_id") == start_body["operation_id"]
                and run.get("recipe") == TELEOP_RECIPE
            )

        def startup_times(state):
            run = state.get("run") or {}
            values = tuple(run.get(key) for key in ("accepted_at", "startup_deadline"))
            if "accepted_at" not in run and startup_anchor is not None and _startup_run_reference(state):
                values = (startup_anchor[0], run.get("startup_deadline"))
            if (
                all(type(value) in (int, float) and math.isfinite(value) for value in values)
                and values[0] < values[1] <= values[0] + 180
            ):
                return values
            return None

        def native_admission_terminal(state):
            run = state.get("run") or {}
            if run.get("status") in {"faulted", "interrupted", "stopped", "completed"}:
                # Freeze the actual owned-run failure before ordinary Stop and
                # cleanup reporting can replace the current snapshot.
                summary["native_admission_terminal"] = json.loads(json.dumps(state, allow_nan=False))
                record({"event": "native_admission_terminal", "snapshot": state})
                cause = (
                    run.get("native_first_cause")
                    or run.get("first_cause")
                    or state.get("evidence", {}).get("fault")
                    or "terminal before native Live"
                )
                raise RuntimeError("native admission " + run["status"] + ": " + str(cause))

        def native_admitted():
            nonlocal startup_anchor
            if renewal.done():
                renewal.result()
                raise RuntimeError("original controller renewal ended")
            state = client.snapshot or {}
            run = state.get("run") or {}
            if reattached:
                check_admission_attachment()
            if run.get("run_id") != owned_run_id or run.get("recipe") != TELEOP_RECIPE:
                return False
            if startup_anchor is None and admission_identity(state):
                startup_anchor = startup_times(state)
            native_admission_terminal(state)
            return run.get("native_live_at") is not None

        # One current transport loss may reattach to the already accepted Start.
        # All attachment work consumes this original admission ceiling.
        reattached = False

        def admission_remaining():
            remaining = admission_until - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("original native admission ceiling exhausted")
            return remaining

        def check_admission_attachment():
            state = client.snapshot or {}
            run, evidence = state.get("run") or {}, state.get("evidence") or {}
            if (
                startup_anchor is None
                or not admission_identity(state)
                or startup_times(state) != startup_anchor
            ):
                raise RuntimeError(
                    "original native admission ownership/startup timestamps changed or unavailable"
                )
            native_admission_terminal(state)
            if (
                run.get("uncertain") is not False
                or run.get("first_cause")
                or run.get("native_first_cause")
                or evidence.get("fault")
                or run.get("status") in {"finishing", "recovering"}
            ):
                raise RuntimeError(
                    "native admission reattachment unsafe: "
                    + str(
                        run.get("native_first_cause")
                        or run.get("first_cause")
                        or evidence.get("fault")
                        or run.get("status")
                    )
                )

        async def authenticated_startup_times():
            budget = admission_remaining()
            state = (await asyncio.wait_for(client.get("state"), budget))["snapshot"]
            admission_remaining()
            if not admission_identity(state):
                raise RuntimeError("original native admission ownership changed or unavailable")
            native_admission_terminal(state)
            run = await _startup_run_detail(client, state, admission_remaining)
            values = startup_times({"run": run})
            if values is None:
                raise RuntimeError("original native admission startup timestamps unavailable")
            client.snapshot = state
            return values

        # Freeze the actual clocks before any replacement; a public projection
        # never becomes authority merely by borrowing a missing cached field.
        if (client.snapshot or {}).get("status_projection") == {"kind": "am1-essential-status", "version": 1}:
            startup_anchor = await authenticated_startup_times()
        admission_first_error = None
        try:
            while True:
                try:
                    await client.wait(native_admitted, admission_remaining())
                    admission_remaining()
                    if _startup_run_reference(client.snapshot or {}):
                        if await authenticated_startup_times() != startup_anchor:
                            raise RuntimeError("original native admission startup timestamps changed")
                        if not native_admitted():
                            continue
                    break
                except ConnectionError as original:
                    from aiohttp import ClientConnectionResetError, ServerTimeoutError

                    close = getattr(client, "transport_close", None) or {}
                    ws = getattr(client, "ws", None)
                    stream_error = ws.exception() if ws is not None and hasattr(ws, "exception") else None
                    no_pong = (
                        type(stream_error) is ServerTimeoutError
                        and str(stream_error) == f"No PONG received after {ws._pong_heartbeat} seconds"
                        and close.get("reason") == "message-error"
                    )
                    closing_transport = (
                        type(stream_error) is ClientConnectionResetError
                        and str(stream_error) == "Cannot write to closing transport"
                        and close.get("reason") == "message-error"
                    )
                    if (
                        reattached
                        or close.get("expected") is not False
                        or close.get("reason") != "stream-ended" and not (no_pong or closing_transport)
                        or close.get("close_code") != 1006
                        or close.get("reader_error") is not None
                    ):
                        raise
                    if no_pong or closing_transport:
                        admission_first_error = original
                    if renewal.done():
                        renewal.result()
                        raise RuntimeError("original controller renewal ended") from None
                    remaining = admission_remaining()
                    if ((client.snapshot or {}).get("run") or {}).get("native_live_at") is not None:
                        raise RuntimeError("native admission already reached Live before transport loss") from None
                    check_admission_attachment()
                    record(
                        {
                            "event": "native_admission_transport_closed",
                            "transport_close": close,
                            "remaining_s": remaining,
                        }
                    )
                    reattached = True
                    async with asyncio.timeout(admission_remaining()):
                        await client.detach()
                        await client.attach()
                    remaining = admission_remaining()
                    check_admission_attachment()
                    if _startup_run_reference(client.snapshot or {}):
                        if await authenticated_startup_times() != startup_anchor:
                            raise RuntimeError("original native admission startup timestamps changed") from None
                        check_admission_attachment()
                    record({"event": "native_admission_reattached", "remaining_s": remaining})
        except (Exception, asyncio.CancelledError) as qualification_error:
            if admission_first_error is not None and qualification_error is not admission_first_error:
                raise admission_first_error from qualification_error
            raise
        workspace = validate_workspace(_live(client.snapshot)["workspace"])
        run_id, deadline = client.snapshot["run"]["run_id"], client.snapshot["run"]["deadline"]
        summary.update(run_id=run_id, original_native_deadline=deadline, workspace=workspace)
        record({"event": "native_admitted", "snapshot": client.snapshot})
        if args.source == "virtual-leaders":
            if segment in SelectedLeftWorkload.DURATIONS:
                selected_kwargs = {"peer_side": peer["side"], "peer_direction": peer["direction_raw"], "peer_raw_step": peer["raw_step"],
                                   **({"peer_active_p": peer["active_p"]} if "active_p" in peer else {})} if peer else {}
                if left_reach_cycles > 1:
                    selected_kwargs["left_reach_cycles"] = left_reach_cycles
                try:
                    validate_target(workspace["reference"], workspace=workspace)
                except ValueError:
                    selected_kwargs["command_reference"] = _qualified_command_reference(client, workspace)
                workload = source = SelectedLeftWorkload(workspace, segment=segment, **selected_kwargs)
                summary["selected_profile"] = workload.plan()
                record({"event": "workload_plan", "run_id": run_id, **workload.plan()})
            else:
                workload = source = TeleopWorkload(workspace) if segment == "full" else TeleopWorkload(workspace, segment=segment)
        producer = FrameProducer(source)
        producer.workspace = workspace
        recovery = SameRunRecovery(client, record)
        def renewal_healthy():
            if renewal.done():
                renewal.result()
                raise RuntimeError("original controller renewal ended")

        if not bench:
            await recovery.resume(renewal_check=renewal_healthy)
            producer.native_cursor = _live(client.snapshot)["cursor"]
            if workload and not args.next_start_seconds:
                workload.admit(time.monotonic())
        active_at, interrupted, frame_count = (None if bench else time.monotonic()), False, 0

        def wall_deadline():
            duration = args.next_start_seconds or (args.duration_seconds if workload is None else None)
            return None if duration is None or active_at is None else active_at + duration

        async def rejoin(*, require_episode=False, refusal_identity=None, expected_pause=None, until=None, grant_error=None,
                         input_error=None):
            ceiling = until if until is not None else wall_deadline()
            if bench:
                remaining = (client.snapshot or {}).get("run", {}).get("remaining_s")
                if type(remaining) not in (int, float) or not math.isfinite(remaining) or remaining <= 0:
                    raise RuntimeError("original bench rejoin remaining clock unavailable")
                ceiling = min(time.monotonic() + min(12., remaining), ceiling if ceiling is not None else float("inf"))
            if input_error is not None:
                context = getattr(input_error, "input_context", None)
                if (not bench or not isinstance(context, dict)
                    or set(context) != {"identity", "snapshot", "requested", "network_seq", "source"}
                    or context["network_seq"] != client.sequence
                    or type(context["requested"]) not in (int, float)
                    or not math.isfinite(context["requested"]) or context["requested"] > time.monotonic()):
                    raise RuntimeError("original ambiguous input context unavailable") from input_error
                recovery.check(context["snapshot"])
                binding = {"run_id": recovery.original["run"]["run_id"],
                           "service_incarnation": recovery.original["service"],
                           "host_incarnation": recovery.original["run"]["backend_incarnation"],
                           "controller_generation": client.generation, "connection_generation": client.connection}
                if any(context["identity"].get(k) != v for k, v in binding.items()):
                    raise RuntimeError("original ambiguous input authority changed") from input_error
                ceiling = min(ceiling, context["requested"] + context["snapshot"]["run"]["remaining_s"])
                if workload:
                    workload.freeze()
                record({"event": "bench_input_receipt_requalification", "identity": context["identity"],
                        "network_seq": context["network_seq"], "source": context["source"],
                        "original_ceiling_monotonic_s": ceiling,
                        "meaning": "discard ambiguous input; fresh attachment, held qualification and new capture"})
            if bench and await ensure_authority(force=input_error is not None, until=ceiling):
                require_episode, refusal_identity, expected_pause, grant_error = False, None, None, None
            renewal_healthy()
            if workload:
                workload.freeze()
            while True:
                try:
                    recovered = await recovery.resume(
                        wall_deadline=ceiling,
                        require_episode=require_episode,
                        refusal_identity=refusal_identity,
                        expected_pause=expected_pause,
                        renewal_check=renewal_healthy, grant_error=grant_error,
                    )
                    break
                except Exception as error:
                    if not bench or not renewal.done() or time.monotonic() >= ceiling:
                        raise
                    record({"event": "bench_rejoin_renewal_interrupted", "error_type": type(error).__name__,
                            "message": str(error), "original_ceiling_monotonic_s": ceiling,
                            "transport_close": getattr(client, "transport_close", None),
                            "resume_result_context": getattr(error, "resume_result_context", None),
                            "accepted_resume_context": getattr(error, "accepted_resume_context", None)})
                    await ensure_authority(until=ceiling)
                    require_episode, refusal_identity, expected_pause, grant_error = False, None, None, None
            producer.native_cursor = _live(client.snapshot)["cursor"]
            producer.reacquire = True
            if workload and not args.next_start_seconds and active_at is not None:
                workload.admit(time.monotonic())
            return recovered

        async def ensure_authority(*, force=False, until=None):
            async with asyncio.timeout_at(until):
                return await refresh_authority(force=force, until=until)

        async def refresh_authority(*, force=False, until=None):
            nonlocal renewal
            current = (client.snapshot or {}).get("controller") or {}
            if not force and not renewal.done() and current.get("controller_generation") == client.generation:
                return False
            if not bench:
                renewal_healthy()
                return
            if workload:
                workload.freeze()
            if not renewal.done():
                renewal.cancel()
            previous = await asyncio.gather(renewal, return_exceptions=True)
            if previous and isinstance(previous[0], Exception):
                record({"event": "controller_renewal_ended", "error_type": type(previous[0]).__name__,
                        "message": str(previous[0])})
            await client.detach()
            await client.attach()
            current = (client.snapshot or {}).get("controller") or {}
            if current.get("controller_generation") == client.generation:
                # A transport/receipt loss may leave current authority intact.
                # Renew only the exact generation; never fall back to Claim.
                recovery.check(client.snapshot, history=False)
                event = _latest_run_event(client.snapshot, client.identities())
                if event.get("kind") in {"pause", "stop", "handoff", "revoked", "released"}:
                    raise RuntimeError("explicit control intent superseded automatic bench rejoin")
                try:
                    await client.post("claim", {"controller_generation": client.generation})
                except HTTPOutcomeError as error:
                    if not error.non_admitted or error.response.get("reason") != "controller renewal fence":
                        raise
                    record({"event": "controller_expired_during_renewal", "response": error.response})
                    await recovery.reacquire(until=until)
            else:
                await recovery.reacquire(until=until)
            renewal = asyncio.create_task(client.renew())
            return True

        def acquisition_state():
            state = client.snapshot
            if state["run"]["run_id"] != run_id or state["run"]["deadline"] != deadline:
                raise RuntimeError("original run identity/deadline changed")
            recovery.check(state, history=state["run"]["status"] != "recovering")
            return state

        async def qualify_acquisition(state, *, until=None):
            if state["run"]["status"] == "running":
                return True
            if state["run"]["status"] == "recovering":
                await rejoin(require_episode=True, until=until)
                return False
            cause = next(
                (
                    v.get("kind")
                    for v in reversed(state.get("events", []))
                    if v.get("kind")
                    in {"required_view_expired", "input_expired", "native_input_expired", "pause", "stop"}
                ),
                None,
            )
            if (
                state["run"]["status"] != "paused"
                or cause not in {"required_view_expired", "input_expired", "native_input_expired"}
                or not recovery.history_qualified(state)
                or state["run"].get("uncertain")
            ):
                raise RuntimeError(
                    "input held: "
                    + str(state["run"].get("first_cause") or cause or _live(state).get("state"))
                )
            if workload:
                workload.freeze()
            await rejoin(expected_pause=cause, until=until)
            record({"event": "qualified_hold_recovery", "cause": cause, "snapshot": client.snapshot})
            return True

        if bench:
            await rejoin()
            if workload and not args.next_start_seconds:
                workload.admit(time.monotonic())
            active_at = time.monotonic()
        last_phase = None
        peer_baseline_started = peer_baseline_until = None
        while True:
            if peer_baseline_until is not None and time.monotonic() >= peer_baseline_until:
                summary["peer_baseline_qualification_seconds"] = time.monotonic() - peer_baseline_started
                raise TimeoutError("peer baseline original qualification ceiling exhausted")
            if bench and await ensure_authority(until=peer_baseline_until):
                await rejoin(until=peer_baseline_until)
            if renewal.done():
                renewal.result()
            state = acquisition_state()
            wall_elapsed = time.monotonic() - active_at
            if (
                args.next_start_seconds
                and wall_elapsed >= args.next_start_seconds
                or workload is None
                and wall_elapsed >= args.duration_seconds
            ):
                if workload is None and not args.next_start_seconds:
                    summary["clean_input_end"] = "duration_elapsed"
                break
            if not await qualify_acquisition(state, until=peer_baseline_until):
                continue
            if (
                args.interrupt_at_seconds is not None
                and workload
                and not interrupted
                and workload.elapsed_s >= args.interrupt_at_seconds
            ):
                interrupted = True
                workload.freeze()
                renewal.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await renewal
                record({"event": "controlled_input_interruption", "progress_s": workload.elapsed_s,
                        "controller_gap": controller_gap, "declared_seconds": args.interruption_seconds})
                await client.detach()
                await asyncio.sleep(args.interruption_seconds)
                if bench:
                    await ensure_authority(force=True, until=peer_baseline_until)
                else:
                    await client.attach()
                    await client.claim()
                    renewal = asyncio.create_task(client.renew())
                await rejoin(until=peer_baseline_until)
                record(
                    {
                        "event": "qualified_reattachment",
                        "progress_s": workload.elapsed_s,
                        "snapshot": client.snapshot,
                    }
                )
            if peer:
                state = acquisition_state()  # Rejoin may have replaced the earlier snapshot.
                previous = workload.peer_baseline
                previous_start = workload.comparison_start_observation
                if workload.fixed and previous is None:
                    now = time.monotonic()
                    if peer_baseline_started is None:
                        remaining = state["run"].get("remaining_s")
                        if type(remaining) not in (int, float) or not math.isfinite(remaining) or remaining <= 0:
                            raise RuntimeError("peer baseline original Native time unavailable")
                        peer_baseline_started, peer_baseline_until = now, now + min(2., remaining)
                        summary.update(peer_baseline_qualified=False, peer_baseline_qualification_seconds=0.,
                                       peer_baseline_qualification_budget_seconds=2.)
                        record({"event": "peer_baseline_qualification_started", "progress_s": workload.elapsed_s,
                                "original_ceiling_monotonic_s": peer_baseline_until})
                    workload.freeze()  # Fresh q0 holds cannot consume any preparation/dwell time.
                    summary["peer_baseline_qualification_seconds"] = now - peer_baseline_started
                workload.observe_peer_baseline(state)
                workload.require_peer_baseline()
                if previous is None and workload.peer_baseline is not None:
                    if workload.fixed:
                        now = time.monotonic()
                        summary.update(peer_baseline_qualified=True,
                                       peer_baseline_qualification_seconds=now - peer_baseline_started)
                        peer_baseline_until = None
                        workload.admit(now)  # Start at zero with no wait-time catch-up.
                    record({"event": "peer_baseline_settings_verified", "binding": workload.peer_baseline,
                            "peer_selection": peer})
                if previous_start is None and workload.comparison_start_observation is not None:
                    record({"event": "peer_comparison_start_observed", **workload.comparison_start_observation})
            phase = workload.phase if workload else "usb"
            if phase != last_phase:
                record(
                    {
                        "event": "workload_phase",
                        "phase": phase,
                        "progress_s": workload.elapsed_s if workload else None,
                    }
                )
                last_phase = phase
            body = dict(ZERO_BODY)
            if workload and workload.body_phase_elapsed is not None:
                measured = client.snapshot["evidence"].get("measured_body", {})
                body_mapper.last_remote_state.update(measured)
                body = body_recipe_action(body_mapper, workload.body_phase_elapsed)
            elif keyboard is not None:
                keys = keyboard.get_action()
                if body_mapper.teleop_keys.get("quit", "q") in keys:
                    summary["clean_input_end"] = "local_quit"
                    break
                body_mapper.last_remote_state.update(client.snapshot["evidence"].get("measured_body", {}))
                # The reviewed profile uses the ordinary lowest body speed.
                keys = {k: v for k, v in keys.items() if k not in {"t", "g"}}
                body_mapper.speed_index = 0
                body = {
                    **body_mapper._from_keyboard_to_base_action(keys),
                    **body_mapper._from_keyboard_to_lift_action(keys),
                }
            if "lift_axis.vel" in body and "lift_axis.height_mm" in body:
                # LiftAxis gives explicit velocity precedence, including stopped
                # zero. Retain that behavior if a legacy mapper emits both.
                original_body = dict(body)
                body.pop("lift_axis.height_mm")
                record({"event": "body_velocity_precedence", "original": original_body, "sent": body})
            acquisition_started = time.monotonic()
            try:
                async with asyncio.timeout_at(peer_baseline_until) as baseline_budget:
                    sent = await send_leader_sample(
                        client,
                        producer,
                        body,
                        record,
                        phase=phase,
                        progress_s=workload.elapsed_s if workload else None,
                    )
            except (TimeoutError, ConnectionError) as error:
                if peer_baseline_until is not None and (baseline_budget.expired() or time.monotonic() >= peer_baseline_until):
                    summary["peer_baseline_qualification_seconds"] = time.monotonic() - peer_baseline_started
                    raise TimeoutError("peer baseline original qualification ceiling exhausted") from error
                if bench and hasattr(error, "input_context"):
                    await rejoin(input_error=error, until=peer_baseline_until)
                    continue
                if not hasattr(error, "grant_context"):
                    raise
                await rejoin(grant_error=error, until=peer_baseline_until)
                continue
            except CommandRefusedError as error:
                if bench and error.reason == "stale control generation":
                    await ensure_authority(force=True, until=peer_baseline_until)
                    await rejoin(until=peer_baseline_until)
                    continue
                mailbox = mailbox_input_refusal(error)
                view = selected_view_refusal(error)
                if not (recovery_grant_refusal(error) or mailbox or view):
                    raise
                await rejoin(require_episode=True, refusal_identity=error.identity,
                             expected_pause="native_input_expired" if mailbox else "required_view_expired" if view else None,
                             until=peer_baseline_until)
                continue
            if sent is None:
                if workload:
                    workload.freeze()
                await _acquisition_delay(acquisition_started)
                if workload and not args.next_start_seconds:
                    workload.admit(time.monotonic())
                continue  # Normal run/deadline/status/rejoin checks precede a new acquisition.
            grant, frame, result = sent
            if producer.last_clutch:
                record(producer.last_clutch)
                producer.last_clutch = None
            record(
                {
                    "event": "leader_frame",
                    "network_seq": client.sequence,
                    "grant": grant,
                    "frame": frame,
                    "receipt": result,
                    "progress_s": workload.elapsed_s if workload else None,
                    "phase": phase,
                    **(workload.cycle_metadata() if isinstance(workload, SelectedLeftWorkload) else {}),
                    "raw_leaders": getattr(source, "last_raw", None),
                    "command_clamps": producer.clutch.last_clamps,
                    "native": client.snapshot,
                }
            )
            frame_count += 1
            if workload:
                if not args.next_start_seconds:
                    workload.advance(time.monotonic())
                if (
                    workload.complete
                    or args.next_start_seconds
                    and time.monotonic() - active_at >= args.next_start_seconds
                    or not args.next_start_seconds
                    and workload.elapsed_s >= args.duration_seconds
                ):
                    break
            elif time.monotonic() - active_at >= args.duration_seconds:
                if not args.next_start_seconds:
                    summary["clean_input_end"] = "duration_elapsed"
                break
            await _acquisition_delay(acquisition_started)
        # Qualification time is separate from active program progress. Keep new
        # acquisitions alive while actual body feedback settles; never refresh
        # one retained zero sample with another grant.
        qualification_at = time.monotonic()
        native_remaining = client.snapshot["run"].get("remaining_s")
        if type(native_remaining) not in (int, float) or not math.isfinite(native_remaining) or native_remaining <= 0:
            raise RuntimeError("original terminal native remaining clock unavailable")
        qualification_ceiling = qualification_at + min(terminal_budget, native_remaining)
        record({"event": "terminal_qualification_started", "budget_seconds": terminal_budget,
                "original_ceiling_monotonic_s": qualification_ceiling, "native_remaining_s": native_remaining})
        summary["producer_program_complete"] = bool(workload and workload.complete)
        held_finish = summary["producer_program_complete"] or (
            args.source == "usb-leaders" and summary["clean_input_end"] is not None and not args.next_start_seconds
        )
        if held_finish:
            # No new motion is needed after the declared program has completed.
            # Own Pause cancels that intent; only current Native HOLD proof can
            # qualify finishing. Ordinary Stop/readback remains separate below.
            proof = await recovery.finish_owned(bench=bench, wall_deadline=qualification_ceiling, renewal_check=renewal_healthy)
            final = proof["snapshot"]
            summary.update({key: proof[key] for key in
                            ("pause_operation_id", "pause_receipt", "native_held_qualified",
                             "zero_body_command", "measured_body_exact_zero")})
        while not held_finish:
            remaining = qualification_ceiling - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("terminal current native body-zero feedback did not qualify")
            if bench and await ensure_authority(until=qualification_ceiling):
                await rejoin(until=qualification_ceiling)
            renewal_healthy()
            if not await qualify_acquisition(acquisition_state(), until=qualification_ceiling):
                continue
            remaining = qualification_ceiling - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("terminal current native body-zero feedback did not qualify")
            attempt_budget = asyncio.timeout(remaining)
            try:
                async with attempt_budget:
                    sent = await send_leader_sample(
                        client,
                        producer,
                        ZERO_BODY,
                        record,
                        phase="terminal_zero_qualification",
                        progress_s=workload.elapsed_s if workload else None,
                    )
            except TimeoutError as error:
                if bench and hasattr(error, "input_context") and not attempt_budget.expired():
                    await rejoin(input_error=error, until=qualification_ceiling)
                    continue
                if hasattr(error, "grant_context") and not attempt_budget.expired():
                    await rejoin(grant_error=error, until=qualification_ceiling)
                    continue
                if attempt_budget.expired():
                    raise RuntimeError(
                        "terminal current native body-zero feedback did not qualify"
                    ) from error
                raise
            except ConnectionError as error:
                if bench and hasattr(error, "input_context"):
                    await rejoin(input_error=error, until=qualification_ceiling)
                    continue
                if not hasattr(error, "grant_context"):
                    raise
                await rejoin(grant_error=error, until=qualification_ceiling)
                continue
            except CommandRefusedError as error:
                mailbox = mailbox_input_refusal(error)
                if not (recovery_grant_refusal(error) or mailbox):
                    raise
                await rejoin(
                    require_episode=True, refusal_identity=error.identity, until=qualification_ceiling,
                    expected_pause="native_input_expired" if mailbox else None,
                )
                continue
            if sent is None:
                await asyncio.sleep(min(0.03, max(0, qualification_ceiling - time.monotonic())))
                continue
            _, frame, _ = sent
            remaining = qualification_ceiling - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("terminal current native body-zero feedback did not qualify")
            try:
                await client.wait(
                    lambda frame=frame: native_frame_ack(client.snapshot, client.sequence, frame["source"]),
                    min(0.18, remaining),
                )
            except TimeoutError:
                recovery.check(client.snapshot, history=False)
                record({"event": "terminal_zero_ack_pending", "network_seq": client.sequence,
                        "source": frame["source"], "native_last_input_seq": _live(client.snapshot).get("last_input_seq"),
                        "native_source": _live(client.snapshot).get("source"),
                        "original_ceiling_monotonic_s": qualification_ceiling})
                continue  # New grant/capture, exact current ACK, and the same original ceiling.
            final = client.snapshot
            record({"event": "terminal_zero_qualification", "frame": frame, "native": final})
            if native_frame_ack(final, client.sequence, frame["source"], body_zero=True):
                break
            if time.monotonic() >= qualification_ceiling:
                raise RuntimeError("terminal current native body-zero feedback did not qualify")
            await asyncio.sleep(0.03)
        summary.update(
            frame_count=frame_count,
            active_progress_s=workload.elapsed_s if workload else None,
            phases_completed=sorted(workload.phases_completed) if workload else [],
            producer_program_complete=bool(workload and workload.complete),
            final_native_ack=final,
            final_body_zero=summary["measured_body_exact_zero"] if held_finish else True,
            zero_body_command=True,
            measured_body_exact_zero=summary["measured_body_exact_zero"] if held_finish else True,
            finish_qualified=True,
            interrupted=interrupted,
            qualified_recoveries=recovery.pause_recoveries,
            qualified_native_recovery_episodes=recovery.completed_episodes,
            terminal_qualification_seconds=time.monotonic() - qualification_at,
            endpoint_error_normalized={
                k: final["evidence"]["measured_positions"][k] - frame["target"][k] for k in TELEOP_ARM_KEYS
            } if frame_count else {},
        )
    except BaseException as error:
        first_error = error
        if summary.get("peer_baseline_qualified") is False:
            summary["peer_baseline_qualification_seconds"] = time.monotonic() - peer_baseline_started
        summary["failure"] = str(error)
        summary["failure_type"] = type(error).__name__
        summary["last_raw_leaders"] = getattr(source, "last_raw", None)
        if workload:
            workload.freeze()
            summary.update(
                active_progress_s=workload.elapsed_s, phases_completed=sorted(workload.phases_completed),
                producer_program_complete=bool(workload.complete),
            )
        summary["frame_count"] = frame_count
        if recovery:
            summary.update(
                qualified_native_recovery_episodes=recovery.completed_episodes,
                ordinary_pauses_observed=recovery.pause_recoveries,
            )
    finally:
        if isinstance(workload, SelectedLeftWorkload):
            summary["selected_profile"] = workload.plan()

        async def cleanup_step(name, operation, timeout):
            try:
                await asyncio.wait_for(operation(), timeout)
            except BaseException as error:
                cleanup_errors.append(
                    {"step": name, "error_type": type(error).__name__, "message": str(error)}
                )

        if renewal:

            async def stop_renewal():
                if not renewal.done():
                    renewal.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await renewal

            await cleanup_step("controller_renewal", stop_renewal, 2.0)
        if started and owned_run_id and client is not None:
            summary["native_cleanup_uncertain"] = True

            async def stop_owned_run():
                def terminal_qualified(terminal):
                    if (terminal.get("run") or {}).get("run_id") != owned_run_id:
                        raise RuntimeError("cleanup status belongs to another run")
                    if terminal["run"].get("status") in {"stopped", "faulted", "interrupted"}:
                        summary["native_terminal"] = terminal
                        terminal_run = terminal["run"]
                        summary["native_cleanup_uncertain"] = (
                            terminal_run.get("uncertain") is not False
                            or terminal_run.get("cleanup") != "physical_readback_verified"
                        )
                        if terminal_run["status"] in {"faulted", "interrupted"}:
                            summary["terminal_failure"] = {
                                "status": terminal_run["status"],
                                "cause": terminal_run.get("native_first_cause")
                                or terminal_run.get("first_cause")
                                or terminal.get("evidence", {}).get("fault")
                                or "native terminal cause unavailable",
                            }
                        if summary["native_cleanup_uncertain"]:
                            raise RuntimeError("native cleanup remains uncertain")
                        return True
                    return False

                try:
                    before_stop = (await client.get("state"))["snapshot"]
                except (OSError, TimeoutError, aiohttp.ClientError, RuntimeError) as error:
                    summary["pre_stop_state_error"] = {
                        "error_type": type(error).__name__,
                        "message": str(error),
                    }
                else:
                    if terminal_qualified(before_stop):
                        return
                try:
                    stopped = await client.post(
                        "stop", {"run_id": owned_run_id, "operation_id": str(uuid4())}
                    )
                    summary["stop_receipt"] = stopped
                except (OSError, TimeoutError, aiohttp.ClientError, RuntimeError) as error:
                    try:
                        terminal = (await client.get("state"))["snapshot"]
                        qualified = terminal_qualified(terminal)
                    except BaseException as qualification_error:
                        summary["stop_qualification_error"] = {
                            "error_type": type(qualification_error).__name__,
                            "message": str(qualification_error),
                        }
                        raise error from qualification_error
                    if not qualified:
                        raise
                    summary["stop_request_error"] = {
                        "error_type": type(error).__name__,
                        "message": str(error),
                    }
                    return
                until = time.monotonic() + 85
                while time.monotonic() < until:
                    if terminal_qualified((await client.get("state"))["snapshot"]):
                        return
                    await asyncio.sleep(0.1)
                raise TimeoutError("native cleanup did not qualify")

            await cleanup_step("native_stop", stop_owned_run, 89.0)
            summary["physical_register_off_readback_verified"] = (
                summary.get("native_cleanup_uncertain") is False
                and (summary.get("native_terminal", {}).get("run") or {}).get("cleanup") == "physical_readback_verified")
        if isinstance(source, USBLeaderSource):
            await cleanup_step("usb_leader_close", lambda: asyncio.to_thread(source.close), 3.0)
        if keyboard is not None:
            await cleanup_step("keyboard_close", lambda: asyncio.to_thread(keyboard.disconnect), 2.0)
        if client is not None:
            await cleanup_step("session_close", client.close, 3.0)
            summary["transport_error"] = getattr(client, "transport_error", None)
            summary["transport_close"] = getattr(client, "transport_close", None)
        await cleanup_step("frame_log_close", lambda: asyncio.to_thread(log.close), 2.0)
        summary["cleanup_errors"] = cleanup_errors
        summary["controller_renewal"] = renewal_counts
        if summary.get("terminal_failure") and first_error is None:
            terminal_failure = summary["terminal_failure"]
            first_error = RuntimeError(
                "native terminal " + terminal_failure["status"] + ": " + str(terminal_failure["cause"])
            )
            summary.update(failure=str(first_error), failure_type=type(first_error).__name__)
        (output / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8"
        )
    if first_error is not None:
        raise first_error
    if cleanup_errors:
        raise RuntimeError("cleanup failed; inspect durable summary: " + str(output / "summary.json"))
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("usb-leaders", "virtual-leaders"), default="usb-leaders")
    for name in ("url", "ca", "storage-state", "output"):
        parser.add_argument("--" + name, required=True)
    for name in ("left-port", "right-port", "leader-id", "calibration-dir"):
        parser.add_argument("--" + name)
    parser.add_argument("--duration-seconds", type=float, default=180.0)
    parser.add_argument("--next-start-seconds", type=float)
    parser.add_argument("--interrupt-at-seconds", type=float)
    parser.add_argument("--interruption-seconds", type=float, default=0.65)
    parser.add_argument("--bench-continuity", action="store_true", help="predetermined virtual bench; viewing is advisory")
    parser.add_argument("--workload-segment", choices=("full", "combined", "body", "continuity", "combined-body", *SelectedLeftWorkload.DURATIONS), default="full")
    parser.add_argument("--left-reach-cycles", type=int, choices=(1, 2, 3), default=1,
                        help="one to three normal virtual left-reach cycles using the original reference")
    parser.add_argument("--peer-side", choices=("left", "right"))
    parser.add_argument("--peer-direction", type=int, choices=(-1, 1))
    parser.add_argument("--peer-raw-step", type=int)
    parser.add_argument("--peer-active-p", type=int, choices=(16, 32))
    parser.add_argument("--interrupt-controller", action="store_true", help="declared bench gap also stops current controller renewals")
    parser.add_argument(
        "--no-keyboard", action="store_true", help="USB stationary probe/arm-only operation; body stays zero"
    )
    args = parser.parse_args(argv)
    try:
        _left_reach_cycles(args)
        _peer_options(args.workload_segment, args.peer_side, args.peer_direction, args.peer_raw_step,
                      bench=args.bench_continuity, next_start=args.next_start_seconds, active_p=args.peer_active_p)
    except ValueError as error:
        parser.error(str(error))
    if args.source == "usb-leaders" and not all(
        (args.left_port, args.right_port, args.leader_id, args.calibration_dir)
    ):
        parser.error("USB requires existing explicit ports, leader ID and calibration directory")
    if ((args.bench_continuity or args.workload_segment != "full") and args.source != "virtual-leaders"
        or args.interrupt_controller and not args.bench_continuity):
        parser.error("bench continuity/controller gap requires predetermined virtual source")
    if not 0 < args.duration_seconds <= 180 or not 0.3 <= args.interruption_seconds <= (12 if args.interrupt_controller else 2):
        parser.error("bounded duration <=180s and selected finite interruption required")
    if args.next_start_seconds is not None and not 0 < args.next_start_seconds <= 5:
        parser.error("next Start check must be greater than zero through5s")
    print(json.dumps(asyncio.run(run(args)), sort_keys=True))


if __name__ == "__main__":
    main()
