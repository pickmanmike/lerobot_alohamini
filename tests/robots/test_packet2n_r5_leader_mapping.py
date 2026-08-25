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
LEGACY_REPO_HEAD = "edc14bbbebb173061cf3b04ead08ffa9fcb81051"
LEGACY_RUNNER_SHA256 = "0BDBDB2F20AD9D47A2B3DBF84924B833E822FE733EA33FAD505753BAD0BE336E"
EXPECTED_BRANCH = "fix/am1-elbow-commissioning"
LEFT_PORT = "COM8"
RIGHT_PORT = "COM7"
LEADER_ID = "so101_leader_bi"
ARM_PROFILE = "so-arm-5dof"
LEFT_MAP_STAGE = "MapLeft"
RIGHT_MAP_STAGE = "MapRight"
RESTART_STAGE = "RestartCalibration"
RESTART_CONFIRMATION = "RECALIBRATE"
REJECTION_REASON = "OPERATOR_REJECTED_INCOMPLETE_RANGE"
INTERRUPTED_STAGE = "RecoverInterruptedCalibration"
INTERRUPTED_CONFIRMATION = "RECOVER"
INTERRUPTED_REASON = "INTERRUPTED_CALIBRATION_RIGHT_BUS_DISCONNECT"
IMPORT_SOURCE_PATHS = {
    "lerobot": REPO_ROOT / "src" / "lerobot" / "__init__.py",
    "calibrate_bi": REPO_ROOT / "examples" / "alohamini" / "calibrate_bi.py",
    "teleoperate_bi": REPO_ROOT / "examples" / "alohamini" / "teleoperate_bi.py",
    "leader_client_utils": REPO_ROOT / "examples" / "alohamini" / "leader_client_utils.py",
    "lerobot.teleoperators.bi_so_leader.bi_so_leader": (
        REPO_ROOT / "src" / "lerobot" / "teleoperators" / "bi_so_leader" / "bi_so_leader.py"
    ),
    "lerobot.teleoperators.so_leader.so_leader": (
        REPO_ROOT / "src" / "lerobot" / "teleoperators" / "so_leader" / "so_leader.py"
    ),
}
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


def make_import_source_probe() -> dict[str, object]:
    python_path = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    site_packages = REPO_ROOT / ".venv" / "Lib" / "site-packages"
    return {
        "exit_code": 0,
        "stderr": [],
        "repository_root": str(REPO_ROOT),
        "cwd": str(REPO_ROOT),
        "python_executable": str(python_path),
        "sys_executable": str(python_path),
        "sys_prefix": str(REPO_ROOT / ".venv"),
        "sys_base_prefix": r"C:\Python312",
        "pythonpath": None,
        "sys_path": [
            str(REPO_ROOT / "examples" / "alohamini"),
            str(site_packages),
            str(REPO_ROOT / "src"),
        ],
        "direct_url": {
            "path": str(site_packages / "lerobot-0.6.1.dist-info" / "direct_url.json"),
            "content": {"url": REPO_ROOT.as_uri(), "dir_info": {"editable": True}},
        },
        "pth_files": [
            {
                "path": str(site_packages / "__editable__.lerobot-0.6.1.pth"),
                "content": f"{REPO_ROOT / 'src'}\n",
                "error": None,
            }
        ],
        "modules": [
            {"name": name, "path": str(path), "error": None} for name, path in IMPORT_SOURCE_PATHS.items()
        ],
    }


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
        "python_path": str(REPO_ROOT / ".venv" / "Scripts" / "python.exe"),
        "import_source_probe": make_import_source_probe(),
        "calibration_root": str(tmp_path / "calibration"),
        "state_root": str(logs_dir),
        "rejected_archive_root": str(tmp_path / "archives"),
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


