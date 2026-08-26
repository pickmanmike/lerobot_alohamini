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
DOCUMENTATION_PATH = REPO_ROOT / "docs" / "alohamini" / "alohamini.md"


@pytest.fixture(autouse=True)
def require_powershell_for_harness_tests(request: pytest.FixtureRequest) -> None:
    if request.node.name.startswith("test_documentation_"):
        return
    if shutil.which("pwsh") is None:
        pytest.skip("PowerShell 7 is unavailable")


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


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def prepare_attempt_fixture(tmp_path: Path) -> dict[str, Path | dict[str, object]]:
    repository, python = make_fake_repository(tmp_path)
    calibration_root = tmp_path / "calibration"
    active = calibration_root / "teleoperators" / "so_leader"
    write_valid_pair(active)
    (active / "unrelated-am2.json").write_bytes(b"preserve-am2")
    return {
        "repository": repository,
        "python": python,
        "calibration_root": calibration_root,
        "active": active,
        "payload": make_provenance_payload(repository, python, calibration_root),
    }


def run_attempt_harness(
    tmp_path: Path,
    fixture: dict[str, Path | dict[str, object]],
    *,
    native_body: str,
    copy_body: str = "[System.IO.File]::Copy($Source, $Destination, $false)",
    start_body: str = (
        "$events.Add('transcript:start') | Out-Null; "
        "[System.IO.File]::WriteAllText($Path, 'transcript-started')"
    ),
    stop_body: str = "$events.Add('transcript:stop') | Out-Null",
    include_promotion_seams: bool = False,
    move_body: str = "[System.IO.Directory]::Move($Source, $Destination)",
    remove_body: str = "[System.IO.Directory]::Delete($Path, $true)",
    before_promotion_body: str = "",
    after_second_move_body: str = "",
) -> tuple[subprocess.CompletedProcess[str], dict[str, object] | None]:
    repository = fixture["repository"]
    python = fixture["python"]
    payload = fixture["payload"]
    assert isinstance(repository, Path)
    assert isinstance(python, Path)
    assert isinstance(payload, dict)
    promotion_definitions = f"""
$moveCalls = [System.Collections.Generic.List[object]]::new()
$removeCalls = [System.Collections.Generic.List[string]]::new()
$moveDirectory = {{
    param($Source, $Destination, $Operation)
    $moveCalls.Add([pscustomobject]@{{ source = $Source; destination = $Destination; operation = $Operation }}) | Out-Null
    {move_body}
}}
$removeDirectory = {{
    param($Path)
    $removeCalls.Add($Path) | Out-Null
    {remove_body}
}}
$beforePromotion = {{ param($Context) {before_promotion_body} }}
$afterSecondMove = {{ param($Context) {after_second_move_body} }}
"""
    promotion_arguments = ""
    if include_promotion_seams:
        promotion_arguments = """ `
    -MoveDirectoryInvoker $moveDirectory -RemoveDirectoryInvoker $removeDirectory `
    -BeforePromotionHook $beforePromotion -AfterSecondMoveHook $afterSecondMove"""
    body = probe_blocks(payload, porcelain="") + f"""
$events = [System.Collections.Generic.List[string]]::new()
$nativeCalls = 0
$copy = {{ param($Source, $Destination) {copy_body} }}
$startTranscript = {{ param($Path) {start_body} }}
$stopTranscript = {{ {stop_body} }}
$native = {{
    param($Command, $StagingLeaf, [ref]$Launched, [ref]$ExitCode)
    $script:nativeCalls += 1
    {native_body}
}}
{promotion_definitions}
$outcome = Invoke-Am1CalibrationAttempt -RepositoryRoot {ps_literal(repository)} `
    -PythonPath {ps_literal(python)} -LeftPortValue 'COM8' -RightPortValue 'COM7' `
    -LeaderIdValue 'so101_leader_bi' -ArmProfileValue 'so-arm-5dof' `
    -Confirmation 'CALIBRATE' -RunId 'test-run' `
    -PythonProbeInvoker $pythonProbe -GitInvoker $gitProbe -CopyFileInvoker $copy `
    -StartTranscriptInvoker $startTranscript -NativeCommandInvoker $native `
    -StopTranscriptInvoker $stopTranscript{promotion_arguments}
Write-Am1CalibrationOutcome -Outcome $outcome
$report = [ordered]@{{
    outcome = $outcome
    native_calls = $nativeCalls
    events = @($events)
    move_calls = @($moveCalls)
    remove_calls = @($removeCalls)
}}
[Console]::Out.WriteLine('OUTCOME_JSON=' + ($report | ConvertTo-Json -Depth 100 -Compress))
"""
    result = run_harness(tmp_path, body)
    report = None
    for line in result.stdout.splitlines():
        if line.startswith("OUTCOME_JSON="):
            report = json.loads(line.removeprefix("OUTCOME_JSON="))
    return result, report


