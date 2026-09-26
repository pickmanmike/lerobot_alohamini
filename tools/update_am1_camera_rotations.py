#!/usr/bin/env python

"""Apply the reviewed one-time AM1 wrist-view rotation update to private maps."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


SEMANTIC_OLD = {
    "forward": 180,
    "chest": 180,
    "backward": 180,
    "wrist_right": 90,
    "wrist_left": 270,
}
SEMANTIC_TARGET = {**SEMANTIC_OLD, "wrist_right": 270, "wrist_left": 90}
IDENTIFICATION_OLD = {
    "preview_1": 180,
    "preview_2": 180,
    "preview_3": 90,
    "preview_4": 180,
    "preview_5": 270,
}
IDENTIFICATION_TARGET = {**IDENTIFICATION_OLD, "preview_3": 270, "preview_5": 90}
ROLE_TO_PREVIEW = {
    "forward": "preview_1",
    "chest": "preview_2",
    "wrist_right": "preview_3",
    "backward": "preview_4",
    "wrist_left": "preview_5",
}


class RotationRefusal(RuntimeError):
    """The private maps do not match the reviewed identity/rotation precondition."""


@dataclass(frozen=True)
class RotationUpdateResult:
    changed: bool
    backups: list[str]
    before_sha256: dict[str, str]
    after_sha256: dict[str, str]


def _load(path: Path) -> tuple[bytes, dict[str, Any], int]:
    try:
        raw = path.read_bytes()
        data = json.loads(raw)
        mode = stat.S_IMODE(path.stat().st_mode)
    except (OSError, json.JSONDecodeError) as exc:
        raise RotationRefusal(f"Unable to read valid private camera map {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RotationRefusal(f"Private camera map must be a JSON object: {path}")
    return raw, data, mode


def _validate_maps(semantic: dict[str, Any], identification: dict[str, Any]) -> bool:
    semantic_cameras = semantic.get("cameras")
    identification_cameras = identification.get("cameras")
    if not isinstance(semantic_cameras, dict) or set(semantic_cameras) != set(ROLE_TO_PREVIEW):
        raise RotationRefusal("Semantic camera identities do not match the five reviewed AM1 roles.")
    if not isinstance(identification_cameras, dict) or set(identification_cameras) != set(ROLE_TO_PREVIEW.values()):
        raise RotationRefusal("Identification camera identities do not match preview_1 through preview_5.")
    for role, preview in ROLE_TO_PREVIEW.items():
        if semantic_cameras[role] != identification_cameras[preview]:
            raise RotationRefusal(
                f"Camera identity discrepancy: semantic {role} does not match {preview}; no rotation was changed."
            )

    semantic_rotations = semantic.get("rotations")
    identification_rotations = identification.get("rotations")
    old = semantic_rotations == SEMANTIC_OLD and identification_rotations == IDENTIFICATION_OLD
    target = semantic_rotations == SEMANTIC_TARGET and identification_rotations == IDENTIFICATION_TARGET
    if not old and not target:
        raise RotationRefusal(
            "Private rotation values differ from both the reviewed pre-change state and the explicit target."
        )
    return old


def _encoded(data: dict[str, Any]) -> bytes:
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _replace(path: Path, payload: bytes, mode: int) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def update_rotation_files(
    semantic_path: Path,
    identification_path: Path,
    *,
    timestamp: str | None = None,
) -> RotationUpdateResult:
    semantic_path = Path(semantic_path)
    identification_path = Path(identification_path)
    semantic_raw, semantic, semantic_mode = _load(semantic_path)
    identification_raw, identification, identification_mode = _load(identification_path)
    needs_update = _validate_maps(semantic, identification)
    before = {
        str(semantic_path): hashlib.sha256(semantic_raw).hexdigest(),
        str(identification_path): hashlib.sha256(identification_raw).hexdigest(),
    }
    if not needs_update:
        return RotationUpdateResult(False, [], before, dict(before))

    timestamp = timestamp or datetime.now().strftime("%Y%m%dT%H%M%S")
    backups = [
        semantic_path.with_name(f"{semantic_path.name}.bak.{timestamp}"),
        identification_path.with_name(f"{identification_path.name}.bak.{timestamp}"),
    ]
    if any(path.exists() for path in backups):
        raise RotationRefusal("A requested private-map backup path already exists; choose a new timestamp.")
    for source, backup, mode in (
        (semantic_path, backups[0], semantic_mode),
        (identification_path, backups[1], identification_mode),
    ):
        shutil.copyfile(source, backup)
        os.chmod(backup, mode)

    semantic["rotations"] = dict(SEMANTIC_TARGET)
    identification["rotations"] = dict(IDENTIFICATION_TARGET)
    try:
        _replace(semantic_path, _encoded(semantic), semantic_mode)
        _replace(identification_path, _encoded(identification), identification_mode)
    except BaseException:
        _replace(semantic_path, semantic_raw, semantic_mode)
        _replace(identification_path, identification_raw, identification_mode)
        raise

    after = {
        str(semantic_path): hashlib.sha256(semantic_path.read_bytes()).hexdigest(),
        str(identification_path): hashlib.sha256(identification_path.read_bytes()).hexdigest(),
    }
    return RotationUpdateResult(True, [str(path) for path in backups], before, after)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--semantic", type=Path, required=True)
    parser.add_argument("--identification", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = update_rotation_files(args.semantic, args.identification)
    except RotationRefusal as exc:
        parser.error(str(exc))
    print(json.dumps(result.__dict__, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
