[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Status", "Calibrate", "MapLeft", "MapRight", "Verify")]
    [string]$Stage,

    [Parameter()]
    [string]$StatePath = "C:\Users\pickm\AlohaMini1Logs\packet2n-r5-state.json",

    [Parameter()]
    [string]$Confirm,

    [Parameter(DontShow = $true)]
    [string]$TestPlanPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RunnerVersion = "packet2n-r5-runner-v1"
$SchemaVersion = "1"
$PacketIdentity = "packet2n-r5r"
$BehaviorBaseline = "cae57b59db1d9156be568aa4b216fc90701aa741"
$ExpectedBranch = "fix/am1-elbow-commissioning"
$ExpectedPorts = [ordered]@{
    physical_left  = "COM8"
    logical_left   = "COM8"
    physical_right = "COM7"
    logical_right  = "COM7"
}
$ExpectedLeaderId = "so101_leader_bi"
$ExpectedProfile = "so-arm-5dof"
$ExpectedMapKeys = @(
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
    "arm_right_gripper.pos"
)
$ExpectedCalibrationKeys = @(
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper"
)
$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ActualNoRobotProof = "NO_ROBOT: robot client construction and connection skipped."
$ActualCleanupPrefix = "Shutdown complete:"
$RealCalibrationRoot = "C:\Users\pickm\.cache\huggingface\lerobot\calibration"
$RealBackupDirectory = "C:\Users\pickm\AlohaMini1Backups\packet2n-r5-20260822-121722-7941f445-9587-4345-8e2f-edd54ca750f6"
$RealManifestSha256 = "B90DF72155C60996B4E2704E4A44ED1895BBAEA0C0A332DC24674EC3FA399B8A"
$RealLogsDirectory = "C:\Users\pickm\AlohaMini1Logs"
$RealBackupMetadata = [ordered]@{
    left = [ordered]@{
        path        = (Join-Path $RealBackupDirectory "so101_leader_bi_left.json")
        sha256      = "6F5D6126E84398D0621A26E74E4DF6678EBA7C14C62D343020610B4D5D8B3D8C"
        size        = [int64]960
        source_mtime = "2026-08-15T05:18:25.9699568Z"
    }
    right = [ordered]@{
        path        = (Join-Path $RealBackupDirectory "so101_leader_bi_right.json")
        sha256      = "65A301F20FC7DC96BD7FB5982E3670BF1A01F535953D7A253AB8D33A03646F11"
        size        = [int64]961
        source_mtime = "2026-08-15T05:19:53.2654429Z"
    }
}
$OverrideEnvironmentNames = @(
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONSTARTUP",
    "PYTHONUSERBASE",
    "HF_LEROBOT_CALIBRATION",
    "HF_LEROBOT_HOME",
    "HF_HOME",
    "HF_HUB_CACHE",
    "HUGGINGFACE_HUB_CACHE"
)
$ReviewedImportModules = @(
    "calibrate_bi",
    "teleoperate_bi",
    "leader_client_utils",
    "lerobot.teleoperators.bi_so_leader.bi_so_leader",
    "lerobot.teleoperators.so_leader.so_leader"
)

function Invoke-ExternalCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter()]
        [string[]]$Arguments = @(),

        [Parameter()]
        [string]$WorkingDirectory = $RepositoryRoot
    )

    $stdoutPath = [System.IO.Path]::GetTempFileName()
    $stderrPath = [System.IO.Path]::GetTempFileName()
    try {
        $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -WorkingDirectory $WorkingDirectory -NoNewWindow -PassThru -Wait -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
        return [ordered]@{
            exit_code = [int]$process.ExitCode
            stdout    = @((Get-Content -LiteralPath $stdoutPath -ErrorAction SilentlyContinue))
            stderr    = @((Get-Content -LiteralPath $stderrPath -ErrorAction SilentlyContinue))
        }
    }
    finally {
        Remove-Item -LiteralPath $stdoutPath -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $stderrPath -ErrorAction SilentlyContinue
    }
}

function Get-Sha256Hex {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Missing file: $Path"
    }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
}

function Get-TextSha256Hex {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Text
    )

    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "")
    }
    finally {
        $sha.Dispose()
    }
}

function Ensure-ParentDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
}

function Write-TextAtomic {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Text
    )

    if (Test-Path -LiteralPath $Path) {
        throw "Refusing to overwrite existing file: $Path"
    }
    Ensure-ParentDirectory -Path $Path
    $tempPath = "$Path.tmp"
    [System.IO.File]::WriteAllText($tempPath, $Text, [System.Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $tempPath -Destination $Path
}

function Append-TextLine {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Text
    )

    [System.IO.File]::AppendAllText($Path, $Text + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
}

function ConvertTo-CanonicalJson {
    param(
        [Parameter(Mandatory = $true)]
        $Value
    )

    return ($Value | ConvertTo-Json -Depth 100)
}

function Write-JsonAtomic {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        $Value,

        [Parameter()]
        [switch]$Overwrite
    )

    Ensure-ParentDirectory -Path $Path
    if ((-not $Overwrite) -and (Test-Path -LiteralPath $Path)) {
        throw "Refusing to overwrite existing file: $Path"
    }
    $tempPath = "$Path.tmp"
    [System.IO.File]::WriteAllText($tempPath, ((ConvertTo-CanonicalJson -Value $Value) + [Environment]::NewLine), [System.Text.UTF8Encoding]::new($false))
    if (Test-Path -LiteralPath $Path) {
        [System.IO.File]::Move($tempPath, $Path, $true)
    }
    else {
        Move-Item -LiteralPath $tempPath -Destination $Path
    }
}

function Read-JsonFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Missing JSON file: $Path"
    }
    return (Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json -AsHashtable -Depth 100)
}

function New-Failure {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    throw [System.InvalidOperationException]::new($Message)
}

function Require-Confirmation {
    param(
        [Parameter(Mandatory = $true)]
        [string]$StageName,

        [string]$ConfirmValue
    )

    $expected = switch ($StageName) {
        "Calibrate" { "CALIBRATE" }
        "MapLeft" { "MAPLEFT" }
        "MapRight" { "MAPRIGHT" }
        default { $null }
    }
    if ($null -ne $expected -and $ConfirmValue -ne $expected) {
        New-Failure "Stage $StageName requires -Confirm $expected"
    }
}

function Get-RunnerSha256 {
    return Get-Sha256Hex -Path $PSCommandPath
}

function Get-SessionId {
    return [guid]::NewGuid().ToString()
}

function Get-TestModePlan {
    if ($env:PACKET2N_R5_TEST_MODE -ne "1") {
        return $null
    }
    if (-not $TestPlanPath) {
        New-Failure "PACKET2N_R5_TEST_MODE=1 requires -TestPlanPath"
    }
    $plan = Read-JsonFile -Path $TestPlanPath
    $plan.is_test_mode = $true
    return $plan
}

