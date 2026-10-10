"""Immutable explicitly selected session recipes and JSON validation."""

import uuid
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class Recipe:
    name: str
    mode: str
    trajectory_s: float | None
    live_s: float
    seed: int | None = 17


RECIPES = MappingProxyType(
    {
        "fake-finite": Recipe("fake-finite", "finite", 352.0, 420.0),
        "fake-finite-short": Recipe("fake-finite-short", "finite", 1.0, 12.0),
        "sim-arm-smoke": Recipe("sim-arm-smoke", "finite", 88.0, 180.0),
        "sim-arm-smoke-repeat": Recipe("sim-arm-smoke-repeat", "finite", 352.0, 420.0),
        "sim-arm-hold-body": Recipe("sim-arm-hold-body", "finite", 12.0, 30.0),
        "physical-arm-smoke": Recipe("physical-arm-smoke", "finite", 88.0, 180.0, seed=None),
        "physical-arm-smoke-repeat": Recipe("physical-arm-smoke-repeat", "finite", 352.0, 420.0, seed=None),
        "physical-arm-hold-body": Recipe("physical-arm-hold-body", "finite", 12.0, 30.0, seed=None),
        "fake-interactive": Recipe("fake-interactive", "interactive", None, 420.0),
    }
)


def uuid_text(value):
    if not isinstance(value, str):
        raise ValueError("UUID string required")
    return str(uuid.UUID(value))
