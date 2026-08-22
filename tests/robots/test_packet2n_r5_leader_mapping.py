#!/usr/bin/env python

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "tools" / "packet2n_r5_leader_mapping.ps1"
RUNNER_VERSION = "packet2n-r5-runner-v1"
BEHAVIOR_BASELINE = "cae57b59db1d9156be568aa4b216fc90701aa741"
EXPECTED_BRANCH = "fix/am1-elbow-commissioning"
LEFT_PORT = "COM8"
RIGHT_PORT = "COM7"
LEADER_ID = "so101_leader_bi"
ARM_PROFILE = "so-arm-5dof"
LEFT_MAP_STAGE = "MapLeft"
RIGHT_MAP_STAGE = "MapRight"
SAMPLE_KEYS = (
    "arm_left_shoulder_pan.pos",
    "arm_left_shoulder_lift.pos",
    "arm_left_elbow_flex.pos",
    "arm_left_wrist_flex.pos",
    "arm_left_wrist_roll.pos",
    "arm_left_gripper.pos",
    "arm_right_shoulder_pan.pos",
    "arm_right_shoulder_lift.pos",
    "arm_right_elbow_flex.pos",
    "arm_right_wrist_flex.pos",
    "arm_right_wrist_roll.pos",
    "arm_right_gripper.pos",
)
JOINT_NAMES = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
)
pytestmark = pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest().upper()


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def calibration_record(*, homing_offset: int = 2048, range_min: int = 1000, range_max: int = 3000) -> dict[str, int]:
    return {
        "drive_mode": 0,
        "homing_offset": homing_offset,
        "range_min": range_min,
        "range_max": range_max,
    }


def make_calibration(seed: int) -> dict[str, dict[str, int]]:
    return {
        joint: {
            "id": index,
            **(
                calibration_record(homing_offset=2048 + seed + index, range_min=0, range_max=4095)
                if joint == "wrist_roll"
                else calibration_record(homing_offset=2048 + seed + index)
            ),
        }
        for index, joint in enumerate(JOINT_NAMES, start=1)
    }


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def sample_line(index: int, values: dict[str, float]) -> str:
    encoded = " ".join(f"{key}={values[key]:.1f}" for key in SAMPLE_KEYS)
    return f"SAMPLE {index:02d} {encoded}"


def make_map_values(*, physical_side: str, sample_index: int) -> dict[str, float]:
    left_gripper = 5.0
    right_gripper = 5.0
    left_body = [0.2, 0.4, 0.6, 0.8, 1.0]
    right_body = [0.1, 0.2, 0.3, 0.4, 0.5]
    if physical_side == "left":
        left_gripper = 5.0 + sample_index * 0.5
        right_gripper = 0.4 + (sample_index % 2) * 0.2
    else:
        right_gripper = 7.0 + sample_index * 0.5
        left_gripper = 0.6 + (sample_index % 2) * 0.2
    return {
        "arm_left_shoulder_pan.pos": left_body[0],
        "arm_left_shoulder_lift.pos": left_body[1],
        "arm_left_elbow_flex.pos": left_body[2],
        "arm_left_wrist_flex.pos": left_body[3],
        "arm_left_wrist_roll.pos": left_body[4],
        "arm_left_gripper.pos": left_gripper,
        "arm_right_shoulder_pan.pos": right_body[0],
        "arm_right_shoulder_lift.pos": right_body[1],
        "arm_right_elbow_flex.pos": right_body[2],
        "arm_right_wrist_flex.pos": right_body[3],
        "arm_right_wrist_roll.pos": right_body[4],
        "arm_right_gripper.pos": right_gripper,
    }


def make_valid_map_log(*, stage: str, state_hash: str, evidence_hash: str, physical_side: str) -> str:
    lines = [
        f"RUN_MARKER={stage}",
        f"STATE_SHA256={state_hash}",
        f"EVIDENCE_SHA256={evidence_hash}",
        "NO_ROBOT_PROOF=1",
        "CLEANUP_PROOF=1",
    ]
    for index in range(60):
        lines.append(sample_line(index, make_map_values(physical_side=physical_side, sample_index=index)))
    lines.append("CLIENT_EXIT_CODE=0")
    return "\n".join(lines) + "\n"


def make_partial_log(stage: str) -> str:
    return "\n".join(
        [
            f"RUN_MARKER={stage}",
            "NO_ROBOT_PROOF=1",
            "CLIENT_EXIT_CODE=0",
        ]
    ) + "\n"