function Get-ImportSourcesMatch {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonPath
    )

    $importCommand = "import importlib, sys; sys.path.insert(0, 'examples/alohamini'); names=('calibrate_bi','teleoperate_bi','leader_client_utils','lerobot.teleoperators.bi_so_leader.bi_so_leader','lerobot.teleoperators.so_leader.so_leader'); modules=tuple(importlib.import_module(n) for n in names); print(*(m.__file__ for m in modules), sep='\n')"
    $result = Invoke-ExternalCommand -FilePath $PythonPath -Arguments @("-c", $importCommand) -WorkingDirectory $RepositoryRoot
    if ($result.exit_code -ne 0 -or $result.stderr.Count -gt 0) {
        return $false
    }
    $expected = @(
        (Join-Path $RepositoryRoot "examples\alohamini\calibrate_bi.py"),
        (Join-Path $RepositoryRoot "examples\alohamini\teleoperate_bi.py"),
        (Join-Path $RepositoryRoot "examples\alohamini\leader_client_utils.py"),
        (Join-Path $RepositoryRoot "src\lerobot\teleoperators\bi_so_leader\bi_so_leader.py"),
        (Join-Path $RepositoryRoot "src\lerobot\teleoperators\so_leader\so_leader.py")
    )
    if ($result.stdout.Count -ne $expected.Count) {
        return $false
    }
    for ($index = 0; $index -lt $expected.Count; $index++) {
        $actualPath = (Resolve-Path -LiteralPath ([string]$result.stdout[$index]).Trim()).Path
        $expectedPath = (Resolve-Path -LiteralPath $expected[$index]).Path
        if (-not $actualPath.Equals($expectedPath, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $false
        }
    }
    return $true
}

function Get-RealPlan {
    $sessionId = Get-SessionId
    $utcStart = [DateTime]::UtcNow.ToString("o")
    $pythonPath = Get-RepositoryPythonPath
    $branchResult = Invoke-ExternalCommand -FilePath "git" -Arguments @("rev-parse", "--abbrev-ref", "HEAD")
    if ($branchResult.exit_code -ne 0 -or $branchResult.stderr.Count -gt 0 -or $branchResult.stdout.Count -ne 1) {
        New-Failure "Git branch query failed"
    }
    $headResult = Invoke-ExternalCommand -FilePath "git" -Arguments @("rev-parse", "HEAD")
    if ($headResult.exit_code -ne 0 -or $headResult.stderr.Count -gt 0 -or $headResult.stdout.Count -ne 1) {
        New-Failure "Git HEAD query failed"
    }
    $statusResult = Invoke-ExternalCommand -FilePath "git" -Arguments @("status", "--porcelain=v1", "--untracked-files=all")
    if ($statusResult.exit_code -ne 0 -or $statusResult.stderr.Count -gt 0) {
        New-Failure "Git clean-status query failed"
    }
    $baselineResult = Invoke-ExternalCommand -FilePath "git" -Arguments @("merge-base", "--is-ancestor", $BehaviorBaseline, "HEAD")
    if ($baselineResult.exit_code -ne 0 -and $baselineResult.exit_code -ne 1) {
        New-Failure "Git baseline ancestry query failed"
    }
    $diffResult = Invoke-ExternalCommand -FilePath "git" -Arguments @("diff", "--quiet", $BehaviorBaseline, "--", ".", ":(exclude)docs/alohamini/alohamini.md")
    if ($diffResult.exit_code -ne 0 -and $diffResult.exit_code -ne 1) {
        New-Failure "Git protected-path diff query failed"
    }
    $pythonResolved = Test-Path -LiteralPath $pythonPath -PathType Leaf
    $pythonEnvClean = $true
    foreach ($name in $OverrideEnvironmentNames) {
        if (-not [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($name, "Process"))) {
            $pythonEnvClean = $false
            break
        }
    }
    $rootResult = if ($pythonResolved) {
        Invoke-ExternalCommand -FilePath $pythonPath -Arguments @("-c", "from lerobot.utils.constants import HF_LEROBOT_CALIBRATION; print(HF_LEROBOT_CALIBRATION)") -WorkingDirectory $RepositoryRoot
    }
    else {
        $null
    }
    $rootMatches = $false
    if ($null -ne $rootResult -and $rootResult.exit_code -eq 0 -and $rootResult.stderr.Count -eq 0 -and $rootResult.stdout.Count -eq 1) {
        $rootMatches = ([string]$rootResult.stdout[0]).Trim() -eq $RealCalibrationRoot
    }
    $importSourcesMatch = $pythonResolved -and $pythonEnvClean -and (Get-ImportSourcesMatch -PythonPath $pythonPath)
    return [ordered]@{
        is_test_mode                     = $false
        session_id                       = $sessionId
        utc_start                        = $utcStart
        expected_branch                  = $ExpectedBranch
        behavior_baseline                = $BehaviorBaseline
        head                             = ([string]$headResult.stdout[0]).Trim()
        repo_root                        = $RepositoryRoot
        worktree_clean                   = $statusResult.stdout.Count -eq 0
        branch_matches_expected          = ([string]$branchResult.stdout[0]).Trim() -ceq $ExpectedBranch
        baseline_ancestor                = $baselineResult.exit_code -eq 0
        protected_runtime_paths_unchanged = $diffResult.exit_code -eq 0
        python_env_clean                 = $pythonEnvClean
        python_resolved                  = $pythonResolved
        python_path                      = $pythonPath
        import_sources_match             = $importSourcesMatch
        calibration_root_matches_expected = $rootMatches
        calibration_root                 = $RealCalibrationRoot
        state_root                       = $RealLogsDirectory
        manifest                         = [ordered]@{
            path   = (Join-Path $RealBackupDirectory "manifest.json")
            sha256 = $RealManifestSha256
        }
        calibration                      = [ordered]@{
            left = [ordered]@{
                path             = (Join-Path $RealCalibrationRoot "teleoperators\so_leader\so101_leader_bi_left.json")
                backup_path      = $RealBackupMetadata.left.path
                backup_sha256    = $RealBackupMetadata.left.sha256
                backup_size      = $RealBackupMetadata.left.size
                source_mtime_utc = $RealBackupMetadata.left.source_mtime
            }
            right = [ordered]@{
                path             = (Join-Path $RealCalibrationRoot "teleoperators\so_leader\so101_leader_bi_right.json")
                backup_path      = $RealBackupMetadata.right.path
                backup_sha256    = $RealBackupMetadata.right.sha256
                backup_size      = $RealBackupMetadata.right.size
                source_mtime_utc = $RealBackupMetadata.right.source_mtime
            }
        }
        stage_plan                       = [ordered]@{
            Calibrate = [ordered]@{
                transcript_path = (Join-Path $RealLogsDirectory "packet2n-r5-calibration-$sessionId.log")
                evidence_path   = (Join-Path $RealLogsDirectory "packet2n-r5-evidence-$sessionId.json")
            }
            MapLeft = [ordered]@{
                map_path       = (Join-Path $RealLogsDirectory "packet2n-r5-physical-left-only-$sessionId.log")
                physical_side  = "left"
            }
            MapRight = [ordered]@{
                map_path       = (Join-Path $RealLogsDirectory "packet2n-r5-physical-right-only-$sessionId.log")
                physical_side  = "right"
            }
        }
    }
}

function Get-ExecutionPlan {
    $testPlan = Get-TestModePlan
    if ($null -ne $testPlan) {
        return $testPlan
    }
    return Get-RealPlan
}

