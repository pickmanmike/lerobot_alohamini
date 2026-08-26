#!/usr/bin/env python

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "tools" / "calibrate_am1_leaders.ps1"
pytestmark = pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")


def ps_literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def run_harness(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    harness = tmp_path / "harness.ps1"
    harness.write_text(
        f". {ps_literal(SCRIPT_PATH)}\n{body}\n",
        encoding="utf-8",
    )
    return subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-File", str(harness)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def run_json_harness(tmp_path: Path, expression: str) -> subprocess.CompletedProcess[str]:
    return run_harness(
        tmp_path,
        f"$result = {expression}\n[Console]::Out.WriteLine(($result | ConvertTo-Json -Depth 100 -Compress))",
    )


def calibration_record(
    *, joint_id: int, seed: int, wrist_roll: bool = False
) -> dict[str, int]:
    return {
        "id": joint_id,
        "drive_mode": 0,
        "homing_offset": seed + joint_id,
        "range_min": 0 if wrist_roll else 100 + seed + joint_id,
        "range_max": 4095 if wrist_roll else 3000 + seed + joint_id,
    }


def make_calibration(seed: int) -> dict[str, dict[str, int]]:
    joints = (
        "shoulder_pan",
        "shoulder_lift",
        "elbow_flex",
        "wrist_flex",
        "wrist_roll",
        "gripper",
    )
    return {
        joint: calibration_record(joint_id=index, seed=seed, wrist_roll=joint == "wrist_roll")
        for index, joint in enumerate(joints, start=1)
    }


def write_calibration(path: Path, payload: object, *, indent: int | None = 2) -> None:
    path.write_text(json.dumps(payload, indent=indent), encoding="utf-8")


def write_valid_pair(directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True)
    left = directory / "so101_leader_bi_left.json"
    right = directory / "so101_leader_bi_right.json"
    write_calibration(left, make_calibration(0))
    write_calibration(right, make_calibration(10))
    return left, right


def make_fake_repository(tmp_path: Path) -> tuple[Path, Path]:
    repository = tmp_path / "repo"
    python = repository / ".venv" / "Scripts" / "python.exe"
    paths = (
        python,
        repository / "src" / "lerobot" / "__init__.py",
        repository / "examples" / "alohamini" / "calibrate_bi.py",
        repository / "examples" / "alohamini" / "leader_client_utils.py",
        repository / "src" / "lerobot" / "teleoperators" / "bi_so_leader" / "bi_so_leader.py",
        repository / "src" / "lerobot" / "teleoperators" / "so_leader" / "so_leader.py",
    )
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    return repository, python


def make_provenance_payload(repository: Path, python: Path, calibration_root: Path) -> dict[str, object]:
    return {
        "cwd": str(repository),
        "executable": str(python),
        "prefix": str(repository / ".venv"),
        "calibration_root": str(calibration_root),
        "modules": {
            "lerobot": str(repository / "src" / "lerobot" / "__init__.py"),
            "calibrate_bi": str(repository / "examples" / "alohamini" / "calibrate_bi.py"),
            "leader_client_utils": str(repository / "examples" / "alohamini" / "leader_client_utils.py"),
            "bi_so_leader": str(
                repository / "src" / "lerobot" / "teleoperators" / "bi_so_leader" / "bi_so_leader.py"
            ),
            "so_leader": str(
                repository / "src" / "lerobot" / "teleoperators" / "so_leader" / "so_leader.py"
            ),
        },
    }


def probe_blocks(
    payload: dict[str, object],
    *,
    branch: str = "fix/am1-elbow-commissioning",
    head: str = "a" * 40,
    porcelain: str = " M tracked-file.txt",
) -> str:
    encoded = json.dumps(payload, separators=(",", ":"))
    return f"""
foreach ($name in @('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONUSERBASE')) {{
    [System.Environment]::SetEnvironmentVariable($name, $null, 'Process')
}}
$pythonCalls = [System.Collections.Generic.List[object]]::new()
$gitCalls = [System.Collections.Generic.List[object]]::new()
$pythonProbe = {{
    param($Executable, [string[]]$Arguments, $WorkingDirectory)
    $pythonCalls.Add([pscustomobject]@{{ executable = $Executable; arguments = @($Arguments); working_directory = $WorkingDirectory }}) | Out-Null
    [pscustomobject]@{{ exit_code = 0; stdout = {ps_literal(encoded)}; stderr = '' }}
}}
$gitProbe = {{
    param($Executable, [string[]]$Arguments, $WorkingDirectory)
    $gitCalls.Add([pscustomobject]@{{ executable = $Executable; arguments = @($Arguments); working_directory = $WorkingDirectory }}) | Out-Null
    $joined = $Arguments -join ' '
    if ($joined -like '*branch --show-current*') {{ $stdout = {ps_literal(branch)} }}
    elseif ($joined -like '*rev-parse HEAD*') {{ $stdout = {ps_literal(head)} }}
    elseif ($joined -like '*status --porcelain=v1*') {{ $stdout = {ps_literal(porcelain)} }}
    else {{ throw "Unexpected git query: $joined" }}
    [pscustomobject]@{{ exit_code = 0; stdout = $stdout; stderr = '' }}
}}
"""


def filesystem_inventory(roots: tuple[Path, ...]) -> set[tuple[str, str, int, int]]:
    inventory: set[tuple[str, str, int, int]] = set()
    for root in roots:
        if not root.exists():
            inventory.add((str(root), "missing", 0, 0))
            continue
        for path in (root, *root.rglob("*")):
            stat = path.lstat()
            kind = "directory" if path.is_dir() else "file"
            inventory.add((str(path), kind, stat.st_size, stat.st_mtime_ns))
    return inventory


def test_wrapper_can_be_dot_sourced_without_dispatch(tmp_path: Path) -> None:
    result = run_harness(
        tmp_path,
        "[Console]::Out.WriteLine((Get-Am1CalibrationWrapperVersion | ConvertTo-Json -Compress))",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == "am1-simple-leader-calibration-v1"


def test_valid_pair_reports_complete_identity(tmp_path: Path) -> None:
    active = tmp_path / "active"
    left, right = write_valid_pair(active)

    result = run_json_harness(
        tmp_path,
        f"Get-Am1CalibrationPairStatus -DirectoryPath {ps_literal(active)} -LeaderIdValue 'so101_leader_bi'",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["classification"] == "VALID_COMPLETE_PAIR"
    assert payload["failure_reason"] is None
    for side, path in (("left", left), ("right", right)):
        facts = payload[side]
        assert Path(facts["path"]) == path.resolve()
        assert facts["exists"] is True
        assert facts["schema_valid"] is True
        assert facts["size"] == path.stat().st_size
        assert facts["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest().upper()
        assert facts["mtime_utc"].endswith("Z")


def test_missing_pair_side_reports_invalid_with_exact_path(tmp_path: Path) -> None:
    active = tmp_path / "active"
    active.mkdir()
    left = active / "so101_leader_bi_left.json"
    write_calibration(left, make_calibration(0))
    missing = active / "so101_leader_bi_right.json"

    result = run_json_harness(
        tmp_path,
        f"Get-Am1CalibrationPairStatus -DirectoryPath {ps_literal(active)} -LeaderIdValue 'so101_leader_bi'",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["classification"] == "INCOMPLETE_OR_INVALID_PAIR"
    assert str(missing.resolve()) in payload["failure_reason"]
    assert payload["right"]["exists"] is False


def make_invalid_calibration(case: str) -> object:
    payload = make_calibration(0)
    if case == "missing_joint":
        del payload["gripper"]
    elif case == "extra_joint":
        payload["unexpected"] = calibration_record(joint_id=7, seed=0)
    elif case == "missing_field":
        del payload["shoulder_pan"]["range_max"]
    elif case == "extra_field":
        payload["shoulder_pan"]["unexpected"] = 1
    elif case == "boolean_integer":
        payload["shoulder_pan"]["id"] = True
    elif case == "float_integer":
        payload["shoulder_pan"]["homing_offset"] = 1.5
    elif case == "nonzero_drive_mode":
        payload["shoulder_pan"]["drive_mode"] = 1
    elif case == "wrong_id":
        payload["shoulder_pan"]["id"] = 6
    elif case == "duplicate_id":
        payload["elbow_flex"]["id"] = 2
    elif case == "bad_range":
        payload["elbow_flex"]["range_min"] = payload["elbow_flex"]["range_max"]
    elif case == "wrong_wrist_roll_range":
        payload["wrist_roll"]["range_max"] = 4094
    else:
        raise AssertionError(f"unknown case: {case}")
    return payload


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("malformed_json", "JSON"),
        ("missing_joint", "joints"),
        ("extra_joint", "joints"),
        ("missing_field", "fields"),
        ("extra_field", "fields"),
        ("boolean_integer", "integer"),
        ("float_integer", "integer"),
        ("nonzero_drive_mode", "drive_mode"),
        ("wrong_id", "wrong or duplicate ID"),
        ("duplicate_id", "wrong or duplicate ID"),
        ("bad_range", "range"),
        ("wrong_wrist_roll_range", "wrist_roll"),
    ],
)
def test_invalid_schema_is_rejected(case: str, reason: str, tmp_path: Path) -> None:
    active = tmp_path / "active"
    left, _ = write_valid_pair(active)
    if case == "malformed_json":
        left.write_text("{", encoding="utf-8")
    else:
        write_calibration(left, make_invalid_calibration(case))

    result = run_json_harness(
        tmp_path,
        f"Get-Am1CalibrationPairStatus -DirectoryPath {ps_literal(active)} -LeaderIdValue 'so101_leader_bi'",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["classification"] == "INCOMPLETE_OR_INVALID_PAIR"
    assert reason.lower() in payload["failure_reason"].lower()


def test_pair_rejects_semantically_identical_left_and_right_payloads(tmp_path: Path) -> None:
    active = tmp_path / "active"
    active.mkdir()
    payload = make_calibration(0)
    write_calibration(active / "so101_leader_bi_left.json", payload, indent=2)
    write_calibration(active / "so101_leader_bi_right.json", payload, indent=None)

    result = run_json_harness(
        tmp_path,
        f"Get-Am1CalibrationPairStatus -DirectoryPath {ps_literal(active)} -LeaderIdValue 'so101_leader_bi'",
    )

    assert result.returncode == 0, result.stderr
    facts = json.loads(result.stdout)
    assert facts["classification"] == "INCOMPLETE_OR_INVALID_PAIR"
    assert "distinct" in facts["failure_reason"].lower()


@pytest.mark.parametrize(
    ("left", "right", "leader_id", "profile", "confirmation", "require_confirmation", "reason"),
    [
        ("COM9", "COM7", "so101_leader_bi", "so-arm-5dof", "CALIBRATE", False, "left port"),
        ("COM8", "COM6", "so101_leader_bi", "so-arm-5dof", "CALIBRATE", False, "right port"),
        ("COM8", "COM7", "wrong", "so-arm-5dof", "CALIBRATE", False, "leader ID"),
        ("COM8", "COM7", "so101_leader_bi", "am-leader-6dof", "CALIBRATE", False, "arm profile"),
        ("COM8", "COM7", "so101_leader_bi", "so-arm-5dof", "calibrate", True, "confirmation"),
    ],
)
def test_wrong_identity_or_confirmation_refuses_before_native_invoker(
    left: str,
    right: str,
    leader_id: str,
    profile: str,
    confirmation: str,
    require_confirmation: bool,
    reason: str,
    tmp_path: Path,
) -> None:
    require_switch = "-RequireCalibrationConfirmation" if require_confirmation else ""
    body = f"""
$called = $false
$reason = $null
$nativeInvoker = {{ $script:called = $true }}
try {{
    Assert-Am1FixedIdentity -LeftPortValue {ps_literal(left)} -RightPortValue {ps_literal(right)} `
        -LeaderIdValue {ps_literal(leader_id)} -ArmProfileValue {ps_literal(profile)} `
        -Confirmation {ps_literal(confirmation)} {require_switch}
    & $nativeInvoker
}}
catch {{ $reason = $_.Exception.Message }}
[Console]::Out.WriteLine(([ordered]@{{ called = $called; reason = $reason }} | ConvertTo-Json -Compress))
"""
    result = run_harness(tmp_path, body)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["called"] is False
    assert reason.lower() in payload["reason"].lower()


def test_snapshot_rejects_nested_directory(tmp_path: Path) -> None:
    active = tmp_path / "active"
    write_valid_pair(active)
    (active / "nested").mkdir()

    result = run_json_harness(
        tmp_path,
        f"Get-Am1RegularFileSnapshot -DirectoryPath {ps_literal(active)}",
    )

    assert result.returncode != 0
    assert "forbidden directory" in result.stderr.lower()


def test_snapshot_rejects_reparse_entry(tmp_path: Path) -> None:
    active = tmp_path / "active"
    target = tmp_path / "target"
    write_valid_pair(active)
    target.mkdir()
    body = f"""
New-Item -ItemType Junction -Path {ps_literal(active / 'link')} -Target {ps_literal(target)} | Out-Null
$result = Get-Am1RegularFileSnapshot -DirectoryPath {ps_literal(active)}
[Console]::Out.WriteLine(($result | ConvertTo-Json -Depth 100 -Compress))
"""
    result = run_harness(tmp_path, body)

    assert result.returncode != 0
    assert "reparse point" in result.stderr.lower()


def test_native_command_is_exact_and_uses_explicit_staging_leaf(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    python = repository / ".venv" / "Scripts" / "python.exe"
    staging = tmp_path / "run" / "staged-calibration" / "teleoperators" / "so_leader"

    result = run_json_harness(
        tmp_path,
        "New-Am1NativeCalibrationCommand "
        f"-RepositoryRoot {ps_literal(repository)} -PythonPath {ps_literal(python)} "
        f"-StagingLeaf {ps_literal(staging)}",
    )

    assert result.returncode == 0, result.stderr
    command = json.loads(result.stdout)
    assert command == {
        "executable": str(python),
        "arguments": [
            str(repository / "examples" / "alohamini" / "calibrate_bi.py"),
            "--teleop.left_port",
            "COM8",
            "--teleop.right_port",
            "COM7",
            "--teleop.id",
            "so101_leader_bi",
            "--teleop.arm_profile",
            "so-arm-5dof",
            "--teleop.calibration_dir",
            str(staging),
            "--force_fresh_calibration",
        ],
        "working_directory": str(repository),
    }


def test_provenance_reports_dirty_tree_and_uses_safe_probe_arguments(tmp_path: Path) -> None:
    repository, python = make_fake_repository(tmp_path)
    calibration_root = tmp_path / "calibration"
    payload = make_provenance_payload(repository, python, calibration_root)
    body = probe_blocks(payload) + f"""
$provenance = Get-Am1RepositoryProvenance -RepositoryRoot {ps_literal(repository)} `
    -PythonPath {ps_literal(python)} -PythonProbeInvoker $pythonProbe -GitInvoker $gitProbe
[Console]::Out.WriteLine(([ordered]@{{ provenance = $provenance; python_calls = @($pythonCalls); git_calls = @($gitCalls) }} | ConvertTo-Json -Depth 100 -Compress))
"""

    result = run_harness(tmp_path, body)

    assert result.returncode == 0, result.stderr
    facts = json.loads(result.stdout)
    assert facts["provenance"]["branch"] == "fix/am1-elbow-commissioning"
    assert facts["provenance"]["head"] == "a" * 40
    assert facts["provenance"]["porcelain"] == " M tracked-file.txt"
    assert facts["provenance"]["calibration_root"] == str(calibration_root)
    assert len(facts["python_calls"]) == 1
    assert facts["python_calls"][0]["arguments"][0] == "-B"
    assert facts["python_calls"][0]["working_directory"] == str(repository)
    status_calls = [
        call
        for call in facts["git_calls"]
        if "status" in call["arguments"]
    ]
    assert len(status_calls) == 1
    assert status_calls[0]["arguments"][0] == "--no-optional-locks"


@pytest.mark.parametrize(
    ("mismatch", "reason", "probe_called"),
    [
        ("python_path", "repository python", False),
        ("executable", "sys.executable", True),
        ("prefix", "sys.prefix", True),
        ("cwd", "working directory", True),
        ("module", "module path", True),
    ],
)
def test_provenance_refuses_wrong_executable_prefix_cwd_or_module(
    mismatch: str,
    reason: str,
    probe_called: bool,
    tmp_path: Path,
) -> None:
    repository, python = make_fake_repository(tmp_path)
    calibration_root = tmp_path / "calibration"
    payload = make_provenance_payload(repository, python, calibration_root)
    supplied_python = python
    if mismatch == "python_path":
        supplied_python = repository / "other-python.exe"
        supplied_python.touch()
    elif mismatch == "executable":
        payload["executable"] = str(repository / "outside-python.exe")
    elif mismatch == "prefix":
        payload["prefix"] = str(repository / "outside-venv")
    elif mismatch == "cwd":
        payload["cwd"] = str(repository.parent)
    elif mismatch == "module":
        modules = payload["modules"]
        assert isinstance(modules, dict)
        modules["so_leader"] = str(repository.parent / "external" / "so_leader.py")
    body = probe_blocks(payload, porcelain="") + f"""
$reason = $null
try {{
    Get-Am1RepositoryProvenance -RepositoryRoot {ps_literal(repository)} -PythonPath {ps_literal(supplied_python)} `
        -PythonProbeInvoker $pythonProbe -GitInvoker $gitProbe | Out-Null
}}
catch {{ $reason = $_.Exception.Message }}
[Console]::Out.WriteLine(([ordered]@{{ reason = $reason; python_call_count = $pythonCalls.Count }} | ConvertTo-Json -Compress))
"""

    result = run_harness(tmp_path, body)

    assert result.returncode == 0, result.stderr
    refusal = json.loads(result.stdout)
    assert reason.lower() in refusal["reason"].lower()
    assert (refusal["python_call_count"] > 0) is probe_called


@pytest.mark.parametrize("variable", ["PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE"])
def test_provenance_rejects_import_environment_before_probe(variable: str, tmp_path: Path) -> None:
    repository, python = make_fake_repository(tmp_path)
    payload = make_provenance_payload(repository, python, tmp_path / "calibration")
    body = probe_blocks(payload, porcelain="") + f"""
[System.Environment]::SetEnvironmentVariable({ps_literal(variable)}, 'unsafe-value', 'Process')
$reason = $null
try {{
    Get-Am1RepositoryProvenance -RepositoryRoot {ps_literal(repository)} -PythonPath {ps_literal(python)} `
        -PythonProbeInvoker $pythonProbe -GitInvoker $gitProbe | Out-Null
}}
catch {{ $reason = $_.Exception.Message }}
[Console]::Out.WriteLine(([ordered]@{{ reason = $reason; python_call_count = $pythonCalls.Count }} | ConvertTo-Json -Compress))
"""

    result = run_harness(tmp_path, body)

    assert result.returncode == 0, result.stderr
    refusal = json.loads(result.stdout)
    assert variable.lower() in refusal["reason"].lower()
    assert refusal["python_call_count"] == 0


def test_status_is_read_only_and_reports_both_active_identities(tmp_path: Path) -> None:
    repository, python = make_fake_repository(tmp_path)
    calibration_root = tmp_path / "calibration"
    active = calibration_root / "teleoperators" / "so_leader"
    left, right = write_valid_pair(active)
    payload = make_provenance_payload(repository, python, calibration_root)
    before = filesystem_inventory((tmp_path,))
    body = probe_blocks(payload, porcelain="?? local-note.txt") + f"""
[System.Environment]::SetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', $null, 'Process')
$nativeCalled = $false
function Invoke-Am1NativeCalibration {{ $script:nativeCalled = $true; throw 'forbidden native invocation' }}
$statusFacts = Get-Am1LeaderCalibrationStatus -RepositoryRoot {ps_literal(repository)} `
    -PythonPath {ps_literal(python)} -LeftPortValue 'COM8' -RightPortValue 'COM7' `
    -LeaderIdValue 'so101_leader_bi' -ArmProfileValue 'so-arm-5dof' `
    -PythonProbeInvoker $pythonProbe -GitInvoker $gitProbe
[Console]::Out.WriteLine(([ordered]@{{ status = $statusFacts; native_called = $nativeCalled; python_calls = @($pythonCalls) }} | ConvertTo-Json -Depth 100 -Compress))
"""

    result = run_harness(tmp_path, body)

    assert result.returncode == 0, result.stderr
    facts = json.loads(result.stdout)
    assert facts["native_called"] is False
    assert facts["python_calls"][0]["arguments"][0] == "-B"
    assert facts["status"]["pair"]["classification"] == "VALID_COMPLETE_PAIR"
    assert Path(facts["status"]["pair"]["left"]["path"]) == left.resolve()
    assert Path(facts["status"]["pair"]["right"]["path"]) == right.resolve()
    assert facts["status"]["provenance"]["porcelain"] == "?? local-note.txt"
    assert not any(
        path.name.startswith(("am1-leader-calibration-runs", ".am1-candidate-", ".am1-withdrawn-"))
        for path in tmp_path.rglob("*")
    )
    after = filesystem_inventory((tmp_path,))
    allowed = {str(tmp_path), str(tmp_path / "harness.ps1")}
    assert {item for item in after - before if item[0] not in allowed} == set()


def test_status_wrong_identity_refuses_before_python_probe(tmp_path: Path) -> None:
    repository, python = make_fake_repository(tmp_path)
    payload = make_provenance_payload(repository, python, tmp_path / "calibration")
    body = probe_blocks(payload, porcelain="") + f"""
$reason = $null
try {{
    Get-Am1LeaderCalibrationStatus -RepositoryRoot {ps_literal(repository)} `
        -PythonPath {ps_literal(python)} -LeftPortValue 'COM9' -RightPortValue 'COM7' `
        -LeaderIdValue 'so101_leader_bi' -ArmProfileValue 'so-arm-5dof' `
        -PythonProbeInvoker $pythonProbe -GitInvoker $gitProbe | Out-Null
}}
catch {{ $reason = $_.Exception.Message }}
[Console]::Out.WriteLine(([ordered]@{{ reason = $reason; python_call_count = $pythonCalls.Count }} | ConvertTo-Json -Compress))
"""

    result = run_harness(tmp_path, body)

    assert result.returncode == 0, result.stderr
    refusal = json.loads(result.stdout)
    assert "left port" in refusal["reason"].lower()
    assert refusal["python_call_count"] == 0
