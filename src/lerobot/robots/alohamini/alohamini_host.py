#!/usr/bin/env python

# Copyright 2024 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import copy
import json
import logging
import math
import os
import queue
import signal
import stat
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import cv2
import zmq

from .alohamini import AlohaMini
from .config_alohamini import AlohaMiniConfig, AlohaMiniHostConfig
from .model_specs import arm_state_keys_for_robot_model
from .selected_joint_evidence import MARKER as AM1_SELECTED_JOINT_EVIDENCE_KEY, AM1SelectedJointEvidence, live_elbow_config, live_elbow_spec, live_elbow_workspace

AM1_LOCAL_CONTROL_KEY = "_am1_local_control"
AM1_LOCAL_FEEDBACK_KEY = "_am1_local_feedback"
AM1_LIVE_RECIPE = "physical-live-left-elbow"
AM1_LIVE_JOINT = "arm_left_elbow_flex.pos"
AM1_LIVE_WRITE_PROOF_FIELDS = (
    "encoded_goal_raw", "goal_write_returned", "goal_write_started_at_monotonic_s",
    "goal_write_completed_at_monotonic_s", "goal_action_sequence",
)


class AM1LiveInputExpired(Exception):
    """An acknowledged native release, distinct from every motor/feedback fault."""


class AM1RoutineRecordWriter:
    """Practical-mode stdout work only; the worker receives immutable bytes, never hardware."""

    def __init__(self, *, write=None, capacity=128):
        if type(capacity) is not int or not 1 <= capacity <= 128:
            raise ValueError("Routine log capacity must be bounded to 1..128 records")
        self._queue = queue.Queue(maxsize=capacity)
        self._failure = None
        self._failure_lock = threading.Lock()
        self._closed = False
        self._fd = os.dup(sys.stdout.fileno()) if write is None else None
        self._write = write if write is not None else lambda data: os.write(self._fd, data)
        self._thread = threading.Thread(target=self._run, name="am1-routine-log", daemon=True)
        self._thread.start()

    def _latch(self, error):
        with self._failure_lock:
            if self._failure is None:
                self._failure = error

    def raise_if_failed(self):
        if self._failure is not None:
            raise self._failure

    def submit(self, prefix, record):
        """Freeze the original record; a full/failed sink never waits on the owner."""
        try:
            return self.submit_line(prefix + json.dumps(record, allow_nan=False, separators=(",", ":")))
        except BaseException as error:
            self._latch(error)
        return False

    def submit_line(self, line, *, flush=True):
        """The same bounded sink for already-formatted routine and exit reports."""
        if self._closed or self._failure is not None:
            return False
        try:
            line = (line + "\n").encode()
            if len(line) > 32768:
                error = ValueError("Routine log record exceeds 32768 bytes")
                error.add_note(f"AM1 routine record size: encoded_bytes={len(line)} maximum_bytes=32768")
                raise error
            self._queue.put_nowait(line)
            return True
        except queue.Full:
            self._latch(RuntimeError("AM1 routine log queue full"))
        except BaseException as error:
            self._latch(error)
        return False

    def _run(self):
        try:
            while not self._closed or not self._queue.empty():
                try:
                    line = self._queue.get(timeout=.05)
                except queue.Empty:
                    continue
                try:
                    offset = 0
                    while offset < len(line):
                        count = self._write(line[offset:])
                        if type(count) is not int or not 0 < count <= len(line) - offset:
                            raise OSError("Routine log write made invalid progress")
                        offset += count
                finally:
                    self._queue.task_done()
        except BaseException as error:
            self._latch(error)
        finally:
            if self._fd is not None:
                os.close(self._fd)

    def emit_selected(self, record):
        prefix = "[AM1 SELECTED SETTINGS] " if record.get("kind") == "visible_settings" else "[AM1 SELECTED JOINT] "
        self.submit(prefix, record)

    def emit_lift(self, record):
        record = {key: value for key, value in record.items() if key != "comparison_profile"}
        self.submit("[LIFT OPERATIONAL] ", {**record,
            "policy": "am1-confirmed-temperature-slots-relief-v2", "wall_time_ns": time.time_ns()})

    def emit_lift_samples(self, operation):
        # Only the existing routine batch changes sink. Fault/transition emits
        # retain their existing path, and no operation object reaches the worker.
        original = operation.emit
        try:
            operation.emit = self.emit_lift
            operation.emit_pending_sample()
        finally:
            operation.emit = original

    def close(self, *, timeout=2.0):
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 2:
            raise ValueError("Routine log drain needs a finite bound of at most two seconds")
        self._closed = True
        self._thread.join(timeout)
        if self._thread.is_alive():
            error = TimeoutError("AM1 routine log drain exceeded its finite bound")
            self._latch(error)
            if self._failure is not error:
                self._failure.add_note(str(error))
        self.raise_if_failed()