function Get-TestModeRoot {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Plan
    )

    return (Split-Path -Parent $Plan.calibration_root)
}

function Assert-TestModePath {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if ($env:PACKET2N_R5_TEST_MODE -ne "1") {
        return
    }
    $resolvedRoot = [System.IO.Path]::GetFullPath((Get-TestModeRoot -Plan $Plan))
    $resolvedPath = [System.IO.Path]::GetFullPath($Path)
    if (-not $resolvedPath.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        New-Failure "Test-mode mutable path escaped validated root: $Path"
    }
}

function Get-FileInfoSnapshot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $item = Get-Item -LiteralPath $Path
    return [ordered]@{
        path   = $Path
        sha256 = Get-Sha256Hex -Path $Path
        size   = [int64]$item.Length
    }
}

function Assert-PathMissing {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (Test-Path -LiteralPath $Path) {
        New-Failure "Refusing to overwrite existing file: $Path"
    }
}

function Get-RepositoryPythonPath {
    param(
        [hashtable]$Plan
    )

    if ($null -ne $Plan -and $Plan.ContainsKey("python_path")) {
        return $Plan.python_path
    }
    return (Join-Path $RepositoryRoot ".venv\Scripts\python.exe")
}

function Build-StageCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$StageName,

        [hashtable]$Plan
    )

    $pythonPath = Get-RepositoryPythonPath -Plan $Plan
    $calibrateScript = Join-Path $RepositoryRoot "examples\alohamini\calibrate_bi.py"
    $teleoperateScript = Join-Path $RepositoryRoot "examples\alohamini\teleoperate_bi.py"
    $arguments = switch ($StageName) {
        "Calibrate" {
            @(
                $calibrateScript,
                "--teleop.left_port", $ExpectedPorts.physical_left,
                "--teleop.right_port", $ExpectedPorts.physical_right,
                "--teleop.id", $ExpectedLeaderId,
                "--teleop.arm_profile", $ExpectedProfile,
                "--force_fresh_calibration"
            )
        }
        "MapLeft" {
            @(
                $teleoperateScript,
                "--teleop.left_port", $ExpectedPorts.physical_left,
                "--teleop.right_port", $ExpectedPorts.physical_right,
                "--teleop.id", $ExpectedLeaderId,
                "--teleop.arm_profile", $ExpectedProfile,
                "--no_robot",
                "--robot.robot_model", "alohamini1",
                "--require_calibration_match",
                "--duration_s", "12",
                "--fps", "5",
                "--start_paused",
                "--no_keyboard",
                "--no_rerun"
            )
        }
        "MapRight" {
            @(
                $teleoperateScript,
                "--teleop.left_port", $ExpectedPorts.physical_left,
                "--teleop.right_port", $ExpectedPorts.physical_right,
                "--teleop.id", $ExpectedLeaderId,
                "--teleop.arm_profile", $ExpectedProfile,
                "--no_robot",
                "--robot.robot_model", "alohamini1",
                "--require_calibration_match",
                "--duration_s", "12",
                "--fps", "5",
                "--start_paused",
                "--no_keyboard",
                "--no_rerun"
            )
        }
        default { @() }
    }
    return [ordered]@{
        executable = $pythonPath
        arguments  = $arguments
    }
}

function Assert-CalibrationSchema {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Calibration,

        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    $keys = @($Calibration.Keys | Sort-Object)
    $expected = @($ExpectedCalibrationKeys | Sort-Object)
    if (($keys -join ",") -ne ($expected -join ",")) {
        New-Failure "$Label calibration schema mismatch"
    }
    foreach ($joint in $ExpectedCalibrationKeys) {
        $record = $Calibration[$joint]
        if ($null -eq $record) {
            New-Failure "$Label calibration is missing joint $joint"
        }
        $recordKeys = @($record.Keys | Sort-Object)
        $expectedRecordKeys = @("drive_mode", "homing_offset", "id", "range_max", "range_min")
        if (($recordKeys -join ",") -ne ($expectedRecordKeys -join ",")) {
            New-Failure "$Label calibration record mismatch for $joint"
        }
        if (($record.id -as [int]) -lt 1 -or ($record.id -as [int]) -gt 6) {
            New-Failure "$Label calibration id out of range for $joint"
        }
        if (($record.drive_mode -as [int]) -ne 0) {
            New-Failure "$Label calibration drive_mode must be 0 for $joint"
        }
        $rangeMin = [int]$record.range_min
        $rangeMax = [int]$record.range_max
        if ($rangeMin -lt 0 -or $rangeMin -ge $rangeMax -or $rangeMax -gt 4095) {
            New-Failure "$Label calibration range is invalid for $joint"
        }
        if ($joint -eq "wrist_roll" -and ($rangeMin -ne 0 -or $rangeMax -ne 4095)) {
            New-Failure "$Label wrist_roll calibration must be exactly 0..4095"
        }
    }
}

function Get-CalibrationSnapshot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    $calibration = Read-JsonFile -Path $Path
    Assert-CalibrationSchema -Calibration $calibration -Label $Label
    return [ordered]@{
        path        = $Path
        sha256      = Get-Sha256Hex -Path $Path
        size        = [int64](Get-Item -LiteralPath $Path).Length
        calibration = $calibration
    }
}

function Get-CurrentIdentities {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Plan
    )

    $left = Get-CalibrationSnapshot -Path $Plan.calibration.left.path -Label "left"
    $right = Get-CalibrationSnapshot -Path $Plan.calibration.right.path -Label "right"
    return [ordered]@{
        left  = $left
        right = $right
    }
}

function Assert-ManifestAndBackups {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Plan
    )

    $manifestPath = $Plan.manifest.path
    if ((Get-Sha256Hex -Path $manifestPath) -ne $Plan.manifest.sha256) {
        New-Failure "Immutable manifest hash mismatch"
    }
    foreach ($side in @("left", "right")) {
        $backupPath = $Plan.calibration[$side].backup_path
        $backupInfo = Get-FileInfoSnapshot -Path $backupPath
        if ($backupInfo.sha256 -ne $Plan.calibration[$side].backup_sha256) {
            New-Failure "$side backup hash mismatch"
        }
        if ($backupInfo.size -ne [int64]$Plan.calibration[$side].backup_size) {
            New-Failure "$side backup size mismatch"
        }
        if ($Plan.calibration[$side].ContainsKey("source_mtime_utc")) {
            $currentCalibrationInfo = Get-Item -LiteralPath $Plan.calibration[$side].path
            if ($currentCalibrationInfo.LastWriteTimeUtc.ToString("o") -ne $Plan.calibration[$side].source_mtime_utc) {
                New-Failure "$side source last-write timestamp mismatch"
            }
        }
        $backupCalibration = Read-JsonFile -Path $backupPath
        Assert-CalibrationSchema -Calibration $backupCalibration -Label "$side backup"
    }
}

