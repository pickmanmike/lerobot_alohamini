"""Explicit fake feedback/observation adapter. Never connects to hardware."""

import time


class FakeExecutor:
    source = "fake"
    restart_cleanup = "held_body_zero"

    def supports(self, recipe):
        return recipe.name.startswith("fake-")

    def begin(self, recipe):
        self.progress_s = 0.0
        self.recipe = recipe
        return self.dispatch(recipe)

    def advance(self, now, dt):
        self.progress_s = min(self.recipe.trajectory_s, self.progress_s + dt)
        return {"progress_s": self.progress_s, "complete": self.progress_s >= self.recipe.trajectory_s}

    def finish(self, status):
        self.hold()
        return {"cleanup": "held_body_zero", "uncertain": False}

    def close(self):
        self.hold()

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.feedback = True
        self.required_observation = True
        self.optional_quality = True
        self.pose_aligned = True
        self.native_ack = True
        self.fault = None
        self.feedback_age_s = 0.0
        self.observation_age_s = 0.0
        self.output = "held_body_zero"

    def evidence(self):
        return {
            "source": "fake-injected",
            "feedback": self.feedback and self.feedback_age_s <= 0.25,
            "required_observation": self.required_observation and self.observation_age_s <= 0.5,
            "optional_quality": self.optional_quality,
            "pose_aligned": self.pose_aligned,
            "native_ack": self.native_ack,
            "fault": self.fault,
            "feedback_age_s": self.feedback_age_s,
            "observation_age_s": self.observation_age_s,
        }

    def dispatch(self, recipe):
        self.output = "fake_running" if self.native_ack else "held_body_zero"
        return self.native_ack

    def hold(self):
        self.output = "held_body_zero"

    def apply(self, target):
        if self.native_ack:
            self.output = list(target)
        return self.native_ack
