"""Finite generated leader input: individual chains, combined reach, then body.

This is generated bench input, never a recorded human demonstration. Only an
acknowledged active sender advances its clock; interruption time is discarded.
"""

from .am1_live_teleop import TELEOP_ARM_KEYS, validate_workspace


class TeleopWorkload:
    kind = "virtual-leaders"
    duration_s = 172.5

    def __init__(self, workspace, *, segment="full"):
        segments = {"full": (0.0, 172.5), "combined": (96.0, 12.0),
                    "continuity": (96.0, 24.0), "body": (168.0, 4.5)}
        if segment not in segments:
            raise ValueError("Unknown declared bench segment")
        self.segment = segment
        self._program_offset_s, self.duration_s = segments[segment]
        self.workspace = validate_workspace(workspace)
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