function Assert-RepoAndEnvGuards {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Plan
    )

    if (-not [bool]$Plan.worktree_clean) {
        New-Failure "Guard refusal: tracked/untracked worktree must be clean"
    }
    if ($Plan.ContainsKey("branch_matches_expected") -and -not [bool]$Plan.branch_matches_expected) {
        New-Failure "Guard refusal: current branch does not match the reviewed branch"
    }
    if ($Plan.ContainsKey("baseline_ancestor") -and -not [bool]$Plan.baseline_ancestor) {
        New-Failure "Guard refusal: reviewed behavior baseline is not an ancestor of HEAD"
    }
    if (-not [bool]$Plan.protected_runtime_paths_unchanged) {
        New-Failure "Guard refusal: protected runtime paths differ from the behavior baseline"
    }
    if (-not [bool]$Plan.python_env_clean) {
        New-Failure "Guard refusal: Python/HF override environment variables are set"
    }
    if (-not [bool]$Plan.python_resolved) {
        New-Failure "Guard refusal: repository Python could not be resolved"
    }
    if (-not [bool]$Plan.import_sources_match) {
        New-Failure "Guard refusal: repository import sources do not match"
    }
    if ($Plan.ContainsKey("calibration_root_matches_expected") -and -not [bool]$Plan.calibration_root_matches_expected) {
        New-Failure "Guard refusal: calibration root does not match the reviewed repository constant"
    }
}

function New-InitialState {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [string]$StatePathValue
    )

    $identities = Get-CurrentIdentities -Plan $Plan
    $runnerSha = Get-RunnerSha256
    $sessionId = if ($Plan.ContainsKey("session_id")) { $Plan.session_id } else { Get-SessionId }
    $utcStart = if ($Plan.ContainsKey("utc_start")) { $Plan.utc_start } else { [DateTime]::UtcNow.ToString("o") }
    return [ordered]@{
        schema_version   = $SchemaVersion
        runner_version   = $RunnerVersion
        packet_identity  = $PacketIdentity
        session_id       = $sessionId
        utc_start        = $utcStart
        behavior_sha     = $BehaviorBaseline
        repo_head        = $Plan.head
        expected_branch  = $Plan.expected_branch
        runner_sha       = $runnerSha
        state_path       = $StatePathValue
        ports            = [ordered]@{
            physical_left  = $ExpectedPorts.physical_left
            logical_left   = $ExpectedPorts.logical_left
            physical_right = $ExpectedPorts.physical_right
            logical_right  = $ExpectedPorts.logical_right
        }
        leader_id        = $ExpectedLeaderId
        arm_profile      = $ExpectedProfile
        classification   = "ORIGINAL_CALIBRATION_INTACT"
        completed_stages = @()
        failed_stages    = @()
        summaries        = [ordered]@{}
        final_result     = $null
        next_stage       = "Calibrate"
        stages           = [ordered]@{
            Calibrate = [ordered]@{
                result = "pending"
                native = [ordered]@{
                    attempted      = $false
                    launched       = $false
                    real_exit_code = $null
                    executable     = $null
                    arguments      = @()
                }
            }
            MapLeft = [ordered]@{
                result = "pending"
                native = [ordered]@{
                    attempted      = $false
                    launched       = $false
                    real_exit_code = $null
                    executable     = $null
                    arguments      = @()
                }
            }
            MapRight = [ordered]@{
                result = "pending"
                native = [ordered]@{
                    attempted      = $false
                    launched       = $false
                    real_exit_code = $null
                    executable     = $null
                    arguments      = @()
                }
            }
            Verify = [ordered]@{
                result = "pending"
                native = [ordered]@{
                    attempted      = $false
                    launched       = $false
                    real_exit_code = $null
                    executable     = $null
                    arguments      = @()
                }
            }
        }
        pre_calibration  = [ordered]@{
            left  = $identities.left
            right = $identities.right
        }
        post_calibration = $null
        artifacts        = [ordered]@{
            transcript = $null
            evidence   = $null
            map_left   = $null
            map_right  = $null
        }
    }
}

function Save-State {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [hashtable]$State
    )

    Write-JsonAtomic -Path $Path -Value $State -Overwrite
}

function Load-State {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        New-Failure "State file is required for stage $Stage"
    }
    return Read-JsonFile -Path $Path
}

function Assert-StateIdentity {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State
    )

    if ($State.runner_version -ne $RunnerVersion) {
        New-Failure "Runner version mismatch in state"
    }
    if ($State.packet_identity -ne $PacketIdentity) {
        New-Failure "Packet identity mismatch in state"
    }
    if ($State.behavior_sha -ne $BehaviorBaseline) {
        New-Failure "Behavior baseline mismatch in state"
    }
    if ($State.expected_branch -ne $ExpectedBranch) {
        New-Failure "State branch provenance mismatch"
    }
    if ($State.leader_id -ne $ExpectedLeaderId -or $State.arm_profile -ne $ExpectedProfile) {
        New-Failure "Persisted leader identity is invalid"
    }
    foreach ($name in $ExpectedPorts.Keys) {
        if ($State.ports[$name] -ne $ExpectedPorts[$name]) {
            New-Failure "Persisted port assignment is invalid"
        }
    }
}

function Get-StateValidationIssues {
    param(
        [hashtable]$State
    )

    $issues = [System.Collections.Generic.List[string]]::new()
    foreach ($name in @("runner_version", "packet_identity", "session_id", "state_path", "repo_head", "runner_sha", "behavior_sha", "expected_branch", "leader_id", "arm_profile", "ports", "stages", "artifacts", "completed_stages", "classification")) {
        if (-not $State.ContainsKey($name)) {
            $issues.Add("missing $name")
        }
    }
    return @($issues.ToArray())
}

function Assert-StateProvenance {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State,

        [Parameter(Mandatory = $true)]
        [string]$StatePathValue,

        [hashtable]$Plan
    )

    if ($State.repo_head -ne $Plan.head -or $State.state_path -ne $StatePathValue -or $State.runner_sha -ne (Get-RunnerSha256)) {
        New-Failure "State repository provenance is invalid"
    }
}

function Assert-EvidenceAndCalibrationStillMatch {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan
    )

    $evidence = $State.artifacts.evidence
    if ($null -eq $evidence) {
        New-Failure "Evidence is required before mapping"
    }
    if ((Get-Sha256Hex -Path $evidence.path) -ne $evidence.sha256) {
        New-Failure "Evidence hash mismatch"
    }
    $transcript = $State.artifacts.transcript
    if ($null -eq $transcript) {
        New-Failure "Transcript is required before mapping"
    }
    if ((Get-Sha256Hex -Path $transcript.path) -ne $transcript.sha256) {
        New-Failure "Transcript hash mismatch"
    }
    $current = Get-CurrentIdentities -Plan $Plan
    if ($current.left.sha256 -ne $State.post_calibration.left.sha256 -or $current.right.sha256 -ne $State.post_calibration.right.sha256) {
        New-Failure "Current calibration does not match evidence"
    }
}