def native_write_body(*, left: object | None, right: object | None, exit_code: int = 0) -> str:
    lines: list[str] = []
    if left is not None:
        left_text = left if isinstance(left, str) else json.dumps(left, separators=(",", ":"))
        lines.append(
            "[System.IO.File]::WriteAllText((Join-Path $StagingLeaf 'so101_leader_bi_left.json'), "
            f"{ps_literal(left_text)})"
        )
    if right is not None:
        right_text = right if isinstance(right, str) else json.dumps(right, separators=(",", ":"))
        lines.append(
            "[System.IO.File]::WriteAllText((Join-Path $StagingLeaf 'so101_leader_bi_right.json'), "
            f"{ps_literal(right_text)})"
        )
    lines.extend(("$Launched.Value = $true", f"$ExitCode.Value = {exit_code}"))
    return "\n".join(lines)


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


@pytest.mark.parametrize(
    ("copy_body", "reason"),
    [
        ("throw 'simulated backup copy failure'", "simulated backup copy failure"),
        (
            "[System.IO.File]::Copy($Source, $Destination, $false); "
            "if ($Source -like '*_right.json') { [System.IO.File]::AppendAllText($Destination, 'corrupt') }",
            "backup hash",
        ),
    ],
)
def test_backup_failure_calls_no_native_and_preserves_active_tree(
    copy_body: str,
    reason: str,
    tmp_path: Path,
) -> None:
    fixture = prepare_attempt_fixture(tmp_path)
    active = fixture["active"]
    assert isinstance(active, Path)
    before = tree_bytes(active)

    result, report = run_attempt_harness(
        tmp_path,
        fixture,
        native_body="throw 'native must not be called'",
        copy_body=copy_body,
    )

    assert result.returncode == 0, result.stderr
    assert report is not None
    outcome = report["outcome"]
    assert outcome["success"] is False
    assert reason in outcome["primary_reason"].lower()
    assert report["native_calls"] == 0
    assert tree_bytes(active) == before
    assert result.stdout.count("CALIBRATION_FAILURE_REASON=") == 1
    assert "CALIBRATION_RESULT=FAIL" in result.stdout
    assert "CALIBRATION_RESULT=PASS" not in result.stdout


def test_native_launch_failure_stops_transcript_and_preserves_active_tree(tmp_path: Path) -> None:
    fixture = prepare_attempt_fixture(tmp_path)
    active = fixture["active"]
    assert isinstance(active, Path)
    before = tree_bytes(active)

    result, report = run_attempt_harness(
        tmp_path,
        fixture,
        native_body="throw 'simulated native launch failure'",
    )

    assert result.returncode == 0, result.stderr
    assert report is not None
    outcome = report["outcome"]
    assert outcome["success"] is False
    assert outcome["launched"] is False
    assert "simulated native launch failure" in outcome["primary_reason"]
    assert report["events"] == ["transcript:start", "transcript:stop"]
    assert tree_bytes(active) == before
    assert "CALIBRATION_RESULT=PASS" not in result.stdout


def test_launched_false_result_cannot_emit_pass(tmp_path: Path) -> None:
    fixture = prepare_attempt_fixture(tmp_path)

    result, report = run_attempt_harness(
        tmp_path,
        fixture,
        native_body="$Launched.Value = $false; $ExitCode.Value = 0",
    )

    assert result.returncode == 0, result.stderr
    assert report is not None
    assert report["outcome"]["success"] is False
    assert report["outcome"]["launched"] is False
    assert "did not launch" in report["outcome"]["primary_reason"].lower()
    assert "CALIBRATION_RESULT=FAIL" in result.stdout
    assert "CALIBRATION_RESULT=PASS" not in result.stdout


def test_native_nonzero_preserves_partial_staging_transcript_backup_and_active(tmp_path: Path) -> None:
    fixture = prepare_attempt_fixture(tmp_path)
    active = fixture["active"]
    assert isinstance(active, Path)
    before = tree_bytes(active)
    native_body = native_write_body(left=make_calibration(20), right=None, exit_code=42)

    result, report = run_attempt_harness(tmp_path, fixture, native_body=native_body)

    assert result.returncode == 0, result.stderr
    assert report is not None
    outcome = report["outcome"]
    assert outcome["success"] is False
    assert outcome["launched"] is True
    assert outcome["exit_code"] == 42
    assert "exit code 42" in outcome["primary_reason"].lower()
    assert Path(outcome["transcript_path"]).is_file()
    assert Path(outcome["backup_directory"]).is_dir()
    staging = Path(outcome["staging_leaf"])
    assert (staging / "so101_leader_bi_left.json").is_file()
    assert not (staging / "so101_leader_bi_right.json").exists()
    assert tree_bytes(active) == before
    assert "CALIBRATION_RESULT=PASS" not in result.stdout