def base_plan(tmp_path: Path) -> dict[str, object]:
    logs_dir = tmp_path / "logs"
    calibration_dir = tmp_path / "calibration" / "teleoperators" / "so_leader"
    left_calibration_path = calibration_dir / f"{LEADER_ID}_left.json"
    right_calibration_path = calibration_dir / f"{LEADER_ID}_right.json"
    left_original = make_calibration(0)
    right_original = make_calibration(10)
    left_backup_path = tmp_path / "manifest" / "left-original.json"
    right_backup_path = tmp_path / "manifest" / "right-original.json"
    write_json(left_calibration_path, left_original)
    write_json(right_calibration_path, right_original)
    write_json(left_backup_path, left_original)
    write_json(right_backup_path, right_original)
    manifest = {
        "left": {
            "path": str(left_backup_path),
            "sha256": sha256_path(left_backup_path),
            "size": left_backup_path.stat().st_size,
        },
        "right": {
            "path": str(right_backup_path),
            "sha256": sha256_path(right_backup_path),
            "size": right_backup_path.stat().st_size,
        },
    }
    manifest_path = tmp_path / "manifest" / "immutable-manifest.json"
    write_json(manifest_path, manifest)
    left_fresh = make_calibration(100)
    right_fresh = make_calibration(200)
    transcript_path = logs_dir / "packet2n-r5-calibration.log"
    evidence_path = logs_dir / "packet2n-r5-evidence.json"
    left_map_path = logs_dir / "packet2n-r5-map-left.log"
    right_map_path = logs_dir / "packet2n-r5-map-right.log"
    transcript_text = "CALIBRATION START\nCALIBRATION COMPLETE\nCALIBRATION_EXIT_CODE=0\n"
    evidence_payload = {
        "classification": "VALID_FRESH_CALIBRATION",
        "left_sha256": sha256_text(json.dumps(left_fresh, sort_keys=True)),
        "right_sha256": sha256_text(json.dumps(right_fresh, sort_keys=True)),
    }
    evidence_text = json.dumps(evidence_payload, indent=2, sort_keys=True) + "\n"
    return {
        "expected_branch": EXPECTED_BRANCH,
        "behavior_baseline": BEHAVIOR_BASELINE,
        "head": "545cf933d794657cb3802e3c6a14ead551617a1d",
        "repo_root": str(REPO_ROOT),
        "worktree_clean": True,
        "protected_runtime_paths_unchanged": True,
        "python_env_clean": True,
        "python_resolved": True,
        "import_sources_match": True,
        "calibration_root": str(tmp_path / "calibration"),
        "state_root": str(logs_dir),
        "manifest": {
            "path": str(manifest_path),
            "sha256": sha256_path(manifest_path),
            "content": manifest,
        },
        "calibration": {
            "left": {
                "path": str(left_calibration_path),
                "backup_path": str(left_backup_path),
                "backup_sha256": sha256_path(left_backup_path),
                "backup_size": left_backup_path.stat().st_size,
            },
            "right": {
                "path": str(right_calibration_path),
                "backup_path": str(right_backup_path),
                "backup_sha256": sha256_path(right_backup_path),
                "backup_size": right_backup_path.stat().st_size,
            },
        },
        "stage_plan": {
            "Calibrate": {
                "transcript_path": str(transcript_path),
                "transcript_text": transcript_text,
                "evidence_path": str(evidence_path),
                "evidence_text": evidence_text,
                "post_calibration": {
                    "left": left_fresh,
                    "right": right_fresh,
                },
                "launched": True,
                "exit_code": 0,
                "set_last_exit_code_before": 55,
            },
            LEFT_MAP_STAGE: {
                "map_path": str(left_map_path),
                "launched": True,
                "exit_code": 0,
                "physical_side": "left",
                "set_last_exit_code_before": 88,
            },
            RIGHT_MAP_STAGE: {
                "map_path": str(right_map_path),
                "launched": True,
                "exit_code": 0,
                "physical_side": "right",
                "set_last_exit_code_before": 77,
            },
        },
    }


def run_runner(*args: str, plan: dict[str, object] | None = None, tmp_path: Path | None = None) -> subprocess.CompletedProcess[str]:
    assert SCRIPT_PATH.exists(), "the packet2n_r5 leader mapping runner is missing"
    env = os.environ.copy()
    if plan is not None:
        assert tmp_path is not None
        plan_path = tmp_path / "plan.json"
        write_json(plan_path, plan)
        env["PACKET2N_R5_TEST_MODE"] = "1"
        command = [
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(SCRIPT_PATH),
            *args,
            "-TestPlanPath",
            str(plan_path),
        ]
    else:
        command = [
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(SCRIPT_PATH),
            *args,
        ]
    return subprocess.run(
        command,
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
        env=env,
        cwd=REPO_ROOT,
    )