function Invoke-SharedExecutor {
    param(
        [Parameter(Mandatory = $true)]
        [string]$StageName,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [hashtable]$State,

        [Parameter(Mandatory = $true)]
        [string]$StatePathValue,

        [string]$OutputPath,

        [string[]]$HeaderLines = @()
    )

    $stagePlan = $Plan.stage_plan[$StageName]
    if ($null -eq $stagePlan) {
        New-Failure "Missing test stage plan for $StageName"
    }
    $command = Build-StageCommand -StageName $StageName -Plan $Plan
    $State.stages[$StageName].native.executable = $command.executable
    $State.stages[$StageName].native.arguments = @($command.arguments)

    if (-not [bool]$Plan.is_test_mode) {
        $State.stages[$StageName].native.attempted = $true
        $State.stages[$StageName].native.launched = $false
        $State.stages[$StageName].native.real_exit_code = $null
        Save-State -Path $StatePathValue -State $State
        if ($OutputPath) {
            Assert-PathMissing -Path $OutputPath
            $headerText = ""
            if ($HeaderLines.Count -gt 0) {
                $headerText = ($HeaderLines -join [Environment]::NewLine) + [Environment]::NewLine
            }
            Write-TextAtomic -Path $OutputPath -Text $headerText
        }
        try {
            if ($OutputPath) {
                & $command.executable @($command.arguments) 2>&1 | Tee-Object -FilePath $OutputPath -Append | Out-Null
            }
            else {
                & $command.executable @($command.arguments)
            }
            $exitCode = if ($null -eq $LASTEXITCODE) { $null } else { [int]$LASTEXITCODE }
            $State.stages[$StageName].native.launched = $true
            $State.stages[$StageName].native.real_exit_code = $exitCode
            Save-State -Path $StatePathValue -State $State
            if ($null -eq $exitCode) {
                New-Failure "Native command returned no exit code"
            }
            if ($exitCode -ne 0) {
                New-Failure "$StageName native command failed with exit code $exitCode"
            }
            return $exitCode
        }
        catch {
            Save-State -Path $StatePathValue -State $State
            throw
        }
    }

    $preexistingLastExitCode = $null
    if ($stagePlan.ContainsKey("set_last_exit_code_before")) {
        $preexistingLastExitCode = $stagePlan.set_last_exit_code_before
        $global:LASTEXITCODE = [int]$preexistingLastExitCode
    }
    Remove-Variable -Name LASTEXITCODE -Scope Global -ErrorAction SilentlyContinue

    $launched = [bool]$stagePlan.launched
    $exitCode = $stagePlan.exit_code
    $State.stages[$StageName].native.attempted = $true
    $State.stages[$StageName].native.launched = $launched
    $State.stages[$StageName].native.real_exit_code = if ($launched) { $exitCode } else { $null }
    Save-State -Path $StatePathValue -State $State

    if (-not $launched) {
        if ($preexistingLastExitCode -eq 0) {
            New-Failure "Native command did not launch; stale LASTEXITCODE=0 was ignored"
        }
        New-Failure "Native command did not launch"
    }
    if ($null -eq $exitCode) {
        New-Failure "Native command returned no exit code"
    }
    if ([int]$exitCode -ne 0) {
        New-Failure "$StageName native command failed with exit code $exitCode"
    }
    return [int]$exitCode
}

function New-MapLogText {
    param(
        [Parameter(Mandatory = $true)]
        [string]$StageName,

        [Parameter(Mandatory = $true)]
        [hashtable]$State,

        [Parameter(Mandatory = $true)]
        [string]$PhysicalSide
    )

    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add("RUN_MARKER=$StageName")
    $lines.Add("STATE_SHA256=$($State.state_reference_sha256)")
    $lines.Add("EVIDENCE_SHA256=$($State.artifacts.evidence.sha256)")
    $lines.Add("NO_ROBOT_PROOF=1")
    $lines.Add("CLEANUP_PROOF=1")
    for ($index = 0; $index -lt 60; $index++) {
        $pairs = [System.Collections.Generic.List[string]]::new()
        foreach ($key in $ExpectedMapKeys) {
            $value = Get-SampleValue -PhysicalSide $PhysicalSide -SampleIndex $index -Key $key
            $pairs.Add(("{0}={1:N1}" -f $key, $value))
        }
        $lines.Add(("SAMPLE {0:D2} {1}" -f $index, ($pairs -join " ")))
    }
    $lines.Add("CLIENT_EXIT_CODE=0")
    return ($lines -join [Environment]::NewLine) + [Environment]::NewLine
}

function Get-SampleValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PhysicalSide,

        [Parameter(Mandatory = $true)]
        [int]$SampleIndex,

        [Parameter(Mandatory = $true)]
        [string]$Key
    )

    $base = @{
        "arm_left_shoulder_pan.pos"   = 0.2
        "arm_left_shoulder_lift.pos"  = 0.4
        "arm_left_elbow_flex.pos"     = 0.6
        "arm_left_wrist_flex.pos"     = 0.8
        "arm_left_wrist_roll.pos"     = 1.0
        "arm_right_shoulder_pan.pos"  = 0.1
        "arm_right_shoulder_lift.pos" = 0.2
        "arm_right_elbow_flex.pos"    = 0.3
        "arm_right_wrist_flex.pos"    = 0.4
        "arm_right_wrist_roll.pos"    = 0.5
        "arm_left_gripper.pos"        = 5.0
        "arm_right_gripper.pos"       = 5.0
    }
    if ($PhysicalSide -eq "left") {
        $base["arm_left_gripper.pos"] = 5.0 + ($SampleIndex * 0.5)
        $base["arm_right_gripper.pos"] = 0.4 + (($SampleIndex % 2) * 0.2)
    }
    else {
        $base["arm_right_gripper.pos"] = 7.0 + ($SampleIndex * 0.5)
        $base["arm_left_gripper.pos"] = 0.6 + (($SampleIndex % 2) * 0.2)
    }
    return [double]$base[$Key]
}

function Parse-PythonActionPairs {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Line
    )

    $prefix = "[NO_ROBOT] action -> "
    if (-not $Line.StartsWith($prefix)) {
        New-Failure "Malformed no-robot action line"
    }
    $body = $Line.Substring($prefix.Length).Trim()
    if (-not ($body.StartsWith("{") -and $body.EndsWith("}"))) {
        New-Failure "Malformed no-robot action payload"
    }
    $inner = $body.Substring(1, $body.Length - 2)
    if (-not $inner) {
        return @()
    }
    $pairs = [System.Collections.Generic.List[object]]::new()
    foreach ($segment in ($inner -split ", ")) {
        if ($segment -notmatch "^'(?<key>[^']+)': (?<value>.+)$") {
            New-Failure "Malformed no-robot action token"
        }
        $pairs.Add([ordered]@{
            key   = $Matches.key
            value = $Matches.value
        })
    }
    return @($pairs)
}

