"""Finite generated leader input: individual chains, combined reach, then body.

This is generated bench input, never a recorded human demonstration. Only an
acknowledged active sender advances its clock; interruption time is discarded.
"""

import math
import time
from uuid import UUID, uuid4

from .am1_live_teleop import TELEOP_ARM_KEYS, validate_target, validate_workspace


class TeleopWorkload:
    kind = "virtual-leaders"
    duration_s = 172.5

    def __init__(self, workspace, *, segment="full", command_reference=None):
        segments = {"full": (0.0, 172.5), "combined": (96.0, 12.0),
                    "continuity": (96.0, 24.0), "body": (168.0, 4.5),
                    "combined-body": (108.0, 64.5), "ordinary-arms": (96.0, 12.0)}
        if segment not in segments:
            raise ValueError("Unknown declared bench segment")
        self.segment = segment
        self._program_offset_s, self.duration_s = segments[segment]
        self.workspace = validate_workspace(workspace)
        self.measured_reference = dict(self.workspace["reference"])
        self.absolute_targets = segment == "ordinary-arms"
        if self.absolute_targets:
            if command_reference is None:
                raise ValueError("ordinary-arms requires an explicit qualified legal command reference")
            self.origin = validate_target(command_reference, workspace=self.workspace)
        elif command_reference is not None:
            raise ValueError("command reference is only selected by the ordinary-arms profile")
        else:
            self.origin = {
                k: max(
                    workspace["bounds"][k]["min"], min(workspace["bounds"][k]["max"], workspace["reference"][k])
                )
                for k in TELEOP_ARM_KEYS
            }
        self.targets = {
            k: max(
                (workspace["bounds"][k]["min"], workspace["bounds"][k]["max"]),
                key=lambda v: abs(v - self.origin[k]),
            )
            for k in TELEOP_ARM_KEYS
        }
        self.elapsed_s = 0.0
        self.phases_completed = set()
        self._last = None

    @property
    def complete(self):
        return self.elapsed_s >= self.duration_s

    @property
    def _program_elapsed_s(self):
        return self._program_offset_s + self.elapsed_s

    @property
    def phase(self):
        if self.complete:
            return "complete"
        return (
            "individual"
            if self._program_elapsed_s < 96
            else "combined"
            if self._program_elapsed_s < 168
            else "body"
            if not self.complete
            else "complete"
        )

    @property
    def body_phase_elapsed(self):
        return self._program_elapsed_s - 168 if self.phase == "body" else None

    def admit(self, now):
        self._last = now

    def freeze(self):
        self._last = None

    def advance(self, now):
        if self._last is None or self.complete:
            return
        if now < self._last:
            raise ValueError("workload acquisition clock regressed")
        self.elapsed_s = min(self.duration_s, round(self.elapsed_s + min(0.1, now - self._last), 9))
        self._last = now
        if self.segment == "full":
            if self.elapsed_s >= 96:
                self.phases_completed.add("individual")
            if self.elapsed_s >= 168:
                self.phases_completed.add("combined")
            if self.complete:
                self.phases_completed.add("body")
        elif self.segment == "combined-body":
            if self.elapsed_s >= 60:
                self.phases_completed.add("combined")
            if self.complete:
                self.phases_completed.add("body")
        elif self.complete:
            self.phases_completed.add("body" if self.segment == "body" else "combined")

    def get_action(self):
        target = dict(self.origin)
        if self.phase == "individual":
            key = TELEOP_ARM_KEYS[min(11, int(self._program_elapsed_s // 8))]
            within = self._program_elapsed_s % 8
            fraction = within / 4 if within <= 4 else (8 - within) / 4
            target[key] += fraction * (self.targets[key] - self.origin[key])
        elif self.phase == "combined":
            within = (self._program_elapsed_s - 96) % 12
            fraction = within / 6 if within <= 6 else (12 - within) / 6
            # Each cycle coherently reaches/orients both arm chains and empty grippers.
            for key in TELEOP_ARM_KEYS:
                target[key] += fraction * (self.targets[key] - self.origin[key])
        return {k.removeprefix("arm_"): v for k, v in target.items()}

    def plan(self):
        if not self.absolute_targets:
            return None
        return {"segment": self.segment, "program_active_seconds": self.duration_s,
                "measured_reference": dict(self.measured_reference), "command_reference": dict(self.origin),
                "command_reference_basis": "original current native qualified HOLD cursor; measured reference unchanged",
                "targets": dict(self.targets), "active_progress_s": self.elapsed_s,
                "phases_completed": sorted(self.phases_completed),
                "schedule": [{"phase": "coordinated_reach", "start_s": 0., "end_s": 6.},
                             {"phase": "coordinated_return", "start_s": 6., "end_s": 12.}]}


class InputUnavailable(Exception):
    """No current virtual intention; no sample may be acquired or resent."""


def decode_virtual_intent(command, *, intent_channel=None):
    """Closed structural contract, without current time, ownership or workspace acceptance."""
    if type(command) is not dict:
        raise ValueError("closed virtual intent required")
    op = command.get("op")
    if type(op) is not str:
        raise ValueError("named virtual intent operation required")
    keys = {"seq", "op", "intent_channel", "issued_at_perf_counter_s", "expires_at_perf_counter_s"}
    if op in {"move", "return"}:
        keys.add("transition_seconds")
    if op == "move":
        keys.add("targets")
    if op not in {"move", "hold", "return", "finish"} or set(command) != keys:
        raise ValueError("closed move/hold/return/finish virtual intent required")
    channel = command["intent_channel"]
    try:
        valid_channel = type(channel) is str and str(UUID(channel)) == channel
    except ValueError:
        valid_channel = False
    if not valid_channel or intent_channel is not None and channel != intent_channel:
        raise ValueError("virtual intent belongs to a different or invalid run input channel")
    seq, issued, expiry = (command[key] for key in
                           ("seq", "issued_at_perf_counter_s", "expires_at_perf_counter_s"))
    if (type(seq) is not int or seq <= 0
        or any(type(value) not in (int, float) or not math.isfinite(value) for value in (issued, expiry))
        or not 0 < expiry-issued <= 300.):
        raise ValueError("finite original PC-clock intent lifetime and positive integer sequence required")
    seconds = command.get("transition_seconds", 0.)
    if op in {"move", "return"} and (type(seconds) not in (int, float)
            or not math.isfinite(seconds) or not 0 < seconds <= 300.):
        raise ValueError("finite positive transition duration required")
    if op == "move":
        patch = command["targets"]
        if type(patch) is not dict or not patch or not set(patch) <= set(TELEOP_ARM_KEYS):
            raise ValueError("one or more declared arm/gripper target keys required")
        if any(type(value) not in (int, float) or not math.isfinite(value)
               or not (0 if key.endswith("gripper.pos") else -100) <= value <= 100
               for key, value in patch.items()):
            raise ValueError("finite normalized virtual arm/gripper target required")
    return {**command, **({"targets": dict(command["targets"])} if op == "move" else {})}


class VirtualIntentWorkload:
    """Changing finite intentions inside one original ordinary-arm workspace.

    Intent expiry uses the PC capture clock, independently of acknowledged active
    ramp progress. Controller renewal and recovery cannot extend that expiry.
    """

    kind = "virtual-leaders"
    absolute_targets = True
    segment = "ordinary-arms"
    body_phase_elapsed = None
    InputUnavailable = InputUnavailable

    def __init__(self, workspace, *, command_reference, clock=time.perf_counter, intent_channel=None):
        self.workspace = validate_workspace(workspace)
        self.measured_reference = dict(self.workspace["reference"])
        self.origin = validate_target(command_reference, workspace=self.workspace)
        self.cursor = dict(self.origin)
        self.clock = clock
        self.intent_channel = str(uuid4()) if intent_channel is None else intent_channel
        self.command = None
        self.sequence = 0
        self.elapsed_s = self.ramp_elapsed_s = 0.
        self.phases_completed = set()
        self._last = None
        self.complete = False
        self.discarded_commands = 0

    @property
    def active(self):
        return (not self.complete and self.command is not None
                and self.clock() < self.command["expires_at_perf_counter_s"])

    @property
    def phase(self):
        return (f"intent-{self.command['seq']}-{self.command['op']}" if self.command is not None
                else "waiting-intent")

    def submit(self, command):
        if self.complete:
            raise ValueError("current closed virtual intent required")
        seq = command.get("seq") if type(command) is dict else None
        if type(seq) is int and 0 < seq <= self.sequence:
            self.discarded_commands += 1
            return False
        if type(seq) is int and seq > 0:
            self.sequence = seq  # A corrected command needs a new acquisition/sequence too.
        try:
            command = decode_virtual_intent(command, intent_channel=self.intent_channel)
            op = command["op"]
            issued, expiry = command["issued_at_perf_counter_s"], command["expires_at_perf_counter_s"]
            now = self.clock()
            if issued > now:
                raise ValueError("virtual intent acquisition cannot be in the future")
            target = dict(self.cursor)
            if op == "move":
                target.update(command["targets"])
            elif op == "return":
                target = dict(self.origin)
            target = validate_target(target, workspace=self.workspace)
        except ValueError:
            self.invalidate()
            raise
        if expiry <= now:
            self.discarded_commands += 1
            self.invalidate()
            return False
        self.command = command
        self.start_target, self.targets = dict(self.cursor), target
        self.ramp_elapsed_s = 0.
        self._last = None
        self.complete = op == "finish"
        return True

    def admit(self, now):
        self._last = now

    def freeze(self):
        self._last = None

    def invalidate(self):
        self.command, self._last = None, None

    def advance(self, now):
        if self._last is None or not self.active:
            return
        if now < self._last:
            raise ValueError("virtual intent progress clock regressed")
        delta = min(.1, now-self._last)
        self.elapsed_s = round(self.elapsed_s+delta, 9)
        self.ramp_elapsed_s = round(self.ramp_elapsed_s+delta, 9)
        self._last = now
        if self.ramp_elapsed_s >= self.command.get("transition_seconds", 0.):
            self.phases_completed.add(self.phase)

    def get_action(self):
        if not self.active:
            raise InputUnavailable("original virtual intent expired or unavailable")
        duration = self.command.get("transition_seconds", 0.)
        fraction = min(1., self.ramp_elapsed_s/duration) if duration else 1.
        return {key.removeprefix("arm_"): self.start_target[key]+fraction*(target-self.start_target[key])
                for key, target in self.targets.items()}

    def accepted(self, target):
        self.cursor = validate_target(target, workspace=self.workspace)

    def plan(self):
        return {"segment": self.segment, "input_mode": "changing-intent", "program_active_seconds": None,
                "intent_channel": self.intent_channel,
                "measured_reference": dict(self.measured_reference), "command_reference": dict(self.origin),
                "command_reference_basis": "original current native qualified HOLD cursor; measured reference unchanged",
                "active_progress_s": self.elapsed_s, "phases_completed": sorted(self.phases_completed),
                "last_intent_seq": self.sequence, "discarded_commands": self.discarded_commands,
                "completion": "explicit_finish_or_clean_eof" if self.complete else "not_finished"}