def load_state(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_missing_state_refuses_before_command_execution(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "missing-state.json"
    result = run_runner(
        "-Stage",
        LEFT_MAP_STAGE,
        "-StatePath",
        str(state_path),
        "-Confirm",
        "MAPLEFT",
        plan=plan,
        tmp_path=tmp_path,
    )

    assert result.returncode != 0
    assert "State file is required for stage MapLeft" in result.stderr
    assert not Path(plan["stage_plan"][LEFT_MAP_STAGE]["map_path"]).exists()


def test_failed_guard_creates_no_map_log(tmp_path):
    plan = base_plan(tmp_path)
    plan["worktree_clean"] = False
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    calibrate = run_runner(
        "-Stage",
        "Calibrate",
        "-StatePath",
        str(state_path),
        "-Confirm",
        "CALIBRATE",
        plan=plan,
        tmp_path=tmp_path,
    )
    assert calibrate.returncode != 0
    assert "tracked/untracked worktree" in calibrate.stderr
    assert not Path(plan["stage_plan"]["Calibrate"]["transcript_path"]).exists()


def test_native_never_launched_cannot_yield_exit_zero(tmp_path):
    plan = base_plan(tmp_path)
    plan["stage_plan"]["Calibrate"]["launched"] = False
    plan["stage_plan"]["Calibrate"]["exit_code"] = 0
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    result = run_runner(
        "-Stage",
        "Calibrate",
        "-StatePath",
        str(state_path),
        "-Confirm",
        "CALIBRATE",
        plan=plan,
        tmp_path=tmp_path,
    )

    assert result.returncode != 0
    assert "Native command did not launch" in result.stderr
    state = load_state(state_path)
    assert state["stages"]["Calibrate"]["native"]["launched"] is False
    assert state["stages"]["Calibrate"]["native"]["real_exit_code"] == 0
    assert not Path(plan["stage_plan"]["Calibrate"]["transcript_path"]).exists()


def test_stale_lastexitcode_cannot_produce_success(tmp_path):
    plan = base_plan(tmp_path)
    plan["stage_plan"]["Calibrate"]["launched"] = False
    plan["stage_plan"]["Calibrate"]["exit_code"] = None
    plan["stage_plan"]["Calibrate"]["set_last_exit_code_before"] = 0
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    result = run_runner(
        "-Stage",
        "Calibrate",
        "-StatePath",
        str(state_path),
        "-Confirm",
        "CALIBRATE",
        plan=plan,
        tmp_path=tmp_path,
    )

    assert result.returncode != 0
    assert "stale LASTEXITCODE" in result.stderr
    state = load_state(state_path)
    assert state["stages"]["Calibrate"]["native"]["launched"] is False
    assert state["stages"]["Calibrate"]["result"] == "failed"


def test_successful_calibration_resumes_in_fresh_process(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    calibrate = run_runner(
        "-Stage",
        "Calibrate",
        "-StatePath",
        str(state_path),
        "-Confirm",
        "CALIBRATE",
        plan=plan,
        tmp_path=tmp_path,
    )
    assert calibrate.returncode == 0, calibrate.stderr

    map_left = run_runner(
        "-Stage",
        LEFT_MAP_STAGE,
        "-StatePath",
        str(state_path),
        "-Confirm",
        "MAPLEFT",
        plan=plan,
        tmp_path=tmp_path,
    )

    assert map_left.returncode == 0, map_left.stderr
    state = load_state(state_path)
    assert state["classification"] == "VALID_FRESH_CALIBRATION"
    assert state["completed_stages"] == ["Calibrate", LEFT_MAP_STAGE]
    assert state["next_stage"] == RIGHT_MAP_STAGE


def test_changed_calibration_after_evidence_refuses(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    calibrate = run_runner(
        "-Stage",
        "Calibrate",
        "-StatePath",
        str(state_path),
        "-Confirm",
        "CALIBRATE",
        plan=plan,
        tmp_path=tmp_path,
    )
    assert calibrate.returncode == 0, calibrate.stderr

    left_path = Path(plan["calibration"]["left"]["path"])
    write_json(left_path, make_calibration(999))
    map_left = run_runner(
        "-Stage",
        LEFT_MAP_STAGE,
        "-StatePath",
        str(state_path),
        "-Confirm",
        "MAPLEFT",
        plan=plan,
        tmp_path=tmp_path,
    )

    assert map_left.returncode != 0
    assert "Current calibration does not match evidence" in map_left.stderr
    assert not Path(plan["stage_plan"][LEFT_MAP_STAGE]["map_path"]).exists()


def test_wrong_com_assignment_in_state_refuses(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    calibrate = run_runner(
        "-Stage",
        "Calibrate",
        "-StatePath",
        str(state_path),
        "-Confirm",
        "CALIBRATE",
        plan=plan,
        tmp_path=tmp_path,
    )
    assert calibrate.returncode == 0, calibrate.stderr
    state = load_state(state_path)
    state["ports"]["logical_left"] = "COM999"
    write_json(state_path, state)

    map_left = run_runner(
        "-Stage",
        LEFT_MAP_STAGE,
        "-StatePath",
        str(state_path),
        "-Confirm",
        "MAPLEFT",
        plan=plan,
        tmp_path=tmp_path,
    )

    assert map_left.returncode != 0
    assert "Persisted port assignment is invalid" in map_left.stderr


def test_invalid_partial_log_is_never_accepted(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    calibrate = run_runner(
        "-Stage",
        "Calibrate",
        "-StatePath",
        str(state_path),
        "-Confirm",
        "CALIBRATE",
        plan=plan,
        tmp_path=tmp_path,
    )
    assert calibrate.returncode == 0, calibrate.stderr
    state = load_state(state_path)
    evidence_hash = state["artifacts"]["evidence"]["sha256"]
    state_hash = sha256_path(state_path)
    left_map_path = Path(plan["stage_plan"][LEFT_MAP_STAGE]["map_path"])
    right_map_path = Path(plan["stage_plan"][RIGHT_MAP_STAGE]["map_path"])
    write_text(left_map_path, make_valid_map_log(stage=LEFT_MAP_STAGE, state_hash=state_hash, evidence_hash=evidence_hash, physical_side="left"))
    write_text(right_map_path, make_partial_log(RIGHT_MAP_STAGE))
    state["completed_stages"] = ["Calibrate", LEFT_MAP_STAGE, RIGHT_MAP_STAGE]
    state["artifacts"]["map_left"] = {"path": str(left_map_path), "sha256": sha256_path(left_map_path)}
    state["artifacts"]["map_right"] = {"path": str(right_map_path), "sha256": sha256_path(right_map_path)}
    write_json(state_path, state)

    result = run_runner(
        "-Stage",
        "Verify",
        "-StatePath",
        str(state_path),
        plan=plan,
        tmp_path=tmp_path,
    )

    assert result.returncode != 0
    assert "Map log validation failed for MapRight" in result.stderr


def test_verify_requires_both_valid_maps_and_yields_only_mapping_result_correct(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    calibrate = run_runner(
        "-Stage",
        "Calibrate",
        "-StatePath",
        str(state_path),
        "-Confirm",
        "CALIBRATE",
        plan=plan,
        tmp_path=tmp_path,
    )
    assert calibrate.returncode == 0, calibrate.stderr
    map_left = run_runner(
        "-Stage",
        LEFT_MAP_STAGE,
        "-StatePath",
        str(state_path),
        "-Confirm",
        "MAPLEFT",
        plan=plan,
        tmp_path=tmp_path,
    )
    assert map_left.returncode == 0, map_left.stderr
    map_right = run_runner(
        "-Stage",
        RIGHT_MAP_STAGE,
        "-StatePath",
        str(state_path),
        "-Confirm",
        "MAPRIGHT",
        plan=plan,
        tmp_path=tmp_path,
    )
    assert map_right.returncode == 0, map_right.stderr

    verify = run_runner(
        "-Stage",
        "Verify",
        "-StatePath",
        str(state_path),
        plan=plan,
        tmp_path=tmp_path,
    )

    assert verify.returncode == 0, verify.stderr
    assert verify.stdout.strip() == "MAPPING_RESULT=CORRECT"
    state = load_state(state_path)
    assert state["final_result"] == "MAPPING_RESULT=CORRECT"
    assert state["next_stage"] is None


def test_status_on_original_state_is_offline_and_reports_next_calibrate(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "missing-state.json"
    result = run_runner(
        "-Stage",
        "Status",
        "-StatePath",
        str(state_path),
        plan=plan,
        tmp_path=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["classification"] == "ORIGINAL_CALIBRATION_INTACT"
    assert payload["next_stage"] == "Calibrate"
    assert not state_path.exists()