function Validate-ActionPairs {
    param(
        [Parameter(Mandatory = $true)]
        [string]$StageName,

        [Parameter(Mandatory = $true)]
        [object[]]$ActionPairsCollection
    )

    if ($ActionPairsCollection.Count -ne 60) {
        New-Failure "Map log validation failed for ${StageName}: expected exactly 60 samples"
    }

    $ranges = @{}
    foreach ($key in $ExpectedMapKeys) {
        $ranges[$key] = [System.Collections.Generic.List[double]]::new()
    }

    foreach ($pairs in $ActionPairsCollection) {
        $seen = @{}
        foreach ($pair in $pairs) {
            $key = $pair.key
            if ($seen.ContainsKey($key)) {
                New-Failure "Map log validation failed for ${StageName}: duplicate key $key"
            }
            if ($ExpectedMapKeys -notcontains $key) {
                New-Failure "Map log validation failed for ${StageName}: unexpected key $key"
            }
            $value = 0.0
            if (-not [double]::TryParse([string]$pair.value, [ref]$value) -or [double]::IsNaN($value) -or [double]::IsInfinity($value)) {
                New-Failure "Map log validation failed for ${StageName}: nonnumeric value for $key"
            }
            $seen[$key] = $value
        }
        foreach ($expectedKey in $ExpectedMapKeys) {
            if (-not $seen.ContainsKey($expectedKey)) {
                New-Failure "Map log validation failed for ${StageName}: missing key $expectedKey"
            }
            $ranges[$expectedKey].Add([double]$seen[$expectedKey])
        }
    }

    $perKeySpan = @{}
    foreach ($key in $ExpectedMapKeys) {
        $measure = $ranges[$key] | Measure-Object -Maximum -Minimum
        $perKeySpan[$key] = [double]$measure.Maximum - [double]$measure.Minimum
    }

    $leftOppositeMax = @(
        $perKeySpan["arm_right_shoulder_pan.pos"],
        $perKeySpan["arm_right_shoulder_lift.pos"],
        $perKeySpan["arm_right_elbow_flex.pos"],
        $perKeySpan["arm_right_wrist_flex.pos"],
        $perKeySpan["arm_right_wrist_roll.pos"],
        $perKeySpan["arm_right_gripper.pos"]
    ) | Measure-Object -Maximum
    $rightOppositeMax = @(
        $perKeySpan["arm_left_shoulder_pan.pos"],
        $perKeySpan["arm_left_shoulder_lift.pos"],
        $perKeySpan["arm_left_elbow_flex.pos"],
        $perKeySpan["arm_left_wrist_flex.pos"],
        $perKeySpan["arm_left_wrist_roll.pos"],
        $perKeySpan["arm_left_gripper.pos"]
    ) | Measure-Object -Maximum

    if ($StageName -eq "MapLeft") {
        if ($perKeySpan["arm_left_gripper.pos"] -lt 20.0 -or [double]$leftOppositeMax.Maximum -ge 2.0) {
            New-Failure "Map log validation failed for ${StageName}: logical-left classification failed"
        }
    }
    else {
        if ($perKeySpan["arm_right_gripper.pos"] -lt 20.0 -or [double]$rightOppositeMax.Maximum -ge 2.0) {
            New-Failure "Map log validation failed for ${StageName}: logical-right classification failed"
        }
    }
}

function Validate-MapLog {
    param(
        [Parameter(Mandatory = $true)]
        [string]$StageName,

        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [hashtable]$State
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        New-Failure "Map log validation failed for ${StageName}: file is missing"
    }
    $content = Get-Content -Raw -LiteralPath $Path
    if ($content -match "(?i)\bzmq\b|running calibration of|calibration saved to|press enter to use provided calibration") {
        New-Failure "Map log validation failed for ${StageName}: runtime text is forbidden"
    }
    $lines = @($content -split "`r?`n" | Where-Object { $_ -ne "" })
    if ($lines.Count -lt 7) {
        New-Failure "Map log validation failed for ${StageName}: log is incomplete"
    }
    $expectedActualMarker = if ($StageName -eq "MapLeft") { "MAP_RUN=PHYSICAL_LEFT_ONLY" } else { "MAP_RUN=PHYSICAL_RIGHT_ONLY" }
    if ($lines[0].StartsWith("MAP_RUN=")) {
        if ($lines[0] -ne $expectedActualMarker) {
            New-Failure "Map log validation failed for ${StageName}: first marker mismatch"
        }
        if ($lines[-1] -ne "CLIENT_EXIT_CODE=0") {
            New-Failure "Map log validation failed for ${StageName}: success terminator mismatch"
        }
        if ((@($lines | Where-Object { $_ -eq $ActualNoRobotProof })).Count -ne 1) {
            New-Failure "Map log validation failed for ${StageName}: no-robot proof count mismatch"
        }
        if ((@($lines | Where-Object { $_ -like "$ActualCleanupPrefix*" })).Count -ne 1) {
            New-Failure "Map log validation failed for ${StageName}: cleanup proof count mismatch"
        }
        $stateLine = @($lines | Where-Object { $_ -like "STATE_SHA256=*" })
        $evidenceLine = @($lines | Where-Object { $_ -like "EVIDENCE_SHA256=*" })
        if ($stateLine.Count -ne 1 -or $evidenceLine.Count -ne 1) {
            New-Failure "Map log validation failed for ${StageName}: state/evidence metadata is incomplete"
        }
        $artifactKey = if ($StageName -eq "MapLeft") { "map_left" } else { "map_right" }
        $artifact = $State.artifacts[$artifactKey]
        $expectedStateHash = $null
        if ($null -ne $artifact -and ($artifact.Keys -contains "expected_state_sha256")) {
            $expectedStateHash = $artifact.expected_state_sha256
        }
        if ($expectedStateHash -and $stateLine[0].Substring(13) -ne $expectedStateHash) {
            New-Failure "Map log validation failed for ${StageName}: state hash mismatch"
        }
        if ($evidenceLine[0].Substring(16) -ne $State.artifacts.evidence.sha256) {
            New-Failure "Map log validation failed for ${StageName}: evidence hash mismatch"
        }
        $actionLines = @($lines | Where-Object { $_.StartsWith("[NO_ROBOT] action -> ") })
        $collection = [System.Collections.Generic.List[object]]::new()
        foreach ($actionLine in $actionLines) {
            $collection.Add((Parse-PythonActionPairs -Line $actionLine))
        }
        Validate-ActionPairs -StageName $StageName -ActionPairsCollection @($collection)
        return
    }
    if ($lines[0] -ne "RUN_MARKER=$StageName") {
        New-Failure "Map log validation failed for ${StageName}: first marker mismatch"
    }
    if ($lines[-1] -ne "CLIENT_EXIT_CODE=0") {
        New-Failure "Map log validation failed for ${StageName}: success terminator mismatch"
    }
    if ((@($lines | Where-Object { $_ -eq "NO_ROBOT_PROOF=1" })).Count -ne 1) {
        New-Failure "Map log validation failed for ${StageName}: no-robot proof count mismatch"
    }
    if ((@($lines | Where-Object { $_ -eq "CLEANUP_PROOF=1" })).Count -ne 1) {
        New-Failure "Map log validation failed for ${StageName}: cleanup proof count mismatch"
    }
    $stateLine = @($lines | Where-Object { $_ -like "STATE_SHA256=*" })
    $evidenceLine = @($lines | Where-Object { $_ -like "EVIDENCE_SHA256=*" })
    if ($stateLine.Count -ne 1 -or $evidenceLine.Count -ne 1) {
        New-Failure "Map log validation failed for ${StageName}: state/evidence metadata is incomplete"
    }
    $artifactKey = if ($StageName -eq "MapLeft") { "map_left" } else { "map_right" }
    $artifact = $State.artifacts[$artifactKey]
    $expectedStateHash = $null
    if ($null -ne $artifact -and ($artifact.Keys -contains "expected_state_sha256")) {
        $expectedStateHash = $artifact.expected_state_sha256
    }
    if ($expectedStateHash -and $stateLine[0].Substring(13) -ne $expectedStateHash) {
        New-Failure "Map log validation failed for ${StageName}: state hash mismatch"
    }
    if ($evidenceLine[0].Substring(16) -ne $State.artifacts.evidence.sha256) {
        New-Failure "Map log validation failed for ${StageName}: evidence hash mismatch"
    }

    $samples = @($lines | Where-Object { $_ -like "SAMPLE *" })
    $collection = [System.Collections.Generic.List[object]]::new()
    foreach ($sample in $samples) {
        $tokens = @($sample.Split(" ", [System.StringSplitOptions]::RemoveEmptyEntries))
        if ($tokens.Count -ne 14) {
            New-Failure "Map log validation failed for ${StageName}: sample field count mismatch"
        }
        $pairs = [System.Collections.Generic.List[object]]::new()
        for ($index = 2; $index -lt $tokens.Count; $index++) {
            $pair = $tokens[$index].Split("=", 2)
            if ($pair.Count -ne 2) {
                New-Failure "Map log validation failed for ${StageName}: malformed sample token"
            }
            $pairs.Add([ordered]@{
                key   = $pair[0]
                value = $pair[1]
            })
        }
        $collection.Add(@($pairs))
    }
    Validate-ActionPairs -StageName $StageName -ActionPairsCollection @($collection)
}

