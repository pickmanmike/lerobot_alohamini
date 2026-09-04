#!/usr/bin/env python

# Copyright 2026 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
WINDOWS_HELPER = REPO_ROOT / "tools" / "run_am1.ps1"
HOST_HELPER = REPO_ROOT / "tools" / "run_am1_host.sh"
EXAMPLE_CONFIG = REPO_ROOT / "config" / "am1.local.example.json"
PYTHON = Path(sys.executable)
POWERSHELL = shutil.which("pwsh")
requires_powershell = pytest.mark.skipif(POWERSHELL is None, reason="PowerShell 7 is required")


def find_bash() -> str:
    if bash := shutil.which("bash"):
        return bash
    if git := shutil.which("git"):
        git_bash = Path(git).resolve().parents[1] / "bin" / "bash.exe"
        if git_bash.is_file():
            return str(git_bash)
    return "bash"


BASH = find_bash()


def ps_literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def run_powershell(body: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", body],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def command_payload(mode: str) -> dict:
    body = f"""
. {ps_literal(WINDOWS_HELPER)}
$config = Get-Content -LiteralPath {ps_literal(EXAMPLE_CONFIG)} -Raw | ConvertFrom-Json
$command = New-Am1WindowsCommand -Mode {mode} -Config $config `
    -RepositoryRoot {ps_literal(REPO_ROOT)} -LeftPort 'COM8' -RightPort 'COM7'
$command | ConvertTo-Json -Depth 6 -Compress
"""
    result = run_powershell(body)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.splitlines()[-1])


@requires_powershell
def test_windows_base_command_has_no_leader_or_lift_motion_inputs():
    payload = command_payload("Base")

    arguments = payload["arguments"]
    assert arguments == [
        str(REPO_ROOT / "examples" / "alohamini" / "teleoperate_bi.py"),
        "--base_only",
        "--no_leader",
        "--start_paused",
        "--no_cameras",
        "--no_rerun",
        "--robot.robot_model",
        "alohamini1",
        "--robot.remote_ip",
        "192.168.1.134",
        "--fps",
        "10",
        "--duration_s",
        "30",
    ]
    assert all("COM" not in argument for argument in arguments)
    assert all("lift" not in argument.lower() for argument in arguments)


@requires_powershell
def test_windows_arms_command_preserves_physically_validated_settings():
    payload = command_payload("Arms")

    arguments = payload["arguments"]
    assert "--base_only" not in arguments
    assert arguments[arguments.index("--teleop.left_port") + 1] == "COM8"
    assert arguments[arguments.index("--teleop.right_port") + 1] == "COM7"
    assert arguments[arguments.index("--startup_sync_duration_s") + 1] == "120"
    assert arguments[arguments.index("--max_start_mismatch") + 1] == "10"
    assert arguments[arguments.index("--fps") + 1] == "10"
    assert arguments[arguments.index("--duration_s") + 1] == "45"
    assert "--no_keyboard" in arguments
    assert "--no_cameras" in arguments
    assert "--profile_cadence" in arguments


@pytest.mark.parametrize(
    "mutation",
    [
        "$config.arm_settings.client_fps = 11",
        "$config.arm_settings.client_duration_s = 46",
        "$config.arm_settings.startup_sync_duration_s = 121",
        "$config.arm_settings.max_start_mismatch = 11",
        "$config.arm_settings.host_max_relative_target = 21",
        "$config.leader_calibration_sha256.left = 'BAD'",
        "$config.leader_calibration_sha256.right = 'BAD'",
    ],
)
@requires_powershell
def test_windows_arms_command_rejects_changes_to_the_validated_envelope(mutation):
    body = f"""
. {ps_literal(WINDOWS_HELPER)}
$config = Get-Content -LiteralPath {ps_literal(EXAMPLE_CONFIG)} -Raw | ConvertFrom-Json
{mutation}
try {{
    $null = New-Am1WindowsCommand -Mode Arms -Config $config `
        -RepositoryRoot {ps_literal(REPO_ROOT)} -LeftPort 'COM8' -RightPort 'COM7'
    [Console]::Error.WriteLine('UNSAFE_CONFIG_ACCEPTED')
    exit 9
}}
catch {{
    [Console]::Out.WriteLine($_.Exception.Message)
}}
"""

    result = run_powershell(body)

    assert result.returncode == 0, result.stderr
    assert "must retain the validated AM1" in result.stdout


@requires_powershell
def test_windows_logged_command_returns_and_records_the_real_child_exit(tmp_path):
    log_path = tmp_path / "child.log"
    body = f"""
. {ps_literal(WINDOWS_HELPER)}
$code = Invoke-Am1LoggedCommand -Executable {ps_literal(PYTHON)} `
    -Arguments @('-c', 'import sys; print("CHILD_OUTPUT"); raise SystemExit(23)') `
    -LogPath {ps_literal(log_path)}
[Console]::Out.WriteLine("RESULT=$code")
"""

    result = run_powershell(body)

    assert result.returncode == 0, result.stderr
    assert "RESULT=23" in result.stdout
    assert "CHILD_OUTPUT" in log_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("mode", "required", "forbidden"),
    [
        (
            "base",
            ("--no_follower", "--skip_lift_home", "--no_cameras", "--max_loop_freq_hz"),
            ("--max_relative_target", "/dev/am_arm_follower_right"),
        ),
        (
            "arms",
            ("--skip_lift_home", "--no_cameras", "--max_relative_target", "20"),
            ("--no_follower",),
        ),
    ],
)
def test_host_helper_prints_mode_specific_command_without_hardware(mode, required, forbidden):
    result = subprocess.run(
        [BASH, str(HOST_HELPER), "--mode", mode, "--print-command"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "//.venv/bin/python" not in result.stdout
    assert "/.venv/bin/python" in result.stdout
    assert f"/{REPO_ROOT.name}/.venv/bin/python" in result.stdout.replace("\\", "/")
    for value in required:
        assert value in result.stdout
    for value in forbidden:
        assert value not in result.stdout


def test_host_helper_help_lists_only_arms_and_base_modes():
    result = subprocess.run(
        [BASH, str(HOST_HELPER), "--help"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--mode arms|base" in result.stdout


def test_host_runtime_pipeline_keeps_tee_alive_during_interrupt():
    runtime_pipeline_lines = [
        line.strip()
        for line in HOST_HELPER.read_text(encoding="utf-8").splitlines()
        if '"${command[@]}" 2>&1 | tee' in line
    ]

    assert runtime_pipeline_lines == [
        'PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$repository_root/src" '
        '"${command[@]}" 2>&1 | tee -i -a "$log_path"'
    ]


def test_local_config_example_is_valid_and_real_config_is_ignored():
    config = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))

    assert config["leader_calibration_sha256"] == {
        "left": "34D06E15F6768A3290B85BBE3507D9B14A8CCED263A40C575E02010560E13FBE",
        "right": "C5F04F97B2B4B371EF4C4292616E7BBCAAE3987805930DE46CAEB3C614D2950C",
    }
    assert config["arm_settings"] == {
        "client_fps": 10,
        "client_duration_s": 45,
        "startup_sync_duration_s": 120,
        "max_start_mismatch": 10,
        "host_max_relative_target": 20,
    }
    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", "config/am1.local.json"],
        cwd=REPO_ROOT,
        timeout=10,
        check=False,
    )
    assert ignored.returncode == 0
    serialized = EXAMPLE_CONFIG.read_text(encoding="utf-8").lower()
    assert "private key" not in serialized
    assert "cloudflare" not in serialized
    assert "password" not in serialized
    assert "token" not in serialized