def test_native_nonzero_remains_primary_when_transcript_stop_also_fails(tmp_path: Path) -> None:
    fixture = prepare_attempt_fixture(tmp_path)
    native_body = native_write_body(left=make_calibration(20), right=None, exit_code=42)

    result, report = run_attempt_harness(
        tmp_path,
        fixture,
        native_body=native_body,
        stop_body=(
            "$events.Add('transcript:stop') | Out-Null; "
            "throw 'simulated transcript stop failure'"
        ),
    )

    assert result.returncode == 0, result.stderr
    assert report is not None
    outcome = report["outcome"]
    assert outcome["success"] is False
    assert outcome["primary_reason"] == "Native calibration exited with exit code 42"
    assert outcome["secondary_failures"] == [
        "Transcript stop failed: simulated transcript stop failure"
    ]
    assert "CALIBRATION_RESULT=PASS" not in result.stdout


def test_native_interrupt_stops_transcript_and_never_promotes(tmp_path: Path) -> None:
    fixture = prepare_attempt_fixture(tmp_path)
    active = fixture["active"]
    assert isinstance(active, Path)
    before = tree_bytes(active)
    native_body = """
$events.Add('native:interrupt') | Out-Null
$Launched.Value = $true
throw [System.OperationCanceledException]::new('simulated operator interrupt')
"""

    result, report = run_attempt_harness(tmp_path, fixture, native_body=native_body)

    assert result.returncode == 0, result.stderr
    assert report is not None
    outcome = report["outcome"]
    assert outcome["success"] is False
    assert outcome["interrupted"] is True
    assert outcome["exit_code"] == 130
    assert "simulated operator interrupt" in outcome["primary_reason"]
    assert report["events"] == ["transcript:start", "native:interrupt", "transcript:stop"]
    assert Path(outcome["transcript_path"]).is_file()
    assert tree_bytes(active) == before
    assert not list(active.parent.glob(".am1-candidate-*"))
    assert not list(active.parent.glob(".am1-withdrawn-*"))
    assert "CALIBRATION_RESULT=PASS" not in result.stdout


@pytest.mark.parametrize(
    ("native_body", "reason"),
    [
        (native_write_body(left=make_calibration(20), right=None), "missing"),
        (native_write_body(left="{", right=make_calibration(30)), "malformed"),
    ],
)
def test_staged_missing_or_malformed_side_refuses_without_promotion(
    native_body: str,
    reason: str,
    tmp_path: Path,
) -> None:
    fixture = prepare_attempt_fixture(tmp_path)
    active = fixture["active"]
    assert isinstance(active, Path)
    before = tree_bytes(active)

    result, report = run_attempt_harness(tmp_path, fixture, native_body=native_body)

    assert result.returncode == 0, result.stderr
    assert report is not None
    outcome = report["outcome"]
    assert outcome["success"] is False
    assert reason in outcome["primary_reason"].lower()
    assert Path(outcome["staging_leaf"]).is_dir()
    assert tree_bytes(active) == before
    assert not list(active.parent.glob(".am1-candidate-*"))
    assert not list(active.parent.glob(".am1-withdrawn-*"))
    assert result.stdout.count("CALIBRATION_FAILURE_REASON=") == 1
    assert "CALIBRATION_RESULT=PASS" not in result.stdout


def test_candidate_and_withdrawal_must_be_direct_nonexistent_siblings(tmp_path: Path) -> None:
    active_parent = tmp_path / "teleoperators"
    active = active_parent / "so_leader"
    write_valid_pair(active)
    candidate = tmp_path / "outside" / ".am1-candidate-test-run"
    withdrawal = active_parent / ".am1-withdrawn-test-run"

    result = run_json_harness(
        tmp_path,
        "Assert-Am1DirectSiblingPaths "
        f"-ActiveDirectory {ps_literal(active)} -CandidatePath {ps_literal(candidate)} "
        f"-WithdrawalPath {ps_literal(withdrawal)} -RunId 'test-run'; "
        "[ordered]@{ accepted = $true }",
    )

    assert result.returncode != 0
    assert "direct sibling" in result.stderr.lower()
    assert active.is_dir()
    assert not candidate.exists()
    assert not withdrawal.exists()