function Update-StateForFailure {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State,

        [Parameter(Mandatory = $true)]
        [string]$StageName,

        [Parameter(Mandatory = $true)]
        [string]$StatePathValue,

        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    $State.stages[$StageName].result = "failed"
    if ($State.failed_stages -notcontains $StageName) {
        $State.failed_stages = @($State.failed_stages + $StageName)
    }
    $State.summaries[$StageName] = $Message
    Save-State -Path $StatePathValue -State $State
}

function Invoke-CalibrateStage {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [string]$StatePathValue
    )

    Assert-RepoAndEnvGuards -Plan $Plan
    Assert-ManifestAndBackups -Plan $Plan
    Assert-TestModePath -Plan $Plan -Path $StatePathValue
    if (Test-Path -LiteralPath $StatePathValue) {
        New-Failure "Calibrate refuses when the state path already exists"
    }
    $state = New-InitialState -Plan $Plan -StatePathValue $StatePathValue
    Save-State -Path $StatePathValue -State $state
    try {
        $transcriptPath = $Plan.stage_plan.Calibrate.transcript_path
        $evidencePath = $Plan.stage_plan.Calibrate.evidence_path
        Assert-TestModePath -Plan $Plan -Path $StatePathValue
        Assert-TestModePath -Plan $Plan -Path $transcriptPath
        Assert-TestModePath -Plan $Plan -Path $evidencePath
        Assert-PathMissing -Path $transcriptPath
        Assert-PathMissing -Path $evidencePath
        $command = Build-StageCommand -StageName "Calibrate" -Plan $Plan
        $state.stages.Calibrate.native.executable = $command.executable
        $state.stages.Calibrate.native.arguments = @($command.arguments)
        Save-State -Path $StatePathValue -State $state
        if ([bool]$Plan.is_test_mode) {
            Invoke-SharedExecutor -StageName "Calibrate" -Plan $Plan -State $state -StatePathValue $StatePathValue
            Write-TextAtomic -Path $transcriptPath -Text $Plan.stage_plan.Calibrate.transcript_text
            Write-JsonAtomic -Path $Plan.calibration.left.path -Value $Plan.stage_plan.Calibrate.post_calibration.left -Overwrite
            Write-JsonAtomic -Path $Plan.calibration.right.path -Value $Plan.stage_plan.Calibrate.post_calibration.right -Overwrite
            Write-TextAtomic -Path $evidencePath -Text $Plan.stage_plan.Calibrate.evidence_text
        }
        else {
            $headerLines = @(
                "SESSION_ID=$($state.session_id)",
                "UTC_START=$($state.utc_start)",
                "BEHAVIOR_SHA=$BehaviorBaseline",
                "CALIBRATION_EXECUTABLE=$($command.executable)",
                "CALIBRATION_ARGUMENTS=$((ConvertTo-CanonicalJson -Value @($command.arguments)))"
            )
            Invoke-SharedExecutor -StageName "Calibrate" -Plan $Plan -State $state -StatePathValue $StatePathValue -OutputPath $transcriptPath -HeaderLines $headerLines
            Append-TextLine -Path $transcriptPath -Text "CALIBRATION_EXIT_CODE=0"
        }
        $stateReferenceSha = Get-Sha256Hex -Path $StatePathValue
        $postIdentities = Get-CurrentIdentities -Plan $Plan
        $state.post_calibration = [ordered]@{
            left  = $postIdentities.left
            right = $postIdentities.right
        }
        $state.classification = "VALID_FRESH_CALIBRATION"
        $state.completed_stages = @("Calibrate")
        $state.next_stage = "MapLeft"
        $state.state_reference_sha256 = $stateReferenceSha
        $state.artifacts.transcript = Get-FileInfoSnapshot -Path $transcriptPath
        if (-not [bool]$Plan.is_test_mode) {
            $evidencePayload = [ordered]@{
                classification            = "VALID_FRESH_CALIBRATION"
                session_id                = $state.session_id
                utc_start                 = $state.utc_start
                behavior_sha              = $BehaviorBaseline
                calibration_executable    = $command.executable
                calibration_arguments     = @($command.arguments)
                transcript_path           = $transcriptPath
                transcript_sha256         = Get-Sha256Hex -Path $transcriptPath
                pre_calibration           = $state.pre_calibration
                post_calibration          = $state.post_calibration
                left_sha256               = $state.post_calibration.left.sha256
                right_sha256              = $state.post_calibration.right.sha256
            }
            Write-JsonAtomic -Path $evidencePath -Value $evidencePayload
        }
        $state.artifacts.evidence = Get-FileInfoSnapshot -Path $evidencePath
        $state.stages.Calibrate.result = "completed"
        $state.summaries.Calibrate = "Calibration completed"
        Save-State -Path $StatePathValue -State $state
        return
    }
    catch {
        Update-StateForFailure -State $state -StageName "Calibrate" -StatePathValue $StatePathValue -Message $_.Exception.Message
        throw
    }
}