def run_runner(
    *args: str,
    plan: dict[str, object] | None = None,
    tmp_path: Path | None = None,
    ps_native_error_preference: bool = False,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    assert SCRIPT_PATH.exists(), "the packet2n_r5 leader mapping runner is missing"
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    if plan is not None:
        assert tmp_path is not None
        plan_path = tmp_path / "plan.json"
        write_json(plan_path, plan)
        env["PACKET2N_R5_TEST_MODE"] = "1"
        runner_arguments = [str(SCRIPT_PATH), *args, "-TestPlanPath", str(plan_path)]
        if ps_native_error_preference:
            wrapper_path = tmp_path / "invoke-runner-with-native-errors.ps1"
            parameter_names = {"-Stage", "-StatePath", "-Confirm", "-TestPlanPath"}
            rendered_arguments = [
                argument
                if argument in parameter_names
                else f"'{argument.replace(chr(39), chr(39) * 2)}'"
                for argument in runner_arguments
            ]
            write_text(
                wrapper_path,
                "$PSNativeCommandUseErrorActionPreference = $true\n"
                f"& {' '.join(rendered_arguments)}\n"
                "$RunnerExitCode = $LASTEXITCODE\n"
                '[Console]::Error.WriteLine("PS_NATIVE_PREFERENCE_AFTER=$PSNativeCommandUseErrorActionPreference")\n'
                "exit $RunnerExitCode\n",
            )
            command = [
                "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(wrapper_path),
            ]
        else:
            command = [
                "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                *runner_arguments,
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


def run_diagnose_imports(
    plan: dict[str, object], tmp_path: Path, state_path: Path
) -> subprocess.CompletedProcess[str]:
    return run_runner(
        "-Stage",
        "DiagnoseImports",
        "-StatePath",
        str(state_path),
        plan=plan,
        tmp_path=tmp_path,
    )


def import_probe_module(plan: dict[str, object], module_name: str) -> dict[str, object]:
    return next(record for record in plan["import_source_probe"]["modules"] if record["name"] == module_name)


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


def restart_transaction_paths(
    plan: dict[str, object], state_path: Path, session_id: str
) -> dict[str, Path]:
    active_dir = Path(plan["calibration"]["left"]["path"]).parent
    archive_path = Path(plan["rejected_archive_root"]) / f"packet2n-r5-rejected-{session_id}"
    return {
        "journal": Path(f"{state_path}.restart-calibration.json"),
        "archive": archive_path,
        "archive_staging": Path(f"{archive_path}.staging"),
        "staged_original": active_dir.parent / f".packet2n-r5-original-{session_id}",
        "rollback": active_dir.parent / f".packet2n-r5-rejected-{session_id}",
    }


def run_restart(
    plan: dict[str, object],
    tmp_path: Path,
    state_path: Path,
    *,
    confirmation: str = RESTART_CONFIRMATION,
) -> subprocess.CompletedProcess[str]:
    return run_runner(
        "-Stage",
        RESTART_STAGE,
        "-StatePath",
        str(state_path),
        "-Confirm",
        confirmation,
        plan=plan,
        tmp_path=tmp_path,
    )


def interrupted_transaction_paths(
    plan: dict[str, object], state_path: Path, session_id: str
) -> dict[str, Path]:
    active_dir = Path(plan["calibration"]["left"]["path"]).parent
    archive = Path(plan["rejected_archive_root"]) / f"packet2n-r5-interrupted-{session_id}"
    return {
        "journal": Path(f"{state_path}.recover-interrupted-calibration.json"),
        "archive": archive,
        "archive_staging": Path(f"{archive}.staging"),
        "staged_original": active_dir.parent / f".packet2n-r5-interrupted-original-{session_id}",
        "rollback": active_dir.parent / f".packet2n-r5-interrupted-rejected-{session_id}",
    }


def run_interrupted_recovery(
    plan: dict[str, object],
    tmp_path: Path,
    state_path: Path,
    *,
    confirmation: str = INTERRUPTED_CONFIRMATION,
) -> subprocess.CompletedProcess[str]:
    return run_runner(
        "-Stage",
        INTERRUPTED_STAGE,
        "-StatePath",
        str(state_path),
        "-Confirm",
        confirmation,
        plan=plan,
        tmp_path=tmp_path,
    )


def prepare_interrupted_calibration_candidate(
    tmp_path: Path,
) -> tuple[dict[str, object], Path, dict[str, object], dict[str, bytes], dict[str, bytes]]:
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    plan["stage_plan"]["Calibrate"]["exit_code"] = 1
    failed = run_calibrate(plan, tmp_path, state_path)
    assert failed.returncode != 0
    state = load_state(state_path)
    transcript_path = Path(state["artifacts"]["transcript"]["path"])
    header = "\n".join(
        [
            f"PACKET2N_R5_SESSION_ID={state['session_id']}",
            f"PACKET2N_R5_SESSION_STARTED_UTC={state['utc_start']}",
            f"PACKET2N_R5_BEHAVIOR_SHA={state['behavior_sha']}",
            f"PACKET2N_R5_CALIBRATION_EXECUTABLE={state['stages']['Calibrate']['native']['executable']}",
            "PACKET2N_R5_CALIBRATION_ARGS_JSON="
            + json.dumps(state["stages"]["Calibrate"]["native"]["arguments"], separators=(",", ":")),
            "FAILED WITHOUT NATIVE TRACEBACK",
            "",
        ]
    )
    write_text(transcript_path, header)
    left_path = Path(plan["calibration"]["left"]["path"])
    write_json(left_path, plan["stage_plan"]["Calibrate"]["post_calibration"]["left"])
    fresh_time = datetime(2026, 8, 25, 0, 39, 56, tzinfo=UTC).timestamp()
    os.utime(left_path, (fresh_time, fresh_time))
    state_bytes = state_path.read_bytes()
    active = {
        Path(plan["calibration"][side]["path"]).name: Path(plan["calibration"][side]["path"]).read_bytes()
        for side in ("left", "right")
    }
    originals = {
        Path(plan["calibration"][side]["path"]).name: Path(plan["calibration"][side]["backup_path"]).read_bytes()
        for side in ("left", "right")
    }
    plan["interrupted_legacy_fixture"] = {
        "schema_version": "1",
        "repo_head": state["repo_head"],
        "runner_sha256": state["runner_sha"],
        "behavior_sha": state["behavior_sha"],
        "session_id": state["session_id"],
        "state": {"path": str(state_path), "sha256": sha256_path(state_path), "size": len(state_bytes)},
        "active": {
            side: {
                "path": str(Path(plan["calibration"][side]["path"])),
                "sha256": sha256_path(Path(plan["calibration"][side]["path"])),
                "size": Path(plan["calibration"][side]["path"]).stat().st_size,
                "mtime_utc": powershell_utc_timestamp(Path(plan["calibration"][side]["path"])),
                "calibration": json.loads(Path(plan["calibration"][side]["path"]).read_text(encoding="utf-8")),
            }
            for side in ("left", "right")
        },
        "transcript": {
            "path": str(transcript_path),
            "sha256": sha256_path(transcript_path),
            "size": transcript_path.stat().st_size,
        },
        "source_evidence_present": False,
        "traceback_text_present": False,
    }
    return plan, state_path, state, active, originals


def state_session_binding_sha256(state: dict[str, object]) -> str:
    artifacts = state["artifacts"]
    payload = {
        "session_id": state["session_id"],
        "utc_start": state["utc_start"],
        "state_path": state["state_path"],
        "repo_head": state["repo_head"],
        "runner_sha": state["runner_sha"],
        "behavior_sha": state["behavior_sha"],
        "expected_branch": state["expected_branch"],
        "packet_identity": state["packet_identity"],
        "leader_id": state["leader_id"],
        "arm_profile": state["arm_profile"],
        "ports": {
            name: state["ports"][name]
            for name in ("physical_left", "logical_left", "physical_right", "logical_right")
        },
        "artifact_paths": {
            name: artifacts[name]["path"]
            for name in ("transcript", "evidence", "map_left", "map_right")
        },
    }
    script = (
        "$Value = [Console]::In.ReadToEnd() | ConvertFrom-Json -AsHashtable -Depth 100 -DateKind String; "
        "$Text = $Value | ConvertTo-Json -Depth 100; "
        "$Bytes = [Text.Encoding]::UTF8.GetBytes($Text); "
        "$Sha = [Security.Cryptography.SHA256]::Create(); "
        "try { [Console]::Out.Write(([BitConverter]::ToString($Sha.ComputeHash($Bytes))).Replace('-', '')) } "
        "finally { $Sha.Dispose() }"
    )
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        input=json.dumps(payload, separators=(",", ":")),
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def convert_fresh_state_to_legacy_provenance(state_path: Path) -> dict[str, object]:
    state = load_state(state_path)
    state["repo_head"] = LEGACY_REPO_HEAD
    state["runner_sha"] = LEGACY_RUNNER_SHA256
    state["session_binding_sha256"] = state_session_binding_sha256(state)
    evidence_path = Path(state["artifacts"]["evidence"]["path"])
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["state_session_binding"] = state["session_binding_sha256"]
    rewrite_evidence_and_state(state_path, state, evidence)
    state = load_state(state_path)
    transcript_path = Path(state["artifacts"]["transcript"]["path"])
    return {
        "schema_version": "1",
        "repo_head": LEGACY_REPO_HEAD,
        "runner_sha256": LEGACY_RUNNER_SHA256,
        "behavior_sha": BEHAVIOR_BASELINE,
        "session_id": state["session_id"],
        "state": {
            "path": str(state_path),
            "sha256": sha256_path(state_path),
            "size": state_path.stat().st_size,
        },
        "fresh": state["post_calibration"],
        "evidence": {
            "path": str(evidence_path),
            "sha256": sha256_path(evidence_path),
            "size": evidence_path.stat().st_size,
        },
        "transcript": {
            "path": str(transcript_path),
            "sha256": sha256_path(transcript_path),
            "size": transcript_path.stat().st_size,
        },
        "transcript_body_evaluation": "KNOWN_LIMITATION",
    }


def rewrite_archive_record_and_receipt(archive: Path, record: dict[str, object]) -> None:
    record_path = archive / "archive-record.json"
    write_json(record_path, record)
    receipt_path = archive / "restart-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["archive_record_sha256"] = sha256_path(record_path)
    write_json(receipt_path, receipt)


def update_archive_artifact_identity(record: dict[str, object], name: str, path: Path) -> None:
    artifact = record["artifacts"][name]
    artifact["sha256"] = sha256_path(path)
    artifact["size"] = path.stat().st_size
    artifact["archive_mtime_utc"] = powershell_utc_timestamp(path)


def assert_complete_pair(directory: Path, expected: dict[str, bytes]) -> None:
    assert directory.is_dir()
    assert sorted(path.name for path in directory.iterdir()) == sorted(expected)
    for name, content in expected.items():
        path = directory / name
        assert path.is_file()
        assert path.read_bytes() == content


def create_directory_junction_or_skip(link: Path, target: Path) -> None:
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        pytest.skip(f"temporary junction creation is unavailable: {result.stderr}")


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


def test_diagnose_imports_accepts_the_current_repository_without_state_mutation(tmp_path):
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"

    result = run_runner("-Stage", "DiagnoseImports", "-StatePath", str(state_path))

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["matches"] is True
    assert [record["module"] for record in payload["modules"]] == list(IMPORT_SOURCE_PATHS)
    assert all(record["matches"] for record in payload["modules"])
    assert not state_path.exists()


def test_diagnose_imports_is_offline_and_preserves_calibration_and_runner_state(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    calibration_paths = [Path(plan["calibration"][side]["path"]) for side in ("left", "right")]
    before_hashes = [sha256_path(path) for path in calibration_paths]

    result = run_diagnose_imports(plan, tmp_path, state_path)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["matches"] is True
    assert [sha256_path(path) for path in calibration_paths] == before_hashes
    assert not state_path.exists()
    assert not Path(plan["stage_plan"]["Calibrate"]["transcript_path"]).exists()
    assert not Path(plan["stage_plan"]["Calibrate"]["evidence_path"]).exists()


def test_diagnose_imports_stage_name_is_case_insensitive_without_recovery_side_effects(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"

    result = run_runner(
        "-Stage", "diagnoseimports", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["matches"] is True
    assert "RECOVERY_CLASSIFICATION=" not in result.stderr
    assert not state_path.exists()


@pytest.mark.parametrize(
    ("case", "changed_modules"),
    [
        ("stale external checkout", tuple(IMPORT_SOURCE_PATHS)),
        ("mixed checkout", ("teleoperate_bi",)),
        ("global or user-site lerobot", ("lerobot",)),
    ],
)
def test_diagnose_imports_refuses_external_and_mixed_module_sources(tmp_path, case, changed_modules):
    plan = base_plan(tmp_path)
    external_root = tmp_path / "external-site"
    for module_name in changed_modules:
        external_path = external_root / Path(*module_name.split(".")).with_suffix(".py")
        write_text(external_path, "# external\n")
        import_probe_module(plan, module_name)["path"] = str(external_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"

    result = run_diagnose_imports(plan, tmp_path, state_path)

    assert result.returncode != 0, case
    payload = json.loads(result.stdout)
    assert payload["matches"] is False
    for module_name in changed_modules:
        record = next(record for record in payload["modules"] if record["module"] == module_name)
        assert record["matches"] is False
        assert record["expected_canonical"] == str(IMPORT_SOURCE_PATHS[module_name])
        assert record["actual_canonical"] == str(Path(import_probe_module(plan, module_name)["path"]))
        assert module_name in result.stderr
        assert record["expected_canonical"] in result.stderr
        assert record["actual_canonical"] in result.stderr
    assert not state_path.exists()


def test_diagnose_imports_refuses_inherited_pythonpath_even_when_modules_resolve_locally(tmp_path):
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"

    result = run_runner(
        "-Stage",
        "DiagnoseImports",
        "-StatePath",
        str(state_path),
        extra_env={"PYTHONPATH": str(tmp_path / "contaminating-pythonpath")},
    )

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["matches"] is False
    assert "PYTHONPATH" in result.stderr
    assert "RECOVERY_CLASSIFICATION=" not in result.stderr
    assert "RECOVERY_NEXT_STAGE=" not in result.stderr
    assert not state_path.exists()


def test_diagnose_imports_does_not_launch_python_when_guarded_environment_is_set(tmp_path):
    sentinel_path = tmp_path / "python-child-launched.txt"
    contaminating_path = tmp_path / "contaminating-pythonpath"
    write_text(
        contaminating_path / "sitecustomize.py",
        "from pathlib import Path\n"
        f"Path({str(sentinel_path)!r}).write_text('launched', encoding='utf-8')\n",
    )
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"

    result = run_runner(
        "-Stage",
        "DiagnoseImports",
        "-StatePath",
        str(state_path),
        extra_env={"PYTHONPATH": str(contaminating_path)},
    )

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["matches"] is False
    assert "probe skipped because guarded override variables are set" in payload["failures"]
    assert "PYTHONPATH" in result.stderr
    assert not sentinel_path.exists()
    assert not state_path.exists()


@pytest.mark.parametrize(
    ("direct_url_change", "expected_reason"),
    [
        ({"url": "file:///C:/stale/external/lerobot", "dir_info": {"editable": True}}, "editable URL"),
        ({"url": REPO_ROOT.as_uri(), "dir_info": {"editable": False}}, "editable installation"),
    ],
)
def test_diagnose_imports_rejects_stale_or_noneditable_distribution_metadata(
    tmp_path, direct_url_change, expected_reason
):
    plan = base_plan(tmp_path)
    plan["import_source_probe"]["direct_url"]["content"] = direct_url_change
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"

    result = run_diagnose_imports(plan, tmp_path, state_path)

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["matches"] is False
    assert payload["direct_url"]["matches"] is False
    assert expected_reason in payload["direct_url"]["reason"]
    assert expected_reason in result.stderr
    assert "RECOVERY_CLASSIFICATION=" not in result.stderr
    assert not state_path.exists()


def test_diagnose_imports_accepts_legitimate_windows_path_case_differences(tmp_path):
    plan = base_plan(tmp_path)
    for record in plan["import_source_probe"]["modules"]:
        record["path"] = record["path"].swapcase()
    plan["import_source_probe"]["sys_executable"] = plan["import_source_probe"]["sys_executable"].swapcase()
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"

    result = run_diagnose_imports(plan, tmp_path, state_path)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["matches"] is True


def test_diagnose_imports_rejects_sibling_path_that_only_shares_a_text_prefix(tmp_path):
    plan = base_plan(tmp_path)
    sibling_path = Path(f"{REPO_ROOT}-external") / "src" / "lerobot" / "__init__.py"
    import_probe_module(plan, "lerobot")["path"] = str(sibling_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"

    result = run_diagnose_imports(plan, tmp_path, state_path)

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    record = next(record for record in payload["modules"] if record["module"] == "lerobot")
    assert record["matches"] is False
    assert record["reason"] == "source is outside the intended repository"


@pytest.mark.parametrize(
    ("actual_path", "expected_reason"),
    [
        ("\0invalid", "source path is malformed"),
        (str(REPO_ROOT / "src" / "lerobot" / "missing.py"), "source file does not exist"),
    ],
)
def test_diagnose_imports_clearly_refuses_malformed_or_missing_module_paths(tmp_path, actual_path, expected_reason):
    plan = base_plan(tmp_path)
    import_probe_module(plan, "lerobot")["path"] = actual_path
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"

    result = run_diagnose_imports(plan, tmp_path, state_path)

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    record = next(record for record in payload["modules"] if record["module"] == "lerobot")
    assert record["matches"] is False
    assert record["reason"] == expected_reason
    assert "lerobot" in result.stderr
    assert expected_reason in result.stderr


def test_calibrate_provenance_refusal_is_detailed_and_precedes_state_or_native_action(tmp_path):
    plan = base_plan(tmp_path)
    external_path = tmp_path / "external" / "teleoperate_bi.py"
    write_text(external_path, "# external\n")
    import_probe_module(plan, "teleoperate_bi")["path"] = str(external_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"

    result = run_calibrate(plan, tmp_path, state_path)

    assert result.returncode != 0
    assert "teleoperate_bi" in result.stderr
    assert str(IMPORT_SOURCE_PATHS["teleoperate_bi"]) in result.stderr
    assert str(external_path) in result.stderr
    assert not state_path.exists()
    assert not Path(plan["stage_plan"]["Calibrate"]["transcript_path"]).exists()


def test_diagnose_imports_does_not_change_status_classification(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"

    diagnose = run_diagnose_imports(plan, tmp_path, state_path)
    status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)

    assert diagnose.returncode == 0, diagnose.stderr
    assert status.returncode == 0, status.stderr
    payload = json.loads(status.stdout)
    assert payload["classification"] == "ORIGINAL_CALIBRATION_INTACT"
    assert payload["next_stage"] == "Calibrate"
    assert not state_path.exists()


def test_diagnose_imports_probe_failure_still_returns_actionable_json_without_state(tmp_path):
    plan = base_plan(tmp_path)
    plan["import_source_probe"] = {
        "exit_code": 1,
        "stderr": [],
        "probe_error": "Python import probe exited with status 1",
    }
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"

    result = run_diagnose_imports(plan, tmp_path, state_path)

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["matches"] is False
    assert "Python import probe exited with status 1" in payload["failures"]
    assert payload["direct_url"]["path"] is None
    assert payload["direct_url"]["content"] is None
    assert "Python import probe exited with status 1" in result.stderr
    assert "RECOVERY_CLASSIFICATION=" not in result.stderr
    assert not state_path.exists()


def test_diagnose_imports_refuses_executable_code_in_editable_pth(tmp_path):
    plan = base_plan(tmp_path)
    plan["import_source_probe"]["pth_files"][0]["content"] += "import unexpected_startup_code\n"
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"

    result = run_diagnose_imports(plan, tmp_path, state_path)

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["matches"] is False
    assert payload["pth_files"][0]["matches"] is False
    assert payload["pth_files"][0]["reason"] == "editable .pth contains executable code"
    assert not state_path.exists()


def test_diagnose_imports_malformed_module_records_fail_closed_with_json(tmp_path):
    plan = base_plan(tmp_path)
    plan["import_source_probe"]["modules"] = "malformed"
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"

    result = run_diagnose_imports(plan, tmp_path, state_path)

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["matches"] is False
    assert "module records are malformed" in payload["failures"]
    assert not state_path.exists()


def test_diagnose_imports_requires_boolean_true_editable_metadata(tmp_path):
    plan = base_plan(tmp_path)
    plan["import_source_probe"]["direct_url"]["content"]["dir_info"]["editable"] = "false"
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"

    result = run_diagnose_imports(plan, tmp_path, state_path)

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["matches"] is False
    assert "does not identify an editable installation" in payload["direct_url"]["reason"]
    assert not state_path.exists()


@pytest.mark.parametrize("malformed_field", ["direct_url", "pth_files", "module_record"])
def test_diagnose_imports_malformed_metadata_shapes_fail_closed_with_json(tmp_path, malformed_field):
    plan = base_plan(tmp_path)
    if malformed_field == "direct_url":
        plan["import_source_probe"]["direct_url"] = "malformed"
    elif malformed_field == "pth_files":
        plan["import_source_probe"]["pth_files"] = "malformed"
    else:
        del plan["import_source_probe"]["modules"][0]["error"]
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"

    result = run_diagnose_imports(plan, tmp_path, state_path)

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["matches"] is False
    assert any("malformed" in failure for failure in payload["failures"])
    assert not state_path.exists()


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


def test_calibrate_failure_preserves_primary_error_and_prints_exact_supported_recovery(tmp_path):
    plan = base_plan(tmp_path)
    plan["stage_plan"]["Calibrate"]["exit_code"] = 1
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"

    result = run_calibrate(plan, tmp_path, state_path)

    assert result.returncode != 0
    assert result.stderr.splitlines()[0] == "Calibrate native command failed with exit code 1"
    assert (
        r"pwsh -NoLogo -NoProfile -File .\tools\packet2n_r5_leader_mapping.ps1 "
        r"-Stage RecoverInterruptedCalibration -Confirm RECOVER"
    ) in result.stderr


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
    real_executor_start = source.index("$useDirectNative = (-not [bool]$Plan.is_test_mode)")
    real_executor_end = source.index("$preexistingLastExitCode = $null", real_executor_start)
    real_executor = source[real_executor_start:real_executor_end]

    attempted_index = real_executor.index("native.attempted = $true")
    invocation_index = real_executor.index("& $command.executable @($command.arguments)")
    launched_index = real_executor.index("native.launched = $true")
    assert attempted_index < invocation_index < launched_index


def test_native_exit_seven_is_recorded_when_ps_native_errors_are_terminating(tmp_path):
    plan = base_plan(tmp_path)
    plan["stage_plan"]["Calibrate"]["direct_native_exit_probe"] = 7
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
        ps_native_error_preference=True,
    )

    assert result.returncode != 0
    assert "Calibrate native command failed with exit code 7" in result.stderr
    assert "PS_NATIVE_PREFERENCE_AFTER=True" in result.stderr
    assert "RECOVERY_CLASSIFICATION=INVALID_OR_UNCERTAIN_STATE" in result.stderr
    assert "RECOVERY_NEXT_STAGE=NONE" in result.stderr
    state = load_state(state_path)
    assert state["stages"]["Calibrate"]["result"] == "failed"
    assert state["stages"]["Calibrate"]["native"]["attempted"] is True
    assert state["stages"]["Calibrate"]["native"]["launched"] is True
    assert state["stages"]["Calibrate"]["native"]["real_exit_code"] == 7
    assert state["completed_stages"] == []
    transcript_path = Path(state["artifacts"]["transcript"]["path"])
    assert transcript_path.exists()
    assert "CALIBRATION_EXIT_CODE=0" not in transcript_path.read_text(encoding="utf-8")
    assert not Path(state["artifacts"]["evidence"]["path"]).exists()


def test_test_mode_rejects_temp_junction_before_target_mutation(tmp_path):
    junction = tmp_path / "mutable-junction"
    protected_target = tmp_path.parent / f"{tmp_path.name}-protected-junction-target"
    protected_target.mkdir()
    junction_result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(protected_target)],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    if junction_result.returncode != 0:
        shutil.rmtree(protected_target, ignore_errors=True)
        pytest.skip(f"temporary junction creation is unavailable: {junction_result.stderr}")
    try:
        plan = base_plan(junction)
        state_path = junction / "logs" / "packet2n-r5-state.json"
        target_left = protected_target / "calibration" / "teleoperators" / "so_leader" / f"{LEADER_ID}_left.json"
        original_hash = sha256_path(target_left)

        result = run_calibrate(plan, tmp_path, state_path)

        assert result.returncode != 0
        assert "reparse point" in result.stderr.lower()
        assert sha256_path(target_left) == original_hash
        assert not (protected_target / "logs" / "packet2n-r5-state.json").exists()
    finally:
        if junction.exists():
            os.rmdir(junction)
        shutil.rmtree(protected_target, ignore_errors=True)


def prepare_fresh_restart_candidate(
    tmp_path: Path,
) -> tuple[dict[str, object], Path, dict[str, object], dict[str, bytes], dict[str, bytes]]:
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    result = run_calibrate(plan, tmp_path, state_path)
    assert result.returncode == 0, result.stderr
    state = load_state(state_path)
    active = {
        Path(plan["calibration"][side]["path"]).name: Path(plan["calibration"][side]["path"]).read_bytes()
        for side in ("left", "right")
    }
    originals = {
        Path(plan["calibration"][side]["path"]).name: Path(plan["calibration"][side]["backup_path"]).read_bytes()
        for side in ("left", "right")
    }
    return plan, state_path, state, active, originals


def test_restart_calibration_requires_exact_case_sensitive_confirmation(tmp_path):
    plan, state_path, state, active, _ = prepare_fresh_restart_candidate(tmp_path)
    paths = restart_transaction_paths(plan, state_path, state["session_id"])

    for confirmation in ("", "recalibrate", "RECALIBRATE "):
        args = ["-Stage", RESTART_STAGE, "-StatePath", str(state_path)]
        if confirmation:
            args.extend(["-Confirm", confirmation])
        result = run_runner(*args, plan=plan, tmp_path=tmp_path)
        assert result.returncode != 0
        assert "requires -Confirm RECALIBRATE" in result.stderr
        assert_complete_pair(Path(plan["calibration"]["left"]["path"]).parent, active)
        assert state_path.exists()
        assert not paths["journal"].exists()
        assert not paths["archive"].exists()


def test_valid_fresh_pre_map_session_archives_and_restores_original_pair_offline(tmp_path):
    plan, state_path, state, fresh, originals = prepare_fresh_restart_candidate(tmp_path)
    state_bytes = state_path.read_bytes()
    transcript_path = Path(state["artifacts"]["transcript"]["path"])
    evidence_path = Path(state["artifacts"]["evidence"]["path"])
    transcript_bytes = transcript_path.read_bytes()
    evidence_bytes = evidence_path.read_bytes()
    manifest_path = Path(plan["manifest"]["path"])
    manifest_bytes = manifest_path.read_bytes()
    native_truth = json.loads(json.dumps(state["stages"]))
    paths = restart_transaction_paths(plan, state_path, state["session_id"])

    result = run_restart(plan, tmp_path, state_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "RESTART_CALIBRATION_COMPLETE"
    active_dir = Path(plan["calibration"]["left"]["path"]).parent
    assert_complete_pair(active_dir, originals)
    for side in ("left", "right"):
        active_path = Path(plan["calibration"][side]["path"])
        backup_path = Path(plan["calibration"][side]["backup_path"])
        assert sha256_path(active_path) == sha256_path(backup_path)
        assert active_path.stat().st_size == backup_path.stat().st_size
        assert powershell_utc_timestamp(active_path) == plan["calibration"][side]["source_mtime_utc"]

    archive = paths["archive"]
    assert archive.is_dir()
    assert (archive / "rejected-calibration" / Path(plan["calibration"]["left"]["path"]).name).read_bytes() == fresh[
        Path(plan["calibration"]["left"]["path"]).name
    ]
    assert (archive / "rejected-calibration" / Path(plan["calibration"]["right"]["path"]).name).read_bytes() == fresh[
        Path(plan["calibration"]["right"]["path"]).name
    ]
    assert (archive / "transcript" / transcript_path.name).read_bytes() == transcript_bytes
    assert (archive / "evidence" / evidence_path.name).read_bytes() == evidence_bytes
    assert (archive / "state-snapshot" / state_path.name).read_bytes() == state_bytes
    assert (archive / "immutable-backup" / manifest_path.name).read_bytes() == manifest_bytes
    assert_complete_pair(archive / "retired-active-calibration", fresh)
    assert (archive / "retired-state" / state_path.name).read_bytes() == state_bytes

    record_path = archive / "archive-record.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["schema_version"] == "1"
    assert record["record_type"] == "packet2n-r5-rejected-calibration"
    assert record["reason"] == REJECTION_REASON
    assert record["session_id"] == state["session_id"]
    assert record["session_start_utc"] == state["utc_start"]
    assert record["archive_path"] == str(archive)
    assert record["state_binding_sha256"] == state["session_binding_sha256"]
    assert record["source_provenance"] == {
        "repo_head": state["repo_head"],
        "runner_sha256": state["runner_sha"],
        "behavior_sha": BEHAVIOR_BASELINE,
    }
    assert record["recovery_provenance"] == {
        "repo_head": plan["head"],
        "runner_sha256": sha256_path(SCRIPT_PATH),
        "behavior_sha": BEHAVIOR_BASELINE,
    }
    assert record["immutable_backup"]["manifest"]["sha256"] == sha256_path(manifest_path)
    assert record["immutable_backup"]["left"]["sha256"] == sha256_path(
        Path(plan["calibration"]["left"]["backup_path"])
    )
    assert record["immutable_backup"]["right"]["sha256"] == sha256_path(
        Path(plan["calibration"]["right"]["backup_path"])
    )
    assert record["transcript_validation"] == {
        "header_valid": True,
        "hash_and_size_valid": True,
        "final_terminator_valid": True,
        "native_calibration_output_evaluation": "NOT_EVALUATED",
        "body_contains_native_calibration_output": None,
        "limitation": "Transcript body content was not evaluated for native calibration output.",
    }
    for artifact_name in ("left_calibration", "right_calibration", "transcript", "evidence", "state"):
        artifact = record["artifacts"][artifact_name]
        archive_artifact_path = Path(artifact["archive_path"])
        assert archive_artifact_path.exists()
        assert sha256_path(archive_artifact_path) == artifact["sha256"]
        assert archive_artifact_path.stat().st_size == artifact["size"]
        assert artifact["source_mtime_utc"]
        assert artifact["archive_mtime_utc"]

    receipt = json.loads((archive / "restart-receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "completed"
    assert receipt["reason"] == REJECTION_REASON
    assert receipt["session_id"] == state["session_id"]
    assert receipt["archive_record_sha256"] == sha256_path(record_path)
    assert receipt["active_classification"] == "ORIGINAL_CALIBRATION_INTACT"
    assert receipt["next_stage"] == "Calibrate"
    assert receipt["offline"] is True
    assert receipt["native_stage_truth"] == native_truth

    assert not state_path.exists()
    assert not paths["journal"].exists()
    assert not paths["archive_staging"].exists()
    assert not paths["staged_original"].exists()
    assert not paths["rollback"].exists()
    assert transcript_path.read_bytes() == transcript_bytes
    assert evidence_path.read_bytes() == evidence_bytes
    assert not Path(state["artifacts"]["map_left"]["path"]).exists()
    assert not Path(state["artifacts"]["map_right"]["path"]).exists()

    status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)
    assert status.returncode == 0, status.stderr
    status_payload = json.loads(status.stdout)
    assert status_payload["classification"] == "ORIGINAL_CALIBRATION_INTACT"
    assert status_payload["next_stage"] == "Calibrate"
    assert status_payload["rejected_archives"] == [
        {
            "archive_path": str(archive),
            "reason": REJECTION_REASON,
            "session_id": state["session_id"],
            "verified": True,
        }
    ]


@pytest.mark.parametrize("mapped_state", ["mapped", "verified"])
def test_restart_calibration_refuses_after_mapping_has_begun(tmp_path, mapped_state):
    plan, state_path, state, fresh, _ = prepare_fresh_restart_candidate(tmp_path)
    assert run_map_stage(LEFT_MAP_STAGE, plan, tmp_path, state_path).returncode == 0
    if mapped_state == "verified":
        assert run_map_stage(RIGHT_MAP_STAGE, plan, tmp_path, state_path).returncode == 0
        assert (
            run_runner("-Stage", "Verify", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path).returncode
            == 0
        )
    state = load_state(state_path)
    paths = restart_transaction_paths(plan, state_path, state["session_id"])

    result = run_restart(plan, tmp_path, state_path)

    assert result.returncode != 0
    assert "only exact completed stages [Calibrate]" in result.stderr
    assert_complete_pair(Path(plan["calibration"]["left"]["path"]).parent, fresh)
    assert state_path.exists()
    assert not paths["journal"].exists()
    assert not paths["archive"].exists()


@pytest.mark.parametrize(
    "refusal",
    [
        "changed_current",
        "missing_evidence",
        "malformed_evidence",
        "bad_backup",
        "preexisting_archive",
        "unexpected_active_entry",
    ],
)
def test_restart_calibration_refuses_unsafe_or_non_overwriting_inputs(tmp_path, refusal):
    plan, state_path, state, fresh, _ = prepare_fresh_restart_candidate(tmp_path)
    paths = restart_transaction_paths(plan, state_path, state["session_id"])
    if refusal == "changed_current":
        write_json(Path(plan["calibration"]["left"]["path"]), make_calibration(999))
    elif refusal == "missing_evidence":
        Path(state["artifacts"]["evidence"]["path"]).unlink()
    elif refusal == "malformed_evidence":
        write_text(Path(state["artifacts"]["evidence"]["path"]), "{not-json\n")
    elif refusal == "bad_backup":
        write_json(Path(plan["calibration"]["right"]["backup_path"]), make_calibration(777))
    elif refusal == "preexisting_archive":
        write_text(paths["archive"] / "owner.txt", "preexisting\n")
    else:
        write_text(Path(plan["calibration"]["left"]["path"]).parent / "unexpected.json", "{}\n")
    state_before = state_path.read_bytes()

    result = run_restart(plan, tmp_path, state_path)

    assert result.returncode != 0
    assert state_path.read_bytes() == state_before
    assert not paths["journal"].exists()
    assert not paths["archive_staging"].exists()
    if refusal == "preexisting_archive":
        assert (paths["archive"] / "owner.txt").read_text(encoding="utf-8") == "preexisting\n"
    elif refusal not in {"changed_current", "unexpected_active_entry"}:
        assert_complete_pair(Path(plan["calibration"]["left"]["path"]).parent, fresh)


def test_failure_before_archive_publication_preserves_fresh_state_and_resumes(tmp_path):
    plan, state_path, state, fresh, originals = prepare_fresh_restart_candidate(tmp_path)
    plan["restart_failure_point"] = "before_archive_publish"
    paths = restart_transaction_paths(plan, state_path, state["session_id"])

    failed = run_restart(plan, tmp_path, state_path)

    assert failed.returncode != 0
    assert "TEST FAILURE INJECTION: before_archive_publish" in failed.stderr
    assert_complete_pair(Path(plan["calibration"]["left"]["path"]).parent, fresh)
    assert state_path.exists()
    assert paths["journal"].is_file()
    assert paths["archive_staging"].is_dir()
    assert not paths["archive"].exists()

    status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)
    payload = json.loads(status.stdout)
    assert payload["classification"] == "RESTART_CALIBRATION_RECOVERABLE"
    assert payload["next_stage"] == RESTART_STAGE
    assert payload["restart_transaction"]["session_id"] == state["session_id"]

    blocked = run_map_stage(LEFT_MAP_STAGE, plan, tmp_path, state_path)
    assert blocked.returncode != 0
    assert "incomplete RestartCalibration transaction" in blocked.stderr
    del plan["restart_failure_point"]
    resumed = run_restart(plan, tmp_path, state_path)
    assert resumed.returncode == 0, resumed.stderr
    assert_complete_pair(Path(plan["calibration"]["left"]["path"]).parent, originals)


@pytest.mark.parametrize(
    ("failure_point", "active_layout"),
    [
        ("after_active_directory_move", "missing"),
        ("after_original_directory_move", "original"),
    ],
)
def test_directory_pair_swap_recovers_across_each_atomic_move(tmp_path, failure_point, active_layout):
    plan, state_path, state, fresh, originals = prepare_fresh_restart_candidate(tmp_path)
    plan["restart_failure_point"] = failure_point
    paths = restart_transaction_paths(plan, state_path, state["session_id"])
    active_dir = Path(plan["calibration"]["left"]["path"]).parent

    failed = run_restart(plan, tmp_path, state_path)

    assert failed.returncode != 0
    assert f"TEST FAILURE INJECTION: {failure_point}" in failed.stderr
    assert paths["archive"].is_dir()
    assert state_path.exists()
    assert paths["journal"].is_file()
    assert_complete_pair(paths["rollback"], fresh)
    if active_layout == "missing":
        assert not active_dir.exists()
        assert_complete_pair(paths["staged_original"], originals)
    else:
        assert_complete_pair(active_dir, originals)
        assert not paths["staged_original"].exists()

    status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)
    payload = json.loads(status.stdout)
    assert payload["classification"] == "RESTART_CALIBRATION_RECOVERABLE"
    assert payload["next_stage"] == RESTART_STAGE

    del plan["restart_failure_point"]
    resumed = run_restart(plan, tmp_path, state_path)
    assert resumed.returncode == 0, resumed.stderr
    assert_complete_pair(active_dir, originals)
    assert_complete_pair(paths["archive"] / "retired-active-calibration", fresh)
    assert not state_path.exists()
    assert not paths["journal"].exists()


def test_restart_rerun_refuses_unrecognized_directory_layout_without_mutation(tmp_path):
    plan, state_path, state, fresh, _ = prepare_fresh_restart_candidate(tmp_path)
    plan["restart_failure_point"] = "after_active_directory_move"
    paths = restart_transaction_paths(plan, state_path, state["session_id"])
    active_dir = Path(plan["calibration"]["left"]["path"]).parent
    assert run_restart(plan, tmp_path, state_path).returncode != 0
    active_dir.mkdir()
    lone_path = active_dir / Path(plan["calibration"]["left"]["path"]).name
    lone_path.write_bytes(fresh[lone_path.name])
    rollback_before = {
        path.name: path.read_bytes() for path in paths["rollback"].iterdir()
    }
    del plan["restart_failure_point"]

    status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)
    assert status.returncode == 0, status.stderr
    status_payload = json.loads(status.stdout)
    assert status_payload["classification"] == "INVALID_OR_UNCERTAIN_STATE"
    assert "layout" in status_payload["report"].lower()

    result = run_restart(plan, tmp_path, state_path)

    assert result.returncode != 0
    assert "unrecognized RestartCalibration directory layout" in result.stderr
    assert sorted(path.name for path in active_dir.iterdir()) == [lone_path.name]
    assert lone_path.read_bytes() == fresh[lone_path.name]
    assert {path.name: path.read_bytes() for path in paths["rollback"].iterdir()} == rollback_before
    assert state_path.exists()
    assert paths["journal"].exists()


def test_generic_legacy_premap_session_is_not_restart_authority(tmp_path):
    plan, state_path, state, fresh, _ = prepare_fresh_restart_candidate(tmp_path)
    convert_fresh_state_to_legacy_provenance(state_path)
    paths = restart_transaction_paths(plan, state_path, state["session_id"])

    status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["classification"] == "INVALID_OR_UNCERTAIN_STATE"

    restarted = run_restart(plan, tmp_path, state_path)

    assert restarted.returncode != 0
    assert "State repository provenance is invalid" in restarted.stderr
    assert_complete_pair(Path(plan["calibration"]["left"]["path"]).parent, fresh)
    assert state_path.is_file()
    assert not paths["journal"].exists()
    assert not paths["archive"].exists()


def test_validated_test_plan_can_authorize_one_exact_sandbox_legacy_fixture(tmp_path):
    plan, state_path, _, _, originals = prepare_fresh_restart_candidate(tmp_path)
    plan["restart_legacy_fixture"] = convert_fresh_state_to_legacy_provenance(state_path)

    status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["classification"] == "VALID_FRESH_CALIBRATION"
    mapped = run_map_stage(LEFT_MAP_STAGE, plan, tmp_path, state_path)
    assert mapped.returncode != 0
    assert "State repository provenance is invalid" in mapped.stderr
    assert not Path(load_state(state_path)["artifacts"]["map_left"]["path"]).exists()

    restarted = run_restart(plan, tmp_path, state_path)
    assert restarted.returncode == 0, restarted.stderr
    assert_complete_pair(Path(plan["calibration"]["left"]["path"]).parent, originals)
    archive = restart_transaction_paths(plan, state_path, "test-session")["archive"]
    record = json.loads((archive / "archive-record.json").read_text(encoding="utf-8"))
    assert record["transcript_validation"] == {
        "header_valid": True,
        "hash_and_size_valid": True,
        "final_terminator_valid": True,
        "native_calibration_output_evaluation": "KNOWN_APPROVED_LEGACY_LIMITATION",
        "body_contains_native_calibration_output": False,
        "limitation": "The exact approved legacy transcript is known to contain no native calibration output; its bound header, hash, size, and final terminator are validated.",
    }


@pytest.mark.parametrize(
    "failure_point",
    [
        "after_state_namespace_publish",
        "after_receipt_temp_flush",
        "after_receipt_publish",
    ],
)
def test_exact_legacy_restart_recovers_after_live_state_retirement(tmp_path, failure_point):
    plan, state_path, state, fresh, originals = prepare_fresh_restart_candidate(tmp_path)
    plan["restart_legacy_fixture"] = convert_fresh_state_to_legacy_provenance(state_path)
    state = load_state(state_path)
    paths = restart_transaction_paths(plan, state_path, state["session_id"])
    active_dir = Path(plan["calibration"]["left"]["path"]).parent
    plan["restart_failure_point"] = failure_point

    interrupted = run_restart(plan, tmp_path, state_path)

    assert interrupted.returncode != 0
    assert f"TEST FAILURE INJECTION: {failure_point}" in interrupted.stderr
    assert not state_path.exists()
    assert (paths["archive"] / "state-snapshot" / state_path.name).is_file()
    assert (paths["archive"] / "retired-state" / state_path.name).is_file()
    assert_complete_pair(active_dir, originals)
    assert_complete_pair(paths["archive"] / "retired-active-calibration", fresh)

    interrupted_status = run_runner(
        "-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path
    )

    assert interrupted_status.returncode == 0, interrupted_status.stderr
    interrupted_payload = json.loads(interrupted_status.stdout)
    assert interrupted_payload["classification"] == "RESTART_CALIBRATION_RECOVERABLE", interrupted_payload

    del plan["restart_failure_point"]
    resumed = run_restart(plan, tmp_path, state_path)

    assert resumed.returncode == 0, resumed.stderr
    assert resumed.stdout.strip() == "RESTART_CALIBRATION_COMPLETE"
    assert_complete_pair(active_dir, originals)
    assert_complete_pair(paths["archive"] / "retired-active-calibration", fresh)
    assert not paths["journal"].exists()
    final_status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)
    assert final_status.returncode == 0, final_status.stderr
    final_payload = json.loads(final_status.stdout)
    assert final_payload["classification"] == "ORIGINAL_CALIBRATION_INTACT"
    assert final_payload["next_stage"] == "Calibrate"
    assert final_payload["rejected_archives"] == [
        {
            "archive_path": str(paths["archive"]),
            "reason": REJECTION_REASON,
            "session_id": state["session_id"],
            "verified": True,
        }
    ]


def test_later_calibrate_keeps_verified_rejected_archive_visible_and_am2_files_unchanged(tmp_path):
    plan, state_path, state, _, _ = prepare_fresh_restart_candidate(tmp_path)
    am2_dir = tmp_path / "calibration" / "teleoperators" / "am_leader"
    am2_files = {
        "am_leader_bi_left.json": b"AM2 LEFT\n",
        "am_leader_bi_right.json": b"AM2 RIGHT\n",
        "am2pro_leader_bi_left.json": b"AM2 PRO LEFT\n",
        "am2pro_leader_bi_right.json": b"AM2 PRO RIGHT\n",
    }
    for name, content in am2_files.items():
        write_text(am2_dir / name, content.decode())
    am2_before = {name: sha256_path(am2_dir / name) for name in am2_files}
    archive = restart_transaction_paths(plan, state_path, state["session_id"])["archive"]
    assert run_restart(plan, tmp_path, state_path).returncode == 0
    assert {name: sha256_path(am2_dir / name) for name in am2_files} == am2_before

    plan["session_id"] = "second-session"
    plan["utc_start"] = "2026-08-24T12:00:00.0000000Z"
    calibrated = run_calibrate(plan, tmp_path, state_path)
    assert calibrated.returncode == 0, calibrated.stderr
    status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)
    assert status.returncode == 0, status.stderr
    payload = json.loads(status.stdout)
    assert payload["classification"] == "VALID_FRESH_CALIBRATION"
    assert payload["next_stage"] == LEFT_MAP_STAGE
    assert payload["rejected_archives"] == [
        {
            "archive_path": str(archive),
            "reason": REJECTION_REASON,
            "session_id": state["session_id"],
            "verified": True,
        }
    ]
    assert {name: sha256_path(am2_dir / name) for name in am2_files} == am2_before


def test_restart_refuses_same_so_leader_directory_with_am2_and_am2_pro_files_byte_identically(tmp_path):
    plan, state_path, state, fresh, _ = prepare_fresh_restart_candidate(tmp_path)
    active_dir = Path(plan["calibration"]["left"]["path"]).parent
    extra_files = {
        "am_leader_bi_left.json": b"AM2 LEFT\n",
        "am_leader_bi_right.json": b"AM2 RIGHT\n",
        "am2pro_leader_bi_left.json": b"AM2 PRO LEFT\n",
        "am2pro_leader_bi_right.json": b"AM2 PRO RIGHT\n",
    }
    for name, content in extra_files.items():
        (active_dir / name).write_bytes(content)
    before = {path.name: path.read_bytes() for path in active_dir.iterdir()}
    paths = restart_transaction_paths(plan, state_path, state["session_id"])
    state_before = state_path.read_bytes()

    restarted = run_restart(plan, tmp_path, state_path)

    assert restarted.returncode != 0
    assert "exactly the verified fresh pair" in restarted.stderr
    assert {path.name: path.read_bytes() for path in active_dir.iterdir()} == before
    for name, content in fresh.items():
        assert before[name] == content
    assert state_path.read_bytes() == state_before
    assert not paths["journal"].exists()
    assert not paths["archive"].exists()
    assert not paths["archive_staging"].exists()
    assert not paths["staged_original"].exists()
    assert not paths["rollback"].exists()


@pytest.mark.parametrize("removed", ["retired_pair", "retired_state"])
def test_status_refuses_rejected_archive_missing_retired_artifact(tmp_path, removed):
    plan, state_path, state, _, _ = prepare_fresh_restart_candidate(tmp_path)
    archive = restart_transaction_paths(plan, state_path, state["session_id"])["archive"]
    assert run_restart(plan, tmp_path, state_path).returncode == 0
    if removed == "retired_pair":
        (archive / "retired-active-calibration" / "so101_leader_bi_left.json").unlink()
    else:
        (archive / "retired-state" / state_path.name).unlink()

    status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)

    assert status.returncode == 0, status.stderr
    payload = json.loads(status.stdout)
    assert payload["classification"] == "INVALID_OR_UNCERTAIN_STATE"
    assert "rejected archive" in payload["report"].lower()


def test_status_refuses_orphaned_rejected_archive_staging_directory(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "missing-state.json"
    orphaned_staging = Path(plan["rejected_archive_root"]) / "packet2n-r5-rejected-orphan.staging"
    write_text(orphaned_staging / "partial.txt", "partial\n")

    status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)

    assert status.returncode == 0, status.stderr
    payload = json.loads(status.stdout)
    assert payload["classification"] == "INVALID_OR_UNCERTAIN_STATE"
    assert "staging" in payload["report"].lower()


@pytest.mark.parametrize(
    "failure_point",
    [
        "after_first_archive_copy",
        "after_archive_record_write",
        "after_first_original_copy",
    ],
)
def test_restart_resumes_each_partial_staging_seam_without_touching_active_fresh_pair(tmp_path, failure_point):
    plan, state_path, state, fresh, originals = prepare_fresh_restart_candidate(tmp_path)
    plan["restart_failure_point"] = failure_point
    paths = restart_transaction_paths(plan, state_path, state["session_id"])
    active_dir = Path(plan["calibration"]["left"]["path"]).parent

    failed = run_restart(plan, tmp_path, state_path)

    assert failed.returncode != 0
    assert f"TEST FAILURE INJECTION: {failure_point}" in failed.stderr
    assert_complete_pair(active_dir, fresh)
    assert state_path.is_file()
    assert paths["journal"].is_file()
    journal = json.loads(paths["journal"].read_text(encoding="utf-8"))
    if failure_point == "after_first_archive_copy":
        staged_files = [path for path in paths["archive_staging"].rglob("*") if path.is_file()]
        assert len(staged_files) == 1
        assert not paths["archive"].exists()
        assert journal["archive_record_sha256"] is None
    elif failure_point == "after_archive_record_write":
        assert (paths["archive_staging"] / "archive-record.json").is_file()
        assert not paths["archive"].exists()
        assert journal["archive_record_sha256"] is None
    else:
        staged_original_files = [path for path in paths["staged_original"].iterdir() if path.is_file()]
        assert len(staged_original_files) == 1
        assert paths["archive"].is_dir()

    status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["classification"] == "RESTART_CALIBRATION_RECOVERABLE"

    del plan["restart_failure_point"]
    resumed = run_restart(plan, tmp_path, state_path)

    assert resumed.returncode == 0, resumed.stderr
    assert resumed.stdout.strip() == "RESTART_CALIBRATION_COMPLETE"
    assert_complete_pair(active_dir, originals)
    assert_complete_pair(paths["archive"] / "retired-active-calibration", fresh)
    assert not paths["journal"].exists()


def test_restart_reconstructs_interrupted_archive_copy_from_unchanged_source(tmp_path):
    plan, state_path, state, fresh, originals = prepare_fresh_restart_candidate(tmp_path)
    plan["restart_failure_point"] = "after_first_archive_copy"
    paths = restart_transaction_paths(plan, state_path, state["session_id"])
    active_dir = Path(plan["calibration"]["left"]["path"]).parent
    failed = run_restart(plan, tmp_path, state_path)
    assert failed.returncode != 0
    staged_files = [path for path in paths["archive_staging"].rglob("*") if path.is_file()]
    assert len(staged_files) == 1
    staged_files[0].write_bytes(b"interrupted partial copy")

    status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["classification"] == "RESTART_CALIBRATION_RECOVERABLE"
    assert_complete_pair(active_dir, fresh)
    assert state_path.is_file()

    del plan["restart_failure_point"]
    resumed = run_restart(plan, tmp_path, state_path)

    assert resumed.returncode == 0, resumed.stderr
    assert_complete_pair(active_dir, originals)
    assert_complete_pair(paths["archive"] / "retired-active-calibration", fresh)


@pytest.mark.parametrize(
    "tamper",
    [
        "phase",
        "source_state",
        "source_fresh",
        "state_binding",
        "native_stage_truth",
        "source_provenance",
        "session_start",
        "nested_schema",
    ],
)
def test_status_refuses_tampered_restart_journal_authority(tmp_path, tamper):
    plan, state_path, state, fresh, _ = prepare_fresh_restart_candidate(tmp_path)
    plan["restart_failure_point"] = "before_archive_publish"
    paths = restart_transaction_paths(plan, state_path, state["session_id"])
    assert run_restart(plan, tmp_path, state_path).returncode != 0
    journal = json.loads(paths["journal"].read_text(encoding="utf-8"))
    if tamper == "phase":
        journal["phase"] = "operator_guessed"
    elif tamper == "source_state":
        journal["source_state"]["sha256"] = "0" * 64
    elif tamper == "source_fresh":
        journal["source_fresh"]["left"]["sha256"] = "0" * 64
    elif tamper == "state_binding":
        journal["state_binding_sha256"] = "0" * 64
    elif tamper == "native_stage_truth":
        journal["native_stage_truth"]["Calibrate"]["native"]["attempted"] = False
    elif tamper == "source_provenance":
        journal["source_provenance"]["repo_head"] = "f" * 40
    elif tamper == "session_start":
        journal["session_start_utc"] = "2026-01-01T00:00:00.0000000Z"
    else:
        journal["source_state"]["unexpected"] = True
    write_json(paths["journal"], journal)
    del plan["restart_failure_point"]

    status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)

    assert status.returncode == 0, status.stderr
    payload = json.loads(status.stdout)
    assert payload["classification"] == "INVALID_OR_UNCERTAIN_STATE"
    assert "journal" in payload["report"].lower()
    assert_complete_pair(Path(plan["calibration"]["left"]["path"]).parent, fresh)
    assert state_path.is_file()


def test_status_refuses_valid_phase_that_disagrees_with_physical_layout(tmp_path):
    plan, state_path, state, _, originals = prepare_fresh_restart_candidate(tmp_path)
    plan["restart_failure_point"] = "after_active_directory_move"
    paths = restart_transaction_paths(plan, state_path, state["session_id"])
    assert run_restart(plan, tmp_path, state_path).returncode != 0
    assert_complete_pair(paths["staged_original"], originals)
    state_before = state_path.read_bytes()
    journal = json.loads(paths["journal"].read_text(encoding="utf-8"))
    assert journal["phase"] == "archive_published"
    journal["phase"] = "initialized"
    write_json(paths["journal"], journal)
    del plan["restart_failure_point"]

    status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)

    assert status.returncode == 0, status.stderr
    payload = json.loads(status.stdout)
    assert payload["classification"] == "INVALID_OR_UNCERTAIN_STATE"
    assert "phase" in payload["report"].lower()
    assert state_path.read_bytes() == state_before