def test_successful_candidate_promotion_preserves_unrelated_files_and_reports_evidence(tmp_path: Path) -> None:
    fixture = prepare_attempt_fixture(tmp_path)
    active = fixture["active"]
    assert isinstance(active, Path)
    original = tree_bytes(active)
    staged_left = make_calibration(40)
    staged_right = make_calibration(50)
    native_body = native_write_body(left=staged_left, right=staged_right)

    result, report = run_attempt_harness(
        tmp_path,
        fixture,
        native_body=native_body,
        include_promotion_seams=True,
    )

    assert result.returncode == 0, result.stderr
    assert report is not None
    outcome = report["outcome"]
    assert outcome["success"] is True, json.dumps(outcome, indent=2)
    assert outcome["primary_reason"] is None
    staging = Path(outcome["staging_leaf"])
    active_left = active / "so101_leader_bi_left.json"
    active_right = active / "so101_leader_bi_right.json"
    staged_left_path = staging / active_left.name
    staged_right_path = staging / active_right.name
    assert hashlib.sha256(active_left.read_bytes()).digest() == hashlib.sha256(staged_left_path.read_bytes()).digest()
    assert hashlib.sha256(active_right.read_bytes()).digest() == hashlib.sha256(staged_right_path.read_bytes()).digest()
    assert (active / "unrelated-am2.json").read_bytes() == original["unrelated-am2.json"]
    backup = Path(outcome["backup_directory"])
    assert (backup / active_left.name).read_bytes() == original[active_left.name]
    assert (backup / active_right.name).read_bytes() == original[active_right.name]
    assert not Path(outcome["candidate_path"]).exists()
    assert not Path(outcome["withdrawal_path"]).exists()
    assert [call["operation"] for call in report["move_calls"]] == ["withdraw-active", "promote-candidate"]
    assert report["remove_calls"] == [outcome["withdrawal_path"]]
    assert result.stdout.count("CALIBRATION_RESULT=PASS") == 1
    assert f"ACTIVE_LEFT_PATH={active_left}" in result.stdout
    assert f"ACTIVE_RIGHT_PATH={active_right}" in result.stdout
    assert "ACTIVE_LEFT_SHA256=" in result.stdout
    assert "ACTIVE_RIGHT_SHA256=" in result.stdout
    assert "PAIR_BACKUP=" in result.stdout
    assert "STAGED_EVIDENCE=" in result.stdout
    assert "NEXT_COMMAND=" in result.stdout
    assert "CALIBRATION_RESULT=FAIL" not in result.stdout


def test_fail_closed_recovery_command_quotes_apostrophe_in_withdrawal_path(tmp_path: Path) -> None:
    apostrophe_root = tmp_path / "operator's-calibration-root"
    fixture = prepare_attempt_fixture(apostrophe_root)
    native_body = native_write_body(left=make_calibration(40), right=make_calibration(50))

    result, report = run_attempt_harness(
        apostrophe_root,
        fixture,
        native_body=native_body,
        include_promotion_seams=True,
    )

    assert result.returncode == 0, result.stderr
    assert report is not None
    withdrawal_path = report["outcome"]["withdrawal_path"]
    assert isinstance(withdrawal_path, str)
    quoted_withdrawal = withdrawal_path.replace("'", "''")
    expected = (
        "FAIL_CLOSED_RECOVERY=Rename-Item "
        f"-LiteralPath '{quoted_withdrawal}' -NewName 'so_leader'"
    )
    assert expected in result.stdout


def test_concurrent_active_change_refuses_before_first_rename(tmp_path: Path) -> None:
    fixture = prepare_attempt_fixture(tmp_path)
    active = fixture["active"]
    assert isinstance(active, Path)
    native_body = native_write_body(left=make_calibration(40), right=make_calibration(50))
    external_bytes = b"external-concurrent-change"
    before_hook = (
        "[System.IO.File]::WriteAllBytes((Join-Path $Context.active 'unrelated-am2.json'), "
        f"[byte[]]@({','.join(str(value) for value in external_bytes)}))"
    )

    result, report = run_attempt_harness(
        tmp_path,
        fixture,
        native_body=native_body,
        include_promotion_seams=True,
        before_promotion_body=before_hook,
    )

    assert result.returncode == 0, result.stderr
    assert report is not None
    outcome = report["outcome"]
    assert outcome["success"] is False
    assert "active calibration tree changed" in outcome["primary_reason"].lower()
    assert report["move_calls"] == []
    assert report["remove_calls"] == []
    assert (active / "unrelated-am2.json").read_bytes() == external_bytes
    assert not list(active.parent.glob(".am1-candidate-*"))
    assert not list(active.parent.glob(".am1-withdrawn-*"))
    assert "CALIBRATION_RESULT=PASS" not in result.stdout