function Invoke-MapStage {
    param(
        [Parameter(Mandatory = $true)]
        [string]$StageName,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [string]$StatePathValue
    )

    $state = Load-State -Path $StatePathValue
    $issues = @(Get-StateValidationIssues -State $state)
    if ($issues.Count -gt 0) {
        New-Failure ("INVALID_OR_UNCERTAIN_STATE: " + ($issues -join ", "))
    }
    Assert-StateIdentity -State $state
    Assert-StateProvenance -State $state -StatePathValue $StatePathValue -Plan $Plan
    Assert-RepoAndEnvGuards -Plan $Plan
    Assert-ManifestAndBackups -Plan $Plan
    if ($StageName -eq "MapLeft" -and ($state.completed_stages -notcontains "Calibrate")) {
        New-Failure "Calibrate must complete before MapLeft"
    }
    if ($StageName -eq "MapRight" -and ($state.completed_stages -notcontains "MapLeft")) {
        New-Failure "MapLeft must complete before MapRight"
    }
    Assert-EvidenceAndCalibrationStillMatch -State $state -Plan $Plan
    try {
        $mapPath = $Plan.stage_plan[$StageName].map_path
        Assert-TestModePath -Plan $Plan -Path $mapPath
        Assert-PathMissing -Path $mapPath
        $command = Build-StageCommand -StageName $StageName -Plan $Plan
        $state.stages[$StageName].native.executable = $command.executable
        $state.stages[$StageName].native.arguments = @($command.arguments)
        Save-State -Path $StatePathValue -State $state
        if ([bool]$Plan.is_test_mode) {
            Invoke-SharedExecutor -StageName $StageName -Plan $Plan -State $state -StatePathValue $StatePathValue
            $mapText = New-MapLogText -StageName $StageName -State $state -PhysicalSide $Plan.stage_plan[$StageName].physical_side
            Write-TextAtomic -Path $mapPath -Text $mapText
        }
        else {
            $marker = if ($StageName -eq "MapLeft") { "PHYSICAL_LEFT_ONLY" } else { "PHYSICAL_RIGHT_ONLY" }
            $headerLines = @(
                "MAP_RUN=$marker",
                "SESSION_ID=$($state.session_id)",
                "UTC_START=$($state.utc_start)",
                "STATE_SHA256=$($state.state_reference_sha256)",
                "EVIDENCE_SHA256=$($state.artifacts.evidence.sha256)",
                "TRANSCRIPT_PATH=$($state.artifacts.transcript.path)"
            )
            Invoke-SharedExecutor -StageName $StageName -Plan $Plan -State $state -StatePathValue $StatePathValue -OutputPath $mapPath -HeaderLines $headerLines
            Append-TextLine -Path $mapPath -Text "CLIENT_EXIT_CODE=0"
        }
        $mapArtifactKey = if ($StageName -eq "MapLeft") { "map_left" } else { "map_right" }
        $state.artifacts[$mapArtifactKey] = [ordered]@{
            path                  = $mapPath
            sha256                = Get-Sha256Hex -Path $mapPath
            expected_state_sha256 = $state.state_reference_sha256
        }
        Validate-MapLog -StageName $StageName -Path $mapPath -State $state
        $state.stages[$StageName].result = "completed"
        if ($state.completed_stages -notcontains $StageName) {
            $state.completed_stages = @($state.completed_stages + $StageName)
        }
        $state.summaries[$StageName] = "$StageName completed"
        $state.next_stage = if ($StageName -eq "MapLeft") { "MapRight" } else { "Verify" }
        Save-State -Path $StatePathValue -State $state
        return
    }
    catch {
        Update-StateForFailure -State $state -StageName $StageName -StatePathValue $StatePathValue -Message $_.Exception.Message
        throw
    }
}

function Invoke-VerifyStage {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [string]$StatePathValue
    )

    $state = Load-State -Path $StatePathValue
    $issues = @(Get-StateValidationIssues -State $state)
    if ($issues.Count -gt 0) {
        New-Failure ("INVALID_OR_UNCERTAIN_STATE: " + ($issues -join ", "))
    }
    Assert-StateIdentity -State $state
    Assert-StateProvenance -State $state -StatePathValue $StatePathValue -Plan $Plan
    Assert-EvidenceAndCalibrationStillMatch -State $state -Plan $Plan
    if ($null -eq $state.artifacts.map_left -or $null -eq $state.artifacts.map_right) {
        New-Failure "Verify requires both map artifacts"
    }
    if ((Get-Sha256Hex -Path $state.artifacts.map_left.path) -ne $state.artifacts.map_left.sha256) {
        New-Failure "Map log validation failed for MapLeft: stored hash mismatch"
    }
    if ((Get-Sha256Hex -Path $state.artifacts.map_right.path) -ne $state.artifacts.map_right.sha256) {
        New-Failure "Map log validation failed for MapRight: stored hash mismatch"
    }
    Validate-MapLog -StageName "MapLeft" -Path $state.artifacts.map_left.path -State $state
    Validate-MapLog -StageName "MapRight" -Path $state.artifacts.map_right.path -State $state
    $state.stages.Verify.result = "completed"
    if ($state.completed_stages -notcontains "Verify") {
        $state.completed_stages = @($state.completed_stages + "Verify")
    }
    $state.final_result = "MAPPING_RESULT=CORRECT"
    $state.next_stage = $null
    $state.summaries.Verify = "Mapping verified"
    Save-State -Path $StatePathValue -State $state
    [Console]::Out.WriteLine("MAPPING_RESULT=CORRECT")
}

function Get-StatusPayload {
    param(
        [hashtable]$Plan,
        [string]$StatePathValue
    )

    if (-not (Test-Path -LiteralPath $StatePathValue -PathType Leaf)) {
        $current = Get-CurrentIdentities -Plan $Plan
        $leftBackup = Get-Sha256Hex -Path $Plan.calibration.left.backup_path
        $rightBackup = Get-Sha256Hex -Path $Plan.calibration.right.backup_path
        if ($current.left.sha256 -eq $leftBackup -and $current.right.sha256 -eq $rightBackup) {
            return [ordered]@{
                classification = "ORIGINAL_CALIBRATION_INTACT"
                next_stage     = "Calibrate"
            }
        }
        return [ordered]@{
            classification = "ORPHANED_FRESH_CALIBRATION"
            next_stage     = "Calibrate"
            report         = "dry-run-only recovery plan: preserve orphaned files, then restore immutable originals only under later exact reviewed authorization"
        }
    }
    $state = Read-JsonFile -Path $StatePathValue
    $issues = @(Get-StateValidationIssues -State $state)
    if ($issues.Count -gt 0) {
        return [ordered]@{
            classification = "INVALID_OR_UNCERTAIN_STATE"
            next_stage     = $null
            report         = ($issues -join ", ")
        }
    }
    return [ordered]@{
        classification = $state.classification
        next_stage     = $state.next_stage
        final_result   = $state.final_result
    }
}

try {
    Require-Confirmation -StageName $Stage -ConfirmValue $Confirm
    $plan = Get-ExecutionPlan
    if ($Stage -eq "Status") {
        $payload = Get-StatusPayload -Plan $plan -StatePathValue $StatePath
        [Console]::Out.WriteLine((ConvertTo-CanonicalJson -Value $payload))
        exit 0
    }
    switch ($Stage) {
        "Calibrate" { Invoke-CalibrateStage -Plan $plan -StatePathValue $StatePath }
        "MapLeft" { Invoke-MapStage -StageName "MapLeft" -Plan $plan -StatePathValue $StatePath }
        "MapRight" { Invoke-MapStage -StageName "MapRight" -Plan $plan -StatePathValue $StatePath }
        "Verify" { Invoke-VerifyStage -Plan $plan -StatePathValue $StatePath }
        default { New-Failure "Unhandled stage $Stage" }
    }
    exit 0
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