def test_restart_refuses_retired_state_junction_before_state_mutation(tmp_path):
    plan, state_path, state, _, originals = prepare_fresh_restart_candidate(tmp_path)
    plan["restart_failure_point"] = "after_original_directory_move"
    paths = restart_transaction_paths(plan, state_path, state["session_id"])
    assert run_restart(plan, tmp_path, state_path).returncode != 0
    active_dir = Path(plan["calibration"]["left"]["path"]).parent
    assert_complete_pair(active_dir, originals)
    state_before = state_path.read_bytes()
    junction = paths["archive"] / "retired-state"
    junction_target = tmp_path / "retired-state-junction-target"
    junction_target.mkdir()
    junction_result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(junction_target)],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    if junction_result.returncode != 0:
        pytest.skip(f"temporary junction creation is unavailable: {junction_result.stderr}")
    del plan["restart_failure_point"]

    try:
        resumed = run_restart(plan, tmp_path, state_path)

        assert resumed.returncode != 0
        assert "reparse point" in resumed.stderr.lower()
        assert state_path.read_bytes() == state_before
        assert list(junction_target.iterdir()) == []
        assert_complete_pair(active_dir, originals)
        assert paths["journal"].is_file()
    finally:
        if junction.exists():
            os.rmdir(junction)
        shutil.rmtree(junction_target, ignore_errors=True)