def test_second_rename_failure_restores_original_active_and_preserves_primary(tmp_path: Path) -> None:
    fixture = prepare_attempt_fixture(tmp_path)
    active = fixture["active"]
    assert isinstance(active, Path)
    original = tree_bytes(active)
    native_body = native_write_body(left=make_calibration(40), right=make_calibration(50))
    move_body = """
if ($Operation -ceq 'promote-candidate') { throw 'simulated second rename failure' }
[System.IO.Directory]::Move($Source, $Destination)
"""

    result, report = run_attempt_harness(
        tmp_path,
        fixture,
        native_body=native_body,
        include_promotion_seams=True,
        move_body=move_body,
    )

    assert result.returncode == 0, result.stderr
    assert report is not None
    outcome = report["outcome"]
    assert outcome["success"] is False
    assert outcome["primary_reason"] == "simulated second rename failure"
    assert outcome["secondary_failures"] == []
    assert tree_bytes(active) == original
    assert Path(outcome["candidate_path"]).is_dir()
    assert not Path(outcome["withdrawal_path"]).exists()
    assert [call["operation"] for call in report["move_calls"]] == [
        "withdraw-active",
        "promote-candidate",
        "restore-withdrawal",
    ]
    assert report["remove_calls"] == []
    assert "CALIBRATION_RESULT=PASS" not in result.stdout


def test_post_verification_failure_rolls_back_complete_directories_before_cleanup(tmp_path: Path) -> None:
    fixture = prepare_attempt_fixture(tmp_path)
    active = fixture["active"]
    assert isinstance(active, Path)
    original = tree_bytes(active)
    native_body = native_write_body(left=make_calibration(40), right=make_calibration(50))

    result, report = run_attempt_harness(
        tmp_path,
        fixture,
        native_body=native_body,
        include_promotion_seams=True,
        after_second_move_body="throw 'simulated final verification failure'",
    )

    assert result.returncode == 0, result.stderr
    assert report is not None
    outcome = report["outcome"]
    assert outcome["success"] is False
    assert outcome["primary_reason"] == "simulated final verification failure"
    assert tree_bytes(active) == original
    assert Path(outcome["candidate_path"]).is_dir()
    assert not Path(outcome["withdrawal_path"]).exists()
    assert [call["operation"] for call in report["move_calls"]] == [
        "withdraw-active",
        "promote-candidate",
        "return-promoted-active",
        "restore-withdrawal",
    ]
    assert report["remove_calls"] == []
    assert "CALIBRATION_RESULT=PASS" not in result.stdout


def test_rollback_failure_is_secondary_and_primary_promotion_error_survives(tmp_path: Path) -> None:
    fixture = prepare_attempt_fixture(tmp_path)
    active = fixture["active"]
    assert isinstance(active, Path)
    native_body = native_write_body(left=make_calibration(40), right=make_calibration(50))
    move_body = """
if ($Operation -ceq 'promote-candidate') { throw 'simulated primary promotion failure' }
if ($Operation -ceq 'restore-withdrawal') { throw 'simulated rollback failure' }
[System.IO.Directory]::Move($Source, $Destination)
"""

    result, report = run_attempt_harness(
        tmp_path,
        fixture,
        native_body=native_body,
        include_promotion_seams=True,
        move_body=move_body,
    )

    assert result.returncode == 0, result.stderr
    assert report is not None
    outcome = report["outcome"]
    assert outcome["success"] is False
    assert outcome["primary_reason"] == "simulated primary promotion failure"
    assert any("simulated rollback failure" in failure for failure in outcome["secondary_failures"])
    assert not active.exists()
    assert Path(outcome["candidate_path"]).is_dir()
    assert Path(outcome["withdrawal_path"]).is_dir()
    assert report["remove_calls"] == []
    assert "CALIBRATION_RESULT=PASS" not in result.stdout


