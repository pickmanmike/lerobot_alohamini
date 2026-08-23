#!/usr/bin/env python

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
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
BODY_KEYS = (
    "x.vel",
    "y.vel",
    "theta.vel",
    "lift_axis.vel",
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


def powershell_utc_timestamp(path: Path) -> str:
    stamp_ns = path.stat().st_mtime_ns
    seconds, remainder_ns = divmod(stamp_ns, 1_000_000_000)
    ticks = remainder_ns // 100
    base = datetime.fromtimestamp(seconds, tz=UTC)
    fractional = f"{ticks:07d}"
    return base.strftime("%Y-%m-%dT%H:%M:%S") + f".{fractional}Z"


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


def make_partial_log(stage: str) -> str:
    return "\n".join(
        [
            f"RUN_MARKER={stage}",
            "NO_ROBOT_PROOF=1",
            "CLIENT_EXIT_CODE=0",
        ]
    ) + "\n"


def format_python_dict(pairs: list[tuple[str, object]]) -> str:
    rendered = []
    for key, value in pairs:
        rendered.append(f"{key!r}: {value!r}")
    return "{" + ", ".join(rendered) + "}"


def actual_action_pairs(*, physical_side: str, sample_index: int) -> list[tuple[str, float]]:
    values = make_map_values(physical_side=physical_side, sample_index=sample_index)
    pairs = [(key, values[key]) for key in SAMPLE_KEYS]
    pairs.extend((key, 0.0) for key in BODY_KEYS)
    return pairs


def make_actual_map_log(
    *,
    marker: str,
    state: dict[str, object],
    physical_side: str,
    action_pairs: list[list[tuple[str, object]]] | None = None,
    extra_metadata: list[str] | None = None,
    extra_before_terminator: list[str] | None = None,
) -> str:
    actions = action_pairs or [actual_action_pairs(physical_side=physical_side, sample_index=index) for index in range(60)]
    artifacts = state["artifacts"]
    post = state["post_calibration"]
    lines = [
        f"MAP_RUN={marker}",
        f"PACKET2N_R5_SESSION_ID={state['session_id']}",
        f"PACKET2N_R5_SESSION_STARTED_UTC={state['utc_start']}",
        f"PACKET2N_R5_BEHAVIOR_SHA={state['behavior_sha']}",
        f"PACKET2N_R5_STATE_PATH={state['state_path']}",
        f"PACKET2N_R5_STATE_BINDING_SHA256={state['session_binding_sha256']}",
        "PACKET2N_R5_GUARD_SUCCESS=1",
        f"PACKET2N_R5_EVIDENCE_PATH={artifacts['evidence']['path']}",
        f"PACKET2N_R5_EVIDENCE_SHA256={artifacts['evidence']['sha256']}",
        f"PACKET2N_R5_TRANSCRIPT_PATH={artifacts['transcript']['path']}",
        f"PACKET2N_R5_TRANSCRIPT_SHA256={artifacts['transcript']['sha256']}",
        f"PACKET2N_R5_POST_SOURCE_LEFT_JSON={json.dumps(post['left'], separators=(',', ':'), sort_keys=True)}",
        f"PACKET2N_R5_POST_SOURCE_RIGHT_JSON={json.dumps(post['right'], separators=(',', ':'), sort_keys=True)}",
        *(extra_metadata or []),
        "NO_ROBOT: robot client construction and connection skipped.",
    ]
    for pairs in actions:
        lines.append(f"[NO_ROBOT] action -> {format_python_dict(pairs)}")
    lines.append(
        "Shutdown complete: final zero requested when connected; keyboard, leader buses, robot client, and visualization cleaned up when started."
    )
    lines.extend(extra_before_terminator or [])
    lines.append("CLIENT_EXIT_CODE=0")
    return "\n".join(lines) + "\n"


def base_plan(tmp_path: Path) -> dict[str, object]:
    logs_dir = tmp_path / "logs"
    session_id = "test-session"
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
    transcript_path = logs_dir / f"packet2n-r5-calibration-{session_id}.log"
    evidence_path = logs_dir / f"packet2n-r5-evidence-{session_id}.json"
    left_map_path = logs_dir / f"packet2n-r5-physical-left-only-{session_id}.log"
    right_map_path = logs_dir / f"packet2n-r5-physical-right-only-{session_id}.log"
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
        "session_id": session_id,
        "utc_start": "2026-08-23T00:00:00.0000000Z",
        "head": "545cf933d794657cb3802e3c6a14ead551617a1d",
        "repo_root": str(REPO_ROOT),
        "worktree_clean": True,
        "protected_runtime_paths_unchanged": True,
        "allow_synthetic_map_logs": True,
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
                "source_mtime_utc": powershell_utc_timestamp(left_calibration_path),
            },
            "right": {
                "path": str(right_calibration_path),
                "backup_path": str(right_backup_path),
                "backup_sha256": sha256_path(right_backup_path),
                "backup_size": right_backup_path.stat().st_size,
                "source_mtime_utc": powershell_utc_timestamp(right_calibration_path),
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


def run_calibrate(plan: dict[str, object], tmp_path: Path, state_path: Path) -> subprocess.CompletedProcess[str]:
    return run_runner(
        "-Stage",
        "Calibrate",
        "-StatePath",
        str(state_path),
        "-Confirm",
        "CALIBRATE",
        plan=plan,
        tmp_path=tmp_path,
    )


def run_map_stage(
    stage: str,
    plan: dict[str, object],
    tmp_path: Path,
    state_path: Path,
) -> subprocess.CompletedProcess[str]:
    confirmation = "MAPLEFT" if stage == LEFT_MAP_STAGE else "MAPRIGHT"
    return run_runner(
        "-Stage",
        stage,
        "-StatePath",
        str(state_path),
        "-Confirm",
        confirmation,
        plan=plan,
        tmp_path=tmp_path,
    )


def rewrite_evidence_and_state(state_path: Path, state: dict[str, object], evidence: dict[str, object]) -> None:
    evidence_path = Path(state["artifacts"]["evidence"]["path"])
    write_json(evidence_path, evidence)
    state["artifacts"]["evidence"]["sha256"] = sha256_path(evidence_path)
    state["artifacts"]["evidence"]["size"] = evidence_path.stat().st_size
    write_json(state_path, state)


def expected_native_command(stage: str) -> tuple[str, list[str]]:
    executable = str(REPO_ROOT / ".venv" / "Scripts" / "python.exe")
    if stage == "Calibrate":
        arguments = [
            str(REPO_ROOT / "examples" / "alohamini" / "calibrate_bi.py"),
            "--teleop.left_port",
            LEFT_PORT,
            "--teleop.right_port",
            RIGHT_PORT,
            "--teleop.id",
            LEADER_ID,
            "--teleop.arm_profile",
            ARM_PROFILE,
            "--force_fresh_calibration",
        ]
    else:
        arguments = [
            str(REPO_ROOT / "examples" / "alohamini" / "teleoperate_bi.py"),
            "--teleop.left_port",
            LEFT_PORT,
            "--teleop.right_port",
            RIGHT_PORT,
            "--teleop.id",
            LEADER_ID,
            "--teleop.arm_profile",
            ARM_PROFILE,
            "--no_robot",
            "--robot.robot_model",
            "alohamini1",
            "--require_calibration_match",
            "--duration_s",
            "12",
            "--fps",
            "5",
            "--start_paused",
            "--no_keyboard",
            "--no_rerun",
        ]
    return executable, arguments


def mark_native_stages_completed(state: dict[str, object], *stage_names: str) -> None:
    completed = list(state["completed_stages"])
    for stage in stage_names:
        executable, arguments = expected_native_command(stage)
        state["stages"][stage] = {
            "result": "completed",
            "native": {
                "attempted": True,
                "launched": True,
                "real_exit_code": 0,
                "executable": executable,
                "arguments": arguments,
            },
        }
        if stage not in completed:
            completed.append(stage)
    state["completed_stages"] = completed


def install_actual_map_artifacts(state_path: Path, state: dict[str, object]) -> tuple[Path, Path]:
    left_path = Path(state["artifacts"]["map_left"]["path"])
    right_path = Path(state["artifacts"]["map_right"]["path"])
    write_text(
        left_path,
        make_actual_map_log(marker="PHYSICAL_LEFT_ONLY", state=state, physical_side="left"),
    )
    write_text(
        right_path,
        make_actual_map_log(marker="PHYSICAL_RIGHT_ONLY", state=state, physical_side="right"),
    )
    mark_native_stages_completed(state, LEFT_MAP_STAGE, RIGHT_MAP_STAGE)
    for artifact_name, path in (("map_left", left_path), ("map_right", right_path)):
        state["artifacts"][artifact_name]["sha256"] = sha256_path(path)
    write_json(state_path, state)
    return left_path, right_path


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


@pytest.mark.parametrize(
    "artifact_path",
    [
        ("Calibrate", "transcript_path"),
        ("Calibrate", "evidence_path"),
        (LEFT_MAP_STAGE, "map_path"),
        (RIGHT_MAP_STAGE, "map_path"),
    ],
)
def test_calibrate_refuses_any_preexisting_reserved_artifact_before_state_or_native_attempt(tmp_path, artifact_path):
    plan = base_plan(tmp_path)
    stage, field = artifact_path
    reserved_path = Path(plan["stage_plan"][stage][field])
    write_text(reserved_path, "preexisting\n")
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"

    result = run_calibrate(plan, tmp_path, state_path)

    assert result.returncode != 0
    assert "Refusing to overwrite existing file" in result.stderr
    assert not state_path.exists()


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
    assert state["stages"]["Calibrate"]["native"]["real_exit_code"] is None
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
    left_map_path = Path(plan["stage_plan"][LEFT_MAP_STAGE]["map_path"])
    right_map_path = Path(plan["stage_plan"][RIGHT_MAP_STAGE]["map_path"])
    write_text(
        left_map_path,
        make_actual_map_log(marker="PHYSICAL_LEFT_ONLY", state=state, physical_side="left"),
    )
    write_text(right_map_path, make_partial_log(RIGHT_MAP_STAGE))
    mark_native_stages_completed(state, LEFT_MAP_STAGE, RIGHT_MAP_STAGE)
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


def test_status_without_state_reports_orphaned_fresh_calibration_and_dry_run_plan(tmp_path):
    plan = base_plan(tmp_path)
    write_json(Path(plan["calibration"]["left"]["path"]), make_calibration(400))
    write_json(Path(plan["calibration"]["right"]["path"]), make_calibration(500))
    result = run_runner(
        "-Stage",
        "Status",
        "-StatePath",
        str(tmp_path / "logs" / "missing-state.json"),
        plan=plan,
        tmp_path=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert "ORPHANED_FRESH_CALIBRATION" in result.stdout
    assert "dry-run-only recovery plan" in result.stdout
    assert "preserve orphaned files" in result.stdout
    assert "restore immutable originals only under later exact reviewed authorization" in result.stdout


def test_status_with_malformed_state_reports_invalid_uncertain_and_missing_keys(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    write_json(state_path, {"schema_version": "1"})
    result = run_runner(
        "-Stage",
        "Status",
        "-StatePath",
        str(state_path),
        plan=plan,
        tmp_path=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert "INVALID_OR_UNCERTAIN_STATE" in result.stdout
    assert "missing" in result.stdout
    assert "runner_version" in result.stdout


def test_status_rejects_incomplete_persisted_calibration_session(tmp_path):
    plan = base_plan(tmp_path)
    plan["stage_plan"]["Calibrate"]["launched"] = False
    plan["stage_plan"]["Calibrate"]["exit_code"] = None
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    assert run_calibrate(plan, tmp_path, state_path).returncode != 0

    result = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["classification"] == "INVALID_OR_UNCERTAIN_STATE"
    assert payload["next_stage"] is None
    assert "incomplete" in payload["report"]


def test_status_rejects_uncompleted_reserved_map_artifact(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    assert run_calibrate(plan, tmp_path, state_path).returncode == 0
    state = load_state(state_path)
    write_text(Path(state["artifacts"]["map_left"]["path"]), "invalid partial map\n")

    result = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["classification"] == "INVALID_OR_UNCERTAIN_STATE"
    assert payload["next_stage"] is None
    assert "uncompleted reserved map artifact" in payload["report"]


def test_calibration_records_exact_real_command_array_and_executable(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    result = run_calibrate(plan, tmp_path, state_path)

    assert result.returncode == 0, result.stderr
    state = load_state(state_path)
    native = state["stages"]["Calibrate"]["native"]
    assert native["executable"] == str(REPO_ROOT / ".venv" / "Scripts" / "python.exe")
    assert native["arguments"] == [
        str(REPO_ROOT / "examples" / "alohamini" / "calibrate_bi.py"),
        "--teleop.left_port",
        LEFT_PORT,
        "--teleop.right_port",
        RIGHT_PORT,
        "--teleop.id",
        LEADER_ID,
        "--teleop.arm_profile",
        ARM_PROFILE,
        "--force_fresh_calibration",
    ]


def test_mapping_records_exact_real_command_array_and_executable(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    assert run_calibrate(plan, tmp_path, state_path).returncode == 0
    assert run_map_stage(LEFT_MAP_STAGE, plan, tmp_path, state_path).returncode == 0
    assert run_map_stage(RIGHT_MAP_STAGE, plan, tmp_path, state_path).returncode == 0
    state = load_state(state_path)
    expected_arguments = [
        str(REPO_ROOT / "examples" / "alohamini" / "teleoperate_bi.py"),
        "--teleop.left_port",
        LEFT_PORT,
        "--teleop.right_port",
        RIGHT_PORT,
        "--teleop.id",
        LEADER_ID,
        "--teleop.arm_profile",
        ARM_PROFILE,
        "--no_robot",
        "--robot.robot_model",
        "alohamini1",
        "--require_calibration_match",
        "--duration_s",
        "12",
        "--fps",
        "5",
        "--start_paused",
        "--no_keyboard",
        "--no_rerun",
    ]

    for stage in (LEFT_MAP_STAGE, RIGHT_MAP_STAGE):
        native = state["stages"][stage]["native"]
        assert native["executable"] == str(REPO_ROOT / ".venv" / "Scripts" / "python.exe")
        assert native["arguments"] == expected_arguments


def test_script_defines_real_execution_plan_and_no_unconditional_test_mode_disable():
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "function Get-ExecutionPlan" in source
    assert "Hardware-capable stages are intentionally disabled outside PACKET2N_R5_TEST_MODE=1" not in source


def test_preexisting_map_path_refuses_before_launch_and_keeps_native_unattempted(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    calibrate = run_calibrate(plan, tmp_path, state_path)
    assert calibrate.returncode == 0, calibrate.stderr
    map_path = Path(plan["stage_plan"][LEFT_MAP_STAGE]["map_path"])
    write_text(map_path, "preexisting\n")

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
    assert "Refusing to overwrite existing file" in result.stderr
    state = load_state(state_path)
    assert state["stages"][LEFT_MAP_STAGE]["native"]["attempted"] is False
    assert state["stages"][LEFT_MAP_STAGE]["native"]["real_exit_code"] is None


def test_transcript_tampering_refuses_before_mapping(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    calibrate = run_calibrate(plan, tmp_path, state_path)
    assert calibrate.returncode == 0, calibrate.stderr
    transcript_path = Path(plan["stage_plan"]["Calibrate"]["transcript_path"])
    write_text(transcript_path, transcript_path.read_text(encoding="utf-8") + "tampered\n")

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
    assert "Transcript hash mismatch" in result.stderr


def test_state_repo_head_path_and_runner_sha_mismatches_refuse_later_stage(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    calibrate = run_calibrate(plan, tmp_path, state_path)
    assert calibrate.returncode == 0, calibrate.stderr
    state = load_state(state_path)
    state["repo_head"] = "deadbeef"
    state["state_path"] = str(tmp_path / "logs" / "wrong-state.json")
    state["runner_sha"] = "BADSHA"
    write_json(state_path, state)

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
    assert "State repository provenance is invalid" in result.stderr


def test_verify_accepts_actual_client_log_shape_and_uses_per_key_opposite_family_range(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    calibrate = run_calibrate(plan, tmp_path, state_path)
    assert calibrate.returncode == 0, calibrate.stderr
    state = load_state(state_path)
    left_actions = []
    for index in range(60):
        pairs = actual_action_pairs(physical_side="left", sample_index=index)
        adjusted = []
        for key, value in pairs:
            if key == "arm_right_shoulder_pan.pos":
                adjusted.append((key, -40.0))
            elif key == "arm_right_shoulder_lift.pos":
                adjusted.append((key, -20.0))
            elif key == "arm_right_elbow_flex.pos":
                adjusted.append((key, 0.0))
            elif key == "arm_right_wrist_flex.pos":
                adjusted.append((key, 20.0))
            elif key == "arm_right_wrist_roll.pos":
                adjusted.append((key, 40.0))
            else:
                adjusted.append((key, value))
        left_actions.append(adjusted)
    right_actions = [actual_action_pairs(physical_side="right", sample_index=index) for index in range(60)]

    left_map_path = Path(plan["stage_plan"][LEFT_MAP_STAGE]["map_path"])
    right_map_path = Path(plan["stage_plan"][RIGHT_MAP_STAGE]["map_path"])
    write_text(
        left_map_path,
        make_actual_map_log(
            marker="PHYSICAL_LEFT_ONLY",
            state=state,
            physical_side="left",
            action_pairs=left_actions,
        ),
    )
    write_text(
        right_map_path,
        make_actual_map_log(
            marker="PHYSICAL_RIGHT_ONLY",
            state=state,
            physical_side="right",
            action_pairs=right_actions,
        ),
    )
    mark_native_stages_completed(state, LEFT_MAP_STAGE, RIGHT_MAP_STAGE)
    state["artifacts"]["map_left"] = {"path": str(left_map_path), "sha256": sha256_path(left_map_path)}
    state["artifacts"]["map_right"] = {"path": str(right_map_path), "sha256": sha256_path(right_map_path)}
    write_json(state_path, state)

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


def test_verify_rejects_actual_client_log_with_duplicate_key(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    calibrate = run_calibrate(plan, tmp_path, state_path)
    assert calibrate.returncode == 0, calibrate.stderr
    state = load_state(state_path)
    bad_pairs = actual_action_pairs(physical_side="left", sample_index=0)
    bad_pairs.insert(1, ("arm_left_shoulder_pan.pos", 123.0))
    left_actions = [bad_pairs] + [actual_action_pairs(physical_side="left", sample_index=index) for index in range(1, 60)]
    right_actions = [actual_action_pairs(physical_side="right", sample_index=index) for index in range(60)]
    left_map_path = Path(plan["stage_plan"][LEFT_MAP_STAGE]["map_path"])
    right_map_path = Path(plan["stage_plan"][RIGHT_MAP_STAGE]["map_path"])
    write_text(
        left_map_path,
        make_actual_map_log(
            marker="PHYSICAL_LEFT_ONLY",
            state=state,
            physical_side="left",
            action_pairs=left_actions,
        ),
    )
    write_text(
        right_map_path,
        make_actual_map_log(
            marker="PHYSICAL_RIGHT_ONLY",
            state=state,
            physical_side="right",
            action_pairs=right_actions,
        ),
    )
    mark_native_stages_completed(state, LEFT_MAP_STAGE, RIGHT_MAP_STAGE)
    state["artifacts"]["map_left"] = {"path": str(left_map_path), "sha256": sha256_path(left_map_path)}
    state["artifacts"]["map_right"] = {"path": str(right_map_path), "sha256": sha256_path(right_map_path)}
    write_json(state_path, state)

    verify = run_runner(
        "-Stage",
        "Verify",
        "-StatePath",
        str(state_path),
        plan=plan,
        tmp_path=tmp_path,
    )

    assert verify.returncode != 0
    assert "duplicate key arm_left_shoulder_pan.pos" in verify.stderr


def test_verify_rejects_reversed_or_ambiguous_actual_maps(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    calibrate = run_calibrate(plan, tmp_path, state_path)
    assert calibrate.returncode == 0, calibrate.stderr
    state = load_state(state_path)
    left_map_path = Path(plan["stage_plan"][LEFT_MAP_STAGE]["map_path"])
    right_map_path = Path(plan["stage_plan"][RIGHT_MAP_STAGE]["map_path"])
    write_text(
        left_map_path,
        make_actual_map_log(
            marker="PHYSICAL_LEFT_ONLY",
            state=state,
            physical_side="right",
        ),
    )
    write_text(
        right_map_path,
        make_actual_map_log(
            marker="PHYSICAL_RIGHT_ONLY",
            state=state,
            physical_side="right",
        ),
    )
    mark_native_stages_completed(state, LEFT_MAP_STAGE, RIGHT_MAP_STAGE)
    state["artifacts"]["map_left"] = {"path": str(left_map_path), "sha256": sha256_path(left_map_path)}
    state["artifacts"]["map_right"] = {"path": str(right_map_path), "sha256": sha256_path(right_map_path)}
    write_json(state_path, state)

    verify = run_runner(
        "-Stage",
        "Verify",
        "-StatePath",
        str(state_path),
        plan=plan,
        tmp_path=tmp_path,
    )

    assert verify.returncode != 0
    assert "logical-left classification failed" in verify.stderr


def test_protected_runtime_guard_allows_reviewed_runner_test_doc_changes_only(tmp_path):
    plan = base_plan(tmp_path)
    plan["protected_runtime_paths_unchanged"] = False
    plan["protected_runtime_review"] = {
        "runtime_paths_unchanged": True,
        "allowed_reviewed_paths": [
            "tools/packet2n_r5_leader_mapping.ps1",
            "tests/robots/test_packet2n_r5_leader_mapping.py",
            "docs/alohamini/alohamini.md",
        ],
    }
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

    assert result.returncode == 0, result.stderr


def test_calibrate_reserves_session_bound_artifact_paths_and_map_uses_state_bound_path(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    calibrate = run_calibrate(plan, tmp_path, state_path)

    assert calibrate.returncode == 0, calibrate.stderr
    state = load_state(state_path)
    session_id = state["session_id"]
    assert state["artifacts"]["map_left"] is not None
    assert state["artifacts"]["map_right"] is not None
    assert session_id in state["artifacts"]["transcript"]["path"]
    assert session_id in state["artifacts"]["evidence"]["path"]
    assert session_id in state["artifacts"]["map_left"]["path"]
    assert session_id in state["artifacts"]["map_right"]["path"]
    reserved_map_path = Path(state["artifacts"]["map_left"]["path"])
    diverted_map_path = tmp_path / "logs" / "diverted-map-left.log"
    plan["stage_plan"][LEFT_MAP_STAGE]["map_path"] = str(diverted_map_path)

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
    assert reserved_map_path.exists()
    assert not diverted_map_path.exists()


def test_map_guard_rejects_changed_post_calibration_mtime(tmp_path):
    plan = base_plan(tmp_path)
    left_path = Path(plan["calibration"]["left"]["path"])
    right_path = Path(plan["calibration"]["right"]["path"])
    plan["calibration"]["left"]["source_mtime_utc"] = powershell_utc_timestamp(left_path)
    plan["calibration"]["right"]["source_mtime_utc"] = powershell_utc_timestamp(right_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    calibrate = run_calibrate(plan, tmp_path, state_path)
    assert calibrate.returncode == 0, calibrate.stderr

    newer_seconds = max(left_path.stat().st_mtime, right_path.stat().st_mtime) + 5.0
    os.utime(left_path, (newer_seconds, newer_seconds))
    os.utime(right_path, (newer_seconds, newer_seconds))

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


def test_verify_rejects_actual_log_with_nonzero_body_key(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    calibrate = run_calibrate(plan, tmp_path, state_path)
    assert calibrate.returncode == 0, calibrate.stderr
    state = load_state(state_path)
    bad_pairs = actual_action_pairs(physical_side="left", sample_index=0)
    bad_pairs[-1] = ("lift_axis.vel", 1.0)
    left_actions = [bad_pairs] + [actual_action_pairs(physical_side="left", sample_index=index) for index in range(1, 60)]
    right_actions = [actual_action_pairs(physical_side="right", sample_index=index) for index in range(60)]
    left_map_path = Path(plan["stage_plan"][LEFT_MAP_STAGE]["map_path"])
    right_map_path = Path(plan["stage_plan"][RIGHT_MAP_STAGE]["map_path"])
    write_text(
        left_map_path,
        make_actual_map_log(
            marker="PHYSICAL_LEFT_ONLY",
            state=state,
            physical_side="left",
            action_pairs=left_actions,
        ),
    )
    write_text(
        right_map_path,
        make_actual_map_log(
            marker="PHYSICAL_RIGHT_ONLY",
            state=state,
            physical_side="right",
            action_pairs=right_actions,
        ),
    )
    mark_native_stages_completed(state, LEFT_MAP_STAGE, RIGHT_MAP_STAGE)
    state["artifacts"]["map_left"] = {"path": str(left_map_path), "sha256": sha256_path(left_map_path)}
    state["artifacts"]["map_right"] = {"path": str(right_map_path), "sha256": sha256_path(right_map_path)}
    write_json(state_path, state)

    verify = run_runner(
        "-Stage",
        "Verify",
        "-StatePath",
        str(state_path),
        plan=plan,
        tmp_path=tmp_path,
    )

    assert verify.returncode != 0
    assert "body key lift_axis.vel must be exactly 0" in verify.stderr


def test_verify_rejects_actual_log_with_case_variant_body_key(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    calibrate = run_calibrate(plan, tmp_path, state_path)
    assert calibrate.returncode == 0, calibrate.stderr
    state = load_state(state_path)
    bad_pairs = actual_action_pairs(physical_side="left", sample_index=0)
    bad_pairs[-1] = ("Lift_Axis.vel", 0.0)
    left_actions = [bad_pairs] + [actual_action_pairs(physical_side="left", sample_index=index) for index in range(1, 60)]
    right_actions = [actual_action_pairs(physical_side="right", sample_index=index) for index in range(60)]
    left_map_path = Path(plan["stage_plan"][LEFT_MAP_STAGE]["map_path"])
    right_map_path = Path(plan["stage_plan"][RIGHT_MAP_STAGE]["map_path"])
    write_text(
        left_map_path,
        make_actual_map_log(
            marker="PHYSICAL_LEFT_ONLY",
            state=state,
            physical_side="left",
            action_pairs=left_actions,
        ),
    )
    write_text(
        right_map_path,
        make_actual_map_log(
            marker="PHYSICAL_RIGHT_ONLY",
            state=state,
            physical_side="right",
            action_pairs=right_actions,
        ),
    )
    mark_native_stages_completed(state, LEFT_MAP_STAGE, RIGHT_MAP_STAGE)
    state["artifacts"]["map_left"] = {"path": str(left_map_path), "sha256": sha256_path(left_map_path)}
    state["artifacts"]["map_right"] = {"path": str(right_map_path), "sha256": sha256_path(right_map_path)}
    write_json(state_path, state)

    verify = run_runner(
        "-Stage",
        "Verify",
        "-StatePath",
        str(state_path),
        plan=plan,
        tmp_path=tmp_path,
    )

    assert verify.returncode != 0
    assert "unexpected key Lift_Axis.vel" in verify.stderr


def test_calibrate_rejects_stale_original_post_calibration_without_success_terminator(tmp_path):
    plan = base_plan(tmp_path)
    plan["stage_plan"]["Calibrate"]["post_calibration"] = {
        "left": make_calibration(0),
        "right": make_calibration(10),
    }
    plan["stage_plan"]["Calibrate"]["transcript_text"] = "CALIBRATION START\nCALIBRATION COMPLETE\n"
    plan["stage_plan"]["Calibrate"]["evidence_text"] = json.dumps({"classification": "VALID_FRESH_CALIBRATION"}) + "\n"
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"

    result = run_calibrate(plan, tmp_path, state_path)

    assert result.returncode != 0
    transcript_path = Path(plan["stage_plan"]["Calibrate"]["transcript_path"])
    if transcript_path.exists():
        assert "CALIBRATION_EXIT_CODE=0" not in transcript_path.read_text(encoding="utf-8")


def test_invalid_raw_map_never_gets_success_terminator(tmp_path):
    plan = base_plan(tmp_path)
    plan["stage_plan"][LEFT_MAP_STAGE]["physical_side"] = "right"
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    calibrate = run_calibrate(plan, tmp_path, state_path)
    assert calibrate.returncode == 0, calibrate.stderr

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
    map_path = Path(plan["stage_plan"][LEFT_MAP_STAGE]["map_path"])
    assert map_path.exists()
    assert "CLIENT_EXIT_CODE=0" not in map_path.read_text(encoding="utf-8")


def test_evidence_semantic_tamper_refuses_even_with_rehashed_artifact(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    calibrate = run_calibrate(plan, tmp_path, state_path)
    assert calibrate.returncode == 0, calibrate.stderr
    state = load_state(state_path)
    evidence_path = Path(state["artifacts"]["evidence"]["path"])
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["session_id"] = "other-session"
    write_json(evidence_path, evidence)
    state["artifacts"]["evidence"]["sha256"] = sha256_path(evidence_path)
    write_json(state_path, state)

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
    assert "Evidence semantic validation failed" in result.stderr


def test_status_derives_invalid_from_tampered_state_instead_of_echoing_state_classification(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    calibrate = run_calibrate(plan, tmp_path, state_path)
    assert calibrate.returncode == 0, calibrate.stderr
    state = load_state(state_path)
    state["classification"] = "VALID_FRESH_CALIBRATION"
    transcript_path = Path(state["artifacts"]["transcript"]["path"])
    write_text(transcript_path, transcript_path.read_text(encoding="utf-8") + "tampered\n")
    write_json(state_path, state)

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
    assert payload["classification"] == "INVALID_OR_UNCERTAIN_STATE"
    assert payload["next_stage"] is None


def test_calibration_schema_rejects_string_ids_and_duplicate_joint_ids(tmp_path):
    plan = base_plan(tmp_path)
    left_path = Path(plan["calibration"]["left"]["path"])
    left_calibration = json.loads(left_path.read_text(encoding="utf-8"))
    left_calibration["shoulder_pan"]["id"] = "1"
    left_calibration["shoulder_lift"]["id"] = 1
    write_json(left_path, left_calibration)
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
    assert "calibration id" in result.stderr.lower()


def test_verify_reruns_repo_and_manifest_guards(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    calibrate = run_calibrate(plan, tmp_path, state_path)
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
    plan["worktree_clean"] = False

    verify = run_runner(
        "-Stage",
        "Verify",
        "-StatePath",
        str(state_path),
        plan=plan,
        tmp_path=tmp_path,
    )

    assert verify.returncode != 0
    assert "tracked/untracked worktree" in verify.stderr


def test_real_native_transcript_keeps_operator_prompts_visible_without_pipeline_buffering():
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "Tee-Object -FilePath $OutputPath -Append | Out-Null" not in source
    assert "Start-Transcript -Path $OutputPath -Append" in source
    assert "& $command.executable @($command.arguments) 2>&1 |" not in source
    assert "& $command.executable @($command.arguments)" in source


def test_calibrate_accepts_intended_cross_mapped_offset_order_flip(tmp_path):
    plan = base_plan(tmp_path)
    plan["stage_plan"]["Calibrate"]["post_calibration"] = {
        "left": make_calibration(200),
        "right": make_calibration(100),
    }
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"

    result = run_calibrate(plan, tmp_path, state_path)

    assert result.returncode == 0, result.stderr


def test_calibrate_rejects_post_hash_equal_to_opposite_original(tmp_path):
    plan = base_plan(tmp_path)
    plan["stage_plan"]["Calibrate"]["post_calibration"] = {
        "left": make_calibration(10),
        "right": make_calibration(200),
    }
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"

    result = run_calibrate(plan, tmp_path, state_path)

    assert result.returncode != 0
    assert "both immutable originals" in result.stderr
    transcript = Path(plan["stage_plan"]["Calibrate"]["transcript_path"])
    assert "CALIBRATION_EXIT_CODE=0" not in transcript.read_text(encoding="utf-8")


@pytest.mark.parametrize("mutation", ["tamper", "missing"])
def test_map_right_revalidates_completed_left_map_before_launch(tmp_path, mutation):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    assert run_calibrate(plan, tmp_path, state_path).returncode == 0
    assert run_map_stage(LEFT_MAP_STAGE, plan, tmp_path, state_path).returncode == 0
    state = load_state(state_path)
    left_map = Path(state["artifacts"]["map_left"]["path"])
    if mutation == "tamper":
        write_text(left_map, left_map.read_text(encoding="utf-8") + "tampered\n")
    else:
        left_map.unlink()

    result = run_map_stage(RIGHT_MAP_STAGE, plan, tmp_path, state_path)

    assert result.returncode != 0
    state = load_state(state_path)
    assert state["stages"][RIGHT_MAP_STAGE]["native"]["attempted"] is False


def test_status_revalidates_completed_map_artifacts(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    assert run_calibrate(plan, tmp_path, state_path).returncode == 0
    assert run_map_stage(LEFT_MAP_STAGE, plan, tmp_path, state_path).returncode == 0
    state = load_state(state_path)
    left_map = Path(state["artifacts"]["map_left"]["path"])
    write_text(left_map, left_map.read_text(encoding="utf-8") + "tampered\n")

    result = run_runner(
        "-Stage",
        "Status",
        "-StatePath",
        str(state_path),
        plan=plan,
        tmp_path=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["classification"] == "INVALID_OR_UNCERTAIN_STATE"


def test_protected_runtime_guard_uses_whole_tree_with_exact_reviewed_exclusions():
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "Get-ProtectedRuntimePaths" not in source
    assert '":(exclude)docs/alohamini/alohamini.md"' in source
    assert '":(exclude)tests/robots/test_packet2n_r5_leader_mapping.py"' in source
    assert '":(exclude)tools/packet2n_r5_leader_mapping.ps1"' in source


@pytest.mark.parametrize(
    "field",
    [
        "classification",
        "session_start",
        "behavior",
        "evidence_path",
        "state_path",
        "state_binding",
        "transcript_size",
        "executable",
        "arguments",
        "pre_identity",
        "post_identity",
        "current_identity",
    ],
)
def test_evidence_semantics_reject_every_bound_field_tamper(tmp_path, field):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    assert run_calibrate(plan, tmp_path, state_path).returncode == 0
    state = load_state(state_path)
    evidence_path = Path(state["artifacts"]["evidence"]["path"])
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if field == "classification":
        evidence["classification"] = "ORIGINAL_CALIBRATION_INTACT"
    elif field == "session_start":
        evidence["utc_start"] = "2000-01-01T00:00:00.0000000Z"
    elif field == "behavior":
        evidence["behavior_sha"] = "BADSHA"
    elif field == "evidence_path":
        evidence["evidence_path"] = str(tmp_path / "wrong-evidence.json")
    elif field == "state_path":
        evidence["state_path"] = str(tmp_path / "wrong-state.json")
    elif field == "state_binding":
        evidence["state_session_binding"] = "BADSHA"
    elif field == "transcript_size":
        evidence["transcript_size"] += 1
    elif field == "executable":
        evidence["calibration_executable"] = "C:\\wrong\\python.exe"
    elif field == "arguments":
        evidence["calibration_arguments"] = ["wrong"]
    elif field == "pre_identity":
        evidence["pre_calibration"]["left"]["size"] += 1
    elif field == "post_identity":
        evidence["post_calibration"]["left"]["calibration"]["elbow_flex"]["homing_offset"] += 1
    else:
        evidence["current_identities"]["left"]["mtime_utc"] = "2000-01-01T00:00:00.0000000Z"
    rewrite_evidence_and_state(state_path, state, evidence)

    result = run_map_stage(LEFT_MAP_STAGE, plan, tmp_path, state_path)

    assert result.returncode != 0
    assert "Evidence semantic validation failed" in result.stderr


def test_calibration_transcript_has_exact_header_and_one_final_terminator(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    result = run_calibrate(plan, tmp_path, state_path)
    assert result.returncode == 0, result.stderr
    state = load_state(state_path)
    command = state["stages"]["Calibrate"]["native"]
    lines = Path(state["artifacts"]["transcript"]["path"]).read_text(encoding="utf-8").splitlines()

    assert lines[:5] == [
        f"PACKET2N_R5_SESSION_ID={state['session_id']}",
        f"PACKET2N_R5_SESSION_STARTED_UTC={state['utc_start']}",
        f"PACKET2N_R5_BEHAVIOR_SHA={state['behavior_sha']}",
        f"PACKET2N_R5_CALIBRATION_EXECUTABLE={command['executable']}",
        f"PACKET2N_R5_CALIBRATION_ARGS_JSON={json.dumps(command['arguments'], separators=(',', ':'))}",
    ]
    assert lines[-1] == "CALIBRATION_EXIT_CODE=0"
    assert lines.count("CALIBRATION_EXIT_CODE=0") == 1


def test_rehashed_transcript_header_tamper_is_rejected(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    assert run_calibrate(plan, tmp_path, state_path).returncode == 0
    state = load_state(state_path)
    transcript_path = Path(state["artifacts"]["transcript"]["path"])
    lines = transcript_path.read_text(encoding="utf-8").splitlines()
    lines[0] = "PACKET2N_R5_SESSION_ID=wrong-session"
    write_text(transcript_path, "\n".join(lines) + "\n")
    state["artifacts"]["transcript"]["sha256"] = sha256_path(transcript_path)
    state["artifacts"]["transcript"]["size"] = transcript_path.stat().st_size
    evidence_path = Path(state["artifacts"]["evidence"]["path"])
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["transcript_sha256"] = sha256_path(transcript_path)
    rewrite_evidence_and_state(state_path, state, evidence)

    result = run_map_stage(LEFT_MAP_STAGE, plan, tmp_path, state_path)

    assert result.returncode != 0
    assert "Transcript semantic validation failed" in result.stderr


@pytest.mark.parametrize("artifact_name", ["transcript", "evidence", "map_left"])
def test_state_rejects_session_artifact_path_redirection(tmp_path, artifact_name):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    assert run_calibrate(plan, tmp_path, state_path).returncode == 0
    state = load_state(state_path)
    original_path = Path(state["artifacts"][artifact_name]["path"])
    redirected_path = tmp_path / "logs" / f"redirected-{artifact_name}{original_path.suffix}"
    if original_path.exists():
        write_text(redirected_path, original_path.read_text(encoding="utf-8"))
    state["artifacts"][artifact_name]["path"] = str(redirected_path)
    if artifact_name == "transcript":
        evidence_path = Path(state["artifacts"]["evidence"]["path"])
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["transcript_path"] = str(redirected_path)
        rewrite_evidence_and_state(state_path, state, evidence)
    else:
        write_json(state_path, state)

    result = run_map_stage(LEFT_MAP_STAGE, plan, tmp_path, state_path)

    assert result.returncode != 0
    assert "reserved artifact path" in result.stderr


def test_verify_accepts_complete_exact_packet_metadata(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    assert run_calibrate(plan, tmp_path, state_path).returncode == 0
    state = load_state(state_path)
    install_actual_map_artifacts(state_path, state)

    result = run_runner("-Stage", "Verify", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "MAPPING_RESULT=CORRECT"


@pytest.mark.parametrize("mutation", ["mismatch", "duplicate", "missing", "unexpected"])
def test_verify_rejects_inexact_packet_metadata(tmp_path, mutation):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    assert run_calibrate(plan, tmp_path, state_path).returncode == 0
    state = load_state(state_path)
    left_path = Path(state["artifacts"]["map_left"]["path"])
    left_text = make_actual_map_log(marker="PHYSICAL_LEFT_ONLY", state=state, physical_side="left")
    if mutation == "mismatch":
        left_text = left_text.replace(
            f"PACKET2N_R5_SESSION_ID={state['session_id']}",
            "PACKET2N_R5_SESSION_ID=wrong-session",
        )
    elif mutation == "duplicate":
        left_text = left_text.replace(
            "PACKET2N_R5_GUARD_SUCCESS=1\n",
            "PACKET2N_R5_GUARD_SUCCESS=1\nPACKET2N_R5_GUARD_SUCCESS=1\n",
        )
    elif mutation == "missing":
        left_text = left_text.replace("PACKET2N_R5_GUARD_SUCCESS=1\n", "")
    else:
        left_text = left_text.replace(
            "PACKET2N_R5_GUARD_SUCCESS=1\n",
            "PACKET2N_R5_GUARD_SUCCESS=1\nPACKET2N_R5_UNEXPECTED=1\n",
        )
    write_text(left_path, left_text)
    right_path = Path(state["artifacts"]["map_right"]["path"])
    write_text(right_path, make_actual_map_log(marker="PHYSICAL_RIGHT_ONLY", state=state, physical_side="right"))
    mark_native_stages_completed(state, LEFT_MAP_STAGE, RIGHT_MAP_STAGE)
    for name, path in (("map_left", left_path), ("map_right", right_path)):
        state["artifacts"][name]["sha256"] = sha256_path(path)
    write_json(state_path, state)

    result = run_runner("-Stage", "Verify", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)

    assert result.returncode != 0
    assert "Packet map metadata" in result.stderr


def test_verify_rejects_preexisting_success_terminator_record(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    assert run_calibrate(plan, tmp_path, state_path).returncode == 0
    state = load_state(state_path)
    left_path = Path(state["artifacts"]["map_left"]["path"])
    right_path = Path(state["artifacts"]["map_right"]["path"])
    left_text = make_actual_map_log(
        marker="PHYSICAL_LEFT_ONLY",
        state=state,
        physical_side="left",
        extra_before_terminator=["CLIENT_EXIT_CODE=0"],
    )
    write_text(left_path, left_text)
    write_text(
        right_path,
        make_actual_map_log(
            marker="PHYSICAL_RIGHT_ONLY",
            state=state,
            physical_side="right",
        ),
    )
    mark_native_stages_completed(state, LEFT_MAP_STAGE, RIGHT_MAP_STAGE)
    state["artifacts"]["map_left"] = {"path": str(left_path), "sha256": sha256_path(left_path)}
    state["artifacts"]["map_right"] = {"path": str(right_path), "sha256": sha256_path(right_path)}
    write_json(state_path, state)

    result = run_runner("-Stage", "Verify", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)

    assert result.returncode != 0
    assert "success terminator count mismatch" in result.stderr


def test_production_validation_refuses_synthetic_map_grammar(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    assert run_calibrate(plan, tmp_path, state_path).returncode == 0
    assert run_map_stage(LEFT_MAP_STAGE, plan, tmp_path, state_path).returncode == 0
    assert run_map_stage(RIGHT_MAP_STAGE, plan, tmp_path, state_path).returncode == 0
    plan["allow_synthetic_map_logs"] = False

    result = run_runner("-Stage", "Verify", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)

    assert result.returncode != 0
    assert "synthetic map grammar is test-only" in result.stderr


def test_raw_map_refuses_preexisting_client_exit_record_without_appending_success(tmp_path):
    plan = base_plan(tmp_path)
    plan["stage_plan"][LEFT_MAP_STAGE]["raw_extra_lines"] = ["CLIENT_EXIT_CODE=0"]
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    assert run_calibrate(plan, tmp_path, state_path).returncode == 0

    result = run_map_stage(LEFT_MAP_STAGE, plan, tmp_path, state_path)

    assert result.returncode != 0
    assert "raw log contains a preexisting success terminator record" in result.stderr
    map_text = Path(plan["stage_plan"][LEFT_MAP_STAGE]["map_path"]).read_text(encoding="utf-8")
    assert map_text.count("CLIENT_EXIT_CODE=0") == 1


def test_status_reports_valid_fresh_calibration_and_first_incomplete_stage(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    assert run_calibrate(plan, tmp_path, state_path).returncode == 0

    result = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "classification": "VALID_FRESH_CALIBRATION",
        "next_stage": LEFT_MAP_STAGE,
        "final_result": None,
    }


def test_status_requires_exact_original_mtime(tmp_path):
    plan = base_plan(tmp_path)
    left_path = Path(plan["calibration"]["left"]["path"])
    changed = left_path.stat().st_mtime + 10.0
    os.utime(left_path, (changed, changed))
    state_path = tmp_path / "logs" / "missing-state.json"

    result = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["classification"] == "INVALID_OR_UNCERTAIN_STATE"


def test_lowercase_hardware_confirmation_refuses_before_state_creation(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"

    result = run_runner(
        "-Stage",
        "Calibrate",
        "-StatePath",
        str(state_path),
        "-Confirm",
        "calibrate",
        plan=plan,
        tmp_path=tmp_path,
    )

    assert result.returncode != 0
    assert "requires -Confirm CALIBRATE" in result.stderr
    assert not state_path.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [("leader_id", LEADER_ID.upper()), ("arm_profile", ARM_PROFILE.upper()), ("logical_left", "com8")],
)
def test_state_identity_comparisons_are_case_sensitive(tmp_path, field, value):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    assert run_calibrate(plan, tmp_path, state_path).returncode == 0
    state = load_state(state_path)
    if field == "logical_left":
        state["ports"][field] = value
    else:
        state[field] = value
    write_json(state_path, state)

    result = run_map_stage(LEFT_MAP_STAGE, plan, tmp_path, state_path)

    assert result.returncode != 0


def test_hardware_stage_failure_prints_recovery_classification_and_next_stage(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "missing-state.json"

    result = run_map_stage(LEFT_MAP_STAGE, plan, tmp_path, state_path)

    assert result.returncode != 0
    assert "RECOVERY_CLASSIFICATION=ORIGINAL_CALIBRATION_INTACT" in result.stderr
    assert "RECOVERY_NEXT_STAGE=Calibrate" in result.stderr


def test_test_mode_rejects_calibration_path_outside_validated_root(tmp_path):
    plan = base_plan(tmp_path)
    escaped_path = tmp_path.parent / f"{tmp_path.name}-escaped-left.json"
    write_json(escaped_path, make_calibration(0))
    original_hash = sha256_path(escaped_path)
    plan["calibration"]["left"]["path"] = str(escaped_path)
    plan["calibration"]["left"]["source_mtime_utc"] = powershell_utc_timestamp(escaped_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"

    result = run_calibrate(plan, tmp_path, state_path)

    assert result.returncode != 0
    assert "escaped validated root" in result.stderr
    assert sha256_path(escaped_path) == original_hash


@pytest.mark.parametrize(
    ("container", "field", "value"),
    [
        ("stage", "result", "pending"),
        ("native", "attempted", False),
        ("native", "launched", False),
        ("native", "real_exit_code", None),
        ("native", "real_exit_code", 9),
        ("native", "executable", "C:\\wrong\\python.exe"),
        ("native", "arguments", ["wrong"]),
    ],
)
def test_status_rejects_completed_calibrate_stage_with_inconsistent_truth(tmp_path, container, field, value):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    assert run_calibrate(plan, tmp_path, state_path).returncode == 0
    state = load_state(state_path)
    target = state["stages"]["Calibrate"] if container == "stage" else state["stages"]["Calibrate"]["native"]
    target[field] = value
    write_json(state_path, state)

    result = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["classification"] == "INVALID_OR_UNCERTAIN_STATE"
    assert "Calibrate" in payload["report"]


def test_later_stage_preflight_rejects_inconsistent_completed_stage_before_native_attempt(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    assert run_calibrate(plan, tmp_path, state_path).returncode == 0
    state = load_state(state_path)
    state["stages"]["Calibrate"]["result"] = "pending"
    write_json(state_path, state)

    result = run_map_stage(LEFT_MAP_STAGE, plan, tmp_path, state_path)

    assert result.returncode != 0
    assert "Calibrate" in result.stderr
    assert load_state(state_path)["stages"][LEFT_MAP_STAGE]["native"]["attempted"] is False


@pytest.mark.parametrize("stage", [LEFT_MAP_STAGE, RIGHT_MAP_STAGE])
def test_status_rejects_completed_map_stage_with_inconsistent_native_truth(tmp_path, stage):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    assert run_calibrate(plan, tmp_path, state_path).returncode == 0
    assert run_map_stage(LEFT_MAP_STAGE, plan, tmp_path, state_path).returncode == 0
    if stage == RIGHT_MAP_STAGE:
        assert run_map_stage(RIGHT_MAP_STAGE, plan, tmp_path, state_path).returncode == 0
    state = load_state(state_path)
    state["stages"][stage]["native"]["attempted"] = False
    write_json(state_path, state)

    result = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["classification"] == "INVALID_OR_UNCERTAIN_STATE"
    assert stage in payload["report"]


def test_completed_verify_stage_is_non_native_and_status_rejects_native_tamper(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    assert run_calibrate(plan, tmp_path, state_path).returncode == 0
    assert run_map_stage(LEFT_MAP_STAGE, plan, tmp_path, state_path).returncode == 0
    assert run_map_stage(RIGHT_MAP_STAGE, plan, tmp_path, state_path).returncode == 0
    verify = run_runner("-Stage", "Verify", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)
    assert verify.returncode == 0, verify.stderr
    state = load_state(state_path)
    assert state["stages"]["Verify"] == {
        "result": "completed",
        "native": {
            "attempted": False,
            "launched": False,
            "real_exit_code": None,
            "executable": None,
            "arguments": [],
        },
    }
    state["stages"]["Verify"]["native"]["attempted"] = True
    write_json(state_path, state)

    result = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["classification"] == "INVALID_OR_UNCERTAIN_STATE"
    assert "Verify" in payload["report"]


def test_status_rejects_completed_result_missing_from_completed_stage_list(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    assert run_calibrate(plan, tmp_path, state_path).returncode == 0
    state = load_state(state_path)
    state["stages"][LEFT_MAP_STAGE]["result"] = "completed"
    write_json(state_path, state)

    result = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["classification"] == "INVALID_OR_UNCERTAIN_STATE"
    assert LEFT_MAP_STAGE in payload["report"]


def test_test_mode_rejects_repository_mutable_root_before_any_runner_write(tmp_path):
    repo_sandbox = REPO_ROOT / f".packet2n-r5-test-{sha256_text(str(tmp_path))[:12]}"
    assert not repo_sandbox.exists()
    try:
        plan = base_plan(repo_sandbox)
        state_path = repo_sandbox / "logs" / "packet2n-r5-state.json"
        left_path = Path(plan["calibration"]["left"]["path"])
        original_hash = sha256_path(left_path)

        result = run_calibrate(plan, repo_sandbox, state_path)

        assert result.returncode != 0
        assert "test-mode sandbox" in result.stderr.lower()
        assert sha256_path(left_path) == original_hash
        assert not state_path.exists()
    finally:
        shutil.rmtree(repo_sandbox, ignore_errors=True)


def test_test_mode_explicitly_rejects_real_calibration_root_without_touching_it(tmp_path):
    plan = base_plan(tmp_path)
    original_hashes = {
        side: sha256_path(Path(plan["calibration"][side]["path"])) for side in ("left", "right")
    }
    plan["calibration_root"] = r"C:\Users\pickm\.cache\huggingface\lerobot\calibration"
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"

    result = run_calibrate(plan, tmp_path, state_path)

    assert result.returncode != 0
    assert "production calibration root" in result.stderr
    assert {
        side: sha256_path(Path(plan["calibration"][side]["path"])) for side in ("left", "right")
    } == original_hashes
    assert not state_path.exists()


def test_test_mode_explicitly_rejects_real_logs_root_before_state_creation(tmp_path):
    plan = base_plan(tmp_path)
    plan["state_root"] = r"C:\Users\pickm\AlohaMini1Logs"
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"

    result = run_calibrate(plan, tmp_path, state_path)

    assert result.returncode != 0
    assert "production logs root" in result.stderr
    assert not state_path.exists()


def test_real_native_launch_truth_is_persisted_only_after_direct_invocation_returns():
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    real_executor_start = source.index("if (-not [bool]$Plan.is_test_mode)")
    real_executor_end = source.index("$preexistingLastExitCode = $null", real_executor_start)
    real_executor = source[real_executor_start:real_executor_end]

    attempted_index = real_executor.index("native.attempted = $true")
    invocation_index = real_executor.index("& $command.executable @($command.arguments)")
    launched_index = real_executor.index("native.launched = $true")
    assert attempted_index < invocation_index < launched_index