@pytest.mark.parametrize(
    "tamper",
    ["native_stage_truth", "source_provenance", "recovery_provenance", "completed_utc", "exact_schema"],
)
def test_status_refuses_tampered_completed_receipt_bindings(tmp_path, tamper):
    plan, state_path, state, _, _ = prepare_fresh_restart_candidate(tmp_path)
    archive = restart_transaction_paths(plan, state_path, state["session_id"])["archive"]
    assert run_restart(plan, tmp_path, state_path).returncode == 0
    receipt_path = archive / "restart-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if tamper == "native_stage_truth":
        receipt["native_stage_truth"]["Calibrate"]["native"]["attempted"] = False
    elif tamper == "source_provenance":
        receipt["source_provenance"]["repo_head"] = "f" * 40
    elif tamper == "recovery_provenance":
        receipt["recovery_provenance"]["runner_sha256"] = "0" * 64
    elif tamper == "completed_utc":
        receipt["completed_utc"] = "not-a-timestamp"
    else:
        receipt["unexpected"] = True
    write_json(receipt_path, receipt)

    status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)

    assert status.returncode == 0, status.stderr
    payload = json.loads(status.stdout)
    assert payload["classification"] == "INVALID_OR_UNCERTAIN_STATE"
    assert "receipt" in payload["report"].lower()