def test_withdrawal_cleanup_failure_reports_verified_new_active_without_rollback(tmp_path: Path) -> None:
    fixture = prepare_attempt_fixture(tmp_path)
    active = fixture["active"]
    assert isinstance(active, Path)
    original = tree_bytes(active)
    native_body = native_write_body(left=make_calibration(40), right=make_calibration(50))

    result, report = run_attempt_harness(
        tmp_path,
        fixture,
        native_body=native_body,
        include_promotion_seams=True,
        remove_body="throw 'simulated withdrawal cleanup failure'",
    )

    assert result.returncode == 0, result.stderr
    assert report is not None
    outcome = report["outcome"]
    assert outcome["success"] is False
    assert outcome["primary_reason"] == "simulated withdrawal cleanup failure"
    assert outcome["active_pair_state"] == "PROMOTED_VERIFIED"
    assert outcome["withdrawal_cleanup_state"] == "FAILED_OR_PARTIAL"
    assert outcome["secondary_failures"] == []
    active_left = active / "so101_leader_bi_left.json"
    active_right = active / "so101_leader_bi_right.json"
    assert outcome["left"]["sha256"] == hashlib.sha256(active_left.read_bytes()).hexdigest().upper()
    assert outcome["right"]["sha256"] == hashlib.sha256(active_right.read_bytes()).hexdigest().upper()
    assert tree_bytes(active) != original
    assert Path(outcome["withdrawal_path"]).is_dir()
    assert tree_bytes(Path(outcome["withdrawal_path"])) == original
    assert [call["operation"] for call in report["move_calls"]] == [
        "withdraw-active",
        "promote-candidate",
    ]
    assert report["remove_calls"] == [outcome["withdrawal_path"]]
    assert "ACTIVE_PAIR_STATE=PROMOTED_VERIFIED" in result.stdout
    assert f"ACTIVE_LEFT_PATH={active_left}" in result.stdout
    assert f"ACTIVE_RIGHT_PATH={active_right}" in result.stdout
    assert "WITHDRAWAL_CLEANUP_STATE=FAILED_OR_PARTIAL" in result.stdout
    assert f"WITHDRAWAL_PATH={outcome['withdrawal_path']}" in result.stdout
    assert "CALIBRATION_RESULT=FAIL" in result.stdout
    assert "CALIBRATION_RESULT=PASS" not in result.stdout


def test_production_launcher_does_not_mark_missing_executable_as_launched(tmp_path: Path) -> None:
    missing_executable = tmp_path / "missing-python.exe"
    body = f"""
$command = [pscustomobject]@{{
    executable = {ps_literal(missing_executable)}
    arguments = @()
    working_directory = {ps_literal(tmp_path)}
}}
$launched = $false
$exitCode = $null
$reason = $null
try {{
    Invoke-Am1InteractiveCalibrationCommand -Command $command -StagingLeaf {ps_literal(tmp_path)} `
        -Launched ([ref]$launched) -ExitCode ([ref]$exitCode)
}}
catch {{ $reason = $_.Exception.Message }}
[Console]::Out.WriteLine(([ordered]@{{
    launched = $launched
    exit_code = $exitCode
    reason = $reason
}} | ConvertTo-Json -Compress))
"""

    result = run_harness(tmp_path, body)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.splitlines()[-1])
    assert report["reason"]
    assert report["launched"] is False
    assert report["exit_code"] is None


@pytest.mark.parametrize(("launched", "exit_code", "reason"), [(False, 0, "launch"), (True, 42, "exit")])
def test_outcome_writer_refuses_synthetic_success_without_runtime_invariants(
    launched: bool,
    exit_code: int,
    reason: str,
    tmp_path: Path,
) -> None:
    body = f"""
$outcome = [pscustomobject][ordered]@{{
    success = $true
    primary_reason = $null
    secondary_failures = @()
    launched = ${str(launched).lower()}
    exit_code = {exit_code}
    active_pair_state = 'PROMOTED_VERIFIED'
    withdrawal_cleanup_state = 'COMPLETE'
    run_directory = 'run-evidence'
    backup_directory = 'pair-backup'
    staging_leaf = 'staged-evidence'
    transcript_path = 'transcript-path'
    withdrawal_path = 'withdrawal-path'
    left = [pscustomobject]@{{ path = 'left-path'; sha256 = 'left-hash' }}
    right = [pscustomobject]@{{ path = 'right-path'; sha256 = 'right-hash' }}
}}
$failure = $null
try {{ Write-Am1CalibrationOutcome -Outcome $outcome }}
catch {{ $failure = $_.Exception.Message }}
[Console]::Out.WriteLine('WRITER_JSON=' + ([ordered]@{{ failure = $failure }} | ConvertTo-Json -Compress))
"""

    result = run_harness(tmp_path, body)

    assert result.returncode == 0, result.stderr
    report = json.loads(
        next(line.removeprefix("WRITER_JSON=") for line in result.stdout.splitlines() if line.startswith("WRITER_JSON="))
    )
    assert reason in report["failure"].lower()
    assert "CALIBRATION_RESULT=PASS" not in result.stdout


