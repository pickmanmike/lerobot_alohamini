"""Bounded, source-labelled AM1 console data; never owns a motor reader."""

from __future__ import annotations

import math
from collections import deque
from typing import Any


ARM_JOINTS = (
    "shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper",
)
ARM_KEYS = frozenset(f"arm_{side}_{joint}.pos" for side in ("left", "right") for joint in ARM_JOINTS)
BODY_UNITS = {"x.vel": "m/s", "y.vel": "m/s", "theta.vel": "deg/s",
              "lift_axis.vel": "raw velocity", "lift_axis.height_mm": "mm"}
SYSTEM_UNITS = {"cpu_percent": "%", "memory_percent": "%", "cpu_temp_c": "C",
                "uptime_s": "s", "storage_free_bytes": "bytes", "throttled_raw": "hex"}
CAMERA_ROLES = ("forward", "backward", "chest", "wrist_left", "wrist_right")
FIELD_STATES = frozenset({"Live", "Snapshot", "Not sampled", "Unavailable"})


def field(value: Any, unit: str, source: str, acquired_at: int | None, state: str) -> dict[str, Any]:
    if state not in FIELD_STATES:
        raise ValueError(f"invalid AM1 console field state {state!r}")
    if acquired_at is not None and (type(acquired_at) is not int or acquired_at < 0):
        raise ValueError("acquisition time must be an integer wall timestamp")
    return {"value": value, "unit": unit, "source": source, "acquired_at_ns": acquired_at, "state": state}


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


class ConsoleProgress:
    """One latest display record; never grants readiness or controls duration.

    Native and server are Windows processes using the system-wide QPC-backed
    Python monotonic clock. Pi monotonic times are deliberately not accepted.
    Browser clients receive a remaining value and interpolate on their own clock.
    """

    STAGES = ("connections", "cameras", "lift_home", "lift_relief",
              "leader_preparation", "arm_sync", "final_readiness")

    def __init__(self) -> None:
        self.startup: dict[str, Any] | None = None
        self.started_at: float | None = None
        self.timing: dict[str, float] | None = None
        self.sync_plan_origin: float | None = None
        self.stopped = False

    def update(self, event: dict[str, Any], *, received_at: float) -> None:
        kind = event.get("event")
        if kind in {"stop_requested", "client_exited", "cleanup", "session_complete", "session_error"}:
            self.stopped = True
        if kind == "startup_progress" and not self.stopped:
            stage = event.get("stage")
            if stage not in self.STAGES:
                return
            step = self.STAGES.index(stage) + 1
            origin = _numeric(event.get("plan_started_at"))
            if origin is not None and self.sync_plan_origin is not None and origin < self.sync_plan_origin:
                return
            new_plan = stage == "arm_sync" and origin is not None and (
                self.sync_plan_origin is None or origin > self.sync_plan_origin)
            if self.startup is not None and step < self.startup["step"] and not new_plan:
                return  # Sequential transport may deliver an older producer later.
            if self.startup is None or step != self.startup["step"] or new_plan:
                self.started_at = received_at
            if origin is not None:
                self.sync_plan_origin = origin
            self.startup = {"stage": stage, "step": step, "total": 7,
                            "source": str(event.get("source", "owning process event"))[:100]}
            for key in ("frames_sent", "frame_count", "fps", "remaining_estimate_s"):
                value = _numeric(event.get(key))
                if value is not None and value >= 0:
                    self.startup[key] = value
            self.startup["waiting"] = event.get("waiting") is True
        if kind == "live_timing" and not self.stopped:
            values = {key: _numeric(event.get(key)) for key in
                      ("live_started_at", "deadline", "sampled_at", "duration_s")}
            if (event.get("clock") != "windows_monotonic" or any(v is None for v in values.values())
                    or not 1 <= values["duration_s"] <= 1800
                    or not 0 <= values["live_started_at"] <= values["sampled_at"] <= received_at
                    or abs(values["deadline"] - values["live_started_at"] - values["duration_s"]) > 1e-6):
                return
            if self.timing is not None and (
                values["live_started_at"] != self.timing["live_started_at"]
                or values["deadline"] != self.timing["deadline"]
                or values["sampled_at"] < self.timing["sampled_at"]
            ):
                return  # Resume/reordered messages cannot create a new origin.
            self.timing = values

    def snapshot(self, *, now: float) -> dict[str, Any]:
        startup = None if self.startup is None else {
            **self.startup, "elapsed_s": max(0.0, now - self.started_at),
        }
        timing: dict[str, Any] = {"state": "Unavailable", "remaining_s": None}
        if self.timing is not None:
            age = max(0.0, now - self.timing["sampled_at"])
            remaining = max(0.0, self.timing["deadline"] - now)
            timing = {**self.timing, "remaining_s": remaining, "age_s": age,
                      "state": "Stopped" if self.stopped else "Stale" if age > 2.0 else
                               "Elapsed" if remaining == 0 else "Current",
                      "source": "native live admission and enforced deadline (Windows monotonic)"}
        return {"startup": startup, "live_timing": timing}