@pytest.mark.parametrize(
    "failure_point",
    ["after_initial_journal_temp_flush", "after_receipt_temp_flush", "after_receipt_publish"],
)
def test_restart_durable_write_and_receipt_ordering_seams_resume(tmp_path, failure_point):
    plan, state_path, state, fresh, originals = prepare_fresh_restart_candidate(tmp_path)
    plan["restart_failure_point"] = failure_point
    paths = restart_transaction_paths(plan, state_path, state["session_id"])
    receipt_path = paths["archive"] / "restart-receipt.json"
    journal_temp = Path(f"{paths['journal']}.restart-durable.tmp")
    receipt_temp = Path(f"{receipt_path}.restart-durable.tmp")

    failed = run_restart(plan, tmp_path, state_path)

    assert failed.returncode != 0
    assert f"TEST FAILURE INJECTION: {failure_point}" in failed.stderr
    if failure_point == "after_initial_journal_temp_flush":
        assert journal_temp.is_file()
        assert not paths["journal"].exists()
        assert state_path.is_file()
        assert_complete_pair(Path(plan["calibration"]["left"]["path"]).parent, fresh)
    elif failure_point == "after_receipt_temp_flush":
        assert receipt_temp.is_file()
        assert not receipt_path.exists()
        assert paths["journal"].is_file()
        assert not state_path.exists()
        assert_complete_pair(Path(plan["calibration"]["left"]["path"]).parent, originals)
    else:
        assert receipt_path.is_file()
        assert paths["journal"].is_file()
        assert not state_path.exists()
        assert_complete_pair(Path(plan["calibration"]["left"]["path"]).parent, originals)

    del plan["restart_failure_point"]
    resumed = run_restart(plan, tmp_path, state_path)

    assert resumed.returncode == 0, resumed.stderr
    assert resumed.stdout.strip() == "RESTART_CALIBRATION_COMPLETE"
    assert receipt_path.is_file()
    assert not paths["journal"].exists()
    assert not journal_temp.exists()
    assert not receipt_temp.exists()


@pytest.mark.parametrize("unexpected_kind", ["file", "directory", "premature_receipt_temp"])
def test_status_and_restart_refuse_unexpected_published_archive_entry_without_mutation(
    tmp_path, unexpected_kind
):
    plan, state_path, state, fresh, _ = prepare_fresh_restart_candidate(tmp_path)
    plan["restart_failure_point"] = "after_first_original_copy"
    paths = restart_transaction_paths(plan, state_path, state["session_id"])
    active_dir = Path(plan["calibration"]["left"]["path"]).parent
    assert run_restart(plan, tmp_path, state_path).returncode != 0
    state_before = state_path.read_bytes()
    if unexpected_kind == "file":
        write_text(paths["archive"] / "unexpected.bin", "unexpected\n")
    elif unexpected_kind == "directory":
        write_text(paths["archive"] / "unexpected" / "nested.bin", "unexpected\n")
    else:
        write_text(paths["archive"] / "restart-receipt.json.restart-durable.tmp", "premature\n")
    del plan["restart_failure_point"]

    status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)
    resumed = run_restart(plan, tmp_path, state_path)

    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["classification"] == "INVALID_OR_UNCERTAIN_STATE"
    assert resumed.returncode != 0
    expected_error = "layout" if unexpected_kind == "premature_receipt_temp" else "unexpected"
    assert expected_error in resumed.stderr.lower()
    assert_complete_pair(active_dir, fresh)
    assert state_path.read_bytes() == state_before
    assert paths["journal"].is_file()