class AM1LocalControl:
    """AM1-only host-side pause latch on the existing motor-owner thread."""

    def __init__(self, *, run_id: str | None = None, host_incarnation: str | None = None,
                 selected_evidence: AM1SelectedJointEvidence | None = None,
                 live: bool = False, live_recipe: str = AM1_LIVE_RECIPE, practical_config=None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if (run_id is None) != (host_incarnation is None):
            raise ValueError("AM1 protected control requires both run and host identity")
        self.binding = None if run_id is None else {
            "run_id": str(UUID(run_id)), "host_incarnation": str(UUID(host_incarnation)),
        }
        self.intent_revision = -1
        self.accepted_action_sequence = -1
        self.accepted_action = None
        self.state = "ready"
        self.epoch = -1
        self.observation_id = 0
        self.selected_evidence = selected_evidence
        self.live = live
        self.teleop = live and live_recipe == "physical-live-teleop"
        self._teleop_source = None
        self.practical_config = live_elbow_config(live_recipe, practical_config) if live and not self.teleop else practical_config
        if self.teleop:
            from examples.alohamini.am1_live_teleop import TELEOP_SPEC
            self.live_spec = TELEOP_SPEC
        else:
            self.live_spec = live_elbow_spec(live_recipe, self.practical_config) if live else None
        if live and self.live_spec is None:
            raise ValueError("Live native recipe is not allowlisted")
        if live and getattr(self.live_spec, "practical_motion", False) and self.practical_config is None:
            raise ValueError("Practical native control requires its consumed configuration")
        self.clock = clock
        self.live_binding = None
        self.live_sample = None
        self.live_held = None
        self.live_cursor_at = None
        self._live_summary = {"state": "held", "last_input_seq": -1}
        if not self.teleop:
            self._live_summary["joint"] = self.live_spec.joint if live else AM1_LIVE_JOINT

    def live_snapshot(self) -> dict:
        return copy.deepcopy(self._live_summary)

    def _clear_live_write_proof(self) -> None:
        for key in AM1_LIVE_WRITE_PROOF_FIELDS:
            self._live_summary.pop(key, None)

    def _live_hold(self, robot: AlohaMini, *, expired: bool = False) -> None:
        summary = self._live_summary
        self._clear_live_write_proof()
        summary["hold_started_at_monotonic_s"] = self.clock()
        robot.stop_motion()
        robot.hold_follower_arms()
        summary["hold_completed_at_monotonic_s"] = self.clock()
        self.state = "paused"
        summary["state"] = "expired" if expired else "held"
        self.accepted_action = {"mode": "raw_goal_hold" if self.teleop else "measured_arm_hold", "body_velocity": 0.0}
        self.live_sample = None
        if self.teleop:
            summary["body"] = {"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 0}
            for key in ("accepted_target", "held_goals_raw", "held_goal_acquisition"):
                summary.pop(key, None)
        if self.selected_evidence is not None and not self.teleop:
            self.selected_evidence.deactivate_live(robot)

    def check_live(self, robot: AlohaMini) -> bool:
        """Runs on the motor owner even when no new owner/gateway command arrives."""
        if not self.live:
            return True
        if self.state == "stopped":
            return False  # A cleanup Stop cannot be reopened by expiry/watchdog ticks.
        now = self.clock()
        deadline = self._live_summary.get("live_deadline_monotonic_s")
        if deadline is not None and now >= deadline:
            if self._live_summary["state"] != "budget_expired":
                self._live_hold(robot)
                self._live_summary["state"] = "budget_expired"
            return False
        if self.live_sample is not None and now >= self.live_sample["valid_until_monotonic_s"]:
            self._live_summary["expiry_detected_at_monotonic_s"] = now
            self._live_hold(robot, expired=True)
        return True

    def before_live_write(self, robot: AlohaMini) -> None:
        valid = self.check_live(robot)
        if not valid or self._live_summary["state"] == "expired":
            raise AM1LiveInputExpired()
        if self.live_sample is not None:
            now = self.clock()
            self._live_summary.update(last_write_checked_at_monotonic_s=now,
                last_grant_age_at_write_s=max(0, now - (self.live_sample["valid_until_monotonic_s"] - .25)))

    def _refresh_live_cursor(self, robot: AlohaMini) -> None:
        # The motor projection carries native model definitions and the shared
        # session contract, not runtime finite providers or their dependencies.
        joint_keys = tuple(key for side in arm_state_keys_for_robot_model("alohamini1") for key in side)
        measured = robot.get_observation()
        if any(type(measured.get(k)) not in (int, float) or not math.isfinite(measured[k]) for k in joint_keys):
            raise RuntimeError("Live admission requires complete actual measured follower arms")
        if self.live_held is None:
            self.live_held = robot.am1_live_arm_commands({**self.binding, "recipe": self.live_spec.recipe})
            self.live_held[self.live_spec.joint] = float(measured[self.live_spec.joint])
        else:
            self.live_held[self.live_spec.joint] = float(measured[self.live_spec.joint])
        self._live_summary.update(cursor_norm=self.live_held[self.live_spec.joint],
                                  cursor_feedback_sequence=self.observation_id,
                                  cursor_intent_revision=self.intent_revision)
        self.live_cursor_at = self.clock()
        return measured

    def _apply_live(self, robot: AlohaMini, command: dict) -> bool:
        marker = command.get(AM1_LOCAL_CONTROL_KEY)
        base = {"version", "mode", "epoch", "run_id", "host_incarnation", "intent_revision", "action_sequence"}
        if (not isinstance(marker, dict) or set(marker) != base | {"live_binding", "live_input", "live_admitted"}
                or marker.get("version") != 2 or type(marker.get("live_admitted")) is not bool
                or any(marker.get(k) != v for k, v in (self.binding or {}).items())
                or any(type(marker.get(k)) is not int or marker[k] < 0 for k in
                       ("epoch", "intent_revision", "action_sequence"))
                or marker.get("mode") not in {"active", "pause"}
                or set(command) != {AM1_LOCAL_CONTROL_KEY, "x.vel", "y.vel", "theta.vel", "lift_axis.vel"}
                or any(type(command[k]) not in (int, float) or command[k] != 0 for k in
                       ("x.vel", "y.vel", "theta.vel", "lift_axis.vel"))):
            return False
        if marker["action_sequence"] <= self.accepted_action_sequence or marker["intent_revision"] < self.intent_revision:
            return False
        owner = marker["live_binding"]
        if (not isinstance(owner, dict) or set(owner) != {"service_incarnation", "controller_generation", "connection_generation"}
                or type(owner["service_incarnation"]) is not str or not owner["service_incarnation"]
                or any(type(owner[k]) is not str or not owner[k] for k in ("controller_generation", "connection_generation"))):
            return False
        try:
            if any(str(UUID(value)) != value for value in owner.values()):
                return False
        except ValueError:
            return False
        if self.live_binding != owner:
            if marker["mode"] != "pause" or (self.live_binding is not None and marker["intent_revision"] <= self.intent_revision):
                return False
        # Identity/epoch eligibility precedes EVERY stored-sample mutation.
        if marker["mode"] == "active" and not (
                (self.state == "active" and marker["epoch"] == self.epoch)
                or (self.state == "paused" and marker["epoch"] == self.epoch + 1 and marker["epoch"] % 2 == 0)):
            return False
        if marker["mode"] == "active" and marker["intent_revision"] != self.intent_revision:
            return False
        original = marker["live_input"]
        if marker["mode"] == "pause" and original is not None:
            return False
        if original is not None:
            if (not isinstance(original, dict) or set(original) != {"target", "grant_id", "seq", "valid_until_monotonic_s"}
                    or type(original["seq"]) is not int or original["seq"] < 0
                    or type(original["grant_id"]) is not str or not original["grant_id"]
                    or type(original["valid_until_monotonic_s"]) not in (int, float)
                    or not math.isfinite(original["valid_until_monotonic_s"])
                    or not isinstance(original["target"], dict) or set(original["target"]) != {self.live_spec.joint}
                    or type(original["target"][self.live_spec.joint]) not in (int, float)
                    or not math.isfinite(original["target"][self.live_spec.joint])):
                return False
            last = self._live_summary["last_input_seq"]
            if original["seq"] < last or (original["seq"] == last and original != self.live_sample):
                return False
        # Expiry never depends on a successfully parsed arriving command.
        if not self.check_live(robot):
            return False
        if original is not None and self.clock() >= original["valid_until_monotonic_s"]:
            return False
        if original is not None and original["valid_until_monotonic_s"] > self.clock() + .25 + 1e-9:
            return False
        if marker["mode"] == "active" and self.state == "paused" and marker["epoch"] != self.epoch + 1:
            return False
        standard = {k: marker[k] for k in base}
        standard["version"] = 1
        action = {k: v for k, v in command.items() if k != AM1_LOCAL_CONTROL_KEY}
        action[AM1_LOCAL_CONTROL_KEY] = standard
        if marker["mode"] == "pause":
            # Verify this caller's acknowledged map before any protected hold.
            robot.am1_live_arm_commands({**self.binding, "recipe": self.live_spec.recipe})
            accepted = self._apply_standard(robot, action)
            if not accepted:
                return False
            changed = self.live_binding != owner
            self.live_binding = copy.deepcopy(owner)
            self.live_sample = None
            if changed:
                self._live_summary["last_input_seq"] = -1
            self._live_summary["state"] = "held"
            self._clear_live_write_proof()
            measured = self._refresh_live_cursor(robot)
            if marker["live_admitted"] and "live_admitted_at_monotonic_s" not in self._live_summary:
                now = self.clock()
                q0 = self.live_held[self.live_spec.joint]
                low = q0 + self.live_spec.corridor_min_delta_norm
                high = q0 + self.live_spec.corridor_max_delta_norm
                if self.practical_config is None and not -100 <= low <= high <= 100:
                    raise RuntimeError("Live measured origin cannot contain its selected corridor")
                workspace = live_elbow_workspace(self.live_spec.recipe, self.practical_config)
                geometry = dict(corridor_min_norm=low, corridor_max_norm=high)
                if workspace is not None:
                    if not callable(getattr(robot, "am1_live_workspace_context", None)):
                        raise RuntimeError("Live workspace actual native context is unavailable")
                    context, raw = robot.am1_live_workspace_context(
                        {**self.binding, "recipe": self.live_spec.recipe}, measured)
                    try:
                        geometry = workspace.admission(context, raw, q0)
                    except ValueError as exc:
                        raise RuntimeError(str(exc)) from exc
                self._live_summary.update(reference_norm=q0, **geometry,
                    side=self.live_spec.side, label=self.live_spec.label,
                    profile=self.live_spec.profile, rate_norm_per_s=self.live_spec.rate_norm_per_s,
                    live_admitted_at_monotonic_s=now, live_deadline_monotonic_s=now + self.live_spec.live_s)
                if self.selected_evidence is not None:
                    self.selected_evidence.admit_live(robot, self._live_summary, measured=measured)
            return True
        if self.live_held is None or "live_deadline_monotonic_s" not in self._live_summary:
            return False
        if original is None:
            # Readiness does not remove or renew an already applied sample.
            if self.live_sample is not None:
                return False
            self._live_summary["state"] = "awaiting_input"
        else:
            desired = original["target"][self.live_spec.joint]
            if not self._live_summary["corridor_min_norm"] <= desired <= self._live_summary["corridor_max_norm"]:
                return False
            if original != self.live_sample:
                now = self.clock()
                previous = self._live_summary.get("accepted_at_monotonic_s")
                self._live_summary.update(last_input_seq=original["seq"], grant_id=original["grant_id"],
                    valid_until_monotonic_s=original["valid_until_monotonic_s"], accepted_at_monotonic_s=now,
                    last_input_gap_s=None if previous is None else now - previous)
                self.live_sample = copy.deepcopy(original)
            self._live_summary["state"] = "active"
        now = self.clock()
        cursor = self._live_summary["cursor_norm"]
        desired = cursor if original is None else original["target"][self.live_spec.joint]
        step = max(0, now - self.live_cursor_at) * self.live_spec.rate_norm_per_s
        next_cursor = cursor + max(-step, min(step, desired - cursor))
        # All eleven nonselected goals already have acknowledged literal raw
        # measured holds. Re-encoding their affine feedback would floor/clamp
        # setup poses and could move a joint the client cannot select.
        action[self.live_spec.joint] = next_cursor
        robot._am1_live_before_write = lambda: self.before_live_write(robot)
        previous_selected_action = getattr(robot, "_am1_selected_joint_action", None)
        write_binding = {**self.binding, "joint": self.live_spec.joint,
                         "action_sequence": marker["action_sequence"],
                         "intent_revision": marker["intent_revision"], "epoch": marker["epoch"]}
        robot._am1_live_action_binding = write_binding
        try:
            accepted = self._apply_standard(robot, action)
        except AM1LiveInputExpired:
            return False
        finally:
            robot._am1_live_action_binding = None
        if accepted:
            self.accepted_action = {**self.live_held, **(self.accepted_action or {})}
            self._live_summary["cursor_norm"] = next_cursor
            self._live_summary["accepted_target_norm"] = self.accepted_action.get(self.live_spec.joint)
            self._live_summary["nonselected_hold"] = "native-measured-raw-goals"
            self.live_cursor_at = self.clock()
            self._clear_live_write_proof()
            actual = getattr(robot, "_am1_selected_joint_action", None)
            if (isinstance(actual, dict) and actual is not previous_selected_action
                    and all(actual.get(k) == v for k, v in write_binding.items())
                    and actual.get("side") == self.live_spec.side
                    and actual.get("motor") == self.live_spec.joint.removesuffix(".pos")
                    and actual.get("sync_write_returned") is True
                    and type(actual.get("expected_goal_raw")) is int
                    and all(type(actual.get(k)) in (int, float) and math.isfinite(actual[k]) for k in
                            ("write_started_at_monotonic_s", "write_completed_at_monotonic_s"))
                    and 0 <= actual["write_started_at_monotonic_s"] <= actual["write_completed_at_monotonic_s"]):
                self._live_summary.update(encoded_goal_raw=actual["expected_goal_raw"], goal_write_returned=True,
                    goal_write_started_at_monotonic_s=actual["write_started_at_monotonic_s"],
                    goal_write_completed_at_monotonic_s=actual["write_completed_at_monotonic_s"],
                    goal_action_sequence=self.accepted_action_sequence)
                if original is not None:
                    # Historical input receipt only: expiry still clears the current
                    # write proof and this record never qualifies a later action.
                    self._live_summary["last_successful_write"] = {
                        "version": 1, "provenance": "native-encoded-goal-helper-return/historical",
                        **self.binding, **owner, "joint": self.live_spec.joint, "side": self.live_spec.side,
                        "epoch": actual["epoch"], "intent_revision": actual["intent_revision"],
                        "grant_id": original["grant_id"], "last_input_seq": original["seq"],
                        "valid_until_monotonic_s": original["valid_until_monotonic_s"],
                        "accepted_target_norm": self._live_summary["accepted_target_norm"],
                        **{key: self._live_summary[key] for key in AM1_LIVE_WRITE_PROOF_FIELDS},
                    }
            if self.selected_evidence is not None:
                self.selected_evidence.live_action(robot, self._live_summary)
        return accepted

    def annotate(self, observation: dict, *, acquired_at: float | None = None) -> None:
        self.observation_id += 1
        observation[AM1_LOCAL_FEEDBACK_KEY] = {
            "version": 1, "state": self.state, "epoch": self.epoch,
            "observation_id": self.observation_id,
        }
        if self.binding is not None:
            if acquired_at is None or not math.isfinite(acquired_at):
                raise RuntimeError("AM1 protected feedback requires actual acquisition time")
            observation[AM1_LOCAL_FEEDBACK_KEY].update(
                **self.binding, intent_revision=self.intent_revision,
                acquired_at_monotonic_s=acquired_at,
                accepted_action_sequence=self.accepted_action_sequence,
                accepted_action=self.accepted_action,
                write_acknowledged=False,
            )
            if self.live:
                observation[AM1_LOCAL_FEEDBACK_KEY]["live_input"] = self.live_snapshot()
            if self.teleop and "_am1_arm_feedback" in observation:
                observation[AM1_LOCAL_FEEDBACK_KEY]["arm_feedback"] = copy.deepcopy(observation["_am1_arm_feedback"])
            if self.teleop and "_am1_lift_feedback" in observation:
                observation[AM1_LOCAL_FEEDBACK_KEY]["lift_feedback"] = copy.deepcopy(observation["_am1_lift_feedback"])

    def watchdog_stop(self, robot: AlohaMini) -> None:
        if self.selected_evidence is not None:
            self.selected_evidence.deactivate(robot)
        robot.stop_motion()
        if self.state == "active":
            robot.hold_follower_arms()
            self.state = "paused"
            logging.warning("AM1 Local watchdog held follower arms at measured positions; epoch=%s", self.epoch)

    def apply(self, robot: AlohaMini, command: dict) -> bool:
        if self.teleop:
            from .am1_live_teleop import apply_frame
            zero_keys = {"x.vel", "y.vel", "theta.vel", "lift_axis.vel"}
            if (self.state != "ready" and self.binding is not None and set(command) == zero_keys
                    and all(type(value) in (int, float) and value == 0 for value in command.values())):
                self._live_hold(robot)
                self.state = "stopped"
                return True
            try:
                return apply_frame(self, robot, command, AM1_LOCAL_CONTROL_KEY)
            except AM1LiveInputExpired:
                return False
        if self.live:
            zero_keys = {"x.vel", "y.vel", "theta.vel", "lift_axis.vel"}
            if (self.state != "ready" and self.binding is not None
                    and "live_admitted_at_monotonic_s" in self._live_summary and set(command) == zero_keys
                    and all(type(value) in (int, float) and value == 0 for value in command.values())):
                # The outer sender's existing body-zero cleanup has no live grant.
                # It can only stop this owned run and retain its frozen raw holds.
                robot.am1_live_arm_commands({**self.binding, "recipe": self.live_spec.recipe})
                self._live_hold(robot)
                self.state = "stopped"
                return True
            return self._apply_live(robot, command)
        return self._apply_standard(robot, command)

    def _apply_standard(self, robot: AlohaMini, command: dict) -> bool:
        if AM1_SELECTED_JOINT_EVIDENCE_KEY in command and (
            self.selected_evidence is None or self.binding is None
        ):
            raise RuntimeError("Selected joint evidence requires the opted-in protected AM1 owner")
        marker = command.get(AM1_LOCAL_CONTROL_KEY)
        if marker is None:
            if self.binding is not None:
                raise RuntimeError("AM1 protected control binding is missing")
            if self.state != "ready":
                zero_keys = {"x.vel", "y.vel", "theta.vel", "lift_axis.vel"}
                if set(command) != zero_keys or any(
                    type(value) not in (int, float) or float(value) != 0.0
                    for value in command.values()
                ):
                    raise RuntimeError("AM1 Local control marker missing after session activation")
                # The outer client cleanup uses its existing body-zero command after
                # the live sender has closed. This is the only unmarked command that
                # can be accepted: stop and hold, then reject any queued old action.
                robot.stop_motion()
                if self.state == "active":
                    robot.hold_follower_arms()
                self.state = "stopped"
                return True
            robot.send_action(command)
            return True
        expected_keys = {"version", "mode", "epoch"}
        if self.binding is not None:
            expected_keys |= {"run_id", "host_incarnation", "intent_revision", "action_sequence"}
            if not isinstance(marker, dict) or any(marker.get(k) != v for k, v in self.binding.items()):
                raise RuntimeError("AM1 protected control binding differs from this run/host")
            if type(marker.get("intent_revision")) is not int or marker["intent_revision"] < 0:
                raise RuntimeError("AM1 protected intent revision is invalid")
            if type(marker.get("action_sequence")) is not int or marker["action_sequence"] < 0:
                raise RuntimeError("AM1 protected action sequence is invalid")
        if (
            not isinstance(marker, dict)
            or set(marker) != expected_keys
            or marker.get("version") != 1
            or type(marker.get("epoch")) is not int
            or marker["epoch"] < 0
            or marker.get("mode") not in {"active", "pause"}
        ):
            raise RuntimeError("Invalid AM1 Local control marker")
        if self.binding is not None and marker["action_sequence"] <= self.accepted_action_sequence:
            return False  # Duplicate/stale enqueue never reapplies a motor action.
        if self.state == "stopped":
            return False
        mode, epoch = marker["mode"], marker["epoch"]
        if AM1_SELECTED_JOINT_EVIDENCE_KEY in command and mode != "active":
            raise RuntimeError("Selected joint evidence requires an ordinary active action")
        if epoch < self.epoch:
            return False
        if self.binding is not None and marker["intent_revision"] < self.intent_revision:
            raise RuntimeError("AM1 protected intent revision regressed")
        if mode == "pause":
            if self.selected_evidence is not None:
                self.selected_evidence.deactivate(robot)
            if epoch == self.epoch and self.state == "paused":
                if self.binding is not None:
                    self.intent_revision = marker["intent_revision"]
                    self.accepted_action_sequence = marker["action_sequence"]
                return True
            expected = (
                1 if self.state == "ready"
                else self.epoch + (2 if self.state == "paused" and self.epoch % 2 == 1 else 1)
            )
            if epoch != expected or epoch % 2 != 1:
                raise RuntimeError("AM1 Local pause epoch is out of order")
            robot.stop_motion()
            robot.hold_follower_arms()
            self.state, self.epoch = "paused", epoch
            if self.binding is not None:
                self.intent_revision = marker["intent_revision"]
                self.accepted_action_sequence = marker["action_sequence"]
                self.accepted_action = {"mode": "raw_goal_hold" if self.teleop else "measured_arm_hold", "body_velocity": 0.0}
            report = getattr(robot, "_am1_routine_report", None)
            hold_kind = "exact raw-goal" if self.teleop else "measured-position"
            if report is None:
                logging.warning("AM1 Local PAUSED: %s hold acknowledged; epoch=%s", hold_kind, epoch)
            else:
                report(f"AM1 Local PAUSED: {hold_kind} hold acknowledged; epoch={epoch}")
            return True
        if epoch == self.epoch and self.state == "paused":
            return False
        starting = self.state == "ready" and epoch == 0
        resuming = self.state == "paused" and epoch == self.epoch + 1 and epoch % 2 == 0
        continuing = self.state == "active" and epoch == self.epoch
        if not (starting or resuming or continuing):
            raise RuntimeError("AM1 Local active epoch requires the acknowledged pause sequence")
        action = {key: value for key, value in command.items() if key != AM1_LOCAL_CONTROL_KEY}
        hold_admission = False
        if self.selected_evidence is not None:
            definition = getattr(robot, "_am1_selected_elbow_experiment", None)
            hold_admission = getattr(definition, "intercycle_hold_admission_rule", None) is not None
            if hold_admission:
                action = self.selected_evidence.prepare(robot, action, self.binding, control_marker=marker)
            else:
                action = self.selected_evidence.prepare(robot, action, self.binding)
        if resuming and any(float(action.get(key, 0)) != 0 for key in ("x.vel", "y.vel", "theta.vel", "lift_axis.vel")):
            raise RuntimeError("AM1 Local first resumed action must have zero body velocity")
        if hold_admission:
            try:
                accepted = robot.send_action(action)
            except BaseException:
                self.selected_evidence.abort_hold_admission()
                raise
            if accepted is None:
                self.selected_evidence.abort_hold_admission()
        else:
            accepted = robot.send_action(action)
        self.state, self.epoch = "active", epoch
        if self.binding is not None:
            self.intent_revision = marker["intent_revision"]
            self.accepted_action_sequence = marker["action_sequence"]
            self.accepted_action = None if accepted is None else {
                key: _jsonable(value) for key, value in accepted.items()
                if type(_jsonable(value)) in (int, float) and math.isfinite(_jsonable(value))
            }
        if resuming:
            report = getattr(robot, "_am1_routine_report", None)
            if report is None:
                logging.info("AM1 Local RECOVERED: first bounded action applied; epoch=%s", epoch)
            else:
                report(f"AM1 Local RECOVERED: first bounded action applied; epoch={epoch}")
        return True


def _private_directory(path: Path) -> None:
    info = path.stat(follow_symlinks=False)
    if path.is_symlink() or not stat.S_ISDIR(info.st_mode):
        raise RuntimeError("AM1 physical directory must be an actual owned directory")
    if os.name != "nt" and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise RuntimeError("AM1 physical directory requires current owner and mode 0700")


class AM1PhysicalAdmission:
    """Canonical admission before effects, plus an independent serial-owner lock.

    An inherited descriptor is the parent's already locked open-file description.
    Never unlock it from the child: closing our duplicate preserves the parent.
    """

    def __init__(self, state_directory: str | Path, *, inherited_fd: int | None = None):
        if os.name == "nt":
            raise RuntimeError("AM1 physical motor ownership requires native Pi flock")
        import fcntl

        self.canonical = self.serial = None
        directory = Path(state_directory).expanduser()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        _private_directory(directory)
        if any((directory / name).exists() or (directory / name).is_symlink()
               for name in ("cleanup-uncertain.json", "physical-in-progress.json")):
            raise RuntimeError("AM1 physical cleanup requires actual stopped reconciliation")
        lock_path = directory / "active.lock"
        try:
            if inherited_fd is None:
                self.canonical = self._open_lock(lock_path)
                try:
                    fcntl.flock(self.canonical.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as error:
                    raise RuntimeError("Another AM1 physical owner is active; refusing takeover") from error
            else:
                info = os.fstat(inherited_fd)
                target = lock_path.stat(follow_symlinks=False)
                if (info.st_dev, info.st_ino) != (target.st_dev, target.st_ino) or not stat.S_ISREG(info.st_mode):
                    raise RuntimeError("AM1 inherited admission descriptor is not canonical active.lock")
                probe = self._open_lock(lock_path)
                try:
                    try:
                        fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        pass
                    else:
                        raise RuntimeError("AM1 inherited admission descriptor was not already locked")
                    # This succeeds only on the already owning open-file description.
                    fcntl.flock(inherited_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    probe.close()
                self.canonical = os.fdopen(os.dup(inherited_fd), "a+b")
            self.directory = directory
            self.acquire_serial_owner()
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _open_lock(path: Path):
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise RuntimeError("AM1 physical lock requires owned mode 0600 regular file")
            return os.fdopen(descriptor, "a+b")
        except BaseException:
            os.close(descriptor)
            raise

    def acquire_serial_owner(self) -> None:
        import fcntl

        if self.serial is not None:
            return
        self.serial = self._open_lock(self.directory / "serial-owner.lock")
        try:
            fcntl.flock(self.serial.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("Another AM1 serial owner is active; refusing takeover") from error

    def claim_run(self, *, run_id: str, host_incarnation: str, scope: str) -> None:
        """Persist ownership before constructing anything that can touch a device.

        File locks disappear on abrupt process death; this record deliberately does
        not. Only this owner's complete actual cleanup receipt may clear it.
        """
        if scope not in ("protected", "legacy"):
            raise ValueError("AM1 physical owner scope must be protected or legacy")
        record = {
            "version": 1, "kind": "am1-physical-in-progress",
            "run_id": str(UUID(run_id)), "host_incarnation": str(UUID(host_incarnation)),
            "scope": scope, "owner_pid": os.getpid(),
            "claimed_at_monotonic_s": time.monotonic(),
        }
        path = self.directory / "physical-in-progress.json"
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as error:
            raise RuntimeError("AM1 unfinished physical owner requires actual stopped reconciliation") from error
        # Even a partial write or persistence failure keeps the record in place.
        # Removing it on error could hide a crash with uncertain device state.
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(record, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_directory(self.directory)
        self.in_progress = record

    def finish_run(self, receipt: dict) -> bool:
        """Clear only our durable claim after every actual bus close is qualified."""
        record = getattr(self, "in_progress", None)
        readback = receipt.get("readback")
        if (record is None or receipt.get("uncertain") is not False
            or receipt.get("cleanup") != "physical_readback_verified"
            or not isinstance(readback, dict) or not readback.get("verified")
            or not readback.get("buses_closed") or receipt.get("cleanup_errors")
            or any(receipt.get(key) != record[key] for key in ("run_id", "host_incarnation"))):
            return False
        path = self.directory / "physical-in-progress.json"
        if path.is_symlink() or json.loads(path.read_text(encoding="utf-8")) != record:
            raise RuntimeError("AM1 durable owner changed; actual stopped reconciliation is required")
        path.unlink()
        _fsync_directory(self.directory)
        self.in_progress = None
        return True

    def close(self) -> None:
        for stream in (self.serial, self.canonical):
            if stream is not None:
                stream.close()
        self.serial = self.canonical = None


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return  # Mock-only Windows checks; native Pi ownership requires POSIX.
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_private_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def write_am1_cleanup_receipt(args, robot, first_error: BaseException | None, cleanup_errors: list[str]) -> dict:
    readback = getattr(robot, "_am1_shutdown_evidence", None)
    verified = bool(isinstance(readback, dict) and readback.get("verified")
                    and readback.get("buses_closed") and not cleanup_errors)

    def cause_chain(error):
        chain = []
        while error is not None:
            chain.append({"cause": f"{type(error).__name__}: {error}", "notes": list(getattr(error, "__notes__", []))})
            error = error.__cause__
        return chain

    requested_stop = getattr(args, "_am1_stop_interruption", None)
    expected_stop = None
    if requested_stop is not None:
        expected_stop = {
            "signal": getattr(args, "_am1_stop_signal", None),
            "interruption": cause_chain(requested_stop),
        }
    if first_error is not None and first_error is requested_stop:
        # Cancellation is ordinary only after actual, complete stopped readback.
        # Any genuine cleanup failure keeps its own cause and the stop remains
        # visible separately, including notes from an interrupted startup.
        first_error = getattr(args, "_am1_cleanup_first_error", None)
        if not verified and first_error is None:
            first_error = RuntimeError("Physical cleanup was not verified after requested Stop")
    error_chain = cause_chain(first_error)
    result = {
        "version": 1,
        "run_id": getattr(args, "_am1_physical_run_id", args.am1_run_id),
        "host_incarnation": getattr(args, "_am1_physical_host_incarnation", args.am1_host_incarnation),
        "cleanup": "physical_readback_verified" if verified else "physical_cleanup_unknown",
        "uncertain": not verified,
        "first_cause": None if not error_chain else error_chain[-1]["cause"],
        "first_error_chain": error_chain, "expected_stop": expected_stop,
        "cleanup_errors": list(cleanup_errors), "readback": readback,
        "completed_at_monotonic_s": time.monotonic(),
        "provenance": "protected-host/actual-motor-readback",
    }
    fault = getattr(robot, "_am1_selected_joint_fault", None)
    if isinstance(fault, dict):
        result["selected_joint_fault"] = copy.deepcopy(fault)
    experiment = getattr(robot, "_am1_selected_joint_experiment", None)
    if isinstance(experiment, dict):
        result["selected_joint_experiment"] = copy.deepcopy(experiment)
    capture_error = getattr(robot, "_am1_selected_joint_fault_capture_error", None)
    if capture_error is not None:
        result["selected_joint_fault_capture_error"] = capture_error
    path = getattr(args, "_am1_physical_cleanup_receipt", args.am1_cleanup_receipt)
    _atomic_private_json(Path(path), result)
    if not verified and hasattr(args, "am1_physical_state_directory"):
        _atomic_private_json(Path(args.am1_physical_state_directory) / "cleanup-uncertain.json", result)
    return result


class AlohaMiniHost:
    def __init__(self, config: AlohaMiniHostConfig):
        self.zmq_context = zmq.Context()
        self.zmq_cmd_socket = self.zmq_context.socket(zmq.PULL)
        self.zmq_observation_socket = self.zmq_context.socket(zmq.ROUTER)
        self._ipc_paths: list[Path] = []
        try:
            if config.protected_run_directory is None:
                endpoints = (f"tcp://*:{config.port_zmq_cmd}", f"tcp://*:{config.port_zmq_observations}")
            else:
                directory = Path(config.protected_run_directory)
                _private_directory(directory)
                socket_paths = [directory / "command.sock", directory / "observation.sock"]
                if any(len(os.fsencode(path)) > 100 or path.exists() or path.is_symlink() for path in socket_paths):
                    raise RuntimeError("AM1 protected sockets require fresh bounded local paths")
                endpoints = tuple(f"ipc://{path}" for path in socket_paths)
            self.zmq_cmd_socket.setsockopt(zmq.CONFLATE, 1)
            self.zmq_cmd_socket.setsockopt(zmq.LINGER, 0)
            self.zmq_cmd_socket.bind(endpoints[0])
            if config.protected_run_directory is not None:
                self._ipc_paths.append(socket_paths[0])
            self.zmq_observation_socket.setsockopt(zmq.SNDHWM, config.observation_request_window)
            self.zmq_observation_socket.setsockopt(zmq.RCVHWM, config.observation_request_window)
            self.zmq_observation_socket.setsockopt(zmq.LINGER, 0)
            self.zmq_observation_socket.bind(endpoints[1])
            if config.protected_run_directory is not None:
                self._ipc_paths.append(socket_paths[1])
            for path in self._ipc_paths:
                path.chmod(0o600)
        except BaseException:
            self.disconnect()
            raise
        self.connection_time_s = config.connection_time_s
        self.watchdog_timeout_ms = config.watchdog_timeout_ms
        self.max_loop_freq_hz = config.max_loop_freq_hz

    def disconnect(self):
        self.zmq_observation_socket.close(linger=0)
        self.zmq_cmd_socket.close(linger=0)
        self.zmq_context.term()
        for path in self._ipc_paths:
            path.unlink(missing_ok=True)


class HostCommandState:
    """Monotonic watchdog state with optional bounded command-cadence evidence."""

    def __init__(
        self,
        *,
        watchdog_timeout_ms: int,
        diagnostics_enabled: bool = False,
        clock: Callable[[], float] | None = None,
        wall_clock_ns: Callable[[], int] | None = None,
    ) -> None:
        self.watchdog_timeout_ms = watchdog_timeout_ms
        self.diagnostics_enabled = diagnostics_enabled
        self._clock = time.monotonic if clock is None else clock
        self._wall_clock_ns = time.time_ns if wall_clock_ns is None else wall_clock_ns
        self._last_command_s = self._clock()
        self._previous_receive_s: float | None = None
        self._watchdog_active = False
        self._command_sequence = 0
        self._last_receive_gap_ms = 0.0
        self._max_receive_gap_ms = 0.0
        self._receive_gap_over_watchdog_count = 0
        self._last_receive_gap_over_watchdog_sequence: int | None = None
        self._last_receive_gap_over_watchdog_ms: float | None = None
        self._last_receive_gap_over_watchdog_wall_time_ns: int | None = None
        self._watchdog_events = 0
        self._last_watchdog_event_wall_time_ns: int | None = None
        self._last_action_diagnostics: dict[str, object] = {}
        self._last_receive_wall_time_ns: int | None = None

    def capture_wall_time_ns(self) -> int | None:
        """Capture a shared log timestamp without adding work when diagnostics are off."""
        if not self.diagnostics_enabled:
            return None
        return self._wall_clock_ns()

    def record_command(
        self,
        action_diagnostics: dict[str, object],
        *,
        received_at: float | None = None,
        received_wall_time_ns: int | None = None,
    ) -> None:
        completed_at = self._clock()
        receive_time = completed_at if received_at is None else received_at
        if self.diagnostics_enabled:
            self._command_sequence += 1
            if self._previous_receive_s is not None:
                gap_ms = (receive_time - self._previous_receive_s) * 1e3
                self._last_receive_gap_ms = gap_ms
                self._max_receive_gap_ms = max(self._max_receive_gap_ms, gap_ms)
                if gap_ms > self.watchdog_timeout_ms:
                    self._receive_gap_over_watchdog_count += 1
                    self._last_receive_gap_over_watchdog_sequence = self._command_sequence
                    self._last_receive_gap_over_watchdog_ms = gap_ms
                    self._last_receive_gap_over_watchdog_wall_time_ns = received_wall_time_ns
            self._last_action_diagnostics = dict(action_diagnostics)
            self._last_receive_wall_time_ns = received_wall_time_ns
        self._previous_receive_s = receive_time
        # Preserve the established watchdog lifecycle: a successfully applied action
        # resets its age at completion, while diagnostics measure socket receive gaps.
        self._last_command_s = completed_at
        self._watchdog_active = False

    def watchdog_due(self) -> bool:
        if self._watchdog_active:
            return False
        if (self._clock() - self._last_command_s) <= self.watchdog_timeout_ms / 1000:
            return False
        self._watchdog_active = True
        if self.diagnostics_enabled:
            self._watchdog_events += 1
            self._last_watchdog_event_wall_time_ns = self._wall_clock_ns()
        return True

    def snapshot(self) -> dict[str, object] | None:
        if not self.diagnostics_enabled:
            return None
        report = {
            "wall_time_ns": self._wall_clock_ns(),
            "command_sequence": self._command_sequence,
            "last_receive_gap_ms": self._last_receive_gap_ms,
            "max_receive_gap_ms": self._max_receive_gap_ms,
            "receive_gap_over_watchdog_count": self._receive_gap_over_watchdog_count,
            "last_receive_gap_over_watchdog_sequence": (
                self._last_receive_gap_over_watchdog_sequence
            ),
            "last_receive_gap_over_watchdog_ms": self._last_receive_gap_over_watchdog_ms,
            "last_receive_gap_over_watchdog_wall_time_ns": (
                self._last_receive_gap_over_watchdog_wall_time_ns
            ),
            "watchdog_events": self._watchdog_events,
            "last_watchdog_event_wall_time_ns": self._last_watchdog_event_wall_time_ns,
            "target_limited": _jsonable(self._last_action_diagnostics.get("target_limited")),
            "right_wrist_requested": _jsonable(
                self._last_action_diagnostics.get("right_wrist_requested")
            ),
            "right_wrist_final": _jsonable(self._last_action_diagnostics.get("right_wrist_final")),
            "right_wrist_observed": _jsonable(
                self._last_action_diagnostics.get("right_wrist_observed")
            ),
        }
        if "right_shoulder" in self._last_action_diagnostics:
            report["right_shoulder"] = {
                **_jsonable(self._last_action_diagnostics["right_shoulder"]),
                "command_received_wall_time_ns": self._last_receive_wall_time_ns,
            }
        if "left_shoulder" in self._last_action_diagnostics:
            report["left_shoulder"] = {
                **_jsonable(self._last_action_diagnostics["left_shoulder"]),
                "command_received_wall_time_ns": self._last_receive_wall_time_ns,
            }
        return report

    def format_report(self) -> str | None:
        snapshot = self.snapshot()
        if snapshot is None:
            return None
        return f"[HOST CADENCE] {json.dumps(snapshot, separators=(',', ':'))}"


def print_cadence_report(command_state: HostCommandState, *, emit=None) -> None:
    report = command_state.format_report()
    if report is not None:
        (print if emit is None else emit)(report, flush=True)


class AM1HostLoopTiming:
    """Eight completed owner loops and the current loop, emitted after a fault.

    These wall durations include scheduling delays, not just CPU work. They
    locate a delayed phase without attributing it to a particular device/sink.
    No motor reads, waits, periodic output, or freshness-policy changes.
    """

    HISTORY_SIZE = 8

    def __init__(self) -> None:
        self.previous: dict[str, Any] | None = None
        self.current: dict[str, Any] | None = None
        self._completed: deque[dict[str, Any]] = deque(maxlen=self.HISTORY_SIZE)
        self._omitted_completed_loop_count = 0
        self._phase_started_s = 0.0
        self._loop_index = 0

    def begin(self, at: float) -> None:
        self._loop_index += 1
        self.current = {
            "loop_index": self._loop_index, "start_s": at,
            "between_loops_ms": None if self.previous is None else round(
                (at - self.previous["end_s"]) * 1000, 3,
            ),
            "active_phase": "lift_poll", "phase_ms": {}, "completed": False,
        }
        self._phase_started_s = at

    def mark(self, phase: str, at: float) -> None:
        self.current["phase_ms"][self.current["active_phase"]] = round(
            (at - self._phase_started_s) * 1000, 3,
        )
        self.current["active_phase"] = phase
        self._phase_started_s = at

    def finish(self, at: float) -> None:
        self.mark("complete", at)
        self.current.update(
            end_s=at, elapsed_ms=round((at - self.current["start_s"]) * 1000, 3), completed=True,
        )
        self.previous, self.current = self.current, None
        if len(self._completed) == self.HISTORY_SIZE:
            self._omitted_completed_loop_count += 1
        self._completed.append(self.previous)

    def snapshot(self, at: float) -> dict[str, Any]:
        current = None
        if self.current is not None:
            current = {**self.current, "phase_ms": dict(self.current["phase_ms"])}
            current["phase_ms"][current["active_phase"]] = round(
                (at - self._phase_started_s) * 1000, 3,
            )
            current["elapsed_ms"] = round((at - current["start_s"]) * 1000, 3)

        def copy_completed(record: dict[str, Any]) -> dict[str, Any]:
            return {**record, "phase_ms": dict(record["phase_ms"])}

        return {
            "clock": "perf_counter",
            "previous_loop": None if self.previous is None else copy_completed(self.previous),
            "current_loop": current,
            "recent_completed_loops": [copy_completed(record) for record in self._completed],
            "completed_loop_history_limit": self.HISTORY_SIZE,
            "omitted_completed_loop_count": self._omitted_completed_loop_count,
        }


def print_startup_shoulder_report(
    robot: AlohaMini, command_state: HostCommandState, observation: dict,
) -> None:
    """One optional readback on the existing owner, never a second bus or writer."""
    if robot.config.robot_model != "alohamini1":
        return
    snapshot = command_state.snapshot()
    if snapshot is None or "right_shoulder" not in snapshot:
        return
    record = {
        **snapshot["right_shoulder"], "command_sequence": snapshot["command_sequence"],
        "wall_time_ns": time.time_ns(), "read_started_at": time.monotonic(),
        "observed_position": observation.get("arm_right_shoulder_lift.pos"),
        "goal_position_readback": None,
    }
    def emit() -> None:
        record["read_completed_at"] = time.monotonic()
        print(f"[AM1 SYNC SHOULDER] {json.dumps(_jsonable(record), separators=(',', ':'))}", flush=True)

    try:
        record["goal_position_readback"] = robot.right_bus.read(
            "Goal_Position", "arm_right_shoulder_lift", num_retry=0,
        )
    except BaseException as error:
        record["readback_error"] = f"{type(error).__name__}: {error}"
        try:
            emit()
        except BaseException as report_error:
            error.add_note(f"shoulder readback reporting also failed: {report_error}")
        raise  # Preserve a genuine communication/servo failure and its identity.
    emit()


class AM1LeftShoulderEvidence:
    """Sparse diagnostic reads by the existing host owner; never motion authority."""

    SETTINGS = (
        "CW_Dead_Zone", "CCW_Dead_Zone", "Operating_Mode", "Torque_Enable",
        "Goal_Time", "Goal_Velocity", "Torque_Limit", "Max_Torque_Limit",
        "P_Coefficient", "I_Coefficient", "D_Coefficient", "Minimum_Startup_Force",
    )

    def __init__(
        self, *, enabled: bool, clock: Callable[[], float] = time.monotonic,
        wall_clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self.enabled, self._clock, self._wall_clock_ns = enabled, clock, wall_clock_ns
        self._last_read_at: float | None = None
        self._settings: dict | None = None
        self._goal_read_attempts = 0
        self._goal_read_unavailable = 0

    def report(self, robot: AlohaMini, command_state: HostCommandState) -> None:
        if not self.enabled or robot.config.robot_model != "alohamini1":
            return
        snapshot = command_state.snapshot()
        if snapshot is None or "left_shoulder" not in snapshot:
            return
        now = self._clock()
        if self._last_read_at is not None and now - self._last_read_at < 1.0:
            return
        self._last_read_at = now
        record = {
            **snapshot["left_shoulder"], "command_sequence": snapshot["command_sequence"],
            "wall_time_ns": self._wall_clock_ns(),
            "goal_read_started_at": now,
            "goal_read_started_wall_time_ns": self._wall_clock_ns(),
            "goal_position_readback_raw": None, "goal_matches_expected_raw": None,
            "diagnostic_available": False, "feedback_qualified": False,
        }
        bus, name = robot.left_bus, "arm_left_shoulder_lift"
        primary_error: Exception | None = None
        self._goal_read_attempts += 1
        try:
            goal = bus.read("Goal_Position", name, normalize=False, num_retry=0)
            record["goal_position_readback_raw"] = goal
            expected = record.get("expected_goal_raw")
            if type(expected) is int:
                record["goal_matches_expected_raw"] = int(goal) == expected
            record["diagnostic_available"] = True
        except Exception as error:
            record["goal_read_error"] = f"{type(error).__name__}: {error}"
            self._goal_read_unavailable += 1
            if not isinstance(error, ConnectionError):
                primary_error = error
        record["goal_read_completed_at"] = self._clock()
        record["goal_read_attempt_count"] = self._goal_read_attempts
        record["goal_read_unavailable_count"] = self._goal_read_unavailable
        if self._settings is None and record["diagnostic_available"]:
            settings = {"registers_raw": {}, "reads": {}, "unavailable": {}}
            self._settings = settings
            table = bus.model_ctrl_table[bus.motors[name].model]
            settings_started = self._clock()
            for register in self.SETTINGS:
                if register not in table:
                    settings["unavailable"][register] = "not in the actual motor model table"
                    continue
                if self._clock() - settings_started >= 0.020:
                    settings["unavailable"][register] = "20 ms snapshot budget exhausted"
                    continue
                acquired = {
                    "started_at": self._clock(),
                    "started_wall_time_ns": self._wall_clock_ns(),
                }
                try:
                    settings["registers_raw"][register] = bus.read(
                        register, name, normalize=False, num_retry=0,
                    )
                except Exception as error:
                    settings["unavailable"][register] = f"{type(error).__name__}: {error}"
                    if not isinstance(error, ConnectionError):
                        primary_error = error
                acquired["completed_at"] = self._clock()
                settings["reads"][register] = acquired
                if primary_error is not None:
                    break
            settings["complete"] = not settings["unavailable"]
        record["settings_snapshot"] = self._settings
        if primary_error is not None:
            record["diagnostic_available"] = False
        try:
            print(f"[AM1 LEFT SHOULDER] {json.dumps(_jsonable(record), separators=(',', ':'))}", flush=True)
        except BaseException as report_error:
            if primary_error is None:
                raise
            primary_error.add_note(f"shoulder evidence reporting also failed: {report_error}")
        if primary_error is not None:
            raise primary_error


def _jsonable(value):
    """Convert numpy scalars to JSON-native values without touching normal Python values."""
    if hasattr(value, "item"):
        try:
            return value.item()
        except ValueError:
            pass
    return value


def format_lift_diagnostic_report(robot: AlohaMini, observation: dict) -> str:
    lift_snapshot = robot.read_lift_diagnostics()
    report = {
        "wall_time_ns": time.time_ns(),
        "monotonic_time_ns": time.monotonic_ns(),
        "height_mm": _jsonable(observation.get("lift_axis.height_mm")),
        **lift_snapshot,
    }
    return f"[LIFT DIAGNOSTICS] {json.dumps(report, separators=(',', ':'))}"


def build_observation_multipart(observation: dict, camera_keys) -> list[bytes]:
    """Encode state as JSON and camera images as binary JPEG multipart frames."""
    state_observation = {
        key: _jsonable(value) for key, value in observation.items() if key not in camera_keys
    }
    state_observation["_image_encoding"] = "jpeg"

    parts = [json.dumps(state_observation).encode("utf-8")]
    image_names = []
    for cam_key in camera_keys:
        frame = observation.get(cam_key)
        if frame is None:
            continue
        ret, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        if not ret:
            logging.warning("Failed to JPEG encode camera frame %s.", cam_key)
            continue
        image_names.append(cam_key)
        parts.extend([cam_key.encode("utf-8"), buffer.tobytes()])

    state_observation["_images"] = image_names
    parts[0] = json.dumps(state_observation).encode("utf-8")
    return parts


def parse_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value

    value = value.lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise argparse.ArgumentTypeError("Expected true or false.")


def positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("Expected a positive number.")
    return parsed


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("Expected a positive integer.")
    return parsed


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run AlohaMini host process")
    parser.add_argument(
        "--robot_model",
        type=str,
        default="alohamini1",
        choices=["alohamini1", "alohamini2", "alohamini2pro"],
        help=(
            "Robot model — drives follower arm profile, base motors, lift motor, lead screw pitch, "
            "and chassis kinematics.\n"
            "  alohamini1   : so-arm-5dof,         base sts3215, lift sts3215, lead=84 mm/rev,  "
            "wheel=0.05m, radius=0.125m\n"
            "  alohamini2   : am-follower-6dof,    base sts3215, lift sts3095, lead=131 mm/rev, "
            "wheel=0.063m, radius=0.195m\n"
            "  alohamini2pro: am-follower-6dof-hd, base sts3250, lift sts3095, lead=131 mm/rev, "
            "wheel=0.063m, radius=0.195m"
        ),
    )
    parser.add_argument(
        "--no_follower",
        action="store_true",
        help="Do not connect follower arms, only operate the base and lift. Use together with --no_leader on the teleoperate side.",
    )
    parser.add_argument(
        "--no_cameras",
        action="store_true",
        help="Construct the host robot with an empty camera configuration.",
    )
    parser.add_argument(
        "--skip_lift_home",
        action="store_true",
        help="Do not home the lift; lift motion stays disabled for this process.",
    )
    parser.add_argument(
        "--max_relative_target",
        type=positive_float,
        default=None,
        help="Maximum follower-arm position change allowed per command (default: disabled).",
    )
    parser.add_argument(
        "--max_loop_freq_hz",
        type=positive_int,
        default=30,
        help="Maximum host control-loop frequency in Hz (default: 30).",
    )
    parser.add_argument(
        "--profile_timing",
        "--profile-timing",
        type=parse_bool,
        nargs="?",
        const=True,
        default=False,
        help=(
            "Print average Host, motor, camera, JPEG, and network timings once per second "
            "(default: false)."
        ),
    )
    parser.add_argument(
        "--profile_cadence",
        "--profile-cadence",
        type=parse_bool,
        nargs="?",
        const=True,
        default=False,
        help=(
            "Print monotonic command sequence/gap, watchdog, limiter, and right-wrist evidence "
            "once per second (default: false)."
        ),
    )
    parser.add_argument(
        "--profile_lift_diagnostics",
        "--profile-lift-diagnostics",
        type=parse_bool,
        nargs="?",
        const=True,
        default=False,
        help=(
            "Print a read-only AM1 lift current, temperature, voltage, velocity, torque, "
            "mode, status, and process-height snapshot once per second (default: false)."
        ),
    )
    parser.add_argument(
        "--lift_relief", "--lift-relief", action="store_true",
        help="Opt-in guarded AM1 lift-only home/10mm relief/45s rest comparison; no ZMQ host.",
    )
    parser.add_argument(
        "--lift_readback", "--lift-readback", action="store_true",
        help="Opt-in three-second AM1 lift torque-off telemetry check; no homing, motion, or ZMQ host.",
    )
    parser.add_argument("--am1-protected-run-directory")
    parser.add_argument("--am1-run-id")
    parser.add_argument("--am1-host-incarnation")
    parser.add_argument("--am1-cleanup-receipt")
    parser.add_argument("--am1-selected-experiment-file")
    parser.add_argument("--am1-right-p20-trial-file")
    parser.add_argument("--am1-admission-fd", type=int)
    parser.add_argument("--am1-host-runtime-s", type=int, default=480)
    parser.add_argument("--am1-physical-state-directory", default=str(Path.home() / ".local/state/am1-session"))
    return parser


def make_robot_config(args: argparse.Namespace) -> AlohaMiniConfig:
    config = AlohaMiniConfig(
        id="AlohaMiniRobot",
        robot_model=args.robot_model,
        no_follower=args.no_follower,
        max_relative_target=args.max_relative_target,
        diagnostic_lift_only=args.lift_relief,
    )
    if args.no_cameras:
        config.cameras = {}
    return config


def make_host_config(args: argparse.Namespace) -> AlohaMiniHostConfig:
    return AlohaMiniHostConfig(
        max_loop_freq_hz=args.max_loop_freq_hz,
        protected_run_directory=getattr(args, "am1_protected_run_directory", None),
        connection_time_s=(args.am1_host_runtime_s if getattr(args, "am1_protected_run_directory", None) else 6000),
    )


def connect_robot(robot: AlohaMini, *, skip_lift_home: bool) -> None:
    robot.connect(home_lift=not skip_lift_home)


def _validate_host_arguments(args):
    parser = make_parser()
    if args.lift_relief and args.lift_readback:
        parser.error("--lift_relief and --lift_readback are mutually exclusive.")
    if args.lift_relief and (
        args.robot_model != "alohamini1" or not args.no_follower or not args.no_cameras or args.skip_lift_home
    ):
        parser.error("--lift_relief requires AM1, --no_follower, --no_cameras, and homing enabled.")
    if args.lift_readback and (
        args.robot_model != "alohamini1"
        or not args.no_follower
        or not args.no_cameras
        or args.skip_lift_home
    ):
        parser.error(
            "--lift_readback requires AM1, --no_follower, --no_cameras, and the owning lift process."
        )



def visible_control_binding(args):
    """Bind the explicit read-only I0 recipe and its immutable selected condition."""
    recipe = os.environ.get("AM1_VISIBLE_CONTROL_RECIPE")
    experiment_file = getattr(args, "am1_selected_experiment_file", None)
    if recipe is None:
        if experiment_file is not None:
            raise ValueError("Selected experiment file requires its explicit protected recipe")
        return None
    if recipe == "physical-live-teleop":
        binding = {"recipe": recipe, "run_id": args.am1_run_id, "host_incarnation": args.am1_host_incarnation}
        if (experiment_file is not None or args.robot_model != "alohamini1" or args.no_follower
                or not getattr(args, "no_cameras", False) or not getattr(args, "am1_protected_run_directory", None)
                or os.environ.get("AM1_SCRIPTED_PREPARE") != "1"
                or any(os.environ.get(key) is not None for key in ("AM1_ELBOW_RECOVERY", "AM1_ELBOW_RECOVERY_RECIPE", "AM1_ELBOW_INTEGRAL"))
                or os.environ.get("AM1_SHOULDER_INTEGRAL_TEST") == "1"):
            raise ValueError("Whole-robot teleop requires its exact protected owner")
        if any(type(binding[key]) is not str or str(UUID(binding[key])) != binding[key]
               for key in ("run_id", "host_incarnation")):
            raise ValueError("Whole-robot teleop requires canonical protected identities")
        return binding
    from examples.alohamini.am1_session_contract import (
        LEFT_ELBOW_POSITIVE_CONTROL,
        LIVE_ELBOW_SPECS,
        SelectedElbowExperiment,
        uuid_text,
    )

    if (recipe not in {LEFT_ELBOW_POSITIVE_CONTROL.recipe_name, SelectedElbowExperiment.recipe_name, *LIVE_ELBOW_SPECS}
        or os.environ.get("AM1_SCRIPTED_PREPARE") != "1"
        or os.environ.get("AM1_SELECTED_JOINT_EVIDENCE") != "1"
        or os.environ.get("AM1_ELBOW_RECOVERY") is not None
        or os.environ.get("AM1_ELBOW_RECOVERY_RECIPE") is not None
        or os.environ.get("AM1_SHOULDER_INTEGRAL_TEST") == "1"
        or args.robot_model != "alohamini1" or args.no_follower or not getattr(args,"no_cameras",False)
        or not getattr(args, "am1_protected_run_directory", None)
        or uuid_text(args.am1_run_id) != args.am1_run_id
        or uuid_text(args.am1_host_incarnation) != args.am1_host_incarnation):
        raise ValueError("Visible control requires its exact protected recipe and read-only I0 owner")
    binding = {"run_id":args.am1_run_id,"host_incarnation":args.am1_host_incarnation,"recipe":recipe}
    if recipe == LEFT_ELBOW_POSITIVE_CONTROL.recipe_name or recipe in LIVE_ELBOW_SPECS:
        if experiment_file is not None:
            raise ValueError("Historical positive control cannot inherit a selected experiment")
        return binding
    path = Path(experiment_file) if experiment_file is not None else None
    directory = Path(args.am1_protected_run_directory)
    if (path is None or not path.is_absolute() or path.name != "selected-experiment.json"
            or path.parent.resolve() != directory.resolve() or path.is_symlink()):
        raise ValueError("Selected experiment requires its exact private run-owned file")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600
                or not hasattr(os, "geteuid") or metadata.st_uid != os.geteuid()
                or not 0 < metadata.st_size <= 4096):
            raise ValueError("Selected experiment file must be a bounded current-owner-only regular file")
        data = os.read(descriptor, 4097)
    finally:
        os.close(descriptor)
    definition = SelectedElbowExperiment.from_payload(json.loads(data))
    canonical = json.dumps(definition.payload, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    if data != canonical:
        raise ValueError("Selected experiment file must contain its exact canonical finite JSON")
    args._am1_selected_elbow_experiment = definition
    return dict(binding, experiment_sha256=definition.sha256)


def right_p20_trial_binding(args, visible_binding):
    path = getattr(args, "am1_right_p20_trial_file", None)
    if path is None:
        return None
    from .am1_elbow_integral import RIGHT_P20_RECIPE, PRACTICAL_RECIPE, load_right_p20_binding
    recipe = None if visible_binding is None else visible_binding.get("recipe")
    expected = dict(run_id=args.am1_run_id, host_incarnation=args.am1_host_incarnation, recipe=recipe)
    if (visible_binding != expected or args.robot_model != "alohamini1" or args.no_follower or not args.no_cameras
            or recipe not in (RIGHT_P20_RECIPE, PRACTICAL_RECIPE)
            or not getattr(args, "am1_protected_run_directory", None)
            or os.environ.get("AM1_SCRIPTED_PREPARE") != "1" or os.environ.get("AM1_SELECTED_JOINT_EVIDENCE") != "1"
            or os.environ.get("AM1_ELBOW_RECOVERY") is not None or os.environ.get("AM1_SHOULDER_INTEGRAL_TEST") == "1"):
        raise ValueError("Right P20 file requires its exact isolated protected right-live owner")
    binding = load_right_p20_binding(path, args.am1_protected_run_directory, args.am1_run_id, args.am1_host_incarnation)
    if (binding["kind"] == "practical13-right") != (recipe == PRACTICAL_RECIPE):
        raise ValueError("Right trial file differs from its selected recipe")
    return binding


def elbow_recovery_binding(args):
    """Only a reviewed protected recipe can select the owner-scoped ID3 trial."""
    integral = os.environ.get("AM1_ELBOW_RECOVERY")
    recipe = os.environ.get("AM1_ELBOW_RECOVERY_RECIPE")
    if integral is None and recipe is None:
        return None
    from examples.alohamini.am1_session_contract import ELBOW_RECOVERY_DEFINITIONS

    definition = ELBOW_RECOVERY_DEFINITIONS.get(recipe)
    if (
        definition is None or integral != f"I{definition.integral}"
        or os.environ.get("AM1_SCRIPTED_PREPARE") != "1"
        or os.environ.get("AM1_SELECTED_JOINT_EVIDENCE") != "1"
        or args.robot_model != "alohamini1" or args.no_follower or not args.no_cameras
        or not getattr(args, "am1_protected_run_directory", None)
        or not args.am1_run_id or not args.am1_host_incarnation
    ):
        raise ValueError("Elbow recovery requires an exact protected recipe and its existing preparation/evidence gates")
    identity = {"run_id": str(UUID(args.am1_run_id)), "host_incarnation": str(UUID(args.am1_host_incarnation))}
    if identity != {"run_id": args.am1_run_id, "host_incarnation": args.am1_host_incarnation}:
        raise ValueError("Elbow recovery requires canonical protected run identities")
    return {**identity, "recipe": recipe, "integral": definition.integral}


def _run_host(args):
    _validate_host_arguments(args)
    visible_binding = visible_control_binding(args)
    teleop = visible_binding is not None and visible_binding["recipe"] == "physical-live-teleop"
    recovery_binding = elbow_recovery_binding(args)
    right_p20_binding = right_p20_trial_binding(args, visible_binding)
    practical_config = live_elbow_config(None if visible_binding is None else visible_binding["recipe"])
    if right_p20_binding is not None and right_p20_binding["kind"] == "practical13-right":
        from examples.alohamini.am1_session_contract import PracticalElbowConfig
        practical_config = PracticalElbowConfig.from_payload(right_p20_binding["request"]["config"])
    if (visible_binding is not None and visible_binding["recipe"] == "physical-live-right-elbow-practical"
            and practical_config is None):
        raise ValueError("Practical owner requires its fresh consumed trial configuration")

    logging.info("Configuring AlohaMini")
    robot_config = make_robot_config(args)
    if args.no_follower:
        logging.info("no_follower mode: follower arms will not connect, only base and lift operate.")
    robot = AlohaMini(robot_config)
    args._am1_robot = robot
    # Every admitted AM1 owner needs actual cleanup proof, including legacy CLI.
    robot._am1_protected_cleanup = args.robot_model == "alohamini1"
    if right_p20_binding is not None:
        robot._am1_right_p20_trial_binding = right_p20_binding
        robot._am1_right_p20_trial_directory = args.am1_protected_run_directory
    if practical_config is not None:
        robot._am1_practical_config = practical_config
    left_shoulder_evidence = AM1LeftShoulderEvidence(
        enabled=(os.environ.get("AM1_LEFT_SHOULDER_EVIDENCE") == "1"
                 and args.robot_model == "alohamini1" and not args.no_follower),
    )
    robot._am1_left_shoulder_evidence_enabled = left_shoulder_evidence.enabled
    selected_evidence = AM1SelectedJointEvidence(
        enabled=(os.environ.get("AM1_SELECTED_JOINT_EVIDENCE") == "1"
                 and args.robot_model == "alohamini1" and not args.no_follower
                 and args.am1_run_id is not None and args.am1_host_incarnation is not None),
    )
    robot._am1_selected_joint_evidence_enabled = selected_evidence.enabled
    if recovery_binding is not None:
        robot._am1_elbow_recovery_binding = recovery_binding
    if teleop:
        robot._am1_teleop_binding = visible_binding
    elif visible_binding is not None:
        robot._am1_visible_control_binding = visible_binding
        robot._am1_visible_control_evidence = selected_evidence
        experiment = getattr(args, "_am1_selected_elbow_experiment", None)
        if experiment is not None:
            robot._am1_selected_elbow_experiment = experiment
            robot._am1_selected_joint_experiment = experiment.summary()

    if args.lift_relief:
        from .lift_relief import run_lift_relief

        raise SystemExit(run_lift_relief(robot))
    if args.lift_readback:
        from .lift_relief import run_lift_readback

        raise SystemExit(run_lift_readback(robot))

    primary_error: BaseException | None = None
    interrupted_motor_io = False
    keyboard_interrupted = False
    try:
        logging.info("Connecting AlohaMini")
        connect_robot(robot, skip_lift_home=args.skip_lift_home)

        logging.info("Starting HostAgent")
        host_config = make_host_config(args)
        host = AlohaMiniHost(host_config)
    except BaseException as error:
        args._am1_finishing = True
        args._am1_cleanup_errors = []
        try:
            if robot.is_connected:
                robot.disconnect(recover_interrupted_bus_io=True)
            elif args.robot_model == "alohamini1":
                # A partially connected startup still owns its open buses.
                issues = robot._safe_shutdown(close_buses=True, recover_interrupted_bus_io=True)
                if issues:
                    raise RuntimeError("AM1 startup cleanup issues: " + "; ".join(issues))
        except BaseException as cleanup_error:
            args._am1_cleanup_first_error = cleanup_error
            args._am1_cleanup_errors.append(f"robot disconnect: {type(cleanup_error).__name__}: {cleanup_error}")
            error.add_note(
                "robot disconnect also failed: "
                f"{type(cleanup_error).__name__}: {cleanup_error}"
            )
        try:
            selected_evidence.emit_pending_fault(robot)
        except BaseException as reporting_error:
            args._am1_cleanup_errors.append(
                f"pending selected joint fault: {type(reporting_error).__name__}: {reporting_error}"
            )
            error.add_note(f"pending selected joint fault also failed: {type(reporting_error).__name__}: {reporting_error}")
        raise

    command_state = HostCommandState(
        watchdog_timeout_ms=host.watchdog_timeout_ms,
        diagnostics_enabled=args.profile_cadence,
    )
    local_control = (
        AM1LocalControl(
            run_id=args.am1_run_id, host_incarnation=args.am1_host_incarnation,
            selected_evidence=selected_evidence,
            live=teleop or visible_binding is not None and live_elbow_spec(visible_binding["recipe"]) is not None,
            live_recipe=AM1_LIVE_RECIPE if visible_binding is None else visible_binding["recipe"],
            practical_config=practical_config,
        )
        if args.robot_model == "alohamini1" and not args.no_follower
        else None
    )
    # Only the unified supervisor opts in. Direct Arms/Local commands remain
    # unmarked and can stay "ready" during live use; state alone is insufficient.
    sync_shoulder_readback = os.environ.get("AM1_SYNC_SHOULDER_READBACK") == "1"
    loop_timing = (
        AM1HostLoopTiming()
        if args.robot_model == "alohamini1" and getattr(robot, "_lift_operation", None) is not None
        else None
    )
    fault_loop_context = None
    fault_loop_context_error = None
    routine_writer = None
    routine_report = print
    exit_message = None
    logging.info("Waiting for commands...")

    try:
        if practical_config is not None or teleop:
            routine_writer = AM1RoutineRecordWriter()
            selected_evidence.emit = routine_writer.emit_selected
            routine_report = routine_writer.submit_line
            robot._am1_routine_report = routine_report
            if teleop:
                # Keep each complete bus receipt below the existing record bound.
                for side in ("left", "right"):
                    routine_writer.submit("[AM1 TELEOP SETTINGS] ", {
                        "binding": robot._am1_teleop_binding,
                        "bus": side,
                        "before": robot._am1_teleop_settings[side],
                        "active": robot._am1_teleop_active_settings[side],
                    })
                routine_writer.submit("[AM1 TELEOP REST] ", {
                    "binding": robot._am1_teleop_binding,
                    "acquisitions": robot._am1_teleop_rest_evidence,
                })
        # One final same-owner startup boundary before ordinary loop timing.
        # Only a due genuine read can refresh the retained original thermal slots.
        if (
            args.robot_model == "alohamini1" and getattr(robot, "_lift_operation", None) is not None
        ):
            robot._refresh_am1_startup_lift_feedback()
        # Business logic
        start = time.perf_counter()
        duration = 0
        timing_report_start_t = start
        timing_loop_count = 0
        timing_totals_ms: dict[str, float] = {}
        timing_command_count = 0
        action_timing_totals_ms: dict[str, float] = {}
        cadence_report_start_t = time.monotonic()
        lift_diagnostics_report_start_t = cadence_report_start_t

        while duration < host.connection_time_s:
            if routine_writer is not None:
                routine_writer.raise_if_failed()
            if local_control is not None and not local_control.check_live(robot):
                break
            loop_start_t = time.perf_counter()
            if loop_timing is not None:
                loop_timing.begin(loop_start_t)
            command_received = False
            data = None
            # One grouped lift transaction on the existing owning thread before
            # any ordinary action. No confirmation wait, worker, or second socket.
            lift_operation = getattr(robot, "_lift_operation", None)
            if lift_operation is not None:
                lift_operation.poll(defer_sample_log=True)
            if loop_timing is not None:
                loop_timing.mark("command", time.perf_counter())
            try:
                msg = host.zmq_cmd_socket.recv_string(zmq.NOBLOCK)
                command_received_t = time.monotonic()
                command_received_wall_time_ns = command_state.capture_wall_time_ns()
                data = dict(json.loads(msg))
                #print(f"Received action: {data}")   # debug
                if local_control is not None:
                    command_received = local_control.apply(robot, data)
                else:
                    robot.send_action(data)
                    command_received = True
                if command_received:
                    command_state.record_command(
                        robot.logs.get("action_diagnostics", {}),
                        received_at=command_received_t,
                        received_wall_time_ns=command_received_wall_time_ns,
                    )
            except zmq.Again:
                pass
            except Exception as e:
                if lift_operation is not None:
                    # Do not let the legacy malformed-command handler swallow a
                    # latched lift fault (including an unsuccessful motor write).
                    lift_operation.raise_if_faulted()
                if local_control is not None and (
                    local_control.state != "ready"
                    or (isinstance(data, dict) and AM1_LOCAL_CONTROL_KEY in data)
                ):
                    raise
                logging.exception("Message fetching failed: %s", e)
            command_done_t = time.perf_counter()
            if loop_timing is not None:
                loop_timing.mark("watchdog", command_done_t)

            if command_state.watchdog_due():
                logging.warning(
                    f"Command not received for more than {host.watchdog_timeout_ms} milliseconds. Stopping robot motion."
                )
                if local_control is not None:
                    local_control.watchdog_stop(robot)
                else:
                    robot.stop_motion()

            if local_control is not None and not local_control.check_live(robot):
                break

            if loop_timing is not None:
                loop_timing.mark("robot_observation", time.perf_counter())
            read_started_at = time.monotonic()
            last_observation = robot.get_observation()
            acquired_at = time.monotonic()
            if local_control is not None:
                local_control.annotate(last_observation, acquired_at=acquired_at)
                if local_control.binding is not None:
                    last_observation[AM1_LOCAL_FEEDBACK_KEY]["read_started_at_monotonic_s"] = read_started_at
                selected_evidence.guard_recovery(robot, local_control, last_observation)
                selected_evidence.publish(robot, local_control, last_observation)
            observation_done_t = time.perf_counter()
            if loop_timing is not None:
                loop_timing.mark("observation_response", observation_done_t)

            # Consume at most one request credit per Host loop. Draining all pending
            # requests here would collapse the client's sliding window back into
            # stop-and-wait behavior because the discarded tokens never receive replies.
            request_identity = None
            request_token = None
            try:
                request_parts = host.zmq_observation_socket.recv_multipart(flags=zmq.NOBLOCK)
                request_identity = request_parts[0]
                request_token = request_parts[-1]
            except zmq.Again:
                pass
            request_poll_done_t = time.perf_counter()

            encode_done_t = request_poll_done_t
            if request_identity is not None and request_token is not None:
                observation_parts = build_observation_multipart(last_observation, robot.cameras.keys())
                encode_done_t = time.perf_counter()
                try:
                    host.zmq_observation_socket.send_multipart(
                        [request_identity, request_token, *observation_parts], flags=zmq.NOBLOCK
                    )
                except zmq.Again:
                    if routine_writer is None:
                        logging.info("Dropping observation response, client is not ready")
                    else:
                        routine_report("Dropping observation response, client is not ready")
            response_send_done_t = time.perf_counter()
            if loop_timing is not None:
                loop_timing.mark("sample_log", response_send_done_t)

            # Routine raw logging must not consume the five-slot freshness
            # margin between this tick's grouped read and action/observation use.
            # A slow sink still counts toward the next poll's unchanged deadline.
            if lift_operation is not None:
                if routine_writer is None:
                    lift_operation.emit_pending_sample()
                else:
                    routine_writer.emit_lift_samples(lift_operation)

            # Bounded sparse readbacks share the owner after ordinary control and
            # response delivery. Their full time still counts toward next poll.
            if local_control is not None:
                selected_evidence.report(robot, local_control, last_observation)

            lift_diagnostics_done_t = time.perf_counter()
            if loop_timing is not None:
                loop_timing.mark("lift_diagnostics", lift_diagnostics_done_t)
            lift_diagnostics_now = time.monotonic()
            if (
                args.profile_lift_diagnostics
                and lift_diagnostics_now - lift_diagnostics_report_start_t >= 1.0
            ):
                routine_report(format_lift_diagnostic_report(robot, last_observation), flush=True)
                lift_diagnostics_report_start_t = lift_diagnostics_now
                lift_diagnostics_done_t = time.perf_counter()
            if loop_timing is not None:
                loop_timing.mark("sleep", time.perf_counter())

            # Ensure a short sleep to avoid overloading the CPU.
            elapsed = lift_diagnostics_done_t - loop_start_t

            time.sleep(max(1 / host.max_loop_freq_hz - elapsed, 0))
            loop_done_t = time.perf_counter()
            if loop_timing is not None:
                loop_timing.mark("reports", loop_done_t)

            loop_timings_ms = {
                "command": (command_done_t - loop_start_t) * 1e3,
                "robot_observation": (observation_done_t - command_done_t) * 1e3,
                "request_poll": (request_poll_done_t - observation_done_t) * 1e3,
                "jpeg_encode": (encode_done_t - request_poll_done_t) * 1e3,
                "response_send": (response_send_done_t - encode_done_t) * 1e3,
                "lift_diagnostics": (lift_diagnostics_done_t - response_send_done_t) * 1e3,
                "sleep": (loop_done_t - lift_diagnostics_done_t) * 1e3,
                "loop": (loop_done_t - loop_start_t) * 1e3,
                **robot.logs.get("observation_timing_ms", {}),
            }
            for name, value_ms in loop_timings_ms.items():
                timing_totals_ms[name] = timing_totals_ms.get(name, 0.0) + value_ms
            timing_loop_count += 1
            if command_received:
                for name, value_ms in robot.logs.get("action_timing_ms", {}).items():
                    action_timing_totals_ms[name] = action_timing_totals_ms.get(name, 0.0) + value_ms
                timing_command_count += 1

            timing_elapsed_s = loop_done_t - timing_report_start_t
            if args.profile_timing and timing_elapsed_s >= 1.0:
                averages = {
                    name: total_ms / timing_loop_count for name, total_ms in timing_totals_ms.items()
                }
                camera_text = " ".join(
                    f"{name}={value:.1f}"
                    for name, value in averages.items()
                    if name.startswith("camera_")
                )
                routine_report(
                    f"[HOST TIMING avg ms/loop] Hz={timing_loop_count / timing_elapsed_s:.1f} "
                    f"cmd={averages['command']:.1f} robot_obs={averages['robot_observation']:.1f} "
                    f"left={averages.get('left_arm', 0.0):.1f} base={averages.get('base', 0.0):.1f} "
                    f"right={averages.get('right_arm', 0.0):.1f} lift={averages.get('lift', 0.0):.1f} "
                    f"currents={averages.get('currents', 0.0):.1f} {camera_text} "
                    f"jpeg={averages['jpeg_encode']:.1f} send={averages['response_send']:.1f} "
                    f"sleep={averages['sleep']:.1f} loop={averages['loop']:.1f}",
                    flush=True,
                )
                if timing_command_count:
                    action_averages = {
                        name: total_ms / timing_command_count
                        for name, total_ms in action_timing_totals_ms.items()
                    }
                    routine_report(
                        f"[HOST ACTION avg ms/command] n={timing_command_count} "
                        f"prepare={action_averages.get('action_prepare', 0.0):.1f} "
                        f"lift={action_averages.get('action_lift', 0.0):.1f} "
                        f"relative={action_averages.get('action_relative_limit', 0.0):.1f} "
                        f"left_gripper_limit={action_averages.get('action_left_gripper_limit', 0.0):.1f} "
                        f"left_joint_limit={action_averages.get('action_left_joint_limit', 0.0):.1f} "
                        f"right_gripper_limit={action_averages.get('action_right_gripper_limit', 0.0):.1f} "
                        f"right_joint_limit={action_averages.get('action_right_joint_limit', 0.0):.1f} "
                        f"left_write={action_averages.get('action_left_write', 0.0):.1f} "
                        f"right_write={action_averages.get('action_right_write', 0.0):.1f} "
                        f"base_write={action_averages.get('action_base_write', 0.0):.1f} "
                        f"total={action_averages.get('action_total', 0.0):.1f}",
                        flush=True,
                    )
                timing_report_start_t = loop_done_t
                timing_loop_count = 0
                timing_totals_ms.clear()
                timing_command_count = 0
                action_timing_totals_ms.clear()

            cadence_now = time.monotonic()
            if args.profile_cadence and cadence_now - cadence_report_start_t >= 1.0:
                if routine_writer is None:
                    print_cadence_report(command_state)
                else:
                    print_cadence_report(command_state, emit=routine_report)
                if sync_shoulder_readback and local_control is not None and local_control.state == "ready":
                    print_startup_shoulder_report(robot, command_state, last_observation)
                if left_shoulder_evidence.enabled and loop_timing is not None:
                    loop_timing.mark("left_shoulder_evidence", time.perf_counter())
                left_shoulder_evidence.report(robot, command_state)
                if left_shoulder_evidence.enabled and loop_timing is not None:
                    loop_timing.mark("report_after_left_shoulder_evidence", time.perf_counter())
                cadence_report_start_t = cadence_now

            duration = time.perf_counter() - start
            if loop_timing is not None:
                loop_timing.finish(time.perf_counter())
        if local_control is not None and local_control.live and local_control.state == "stopped":
            exit_message = "Owned Live Stop received."
        elif (local_control is not None and local_control.live
              and local_control.live_snapshot().get("state") == "budget_expired"):
            exit_message = "Live time budget expired."
        else:
            exit_message = "Cycle time reached."

    except KeyboardInterrupt:
        interrupted_motor_io = True
        keyboard_interrupted = True
    except BaseException as error:
        interrupted_motor_io = True
        primary_error = error
        try:
            selected_evidence.enrich_fault(robot, local_control)
        except BaseException as context_error:
            robot._am1_selected_joint_fault_capture_error = type(context_error).__name__
        if loop_timing is not None:
            # Capture BEFORE cleanup, attach AFTER it. Cleanup time must not be
            # misreported as poll/log/observation delay, or diagnostic I/O delay zero/off.
            try:
                fault_loop_context = loop_timing.snapshot(time.perf_counter())
                sample = getattr(getattr(robot, "_lift_operation", None), "last_record", None) or {}
                # last_record is cached before some operational guards; it may
                # contain the rejected sample, not the last accepted one.
                fault_loop_context["last_cached_lift_sample_monotonic_s"] = sample.get("sample_monotonic_s")
            except BaseException as context_error:
                fault_loop_context_error = type(context_error).__name__
    finally:
        args._am1_finishing = True
        cleanup_errors: list[tuple[str, BaseException]] = []
        try:
            if robot.is_connected:
                robot.disconnect(recover_interrupted_bus_io=interrupted_motor_io)
        except BaseException as error:
            cleanup_errors.append(("robot disconnect", error))
        try:
            host.disconnect()
        except BaseException as error:
            cleanup_errors.append(("host disconnect", error))

        # Every exit message is after zero/off and socket cleanup. A blocked
        # or failed sink must never delay the protective motor requests.
        try:
            if exit_message is not None:
                routine_report(exit_message)
            if keyboard_interrupted:
                routine_report("Keyboard interrupt received. Exiting...")
            routine_report("Shutting down AlohaMini Host.")
            if args.profile_cadence:
                if routine_writer is None:
                    print_cadence_report(command_state)
                else:
                    print_cadence_report(command_state, emit=routine_report)
        except BaseException as error:
            cleanup_errors.append(("shutdown reporting", error))

        try:
            selected_evidence.emit_pending_fault(robot)
        except BaseException as error:
            cleanup_errors.append(("pending selected joint fault", error))

        # A consumer failure or Ctrl+C can bypass the normal end-of-tick emit.
        # Preserve that genuine sample, but only after motor/socket cleanup:
        # a failed or slow log sink must not delay the zero/off requests.
        lift_operation = getattr(robot, "_lift_operation", None)
        if lift_operation is not None:
            try:
                if routine_writer is None:
                    lift_operation.emit_pending_sample()
                else:
                    routine_writer.emit_lift_samples(lift_operation)
            except BaseException as error:
                cleanup_errors.append(("pending lift sample", error))
        if routine_writer is not None:
            try:
                routine_writer.close(timeout=2.0)
            except BaseException as error:
                cleanup_errors.append(("routine log writer drain", error))

        args._am1_cleanup_errors = [
            f"{operation}: {type(error).__name__}: {error}" for operation, error in cleanup_errors
        ]
        if primary_error is not None:
            if fault_loop_context is not None:
                try:
                    primary_error.add_note("AM1 host loop timing context: " + json.dumps(
                        fault_loop_context, allow_nan=False, separators=(",", ":"),
                    ))
                except BaseException as context_error:
                    fault_loop_context_error = type(context_error).__name__
            if fault_loop_context_error is not None:
                primary_error.add_note("AM1 host loop timing context unavailable: " + fault_loop_context_error)
            for operation, error in cleanup_errors:
                primary_error.add_note(
                    f"{operation} also failed: {type(error).__name__}: {error}"
                )
            raise primary_error
        if cleanup_errors:
            _, error = cleanup_errors[0]
            args._am1_cleanup_first_error = error
            for later_operation, later_error in cleanup_errors[1:]:
                error.add_note(
                    f"{later_operation} also failed: {type(later_error).__name__}: {later_error}"
                )
            raise error

    logging.info("Finished AlohaMini cleanly")
def main():
    parser = make_parser()
    args = parser.parse_args()
    _validate_host_arguments(args)
    protected = args.am1_protected_run_directory is not None
    protected_fields = (args.am1_run_id, args.am1_host_incarnation, args.am1_cleanup_receipt)
    if protected != all(value is not None for value in protected_fields) or (
        not protected and any(value is not None for value in protected_fields)
    ):
        parser.error("AM1 protected transport requires run ID, host incarnation and cleanup receipt together")
    if protected:
        if (args.robot_model != "alohamini1" or args.no_follower or not args.no_cameras
            or args.skip_lift_home or args.lift_relief or args.lift_readback
            or args.max_relative_target is None or not 0 < args.max_relative_target <= 20
            or not 0 < args.max_loop_freq_hz <= 30 or not 0 < args.am1_host_runtime_s <= 480):
            parser.error("AM1 protected backend requires the reviewed AM1 Local camera-free bounded host")
        directory = Path(args.am1_protected_run_directory)
        if not directory.is_absolute():
            parser.error("AM1 protected run directory must be absolute")
        _private_directory(directory)
        if Path(args.am1_cleanup_receipt).parent.resolve() != directory.resolve():
            parser.error("AM1 cleanup receipt must be inside this protected run directory")
        if Path(args.am1_cleanup_receipt).exists():
            parser.error("AM1 cleanup receipt must be a fresh run-owned path")
        try:
            args.am1_run_id = str(UUID(args.am1_run_id))
            args.am1_host_incarnation = str(UUID(args.am1_host_incarnation))
        except ValueError:
            parser.error("AM1 protected run/host identities must be UUIDs")
    admission = None
    first_error = None
    original_handlers = {}
    signal_seen = False

    def stop_once(signum, frame):
        nonlocal signal_seen
        del frame
        robot = getattr(args, "_am1_robot", None)
        if getattr(args, "_am1_finishing", False) or getattr(robot, "_am1_shutdown_in_progress", False):
            return  # Cooperative cleanup already owns zero/off; do not interrupt it.
        if not signal_seen:
            signal_seen = True
            args._am1_stop_signal = signal.Signals(signum).name
            args._am1_stop_interruption = KeyboardInterrupt()
            raise args._am1_stop_interruption

    try:
        if args.robot_model == "alohamini1":
            canonical_directory = Path.home() / ".local/state/am1-session"
            if Path(args.am1_physical_state_directory).resolve() != canonical_directory.resolve():
                raise RuntimeError("AM1 physical admission must use canonical ~/.local/state/am1-session")
            admission = AM1PhysicalAdmission(
                args.am1_physical_state_directory, inherited_fd=args.am1_admission_fd,
            )
            args._am1_physical_run_id = args.am1_run_id or str(uuid4())
            args._am1_physical_host_incarnation = args.am1_host_incarnation or str(uuid4())
            args._am1_physical_cleanup_receipt = args.am1_cleanup_receipt or str(
                canonical_directory / f"physical-cleanup-{args._am1_physical_host_incarnation}.json"
            )
            # Retain legacy control/CLI identities as None; these internal UUIDs
            # bind only durable physical ownership and the actual cleanup receipt.
            admission.claim_run(
                run_id=args._am1_physical_run_id,
                host_incarnation=args._am1_physical_host_incarnation,
                scope="protected" if protected else "legacy",
            )
            args._am1_claim_started = True
            for signum in (signal.SIGINT, signal.SIGTERM):
                original_handlers[signum] = signal.signal(signum, stop_once)
        _run_host(args)
    except BaseException as error:
        first_error = error
        raise
    finally:
        try:
            if admission is not None and getattr(args, "_am1_claim_started", False):
                robot = getattr(args, "_am1_robot", None)
                errors = list(getattr(robot, "_am1_shutdown_evidence", {}).get("cleanup_errors", []))
                errors.extend(getattr(args, "_am1_cleanup_errors", []))
                # Readback alone cannot erase a disconnect/reporting failure.
                try:
                    receipt = write_am1_cleanup_receipt(args, robot, first_error, errors)
                    admission.finish_run(receipt)
                except BaseException as receipt_error:
                    if first_error is None:
                        raise
                    first_error.add_note(f"Physical cleanup receipt also failed: {type(receipt_error).__name__}: {receipt_error}")
        finally:
            for signum, handler in original_handlers.items():
                signal.signal(signum, handler)
            if admission is not None:
                admission.close()


if __name__ == "__main__":
    main()