class ConsoleSnapshot:
    """Cache one latest measurement per identity, not a growing telemetry history."""

    def __init__(self, *, max_events: int = 64):
        self.events: deque[dict[str, Any]] = deque(maxlen=max(1, min(max_events, 128)))
        self.servos: dict[str, dict[str, dict[str, Any]]] = {}
        for side in ("left", "right"):
            for joint in ARM_JOINTS:
                key = f"arm_{side}_{joint}.pos"
                identity = f"follower.{side}_bus.{key}"
                unit = "normalized 0..100" if joint == "gripper" else "normalized -100..100"
                self.servos[identity] = {
                    "position": field(None, unit, "Pi follower observation", None, "Not sampled"),
                    "target": field(None, unit, "Windows native action sent to Pi", None, "Not sampled"),
                    "current": field(None, "mA", "Pi motor record", None, "Not sampled"),
                    "temperature": field(None, "C", "Pi motor record", None, "Not sampled"),
                    "status": field(None, "status", "Pi motor record", None, "Not sampled"),
                }
        self.body = {key: field(None, unit, "Pi follower observation", None, "Not sampled")
                     for key, unit in BODY_UNITS.items()}
        self.body_targets = {key: field(None, unit, "Windows native action sent to Pi", None, "Not sampled")
                             for key, unit in BODY_UNITS.items() if key != "lift_axis.height_mm"}
        self.system = {key: field(None, unit, "Pi supervisor", None, "Unavailable")
                       for key, unit in SYSTEM_UNITS.items()}
        self.system_sample_pi_wall_ns: int | None = None
        self.cameras = {role: {"state": "Unavailable", "source_state": None, "age_ms": None,
                               "sequence": None, "fps": None, "acquired_at_ns": None}
                        for role in CAMERA_ROLES}
        self.camera_last_request_ns: int | None = None
        self.observation: dict[str, Any] = {"sequence": None, "host_observation_id": None,
                                            "host_state": None, "host_epoch": None,
                                            "acquired_at_ns": None, "age_ms": None}
        self.action: dict[str, Any] = {"sequence": None, "send_interval_ms": None,
                                       "acquired_at_ns": None, "age_ms": None}
        self.phase = "idle"

    def update(self, event: dict[str, Any]) -> None:
        if not isinstance(event, dict) or not isinstance(event.get("event"), str):
            return
        kind = event["event"]
        acquired = event.get("acquired_at_ns")
        if type(acquired) is not int or acquired < 0:
            acquired = None
        if kind in {"live_sample", "host_feedback"} and acquired is not None and (
            self.observation["acquired_at_ns"] is not None and acquired < self.observation["acquired_at_ns"]
        ):
            return
        if kind == "live_sample" and acquired is not None:
            self._live_sample(event, acquired)
            return
        if kind == "action_sent" and acquired is not None:
            self._action_sent(event, acquired)
            return
        if kind == "system_sample" and acquired is not None:
            metrics = event.get("metrics")
            if not isinstance(metrics, dict):
                return
            received = event.get("windows_received_at_ns")
            displayed_at = received if type(received) is int and received >= 0 else acquired
            self.system_sample_pi_wall_ns = acquired
            for key, unit in SYSTEM_UNITS.items():
                value = metrics.get(key)
                if key == "throttled_raw":
                    valid = isinstance(value, str) and value.startswith("0x") and len(value) <= 12
                    normalized = value if valid else None
                else:
                    normalized = _numeric(value)
                if normalized is not None:
                    self.system[key] = field(normalized, unit, "Pi OS sample", acquired, "Snapshot")
                    self.system[key]["windows_received_at_ns"] = displayed_at
                    self.system[key]["age_basis"] = "Windows receipt (lower bound; SSH transit unknown)"
            return
        if kind in {"camera_status", "camera_status_failed"} and acquired is not None:
            if self.camera_last_request_ns is not None and acquired < self.camera_last_request_ns:
                return
            self.camera_last_request_ns = acquired
            if kind == "camera_status_failed":
                for camera in self.cameras.values():
                    camera["state"] = "Unavailable"
                return
            roles = event.get("roles")
            if not isinstance(roles, dict):
                return
            for role in CAMERA_ROLES:
                source = roles.get(role)
                if not isinstance(source, dict):
                    self.cameras[role]["state"] = "Unavailable"
                    continue
                source_state = source.get("state") if source.get("state") in {"fresh", "stale", "unavailable", "missing"} else "unavailable"
                age = _numeric(source.get("age_ms"))
                self.cameras[role] = {
                    "state": source_state,
                    "source_state": source_state,
                    "age_ms": age if age is not None and age >= 0 else None,
                    "sequence": source.get("sequence") if type(source.get("sequence")) is int else None,
                    "fps": _numeric(source.get("fps")), "acquired_at_ns": acquired,
                }
            return
        if kind in {"temperature_warning", "confirmed_motor_fault"}:
            severity = "warning" if kind == "temperature_warning" else "fault"
            self.events.append({"event": kind, "severity": severity,
                                "reason": str(event.get("reason", ""))[:240],
                                "wall_time_ns": event.get("wall_time_ns")})
            return
        if kind == "host_feedback" and acquired is not None:
            self.observation.update(sequence=event.get("observation_sequence"),
                                    host_state=event.get("host_state"), host_epoch=event.get("host_epoch"),
                                    host_observation_id=event.get("host_observation_id"),
                                    acquired_at_ns=acquired)
            if event.get("host_state") != "active":
                self._freeze(motor_only=True)
            return
        if kind in {"session_created", "preflight_passed", "camera_ready", "host_ready",
                    "client_exited", "cleanup", "session_complete", "session_error", "stop_requested"}:
            if kind == "session_created":
                self._freeze()
            if kind in {"session_created", "client_exited", "cleanup", "session_complete", "session_error"}:
                self.servos = {key: value for key, value in self.servos.items() if not key.startswith("leader.")}
            self.phase = kind
            self.events.append({"event": kind, "severity": "status",
                                "wall_time_ns": event.get("wall_time_ns"),
                                "reason": str(event.get("reason", ""))[:240]})

    def _freeze(self, *, motor_only: bool = False) -> None:
        groups = (*self.servos.values(), self.body, self.body_targets)
        for entries in groups if motor_only else (*groups, self.system):
            for item in entries.values():
                if item["state"] == "Live":
                    item["state"] = "Snapshot"
        if not motor_only:
            for camera in self.cameras.values():
                if camera["state"] not in {"Unavailable", "Snapshot"}:
                    camera["state"] = "Snapshot"

    def _live_sample(self, event: dict[str, Any], acquired: int) -> None:
        follower = event.get("follower_positions") or {}
        leader = event.get("leader_positions") or {}
        observed_body = event.get("body_observation") or {}
        if not all(isinstance(values, dict) for values in (follower, leader, observed_body)):
            return
        for key in ARM_KEYS:
            side = "left" if key.startswith("arm_left_") else "right"
            identity = f"follower.{side}_bus.{key}"
            unit = self.servos[identity]["position"]["unit"]
            if (value := _numeric(follower.get(key))) is not None:
                self.servos[identity]["position"] = field(
                    value, unit, "Windows receipt of Pi follower observation", acquired, "Live")
            if (value := _numeric(leader.get(key))) is not None:
                leader_identity = f"leader.{side}_bus.{key}"
                self.servos.setdefault(leader_identity, {})["position"] = field(
                    value, unit, "Windows native leader sample", acquired, "Live")
        for key, unit in BODY_UNITS.items():
            value = _numeric(observed_body.get(key))
            if value is not None:
                self.body[key] = field(value, unit, "Windows receipt of Pi body observation", acquired, "Live")
        self.observation = {"sequence": event.get("observation_sequence"),
                            "host_observation_id": event.get("host_observation_id"),
                            "host_state": event.get("host_state"), "host_epoch": event.get("host_epoch"),
                            "acquired_at_ns": acquired, "age_ms": None}

    def _action_sent(self, event: dict[str, Any], acquired: int) -> None:
        requested = event.get("requested_targets")
        if not isinstance(requested, dict):
            return
        for key in ARM_KEYS:
            if (value := _numeric(requested.get(key))) is None:
                continue
            side = "left" if key.startswith("arm_left_") else "right"
            identity = f"follower.{side}_bus.{key}"
            self.servos[identity]["target"] = field(
                value, self.servos[identity]["position"]["unit"],
                "Windows native action sent to Pi", acquired, "Live")
        for key, previous in self.body_targets.items():
            if (value := _numeric(requested.get(key))) is not None:
                self.body_targets[key] = field(value, previous["unit"],
                                               "Windows native action sent to Pi", acquired, "Live")
        self.action = {"sequence": event.get("action_sequence"),
                       "send_interval_ms": event.get("action_send_interval_ms"),
                       "acquired_at_ns": acquired, "age_ms": None}

    def snapshot(self, *, now_ns: int) -> dict[str, Any]:
        if type(now_ns) is not int or now_ns < 0:
            raise ValueError("snapshot time must be a wall-clock nanosecond timestamp")

        def copy_fields(source: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
            result = {}
            for name, original in source.items():
                item = dict(original)
                reference = item.get("windows_received_at_ns", item.get("acquired_at_ns"))
                item["age_ms"] = round(max(0, now_ns - reference) / 1e6, 3) if reference is not None else None
                terminal = self.phase in {"client_exited", "cleanup", "session_complete", "session_error"}
                if item["state"] == "Live" and (terminal or (item["age_ms"] is not None and item["age_ms"] > 1000)):
                    item["state"] = "Snapshot"
                result[name] = item
            return result

        observation = dict(self.observation)
        acquired = observation["acquired_at_ns"]
        observation["age_ms"] = round(max(0, now_ns - acquired) / 1e6, 3) if acquired is not None else None
        action = dict(self.action)
        acquired = action["acquired_at_ns"]
        action["age_ms"] = round(max(0, now_ns - acquired) / 1e6, 3) if acquired is not None else None
        cameras = {}
        for role, previous in self.cameras.items():
            item = dict(previous)
            acquired = item["acquired_at_ns"]
            if acquired is not None:
                elapsed_ms = max(0, now_ns - acquired) / 1e6
                if item["age_ms"] is not None:
                    item["age_ms"] = round(item["age_ms"] + elapsed_ms, 3)
                if elapsed_ms > 2000 or (item["state"] == "fresh" and
                                         (item["age_ms"] is None or item["age_ms"] > 500)):
                    item["state"] = "Snapshot"
            cameras[role] = item
        return {"servos": {identity: copy_fields(parts) for identity, parts in self.servos.items()},
                "body": copy_fields(self.body), "body_targets": copy_fields(self.body_targets),
                "system": copy_fields(self.system), "system_sample_pi_wall_ns": self.system_sample_pi_wall_ns,
                "cameras": cameras, "observation": observation,
                "action": action, "events": list(self.events), "phase": self.phase}