def test_status_refuses_wrong_archive_path_type_while_staging_is_partial(tmp_path):
    plan, state_path, state, fresh, _ = prepare_fresh_restart_candidate(tmp_path)
    plan["restart_failure_point"] = "after_first_archive_copy"
    paths = restart_transaction_paths(plan, state_path, state["session_id"])
    active_dir = Path(plan["calibration"]["left"]["path"]).parent
    assert run_restart(plan, tmp_path, state_path).returncode != 0
    state_before = state_path.read_bytes()
    write_text(paths["archive"], "wrong path type\n")
    del plan["restart_failure_point"]

    status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)
    resumed = run_restart(plan, tmp_path, state_path)

    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["classification"] == "INVALID_OR_UNCERTAIN_STATE"
    assert resumed.returncode != 0
    assert "archive" in resumed.stderr.lower()
    assert_complete_pair(active_dir, fresh)
    assert state_path.read_bytes() == state_before
    assert paths["journal"].is_file()


def test_status_and_restart_refuse_junction_backed_archive_artifacts_without_mutation(tmp_path):
    plan, state_path, state, fresh, _ = prepare_fresh_restart_candidate(tmp_path)
    plan["restart_failure_point"] = "after_first_original_copy"
    paths = restart_transaction_paths(plan, state_path, state["session_id"])
    active_dir = Path(plan["calibration"]["left"]["path"]).parent
    assert run_restart(plan, tmp_path, state_path).returncode != 0
    state_before = state_path.read_bytes()
    artifact_directory = paths["archive"] / "rejected-calibration"
    junction_target = tmp_path / "archive-artifact-junction-target"
    shutil.copytree(artifact_directory, junction_target)
    shutil.rmtree(artifact_directory)
    create_directory_junction_or_skip(artifact_directory, junction_target)
    del plan["restart_failure_point"]

    try:
        status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)
        resumed = run_restart(plan, tmp_path, state_path)

        assert status.returncode == 0, status.stderr
        assert json.loads(status.stdout)["classification"] == "INVALID_OR_UNCERTAIN_STATE"
        assert resumed.returncode != 0
        assert "reparse point" in resumed.stderr.lower()
        assert_complete_pair(active_dir, fresh)
        assert state_path.read_bytes() == state_before
        assert paths["journal"].is_file()
    finally:
        if artifact_directory.exists():
            os.rmdir(artifact_directory)
        shutil.rmtree(junction_target, ignore_errors=True)


def test_status_and_restart_identify_reparse_archive_record_before_mutation(tmp_path):
    plan, state_path, state, fresh, _ = prepare_fresh_restart_candidate(tmp_path)
    plan["restart_failure_point"] = "after_first_original_copy"
    paths = restart_transaction_paths(plan, state_path, state["session_id"])
    active_dir = Path(plan["calibration"]["left"]["path"]).parent
    assert run_restart(plan, tmp_path, state_path).returncode != 0
    state_before = state_path.read_bytes()
    record_path = paths["archive"] / "archive-record.json"
    record_path.unlink()
    junction_target = tmp_path / "archive-record-junction-target"
    junction_target.mkdir()
    create_directory_junction_or_skip(record_path, junction_target)
    del plan["restart_failure_point"]

    try:
        status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)
        resumed = run_restart(plan, tmp_path, state_path)

        assert status.returncode == 0, status.stderr
        assert json.loads(status.stdout)["classification"] == "INVALID_OR_UNCERTAIN_STATE"
        assert resumed.returncode != 0
        assert "reparse point" in resumed.stderr.lower()
        assert_complete_pair(active_dir, fresh)
        assert state_path.read_bytes() == state_before
        assert paths["journal"].is_file()
    finally:
        if record_path.exists():
            os.rmdir(record_path)
        shutil.rmtree(junction_target, ignore_errors=True)


def test_reparse_receipt_cannot_retire_live_journal(tmp_path):
    plan, state_path, state, _, originals = prepare_fresh_restart_candidate(tmp_path)
    plan["restart_failure_point"] = "after_receipt_publish"
    paths = restart_transaction_paths(plan, state_path, state["session_id"])
    active_dir = Path(plan["calibration"]["left"]["path"]).parent
    assert run_restart(plan, tmp_path, state_path).returncode != 0
    receipt_path = paths["archive"] / "restart-receipt.json"
    receipt_path.unlink()
    junction_target = tmp_path / "receipt-junction-target"
    junction_target.mkdir()
    create_directory_junction_or_skip(receipt_path, junction_target)
    del plan["restart_failure_point"]

    try:
        status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)
        resumed = run_restart(plan, tmp_path, state_path)

        assert status.returncode == 0, status.stderr
        assert json.loads(status.stdout)["classification"] == "INVALID_OR_UNCERTAIN_STATE"
        assert resumed.returncode != 0
        assert "reparse point" in resumed.stderr.lower()
        assert paths["journal"].is_file()
        assert_complete_pair(active_dir, originals)
        assert not state_path.exists()
        assert list(junction_target.iterdir()) == []
    finally:
        if receipt_path.exists():
            os.rmdir(receipt_path)
        shutil.rmtree(junction_target, ignore_errors=True)


def test_restart_write_through_archive_namespace_publication_seam_resumes(tmp_path):
    plan, state_path, state, fresh, originals = prepare_fresh_restart_candidate(tmp_path)
    plan["restart_failure_point"] = "after_archive_namespace_publish"
    paths = restart_transaction_paths(plan, state_path, state["session_id"])
    active_dir = Path(plan["calibration"]["left"]["path"]).parent

    failed = run_restart(plan, tmp_path, state_path)

    assert failed.returncode != 0
    assert "TEST FAILURE INJECTION: after_archive_namespace_publish" in failed.stderr
    assert paths["archive"].is_dir()
    assert not paths["archive_staging"].exists()
    assert json.loads(paths["journal"].read_text(encoding="utf-8"))["phase"] == "archive_staged"
    assert_complete_pair(active_dir, fresh)
    assert state_path.is_file()

    del plan["restart_failure_point"]
    status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)
    resumed = run_restart(plan, tmp_path, state_path)

    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["classification"] == "RESTART_CALIBRATION_RECOVERABLE"
    assert resumed.returncode == 0, resumed.stderr
    assert_complete_pair(active_dir, originals)
    assert not paths["journal"].exists()


@pytest.mark.parametrize(
    ("first_failure", "second_failure", "normalized_phase", "second_layout"),
    [
        (
            "after_archive_namespace_publish",
            "after_first_original_copy",
            "archive_published",
            "partial_original",
        ),
        (
            "after_active_directory_move",
            "after_original_directory_move",
            "active_withdrawn",
            "original_activated",
        ),
        (
            "after_original_directory_move",
            "after_fresh_pair_namespace_publish",
            "original_activated",
            "fresh_pair_retired",
        ),
        (
            "after_fresh_pair_namespace_publish",
            "after_state_namespace_publish",
            "fresh_pair_retired",
            "state_retired",
        ),
        (
            "after_state_namespace_publish",
            "after_receipt_temp_flush",
            "state_retired",
            "receipt_temp",
        ),
    ],
)
def test_restart_reconciles_journal_before_chained_namespace_interruption(
    tmp_path, first_failure, second_failure, normalized_phase, second_layout
):
    plan, state_path, state, fresh, originals = prepare_fresh_restart_candidate(tmp_path)
    paths = restart_transaction_paths(plan, state_path, state["session_id"])
    active_dir = Path(plan["calibration"]["left"]["path"]).parent
    plan["restart_failure_point"] = first_failure

    first = run_restart(plan, tmp_path, state_path)

    assert first.returncode != 0
    assert f"TEST FAILURE INJECTION: {first_failure}" in first.stderr
    first_status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)
    assert first_status.returncode == 0, first_status.stderr
    assert json.loads(first_status.stdout)["classification"] == "RESTART_CALIBRATION_RECOVERABLE"

    plan["restart_failure_point"] = second_failure
    second = run_restart(plan, tmp_path, state_path)

    assert second.returncode != 0
    assert f"TEST FAILURE INJECTION: {second_failure}" in second.stderr
    journal = json.loads(paths["journal"].read_text(encoding="utf-8"))
    assert journal["phase"] == normalized_phase
    second_status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)
    assert second_status.returncode == 0, second_status.stderr
    assert json.loads(second_status.stdout)["classification"] == "RESTART_CALIBRATION_RECOVERABLE"

    if second_layout == "partial_original":
        assert_complete_pair(active_dir, fresh)
        assert len([path for path in paths["staged_original"].iterdir() if path.is_file()]) == 1
        assert not paths["rollback"].exists()
    elif second_layout == "original_activated":
        assert_complete_pair(active_dir, originals)
        assert_complete_pair(paths["rollback"], fresh)
        assert not paths["staged_original"].exists()
    elif second_layout == "fresh_pair_retired":
        assert_complete_pair(active_dir, originals)
        assert_complete_pair(paths["archive"] / "retired-active-calibration", fresh)
        assert not paths["rollback"].exists()
        assert not paths["staged_original"].exists()
    else:
        assert_complete_pair(active_dir, originals)
        assert_complete_pair(paths["archive"] / "retired-active-calibration", fresh)
        assert not state_path.exists()
        assert (paths["archive"] / "retired-state" / state_path.name).is_file()
        receipt_path = paths["archive"] / "restart-receipt.json"
        if second_layout == "state_retired":
            assert not receipt_path.exists()
            assert not Path(f"{receipt_path}.restart-durable.tmp").exists()
        else:
            assert not receipt_path.exists()
            assert Path(f"{receipt_path}.restart-durable.tmp").is_file()

    del plan["restart_failure_point"]
    third = run_restart(plan, tmp_path, state_path)

    assert third.returncode == 0, third.stderr
    assert third.stdout.strip() == "RESTART_CALIBRATION_COMPLETE"
    assert_complete_pair(active_dir, originals)
    assert_complete_pair(paths["archive"] / "retired-active-calibration", fresh)
    assert not paths["journal"].exists()


