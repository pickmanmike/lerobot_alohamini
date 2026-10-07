"""Explicit local virtual-bench observation and bounded recovery policy.

No serial access, browser lease renewal, motor command, or gate approval occurs here.
The caller owns hold/stop operations and the native runtime still qualifies resumption.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

CAUSES = frozenset({"bench required coverage", "state-request-failed"})
ROLES = frozenset({"forward", "backward", "chest", "wrist_left", "wrist_right"})


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


class VirtualBenchPolicy:
    def __init__(self, config, *, log_root):
        if not isinstance(config, dict):
            raise ValueError("virtual bench configuration must be an object")
        generation = config.get("observer_generation")
        if not isinstance(generation, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", generation):
            raise ValueError("invalid observer generation")
        path = Path(config.get("observer_health_path", ""))
        root = Path(log_root).resolve()
        if not path.is_absolute() or path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("observer health must be an owned local log file")
        if path.name != "latest-health.json":
            raise ValueError("observer health filename is invalid")
        if set(config.get("verified_coverage", [])) != {"arms", "lift"}:
            raise ValueError("current observer framing must cover arms and lift")
        roles = config.get("required_camera_roles", [])
        if (
            not isinstance(roles, list)
            or any(role not in ROLES for role in roles)
            or len(set(roles)) != len(roles)
        ):
            raise ValueError("invalid required camera coverage")
        if config.get("recovery_episode_seconds") != 10 or config.get("max_recoveries") != 3:
            raise ValueError("supported recovery budget is 10 seconds and three episodes")
        self.config = dict(config)
        self.root = root
        self.resolved_path = path.resolve()
        self.path = path
        self.generation = generation
        self.roles = tuple(roles)
        self.sequence = None
        self.source_ticks = None
        self.advanced_at = None
        self.advances = 0
        self.observer_expires_at = None
        self._last_observer_record = None
        self.observer_metadata_uncertain = False
        self.disarmed = None
        self.input_epoch = None
        self.episode_at = None
        self.recovery_count = 0
        self.hold_requested = False
        self.seen_pause = False
        self.camera_health = {}
        self.last = {}

    def disarm(self, reason):
        self.disarmed = reason

    def observer(self, *, now, wall_ms, clock=None, wall_clock_ms=None):
        self.observer_expires_at = None
        self.observer_metadata_uncertain = False
        try:
            if (
                self.path.is_symlink()
                or self.path.resolve() != self.resolved_path
                or not self.path.resolve().is_relative_to(self.root)
                or self.path.stat().st_size > 8192
            ):
                self._last_observer_record = None
                return False
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except PermissionError:
            # Windows atomic replacement can briefly refuse a shared read.
            # Retain only accepted metadata and every original expiry clock.
            data = self._last_observer_record
            self.observer_metadata_uncertain = True
        except (OSError, ValueError):
            self._last_observer_record = None
            return False
        # The file may advance during the read. Sample this computer's clocks
        # after acquisition, without treating a newer honest receipt as future.
        if clock is not None:
            now = clock()
        if wall_clock_ms is not None:
            wall_ms = wall_clock_ms()
        if not isinstance(data, dict):
            self._last_observer_record = None
            return False
        seq, ticks = data.get("sequence"), data.get("source_system_relative_ticks")
        age, rtt, receipt = (
            data.get(k) for k in ("capture_age_ms", "round_trip_ms", "received_wall_time_ms")
        )
        if (
            data.get("generation") != self.generation
            or any(data.get(k) is not True for k in ("running", "recording", "challenge_qualified"))
            or type(seq) is not int
            or seq < 1
            or type(ticks) is not int
            or ticks < 0
            or not all(number(v) for v in (age, rtt, receipt))
            or not 0 <= wall_ms - receipt <= 1000
            or not 0 <= max(age, rtt) + wall_ms - receipt <= 500
            or not 0 <= rtt <= 750
        ):
            self._last_observer_record = None
            return False
        if self.sequence is None or (seq > self.sequence and ticks > self.source_ticks):
            self.sequence, self.source_ticks, self.advanced_at = seq, ticks, now
            self.advances += 1
        elif seq != self.sequence or ticks != self.source_ticks:
            self._last_observer_record = None
            return False
        qualified = self.advances >= 2 and 0 <= now - self.advanced_at <= 1
        if qualified:
            self._last_observer_record = data
            self.observer_expires_at = min(
                now + (500 - max(age, rtt) - wall_ms + receipt) / 1000,
                now + (1000 - wall_ms + receipt) / 1000,
                self.advanced_at + 1,
            )
        return qualified

    def cameras(self, health, *, now):
        if not isinstance(health, dict) or not isinstance(health.get("roles"), list):
            return False
        status_age = health.get("status_received_age_ms")
        if not number(status_age) or not 0 <= status_age <= 2000:
            return False
        for role in self.roles:
            matches = [v for v in health["roles"] if isinstance(v, dict) and v.get("role") == role]
            if len(matches) != 1:
                continue
            value = matches[0]
            seq, gen = value.get("sequence"), value.get("generation")
            decoded, source = value.get("decoded_age_ms"), value.get("source_age_ms")
            threshold = 500 if value.get("selected") else 1500
            valid = (
                value.get("identity") == role
                and value.get("configured") is True
                and value.get("fresh") is True
                and value.get("source_state") == "fresh"
                and type(seq) is int
                and seq > 0
                and type(gen) is int
                and gen >= 0
                and value.get("decoded_generation") == gen
                and number(decoded)
                and 0 <= decoded <= threshold
                and number(source)
                and 0 <= source <= 500
            )
            old = self.camera_health.get(role)
            if valid and (
                old is None or gen > old["generation"] or gen == old["generation"] and seq > old["sequence"]
            ):
                self.camera_health[role] = dict(
                    value, received_at=now, threshold=threshold, status_age=status_age
                )
            elif not valid:
                self.camera_health.pop(role, None)
        return self.camera_qualified(now)

    def camera_qualified(self, now):
        for role in self.roles:
            value = self.camera_health.get(role)
            if value is None:
                return False
            elapsed_ms = (now - value["received_at"]) * 1000
            if (
                elapsed_ms < 0
                or value["decoded_age_ms"] + elapsed_ms > value["threshold"]
                or value["source_age_ms"] + elapsed_ms > 500
                or value["status_age"] + elapsed_ms > 2000
            ):
                return False
        return True

    def coverage_expires_at(self):
        if self.observer_expires_at is None:
            return 0.0
        deadlines = [self.observer_expires_at]
        for role in self.roles:
            value = self.camera_health.get(role)
            if value is None:
                return 0.0
            deadlines.extend(
                [
                    value["received_at"] + (value["threshold"] - value["decoded_age_ms"]) / 1000,
                    value["received_at"] + (500 - value["source_age_ms"]) / 1000,
                    value["received_at"] + (2000 - value["status_age"]) / 1000,
                ]
            )
        return min(deadlines)

    def evaluate(self, state, *, now, wall_ms, clock=None, wall_clock_ms=None):
        observed = self.observer(now=now, wall_ms=wall_ms, clock=clock, wall_clock_ms=wall_clock_ms)
        if clock is not None:
            now = clock()
        if wall_clock_ms is not None:
            wall_ms = wall_clock_ms()
        covered = (
            observed
            and self.observer_expires_at is not None
            and now <= self.observer_expires_at
            and self.camera_qualified(now)
        )
        epoch = state.get("input_epoch")
        if self.input_epoch is None and type(epoch) is int:
            self.input_epoch = epoch
        elif type(epoch) is int and epoch != self.input_epoch:
            self.disarm("ownership changed")
        pause = state.get("input_pause") or {}
        cause = pause.get("reason")
        paused = state.get("pause_required") is True
        active = (
            state.get("virtual_bench_admitted") is True
            and state.get("phase") in {"live", "feedback_stale", "paused"}
            and state.get("native_connected") is True
        )
        action = None
        if active and not covered and not paused and not self.disarmed:
            if self.episode_at is None:
                self.episode_at = now
            action = "hold"
            self.hold_requested = True
        if active and paused:
            if cause not in CAUSES:
                self.disarm("pause cause is not allowlisted")
            else:
                self.seen_pause = True
                if self.episode_at is None:
                    self.episode_at = now
        expired = self.episode_at is not None and (now - self.episode_at >= 10 or self.recovery_count >= 3)
        if active and expired:
            action = "stop"
            self.disarm("recovery budget exhausted")
        observation = (state.get("telemetry") or {}).get("observation") or {}
        if (
            self.episode_at is not None
            and not expired
            and not self.disarmed
            and self.seen_pause
            and not paused
            and covered
            and observation.get("host_state") == "active"
            and state.get("pending_gate") is None
        ):
            self.hold_requested = False
            self.seen_pause = False
            self.recovery_count += 1
            self.episode_at = None
        remaining = None if self.episode_at is None else max(0, 10 - (now - self.episode_at))
        if active and self.episode_at is not None and (remaining == 0 or self.recovery_count >= 3):
            action = "stop"
            self.disarm("recovery budget exhausted")
        gate = state.get("pending_gate")
        pose = (state.get("telemetry") or {}).get("recovery_pose") or {}

        def fresh_sample(value):
            acquired = value.get("acquired_at_ns")
            return type(acquired) is int and acquired >= 0 and 0 <= wall_ms - acquired / 1e6 <= 250

        eligible = (
            active
            and not self.disarmed
            and paused
            and cause in CAUSES
            and covered
            and self.episode_at is not None
            and remaining > 0
            and self.recovery_count < 3
            and isinstance(gate, (list, tuple))
            and len(gate) == 2
            and gate[0] == "resume"
            and type(gate[1]) is int
            and observation.get("host_epoch") == gate[1]
            and observation.get("host_state") == "paused"
            and fresh_sample(observation)
            and fresh_sample(pose)
            and pose.get("joint_count") == 12
            and number(pose.get("max_difference"))
            and 0 <= pose["max_difference"] <= 3
            and not state.get("error")
        )
        self.last = {
            "enabled": True,
            "disarmed": self.disarmed is not None,
            "disarm_reason": self.disarmed,
            "required_coverage_qualified": covered,
            "observer_metadata_uncertain": self.observer_metadata_uncertain,
            "recovery_eligible": bool(eligible),
            "recovery_count": self.recovery_count,
            "remaining_seconds": remaining,
            "cause": cause,
            "action": action,
            "input_epoch": epoch,
            "pause_sequence": pause.get("pause_sequence"),
            "host_epoch": gate[1] if isinstance(gate, (list, tuple)) and len(gate) == 2 else None,
        }
        return dict(self.last)

    def approval(self, evidence, state, *, now, wall_ms, clock=None, wall_clock_ms=None):
        current = self.evaluate(state, now=now, wall_ms=wall_ms, clock=clock, wall_clock_ms=wall_clock_ms)
        return (
            isinstance(evidence, dict)
            and current["recovery_eligible"]
            and all(
                type(evidence.get(k)) is int and evidence[k] == current[k]
                for k in ("input_epoch", "pause_sequence", "host_epoch")
            )
        )
