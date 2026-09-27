#!/usr/bin/env python

from __future__ import annotations

import importlib.util
import json
import stat
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def load_module():
    path = REPO_ROOT / "tools" / "update_am1_camera_rotations.py"
    spec = importlib.util.spec_from_file_location("update_am1_camera_rotations", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def write_maps(root: Path, *, mismatch: bool = False) -> tuple[Path, Path]:
    paths = {
        "forward": "/dev/front", "chest": "/dev/chest", "backward": "/dev/rear",
        "wrist_right": "/dev/right", "wrist_left": "/dev/left",
    }
    semantic = {
        "version": 1, "bind": "192.0.2.1", "port": 1984, "cameras": paths,
        "rotations": {"forward": 180, "chest": 180, "backward": 180, "wrist_right": 90, "wrist_left": 270},
    }
    identification = {
        "version": 1, "bind": "192.0.2.1", "port": 1985,
        "cameras": {
            "preview_1": "/dev/front", "preview_2": "/dev/chest",
            "preview_3": "/dev/wrong" if mismatch else "/dev/right",
            "preview_4": "/dev/rear", "preview_5": "/dev/left",
        },
        "rotations": {
            "preview_1": 180, "preview_2": 180, "preview_3": 90,
            "preview_4": 180, "preview_5": 270,
        },
    }
    semantic_path = root / "cameras.json"
    identification_path = root / "identification.json"
    semantic_path.write_text(json.dumps(semantic), encoding="utf-8")
    identification_path.write_text(json.dumps(identification), encoding="utf-8")
    semantic_path.chmod(0o600)
    identification_path.chmod(0o600)
    return semantic_path, identification_path


def test_rotation_update_is_explicit_backed_up_and_preserves_identity_and_permissions(tmp_path):
    module = load_module()
    semantic, identification = write_maps(tmp_path)
    original_modes = (stat.S_IMODE(semantic.stat().st_mode), stat.S_IMODE(identification.stat().st_mode))

    result = module.update_rotation_files(semantic, identification, timestamp="20260920T120000")

    semantic_data = json.loads(semantic.read_text(encoding="utf-8"))
    identification_data = json.loads(identification.read_text(encoding="utf-8"))
    assert semantic_data["rotations"] == {
        "forward": 180, "chest": 180, "backward": 180,
        "wrist_right": 270, "wrist_left": 90,
    }
    assert identification_data["rotations"] == {
        "preview_1": 180, "preview_2": 180, "preview_3": 270,
        "preview_4": 180, "preview_5": 90,
    }
    assert semantic_data["cameras"]["wrist_right"] == identification_data["cameras"]["preview_3"]
    assert semantic_data["cameras"]["wrist_left"] == identification_data["cameras"]["preview_5"]
    assert (stat.S_IMODE(semantic.stat().st_mode), stat.S_IMODE(identification.stat().st_mode)) == original_modes
    assert all(Path(path).is_file() for path in result.backups)
    assert result.changed is True

    repeated = module.update_rotation_files(semantic, identification, timestamp="20260920T120001")
    assert repeated.changed is False
    assert repeated.backups == []


def test_rotation_update_refuses_mapping_discrepancy_without_writing_or_backup(tmp_path):
    module = load_module()
    semantic, identification = write_maps(tmp_path, mismatch=True)
    before = (semantic.read_bytes(), identification.read_bytes())

    with pytest.raises(module.RotationRefusal, match="preview_3"):
        module.update_rotation_files(semantic, identification, timestamp="20260920T120000")

    assert (semantic.read_bytes(), identification.read_bytes()) == before
    assert not list(tmp_path.glob("*.bak.*"))