@pytest.mark.parametrize(
    "tamper",
    [
        "renamed_calibration",
        "relabeled_source",
        "relabeled_state_source",
        "source_mtime_claims",
        "calibration_identity",
        "full_state",
        "nested_state_schema",
        "pending_native_truth",
        "evidence_semantics",
        "transcript_semantics",
        "manifest_bytes",
    ],
)
def test_status_rejects_self_consistent_archive_claims_not_derived_from_plan_and_state(tmp_path, tamper):
    plan, state_path, state, _, _ = prepare_fresh_restart_candidate(tmp_path)
    archive = restart_transaction_paths(plan, state_path, state["session_id"])["archive"]
    assert run_restart(plan, tmp_path, state_path).returncode == 0
    record_path = archive / "archive-record.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    tampered_native_truth = None

    def sync_archived_state(archived_state: dict[str, object]) -> None:
        snapshot_path = Path(record["artifacts"]["state"]["archive_path"])
        retired_path = archive / "retired-state" / Path(archived_state["state_path"]).name
        write_json(snapshot_path, archived_state)
        write_json(retired_path, archived_state)
        update_archive_artifact_identity(record, "state", snapshot_path)
        record["artifacts"]["state"]["source_mtime_utc"] = powershell_utc_timestamp(retired_path)

    if tamper == "renamed_calibration":
        old_path = Path(record["artifacts"]["left_calibration"]["archive_path"])
        renamed_path = old_path.with_name("renamed-left-calibration.json")
        old_path.rename(renamed_path)
        record["artifacts"]["left_calibration"]["archive_path"] = str(renamed_path)
        update_archive_artifact_identity(record, "left_calibration", renamed_path)
    elif tamper == "relabeled_source":
        source_name = Path(record["artifacts"]["left_calibration"]["source_path"]).name
        record["artifacts"]["left_calibration"]["source_path"] = str(tmp_path / "other-owner" / source_name)
    elif tamper == "relabeled_state_source":
        old_snapshot_path = Path(record["artifacts"]["state"]["archive_path"])
        old_retired_path = archive / "retired-state" / state_path.name
        relabeled_state_path = state_path.with_name("relabeled-state.json")
        relabeled_snapshot_path = old_snapshot_path.with_name(relabeled_state_path.name)
        relabeled_retired_path = old_retired_path.with_name(relabeled_state_path.name)
        archived_state = json.loads(old_snapshot_path.read_text(encoding="utf-8"))
        old_snapshot_path.rename(relabeled_snapshot_path)
        old_retired_path.rename(relabeled_retired_path)
        archived_state["state_path"] = str(relabeled_state_path)
        archived_state["session_binding_sha256"] = state_session_binding_sha256(archived_state)
        evidence_path = Path(record["artifacts"]["evidence"]["archive_path"])
        evidence_mtime_ns = evidence_path.stat().st_mtime_ns
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["state_path"] = str(relabeled_state_path)
        evidence["state_session_binding"] = archived_state["session_binding_sha256"]
        write_json(evidence_path, evidence)
        os.utime(evidence_path, ns=(evidence_mtime_ns, evidence_mtime_ns))
        archived_state["artifacts"]["evidence"]["sha256"] = sha256_path(evidence_path)
        archived_state["artifacts"]["evidence"]["size"] = evidence_path.stat().st_size
        write_json(relabeled_snapshot_path, archived_state)
        write_json(relabeled_retired_path, archived_state)
        record["state_binding_sha256"] = archived_state["session_binding_sha256"]
        record["artifacts"]["state"]["source_path"] = str(relabeled_state_path)
        record["artifacts"]["state"]["archive_path"] = str(relabeled_snapshot_path)
        update_archive_artifact_identity(record, "state", relabeled_snapshot_path)
        record["artifacts"]["state"]["source_mtime_utc"] = powershell_utc_timestamp(relabeled_retired_path)
        update_archive_artifact_identity(record, "evidence", evidence_path)
        record["artifacts"]["evidence"]["source_mtime_utc"] = powershell_utc_timestamp(evidence_path)
    elif tamper == "source_mtime_claims":
        preserved_source_mtimes = {
            name: powershell_utc_timestamp(Path(record["artifacts"][name]["archive_path"]))
            for name in ("transcript", "evidence")
        }
        assert {
            name: record["artifacts"][name]["source_mtime_utc"]
            for name in ("transcript", "evidence")
        } == preserved_source_mtimes
        record["artifacts"]["transcript"]["source_mtime_utc"] = "2001-01-01T00:00:00.0000000Z"
        record["artifacts"]["evidence"]["source_mtime_utc"] = "2002-01-01T00:00:00.0000000Z"
    elif tamper == "calibration_identity":
        archived_path = Path(record["artifacts"]["left_calibration"]["archive_path"])
        retired_path = archive / "retired-active-calibration" / archived_path.name
        write_json(archived_path, make_calibration(901))
        shutil.copy2(archived_path, retired_path)
        update_archive_artifact_identity(record, "left_calibration", archived_path)
        record["artifacts"]["left_calibration"]["source_mtime_utc"] = powershell_utc_timestamp(retired_path)
    elif tamper == "full_state":
        snapshot_path = Path(record["artifacts"]["state"]["archive_path"])
        archived_state = json.loads(snapshot_path.read_text(encoding="utf-8"))
        archived_state["unbound_archive_claim"] = "tampered"
        sync_archived_state(archived_state)
    elif tamper == "nested_state_schema":
        snapshot_path = Path(record["artifacts"]["state"]["archive_path"])
        archived_state = json.loads(snapshot_path.read_text(encoding="utf-8"))
        archived_state["artifacts"]["transcript"]["unbound_archive_claim"] = "tampered"
        sync_archived_state(archived_state)
    elif tamper == "pending_native_truth":
        snapshot_path = Path(record["artifacts"]["state"]["archive_path"])
        archived_state = json.loads(snapshot_path.read_text(encoding="utf-8"))
        archived_state["stages"][LEFT_MAP_STAGE]["native"] = {
            "attempted": True,
            "launched": True,
            "real_exit_code": 0,
            "executable": "invented.exe",
            "arguments": ["--invented"],
        }
        tampered_native_truth = archived_state["stages"]
        sync_archived_state(archived_state)
    elif tamper == "evidence_semantics":
        evidence_path = Path(record["artifacts"]["evidence"]["archive_path"])
        evidence_mtime_ns = evidence_path.stat().st_mtime_ns
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["unbound_archive_claim"] = "tampered"
        write_json(evidence_path, evidence)
        os.utime(evidence_path, ns=(evidence_mtime_ns, evidence_mtime_ns))
        update_archive_artifact_identity(record, "evidence", evidence_path)
    elif tamper == "transcript_semantics":
        transcript_path = Path(record["artifacts"]["transcript"]["archive_path"])
        transcript_mtime_ns = transcript_path.stat().st_mtime_ns
        transcript_lines = transcript_path.read_text(encoding="utf-8").splitlines()
        transcript_lines[0] = "TAMPERED_TRANSCRIPT_HEADER=1"
        write_text(transcript_path, "\n".join(transcript_lines) + "\n")
        os.utime(transcript_path, ns=(transcript_mtime_ns, transcript_mtime_ns))
        evidence_path = Path(record["artifacts"]["evidence"]["archive_path"])
        evidence_mtime_ns = evidence_path.stat().st_mtime_ns
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["transcript_sha256"] = sha256_path(transcript_path)
        evidence["transcript_size"] = transcript_path.stat().st_size
        write_json(evidence_path, evidence)
        os.utime(evidence_path, ns=(evidence_mtime_ns, evidence_mtime_ns))
        snapshot_path = Path(record["artifacts"]["state"]["archive_path"])
        archived_state = json.loads(snapshot_path.read_text(encoding="utf-8"))
        archived_state["artifacts"]["transcript"]["sha256"] = sha256_path(transcript_path)
        archived_state["artifacts"]["transcript"]["size"] = transcript_path.stat().st_size
        archived_state["artifacts"]["evidence"]["sha256"] = sha256_path(evidence_path)
        archived_state["artifacts"]["evidence"]["size"] = evidence_path.stat().st_size
        sync_archived_state(archived_state)
        update_archive_artifact_identity(record, "transcript", transcript_path)
        update_archive_artifact_identity(record, "evidence", evidence_path)
    else:
        manifest = record["immutable_backup"]["manifest"]
        manifest_path = Path(manifest["archive_path"])
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_payload["unbound_archive_claim"] = "tampered"
        write_json(manifest_path, manifest_payload)
        manifest["sha256"] = sha256_path(manifest_path)
        manifest["size"] = manifest_path.stat().st_size
        manifest["archive_mtime_utc"] = powershell_utc_timestamp(manifest_path)

    rewrite_archive_record_and_receipt(archive, record)
    if tampered_native_truth is not None:
        receipt_path = archive / "restart-receipt.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["native_stage_truth"] = tampered_native_truth
        write_json(receipt_path, receipt)
    active_dir = Path(plan["calibration"]["left"]["path"]).parent
    active_before_status = {path.name: path.read_bytes() for path in active_dir.iterdir()}
    archive_before_status = {
        str(path.relative_to(archive)): path.read_bytes() for path in archive.rglob("*") if path.is_file()
    }

    status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)

    assert status.returncode == 0, status.stderr
    payload = json.loads(status.stdout)
    assert payload["classification"] == "INVALID_OR_UNCERTAIN_STATE"
    if tamper == "transcript_semantics":
        assert "transcript semantic validation failed" in payload["report"].lower()
    else:
        assert "rejected archive" in payload["report"].lower()
    assert {path.name: path.read_bytes() for path in active_dir.iterdir()} == active_before_status
    assert {str(path.relative_to(archive)): path.read_bytes() for path in archive.rglob("*") if path.is_file()} == archive_before_status
    assert not state_path.exists()
    assert not Path(f"{state_path}.restart-calibration.json").exists()


def test_interrupted_recovery_requires_exact_confirmation_without_mutation(tmp_path):
    plan, state_path, _, active, _ = prepare_interrupted_calibration_candidate(tmp_path)
    paths = interrupted_transaction_paths(plan, state_path, "test-session")
    state_before = state_path.read_bytes()

    for confirmation in ("", "recover", "RECOVER "):
        result = run_interrupted_recovery(plan, tmp_path, state_path, confirmation=confirmation)
        assert result.returncode != 0
        assert "requires -Confirm RECOVER" in result.stderr
        assert_complete_pair(Path(plan["calibration"]["left"]["path"]).parent, active)
        assert state_path.read_bytes() == state_before
        assert not paths["journal"].exists()
        assert not paths["archive"].exists()