@pytest.mark.parametrize(
    ("left_port", "confirmation", "reason"),
    [
        ("COM9", "CALIBRATE", "left port"),
        ("COM8", "calibrate", "confirmation"),
    ],
)
def test_calibrate_main_refuses_identity_or_confirmation_before_attempt(
    left_port: str,
    confirmation: str,
    reason: str,
    tmp_path: Path,
) -> None:
    body = f"""
$attemptCalls = 0
$attempt = {{ $script:attemptCalls += 1; throw 'attempt must not be called' }}
$failure = $null
try {{
    $null = Invoke-Am1LeaderCalibrationMain -StatusMode $false -CalibrateMode $true `
        -Confirmation {ps_literal(confirmation)} -LeftPortValue {ps_literal(left_port)} `
        -RightPortValue 'COM7' -LeaderIdValue 'so101_leader_bi' `
        -ArmProfileValue 'so-arm-5dof' -RunIdValue 'test-run' `
        -CalibrationAttemptInvoker $attempt
}}
catch {{ $failure = $_.Exception.Message }}
[Console]::Out.WriteLine(([ordered]@{{ failure = $failure; attempt_calls = $attemptCalls }} |
    ConvertTo-Json -Compress))
"""

    result = run_harness(tmp_path, body)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.splitlines()[-1])
    assert reason in report["failure"].lower()
    assert report["attempt_calls"] == 0


@pytest.mark.parametrize(("success", "expected_code"), [(True, 0), (False, 1)])
def test_calibrate_main_dispatches_once_and_maps_outcome_to_exit_code(
    success: bool,
    expected_code: int,
    tmp_path: Path,
) -> None:
    outcome = f"""[pscustomobject][ordered]@{{
    success = ${str(success).lower()}
    primary_reason = {"$null" if success else "'simulated calibration failure'"}
    secondary_failures = @()
    launched = ${str(success).lower()}
    exit_code = {0 if success else "$null"}
    active_pair_state = {"'PROMOTED_VERIFIED'" if success else "$null"}
    withdrawal_cleanup_state = {"'COMPLETE'" if success else "$null"}
    run_directory = 'run-evidence'
    backup_directory = 'pair-backup'
    staging_leaf = 'staged-evidence'
    transcript_path = 'transcript-path'
    withdrawal_path = 'withdrawal-path'
    left = {"[pscustomobject]@{ path = 'left-path'; sha256 = 'left-hash' }" if success else "$null"}
    right = {"[pscustomobject]@{ path = 'right-path'; sha256 = 'right-hash' }" if success else "$null"}
}}"""
    body = f"""
$attemptCalls = 0
$observed = $null
$attempt = {{
    param(
        $RepositoryRoot, $PythonPath, $LeftPortValue, $RightPortValue,
        $LeaderIdValue, $ArmProfileValue, $Confirmation, $RunId
    )
    $script:attemptCalls += 1
    $script:observed = [ordered]@{{
        repository = $RepositoryRoot
        python = $PythonPath
        left_port = $LeftPortValue
        right_port = $RightPortValue
        leader_id = $LeaderIdValue
        arm_profile = $ArmProfileValue
        confirmation = $Confirmation
        run_id = $RunId
    }}
    return ({outcome})
}}
$code = Invoke-Am1LeaderCalibrationMain -StatusMode $false -CalibrateMode $true `
    -Confirmation 'CALIBRATE' -LeftPortValue 'COM8' -RightPortValue 'COM7' `
    -LeaderIdValue 'so101_leader_bi' -ArmProfileValue 'so-arm-5dof' `
    -RunIdValue 'test-run' -CalibrationAttemptInvoker $attempt
[Console]::Out.WriteLine('MAIN_JSON=' + ([ordered]@{{
    code = $code
    attempt_calls = $attemptCalls
    observed = $observed
}} | ConvertTo-Json -Depth 20 -Compress))
"""

    result = run_harness(tmp_path, body)

    assert result.returncode == 0, result.stderr
    report = json.loads(
        next(line.removeprefix("MAIN_JSON=") for line in result.stdout.splitlines() if line.startswith("MAIN_JSON="))
    )
    assert report["code"] == expected_code
    assert report["attempt_calls"] == 1
    assert report["observed"]["left_port"] == "COM8"
    assert report["observed"]["right_port"] == "COM7"
    assert report["observed"]["leader_id"] == "so101_leader_bi"
    assert report["observed"]["arm_profile"] == "so-arm-5dof"
    assert report["observed"]["confirmation"] == "CALIBRATE"
    assert report["observed"]["run_id"] == "test-run"
    if success:
        assert result.stdout.count("CALIBRATION_RESULT=PASS") == 1
        assert "ACTIVE_LEFT_PATH=left-path" in result.stdout
        assert "ACTIVE_RIGHT_PATH=right-path" in result.stdout
        assert "CALIBRATION_RESULT=FAIL" not in result.stdout
    else:
        assert result.stdout.count("CALIBRATION_RESULT=FAIL") == 1
        assert "CALIBRATION_RESULT=PASS" not in result.stdout


