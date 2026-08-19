#!/usr/bin/env python

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "tools" / "fetch_am1_pi_log.ps1"
pytestmark = pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")


def run_helper(*args: str) -> subprocess.CompletedProcess[str]:
    assert SCRIPT_PATH.exists(), "the reusable AM1 Pi-log fetch helper is missing"
    return subprocess.run(
        [
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(SCRIPT_PATH),
            *args,
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )


def test_fetch_helper_dry_run_selects_newest_and_prints_exact_paths_without_writing(tmp_path):
    local_directory = tmp_path / "logs"

    result = run_helper(
        "-DryRun",
        "-DryRunRemoteListing",
        "100 /home/pickmanmike/am1-older.log\n200 /home/pickmanmike/am1-newest.log",
        "-LocalDirectory",
        str(local_directory),
    )

    assert result.returncode == 0, result.stderr
    assert "Remote path: /home/pickmanmike/am1-newest.log" in result.stdout
    assert f"Local path: {local_directory / 'am1-newest.log'}" in result.stdout
    assert "DRY RUN: no SSH, SCP, or local write was performed." in result.stdout
    assert not local_directory.exists()


def test_fetch_helper_dry_run_honors_explicit_remote_path(tmp_path):
    local_directory = tmp_path / "logs"

    result = run_helper(
        "-DryRun",
        "-RemotePath",
        "/home/pickmanmike/am1-explicit.log",
        "-LocalDirectory",
        str(local_directory),
    )

    assert result.returncode == 0, result.stderr
    assert "Remote path: /home/pickmanmike/am1-explicit.log" in result.stdout
    assert f"Local path: {local_directory / 'am1-explicit.log'}" in result.stdout


def test_fetch_helper_rejects_missing_dry_run_candidate_without_network(tmp_path):
    result = run_helper(
        "-DryRun",
        "-LocalDirectory",
        str(tmp_path / "logs"),
    )

    assert result.returncode != 0
    assert "No /home/pickmanmike/am1-*.log candidate was supplied or found." in result.stderr


def test_fetch_helper_rejects_unsafe_host_and_remote_path_before_network(tmp_path):
    unsafe_host = run_helper(
        "-DryRun",
        "-RemoteHost",
        "-oProxyCommand=bad",
        "-RemotePath",
        "/home/pickmanmike/am1-valid.log",
        "-LocalDirectory",
        str(tmp_path / "logs"),
    )
    unsafe_path = run_helper(
        "-DryRun",
        "-RemotePath",
        "/tmp/not-an-am1-log",
        "-LocalDirectory",
        str(tmp_path / "logs"),
    )

    assert unsafe_host.returncode != 0
    assert "RemoteHost must be a plain user@host value." in unsafe_host.stderr
    assert unsafe_path.returncode != 0
    assert "RemotePath must name /home/pickmanmike/am1-*.log." in unsafe_path.stderr


def test_fetch_helper_rejects_timestamp_placeholder_before_network(tmp_path):
    result = run_helper(
        "-DryRun",
        "-RemotePath",
        "/home/pickmanmike/am1-left-elbow-diagnostic-host-YYYY-MM-DD-HHMMSS.log",
        "-LocalDirectory",
        str(tmp_path / "logs"),
    )

    assert result.returncode != 0
    normalized_error = " ".join(result.stderr.split())
    assert "RemotePath contains a timestamp placeholder" in normalized_error
    assert "exact Pi HOST_LOG path" in normalized_error
    assert "omit -RemotePath" in normalized_error
    assert "newest AM1 log" in normalized_error