def test_exact_interrupted_candidate_archives_mixed_pair_and_restores_original_directory_offline(tmp_path):
    plan, state_path, state, mixed, originals = prepare_interrupted_calibration_candidate(tmp_path)
    transcript_path = Path(state["artifacts"]["transcript"]["path"])
    transcript_bytes = transcript_path.read_bytes()
    state_bytes = state_path.read_bytes()
    manifest_path = Path(plan["manifest"]["path"])
    manifest_bytes = manifest_path.read_bytes()
    paths = interrupted_transaction_paths(plan, state_path, state["session_id"])
    active_dir = Path(plan["calibration"]["left"]["path"]).parent
    am2_dir = tmp_path / "calibration" / "teleoperators" / "am_leader"
    am2 = {"am_leader_bi_left.json": b"AM2 LEFT\n", "am2pro_leader_bi_right.json": b"AM2 PRO RIGHT\n"}
    for name, content in am2.items():
        (am2_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (am2_dir / name).write_bytes(content)

    result = run_interrupted_recovery(plan, tmp_path, state_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "INTERRUPTED_CALIBRATION_RECOVERY_COMPLETE"
    assert_complete_pair(active_dir, originals)
    archive = paths["archive"]
    for side in ("left", "right"):
        name = Path(plan["calibration"][side]["path"]).name
        assert (archive / "interrupted-active-calibration" / name).read_bytes() == mixed[name]
    assert (archive / "failed-transcript" / transcript_path.name).read_bytes() == transcript_bytes
    assert (archive / "state-snapshot" / state_path.name).read_bytes() == state_bytes
    assert (archive / "retired-state" / state_path.name).read_bytes() == state_bytes
    assert (archive / "immutable-backup" / manifest_path.name).read_bytes() == manifest_bytes
    assert_complete_pair(archive / "retired-active-calibration", mixed)
    evidence = json.loads((archive / "interrupted-evidence.json").read_text(encoding="utf-8"))
    assert evidence["source_evidence_present"] is False
    assert evidence["traceback_text_present"] is False
    assert evidence["native_exit_code"] == 1
    assert evidence["mapping_eligible"] is False
    assert evidence["rejected"] is True
    record = json.loads((archive / "archive-record.json").read_text(encoding="utf-8"))
    assert record["record_type"] == "packet2n-r5-interrupted-calibration"
    assert record["reason"] == INTERRUPTED_REASON
    assert record["session_id"] == state["session_id"]
    assert record["ports"] == state["ports"]
    assert record["native_stage_truth"] == state["stages"]
    assert record["source_provenance"]["repo_head"] == state["repo_head"]
    assert record["recovery_provenance"]["runner_sha256"] == sha256_path(SCRIPT_PATH)
    assert json.loads((archive / "recovery-receipt.json").read_text(encoding="utf-8"))["verified"] is True
    assert not state_path.exists()
    assert not paths["journal"].exists()
    assert {name: (am2_dir / name).read_bytes() for name in am2} == am2
    assert all(stage["native"]["attempted"] is (name == "Calibrate") for name, stage in state["stages"].items())

    status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)
    assert status.returncode == 0, status.stderr
    payload = json.loads(status.stdout)
    assert payload["classification"] == "ORIGINAL_CALIBRATION_INTACT"
    assert payload["next_stage"] == "Calibrate"
    assert payload["interrupted_archives"] == [
        {
            "archive_path": str(archive),
            "reason": INTERRUPTED_REASON,
            "session_id": state["session_id"],
            "verified": True,
            "mapping_eligible": False,
        }
    ]


@pytest.mark.parametrize(
    "refusal",
    [
        "map_artifact",
        "changed_active",
        "bad_immutable",
        "missing_state",
        "missing_transcript",
        "source_evidence_present",
        "wrong_provenance",
        "wrong_imports",
    ],
)
def test_interrupted_recovery_refuses_inexact_authority_without_mutation(tmp_path, refusal):
    plan, state_path, state, active, _ = prepare_interrupted_calibration_candidate(tmp_path)
    paths = interrupted_transaction_paths(plan, state_path, state["session_id"])
    if refusal == "map_artifact":
        write_text(Path(state["artifacts"]["map_left"]["path"]), "map must not exist\n")
    elif refusal == "changed_active":
        write_json(Path(plan["calibration"]["left"]["path"]), make_calibration(999))
    elif refusal == "bad_immutable":
        write_json(Path(plan["calibration"]["right"]["backup_path"]), make_calibration(999))
    elif refusal == "missing_state":
        state_path.unlink()
    elif refusal == "missing_transcript":
        Path(state["artifacts"]["transcript"]["path"]).unlink()
    elif refusal == "source_evidence_present":
        write_json(Path(state["artifacts"]["evidence"]["path"]), {"unexpected": True})
    elif refusal == "wrong_provenance":
        plan["interrupted_legacy_fixture"]["repo_head"] = "0" * 40
    else:
        import_probe_module(plan, "lerobot")["path"] = str(tmp_path / "external" / "lerobot" / "__init__.py")
    state_before = state_path.read_bytes() if state_path.exists() else None
    active_dir = Path(plan["calibration"]["left"]["path"]).parent
    active_before = {path.name: path.read_bytes() for path in active_dir.iterdir()}

    result = run_interrupted_recovery(plan, tmp_path, state_path)

    assert result.returncode != 0
    assert (state_path.read_bytes() if state_path.exists() else None) == state_before
    assert {path.name: path.read_bytes() for path in active_dir.iterdir()} == active_before
    assert not paths["journal"].exists()
    assert not paths["archive"].exists()
    assert not paths["archive_staging"].exists()
    if refusal not in {"changed_active", "bad_immutable"}:
        assert active_before == active


@pytest.mark.parametrize(
    "failure_point",
    [
        "after_archive_namespace_publish",
        "after_active_directory_move",
        "after_original_directory_move",
        "after_fresh_pair_namespace_publish",
        "after_state_namespace_publish",
        "after_receipt_publish",
    ],
)
def test_interrupted_recovery_resumes_every_namespace_seam(tmp_path, failure_point):
    plan, state_path, state, mixed, originals = prepare_interrupted_calibration_candidate(tmp_path)
    paths = interrupted_transaction_paths(plan, state_path, state["session_id"])
    active_dir = Path(plan["calibration"]["left"]["path"]).parent
    plan["restart_failure_point"] = failure_point

    interrupted = run_interrupted_recovery(plan, tmp_path, state_path)

    assert interrupted.returncode != 0
    assert f"TEST FAILURE INJECTION: {failure_point}" in interrupted.stderr
    assert paths["journal"].is_file()
    status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["classification"] == "INTERRUPTED_CALIBRATION_RECOVERABLE"
    blocked = run_runner("-Stage", "Verify", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)
    assert blocked.returncode != 0
    assert "interrupted-calibration recovery transaction" in blocked.stderr

    del plan["restart_failure_point"]
    resumed = run_interrupted_recovery(plan, tmp_path, state_path)

    assert resumed.returncode == 0, resumed.stderr
    assert resumed.stdout.strip() == "INTERRUPTED_CALIBRATION_RECOVERY_COMPLETE"
    assert_complete_pair(active_dir, originals)
    assert_complete_pair(paths["archive"] / "retired-active-calibration", mixed)
    assert not paths["journal"].exists()


def test_interrupted_recovery_resumes_after_receipt_temp_flush(tmp_path):
    plan, state_path, state, mixed, originals = prepare_interrupted_calibration_candidate(tmp_path)
    paths = interrupted_transaction_paths(plan, state_path, state["session_id"])
    receipt = paths["archive"] / "recovery-receipt.json"
    receipt_temp = Path(f"{receipt}.restart-durable.tmp")
    plan["restart_failure_point"] = "after_receipt_temp_flush"

    interrupted = run_interrupted_recovery(plan, tmp_path, state_path)

    assert interrupted.returncode != 0
    assert "TEST FAILURE INJECTION: after_receipt_temp_flush" in interrupted.stderr
    assert paths["journal"].is_file()
    assert receipt_temp.is_file()
    assert not receipt.exists()
    assert not state_path.exists()
    assert_complete_pair(Path(plan["calibration"]["left"]["path"]).parent, originals)
    assert_complete_pair(paths["archive"] / "retired-active-calibration", mixed)
    del plan["restart_failure_point"]

    resumed = run_interrupted_recovery(plan, tmp_path, state_path)

    assert resumed.returncode == 0, resumed.stderr
    assert resumed.stdout.strip() == "INTERRUPTED_CALIBRATION_RECOVERY_COMPLETE"
    assert receipt.is_file()
    assert not receipt_temp.exists()
    assert not paths["journal"].exists()


@pytest.mark.parametrize("relabelled", ["transcript", "active_left"])
def test_interrupted_recovery_resume_rejects_relabelled_pinned_journal_authority(tmp_path, relabelled):
    plan, state_path, state, mixed, _ = prepare_interrupted_calibration_candidate(tmp_path)
    paths = interrupted_transaction_paths(plan, state_path, state["session_id"])
    plan["restart_failure_point"] = "after_archive_namespace_publish"
    assert run_interrupted_recovery(plan, tmp_path, state_path).returncode != 0
    del plan["restart_failure_point"]
    journal = json.loads(paths["journal"].read_text(encoding="utf-8"))
    if relabelled == "transcript":
        journal["source_transcript"]["path"] = str(tmp_path / "relabeled-transcript.log")
    else:
        journal["source_active"]["left"]["path"] = str(tmp_path / "relabeled-left.json")
    write_json(paths["journal"], journal)
    archive_before = {
        str(path.relative_to(paths["archive"])): path.read_bytes()
        for path in paths["archive"].rglob("*")
        if path.is_file()
    }

    resumed = run_interrupted_recovery(plan, tmp_path, state_path)

    assert resumed.returncode != 0
    assert "pinned authority" in resumed.stderr.lower()
    assert_complete_pair(Path(plan["calibration"]["left"]["path"]).parent, mixed)
    assert state_path.is_file()
    assert paths["journal"].is_file()
    assert {
        str(path.relative_to(paths["archive"])): path.read_bytes()
        for path in paths["archive"].rglob("*")
        if path.is_file()
    } == archive_before


@pytest.mark.parametrize("tamper", ["record_authority", "retired_active", "retired_state"])
def test_status_rejects_self_consistent_interrupted_archive_tamper_against_pinned_authority(tmp_path, tamper):
    plan, state_path, state, _, _ = prepare_interrupted_calibration_candidate(tmp_path)
    paths = interrupted_transaction_paths(plan, state_path, state["session_id"])
    assert run_interrupted_recovery(plan, tmp_path, state_path).returncode == 0
    archive = paths["archive"]
    record_path = archive / "archive-record.json"
    receipt_path = archive / "recovery-receipt.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if tamper == "record_authority":
        record["source_provenance"]["repo_head"] = "f" * 40
        receipt["source_provenance"] = record["source_provenance"]
    elif tamper == "retired_active":
        archived = Path(record["artifacts"]["left_calibration"]["archive_path"])
        retired = archive / "retired-active-calibration" / archived.name
        write_json(archived, make_calibration(777))
        shutil.copy2(archived, retired)
        update_archive_artifact_identity(record, "left_calibration", archived)
        record["artifacts"]["left_calibration"]["source_mtime_utc"] = powershell_utc_timestamp(retired)
    else:
        snapshot = Path(record["artifacts"]["state"]["archive_path"])
        retired = archive / "retired-state" / state_path.name
        archived_state = json.loads(snapshot.read_text(encoding="utf-8"))
        archived_state["self_consistent_tamper"] = True
        write_json(snapshot, archived_state)
        shutil.copy2(snapshot, retired)
        update_archive_artifact_identity(record, "state", snapshot)
        record["artifacts"]["state"]["source_mtime_utc"] = powershell_utc_timestamp(retired)
    write_json(record_path, record)
    receipt["archive_record_sha256"] = sha256_path(record_path)
    write_json(receipt_path, receipt)
    archive_before = {
        str(path.relative_to(archive)): path.read_bytes() for path in archive.rglob("*") if path.is_file()
    }

    status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)

    assert status.returncode == 0, status.stderr
    payload = json.loads(status.stdout)
    assert payload["classification"] == "INVALID_OR_UNCERTAIN_STATE"
    assert "pinned authority" in payload["report"].lower()
    assert {str(path.relative_to(archive)): path.read_bytes() for path in archive.rglob("*") if path.is_file()} == archive_before


def test_status_rejects_self_consistent_archived_immutable_authority_rewrite(tmp_path):
    plan, state_path, state, _, _ = prepare_interrupted_calibration_candidate(tmp_path)
    paths = interrupted_transaction_paths(plan, state_path, state["session_id"])
    assert run_interrupted_recovery(plan, tmp_path, state_path).returncode == 0
    archive = paths["archive"]
    record_path = archive / "archive-record.json"
    receipt_path = archive / "recovery-receipt.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    manifest_path = Path(record["artifacts"]["manifest"]["archive_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["self_consistent_immutable_rewrite"] = True
    write_json(manifest_path, manifest)
    update_archive_artifact_identity(record, "manifest", manifest_path)
    for side, seed in (("left", 801), ("right", 802)):
        name = f"original_{side}"
        original_path = Path(record["artifacts"][name]["archive_path"])
        write_json(original_path, make_calibration(seed))
        update_archive_artifact_identity(record, name, original_path)
    write_json(record_path, record)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["archive_record_sha256"] = sha256_path(record_path)
    write_json(receipt_path, receipt)
    archive_before = {
        str(path.relative_to(archive)): path.read_bytes() for path in archive.rglob("*") if path.is_file()
    }

    status = run_runner("-Stage", "Status", "-StatePath", str(state_path), plan=plan, tmp_path=tmp_path)

    assert status.returncode == 0, status.stderr
    payload = json.loads(status.stdout)
    assert payload["classification"] == "INVALID_OR_UNCERTAIN_STATE"
    assert "pinned immutable authority" in payload["report"].lower()
    assert {str(path.relative_to(archive)): path.read_bytes() for path in archive.rglob("*") if path.is_file()} == archive_before


def test_interrupted_recovery_refuses_unexpected_staging_directory_without_mutation(tmp_path):
    plan, state_path, state, mixed, _ = prepare_interrupted_calibration_candidate(tmp_path)
    paths = interrupted_transaction_paths(plan, state_path, state["session_id"])
    active_dir = Path(plan["calibration"]["left"]["path"]).parent
    plan["restart_failure_point"] = "after_first_archive_copy"
    assert run_interrupted_recovery(plan, tmp_path, state_path).returncode != 0
    (paths["archive_staging"] / "unexpected-empty-directory").mkdir()
    state_before = state_path.read_bytes()
    del plan["restart_failure_point"]

    resumed = run_interrupted_recovery(plan, tmp_path, state_path)

    assert resumed.returncode != 0
    assert "unexpected directory" in resumed.stderr.lower()
    assert state_path.read_bytes() == state_before
    assert_complete_pair(active_dir, mixed)
    assert paths["journal"].is_file()
    assert not paths["archive"].exists()


def test_restart_calibration_is_blocked_by_live_interrupted_recovery_journal(tmp_path):
    plan, state_path, state, mixed, _ = prepare_interrupted_calibration_candidate(tmp_path)
    paths = interrupted_transaction_paths(plan, state_path, state["session_id"])
    plan["restart_failure_point"] = "after_archive_namespace_publish"
    assert run_interrupted_recovery(plan, tmp_path, state_path).returncode != 0
    del plan["restart_failure_point"]

    restarted = run_restart(plan, tmp_path, state_path)

    assert restarted.returncode != 0
    assert "interrupted-calibration recovery transaction" in restarted.stderr
    assert_complete_pair(Path(plan["calibration"]["left"]["path"]).parent, mixed)
    assert paths["journal"].is_file()


def test_check_leader_buses_runner_requires_exact_guards_and_uses_reviewed_command(tmp_path):
    plan = base_plan(tmp_path)
    state_path = tmp_path / "logs" / "packet2n-r5-state.json"
    plan["stage_plan"]["CheckLeaderBuses"] = {"launched": True, "exit_code": 0}

    lowercase = run_runner(
        "-Stage", "CheckLeaderBuses", "-StatePath", str(state_path), "-Confirm", "check", plan=plan, tmp_path=tmp_path
    )
    assert lowercase.returncode != 0
    assert "requires -Confirm CHECK" in lowercase.stderr
    import_probe_module(plan, "lerobot")["path"] = str(tmp_path / "external" / "lerobot" / "__init__.py")
    refused = run_runner(
        "-Stage", "CheckLeaderBuses", "-StatePath", str(state_path), "-Confirm", "CHECK", plan=plan, tmp_path=tmp_path
    )
    assert refused.returncode != 0
    assert "import sources" in refused.stderr.lower()
    plan["import_source_probe"] = make_import_source_probe()

    checked = run_runner(
        "-Stage", "CheckLeaderBuses", "-StatePath", str(state_path), "-Confirm", "CHECK", plan=plan, tmp_path=tmp_path
    )

    assert checked.returncode == 0, checked.stderr
    assert checked.stdout.strip() == "LEADER_BUS_CHECK_STAGE=PASS"