def test_documentation_defines_simple_am1_leader_commands_and_identity() -> None:
    text = DOCUMENTATION_PATH.read_text(encoding="utf-8")
    normalized = " ".join(text.split())

    assert "### Simple AM1 leader calibration and recovery" in text
    assert (
        r".\.venv\Scripts\python.exe .\tools\check_am1_leader_buses.py CHECK"
        in normalized
    )
    assert (
        r"pwsh -NoLogo -NoProfile -File .\tools\calibrate_am1_leaders.ps1 -Status"
        in normalized
    )
    assert (
        r"pwsh -NoLogo -NoProfile -File .\tools\calibrate_am1_leaders.ps1 "
        r"-Calibrate -Confirm CALIBRATE"
        in normalized
    )
    assert "physical/logical left is `COM8`" in text
    assert "physical/logical right is `COM7`" in text


def test_documentation_covers_recovery_wrist_roll_and_no_robot_side_check() -> None:
    text = DOCUMENTATION_PATH.read_text(encoding="utf-8")
    normalized = " ".join(text.split())
    simple_section = text.split(
        "### Simple AM1 leader calibration and recovery", maxsplit=1
    )[1].split("<details>", maxsplit=1)[0]
    calibration_stage = text.split(
        "3. For a separately authorized one-shot calibration", maxsplit=1
    )[1].split("4. After a clean calibration `PASS`", maxsplit=1)[0]
    expected_no_robot = (
        r".\.venv\Scripts\python.exe .\examples\alohamini\teleoperate_bi.py --no_robot "
        r"--robot.robot_model alohamini1 --teleop.left_port COM8 --teleop.right_port COM7 "
        r"--teleop.id so101_leader_bi --teleop.arm_profile so-arm-5dof "
        r"--require_calibration_match --duration_s 30 --fps 5 --no_keyboard --no_rerun"
    )

    assert "Before promotion, any failure leaves the active calibration files unchanged" in text
    assert "any backup, transcript, or staged evidence already created" in text
    assert "preserves the backup, transcript, and staged evidence" not in text
    assert "complete fresh rerun" in text
    assert "Do not force wrist roll during range recording" in text
    assert "implementation assigns `0..4095`" in text
    assert expected_no_robot in normalized
    assert "physical left gripper must change only `arm_left_gripper.pos`" in text
    assert "physical right gripper must change only `arm_right_gripper.pos`" in text
    assert "Follower/body 12 V power is off" in text
    assert "Pi motor host is stopped" in text
    assert "Stop immediately" in text
    for stop_term in (
        "unexpected motion",
        "resistance",
        "sound",
        "heat",
        "current",
        "cable strain",
        "disconnect",
        "prompt",
        "communication error",
    ):
        assert stop_term in calibration_stage
    for stop_term in ("wrong-side", "both-side", "sound", "heat", "current", "disconnect"):
        assert stop_term in text
    for recovery_term in (
        "FAIL_CLOSED_RECOVERY=Rename-Item -LiteralPath",
        "all leader power off",
        "no LeRobot process running",
        "active `so_leader` path is absent",
        "printed withdrawal path is a complete ordinary directory",
        "inspect and validate the active directory before any cleanup",
    ):
        assert recovery_term in simple_section


def test_documentation_deprecates_old_runner_as_historical_only() -> None:
    text = DOCUMENTATION_PATH.read_text(encoding="utf-8")

    assert "Historical/deprecated tooling: `tools\\packet2n_r5_leader_mapping.ps1`" in text
    for obsolete_claim in (
        "sole authoritative future corrected-port procedure",
        "only described future correction",
        "current AM1 Packet 2N authority",
        "The runner is the only Packet 2N-R5R operator path",
        "##### Authoritative staged interface",
    ):
        assert obsolete_claim not in text

    legacy_command = (
        r"pwsh -NoLogo -NoProfile -File .\tools\packet2n_r5_leader_mapping.ps1"
    )
    command_offsets = []
    search_from = 0
    while (offset := text.find(legacy_command, search_from)) != -1:
        command_offsets.append(offset)
        search_from = offset + len(legacy_command)

    assert command_offsets
    for offset in command_offsets:
        preceding = text[:offset]
        details_start = preceding.rfind("<details>")
        details_end = preceding.rfind("</details>")
        assert details_start > details_end
        summary_end = text.find("</summary>", details_start, offset)
        assert summary_end != -1
        historical_summary = text[details_start:summary_end].lower()
        assert "historical" in historical_summary
        assert "deprecated" in historical_summary or "superseded" in historical_summary
