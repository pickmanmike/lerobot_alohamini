"""Immutable, explicitly fake session recipes and JSON validation."""

import uuid
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class Recipe:
    name: str
    mode: str
    trajectory_s: float | None
    live_s: float
    seed: int = 17


RECIPES = MappingProxyType(
    {
        "fake-finite": Recipe("fake-finite", "finite", 352.0, 420.0),
        "fake-finite-short": Recipe("fake-finite-short", "finite", 1.0, 12.0),
        "fake-interactive": Recipe("fake-interactive", "interactive", None, 420.0),
    }
)


def uuid_text(value):
    if not isinstance(value, str):
        raise ValueError("UUID string required")
    return str(uuid.UUID(value))
