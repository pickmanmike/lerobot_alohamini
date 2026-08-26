[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Status", "DiagnoseImports", "RestartCalibration", "RecoverInterruptedCalibration", "CheckLeaderBuses", "Calibrate", "MapLeft", "MapRight", "Verify")]
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
$LegacyRestartRepoHead = "edc14bbbebb173061cf3b04ead08ffa9fcb81051"
$LegacyRestartRunnerSha256 = "0BDBDB2F20AD9D47A2B3DBF84924B833E822FE733EA33FAD505753BAD0BE336E"
$LegacyRestartSessionId = "a9128060-c60c-4582-8cb8-cf45fc1750e6"
$LegacyRestartStateSha256 = "0DB0BE72CF57C570D7064D272779374040075507A560B86AE5B31B61186935BD"
$LegacyRestartStateSize = [int64]9028
$LegacyRestartFreshLeftSha256 = "3E3896F0C4B49344FA896DFCD430C7EAB8B04B7ED457E8046689C821EA7BFA88"
$LegacyRestartFreshLeftSize = [int64]963
$LegacyRestartFreshLeftMtimeUtc = "2026-08-24T03:52:27.1823938Z"
$LegacyRestartFreshRightSha256 = "D7D948AD2FFCAA60C6490EAC8631E7ABC6410C7584BCD00EFBDC64839F710119"
$LegacyRestartFreshRightSize = [int64]962
$LegacyRestartFreshRightMtimeUtc = "2026-08-24T03:53:39.0485589Z"
$LegacyRestartEvidenceSha256 = "01484B85820A0674988A88788DD2C8A941092B6BEE8B1BD2A61C0038E071567C"
$LegacyRestartEvidenceSize = [int64]9749
$LegacyRestartTranscriptSha256 = "CB4FF5FD33756D47A6864F2B4DD55D5129D9E22D7DAF86E1C31D2FBA93E2ED05"
$LegacyRestartTranscriptSize = [int64]1422
$RestartRejectionReason = "OPERATOR_REJECTED_INCOMPLETE_RANGE"
$InterruptedRecoveryReason = "INTERRUPTED_CALIBRATION_RIGHT_BUS_DISCONNECT"
$InterruptedSessionId = "897f00dc-2608-4790-a74b-1482220eb5ed"
$InterruptedSessionStartUtc = "2026-08-25T00:38:21.8362906Z"
$InterruptedRepoHead = "a9891f84f244be54a1c4ffdeba4c475e0c1d851f"
$InterruptedRunnerSha256 = "CFFFFB7D421BA8E524D156981A24D45462DFA7F6CD45EE4D95CD9FDD68AC7B42"
$InterruptedStateSha256 = "0371650B298B46B8B724A8425E7D4628AF88F6125F967FEF1ED84091E6E9D7C5"
$InterruptedStateSize = [int64]6110
$InterruptedTranscriptSha256 = "6BA8699C55BED9074EFBBD18637CEB8FCD337CD70C84629C0C6036BE32768447"
$InterruptedTranscriptSize = [int64]1397
$InterruptedTranscriptMtimeUtc = "2026-08-25T00:40:37.3179751Z"
$InterruptedActiveLeftSha256 = "2B3C2245CAFCA67BBDA25FF0A868A158E6DDCF2162C2A5D5782220EF9DACF50D"
$InterruptedActiveLeftSize = [int64]961
$InterruptedActiveLeftMtimeUtc = "2026-08-25T00:39:56.5269224Z"
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
$ExpectedBodyKeys = @(
    "x.vel",
    "y.vel",
    "theta.vel",
    "lift_axis.vel"
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
$RealRejectedArchiveRoot = "C:\Users\pickm\AlohaMini1Backups"
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
$ReviewedImportSources = @(
    [ordered]@{ module = "lerobot"; relative_path = "src\lerobot\__init__.py" },
    [ordered]@{ module = "calibrate_bi"; relative_path = "examples\alohamini\calibrate_bi.py" },
    [ordered]@{ module = "teleoperate_bi"; relative_path = "examples\alohamini\teleoperate_bi.py" },
    [ordered]@{ module = "leader_client_utils"; relative_path = "examples\alohamini\leader_client_utils.py" },
    [ordered]@{ module = "lerobot.teleoperators.bi_so_leader.bi_so_leader"; relative_path = "src\lerobot\teleoperators\bi_so_leader\bi_so_leader.py" },
    [ordered]@{ module = "lerobot.teleoperators.so_leader.so_leader"; relative_path = "src\lerobot\teleoperators\so_leader\so_leader.py" }
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

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $FilePath
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    foreach ($argument in $Arguments) {
        [void]$startInfo.ArgumentList.Add([string]$argument)
    }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) {
            throw "Failed to start external command: $FilePath"
        }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $stdoutText = $stdoutTask.GetAwaiter().GetResult()
        $stderrText = $stderrTask.GetAwaiter().GetResult()
        $stdoutLines = if ([string]::IsNullOrEmpty($stdoutText)) { @() } else { @($stdoutText.TrimEnd([char[]]"`r`n") -split "`r?`n") }
        $stderrLines = if ([string]::IsNullOrEmpty($stderrText)) { @() } else { @($stderrText.TrimEnd([char[]]"`r`n") -split "`r?`n") }
        return [ordered]@{
            exit_code = [int]$process.ExitCode
            stdout    = @($stdoutLines)
            stderr    = @($stderrLines)
        }
    }
    finally {
        $process.Dispose()
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

function ConvertTo-CompactJson {
    param(
        [Parameter(Mandatory = $true)]
        $Value
    )

    return ($Value | ConvertTo-Json -Depth 100 -Compress)
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
    return (Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json -AsHashtable -Depth 100 -DateKind String)
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
        "RestartCalibration" { "RECALIBRATE" }
        "RecoverInterruptedCalibration" { "RECOVER" }
        "CheckLeaderBuses" { "CHECK" }
        default { $null }
    }
    if ($null -ne $expected -and $ConfirmValue -cne $expected) {
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
    $sessionId = if ($plan.ContainsKey("session_id")) { [string]$plan.session_id } else { $null }
    Assert-TestModeMutablePaths -Plan $plan -StatePathValue $StatePath -SessionId $sessionId
    return $plan
}

function Get-CanonicalPathEvidence {
    param(
        $PathValue
    )

    if ($null -eq $PathValue -or [string]::IsNullOrWhiteSpace([string]$PathValue)) {
        return [ordered]@{ input = $PathValue; canonical = $null; valid = $false; exists = $false; reason = "source path is missing" }
    }
    $pathText = [string]$PathValue
    try {
        $fullPath = [System.IO.Path]::GetFullPath($pathText)
        if (-not [System.IO.Path]::IsPathFullyQualified($pathText)) {
            return [ordered]@{ input = $pathText; canonical = $null; valid = $false; exists = $false; reason = "source path is not absolute" }
        }
    }
    catch {
        return [ordered]@{ input = $pathText; canonical = $null; valid = $false; exists = $false; reason = "source path is malformed" }
    }
    $exists = Test-Path -LiteralPath $fullPath -PathType Leaf
    $canonical = if ($exists) { (Resolve-Path -LiteralPath $fullPath).Path } else { $fullPath }
    return [ordered]@{ input = $pathText; canonical = $canonical; valid = $true; exists = $exists; reason = $null }
}

function Test-CanonicalPathWithin {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Candidate,

        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    $relative = [System.IO.Path]::GetRelativePath($Root, $Candidate)
    if ([System.IO.Path]::IsPathRooted($relative)) {
        return $false
    }
    $segments = @($relative -split '[\\/]')
    return ($segments.Count -eq 0 -or $segments[0] -cne "..")
}

function Invoke-ImportSourceProbe {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonPath
    )

    $namesJson = ConvertTo-Json -Compress -InputObject @($ReviewedImportSources | ForEach-Object { $_.module })
    $importCommand = @"
import importlib
import importlib.metadata
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path("examples/alohamini").resolve()))
names = json.loads(r'''$namesJson''')
modules = []
for name in names:
    try:
        module = importlib.import_module(name)
        modules.append({"name": name, "path": getattr(module, "__file__", None), "error": None})
    except BaseException as exc:
        modules.append({"name": name, "path": None, "error": f"{type(exc).__name__}: {exc}"})

direct_url = {"path": None, "content": None, "error": None}
pth_files = []
try:
    distribution = importlib.metadata.distribution("lerobot")
    metadata_path = pathlib.Path(distribution._path)
    direct_url_path = metadata_path / "direct_url.json"
    direct_url["path"] = str(direct_url_path)
    direct_url["content"] = json.loads(direct_url_path.read_text(encoding="utf-8"))
except BaseException as exc:
    direct_url["error"] = f"{type(exc).__name__}: {exc}"

for entry in sys.path:
    if not entry:
        continue
    site_path = pathlib.Path(entry)
    if site_path.name.lower() not in {"site-packages", "dist-packages"} or not site_path.is_dir():
        continue
    for pth_path in sorted(site_path.glob("*.pth")):
        try:
            content = pth_path.read_text(encoding="utf-8")
        except BaseException as exc:
            content = None
            error = f"{type(exc).__name__}: {exc}"
        else:
            error = None
        if "lerobot" in pth_path.name.lower() or (content is not None and "lerobot" in content.lower()):
            pth_files.append({"path": str(pth_path), "content": content, "error": error})

payload = {
    "repository_root": str(pathlib.Path.cwd()),
    "cwd": os.getcwd(),
    "python_executable": sys.executable,
    "sys_executable": sys.executable,
    "sys_prefix": sys.prefix,
    "sys_base_prefix": sys.base_prefix,
    "pythonpath": os.environ.get("PYTHONPATH"),
    "sys_path": sys.path,
    "direct_url": direct_url,
    "pth_files": pth_files,
    "modules": modules,
}
print(json.dumps(payload, separators=(",", ":")))
"@
    $result = Invoke-ExternalCommand -FilePath $PythonPath -Arguments @("-c", $importCommand) -WorkingDirectory $RepositoryRoot
    $probe = [ordered]@{
        exit_code = $result.exit_code
        stderr    = @($result.stderr)
    }
    if ($result.exit_code -ne 0) {
        $probe.probe_error = "Python import probe exited with status $($result.exit_code)"
        return $probe
    }
    if ($result.stdout.Count -ne 1) {
        $probe.probe_error = "Python import probe returned $($result.stdout.Count) output lines instead of one JSON document"
        return $probe
    }
    try {
        $payload = [string]$result.stdout[0] | ConvertFrom-Json -AsHashtable -Depth 100 -DateKind String
    }
    catch {
        $probe.probe_error = "Python import probe returned malformed JSON"
        return $probe
    }
    foreach ($key in $payload.Keys) {
        $probe[$key] = $payload[$key]
    }
    return $probe
}

function Get-ImportSourceDiagnostic {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonPath,

        [Parameter(Mandatory = $true)]
        [bool]$PythonResolved,

        [Parameter(Mandatory = $true)]
        [bool]$PythonEnvClean,

        [string[]]$OverrideEnvironmentNames = @(),

        [Parameter(Mandatory = $true)]
        [hashtable]$Probe
    )

    $failures = [System.Collections.Generic.List[string]]::new()
    $expectedPython = Get-CanonicalPathEvidence -PathValue (Join-Path $RepositoryRoot ".venv\Scripts\python.exe")
    $expectedPrefix = [System.IO.Path]::GetFullPath((Join-Path $RepositoryRoot ".venv"))
    $repositoryCanonical = [System.IO.Path]::GetFullPath($RepositoryRoot)
    $expectedSrc = [System.IO.Path]::GetFullPath((Join-Path $RepositoryRoot "src"))

    if (-not $PythonEnvClean) {
        $names = if ($OverrideEnvironmentNames.Count -gt 0) { $OverrideEnvironmentNames -join ", " } else { "one or more guarded Python/HF variables" }
        $failures.Add("override environment variables are set: $names")
    }
    if (-not $PythonResolved) {
        $failures.Add("repository Python could not be resolved")
    }
    $runnerPython = Get-CanonicalPathEvidence -PathValue $PythonPath
    if (-not $runnerPython.valid -or -not $runnerPython.exists -or -not $runnerPython.canonical.Equals($expectedPython.canonical, [System.StringComparison]::OrdinalIgnoreCase)) {
        $failures.Add("runner Python is not the exact repository virtual-environment executable")
    }
    if ($Probe.ContainsKey("probe_error")) {
        $failures.Add([string]$Probe.probe_error)
    }
    if ($Probe.ContainsKey("stderr") -and @($Probe.stderr).Count -gt 0) {
        $failures.Add("Python import probe wrote to stderr")
    }

    foreach ($field in @(
        [ordered]@{ name = "current working directory"; key = "cwd"; expected = $repositoryCanonical },
        [ordered]@{ name = "sys.executable"; key = "sys_executable"; expected = $expectedPython.canonical },
        [ordered]@{ name = "sys.prefix"; key = "sys_prefix"; expected = $expectedPrefix }
    )) {
        $actualValue = if ($Probe.ContainsKey($field.key)) { $Probe[$field.key] } else { $null }
        $actualEvidence = Get-CanonicalPathEvidence -PathValue $actualValue
        if (-not $actualEvidence.valid -or -not $actualEvidence.canonical.Equals($field.expected, [System.StringComparison]::OrdinalIgnoreCase)) {
            $failures.Add("$($field.name) is not the intended repository value")
        }
    }

    $moduleDiagnostics = @()
    $probeModules = @()
    if (-not $Probe.ContainsKey("modules") -or $Probe.modules -is [string] -or $Probe.modules -isnot [System.Collections.IList]) {
        $failures.Add("module records are malformed")
    }
    else {
        $malformedModuleRecord = $false
        foreach ($record in @($Probe.modules)) {
            if ($record -isnot [System.Collections.IDictionary] -or -not $record.Contains("name") -or -not $record.Contains("path") -or -not $record.Contains("error")) {
                $malformedModuleRecord = $true
                continue
            }
            $probeModules += $record
        }
        if ($malformedModuleRecord) {
            $failures.Add("module records are malformed")
        }
    }
    $expectedModuleNames = @($ReviewedImportSources | ForEach-Object { $_.module })
    $unexpectedModuleNames = @($probeModules | Where-Object { $expectedModuleNames -cnotcontains $_.name } | ForEach-Object { $_.name })
    if ($unexpectedModuleNames.Count -gt 0) {
        $failures.Add("Python import probe returned unexpected module records: $($unexpectedModuleNames -join ', ')")
    }
    foreach ($source in $ReviewedImportSources) {
        $expectedEvidence = Get-CanonicalPathEvidence -PathValue (Join-Path $RepositoryRoot $source.relative_path)
        $actualRecord = @($probeModules | Where-Object { $_.name -ceq $source.module })
        $actualPath = if ($actualRecord.Count -eq 1) { $actualRecord[0].path } else { $null }
        $actualEvidence = Get-CanonicalPathEvidence -PathValue $actualPath
        $reason = $null
        $belongs = $false
        $matches = $false
        if ($actualRecord.Count -gt 1) {
            $reason = "module probe returned duplicate source records"
        }
        elseif (-not $actualEvidence.valid) {
            $reason = if ($actualEvidence.reason -eq "source path is missing" -and $actualRecord.Count -eq 1 -and -not [string]::IsNullOrEmpty([string]$actualRecord[0].error)) { "module import failed: $($actualRecord[0].error)" } else { $actualEvidence.reason }
        }
        else {
            $belongs = Test-CanonicalPathWithin -Candidate $actualEvidence.canonical -Root $repositoryCanonical
            if (-not $belongs) {
                $reason = "source is outside the intended repository"
            }
            elseif (-not $actualEvidence.exists) {
                $reason = "source file does not exist"
            }
            elseif (-not $actualEvidence.canonical.Equals($expectedEvidence.canonical, [System.StringComparison]::OrdinalIgnoreCase)) {
                $reason = "source does not equal the reviewed repository file"
            }
            else {
                $matches = $true
            }
        }
        if (-not $matches) {
            $failures.Add("module $($source.module) expected '$($expectedEvidence.canonical)' but resolved '$($actualEvidence.canonical)': $reason")
        }
        $moduleDiagnostics += [ordered]@{
            module             = $source.module
            expected           = (Join-Path $RepositoryRoot $source.relative_path)
            actual             = $actualPath
            expected_canonical = $expectedEvidence.canonical
            actual_canonical   = $actualEvidence.canonical
            belongs_to_repository = $belongs
            matches            = $matches
            reason             = $reason
        }
    }

    $directUrl = @{}
    if ($Probe.ContainsKey("direct_url") -and $Probe.direct_url -is [System.Collections.IDictionary]) {
        $directUrl = $Probe.direct_url
    }
    else {
        $failures.Add("direct_url metadata is malformed")
    }
    $directUrlPath = if ($directUrl.ContainsKey("path")) { $directUrl.path } else { $null }
    $directUrlContent = if ($directUrl.ContainsKey("content")) { $directUrl.content } else { $null }
    $directUrlMatches = $false
    $directUrlReason = $null
    try {
        if ($directUrlContent -isnot [System.Collections.IDictionary] -or
            -not $directUrlContent.Contains("dir_info") -or
            $directUrlContent.dir_info -isnot [System.Collections.IDictionary] -or
            -not $directUrlContent.dir_info.Contains("editable") -or
            -not $directUrlContent.Contains("url")) {
            throw "direct_url.json content is unavailable"
        }
        if ($directUrlContent.dir_info.editable -isnot [bool] -or $directUrlContent.dir_info.editable -ne $true) {
            throw "direct_url.json does not identify an editable installation"
        }
        $editableUri = [uri][string]$directUrlContent.url
        if (-not $editableUri.IsFile) {
            throw "direct_url.json URL is not a local file URL"
        }
        $editableEvidence = Get-CanonicalPathEvidence -PathValue $editableUri.LocalPath
        if (-not $editableEvidence.valid -or -not $editableEvidence.canonical.Equals($repositoryCanonical, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "direct_url.json editable URL does not equal the intended repository"
        }
        $directUrlPathEvidence = Get-CanonicalPathEvidence -PathValue $directUrlPath
        $venvCanonical = [System.IO.Path]::GetFullPath((Join-Path $RepositoryRoot ".venv"))
        if (-not $directUrlPathEvidence.valid -or -not $directUrlPathEvidence.exists -or -not (Test-CanonicalPathWithin -Candidate $directUrlPathEvidence.canonical -Root $venvCanonical)) {
            throw "direct_url.json metadata is outside the repository virtual environment"
        }
        $directUrlMatches = $true
    }
    catch {
        $directUrlReason = $_.Exception.Message
        $failures.Add($directUrlReason)
    }

    $pthDiagnostics = @()
    $validEditablePthCount = 0
    $probePthFiles = @()
    if (-not $Probe.ContainsKey("pth_files") -or $Probe.pth_files -is [string] -or $Probe.pth_files -isnot [System.Collections.IList]) {
        $failures.Add("editable .pth records are malformed")
    }
    else {
        $malformedPthRecord = $false
        foreach ($record in @($Probe.pth_files)) {
            if ($record -isnot [System.Collections.IDictionary] -or -not $record.Contains("path") -or -not $record.Contains("content") -or -not $record.Contains("error")) {
                $malformedPthRecord = $true
                continue
            }
            $probePthFiles += $record
        }
        if ($malformedPthRecord) {
            $failures.Add("editable .pth records are malformed")
        }
    }
    foreach ($pth in $probePthFiles) {
        $pthMatches = $false
        $pthReason = $null
        $pathLines = @()
        $pthPathEvidence = Get-CanonicalPathEvidence -PathValue $pth.path
        $venvCanonical = [System.IO.Path]::GetFullPath((Join-Path $RepositoryRoot ".venv"))
        if (-not $pthPathEvidence.valid -or -not $pthPathEvidence.exists -or -not (Test-CanonicalPathWithin -Candidate $pthPathEvidence.canonical -Root $venvCanonical)) {
            $pthReason = "editable .pth metadata is outside the repository virtual environment"
        }
        elseif ($null -eq $pth.content) {
            $pthReason = "editable .pth content is unavailable"
        }
        else {
            $contentLines = @(([string]$pth.content -split "`r?`n") | Where-Object { -not [string]::IsNullOrWhiteSpace($_) -and -not $_.TrimStart().StartsWith("#") })
            if (@($contentLines | Where-Object { $_.TrimStart().StartsWith("import ") }).Count -gt 0) {
                $pthReason = "editable .pth contains executable code"
            }
            elseif ($contentLines.Count -ne 1) {
                $pthReason = "editable .pth must contain exactly one source path"
            }
            else {
                $line = $contentLines[0].Trim()
                $candidate = if ([System.IO.Path]::IsPathFullyQualified($line)) { $line } else { Join-Path (Split-Path -Parent ([string]$pth.path)) $line }
                $pthSourceEvidence = Get-CanonicalPathEvidence -PathValue $candidate
                if (-not $pthSourceEvidence.valid -or -not $pthSourceEvidence.canonical.Equals($expectedSrc, [System.StringComparison]::OrdinalIgnoreCase)) {
                    $pthReason = "editable .pth source does not equal the intended repository src directory"
                }
                else {
                    $pthMatches = $true
                    $validEditablePthCount++
                }
            }
        }
        if (-not $pthMatches) {
            $failures.Add($pthReason)
        }
        $pthDiagnostics += [ordered]@{ path = $pth.path; content = $pth.content; matches = $pthMatches; reason = $pthReason }
    }
    if ($pthDiagnostics.Count -eq 0 -or $validEditablePthCount -eq 0) {
        $failures.Add("no valid repository editable .pth source was found")
    }

    return [ordered]@{
        matches                    = $failures.Count -eq 0
        repository_root            = $RepositoryRoot
        current_working_directory  = if ($Probe.ContainsKey("cwd")) { $Probe.cwd } else { $null }
        expected_python_executable = $expectedPython.canonical
        python_executable          = $PythonPath
        sys_executable             = if ($Probe.ContainsKey("sys_executable")) { $Probe.sys_executable } else { $null }
        sys_prefix                 = if ($Probe.ContainsKey("sys_prefix")) { $Probe.sys_prefix } else { $null }
        sys_base_prefix            = if ($Probe.ContainsKey("sys_base_prefix")) { $Probe.sys_base_prefix } else { $null }
        pythonpath                 = if ($Probe.ContainsKey("pythonpath")) { $Probe.pythonpath } else { $null }
        sys_path                   = if ($Probe.ContainsKey("sys_path")) { @($Probe.sys_path) } else { @() }
        direct_url                 = [ordered]@{ path = $directUrlPath; content = $directUrlContent; matches = $directUrlMatches; reason = $directUrlReason }
        pth_files                  = $pthDiagnostics
        modules                    = $moduleDiagnostics
        failures                   = @($failures)
    }
}

function Get-PlanImportSourceDiagnostic {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Plan
    )

    if (-not $Plan.ContainsKey("import_source_probe")) {
        return [ordered]@{
            matches = [bool]$Plan.import_sources_match
            modules = @()
            failures = if ([bool]$Plan.import_sources_match) { @() } else { @("legacy test plan reports mismatched import sources") }
        }
    }
    $overrideNames = if ($Plan.ContainsKey("override_environment_names")) { @($Plan.override_environment_names) } else { @() }
    return Get-ImportSourceDiagnostic `
        -PythonPath (Get-RepositoryPythonPath -Plan $Plan) `
        -PythonResolved ([bool]$Plan.python_resolved) `
        -PythonEnvClean ([bool]$Plan.python_env_clean) `
        -OverrideEnvironmentNames $overrideNames `
        -Probe $Plan.import_source_probe
}

function Get-ImportSourceFailureMessage {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Diagnostic
    )

    if (@($Diagnostic.failures).Count -gt 0) {
        return (@($Diagnostic.failures) -join "; ")
    }
    return "import source diagnostic did not establish a matching repository"
}

function Get-ReservedArtifactPaths {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [string]$SessionId
    )

    $stateRoot = [string]$Plan.state_root
    return [ordered]@{
        transcript = Join-Path $stateRoot "packet2n-r5-calibration-$SessionId.log"
        evidence   = Join-Path $stateRoot "packet2n-r5-evidence-$SessionId.json"
        map_left   = Join-Path $stateRoot "packet2n-r5-physical-left-only-$SessionId.log"
        map_right  = Join-Path $stateRoot "packet2n-r5-physical-right-only-$SessionId.log"
    }
}

function Get-StateSessionBindingDigest {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State
    )

    $orderedPorts = [ordered]@{}
    foreach ($portName in @("physical_left", "logical_left", "physical_right", "logical_right")) {
        $orderedPorts[$portName] = $State.ports[$portName]
    }
    $payload = [ordered]@{
        session_id      = $State.session_id
        utc_start       = $State.utc_start
        state_path      = $State.state_path
        repo_head       = $State.repo_head
        runner_sha      = $State.runner_sha
        behavior_sha    = $State.behavior_sha
        expected_branch = $State.expected_branch
        packet_identity = $State.packet_identity
        leader_id       = $State.leader_id
        arm_profile     = $State.arm_profile
        ports           = $orderedPorts
        artifact_paths  = [ordered]@{
            transcript = $State.artifacts.transcript.path
            evidence   = $State.artifacts.evidence.path
            map_left   = $State.artifacts.map_left.path
            map_right  = $State.artifacts.map_right.path
        }
    }
    return Get-TextSha256Hex -Text (ConvertTo-CanonicalJson -Value $payload)
}

function Format-UtcTimestamp {
    param(
        [Parameter(Mandatory = $true)]
        [datetime]$Value
    )

    return $Value.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffffffZ")
}

function Test-IsJsonInteger {
    param(
        $Value
    )

    return ($Value -is [int] -or $Value -is [long])
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
    $diffArguments = @(
        "diff",
        "--quiet",
        $BehaviorBaseline,
        "--",
        ".",
        ":(exclude)docs/alohamini/alohamini.md",
        ":(exclude)tests/robots/test_packet2n_r5_leader_mapping.py",
        ":(exclude)tests/robots/test_check_am1_leader_buses.py",
        ":(exclude)tools/check_am1_leader_buses.py",
        ":(exclude)tools/packet2n_r5_leader_mapping.ps1"
    )
    $diffResult = Invoke-ExternalCommand -FilePath "git" -Arguments $diffArguments
    if ($diffResult.exit_code -ne 0 -and $diffResult.exit_code -ne 1) {
        New-Failure "Git protected-path diff query failed"
    }
    $pythonResolved = Test-Path -LiteralPath $pythonPath -PathType Leaf
    $setOverrideEnvironmentNames = @()
    foreach ($name in $OverrideEnvironmentNames) {
        if (-not [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($name, "Process"))) {
            $setOverrideEnvironmentNames += $name
        }
    }
    $pythonEnvClean = $setOverrideEnvironmentNames.Count -eq 0
    $rootResult = if ($pythonResolved -and $pythonEnvClean) {
        Invoke-ExternalCommand -FilePath $pythonPath -Arguments @("-c", "from lerobot.utils.constants import HF_LEROBOT_CALIBRATION; print(HF_LEROBOT_CALIBRATION)") -WorkingDirectory $RepositoryRoot
    }
    else {
        $null
    }
    $rootMatches = $false
    if ($null -ne $rootResult -and $rootResult.exit_code -eq 0 -and $rootResult.stderr.Count -eq 0 -and $rootResult.stdout.Count -eq 1) {
        $rootMatches = ([string]$rootResult.stdout[0]).Trim() -eq $RealCalibrationRoot
    }
    $importSourceProbe = if (-not $pythonEnvClean) {
        [ordered]@{ exit_code = $null; stderr = @(); probe_error = "probe skipped because guarded override variables are set" }
    }
    elseif ($pythonResolved) {
        Invoke-ImportSourceProbe -PythonPath $pythonPath
    }
    else {
        [ordered]@{ exit_code = $null; stderr = @(); probe_error = "Repository Python is missing" }
    }
    $importSourceDiagnostic = Get-ImportSourceDiagnostic `
        -PythonPath $pythonPath `
        -PythonResolved $pythonResolved `
        -PythonEnvClean $pythonEnvClean `
        -OverrideEnvironmentNames $setOverrideEnvironmentNames `
        -Probe $importSourceProbe
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
        protected_runtime_review         = [ordered]@{
            runtime_paths_unchanged = $diffResult.exit_code -eq 0
            excluded_reviewed_paths = @(
                "docs/alohamini/alohamini.md",
                "tests/robots/test_packet2n_r5_leader_mapping.py",
                "tests/robots/test_check_am1_leader_buses.py",
                "tools/check_am1_leader_buses.py",
                "tools/packet2n_r5_leader_mapping.ps1"
            )
        }
        python_env_clean                 = $pythonEnvClean
        override_environment_names       = $setOverrideEnvironmentNames
        python_resolved                  = $pythonResolved
        python_path                      = $pythonPath
        import_source_probe              = $importSourceProbe
        import_sources_match             = [bool]$importSourceDiagnostic.matches
        calibration_root_matches_expected = $rootMatches
        calibration_root                 = $RealCalibrationRoot
        state_root                       = $RealLogsDirectory
        rejected_archive_root            = $RealRejectedArchiveRoot
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

function Get-ImportDiagnosisPlan {
    $testPlan = Get-TestModePlan
    if ($null -ne $testPlan) {
        return $testPlan
    }

    $pythonPath = Get-RepositoryPythonPath
    $pythonResolved = Test-Path -LiteralPath $pythonPath -PathType Leaf
    $setOverrideEnvironmentNames = @()
    foreach ($name in $OverrideEnvironmentNames) {
        if (-not [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($name, "Process"))) {
            $setOverrideEnvironmentNames += $name
        }
    }
    $probe = if ($setOverrideEnvironmentNames.Count -gt 0) {
        [ordered]@{ exit_code = $null; stderr = @(); probe_error = "probe skipped because guarded override variables are set" }
    }
    elseif ($pythonResolved) {
        Invoke-ImportSourceProbe -PythonPath $pythonPath
    }
    else {
        [ordered]@{ exit_code = $null; stderr = @(); probe_error = "Repository Python is missing" }
    }
    return [ordered]@{
        is_test_mode               = $false
        python_path                = $pythonPath
        python_resolved            = $pythonResolved
        python_env_clean           = $setOverrideEnvironmentNames.Count -eq 0
        override_environment_names = $setOverrideEnvironmentNames
        import_source_probe        = $probe
        import_sources_match       = $false
    }
}

function Get-TestModeRoot {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Plan
    )

    if ([string]::IsNullOrEmpty($TestPlanPath)) {
        New-Failure "Test-mode sandbox requires -TestPlanPath"
    }
    return [System.IO.Path]::GetFullPath((Split-Path -Parent $TestPlanPath))
}

function Test-PathIsSameOrDescendant {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    $resolvedPath = [System.IO.Path]::GetFullPath($Path)
    if (-not $resolvedPath.Equals([System.IO.Path]::GetPathRoot($resolvedPath), [System.StringComparison]::OrdinalIgnoreCase)) {
        $resolvedPath = $resolvedPath.TrimEnd('\', '/')
    }
    $resolvedRoot = [System.IO.Path]::GetFullPath($Root)
    if (-not $resolvedRoot.Equals([System.IO.Path]::GetPathRoot($resolvedRoot), [System.StringComparison]::OrdinalIgnoreCase)) {
        $resolvedRoot = $resolvedRoot.TrimEnd('\', '/')
    }
    $rootWithSeparator = $resolvedRoot.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    return ($resolvedPath.Equals($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase) -or $resolvedPath.StartsWith($rootWithSeparator, [System.StringComparison]::OrdinalIgnoreCase))
}

function Assert-TestModePathHasNoReparsePoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Boundary
    )

    $currentPath = [System.IO.Path]::GetFullPath($Path)
    if (-not $currentPath.Equals([System.IO.Path]::GetPathRoot($currentPath), [System.StringComparison]::OrdinalIgnoreCase)) {
        $currentPath = $currentPath.TrimEnd('\', '/')
    }
    $boundaryPath = [System.IO.Path]::GetFullPath($Boundary)
    if (-not $boundaryPath.Equals([System.IO.Path]::GetPathRoot($boundaryPath), [System.StringComparison]::OrdinalIgnoreCase)) {
        $boundaryPath = $boundaryPath.TrimEnd('\', '/')
    }
    if (-not (Test-PathIsSameOrDescendant -Path $currentPath -Root $boundaryPath)) {
        New-Failure "Test-mode reparse validation path escaped its boundary: $Path"
    }
    while ($true) {
        if (Test-Path -LiteralPath $currentPath) {
            $item = Get-Item -LiteralPath $currentPath -Force
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                New-Failure "Test-mode sandbox refuses reparse point path component: $currentPath"
            }
        }
        if ($currentPath.Equals($boundaryPath, [System.StringComparison]::OrdinalIgnoreCase)) {
            break
        }
        $parent = [System.IO.Directory]::GetParent($currentPath)
        if ($null -eq $parent) {
            New-Failure "Test-mode reparse validation could not reach its boundary: $Path"
        }
        $currentPath = $parent.FullName
        if (-not $currentPath.Equals([System.IO.Path]::GetPathRoot($currentPath), [System.StringComparison]::OrdinalIgnoreCase)) {
            $currentPath = $currentPath.TrimEnd('\', '/')
        }
    }
}

function Assert-TestModePlanRoots {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Plan
    )

    if ($env:PACKET2N_R5_TEST_MODE -cne "1") {
        return
    }
    $testRoot = [System.IO.Path]::GetFullPath((Get-TestModeRoot -Plan $Plan)).TrimEnd('\', '/')
    $localApplicationData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    if ([string]::IsNullOrEmpty($localApplicationData)) {
        New-Failure "Test-mode sandbox could not resolve the Windows OS-temporary root"
    }
    $osTempRoot = [System.IO.Path]::GetFullPath((Join-Path $localApplicationData "Temp")).TrimEnd('\', '/')
    if ($testRoot.Equals($osTempRoot, [System.StringComparison]::OrdinalIgnoreCase) -or -not (Test-PathIsSameOrDescendant -Path $testRoot -Root $osTempRoot)) {
        New-Failure "Test-mode sandbox must be a dedicated OS-temporary subtree"
    }
    Assert-TestModePathHasNoReparsePoint -Path $testRoot -Boundary $osTempRoot

    $calibrationRoot = [System.IO.Path]::GetFullPath([string]$Plan.calibration_root).TrimEnd('\', '/')
    $stateRoot = [System.IO.Path]::GetFullPath([string]$Plan.state_root).TrimEnd('\', '/')
    if (-not $Plan.ContainsKey("rejected_archive_root")) {
        New-Failure "Test-mode rejected archive root is required"
    }
    $rejectedArchiveRoot = [System.IO.Path]::GetFullPath([string]$Plan.rejected_archive_root).TrimEnd('\', '/')
    if ($calibrationRoot.Equals([System.IO.Path]::GetFullPath($RealCalibrationRoot).TrimEnd('\', '/'), [System.StringComparison]::OrdinalIgnoreCase)) {
        New-Failure "Test-mode sandbox refuses the production calibration root"
    }
    if ($stateRoot.Equals([System.IO.Path]::GetFullPath($RealLogsDirectory).TrimEnd('\', '/'), [System.StringComparison]::OrdinalIgnoreCase)) {
        New-Failure "Test-mode sandbox refuses the production logs root"
    }
    if ($rejectedArchiveRoot.Equals([System.IO.Path]::GetFullPath($RealRejectedArchiveRoot).TrimEnd('\', '/'), [System.StringComparison]::OrdinalIgnoreCase)) {
        New-Failure "Test-mode sandbox refuses the production rejected archive root"
    }

    foreach ($protectedPath in @($RepositoryRoot, $RealCalibrationRoot, $RealLogsDirectory, $RealRejectedArchiveRoot)) {
        if ((Test-PathIsSameOrDescendant -Path $testRoot -Root $protectedPath) -or (Test-PathIsSameOrDescendant -Path $protectedPath -Root $testRoot)) {
            New-Failure "Test-mode sandbox overlaps a protected production or repository path"
        }
    }
    foreach ($entry in @(
        [ordered]@{ name = "calibration root"; path = $calibrationRoot },
        [ordered]@{ name = "state root"; path = $stateRoot },
        [ordered]@{ name = "rejected archive root"; path = $rejectedArchiveRoot }
    )) {
        if ($entry.path.Equals($testRoot, [System.StringComparison]::OrdinalIgnoreCase) -or -not (Test-PathIsSameOrDescendant -Path $entry.path -Root $testRoot)) {
            New-Failure "Test-mode $($entry.name) escaped the test-mode sandbox"
        }
        Assert-TestModePathHasNoReparsePoint -Path $entry.path -Boundary $testRoot
    }
    foreach ($pair in @(
        [ordered]@{ first = $calibrationRoot; second = $stateRoot },
        [ordered]@{ first = $calibrationRoot; second = $rejectedArchiveRoot },
        [ordered]@{ first = $stateRoot; second = $rejectedArchiveRoot }
    )) {
        if ((Test-PathIsSameOrDescendant -Path $pair.first -Root $pair.second) -or (Test-PathIsSameOrDescendant -Path $pair.second -Root $pair.first)) {
            New-Failure "Test-mode calibration, state, and rejected archive roots must be separate subtrees"
        }
    }
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
    Assert-TestModePlanRoots -Plan $Plan
    $resolvedRoot = [System.IO.Path]::GetFullPath((Get-TestModeRoot -Plan $Plan))
    $resolvedPath = [System.IO.Path]::GetFullPath($Path)
    $rootWithSeparator = $resolvedRoot.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    if (-not ($resolvedPath.Equals($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase) -or $resolvedPath.StartsWith($rootWithSeparator, [System.StringComparison]::OrdinalIgnoreCase))) {
        New-Failure "Test-mode mutable path escaped validated root: $Path"
    }
    Assert-TestModePathHasNoReparsePoint -Path $resolvedPath -Boundary $resolvedRoot
}

function Assert-TestModeMutablePaths {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [string]$StatePathValue,

        [string]$SessionId
    )

    if ($env:PACKET2N_R5_TEST_MODE -cne "1") {
        return
    }
    Assert-TestModePlanRoots -Plan $Plan
    Assert-TestModePath -Plan $Plan -Path $StatePathValue
    Assert-TestModePath -Plan $Plan -Path ([string]$Plan.calibration.left.path)
    Assert-TestModePath -Plan $Plan -Path ([string]$Plan.calibration.right.path)
    foreach ($side in @("left", "right")) {
        if (-not (Test-PathIsSameOrDescendant -Path ([string]$Plan.calibration[$side].path) -Root ([string]$Plan.calibration_root))) {
            New-Failure "Test-mode calibration path escaped the validated calibration root: $($Plan.calibration[$side].path)"
        }
    }
    if (-not (Test-PathIsSameOrDescendant -Path $StatePathValue -Root ([string]$Plan.state_root))) {
        New-Failure "Test-mode state path escaped the validated state root: $StatePathValue"
    }
    if (-not [string]::IsNullOrEmpty($SessionId)) {
        $reserved = Get-ReservedArtifactPaths -Plan $Plan -SessionId $SessionId
        foreach ($artifactName in @("transcript", "evidence", "map_left", "map_right")) {
            Assert-TestModePath -Plan $Plan -Path ([string]$reserved[$artifactName])
            if (-not (Test-PathIsSameOrDescendant -Path ([string]$reserved[$artifactName]) -Root ([string]$Plan.state_root))) {
                New-Failure "Test-mode artifact path escaped the validated state root: $($reserved[$artifactName])"
            }
        }
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

function Test-ExactValue {
    param(
        [Parameter(Mandatory = $true)]
        $Actual,

        [Parameter(Mandatory = $true)]
        $Expected
    )

    return (ConvertTo-CompactJson -Value (ConvertTo-SortedCanonicalObject -Value $Actual)) -ceq (ConvertTo-CompactJson -Value (ConvertTo-SortedCanonicalObject -Value $Expected))
}

function Assert-ExactKeySet {
    param(
        [Parameter(Mandatory = $true)]
        $Value,

        [Parameter(Mandatory = $true)]
        [string[]]$ExpectedKeys,

        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    if ($Value -isnot [System.Collections.IDictionary]) {
        New-Failure $Message
    }
    $actualKeys = @($Value.Keys)
    if ($actualKeys.Count -ne $ExpectedKeys.Count) {
        New-Failure $Message
    }
    foreach ($key in $ExpectedKeys) {
        if ($actualKeys -cnotcontains $key) {
            New-Failure $Message
        }
    }
}

function Test-IsSha256Hex {
    param($Value)

    return $Value -is [string] -and [string]$Value -cmatch '^[0-9A-F]{64}$'
}

function Test-IsUtcTimestamp {
    param($Value)

    if ($Value -isnot [string] -or [string]::IsNullOrWhiteSpace([string]$Value)) {
        return $false
    }
    try {
        $parsed = [DateTimeOffset]::Parse([string]$Value, [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::RoundtripKind)
        return $parsed.Offset -eq [TimeSpan]::Zero
    }
    catch {
        return $false
    }
}

function ConvertTo-SortedCanonicalObject {
    param(
        $Value
    )

    if ($null -eq $Value) {
        return $null
    }
    if ($Value -is [System.Collections.IDictionary]) {
        $result = [ordered]@{}
        $keys = [string[]]@($Value.Keys | ForEach-Object { [string]$_ })
        [array]::Sort($keys, [System.StringComparer]::Ordinal)
        foreach ($key in $keys) {
            $exactKey = @($Value.Keys | Where-Object { [string]$_ -ceq $key })
            if ($exactKey.Count -ne 1) {
                New-Failure "Exact value comparison found an ambiguous key"
            }
            $result[$key] = ConvertTo-SortedCanonicalObject -Value $Value[$exactKey[0]]
        }
        return $result
    }
    if ($Value -is [System.Collections.IEnumerable] -and $Value -isnot [string]) {
        return @($Value | ForEach-Object { ConvertTo-SortedCanonicalObject -Value $_ })
    }
    return $Value
}

function Assert-ExactValue {
    param(
        [Parameter(Mandatory = $true)]
        $Actual,

        [Parameter(Mandatory = $true)]
        $Expected,

        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    if (-not (Test-ExactValue -Actual $Actual -Expected $Expected)) {
        New-Failure $Message
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

function Test-UseDirectNativeExitProbe {
    param(
        [Parameter(Mandatory = $true)]
        [string]$StageName,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan
    )

    if (-not [bool]$Plan.is_test_mode -or $StageName -cne "Calibrate") {
        return $false
    }
    $stagePlan = $Plan.stage_plan[$StageName]
    if ($null -eq $stagePlan -or -not $stagePlan.ContainsKey("direct_native_exit_probe")) {
        return $false
    }
    if (-not (Test-IsJsonInteger -Value $stagePlan.direct_native_exit_probe) -or [int]$stagePlan.direct_native_exit_probe -ne 7) {
        New-Failure "Test-mode direct native probe permits only the fixed exit code 7"
    }
    return $true
}

function Build-StageCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$StageName,

        [hashtable]$Plan
    )

    if (Test-UseDirectNativeExitProbe -StageName $StageName -Plan $Plan) {
        $systemDirectory = [Environment]::SystemDirectory
        if ([string]::IsNullOrEmpty($systemDirectory)) {
            New-Failure "Test-mode direct native probe could not resolve the Windows system directory"
        }
        $commandInterpreter = Join-Path $systemDirectory "cmd.exe"
        return [ordered]@{
            executable = [System.IO.Path]::GetFullPath($commandInterpreter)
            arguments  = @("/d", "/c", "exit", "7")
        }
    }

    $pythonPath = Get-RepositoryPythonPath -Plan $Plan
    $calibrateScript = Join-Path $RepositoryRoot "examples\alohamini\calibrate_bi.py"
    $teleoperateScript = Join-Path $RepositoryRoot "examples\alohamini\teleoperate_bi.py"
    $busCheckScript = Join-Path $RepositoryRoot "tools\check_am1_leader_buses.py"
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
        "CheckLeaderBuses" {
            @($busCheckScript, "CHECK")
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

    $keys = @($Calibration.Keys)
    if ($keys.Count -ne $ExpectedCalibrationKeys.Count) {
        New-Failure "$Label calibration schema mismatch"
    }
    foreach ($joint in $keys) {
        if ($ExpectedCalibrationKeys -cnotcontains [string]$joint) {
            New-Failure "$Label calibration schema mismatch"
        }
    }
    $seenIds = [System.Collections.Generic.HashSet[int]]::new()
    $expectedRecordKeys = @("id", "drive_mode", "homing_offset", "range_min", "range_max")
    for ($jointIndex = 0; $jointIndex -lt $ExpectedCalibrationKeys.Count; $jointIndex++) {
        $joint = $ExpectedCalibrationKeys[$jointIndex]
        $expectedId = $jointIndex + 1
        $record = $Calibration[$joint]
        if ($null -eq $record) {
            New-Failure "$Label calibration is missing joint $joint"
        }
        $recordKeys = @($record.Keys)
        if ($recordKeys.Count -ne $expectedRecordKeys.Count) {
            New-Failure "$Label calibration record mismatch for $joint"
        }
        foreach ($field in $recordKeys) {
            if ($expectedRecordKeys -cnotcontains [string]$field) {
                New-Failure "$Label calibration record mismatch for $joint"
            }
        }
        foreach ($field in $expectedRecordKeys) {
            if (-not $record.ContainsKey($field)) {
                New-Failure "$Label calibration record mismatch for $joint"
            }
        }
        if ($null -eq $record.id) {
            New-Failure "$Label calibration record mismatch for $joint"
        }
        foreach ($field in $expectedRecordKeys) {
            if (-not (Test-IsJsonInteger -Value $record[$field])) {
                New-Failure "$Label calibration $field must be a JSON integer for $joint"
            }
        }
        if ([int]$record.id -ne $expectedId) {
            New-Failure "$Label calibration id mismatch for $joint"
        }
        if (-not $seenIds.Add([int]$record.id)) {
            New-Failure "$Label calibration id is duplicated"
        }
        if ([int]$record.drive_mode -ne 0) {
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
        mtime_utc   = Format-UtcTimestamp -Value (Get-Item -LiteralPath $Path).LastWriteTimeUtc
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

function Assert-ImmutableManifestAndBackups {
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
        $backupCalibration = Read-JsonFile -Path $backupPath
        Assert-CalibrationSchema -Calibration $backupCalibration -Label "$side backup"
    }
}

function Assert-OriginalCalibrationIdentities {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Plan
    )

    $current = Get-CurrentIdentities -Plan $Plan
    foreach ($side in @("left", "right")) {
        if ($current[$side].path -cne $Plan.calibration[$side].path) {
            New-Failure "$side source path mismatch from immutable original"
        }
        if ($current[$side].sha256 -cne $Plan.calibration[$side].backup_sha256) {
            New-Failure "$side source hash mismatch from immutable original"
        }
        if ($current[$side].size -ne [int64]$Plan.calibration[$side].backup_size) {
            New-Failure "$side source size mismatch from immutable original"
        }
        if ($current[$side].mtime_utc -cne $Plan.calibration[$side].source_mtime_utc) {
            New-Failure "$side source timestamp mismatch from immutable original"
        }
        $backupCalibration = Read-JsonFile -Path $Plan.calibration[$side].backup_path
        Assert-ExactValue -Actual $current[$side].calibration -Expected $backupCalibration -Message "$side source schema values mismatch from immutable original"
    }
}

function Test-CurrentIdentitiesAreExactOriginals {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Current,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan
    )

    foreach ($side in @("left", "right")) {
        if ($Current[$side].path -cne $Plan.calibration[$side].path) {
            return $false
        }
        if ($Current[$side].sha256 -cne $Plan.calibration[$side].backup_sha256) {
            return $false
        }
        if ($Current[$side].size -ne [int64]$Plan.calibration[$side].backup_size) {
            return $false
        }
        if ($Current[$side].mtime_utc -cne $Plan.calibration[$side].source_mtime_utc) {
            return $false
        }
        $backupCalibration = Read-JsonFile -Path $Plan.calibration[$side].backup_path
        if (-not (Test-ExactValue -Actual $Current[$side].calibration -Expected $backupCalibration)) {
            return $false
        }
    }
    return $true
}

function Test-CurrentHashesAreOriginals {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Current,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan
    )

    return (
        $Current.left.sha256 -ceq $Plan.calibration.left.backup_sha256 -and
        $Current.right.sha256 -ceq $Plan.calibration.right.backup_sha256
    )
}

function Assert-PreCalibrationMatchesOriginals {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan
    )

    foreach ($side in @("left", "right")) {
        $pre = $State.pre_calibration[$side]
        if ($pre.path -cne $Plan.calibration[$side].path -or
            $pre.sha256 -cne $Plan.calibration[$side].backup_sha256 -or
            $pre.size -ne [int64]$Plan.calibration[$side].backup_size -or
            $pre.mtime_utc -cne $Plan.calibration[$side].source_mtime_utc) {
            New-Failure "Evidence semantic validation failed: $side pre-calibration identity mismatch"
        }
        $backupCalibration = Read-JsonFile -Path $Plan.calibration[$side].backup_path
        Assert-ExactValue -Actual $pre.calibration -Expected $backupCalibration -Message "Evidence semantic validation failed: $side pre-calibration schema values mismatch"
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
    $runtimePathsUnchanged = [bool]$Plan.protected_runtime_paths_unchanged
    if ($Plan.ContainsKey("protected_runtime_review") -and $null -ne $Plan.protected_runtime_review) {
        $runtimePathsUnchanged = [bool]$Plan.protected_runtime_review.runtime_paths_unchanged
    }
    if (-not $runtimePathsUnchanged) {
        New-Failure "Guard refusal: protected runtime paths differ from the behavior baseline"
    }
    if (-not [bool]$Plan.python_env_clean) {
        New-Failure "Guard refusal: Python/HF override environment variables are set"
    }
    if (-not [bool]$Plan.python_resolved) {
        New-Failure "Guard refusal: repository Python could not be resolved"
    }
    $importDiagnostic = Get-PlanImportSourceDiagnostic -Plan $Plan
    if (-not [bool]$importDiagnostic.matches) {
        $details = Get-ImportSourceFailureMessage -Diagnostic $importDiagnostic
        New-Failure "Guard refusal: repository import sources do not match. $details"
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

    $sessionId = if ($Plan.ContainsKey("session_id")) { $Plan.session_id } else { Get-SessionId }
    $utcStart = if ($Plan.ContainsKey("utc_start")) { $Plan.utc_start } else { [DateTime]::UtcNow.ToString("o") }
    Assert-TestModeMutablePaths -Plan $Plan -StatePathValue $StatePathValue -SessionId $sessionId
    $identities = Get-CurrentIdentities -Plan $Plan
    $runnerSha = Get-RunnerSha256
    $artifactPaths = Get-ReservedArtifactPaths -Plan $Plan -SessionId $sessionId
    $state = [ordered]@{
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
        session_binding_sha256 = $null
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
            transcript = [ordered]@{
                path   = $artifactPaths.transcript
                sha256 = $null
                size   = $null
            }
            evidence   = [ordered]@{
                path   = $artifactPaths.evidence
                sha256 = $null
                size   = $null
            }
            map_left   = [ordered]@{
                path   = $artifactPaths.map_left
                sha256 = $null
            }
            map_right  = [ordered]@{
                path   = $artifactPaths.map_right
                sha256 = $null
            }
        }
    }
    $state.session_binding_sha256 = Get-StateSessionBindingDigest -State $state
    return $state
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

    if ($State.runner_version -cne $RunnerVersion) {
        New-Failure "Runner version mismatch in state"
    }
    if ($State.packet_identity -cne $PacketIdentity) {
        New-Failure "Packet identity mismatch in state"
    }
    if ($State.behavior_sha -cne $BehaviorBaseline) {
        New-Failure "Behavior baseline mismatch in state"
    }
    if ($State.expected_branch -cne $ExpectedBranch) {
        New-Failure "State branch provenance mismatch"
    }
    if ($State.leader_id -cne $ExpectedLeaderId -or $State.arm_profile -cne $ExpectedProfile) {
        New-Failure "Persisted leader identity is invalid"
    }
    foreach ($name in $ExpectedPorts.Keys) {
        if ($State.ports[$name] -cne $ExpectedPorts[$name]) {
            New-Failure "Persisted port assignment is invalid"
        }
    }
    if ($State.schema_version -cne $SchemaVersion) {
        New-Failure "State schema version mismatch"
    }
}

function Assert-ReservedArtifactPaths {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan
    )

    $reserved = Get-ReservedArtifactPaths -Plan $Plan -SessionId ([string]$State.session_id)
    foreach ($artifactName in @("transcript", "evidence", "map_left", "map_right")) {
        if ($State.artifacts[$artifactName].path -cne $reserved[$artifactName]) {
            New-Failure "State reserved artifact path is invalid for $artifactName"
        }
    }
}

function Get-StateValidationIssues {
    param(
        [hashtable]$State,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan
    )

    $issues = [System.Collections.Generic.List[string]]::new()
    $expectedStateKeys = @(
        "schema_version", "runner_version", "packet_identity", "session_id", "utc_start",
        "behavior_sha", "repo_head", "expected_branch", "runner_sha", "state_path", "ports",
        "leader_id", "arm_profile", "classification", "completed_stages", "failed_stages",
        "summaries", "final_result", "next_stage", "session_binding_sha256", "stages",
        "pre_calibration", "post_calibration", "artifacts"
    )
    $actualStateKeys = @($State.Keys)
    foreach ($name in $expectedStateKeys) {
        if ($actualStateKeys -cnotcontains $name) {
            $issues.Add("missing $name")
        }
    }
    foreach ($name in $actualStateKeys) {
        if ($expectedStateKeys -cnotcontains [string]$name) {
            $issues.Add("unexpected state key $name")
        }
    }
    if ($State.ContainsKey("session_binding_sha256") -and [string]::IsNullOrEmpty([string]$State.session_binding_sha256)) {
        $issues.Add("missing session binding digest")
    }
    if ($State.ContainsKey("completed_stages")) {
        $expectedOrder = @("Calibrate", "MapLeft", "MapRight", "Verify")
        $completed = @($State.completed_stages)
        if ($completed.Count -gt $expectedOrder.Count) {
            $issues.Add("invalid completed stage ordering")
        }
        for ($index = 0; $index -lt $completed.Count -and $index -lt $expectedOrder.Count; $index++) {
            if ([string]$completed[$index] -cne [string]$expectedOrder[$index]) {
                $issues.Add("invalid completed stage ordering")
                break
            }
        }
    }
    $expectedStageNames = @("Calibrate", "MapLeft", "MapRight", "Verify")
    $completedStageNames = if ($State.ContainsKey("completed_stages")) { @($State.completed_stages) } else { @() }
    if (-not $State.ContainsKey("stages") -or $State.stages -isnot [System.Collections.IDictionary]) {
        $issues.Add("invalid stage schema")
    }
    else {
        $actualStageNames = @($State.stages.Keys)
        if ($actualStageNames.Count -ne $expectedStageNames.Count) {
            $issues.Add("invalid stage schema")
        }
        foreach ($actualStageName in $actualStageNames) {
            if ($expectedStageNames -cnotcontains [string]$actualStageName) {
                $issues.Add("unexpected stage $actualStageName")
            }
        }
        foreach ($stageName in $expectedStageNames) {
            if ($actualStageNames -cnotcontains $stageName) {
                $issues.Add("missing stage $stageName")
                continue
            }
            $stageRecord = $State.stages[$stageName]
            if ($stageRecord -isnot [System.Collections.IDictionary]) {
                $issues.Add("invalid $stageName stage schema")
                continue
            }
            $stageKeys = @($stageRecord.Keys)
            if ($stageKeys.Count -ne 2 -or $stageKeys -cnotcontains "result" -or $stageKeys -cnotcontains "native") {
                $issues.Add("invalid $stageName stage schema")
                continue
            }
            $stageResult = [string]$stageRecord.result
            if (@("pending", "failed", "completed") -cnotcontains $stageResult) {
                $issues.Add("invalid $stageName stage result")
            }
            $isCompleted = $completedStageNames -ccontains $stageName
            if ($isCompleted -and $stageResult -cne "completed") {
                $issues.Add("completed stage $stageName does not have result=completed")
            }
            if (-not $isCompleted -and $stageResult -ceq "completed") {
                $issues.Add("stage $stageName has result=completed but is absent from completed_stages")
            }

            $native = $stageRecord.native
            if ($native -isnot [System.Collections.IDictionary]) {
                $issues.Add("invalid $stageName native schema")
                continue
            }
            $expectedNativeKeys = @("attempted", "launched", "real_exit_code", "executable", "arguments")
            $nativeKeys = @($native.Keys)
            $nativeSchemaValid = $nativeKeys.Count -eq $expectedNativeKeys.Count
            if ($nativeKeys.Count -ne $expectedNativeKeys.Count) {
                $issues.Add("invalid $stageName native schema")
            }
            foreach ($nativeKey in $expectedNativeKeys) {
                if ($nativeKeys -cnotcontains $nativeKey) {
                    $issues.Add("missing $stageName native field $nativeKey")
                    $nativeSchemaValid = $false
                }
            }
            if (-not $nativeSchemaValid) {
                continue
            }
            if ($nativeKeys -cnotcontains "attempted" -or $native.attempted -isnot [bool]) {
                $issues.Add("invalid $stageName native attempted value")
            }
            if ($nativeKeys -cnotcontains "launched" -or $native.launched -isnot [bool]) {
                $issues.Add("invalid $stageName native launched value")
            }
            if ($nativeKeys -ccontains "real_exit_code" -and $null -ne $native.real_exit_code -and -not (Test-IsJsonInteger -Value $native.real_exit_code)) {
                $issues.Add("invalid $stageName native exit code")
            }
            if ($nativeKeys -ccontains "executable" -and $null -ne $native.executable -and $native.executable -isnot [string]) {
                $issues.Add("invalid $stageName native executable")
            }
            if ($nativeKeys -cnotcontains "arguments" -or $native.arguments -isnot [System.Array]) {
                $issues.Add("invalid $stageName native arguments")
            }

            if ($stageName -ceq "Verify") {
                if ($native.attempted -ne $false -or $native.launched -ne $false -or $null -ne $native.real_exit_code -or $null -ne $native.executable -or @($native.arguments).Count -ne 0) {
                    $issues.Add("Verify must remain a non-native stage")
                }
                continue
            }
            if ($isCompleted) {
                if ($native.attempted -ne $true -or $native.launched -ne $true -or -not (Test-IsJsonInteger -Value $native.real_exit_code) -or [int64]$native.real_exit_code -ne 0) {
                    $issues.Add("completed native stage $stageName lacks attempted=true, launched=true, exit=0")
                }
                $expectedCommand = Build-StageCommand -StageName $stageName -Plan $Plan
                try {
                    if (-not (Test-ExactValue -Actual $native.executable -Expected $expectedCommand.executable) -or -not (Test-ExactValue -Actual @($native.arguments) -Expected @($expectedCommand.arguments))) {
                        $issues.Add("completed native stage $stageName command does not match the exact reviewed command")
                    }
                }
                catch {
                    $issues.Add("completed native stage $stageName command is invalid")
                }
            }
        }
    }
    if ($State.ContainsKey("artifacts") -and $null -ne $State.artifacts) {
        foreach ($artifactName in @("transcript", "evidence", "map_left", "map_right")) {
            if (@($State.artifacts.Keys) -cnotcontains $artifactName) {
                $issues.Add("missing artifact $artifactName")
            }
        }
    }
    if ($State.ContainsKey("ports") -and $null -ne $State.ports) {
        $expectedPortKeys = @("physical_left", "logical_left", "physical_right", "logical_right")
        $actualPortKeys = @($State.ports.Keys)
        if ($actualPortKeys.Count -ne $expectedPortKeys.Count) {
            $issues.Add("invalid port schema")
        }
        foreach ($portName in $expectedPortKeys) {
            if ($actualPortKeys -cnotcontains $portName) {
                $issues.Add("missing port $portName")
            }
        }
    }
    if ($State.ContainsKey("classification") -and @("ORIGINAL_CALIBRATION_INTACT", "VALID_FRESH_CALIBRATION") -cnotcontains [string]$State.classification) {
        $issues.Add("invalid persisted classification")
    }
    if ($State.ContainsKey("completed_stages") -and $State.completed_stages -ccontains "Calibrate" -and $null -eq $State.post_calibration) {
        $issues.Add("missing post-calibration identities")
    }
    return @($issues.ToArray())
}

function Test-ApprovedLegacyRestartAuthority {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State,

        [Parameter(Mandatory = $true)]
        [string]$StatePathValue,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [string]$StateIdentityPath
    )

    $identityPath = if ([string]::IsNullOrEmpty($StateIdentityPath)) { $StatePathValue } else { $StateIdentityPath }

    if ([bool]$Plan.is_test_mode) {
        if (-not $Plan.ContainsKey("restart_legacy_fixture")) {
            return $false
        }
        $fixture = $Plan.restart_legacy_fixture
        Assert-ExactKeySet `
            -Value $fixture `
            -ExpectedKeys @(
                "schema_version", "repo_head", "runner_sha256", "behavior_sha", "session_id",
                "state", "fresh", "evidence", "transcript", "transcript_body_evaluation"
            ) `
            -Message "Test-mode legacy RestartCalibration fixture schema is invalid"
        Assert-ExactKeySet -Value $fixture.state -ExpectedKeys @("path", "sha256", "size") -Message "Test-mode legacy RestartCalibration state fixture is invalid"
        Assert-ExactKeySet -Value $fixture.fresh -ExpectedKeys @("left", "right") -Message "Test-mode legacy RestartCalibration fresh fixture is invalid"
        foreach ($side in @("left", "right")) {
            Assert-ExactKeySet -Value $fixture.fresh[$side] -ExpectedKeys @("path", "sha256", "size", "mtime_utc", "calibration") -Message "Test-mode legacy RestartCalibration fresh fixture is invalid"
            Assert-CalibrationSchema -Calibration $fixture.fresh[$side].calibration -Label "test-mode legacy RestartCalibration $side fixture"
        }
        foreach ($artifactName in @("evidence", "transcript")) {
            Assert-ExactKeySet -Value $fixture[$artifactName] -ExpectedKeys @("path", "sha256", "size") -Message "Test-mode legacy RestartCalibration $artifactName fixture is invalid"
        }
        if ($fixture.schema_version -cne "1" -or
            $fixture.repo_head -cne $LegacyRestartRepoHead -or
            $fixture.runner_sha256 -cne $LegacyRestartRunnerSha256 -or
            $fixture.behavior_sha -cne $BehaviorBaseline -or
            $fixture.transcript_body_evaluation -cne "KNOWN_LIMITATION" -or
            $fixture.session_id -cne $State.session_id -or
            $fixture.state.path -cne $StatePathValue -or
            -not (Test-IsSha256Hex -Value $fixture.state.sha256) -or
            -not (Test-IsJsonInteger -Value $fixture.state.size) -or
            $fixture.evidence.path -cne $State.artifacts.evidence.path -or
            $fixture.evidence.sha256 -cne $State.artifacts.evidence.sha256 -or
            [int64]$fixture.evidence.size -ne [int64]$State.artifacts.evidence.size -or
            $fixture.transcript.path -cne $State.artifacts.transcript.path -or
            $fixture.transcript.sha256 -cne $State.artifacts.transcript.sha256 -or
            [int64]$fixture.transcript.size -ne [int64]$State.artifacts.transcript.size -or
            -not (Test-ExactValue -Actual $fixture.fresh -Expected $State.post_calibration)) {
            return $false
        }
        $stateItem = Get-Item -LiteralPath $identityPath -Force
        return (
            $stateItem -is [System.IO.FileInfo] -and
            ($stateItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq 0 -and
            (Get-Sha256Hex -Path $identityPath) -ceq $fixture.state.sha256 -and
            [int64]$stateItem.Length -eq [int64]$fixture.state.size -and
            (Get-Sha256Hex -Path $fixture.evidence.path) -ceq $fixture.evidence.sha256 -and
            [int64](Get-Item -LiteralPath $fixture.evidence.path).Length -eq [int64]$fixture.evidence.size -and
            (Get-Sha256Hex -Path $fixture.transcript.path) -ceq $fixture.transcript.sha256 -and
            [int64](Get-Item -LiteralPath $fixture.transcript.path).Length -eq [int64]$fixture.transcript.size
        )
    }

    $expectedStatePath = Join-Path $RealLogsDirectory "packet2n-r5-state.json"
    $expectedEvidencePath = Join-Path $RealLogsDirectory "packet2n-r5-evidence-$LegacyRestartSessionId.json"
    $expectedTranscriptPath = Join-Path $RealLogsDirectory "packet2n-r5-calibration-$LegacyRestartSessionId.log"
    $expectedLeftPath = Join-Path $RealCalibrationRoot "teleoperators\so_leader\so101_leader_bi_left.json"
    $expectedRightPath = Join-Path $RealCalibrationRoot "teleoperators\so_leader\so101_leader_bi_right.json"
    if ($State.session_id -cne $LegacyRestartSessionId -or
        $StatePathValue -cne $expectedStatePath -or
        $State.artifacts.evidence.path -cne $expectedEvidencePath -or
        $State.artifacts.evidence.sha256 -cne $LegacyRestartEvidenceSha256 -or
        [int64]$State.artifacts.evidence.size -ne $LegacyRestartEvidenceSize -or
        $State.artifacts.transcript.path -cne $expectedTranscriptPath -or
        $State.artifacts.transcript.sha256 -cne $LegacyRestartTranscriptSha256 -or
        [int64]$State.artifacts.transcript.size -ne $LegacyRestartTranscriptSize -or
        $State.post_calibration.left.path -cne $expectedLeftPath -or
        $State.post_calibration.left.sha256 -cne $LegacyRestartFreshLeftSha256 -or
        [int64]$State.post_calibration.left.size -ne $LegacyRestartFreshLeftSize -or
        $State.post_calibration.left.mtime_utc -cne $LegacyRestartFreshLeftMtimeUtc -or
        $State.post_calibration.right.path -cne $expectedRightPath -or
        $State.post_calibration.right.sha256 -cne $LegacyRestartFreshRightSha256 -or
        [int64]$State.post_calibration.right.size -ne $LegacyRestartFreshRightSize -or
        $State.post_calibration.right.mtime_utc -cne $LegacyRestartFreshRightMtimeUtc) {
        return $false
    }
    $stateItem = Get-Item -LiteralPath $identityPath -Force
    return (
        $stateItem -is [System.IO.FileInfo] -and
        ($stateItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq 0 -and
        (Get-Sha256Hex -Path $identityPath) -ceq $LegacyRestartStateSha256 -and
        [int64]$stateItem.Length -eq $LegacyRestartStateSize
    )
}

function Get-RestartTranscriptValidationPayload {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State,

        [Parameter(Mandatory = $true)]
        [string]$StatePathValue,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [string]$TranscriptSha256,

        [string]$StateIdentityPath
    )

    $knownLegacyLimitation = (
        $State.repo_head -ceq $LegacyRestartRepoHead -and
        $State.runner_sha -ceq $LegacyRestartRunnerSha256 -and
        $TranscriptSha256 -ceq $State.artifacts.transcript.sha256 -and
        (Test-ApprovedLegacyRestartAuthority -State $State -StatePathValue $StatePathValue -Plan $Plan -StateIdentityPath $StateIdentityPath)
    )
    if ($knownLegacyLimitation) {
        return [ordered]@{
            header_valid                            = $true
            hash_and_size_valid                     = $true
            final_terminator_valid                  = $true
            native_calibration_output_evaluation    = "KNOWN_APPROVED_LEGACY_LIMITATION"
            body_contains_native_calibration_output = $false
            limitation                              = "The exact approved legacy transcript is known to contain no native calibration output; its bound header, hash, size, and final terminator are validated."
        }
    }
    return [ordered]@{
        header_valid                            = $true
        hash_and_size_valid                     = $true
        final_terminator_valid                  = $true
        native_calibration_output_evaluation    = "NOT_EVALUATED"
        body_contains_native_calibration_output = $null
        limitation                              = "Transcript body content was not evaluated for native calibration output."
    }
}

function Assert-StateProvenance {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State,

        [Parameter(Mandatory = $true)]
        [string]$StatePathValue,

        [hashtable]$Plan,

        [switch]$AllowRestartCandidate,

        [switch]$AllowInterruptedCandidate,

        [string]$StateIdentityPath
    )

    Assert-ReservedArtifactPaths -State $State -Plan $Plan
    $currentProvenance = $State.repo_head -ceq $Plan.head -and $State.runner_sha -ceq (Get-RunnerSha256)
    $legacyRestartProvenance = (
        $AllowRestartCandidate -and
        $State.repo_head -ceq $LegacyRestartRepoHead -and
        $State.runner_sha -ceq $LegacyRestartRunnerSha256 -and
        $State.behavior_sha -ceq $BehaviorBaseline -and
        $State.classification -ceq "VALID_FRESH_CALIBRATION" -and
        $State.next_stage -ceq "MapLeft" -and
        @($State.completed_stages).Count -eq 1 -and
        [string]$State.completed_stages[0] -ceq "Calibrate" -and
        [string]::IsNullOrEmpty([string]$State.artifacts.map_left.sha256) -and
        [string]::IsNullOrEmpty([string]$State.artifacts.map_right.sha256) -and
        (Test-ApprovedLegacyRestartAuthority -State $State -StatePathValue $StatePathValue -Plan $Plan -StateIdentityPath $StateIdentityPath)
    )
    $interruptedProvenance = $false
    if ($AllowInterruptedCandidate) {
        try {
            $interruptedAuthority = Get-InterruptedRecoveryAuthority -State $State -Plan $Plan -StatePathValue $StatePathValue
            $interruptedProvenance = (
                $State.repo_head -ceq $interruptedAuthority.repo_head -and
                $State.runner_sha -ceq $interruptedAuthority.runner_sha256 -and
                $State.behavior_sha -ceq $BehaviorBaseline -and
                $State.session_id -ceq $interruptedAuthority.session_id -and
                (Get-Sha256Hex -Path $StatePathValue) -ceq $interruptedAuthority.state.sha256 -and
                [int64](Get-Item -LiteralPath $StatePathValue).Length -eq [int64]$interruptedAuthority.state.size
            )
        }
        catch {
            $interruptedProvenance = $false
        }
    }
    if ((-not $currentProvenance -and -not $legacyRestartProvenance -and -not $interruptedProvenance) -or $State.state_path -cne $StatePathValue) {
        New-Failure "State repository provenance is invalid"
    }
    if ($State.session_binding_sha256 -cne (Get-StateSessionBindingDigest -State $State)) {
        New-Failure "State session binding digest is invalid"
    }
}

function Build-EvidencePayload {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State,

        [Parameter(Mandatory = $true)]
        [string]$Executable,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,

        [Parameter(Mandatory = $true)]
        [string]$TranscriptPath
    )

    return [ordered]@{
        classification          = "VALID_FRESH_CALIBRATION"
        session_id              = $State.session_id
        utc_start               = $State.utc_start
        behavior_sha            = $BehaviorBaseline
        evidence_path           = $State.artifacts.evidence.path
        transcript_path         = $TranscriptPath
        transcript_sha256       = Get-Sha256Hex -Path $TranscriptPath
        transcript_size         = [int64](Get-Item -LiteralPath $TranscriptPath).Length
        calibration_executable  = $Executable
        calibration_arguments   = @($Arguments)
        state_path              = $State.state_path
        state_session_binding   = $State.session_binding_sha256
        pre_calibration         = $State.pre_calibration
        post_calibration        = $State.post_calibration
        current_identities      = $State.post_calibration
    }
}

function Get-CalibrationTranscriptHeaderLines {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State,

        [Parameter(Mandatory = $true)]
        [string]$Executable,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    return @(
        "PACKET2N_R5_SESSION_ID=$($State.session_id)",
        "PACKET2N_R5_SESSION_STARTED_UTC=$($State.utc_start)",
        "PACKET2N_R5_BEHAVIOR_SHA=$($State.behavior_sha)",
        "PACKET2N_R5_CALIBRATION_EXECUTABLE=$Executable",
        "PACKET2N_R5_CALIBRATION_ARGS_JSON=$(ConvertTo-CompactJson -Value @($Arguments))"
    )
}

function Assert-ExactCommand {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Actual,

        [Parameter(Mandatory = $true)]
        [hashtable]$Expected,

        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    if ($Actual.executable -cne $Expected.executable) {
        New-Failure $Message
    }
    $actualArguments = @($Actual.arguments)
    $expectedArguments = @($Expected.arguments)
    if ($actualArguments.Count -ne $expectedArguments.Count) {
        New-Failure $Message
    }
    for ($index = 0; $index -lt $expectedArguments.Count; $index++) {
        if ([string]$actualArguments[$index] -cne [string]$expectedArguments[$index]) {
            New-Failure $Message
        }
    }
}

function Assert-TranscriptSemantics {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State,

        [Parameter(Mandatory = $true)]
        [hashtable]$Command,

        [string]$TranscriptPath
    )

    $transcript = $State.artifacts.transcript
    if ($null -eq $transcript) {
        New-Failure "Transcript semantic validation failed: transcript is required"
    }
    $actualTranscriptPath = if ([string]::IsNullOrEmpty($TranscriptPath)) { [string]$transcript.path } else { $TranscriptPath }
    if ((Get-Sha256Hex -Path $actualTranscriptPath) -cne $transcript.sha256) {
        New-Failure "Transcript hash mismatch"
    }
    $actualSize = [int64](Get-Item -LiteralPath $actualTranscriptPath).Length
    if ($actualSize -ne [int64]$transcript.size -or $actualSize -le 0) {
        New-Failure "Transcript semantic validation failed: size mismatch"
    }
    $lines = @(Get-Content -LiteralPath $actualTranscriptPath)
    $expectedHeader = @(Get-CalibrationTranscriptHeaderLines -State $State -Executable $Command.executable -Arguments @($Command.arguments))
    if ($lines.Count -lt ($expectedHeader.Count + 1)) {
        New-Failure "Transcript semantic validation failed: transcript is incomplete"
    }
    for ($index = 0; $index -lt $expectedHeader.Count; $index++) {
        if ([string]$lines[$index] -cne [string]$expectedHeader[$index]) {
            New-Failure "Transcript semantic validation failed: header mismatch"
        }
    }
    $terminators = @($lines | Where-Object { ([string]$_).StartsWith("CALIBRATION_EXIT_CODE=", [System.StringComparison]::Ordinal) })
    if ($terminators.Count -ne 1 -or [string]$terminators[0] -cne "CALIBRATION_EXIT_CODE=0" -or [string]$lines[-1] -cne "CALIBRATION_EXIT_CODE=0") {
        New-Failure "Transcript semantic validation failed: success terminator mismatch"
    }
}

function Assert-EvidenceSemantics {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan
    )

    if ($State.classification -cne "VALID_FRESH_CALIBRATION" -or $State.completed_stages -cnotcontains "Calibrate") {
        New-Failure "Evidence semantic validation failed: state is not a completed fresh-calibration state"
    }
    $evidence = $State.artifacts.evidence
    if ($null -eq $evidence) {
        New-Failure "Evidence is required before mapping"
    }
    if ((Get-Sha256Hex -Path $evidence.path) -cne $evidence.sha256) {
        New-Failure "Evidence hash mismatch"
    }
    $evidenceSize = [int64](Get-Item -LiteralPath $evidence.path).Length
    if ($evidenceSize -ne [int64]$evidence.size -or $evidenceSize -le 0) {
        New-Failure "Evidence semantic validation failed: size mismatch"
    }
    $evidencePayload = Read-JsonFile -Path $evidence.path
    $transcript = $State.artifacts.transcript
    if ($null -eq $transcript) {
        New-Failure "Transcript is required before mapping"
    }
    $expectedKeys = @("classification", "session_id", "utc_start", "behavior_sha", "evidence_path", "transcript_path", "transcript_sha256", "transcript_size", "calibration_executable", "calibration_arguments", "state_path", "state_session_binding", "pre_calibration", "post_calibration", "current_identities")
    $actualKeys = @($evidencePayload.Keys)
    if ($actualKeys.Count -ne $expectedKeys.Count) {
        New-Failure "Evidence semantic validation failed"
    }
    foreach ($key in $expectedKeys) {
        if ($actualKeys -cnotcontains $key) {
            New-Failure "Evidence semantic validation failed"
        }
    }
    foreach ($key in $actualKeys) {
        if ($expectedKeys -cnotcontains [string]$key) {
            New-Failure "Evidence semantic validation failed"
        }
    }
    if ($evidencePayload.classification -cne "VALID_FRESH_CALIBRATION" -or
        $evidencePayload.session_id -cne $State.session_id -or
        $evidencePayload.utc_start -cne $State.utc_start -or
        $evidencePayload.behavior_sha -cne $BehaviorBaseline) {
        New-Failure "Evidence semantic validation failed"
    }
    $expectedCommand = Build-StageCommand -StageName "Calibrate" -Plan $Plan
    Assert-ExactCommand -Actual $State.stages.Calibrate.native -Expected $expectedCommand -Message "Evidence semantic validation failed: persisted calibration command mismatch"
    Assert-TranscriptSemantics -State $State -Command $expectedCommand
    if ($evidencePayload.evidence_path -cne $evidence.path -or
        $evidencePayload.transcript_path -cne $transcript.path -or
        $evidencePayload.transcript_sha256 -cne $transcript.sha256 -or
        [int64]$evidencePayload.transcript_size -ne [int64]$transcript.size) {
        New-Failure "Evidence semantic validation failed"
    }
    if ($evidencePayload.state_path -cne $State.state_path -or $evidencePayload.state_session_binding -cne $State.session_binding_sha256) {
        New-Failure "Evidence semantic validation failed"
    }
    $evidenceCommand = [ordered]@{
        executable = $evidencePayload.calibration_executable
        arguments  = @($evidencePayload.calibration_arguments)
    }
    Assert-ExactCommand -Actual $evidenceCommand -Expected $expectedCommand -Message "Evidence semantic validation failed: calibration command mismatch"
    Assert-PreCalibrationMatchesOriginals -State $State -Plan $Plan
    Assert-ExactValue -Actual $evidencePayload.pre_calibration -Expected $State.pre_calibration -Message "Evidence semantic validation failed: pre-calibration identity mismatch"
    Assert-ExactValue -Actual $evidencePayload.post_calibration -Expected $State.post_calibration -Message "Evidence semantic validation failed: post-calibration identity mismatch"
    Assert-PostCalibrationFreshness -State $State -Plan $Plan
    $current = Get-CurrentIdentities -Plan $Plan
    if (-not (Test-ExactValue -Actual $current -Expected $State.post_calibration)) {
        New-Failure "Current calibration does not match evidence"
    }
    Assert-ExactValue -Actual $evidencePayload.current_identities -Expected $current -Message "Evidence semantic validation failed: current identity mismatch"
}

function Assert-EvidenceAndCalibrationStillMatch {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan
    )

    Assert-EvidenceSemantics -State $State -Plan $Plan
}

function Assert-ArchivedEvidenceSemantics {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [string]$EvidencePath,

        [Parameter(Mandatory = $true)]
        [string]$TranscriptPath
    )

    if ((Get-Sha256Hex -Path $EvidencePath) -cne $State.artifacts.evidence.sha256 -or
        [int64](Get-Item -LiteralPath $EvidencePath).Length -ne [int64]$State.artifacts.evidence.size) {
        New-Failure "Archived evidence identity does not match the archived state"
    }
    $payload = Read-JsonFile -Path $EvidencePath
    Assert-ExactKeySet `
        -Value $payload `
        -ExpectedKeys @(
            "classification", "session_id", "utc_start", "behavior_sha", "evidence_path",
            "transcript_path", "transcript_sha256", "transcript_size", "calibration_executable",
            "calibration_arguments", "state_path", "state_session_binding", "pre_calibration",
            "post_calibration", "current_identities"
        ) `
        -Message "Archived evidence schema is invalid"
    $expectedCommand = Build-StageCommand -StageName "Calibrate" -Plan $Plan
    Assert-ExactCommand -Actual $State.stages.Calibrate.native -Expected $expectedCommand -Message "Archived state calibration command is invalid"
    $evidenceCommand = [ordered]@{
        executable = $payload.calibration_executable
        arguments  = @($payload.calibration_arguments)
    }
    Assert-ExactCommand -Actual $evidenceCommand -Expected $expectedCommand -Message "Archived evidence calibration command is invalid"
    Assert-TranscriptSemantics -State $State -Command $expectedCommand -TranscriptPath $TranscriptPath
    if ($payload.classification -cne "VALID_FRESH_CALIBRATION" -or
        $payload.session_id -cne $State.session_id -or
        $payload.utc_start -cne $State.utc_start -or
        $payload.behavior_sha -cne $BehaviorBaseline -or
        $payload.evidence_path -cne $State.artifacts.evidence.path -or
        $payload.transcript_path -cne $State.artifacts.transcript.path -or
        $payload.transcript_sha256 -cne $State.artifacts.transcript.sha256 -or
        [int64]$payload.transcript_size -ne [int64]$State.artifacts.transcript.size -or
        $payload.state_path -cne $State.state_path -or
        $payload.state_session_binding -cne $State.session_binding_sha256 -or
        -not (Test-ExactValue -Actual $payload.pre_calibration -Expected $State.pre_calibration) -or
        -not (Test-ExactValue -Actual $payload.post_calibration -Expected $State.post_calibration) -or
        -not (Test-ExactValue -Actual $payload.current_identities -Expected $State.post_calibration)) {
        New-Failure "Archived evidence semantics do not match the archived state"
    }
}

function Assert-RestartArchivedStateSchema {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State
    )

    Assert-ExactKeySet `
        -Value $State `
        -ExpectedKeys @(
            "schema_version", "runner_version", "packet_identity", "session_id", "utc_start",
            "behavior_sha", "repo_head", "expected_branch", "runner_sha", "state_path", "ports",
            "leader_id", "arm_profile", "classification", "completed_stages", "failed_stages",
            "summaries", "final_result", "next_stage", "session_binding_sha256", "stages",
            "pre_calibration", "post_calibration", "artifacts"
        ) `
        -Message "Rejected archive state schema is invalid"
    Assert-ExactKeySet -Value $State.ports -ExpectedKeys @("physical_left", "logical_left", "physical_right", "logical_right") -Message "Rejected archive state port schema is invalid"
    Assert-ExactKeySet -Value $State.stages -ExpectedKeys @("Calibrate", "MapLeft", "MapRight", "Verify") -Message "Rejected archive state stage schema is invalid"
    foreach ($stageName in @("MapLeft", "MapRight", "Verify")) {
        $pendingStage = $State.stages[$stageName]
        if ($pendingStage.result -cne "pending" -or
            $pendingStage.native.attempted -ne $false -or
            $pendingStage.native.launched -ne $false -or
            $null -ne $pendingStage.native.real_exit_code -or
            $null -ne $pendingStage.native.executable -or
            @($pendingStage.native.arguments).Count -ne 0) {
            New-Failure "Rejected archive state $stageName native truth is invalid"
        }
    }
    Assert-ExactKeySet -Value $State.summaries -ExpectedKeys @("Calibrate") -Message "Rejected archive state summary schema is invalid"
    if ($State.summaries.Calibrate -isnot [string] -or [string]::IsNullOrWhiteSpace([string]$State.summaries.Calibrate) -or
        $null -ne $State.final_result -or
        -not (Test-IsUtcTimestamp -Value $State.utc_start) -or
        -not (Test-IsSha256Hex -Value $State.runner_sha) -or
        -not (Test-IsSha256Hex -Value $State.session_binding_sha256)) {
        New-Failure "Rejected archive state scalar schema is invalid"
    }
    foreach ($calibrationSetName in @("pre_calibration", "post_calibration")) {
        $calibrationSet = $State[$calibrationSetName]
        Assert-ExactKeySet -Value $calibrationSet -ExpectedKeys @("left", "right") -Message "Rejected archive state $calibrationSetName schema is invalid"
        foreach ($side in @("left", "right")) {
            $identity = $calibrationSet[$side]
            Assert-ExactKeySet -Value $identity -ExpectedKeys @("path", "sha256", "size", "mtime_utc", "calibration") -Message "Rejected archive state $calibrationSetName $side identity schema is invalid"
            if (-not (Test-IsSha256Hex -Value $identity.sha256) -or
                -not (Test-IsJsonInteger -Value $identity.size) -or
                [int64]$identity.size -le 0 -or
                -not (Test-IsUtcTimestamp -Value $identity.mtime_utc)) {
                New-Failure "Rejected archive state $calibrationSetName $side identity is invalid"
            }
            Assert-CalibrationSchema -Calibration $identity.calibration -Label "archived state $calibrationSetName $side"
        }
    }
    Assert-ExactKeySet -Value $State.artifacts -ExpectedKeys @("transcript", "evidence", "map_left", "map_right") -Message "Rejected archive state artifact schema is invalid"
    foreach ($artifactName in @("transcript", "evidence")) {
        $artifact = $State.artifacts[$artifactName]
        Assert-ExactKeySet -Value $artifact -ExpectedKeys @("path", "sha256", "size") -Message "Rejected archive state $artifactName schema is invalid"
        if (-not (Test-IsSha256Hex -Value $artifact.sha256) -or
            -not (Test-IsJsonInteger -Value $artifact.size) -or
            [int64]$artifact.size -le 0) {
            New-Failure "Rejected archive state $artifactName identity is invalid"
        }
    }
    foreach ($artifactName in @("map_left", "map_right")) {
        $artifact = $State.artifacts[$artifactName]
        Assert-ExactKeySet -Value $artifact -ExpectedKeys @("path", "sha256") -Message "Rejected archive state $artifactName schema is invalid"
        if (-not [string]::IsNullOrEmpty([string]$artifact.sha256)) {
            New-Failure "Rejected archive state contains a map artifact identity"
        }
    }
}

function Get-RestartJournalPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$StatePathValue
    )

    return "$StatePathValue.restart-calibration.json"
}

function Get-ActiveCalibrationDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Plan
    )

    $leftPath = [System.IO.Path]::GetFullPath([string]$Plan.calibration.left.path)
    $rightPath = [System.IO.Path]::GetFullPath([string]$Plan.calibration.right.path)
    $leftDirectory = [System.IO.Path]::GetDirectoryName($leftPath)
    $rightDirectory = [System.IO.Path]::GetDirectoryName($rightPath)
    if ([string]::IsNullOrEmpty($leftDirectory) -or -not $leftDirectory.Equals($rightDirectory, [System.StringComparison]::OrdinalIgnoreCase)) {
        New-Failure "RestartCalibration requires both active calibration files in one directory"
    }
    if ([System.IO.Path]::GetFileName($leftPath) -ceq [System.IO.Path]::GetFileName($rightPath)) {
        New-Failure "RestartCalibration calibration filenames must be distinct"
    }
    return $leftDirectory
}

function Assert-RestartPathHasNoReparsePoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Boundary,

        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    $currentPath = [System.IO.Path]::GetFullPath($Path)
    if (-not $currentPath.Equals([System.IO.Path]::GetPathRoot($currentPath), [System.StringComparison]::OrdinalIgnoreCase)) {
        $currentPath = $currentPath.TrimEnd('\', '/')
    }
    $boundaryPath = [System.IO.Path]::GetFullPath($Boundary)
    if (-not $boundaryPath.Equals([System.IO.Path]::GetPathRoot($boundaryPath), [System.StringComparison]::OrdinalIgnoreCase)) {
        $boundaryPath = $boundaryPath.TrimEnd('\', '/')
    }
    if (-not (Test-PathIsSameOrDescendant -Path $currentPath -Root $boundaryPath)) {
        New-Failure "RestartCalibration $Label escaped its validated boundary: $currentPath is not under $boundaryPath"
    }
    while ($true) {
        if (Test-Path -LiteralPath $currentPath) {
            $item = Get-Item -LiteralPath $currentPath -Force
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                New-Failure "RestartCalibration $Label contains a reparse point path component: $currentPath"
            }
        }
        if ($currentPath.Equals($boundaryPath, [System.StringComparison]::OrdinalIgnoreCase)) {
            break
        }
        $parent = [System.IO.Directory]::GetParent($currentPath)
        if ($null -eq $parent) {
            New-Failure "RestartCalibration $Label could not reach its validated boundary"
        }
        $currentPath = $parent.FullName
        if (-not $currentPath.Equals([System.IO.Path]::GetPathRoot($currentPath), [System.StringComparison]::OrdinalIgnoreCase)) {
            $currentPath = $currentPath.TrimEnd('\', '/')
        }
    }
}

function Assert-RestartPathConfined {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Root,

        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    $resolvedPath = [System.IO.Path]::GetFullPath($Path)
    $resolvedRoot = [System.IO.Path]::GetFullPath($Root)
    if (-not (Test-PathIsSameOrDescendant -Path $resolvedPath -Root $resolvedRoot)) {
        New-Failure "RestartCalibration $Label escaped its validated root"
    }
    $volumeRoot = [System.IO.Path]::GetPathRoot($resolvedRoot)
    Assert-RestartPathHasNoReparsePoint -Path $resolvedRoot -Boundary $volumeRoot -Label "$Label root"
    Assert-RestartPathHasNoReparsePoint -Path $resolvedPath -Boundary $resolvedRoot -Label $Label
}

function Get-RestartNearestExistingPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $candidate = [System.IO.Path]::GetFullPath($Path)
    if (-not $candidate.Equals([System.IO.Path]::GetPathRoot($candidate), [System.StringComparison]::OrdinalIgnoreCase)) {
        $candidate = $candidate.TrimEnd('\', '/')
    }
    while (-not (Test-Path -LiteralPath $candidate)) {
        $parent = [System.IO.Directory]::GetParent($candidate)
        if ($null -eq $parent) {
            New-Failure "RestartCalibration could not resolve an existing volume ancestor for $Path"
        }
        $candidate = $parent.FullName
        if (-not $candidate.Equals([System.IO.Path]::GetPathRoot($candidate), [System.StringComparison]::OrdinalIgnoreCase)) {
            $candidate = $candidate.TrimEnd('\', '/')
        }
    }
    return $candidate
}

function Initialize-RestartNativePaths {
    if ($null -eq ("Packet2nR5RestartNativePaths" -as [type])) {
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class Packet2nR5RestartNativePaths {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern bool GetVolumePathNameW(string fileName, StringBuilder volumePathName, uint bufferLength);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern bool GetVolumeNameForVolumeMountPointW(string volumeMountPoint, StringBuilder volumeName, uint bufferLength);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern bool MoveFileExW(string existingFileName, string newFileName, uint flags);
}
"@
    }
}

function Get-RestartVolumeIdentity {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    Initialize-RestartNativePaths
    $existingPath = Get-RestartNearestExistingPath -Path $Path
    $volumePath = [System.Text.StringBuilder]::new(1024)
    if (-not [Packet2nR5RestartNativePaths]::GetVolumePathNameW($existingPath, $volumePath, [uint32]$volumePath.Capacity)) {
        New-Failure "RestartCalibration could not resolve the actual volume for $Path"
    }
    $volumeName = [System.Text.StringBuilder]::new(1024)
    if (-not [Packet2nR5RestartNativePaths]::GetVolumeNameForVolumeMountPointW($volumePath.ToString(), $volumeName, [uint32]$volumeName.Capacity)) {
        New-Failure "RestartCalibration could not resolve the actual volume identity for $Path"
    }
    return $volumeName.ToString().TrimEnd('\').ToUpperInvariant()
}

function Assert-RestartSameVolume {
    param(
        [Parameter(Mandatory = $true)]
        [string]$First,

        [Parameter(Mandatory = $true)]
        [string]$Second,

        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    $firstVolume = Get-RestartVolumeIdentity -Path $First
    $secondVolume = Get-RestartVolumeIdentity -Path $Second
    if ($firstVolume -cne $secondVolume) {
        New-Failure "RestartCalibration $Label must remain on one actual volume"
    }
}

function ConvertTo-RestartExtendedPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $resolved = [System.IO.Path]::GetFullPath($Path)
    if ($resolved.StartsWith('\\', [System.StringComparison]::Ordinal)) {
        return "\\?\UNC\$($resolved.Substring(2))"
    }
    return "\\?\$resolved"
}

function Invoke-RestartDurableNamespaceMove {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,

        [Parameter(Mandatory = $true)]
        [string]$Destination,

        [Parameter(Mandatory = $true)]
        [string]$Label,

        [switch]$ReplaceExisting
    )

    if (-not (Test-Path -LiteralPath $Source)) {
        New-Failure "RestartCalibration $Label source is missing: $Source"
    }
    $sourceItem = Get-Item -LiteralPath $Source -Force
    if (($sourceItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
        ($sourceItem -isnot [System.IO.FileInfo] -and $sourceItem -isnot [System.IO.DirectoryInfo])) {
        New-Failure "RestartCalibration $Label source is not a regular file or directory: $Source"
    }
    $destinationExists = Test-Path -LiteralPath $Destination
    if ($destinationExists) {
        $destinationItem = Get-Item -LiteralPath $Destination -Force
        if (($destinationItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            New-Failure "RestartCalibration $Label destination is a reparse point: $Destination"
        }
        if (-not $ReplaceExisting) {
            New-Failure "RestartCalibration $Label destination already exists: $Destination"
        }
        if ($sourceItem -isnot [System.IO.FileInfo] -or $destinationItem -isnot [System.IO.FileInfo]) {
            New-Failure "RestartCalibration $Label replacement requires regular files"
        }
    }
    elseif ($ReplaceExisting -and $sourceItem -isnot [System.IO.FileInfo]) {
        New-Failure "RestartCalibration $Label replacement requires a regular file source"
    }
    Assert-RestartSameVolume -First $Source -Second $Destination -Label $Label
    Initialize-RestartNativePaths
    [uint32]$flags = 0x00000008
    if ($ReplaceExisting) {
        $flags = $flags -bor 0x00000001
    }
    $sourceNative = ConvertTo-RestartExtendedPath -Path $Source
    $destinationNative = ConvertTo-RestartExtendedPath -Path $Destination
    if (-not [Packet2nR5RestartNativePaths]::MoveFileExW($sourceNative, $destinationNative, $flags)) {
        $errorCode = [System.Runtime.InteropServices.Marshal]::GetLastWin32Error()
        $errorMessage = [System.ComponentModel.Win32Exception]::new($errorCode).Message
        New-Failure "RestartCalibration $Label namespace publication failed with Windows error $errorCode ($errorMessage): $Source -> $Destination"
    }
}

function Assert-RestartMoveSafe {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,

        [Parameter(Mandatory = $true)]
        [string]$Destination,

        [Parameter(Mandatory = $true)]
        [string]$SourceRoot,

        [Parameter(Mandatory = $true)]
        [string]$DestinationRoot,

        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    Assert-RestartPathConfined -Path $Source -Root $SourceRoot -Label "$Label source"
    Assert-RestartPathConfined -Path $Destination -Root $DestinationRoot -Label "$Label destination"
    Assert-RestartSameVolume -First $Source -Second $Destination -Label $Label
}

function Get-RestartTransactionPaths {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [string]$StatePathValue,

        [Parameter(Mandatory = $true)]
        [string]$SessionId
    )

    if (-not $Plan.ContainsKey("rejected_archive_root") -or [string]::IsNullOrWhiteSpace([string]$Plan.rejected_archive_root)) {
        New-Failure "RestartCalibration rejected archive root is missing"
    }
    $activeDirectory = Get-ActiveCalibrationDirectory -Plan $Plan
    $activeParent = [System.IO.Path]::GetDirectoryName($activeDirectory)
    $archivePath = Join-Path ([string]$Plan.rejected_archive_root) "packet2n-r5-rejected-$SessionId"
    $paths = [ordered]@{
        journal          = Get-RestartJournalPath -StatePathValue $StatePathValue
        archive          = $archivePath
        archive_staging  = "$archivePath.staging"
        active           = $activeDirectory
        staged_original  = Join-Path $activeParent ".packet2n-r5-original-$SessionId"
        rollback         = Join-Path $activeParent ".packet2n-r5-rejected-$SessionId"
    }
    $calibrationRoot = [System.IO.Path]::GetFullPath([string]$Plan.calibration_root)
    $stateRoot = [System.IO.Path]::GetFullPath([string]$Plan.state_root)
    $archiveRoot = [System.IO.Path]::GetFullPath([string]$Plan.rejected_archive_root)
    Assert-RestartPathConfined -Path $paths.active -Root $calibrationRoot -Label "active calibration path"
    Assert-RestartPathConfined -Path $paths.staged_original -Root $activeParent -Label "staged-original path"
    Assert-RestartPathConfined -Path $paths.rollback -Root $activeParent -Label "rollback path"
    Assert-RestartPathConfined -Path $StatePathValue -Root $stateRoot -Label "source state path"
    Assert-RestartPathConfined -Path $paths.journal -Root $stateRoot -Label "journal path"
    Assert-RestartPathConfined -Path $paths.archive -Root $archiveRoot -Label "archive path"
    Assert-RestartPathConfined -Path $paths.archive_staging -Root $archiveRoot -Label "archive staging path"
    Assert-RestartSameVolume -First $paths.active -Second $paths.staged_original -Label "active/staged-original directory swap"
    Assert-RestartSameVolume -First $paths.active -Second $paths.rollback -Label "active/rollback directory swap"
    Assert-RestartSameVolume -First $paths.active -Second $paths.archive -Label "fresh-pair retirement"
    Assert-RestartSameVolume -First $StatePathValue -Second $paths.archive -Label "state retirement"
    foreach ($path in $paths.Values) {
        Assert-TestModePath -Plan $Plan -Path ([string]$path)
    }
    return $paths
}

function Get-FileTimestampUtc {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    return Format-UtcTimestamp -Value (Get-Item -LiteralPath $Path).LastWriteTimeUtc
}

function Test-SnapshotMatchesIdentity {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Snapshot,

        [Parameter(Mandatory = $true)]
        [hashtable]$Identity
    )

    return (
        $Snapshot.sha256 -ceq $Identity.sha256 -and
        [int64]$Snapshot.size -eq [int64]$Identity.size -and
        $Snapshot.mtime_utc -ceq $Identity.mtime_utc -and
        (Test-ExactValue -Actual $Snapshot.calibration -Expected $Identity.calibration)
    )
}

function Get-PairDirectoryLayout {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Directory,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [hashtable]$FreshIdentities
    )

    if (-not (Test-Path -LiteralPath $Directory)) {
        return "missing"
    }
    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) {
        return "unrecognized"
    }
    $directoryItem = Get-Item -LiteralPath $Directory -Force
    if (($directoryItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        return "unrecognized"
    }
    $entries = @(Get-ChildItem -LiteralPath $Directory -Force)
    $expectedNames = @(
        [System.IO.Path]::GetFileName([string]$Plan.calibration.left.path),
        [System.IO.Path]::GetFileName([string]$Plan.calibration.right.path)
    )
    if ($entries.Count -ne 2) {
        return "unrecognized"
    }
    foreach ($entry in $entries) {
        if ($entry -isnot [System.IO.FileInfo] -or
            ($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            $expectedNames -cnotcontains $entry.Name) {
            return "unrecognized"
        }
    }

    $allFresh = $true
    $allOriginal = $true
    foreach ($side in @("left", "right")) {
        $name = [System.IO.Path]::GetFileName([string]$Plan.calibration[$side].path)
        try {
            $snapshot = Get-CalibrationSnapshot -Path (Join-Path $Directory $name) -Label "RestartCalibration $side pair"
        }
        catch {
            return "unrecognized"
        }
        if (-not (Test-SnapshotMatchesIdentity -Snapshot $snapshot -Identity $FreshIdentities[$side])) {
            $allFresh = $false
        }
        $backupCalibration = Read-JsonFile -Path $Plan.calibration[$side].backup_path
        $originalIdentity = [ordered]@{
            sha256      = $Plan.calibration[$side].backup_sha256
            size        = [int64]$Plan.calibration[$side].backup_size
            mtime_utc   = $Plan.calibration[$side].source_mtime_utc
            calibration = $backupCalibration
        }
        if (-not (Test-SnapshotMatchesIdentity -Snapshot $snapshot -Identity $originalIdentity)) {
            $allOriginal = $false
        }
    }
    if ($allFresh -and -not $allOriginal) {
        return "fresh"
    }
    if ($allOriginal -and -not $allFresh) {
        return "original"
    }
    return "unrecognized"
}

function Copy-FilePreservingIdentity {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,

        [Parameter(Mandatory = $true)]
        [string]$Destination
    )

    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
        New-Failure "RestartCalibration source file is missing: $Source"
    }
    Assert-PathMissing -Path $Destination
    Ensure-ParentDirectory -Path $Destination
    $sourceItem = Get-Item -LiteralPath $Source -Force
    if (($sourceItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        New-Failure "RestartCalibration refuses reparse point source: $Source"
    }
    [System.IO.File]::Copy($Source, $Destination, $false)
    [System.IO.File]::SetLastWriteTimeUtc($Destination, $sourceItem.LastWriteTimeUtc)
    $destinationItem = Get-Item -LiteralPath $Destination -Force
    if (($destinationItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
        (Get-Sha256Hex -Path $Destination) -cne (Get-Sha256Hex -Path $Source) -or
        [int64]$destinationItem.Length -ne [int64]$sourceItem.Length -or
        (Format-UtcTimestamp -Value $destinationItem.LastWriteTimeUtc) -cne (Format-UtcTimestamp -Value $sourceItem.LastWriteTimeUtc)) {
        New-Failure "RestartCalibration copied file identity mismatch: $Destination"
    }
}

function Test-FileMatchesSourceIdentity {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,

        [Parameter(Mandatory = $true)]
        [string]$Destination
    )

    if (-not (Test-Path -LiteralPath $Destination -PathType Leaf)) {
        return $false
    }
    $sourceItem = Get-Item -LiteralPath $Source -Force
    $destinationItem = Get-Item -LiteralPath $Destination -Force
    return (
        ($destinationItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq 0 -and
        (Get-Sha256Hex -Path $Destination) -ceq (Get-Sha256Hex -Path $Source) -and
        [int64]$destinationItem.Length -eq [int64]$sourceItem.Length -and
        (Format-UtcTimestamp -Value $destinationItem.LastWriteTimeUtc) -ceq (Format-UtcTimestamp -Value $sourceItem.LastWriteTimeUtc)
    )
}

function Copy-RestartStagedFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,

        [Parameter(Mandatory = $true)]
        [string]$Destination,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan
    )

    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
        New-Failure "RestartCalibration source file is missing: $Source"
    }
    $sourceItem = Get-Item -LiteralPath $Source -Force
    if (($sourceItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        New-Failure "RestartCalibration refuses reparse point source: $Source"
    }
    Assert-TestModePath -Plan $Plan -Path $Destination
    Ensure-ParentDirectory -Path $Destination
    $tempPath = "$Destination.restart-copy.tmp"
    Assert-TestModePath -Plan $Plan -Path $tempPath
    foreach ($candidate in @($Destination, $tempPath)) {
        if (-not (Test-Path -LiteralPath $candidate)) {
            continue
        }
        $candidateItem = Get-Item -LiteralPath $candidate -Force
        if ($candidateItem -isnot [System.IO.FileInfo] -or
            ($candidateItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            New-Failure "RestartCalibration staged path is not a regular file: $candidate"
        }
    }
    if (Test-FileMatchesSourceIdentity -Source $Source -Destination $Destination) {
        if (Test-Path -LiteralPath $tempPath) {
            [System.IO.File]::Delete($tempPath)
        }
        return
    }
    if (Test-Path -LiteralPath $Destination) {
        [System.IO.File]::Delete($Destination)
    }
    if (Test-Path -LiteralPath $tempPath) {
        [System.IO.File]::Delete($tempPath)
    }
    [System.IO.File]::Copy($Source, $tempPath, $false)
    [System.IO.File]::SetLastWriteTimeUtc($tempPath, $sourceItem.LastWriteTimeUtc)
    if (-not (Test-FileMatchesSourceIdentity -Source $Source -Destination $tempPath)) {
        New-Failure "RestartCalibration staged copy identity mismatch: $tempPath"
    }
    Invoke-RestartDurableNamespaceMove -Source $tempPath -Destination $Destination -Label "staged-copy publication"
    if (-not (Test-FileMatchesSourceIdentity -Source $Source -Destination $Destination)) {
        New-Failure "RestartCalibration published staged copy identity mismatch: $Destination"
    }
}

function Get-ArchiveArtifactRecord {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,

        [Parameter(Mandatory = $true)]
        [string]$StagedPath,

        [Parameter(Mandatory = $true)]
        [string]$PublishedPath
    )

    return [ordered]@{
        source_path       = $Source
        archive_path      = $PublishedPath
        sha256            = Get-Sha256Hex -Path $StagedPath
        size              = [int64](Get-Item -LiteralPath $StagedPath).Length
        source_mtime_utc  = Get-FileTimestampUtc -Path $Source
        archive_mtime_utc = Get-FileTimestampUtc -Path $StagedPath
    }
}

function Save-RestartJournal {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [hashtable]$Journal,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan
    )

    Write-RestartJsonDurable -Path $Path -Value $Journal -Plan $Plan -Overwrite
}

function Write-RestartJsonDurable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        $Value,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [switch]$Overwrite,

        [string]$AfterFlushFailurePoint
    )

    Assert-TestModePath -Plan $Plan -Path $Path
    $durablePathRoot = [System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($Path))
    Assert-RestartPathHasNoReparsePoint -Path $Path -Boundary $durablePathRoot -Label "durable JSON path"
    Ensure-ParentDirectory -Path $Path
    if (Test-Path -LiteralPath $Path) {
        $pathItem = Get-Item -LiteralPath $Path -Force
        if ($pathItem -isnot [System.IO.FileInfo] -or
            ($pathItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            New-Failure "RestartCalibration durable JSON path is not a regular file: $Path"
        }
    }
    if ((-not $Overwrite) -and (Test-Path -LiteralPath $Path)) {
        New-Failure "Refusing to overwrite existing file: $Path"
    }
    $tempPath = "$Path.restart-durable.tmp"
    Assert-TestModePath -Plan $Plan -Path $tempPath
    if (Test-Path -LiteralPath $tempPath) {
        $tempItem = Get-Item -LiteralPath $tempPath -Force
        if ($tempItem -isnot [System.IO.FileInfo] -or
            ($tempItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            New-Failure "RestartCalibration durable temp path is not a regular file: $tempPath"
        }
        [System.IO.File]::Delete($tempPath)
    }
    $text = (ConvertTo-CanonicalJson -Value $Value) + [Environment]::NewLine
    $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($text)
    $stream = [System.IO.FileStream]::new(
        $tempPath,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None,
        4096,
        [System.IO.FileOptions]::WriteThrough
    )
    try {
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    }
    finally {
        $stream.Dispose()
    }
    if (-not [string]::IsNullOrEmpty($AfterFlushFailurePoint)) {
        Test-RestartFailurePoint -Plan $Plan -Point $AfterFlushFailurePoint
    }
    if (Test-Path -LiteralPath $Path) {
        if (-not $Overwrite) {
            New-Failure "Refusing to overwrite existing file: $Path"
        }
        Invoke-RestartDurableNamespaceMove -Source $tempPath -Destination $Path -Label "durable JSON replacement" -ReplaceExisting
    }
    else {
        Invoke-RestartDurableNamespaceMove -Source $tempPath -Destination $Path -Label "durable JSON publication"
    }
}

function New-RestartJournal {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [string]$StatePathValue,

        [Parameter(Mandatory = $true)]
        [hashtable]$Paths
    )

    $stateItem = Get-Item -LiteralPath $StatePathValue
    return [ordered]@{
        schema_version        = "1"
        transaction_type      = "packet2n-r5-restart-calibration"
        status                = "in_progress"
        phase                 = "initialized"
        reason                = $RestartRejectionReason
        session_id            = $State.session_id
        session_start_utc     = $State.utc_start
        state_binding_sha256  = $State.session_binding_sha256
        state_path            = $StatePathValue
        archive_path          = $Paths.archive
        archive_staging_path  = $Paths.archive_staging
        active_directory      = $Paths.active
        staged_original_path  = $Paths.staged_original
        rollback_path         = $Paths.rollback
        source_state          = [ordered]@{
            sha256    = Get-Sha256Hex -Path $StatePathValue
            size      = [int64]$stateItem.Length
            mtime_utc = Format-UtcTimestamp -Value $stateItem.LastWriteTimeUtc
        }
        source_fresh          = $State.post_calibration
        native_stage_truth    = $State.stages
        source_provenance     = [ordered]@{
            repo_head     = $State.repo_head
            runner_sha256 = $State.runner_sha
            behavior_sha  = $State.behavior_sha
        }
        recovery_provenance   = [ordered]@{
            repo_head     = $Plan.head
            runner_sha256 = Get-RunnerSha256
            behavior_sha  = $BehaviorBaseline
        }
        archive_record_sha256 = $null
    }
}

function Assert-RestartSourceEvidenceBindings {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan
    )

    $expectedCommand = Build-StageCommand -StageName "Calibrate" -Plan $Plan
    Assert-TranscriptSemantics -State $State -Command $expectedCommand
    $evidence = $State.artifacts.evidence
    if ($null -eq $evidence -or
        -not (Test-Path -LiteralPath $evidence.path -PathType Leaf) -or
        (Get-Sha256Hex -Path $evidence.path) -cne $evidence.sha256 -or
        [int64](Get-Item -LiteralPath $evidence.path).Length -ne [int64]$evidence.size) {
        New-Failure "RestartCalibration source evidence identity is invalid"
    }
    $payload = Read-JsonFile -Path $evidence.path
    Assert-ExactKeySet `
        -Value $payload `
        -ExpectedKeys @(
            "classification", "session_id", "utc_start", "behavior_sha", "evidence_path",
            "transcript_path", "transcript_sha256", "transcript_size", "calibration_executable",
            "calibration_arguments", "state_path", "state_session_binding", "pre_calibration",
            "post_calibration", "current_identities"
        ) `
        -Message "RestartCalibration source evidence schema is invalid"
    $evidenceCommand = [ordered]@{
        executable = $payload.calibration_executable
        arguments  = @($payload.calibration_arguments)
    }
    Assert-ExactCommand -Actual $evidenceCommand -Expected $expectedCommand -Message "RestartCalibration source evidence command is invalid"
    if ($payload.classification -cne "VALID_FRESH_CALIBRATION" -or
        $payload.session_id -cne $State.session_id -or
        $payload.utc_start -cne $State.utc_start -or
        $payload.behavior_sha -cne $BehaviorBaseline -or
        $payload.evidence_path -cne $evidence.path -or
        $payload.transcript_path -cne $State.artifacts.transcript.path -or
        $payload.transcript_sha256 -cne $State.artifacts.transcript.sha256 -or
        [int64]$payload.transcript_size -ne [int64]$State.artifacts.transcript.size -or
        $payload.state_path -cne $State.state_path -or
        $payload.state_session_binding -cne $State.session_binding_sha256 -or
        -not (Test-ExactValue -Actual $payload.pre_calibration -Expected $State.pre_calibration) -or
        -not (Test-ExactValue -Actual $payload.post_calibration -Expected $State.post_calibration) -or
        -not (Test-ExactValue -Actual $payload.current_identities -Expected $State.post_calibration)) {
        New-Failure "RestartCalibration source evidence binding is invalid"
    }
}

function Get-RestartAuthorityState {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Journal,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [string]$StatePathValue
    )

    $stateName = [System.IO.Path]::GetFileName($StatePathValue)
    $candidatePaths = @(
        $StatePathValue,
        (Join-Path $Journal.archive_staging_path (Join-Path "state-snapshot" $stateName)),
        (Join-Path $Journal.archive_path (Join-Path "state-snapshot" $stateName)),
        (Join-Path $Journal.archive_path (Join-Path "retired-state" $stateName))
    )
    foreach ($candidatePath in $candidatePaths) {
        $candidateRoot = if ((Test-PathIsSameOrDescendant -Path $candidatePath -Root $Plan.state_root)) {
            [string]$Plan.state_root
        }
        else {
            [string]$Plan.rejected_archive_root
        }
        Assert-RestartPathHasNoReparsePoint -Path $candidatePath -Boundary $candidateRoot -Label "preserved source state path"
    }
    $existingPaths = @($candidatePaths | Where-Object { Test-Path -LiteralPath $_ })
    if ($existingPaths.Count -eq 0) {
        New-Failure "RestartCalibration journal has no preserved source state"
    }
    $authorityState = $null
    $authorityStateIdentityPath = $null
    foreach ($candidatePath in $existingPaths) {
        $item = Get-Item -LiteralPath $candidatePath -Force
        if ($item -isnot [System.IO.FileInfo] -or
            ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            (Get-Sha256Hex -Path $candidatePath) -cne $Journal.source_state.sha256 -or
            [int64]$item.Length -ne [int64]$Journal.source_state.size -or
            (Format-UtcTimestamp -Value $item.LastWriteTimeUtc) -cne $Journal.source_state.mtime_utc) {
            New-Failure "RestartCalibration preserved source state identity is invalid"
        }
        $candidateState = Read-JsonFile -Path $candidatePath
        if ($null -eq $authorityState) {
            $authorityState = $candidateState
            $authorityStateIdentityPath = $candidatePath
        }
        elseif (-not (Test-ExactValue -Actual $candidateState -Expected $authorityState)) {
            New-Failure "RestartCalibration preserved source state copies disagree"
        }
    }
    $issues = @(Get-StateValidationIssues -State $authorityState -Plan $Plan)
    if ($issues.Count -gt 0) {
        New-Failure ("RestartCalibration source state schema is invalid: " + ($issues -join ", "))
    }
    Assert-StateIdentity -State $authorityState
    Assert-StateProvenance `
        -State $authorityState `
        -StatePathValue $StatePathValue `
        -Plan $Plan `
        -AllowRestartCandidate `
        -StateIdentityPath $authorityStateIdentityPath
    if ($authorityState.classification -cne "VALID_FRESH_CALIBRATION" -or
        $authorityState.next_stage -cne "MapLeft" -or
        @($authorityState.completed_stages).Count -ne 1 -or
        [string]$authorityState.completed_stages[0] -cne "Calibrate" -or
        @($authorityState.failed_stages).Count -ne 0 -or
        $authorityState.stages.MapLeft.result -cne "pending" -or
        $authorityState.stages.MapRight.result -cne "pending" -or
        $authorityState.stages.Verify.result -cne "pending" -or
        -not [string]::IsNullOrEmpty([string]$authorityState.artifacts.map_left.sha256) -or
        -not [string]::IsNullOrEmpty([string]$authorityState.artifacts.map_right.sha256) -or
        (Test-Path -LiteralPath $authorityState.artifacts.map_left.path) -or
        (Test-Path -LiteralPath $authorityState.artifacts.map_right.path)) {
        New-Failure "RestartCalibration journal source state is not the exact pre-map candidate"
    }
    Assert-RestartSourceEvidenceBindings -State $authorityState -Plan $Plan
    return $authorityState
}

function Assert-RestartJournal {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Journal,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [string]$StatePathValue
    )

    $required = @(
        "schema_version", "transaction_type", "status", "phase", "reason", "session_id",
        "session_start_utc", "state_binding_sha256", "state_path", "archive_path",
        "archive_staging_path", "active_directory", "staged_original_path", "rollback_path",
        "source_state", "source_fresh", "native_stage_truth", "source_provenance",
        "recovery_provenance", "archive_record_sha256"
    )
    if (@($Journal.Keys).Count -ne $required.Count) {
        New-Failure "RestartCalibration journal schema is invalid"
    }
    foreach ($key in $required) {
        if (@($Journal.Keys) -cnotcontains $key) {
            New-Failure "RestartCalibration journal schema is invalid"
        }
    }
    if ($Journal.schema_version -cne "1" -or
        $Journal.transaction_type -cne "packet2n-r5-restart-calibration" -or
        $Journal.status -cne "in_progress" -or
        $Journal.reason -cne $RestartRejectionReason -or
        $Journal.state_path -cne $StatePathValue -or
        [string]::IsNullOrWhiteSpace([string]$Journal.session_id)) {
        New-Failure "RestartCalibration journal identity is invalid"
    }
    if (@(
        "initialized", "archive_staged", "archive_published", "active_withdrawn",
        "original_activated", "fresh_pair_retired", "state_retired"
    ) -cnotcontains [string]$Journal.phase -or
        -not (Test-IsUtcTimestamp -Value $Journal.session_start_utc) -or
        -not (Test-IsSha256Hex -Value $Journal.state_binding_sha256)) {
        New-Failure "RestartCalibration journal phase or source identity is invalid"
    }
    Assert-ExactKeySet -Value $Journal.source_state -ExpectedKeys @("sha256", "size", "mtime_utc") -Message "RestartCalibration journal source-state schema is invalid"
    if (-not (Test-IsSha256Hex -Value $Journal.source_state.sha256) -or
        -not (Test-IsJsonInteger -Value $Journal.source_state.size) -or
        [int64]$Journal.source_state.size -le 0 -or
        -not (Test-IsUtcTimestamp -Value $Journal.source_state.mtime_utc)) {
        New-Failure "RestartCalibration journal source-state identity is invalid"
    }
    Assert-ExactKeySet -Value $Journal.source_fresh -ExpectedKeys @("left", "right") -Message "RestartCalibration journal fresh-pair schema is invalid"
    foreach ($side in @("left", "right")) {
        Assert-ExactKeySet `
            -Value $Journal.source_fresh[$side] `
            -ExpectedKeys @("path", "sha256", "size", "mtime_utc", "calibration") `
            -Message "RestartCalibration journal fresh-pair schema is invalid"
        if (-not (Test-IsSha256Hex -Value $Journal.source_fresh[$side].sha256) -or
            -not (Test-IsJsonInteger -Value $Journal.source_fresh[$side].size) -or
            [int64]$Journal.source_fresh[$side].size -le 0 -or
            -not (Test-IsUtcTimestamp -Value $Journal.source_fresh[$side].mtime_utc)) {
            New-Failure "RestartCalibration journal fresh-pair identity is invalid"
        }
        Assert-CalibrationSchema -Calibration $Journal.source_fresh[$side].calibration -Label "RestartCalibration journal $side fresh calibration"
    }
    Assert-ExactKeySet -Value $Journal.native_stage_truth -ExpectedKeys @("Calibrate", "MapLeft", "MapRight", "Verify") -Message "RestartCalibration journal native-stage schema is invalid"
    Assert-ExactKeySet -Value $Journal.source_provenance -ExpectedKeys @("repo_head", "runner_sha256", "behavior_sha") -Message "RestartCalibration journal source provenance schema is invalid"
    Assert-ExactKeySet -Value $Journal.recovery_provenance -ExpectedKeys @("repo_head", "runner_sha256", "behavior_sha") -Message "RestartCalibration journal recovery provenance schema is invalid"
    if ($null -ne $Journal.archive_record_sha256 -and -not (Test-IsSha256Hex -Value $Journal.archive_record_sha256)) {
        New-Failure "RestartCalibration journal archive-record identity is invalid"
    }
    $expectedPaths = Get-RestartTransactionPaths -Plan $Plan -StatePathValue $StatePathValue -SessionId ([string]$Journal.session_id)
    foreach ($binding in @(
        [ordered]@{ actual = $Journal.archive_path; expected = $expectedPaths.archive },
        [ordered]@{ actual = $Journal.archive_staging_path; expected = $expectedPaths.archive_staging },
        [ordered]@{ actual = $Journal.active_directory; expected = $expectedPaths.active },
        [ordered]@{ actual = $Journal.staged_original_path; expected = $expectedPaths.staged_original },
        [ordered]@{ actual = $Journal.rollback_path; expected = $expectedPaths.rollback }
    )) {
        if ([string]$binding.actual -cne [string]$binding.expected) {
            New-Failure "RestartCalibration journal path binding is invalid"
        }
    }
    if ($Journal.recovery_provenance.repo_head -cne $Plan.head -or
        $Journal.recovery_provenance.runner_sha256 -cne (Get-RunnerSha256) -or
        $Journal.recovery_provenance.behavior_sha -cne $BehaviorBaseline) {
        New-Failure "RestartCalibration journal recovery provenance is invalid"
    }
    $authorityState = Get-RestartAuthorityState -Journal $Journal -Plan $Plan -StatePathValue $StatePathValue
    if ($Journal.session_id -cne $authorityState.session_id -or
        $Journal.session_start_utc -cne $authorityState.utc_start -or
        $Journal.state_binding_sha256 -cne $authorityState.session_binding_sha256 -or
        -not (Test-ExactValue -Actual $Journal.source_fresh -Expected $authorityState.post_calibration) -or
        -not (Test-ExactValue -Actual $Journal.native_stage_truth -Expected $authorityState.stages) -or
        $Journal.source_provenance.repo_head -cne $authorityState.repo_head -or
        $Journal.source_provenance.runner_sha256 -cne $authorityState.runner_sha -or
        $Journal.source_provenance.behavior_sha -cne $authorityState.behavior_sha) {
        New-Failure "RestartCalibration journal authority does not match the preserved source state"
    }
    return $expectedPaths
}

function Assert-SourceStateIdentity {
    param(
        [Parameter(Mandatory = $true)]
        [string]$StatePathValue,

        [Parameter(Mandatory = $true)]
        [hashtable]$Expected
    )

    if (-not (Test-Path -LiteralPath $StatePathValue -PathType Leaf)) {
        New-Failure "RestartCalibration source state is missing"
    }
    $item = Get-Item -LiteralPath $StatePathValue
    if ((Get-Sha256Hex -Path $StatePathValue) -cne $Expected.sha256 -or
        [int64]$item.Length -ne [int64]$Expected.size -or
        (Format-UtcTimestamp -Value $item.LastWriteTimeUtc) -cne $Expected.mtime_utc) {
        New-Failure "RestartCalibration source state identity changed"
    }
}

function Assert-ExactRestartCandidate {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [string]$StatePathValue
    )

    $issues = @(Get-StateValidationIssues -State $State -Plan $Plan)
    if ($issues.Count -gt 0) {
        New-Failure ("INVALID_OR_UNCERTAIN_STATE: " + ($issues -join ", "))
    }
    Assert-StateIdentity -State $State
    Assert-StateProvenance -State $State -StatePathValue $StatePathValue -Plan $Plan -AllowRestartCandidate
    Assert-RepoAndEnvGuards -Plan $Plan
    Assert-ImmutableManifestAndBackups -Plan $Plan
    if ($State.classification -cne "VALID_FRESH_CALIBRATION" -or
        $State.next_stage -cne "MapLeft" -or
        @($State.completed_stages).Count -ne 1 -or
        [string]$State.completed_stages[0] -cne "Calibrate") {
        New-Failure "RestartCalibration permits only exact completed stages [Calibrate] with next_stage MapLeft"
    }
    if (@($State.failed_stages).Count -ne 0 -or
        $State.stages.MapLeft.result -cne "pending" -or
        $State.stages.MapRight.result -cne "pending" -or
        $State.stages.Verify.result -cne "pending") {
        New-Failure "RestartCalibration refuses a mapped, verified, or failed session"
    }
    foreach ($artifact in @("map_left", "map_right")) {
        if (-not [string]::IsNullOrEmpty([string]$State.artifacts[$artifact].sha256) -or
            (Test-Path -LiteralPath $State.artifacts[$artifact].path)) {
            New-Failure "RestartCalibration refuses because mapping has begun"
        }
    }
    Assert-EvidenceAndCalibrationStillMatch -State $State -Plan $Plan
}

function New-RejectedArchiveStaging {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Journal,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan
    )

    Assert-PathMissing -Path $Journal.archive_path
    Ensure-ParentDirectory -Path $Journal.archive_path
    if (-not (Test-Path -LiteralPath $Journal.archive_staging_path)) {
        [void][System.IO.Directory]::CreateDirectory($Journal.archive_staging_path)
    }
    $stagingItem = Get-Item -LiteralPath $Journal.archive_staging_path -Force
    if ($stagingItem -isnot [System.IO.DirectoryInfo] -or
        ($stagingItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        New-Failure "RestartCalibration archive staging path is not a regular directory"
    }
    $leftName = [System.IO.Path]::GetFileName([string]$Plan.calibration.left.path)
    $rightName = [System.IO.Path]::GetFileName([string]$Plan.calibration.right.path)
    $stateName = [System.IO.Path]::GetFileName([string]$Journal.state_path)
    $sourceState = Read-JsonFile -Path $Journal.state_path
    $transcriptSource = [string]$sourceState.artifacts.transcript.path
    $evidenceSource = [string]$sourceState.artifacts.evidence.path
    $manifestSource = [string]$Plan.manifest.path
    $copies = [ordered]@{
        left_calibration = [ordered]@{
            source = [string]$Plan.calibration.left.path
            relative = Join-Path "rejected-calibration" $leftName
        }
        right_calibration = [ordered]@{
            source = [string]$Plan.calibration.right.path
            relative = Join-Path "rejected-calibration" $rightName
        }
        transcript = [ordered]@{
            source = $transcriptSource
            relative = Join-Path "transcript" ([System.IO.Path]::GetFileName($transcriptSource))
        }
        evidence = [ordered]@{
            source = $evidenceSource
            relative = Join-Path "evidence" ([System.IO.Path]::GetFileName($evidenceSource))
        }
        state = [ordered]@{
            source = [string]$Journal.state_path
            relative = Join-Path "state-snapshot" $stateName
        }
    }
    $manifestRelative = Join-Path "immutable-backup" ([System.IO.Path]::GetFileName($manifestSource))
    $recordPath = Join-Path $Journal.archive_staging_path "archive-record.json"
    $allowedFiles = @("archive-record.json", "archive-record.json.restart-durable.tmp")
    $allowedDirectories = @()
    foreach ($copy in $copies.Values) {
        $allowedFiles += @($copy.relative, "$($copy.relative).restart-copy.tmp")
        $allowedDirectories += [System.IO.Path]::GetDirectoryName([string]$copy.relative)
    }
    $allowedFiles += @($manifestRelative, "$manifestRelative.restart-copy.tmp")
    $allowedDirectories += [System.IO.Path]::GetDirectoryName($manifestRelative)
    foreach ($entry in @(Get-ChildItem -LiteralPath $Journal.archive_staging_path -Recurse -Force)) {
        $relative = [System.IO.Path]::GetRelativePath($Journal.archive_staging_path, $entry.FullName)
        if (($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            New-Failure "RestartCalibration archive staging contains a reparse point: $($entry.FullName)"
        }
        if ($entry -is [System.IO.DirectoryInfo]) {
            if ($allowedDirectories -cnotcontains $relative) {
                New-Failure "RestartCalibration archive staging contains an unexpected directory: $relative"
            }
        }
        elseif ($entry -isnot [System.IO.FileInfo] -or $allowedFiles -cnotcontains $relative) {
            New-Failure "RestartCalibration archive staging contains an unexpected file: $relative"
        }
    }
    $artifactRecords = [ordered]@{}
    $copyIndex = 0
    foreach ($name in $copies.Keys) {
        $stagedPath = Join-Path $Journal.archive_staging_path $copies[$name].relative
        $publishedPath = Join-Path $Journal.archive_path $copies[$name].relative
        Copy-RestartStagedFile -Source $copies[$name].source -Destination $stagedPath -Plan $Plan
        $artifactRecords[$name] = Get-ArchiveArtifactRecord -Source $copies[$name].source -StagedPath $stagedPath -PublishedPath $publishedPath
        $copyIndex++
        if ($copyIndex -eq 1) {
            Test-RestartFailurePoint -Plan $Plan -Point "after_first_archive_copy"
        }
    }
    $stagedManifest = Join-Path $Journal.archive_staging_path $manifestRelative
    Copy-RestartStagedFile -Source $manifestSource -Destination $stagedManifest -Plan $Plan
    $record = [ordered]@{
        schema_version       = "1"
        record_type          = "packet2n-r5-rejected-calibration"
        reason               = $RestartRejectionReason
        archive_path         = $Journal.archive_path
        archive_created_utc  = [DateTime]::UtcNow.ToString("o")
        session_id           = $Journal.session_id
        session_start_utc    = $Journal.session_start_utc
        state_binding_sha256 = $Journal.state_binding_sha256
        source_provenance    = $Journal.source_provenance
        recovery_provenance  = $Journal.recovery_provenance
        immutable_backup     = [ordered]@{
            manifest = [ordered]@{
                source_path       = $manifestSource
                archive_path      = Join-Path $Journal.archive_path $manifestRelative
                sha256            = Get-Sha256Hex -Path $stagedManifest
                size              = [int64](Get-Item -LiteralPath $stagedManifest).Length
                source_mtime_utc  = Get-FileTimestampUtc -Path $manifestSource
                archive_mtime_utc = Get-FileTimestampUtc -Path $stagedManifest
            }
            left = [ordered]@{
                path             = $Plan.calibration.left.backup_path
                sha256           = $Plan.calibration.left.backup_sha256
                size             = [int64]$Plan.calibration.left.backup_size
                source_mtime_utc = $Plan.calibration.left.source_mtime_utc
            }
            right = [ordered]@{
                path             = $Plan.calibration.right.backup_path
                sha256           = $Plan.calibration.right.backup_sha256
                size             = [int64]$Plan.calibration.right.backup_size
                source_mtime_utc = $Plan.calibration.right.source_mtime_utc
            }
        }
        artifacts            = $artifactRecords
        transcript_validation = Get-RestartTranscriptValidationPayload `
            -State $sourceState `
            -StatePathValue ([string]$Journal.state_path) `
            -Plan $Plan `
            -TranscriptSha256 ([string]$artifactRecords.transcript.sha256)
    }
    if (-not (Test-Path -LiteralPath $recordPath)) {
        Write-RestartJsonDurable -Path $recordPath -Value $record -Plan $Plan
    }
    elseif (Test-Path -LiteralPath "$recordPath.restart-durable.tmp") {
        $recordTempItem = Get-Item -LiteralPath "$recordPath.restart-durable.tmp" -Force
        if ($recordTempItem -isnot [System.IO.FileInfo] -or
            ($recordTempItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            New-Failure "RestartCalibration archive record durable temp is invalid"
        }
        [System.IO.File]::Delete("$recordPath.restart-durable.tmp")
    }
    [void](Assert-RejectedArchiveCore -ActualRoot $Journal.archive_staging_path -PublishedRoot $Journal.archive_path -Plan $Plan -ExpectedStatePath ([string]$Journal.state_path))
    Test-RestartFailurePoint -Plan $Plan -Point "after_archive_record_write"
    return Get-Sha256Hex -Path $recordPath
}

function Get-ArchiveActualPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PublishedPath,

        [Parameter(Mandatory = $true)]
        [string]$PublishedRoot,

        [Parameter(Mandatory = $true)]
        [string]$ActualRoot
    )

    if (-not (Test-PathIsSameOrDescendant -Path $PublishedPath -Root $PublishedRoot)) {
        New-Failure "Rejected archive artifact path escaped its archive"
    }
    $relative = [System.IO.Path]::GetRelativePath($PublishedRoot, $PublishedPath)
    return Join-Path $ActualRoot $relative
}

function Assert-RejectedArchiveNamespace {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ActualRoot,

        [Parameter(Mandatory = $true)]
        [string]$PublishedRoot
    )

    if (-not (Test-Path -LiteralPath $ActualRoot)) {
        New-Failure "Rejected archive root is missing"
    }
    $rootItem = Get-Item -LiteralPath $ActualRoot -Force
    if (($rootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        New-Failure "Rejected archive root contains a reparse point: $ActualRoot"
    }
    if ($rootItem -isnot [System.IO.DirectoryInfo]) {
        New-Failure "Rejected archive root is not a regular directory: $ActualRoot"
    }
    $isPublished = [System.IO.Path]::GetFullPath($ActualRoot).Equals(
        [System.IO.Path]::GetFullPath($PublishedRoot),
        [System.StringComparison]::OrdinalIgnoreCase
    )
    $requiredDirectories = @(
        "rejected-calibration", "transcript", "evidence", "state-snapshot", "immutable-backup"
    )
    $allowedDirectories = @($requiredDirectories)
    if ($isPublished) {
        $allowedDirectories += @("retired-active-calibration", "retired-state")
    }
    $allowedRootFiles = @("archive-record.json")
    if ($isPublished) {
        $allowedRootFiles += @("restart-receipt.json", "restart-receipt.json.restart-durable.tmp")
    }
    $receiptNamespaceCount = 0
    $fileCountRules = [ordered]@{
        "rejected-calibration" = [ordered]@{ minimum = 2; maximum = 2; count = 0 }
        "transcript"           = [ordered]@{ minimum = 1; maximum = 1; count = 0 }
        "evidence"             = [ordered]@{ minimum = 1; maximum = 1; count = 0 }
        "state-snapshot"       = [ordered]@{ minimum = 1; maximum = 1; count = 0 }
        "immutable-backup"     = [ordered]@{ minimum = 1; maximum = 1; count = 0 }
    }
    if ($isPublished) {
        $fileCountRules["retired-active-calibration"] = [ordered]@{ minimum = 0; maximum = 2; count = 0 }
        $fileCountRules["retired-state"] = [ordered]@{ minimum = 0; maximum = 1; count = 0 }
    }
    foreach ($entry in @(Get-ChildItem -LiteralPath $ActualRoot -Recurse -Force)) {
        $relative = [System.IO.Path]::GetRelativePath($ActualRoot, $entry.FullName)
        if (($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            New-Failure "Rejected archive contains a reparse point: $relative"
        }
        if ($entry -is [System.IO.DirectoryInfo]) {
            if ($allowedDirectories -cnotcontains $relative) {
                New-Failure "Rejected archive contains an unexpected directory: $relative"
            }
            continue
        }
        if ($entry -isnot [System.IO.FileInfo]) {
            New-Failure "Rejected archive contains a non-regular entry: $relative"
        }
        $parent = [System.IO.Path]::GetDirectoryName($relative)
        if ([string]::IsNullOrEmpty($parent)) {
            if ($allowedRootFiles -cnotcontains $relative) {
                New-Failure "Rejected archive contains an unexpected file: $relative"
            }
            if ($relative.StartsWith("restart-receipt.json", [System.StringComparison]::Ordinal)) {
                $receiptNamespaceCount++
            }
            continue
        }
        if (-not $fileCountRules.Contains($parent)) {
            New-Failure "Rejected archive contains an unexpected file: $relative"
        }
        $fileCountRules[$parent].count = [int]$fileCountRules[$parent].count + 1
    }
    foreach ($directory in $requiredDirectories) {
        $directoryPath = Join-Path $ActualRoot $directory
        if (-not (Test-Path -LiteralPath $directoryPath -PathType Container)) {
            New-Failure "Rejected archive required directory is missing or has the wrong type: $directory"
        }
    }
    foreach ($directory in $fileCountRules.Keys) {
        $rule = $fileCountRules[$directory]
        if ([int]$rule.count -lt [int]$rule.minimum -or [int]$rule.count -gt [int]$rule.maximum) {
            New-Failure "Rejected archive directory has an unexpected file count: $directory"
        }
    }
    if ($receiptNamespaceCount -gt 1) {
        New-Failure "Rejected archive receipt namespace contains conflicting files"
    }
}

function Assert-RejectedArchiveExactNamespace {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ActualRoot,

        [Parameter(Mandatory = $true)]
        [string]$PublishedRoot,

        [Parameter(Mandatory = $true)]
        [hashtable]$ExpectedRelativeFiles
    )

    $isPublished = [System.IO.Path]::GetFullPath($ActualRoot).Equals(
        [System.IO.Path]::GetFullPath($PublishedRoot),
        [System.StringComparison]::OrdinalIgnoreCase
    )
    $expectedDirectories = @(
        "rejected-calibration", "transcript", "evidence", "state-snapshot", "immutable-backup"
    )
    $expectedFiles = @(
        "archive-record.json",
        [string]$ExpectedRelativeFiles.left_calibration,
        [string]$ExpectedRelativeFiles.right_calibration,
        [string]$ExpectedRelativeFiles.transcript,
        [string]$ExpectedRelativeFiles.evidence,
        [string]$ExpectedRelativeFiles.state,
        [string]$ExpectedRelativeFiles.manifest
    )
    if ($isPublished) {
        $retiredPairDirectory = Join-Path $ActualRoot "retired-active-calibration"
        if (Test-Path -LiteralPath $retiredPairDirectory) {
            $expectedDirectories += "retired-active-calibration"
            $expectedFiles += @(
                (Join-Path "retired-active-calibration" ([System.IO.Path]::GetFileName([string]$ExpectedRelativeFiles.left_calibration))),
                (Join-Path "retired-active-calibration" ([System.IO.Path]::GetFileName([string]$ExpectedRelativeFiles.right_calibration)))
            ) | Where-Object { Test-Path -LiteralPath (Join-Path $ActualRoot $_) }
        }
        $retiredStateDirectory = Join-Path $ActualRoot "retired-state"
        if (Test-Path -LiteralPath $retiredStateDirectory) {
            $expectedDirectories += "retired-state"
            $retiredStateRelative = Join-Path "retired-state" ([System.IO.Path]::GetFileName([string]$ExpectedRelativeFiles.state))
            if (Test-Path -LiteralPath (Join-Path $ActualRoot $retiredStateRelative)) {
                $expectedFiles += $retiredStateRelative
            }
        }
        foreach ($receiptName in @("restart-receipt.json", "restart-receipt.json.restart-durable.tmp")) {
            if (Test-Path -LiteralPath (Join-Path $ActualRoot $receiptName)) {
                $expectedFiles += $receiptName
            }
        }
    }

    $actualDirectories = @()
    $actualFiles = @()
    foreach ($entry in @(Get-ChildItem -LiteralPath $ActualRoot -Recurse -Force)) {
        $relative = [System.IO.Path]::GetRelativePath($ActualRoot, $entry.FullName)
        if (($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            New-Failure "Rejected archive contains a reparse point: $relative"
        }
        if ($entry -is [System.IO.DirectoryInfo]) {
            $actualDirectories += $relative
        }
        elseif ($entry -is [System.IO.FileInfo]) {
            $actualFiles += $relative
        }
        else {
            New-Failure "Rejected archive contains a non-regular entry: $relative"
        }
    }
    if ($actualDirectories.Count -ne $expectedDirectories.Count -or $actualFiles.Count -ne $expectedFiles.Count) {
        New-Failure "Rejected archive namespace is not the exact derived layout"
    }
    foreach ($relative in $actualDirectories) {
        if ($expectedDirectories -cnotcontains $relative) {
            New-Failure "Rejected archive contains an unexpected directory: $relative"
        }
    }
    foreach ($relative in $actualFiles) {
        if ($expectedFiles -cnotcontains $relative) {
            New-Failure "Rejected archive contains an unexpected file: $relative"
        }
    }
}

function Assert-RejectedArchiveCore {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ActualRoot,

        [Parameter(Mandatory = $true)]
        [string]$PublishedRoot,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [string]$ExpectedRecordSha256,

        [string]$ExpectedStatePath
    )

    Assert-RejectedArchiveNamespace -ActualRoot $ActualRoot -PublishedRoot $PublishedRoot
    $recordPath = Join-Path $ActualRoot "archive-record.json"
    if (-not (Test-Path -LiteralPath $recordPath -PathType Leaf)) {
        New-Failure "Rejected archive record is missing"
    }
    if (-not [string]::IsNullOrEmpty($ExpectedRecordSha256) -and (Get-Sha256Hex -Path $recordPath) -cne $ExpectedRecordSha256) {
        New-Failure "Rejected archive record hash mismatch"
    }
    $stateSnapshotEntries = @(Get-ChildItem -LiteralPath (Join-Path $ActualRoot "state-snapshot") -Force)
    if ($stateSnapshotEntries.Count -ne 1 -or
        $stateSnapshotEntries[0] -isnot [System.IO.FileInfo] -or
        ($stateSnapshotEntries[0].Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        New-Failure "Rejected archive state snapshot layout is invalid"
    }
    $actualStatePath = $stateSnapshotEntries[0].FullName
    $archivedState = Read-JsonFile -Path $actualStatePath
    $logicalStatePath = if ([string]::IsNullOrEmpty($ExpectedStatePath)) { [string]$archivedState.state_path } else { $ExpectedStatePath }
    if ($archivedState.state_path -cne $logicalStatePath -or
        -not (Test-PathIsSameOrDescendant -Path $logicalStatePath -Root ([string]$Plan.state_root)) -or
        $stateSnapshotEntries[0].Name -cne [System.IO.Path]::GetFileName($logicalStatePath)) {
        New-Failure "Rejected archive state snapshot source path is invalid"
    }
    $expectedArchivePath = Join-Path ([string]$Plan.rejected_archive_root) "packet2n-r5-rejected-$($archivedState.session_id)"
    if ($PublishedRoot -cne $expectedArchivePath) {
        New-Failure "Rejected archive path is not derived from its archived session"
    }
    $reservedArtifacts = Get-ReservedArtifactPaths -Plan $Plan -SessionId ([string]$archivedState.session_id)
    $expectedRelativeFiles = @{
        left_calibration  = Join-Path "rejected-calibration" ([System.IO.Path]::GetFileName([string]$Plan.calibration.left.path))
        right_calibration = Join-Path "rejected-calibration" ([System.IO.Path]::GetFileName([string]$Plan.calibration.right.path))
        transcript        = Join-Path "transcript" ([System.IO.Path]::GetFileName([string]$reservedArtifacts.transcript))
        evidence          = Join-Path "evidence" ([System.IO.Path]::GetFileName([string]$reservedArtifacts.evidence))
        state             = Join-Path "state-snapshot" ([System.IO.Path]::GetFileName($logicalStatePath))
        manifest          = Join-Path "immutable-backup" ([System.IO.Path]::GetFileName([string]$Plan.manifest.path))
    }
    Assert-RejectedArchiveExactNamespace -ActualRoot $ActualRoot -PublishedRoot $PublishedRoot -ExpectedRelativeFiles $expectedRelativeFiles

    $record = Read-JsonFile -Path $recordPath
    Assert-ExactKeySet `
        -Value $record `
        -ExpectedKeys @(
            "schema_version", "record_type", "reason", "archive_path", "archive_created_utc",
            "session_id", "session_start_utc", "state_binding_sha256", "source_provenance",
            "recovery_provenance", "immutable_backup", "artifacts", "transcript_validation"
        ) `
        -Message "Rejected archive record schema is invalid"
    Assert-ExactKeySet -Value $record.source_provenance -ExpectedKeys @("repo_head", "runner_sha256", "behavior_sha") -Message "Rejected archive source provenance schema is invalid"
    Assert-ExactKeySet -Value $record.recovery_provenance -ExpectedKeys @("repo_head", "runner_sha256", "behavior_sha") -Message "Rejected archive recovery provenance schema is invalid"
    Assert-ExactKeySet -Value $record.immutable_backup -ExpectedKeys @("manifest", "left", "right") -Message "Rejected archive immutable-backup schema is invalid"
    Assert-ExactKeySet -Value $record.immutable_backup.manifest -ExpectedKeys @("source_path", "archive_path", "sha256", "size", "source_mtime_utc", "archive_mtime_utc") -Message "Rejected archive manifest schema is invalid"
    foreach ($side in @("left", "right")) {
        Assert-ExactKeySet -Value $record.immutable_backup[$side] -ExpectedKeys @("path", "sha256", "size", "source_mtime_utc") -Message "Rejected archive immutable $side schema is invalid"
    }
    Assert-ExactKeySet -Value $record.artifacts -ExpectedKeys @("left_calibration", "right_calibration", "transcript", "evidence", "state") -Message "Rejected archive artifacts schema is invalid"
    Assert-ExactKeySet `
        -Value $record.transcript_validation `
        -ExpectedKeys @("header_valid", "hash_and_size_valid", "final_terminator_valid", "native_calibration_output_evaluation", "body_contains_native_calibration_output", "limitation") `
        -Message "Rejected archive transcript-validation schema is invalid"
    $expectedTranscriptValidation = Get-RestartTranscriptValidationPayload `
        -State $archivedState `
        -StatePathValue $logicalStatePath `
        -Plan $Plan `
        -TranscriptSha256 ([string]$archivedState.artifacts.transcript.sha256) `
        -StateIdentityPath $actualStatePath
    if ($record.schema_version -cne "1" -or
        $record.record_type -cne "packet2n-r5-rejected-calibration" -or
        $record.reason -cne $RestartRejectionReason -or
        $record.archive_path -cne $PublishedRoot -or
        -not (Test-IsUtcTimestamp -Value $record.archive_created_utc) -or
        -not (Test-IsUtcTimestamp -Value $record.session_start_utc) -or
        [string]::IsNullOrEmpty([string]$record.session_id) -or
        -not (Test-IsSha256Hex -Value $record.state_binding_sha256) -or
        -not (Test-IsSha256Hex -Value $record.source_provenance.runner_sha256) -or
        -not (Test-IsSha256Hex -Value $record.recovery_provenance.runner_sha256) -or
        [string]::IsNullOrWhiteSpace([string]$record.source_provenance.behavior_sha) -or
        [string]::IsNullOrWhiteSpace([string]$record.recovery_provenance.behavior_sha) -or
        -not (Test-ExactValue -Actual $record.transcript_validation -Expected $expectedTranscriptValidation)) {
        New-Failure "Rejected archive record identity is invalid"
    }
    $expectedSourcePaths = @{
        left_calibration  = [string]$Plan.calibration.left.path
        right_calibration = [string]$Plan.calibration.right.path
        transcript        = [string]$reservedArtifacts.transcript
        evidence          = [string]$reservedArtifacts.evidence
        state             = $logicalStatePath
    }
    foreach ($name in @("left_calibration", "right_calibration", "transcript", "evidence", "state")) {
        if (@($record.artifacts.Keys) -cnotcontains $name) {
            New-Failure "Rejected archive artifact record is missing: $name"
        }
        $artifact = $record.artifacts[$name]
        Assert-ExactKeySet -Value $artifact -ExpectedKeys @("source_path", "archive_path", "sha256", "size", "source_mtime_utc", "archive_mtime_utc") -Message "Rejected archive artifact schema is invalid: $name"
        $expectedArchiveArtifactPath = Join-Path $PublishedRoot ([string]$expectedRelativeFiles[$name])
        $actualPath = Join-Path $ActualRoot ([string]$expectedRelativeFiles[$name])
        if ($artifact.source_path -cne $expectedSourcePaths[$name] -or
            $artifact.archive_path -cne $expectedArchiveArtifactPath -or
            -not (Test-Path -LiteralPath $actualPath -PathType Leaf) -or
            -not (Test-IsSha256Hex -Value $artifact.sha256) -or
            -not (Test-IsUtcTimestamp -Value $artifact.source_mtime_utc) -or
            -not (Test-IsUtcTimestamp -Value $artifact.archive_mtime_utc) -or
            (Get-Sha256Hex -Path $actualPath) -cne $artifact.sha256 -or
            [int64](Get-Item -LiteralPath $actualPath).Length -ne [int64]$artifact.size -or
            (Get-FileTimestampUtc -Path $actualPath) -cne $artifact.archive_mtime_utc -or
            (@("transcript", "evidence") -ccontains $name -and
                $artifact.source_mtime_utc -cne (Get-FileTimestampUtc -Path $actualPath)) -or
            [string]::IsNullOrEmpty([string]$artifact.source_mtime_utc)) {
            New-Failure "Rejected archive artifact identity mismatch: $name"
        }
    }
    $manifest = $record.immutable_backup.manifest
    $actualManifest = Join-Path $ActualRoot ([string]$expectedRelativeFiles.manifest)
    $sourceManifestItem = Get-Item -LiteralPath $Plan.manifest.path -Force
    if ($manifest.source_path -cne $Plan.manifest.path -or
        $manifest.archive_path -cne (Join-Path $PublishedRoot ([string]$expectedRelativeFiles.manifest)) -or
        -not (Test-Path -LiteralPath $actualManifest -PathType Leaf) -or
        -not (Test-IsSha256Hex -Value $manifest.sha256) -or
        -not (Test-IsUtcTimestamp -Value $manifest.source_mtime_utc) -or
        -not (Test-IsUtcTimestamp -Value $manifest.archive_mtime_utc) -or
        (Get-Sha256Hex -Path $actualManifest) -cne $manifest.sha256 -or
        $manifest.sha256 -cne $Plan.manifest.sha256 -or
        [int64](Get-Item -LiteralPath $actualManifest).Length -ne [int64]$manifest.size -or
        [int64]$manifest.size -ne [int64]$sourceManifestItem.Length -or
        $manifest.source_mtime_utc -cne (Format-UtcTimestamp -Value $sourceManifestItem.LastWriteTimeUtc) -or
        (Get-Sha256Hex -Path $Plan.manifest.path) -cne $Plan.manifest.sha256 -or
        -not (Test-ExactValue -Actual (Read-JsonFile -Path $actualManifest) -Expected (Read-JsonFile -Path $Plan.manifest.path))) {
        New-Failure "Rejected archive immutable manifest identity mismatch"
    }
    foreach ($side in @("left", "right")) {
        $immutableSide = $record.immutable_backup[$side]
        if ($immutableSide.path -cne $Plan.calibration[$side].backup_path -or
            $immutableSide.sha256 -cne $Plan.calibration[$side].backup_sha256 -or
            [int64]$immutableSide.size -ne [int64]$Plan.calibration[$side].backup_size -or
            $immutableSide.source_mtime_utc -cne $Plan.calibration[$side].source_mtime_utc) {
            New-Failure "Rejected archive immutable $side identity is invalid"
        }
    }
    Assert-RestartArchivedStateSchema -State $archivedState
    $stateIssues = @(Get-StateValidationIssues -State $archivedState -Plan $Plan)
    if ($stateIssues.Count -gt 0) {
        New-Failure ("Rejected archive state schema is invalid: " + ($stateIssues -join ", "))
    }
    Assert-StateIdentity -State $archivedState
    Assert-StateProvenance -State $archivedState -StatePathValue $logicalStatePath -Plan $Plan -AllowRestartCandidate -StateIdentityPath $actualStatePath
    Assert-PreCalibrationMatchesOriginals -State $archivedState -Plan $Plan
    Assert-PostCalibrationFreshness -State $archivedState -Plan $Plan
    if ($archivedState.session_id -cne $record.session_id -or
        $archivedState.utc_start -cne $record.session_start_utc -or
        $archivedState.session_binding_sha256 -cne $record.state_binding_sha256 -or
        $archivedState.repo_head -cne $record.source_provenance.repo_head -or
        $archivedState.runner_sha -cne $record.source_provenance.runner_sha256 -or
        $archivedState.behavior_sha -cne $record.source_provenance.behavior_sha -or
        $archivedState.session_binding_sha256 -cne (Get-StateSessionBindingDigest -State $archivedState) -or
        $archivedState.classification -cne "VALID_FRESH_CALIBRATION" -or
        $archivedState.next_stage -cne "MapLeft" -or
        @($archivedState.completed_stages).Count -ne 1 -or
        [string]$archivedState.completed_stages[0] -cne "Calibrate" -or
        @($archivedState.failed_stages).Count -ne 0 -or
        $archivedState.stages.MapLeft.result -cne "pending" -or
        $archivedState.stages.MapRight.result -cne "pending" -or
        $archivedState.stages.Verify.result -cne "pending") {
        New-Failure "Rejected archive record is not bound to its archived source state"
    }
    foreach ($side in @("left", "right")) {
        $artifactName = "${side}_calibration"
        $artifact = $record.artifacts[$artifactName]
        $actualCalibrationPath = Join-Path $ActualRoot ([string]$expectedRelativeFiles[$artifactName])
        $actualCalibration = Read-JsonFile -Path $actualCalibrationPath
        Assert-CalibrationSchema -Calibration $actualCalibration -Label "archived rejected $side"
        if ($artifact.sha256 -cne $archivedState.post_calibration[$side].sha256 -or
            [int64]$artifact.size -ne [int64]$archivedState.post_calibration[$side].size -or
            $artifact.source_mtime_utc -cne $archivedState.post_calibration[$side].mtime_utc -or
            -not (Test-ExactValue -Actual $actualCalibration -Expected $archivedState.post_calibration[$side].calibration)) {
            New-Failure "Rejected archive calibration identity does not match archived state: $side"
        }
    }
    $actualTranscriptPath = Join-Path $ActualRoot ([string]$expectedRelativeFiles.transcript)
    $actualEvidencePath = Join-Path $ActualRoot ([string]$expectedRelativeFiles.evidence)
    if ($record.artifacts.transcript.sha256 -cne $archivedState.artifacts.transcript.sha256 -or
        [int64]$record.artifacts.transcript.size -ne [int64]$archivedState.artifacts.transcript.size -or
        $record.artifacts.evidence.sha256 -cne $archivedState.artifacts.evidence.sha256 -or
        [int64]$record.artifacts.evidence.size -ne [int64]$archivedState.artifacts.evidence.size -or
        $record.artifacts.state.sha256 -cne (Get-Sha256Hex -Path $actualStatePath) -or
        [int64]$record.artifacts.state.size -ne [int64](Get-Item -LiteralPath $actualStatePath).Length) {
        New-Failure "Rejected archive artifact identities do not match archived state"
    }
    Assert-ArchivedEvidenceSemantics -State $archivedState -Plan $Plan -EvidencePath $actualEvidencePath -TranscriptPath $actualTranscriptPath
    return $record
}

function Assert-RejectedArchiveMatchesJournal {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Record,

        [Parameter(Mandatory = $true)]
        [hashtable]$Journal
    )

    if ($Record.session_id -cne $Journal.session_id -or
        $Record.session_start_utc -cne $Journal.session_start_utc -or
        $Record.state_binding_sha256 -cne $Journal.state_binding_sha256 -or
        $Record.archive_path -cne $Journal.archive_path -or
        -not (Test-ExactValue -Actual $Record.source_provenance -Expected $Journal.source_provenance) -or
        -not (Test-ExactValue -Actual $Record.recovery_provenance -Expected $Journal.recovery_provenance)) {
        New-Failure "Rejected archive record does not match RestartCalibration journal authority"
    }
}

function New-StagedOriginalPair {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Journal,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan
    )

    if (-not (Test-Path -LiteralPath $Journal.staged_original_path)) {
        [void][System.IO.Directory]::CreateDirectory($Journal.staged_original_path)
    }
    $stagedItem = Get-Item -LiteralPath $Journal.staged_original_path -Force
    if ($stagedItem -isnot [System.IO.DirectoryInfo] -or
        ($stagedItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        New-Failure "RestartCalibration staged-original path is not a regular directory"
    }
    $allowedNames = @()
    foreach ($side in @("left", "right")) {
        $name = [System.IO.Path]::GetFileName([string]$Plan.calibration[$side].path)
        $allowedNames += @($name, "$name.restart-copy.tmp")
    }
    foreach ($entry in @(Get-ChildItem -LiteralPath $Journal.staged_original_path -Force)) {
        if ($entry -isnot [System.IO.FileInfo] -or
            ($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            $allowedNames -cnotcontains $entry.Name) {
            New-Failure "RestartCalibration staged-original directory contains an unexpected entry"
        }
    }
    $copyIndex = 0
    foreach ($side in @("left", "right")) {
        $name = [System.IO.Path]::GetFileName([string]$Plan.calibration[$side].path)
        $destination = Join-Path $Journal.staged_original_path $name
        Copy-RestartStagedFile -Source $Plan.calibration[$side].backup_path -Destination $destination -Plan $Plan
        $expectedTime = [datetime]::Parse($Plan.calibration[$side].source_mtime_utc, $null, [System.Globalization.DateTimeStyles]::RoundtripKind)
        [System.IO.File]::SetLastWriteTimeUtc($destination, $expectedTime.ToUniversalTime())
        $copyIndex++
        if ($copyIndex -eq 1) {
            Test-RestartFailurePoint -Plan $Plan -Point "after_first_original_copy"
        }
    }
    if ((Get-PairDirectoryLayout -Directory $Journal.staged_original_path -Plan $Plan -FreshIdentities $Journal.source_fresh) -cne "original") {
        New-Failure "RestartCalibration staged-original pair verification failed"
    }
}

function Get-StagedOriginalLayout {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Directory,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [hashtable]$FreshIdentities
    )

    if (-not (Test-Path -LiteralPath $Directory)) {
        return "missing"
    }
    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) {
        return "unrecognized"
    }
    $directoryItem = Get-Item -LiteralPath $Directory -Force
    if (($directoryItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        return "unrecognized"
    }
    $allowedNames = @()
    foreach ($side in @("left", "right")) {
        $name = [System.IO.Path]::GetFileName([string]$Plan.calibration[$side].path)
        $allowedNames += @($name, "$name.restart-copy.tmp")
    }
    foreach ($entry in @(Get-ChildItem -LiteralPath $Directory -Force)) {
        if ($entry -isnot [System.IO.FileInfo] -or
            ($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            $allowedNames -cnotcontains $entry.Name) {
            return "unrecognized"
        }
    }
    if ((Get-PairDirectoryLayout -Directory $Directory -Plan $Plan -FreshIdentities $FreshIdentities) -ceq "original") {
        return "original"
    }
    return "partial_original"
}

function Test-RestartFailurePoint {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [string]$Point
    )

    if (-not [bool]$Plan.is_test_mode -or -not $Plan.ContainsKey("restart_failure_point")) {
        return
    }
    $allowed = @(
        "after_initial_journal_temp_flush",
        "after_first_archive_copy",
        "after_archive_record_write",
        "before_archive_publish",
        "after_archive_namespace_publish",
        "after_first_original_copy",
        "after_active_directory_move",
        "after_original_directory_move",
        "after_fresh_pair_namespace_publish",
        "after_retired_state_directory_create",
        "after_state_namespace_publish",
        "after_receipt_temp_flush",
        "after_receipt_publish"
    )
    if ($allowed -cnotcontains [string]$Plan.restart_failure_point) {
        New-Failure "Test-mode RestartCalibration failure point is invalid"
    }
    if ([string]$Plan.restart_failure_point -ceq $Point) {
        New-Failure "TEST FAILURE INJECTION: $Point"
    }
}

function Get-RestartReceiptPayload {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Journal
    )

    return [ordered]@{
        schema_version        = "1"
        status                = "completed"
        reason                = $RestartRejectionReason
        session_id            = $Journal.session_id
        completed_utc         = [DateTime]::UtcNow.ToString("o")
        archive_path          = $Journal.archive_path
        archive_record_sha256 = $Journal.archive_record_sha256
        active_classification = "ORIGINAL_CALIBRATION_INTACT"
        next_stage            = "Calibrate"
        offline               = $true
        native_stage_truth    = $Journal.native_stage_truth
        source_provenance     = $Journal.source_provenance
        recovery_provenance   = $Journal.recovery_provenance
    }
}

function Assert-CompletedRejectedArchive {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ArchivePath,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [string]$ExpectedStatePath
    )

    $record = Assert-RejectedArchiveCore `
        -ActualRoot $ArchivePath `
        -PublishedRoot $ArchivePath `
        -Plan $Plan `
        -ExpectedStatePath $ExpectedStatePath
    $retiredPairPath = Join-Path $ArchivePath "retired-active-calibration"
    if (-not (Test-Path -LiteralPath $retiredPairPath -PathType Container)) {
        New-Failure "Rejected archive retired active calibration directory is missing"
    }
    $retiredPairItem = Get-Item -LiteralPath $retiredPairPath -Force
    $retiredPairEntries = @(Get-ChildItem -LiteralPath $retiredPairPath -Force)
    if (($retiredPairItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $retiredPairEntries.Count -ne 2) {
        New-Failure "Rejected archive retired active calibration directory is invalid"
    }
    foreach ($name in @("left_calibration", "right_calibration")) {
        $artifact = $record.artifacts[$name]
        $expectedName = [System.IO.Path]::GetFileName([string]$artifact.source_path)
        $matches = @($retiredPairEntries | Where-Object { $_.Name -ceq $expectedName })
        if ($matches.Count -ne 1 -or
            $matches[0] -isnot [System.IO.FileInfo] -or
            ($matches[0].Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            (Get-Sha256Hex -Path $matches[0].FullName) -cne $artifact.sha256 -or
            [int64]$matches[0].Length -ne [int64]$artifact.size -or
            (Format-UtcTimestamp -Value $matches[0].LastWriteTimeUtc) -cne $artifact.source_mtime_utc) {
            New-Failure "Rejected archive retired active calibration identity mismatch: $name"
        }
    }
    $retiredStateDirectory = Join-Path $ArchivePath "retired-state"
    if (-not (Test-Path -LiteralPath $retiredStateDirectory -PathType Container)) {
        New-Failure "Rejected archive retired state directory is missing"
    }
    $retiredStateDirectoryItem = Get-Item -LiteralPath $retiredStateDirectory -Force
    $retiredStateEntries = @(Get-ChildItem -LiteralPath $retiredStateDirectory -Force)
    $stateArtifact = $record.artifacts.state
    $expectedStateName = [System.IO.Path]::GetFileName([string]$stateArtifact.source_path)
    if (($retiredStateDirectoryItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $retiredStateEntries.Count -ne 1 -or
        $retiredStateEntries[0] -isnot [System.IO.FileInfo] -or
        $retiredStateEntries[0].Name -cne $expectedStateName -or
        ($retiredStateEntries[0].Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
        (Get-Sha256Hex -Path $retiredStateEntries[0].FullName) -cne $stateArtifact.sha256 -or
        [int64]$retiredStateEntries[0].Length -ne [int64]$stateArtifact.size -or
        (Format-UtcTimestamp -Value $retiredStateEntries[0].LastWriteTimeUtc) -cne $stateArtifact.source_mtime_utc) {
        New-Failure "Rejected archive retired state identity mismatch"
    }
    $receiptPath = Join-Path $ArchivePath "restart-receipt.json"
    $receipt = Read-JsonFile -Path $receiptPath
    Assert-ExactKeySet `
        -Value $receipt `
        -ExpectedKeys @(
            "schema_version", "status", "reason", "session_id", "completed_utc", "archive_path",
            "archive_record_sha256", "active_classification", "next_stage", "offline",
            "native_stage_truth", "source_provenance", "recovery_provenance"
        ) `
        -Message "Rejected archive receipt schema is invalid"
    Assert-ExactKeySet -Value $receipt.native_stage_truth -ExpectedKeys @("Calibrate", "MapLeft", "MapRight", "Verify") -Message "Rejected archive receipt native-stage schema is invalid"
    Assert-ExactKeySet -Value $receipt.source_provenance -ExpectedKeys @("repo_head", "runner_sha256", "behavior_sha") -Message "Rejected archive receipt source provenance schema is invalid"
    Assert-ExactKeySet -Value $receipt.recovery_provenance -ExpectedKeys @("repo_head", "runner_sha256", "behavior_sha") -Message "Rejected archive receipt recovery provenance schema is invalid"
    $actualStatePath = Get-ArchiveActualPath -PublishedPath $record.artifacts.state.archive_path -PublishedRoot $ArchivePath -ActualRoot $ArchivePath
    $archivedState = Read-JsonFile -Path $actualStatePath
    if ($receipt.schema_version -cne "1" -or
        $receipt.status -cne "completed" -or
        $receipt.reason -cne $RestartRejectionReason -or
        $receipt.session_id -cne $record.session_id -or
        -not (Test-IsUtcTimestamp -Value $receipt.completed_utc) -or
        [DateTimeOffset]::Parse([string]$receipt.completed_utc, [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::RoundtripKind) -lt [DateTimeOffset]::Parse([string]$record.archive_created_utc, [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::RoundtripKind) -or
        $receipt.archive_path -cne $ArchivePath -or
        $receipt.archive_record_sha256 -cne (Get-Sha256Hex -Path (Join-Path $ArchivePath "archive-record.json")) -or
        $receipt.active_classification -cne "ORIGINAL_CALIBRATION_INTACT" -or
        $receipt.next_stage -cne "Calibrate" -or
        $receipt.offline -ne $true -or
        -not (Test-ExactValue -Actual $receipt.native_stage_truth -Expected $archivedState.stages) -or
        -not (Test-ExactValue -Actual $receipt.source_provenance -Expected $record.source_provenance) -or
        -not (Test-ExactValue -Actual $receipt.recovery_provenance -Expected $record.recovery_provenance) -or
        $receipt.source_provenance.repo_head -cne $archivedState.repo_head -or
        $receipt.source_provenance.runner_sha256 -cne $archivedState.runner_sha -or
        $receipt.source_provenance.behavior_sha -cne $archivedState.behavior_sha -or
        $receipt.recovery_provenance.repo_head -cne $Plan.head -or
        $receipt.recovery_provenance.runner_sha256 -cne (Get-RunnerSha256) -or
        $receipt.recovery_provenance.behavior_sha -cne $BehaviorBaseline) {
        New-Failure "Rejected archive receipt is invalid"
    }
    return [ordered]@{
        archive_path = $ArchivePath
        reason       = $record.reason
        session_id   = $record.session_id
        verified     = $true
    }
}

function Get-VerifiedRejectedArchiveRecords {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [string]$StatePathValue
    )

    $root = [string]$Plan.rejected_archive_root
    if (-not (Test-Path -LiteralPath $root)) {
        return @()
    }
    if (-not (Test-Path -LiteralPath $root -PathType Container)) {
        New-Failure "Rejected archive root is not a directory"
    }
    Assert-RestartPathConfined -Path $root -Root $root -Label "rejected archive root"
    $archiveEntries = @(Get-ChildItem -LiteralPath $root -Force | Where-Object {
        $_.Name.StartsWith("packet2n-r5-rejected-", [System.StringComparison]::Ordinal)
    })
    foreach ($entry in $archiveEntries) {
        if (($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            New-Failure "Rejected archive entry is a reparse point: $($entry.FullName)"
        }
        if ($entry -isnot [System.IO.DirectoryInfo]) {
            New-Failure "Rejected archive entry has the wrong path type: $($entry.FullName)"
        }
        if ($entry.Name.EndsWith(".staging", [System.StringComparison]::Ordinal)) {
            New-Failure "Rejected archive staging directory exists without an active journal: $($entry.FullName)"
        }
    }
    $records = @()
    foreach ($directory in @($archiveEntries | Sort-Object Name)) {
        $records += Assert-CompletedRejectedArchive `
            -ArchivePath $directory.FullName `
            -Plan $Plan `
            -ExpectedStatePath $StatePathValue
    }
    return @($records)
}

function Get-RejectedArchiveStagingLayout {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Journal,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [hashtable]$AuthorityState
    )

    $root = [string]$Journal.archive_staging_path
    if (-not (Test-Path -LiteralPath $root)) {
        return "missing"
    }
    if (-not (Test-Path -LiteralPath $root -PathType Container)) {
        return "unrecognized"
    }
    $rootItem = Get-Item -LiteralPath $root -Force
    if (($rootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        return "unrecognized"
    }
    $relativeFiles = @(
        (Join-Path "rejected-calibration" ([System.IO.Path]::GetFileName([string]$Plan.calibration.left.path)))
        (Join-Path "rejected-calibration" ([System.IO.Path]::GetFileName([string]$Plan.calibration.right.path)))
        (Join-Path "transcript" ([System.IO.Path]::GetFileName([string]$AuthorityState.artifacts.transcript.path)))
        (Join-Path "evidence" ([System.IO.Path]::GetFileName([string]$AuthorityState.artifacts.evidence.path)))
        (Join-Path "state-snapshot" ([System.IO.Path]::GetFileName([string]$Journal.state_path)))
        (Join-Path "immutable-backup" ([System.IO.Path]::GetFileName([string]$Plan.manifest.path)))
    )
    $allowedFiles = @("archive-record.json", "archive-record.json.restart-durable.tmp")
    $allowedDirectories = @()
    foreach ($relativeFile in $relativeFiles) {
        $allowedFiles += @($relativeFile, "$relativeFile.restart-copy.tmp")
        $allowedDirectories += [System.IO.Path]::GetDirectoryName($relativeFile)
    }
    foreach ($entry in @(Get-ChildItem -LiteralPath $root -Recurse -Force)) {
        $relative = [System.IO.Path]::GetRelativePath($root, $entry.FullName)
        if (($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            return "unrecognized"
        }
        if ($entry -is [System.IO.DirectoryInfo]) {
            if ($allowedDirectories -cnotcontains $relative) {
                return "unrecognized"
            }
        }
        elseif ($entry -isnot [System.IO.FileInfo] -or $allowedFiles -cnotcontains $relative) {
            return "unrecognized"
        }
    }
    $recordPath = Join-Path $root "archive-record.json"
    if (Test-Path -LiteralPath $recordPath -PathType Leaf) {
        $record = Assert-RejectedArchiveCore -ActualRoot $root -PublishedRoot $Journal.archive_path -Plan $Plan -ExpectedRecordSha256 ([string]$Journal.archive_record_sha256) -ExpectedStatePath ([string]$Journal.state_path)
        Assert-RejectedArchiveMatchesJournal -Record $record -Journal $Journal
        return "complete"
    }
    return "partial"
}

function Get-RetiredStateLayout {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Journal
    )

    $directory = Join-Path $Journal.archive_path "retired-state"
    if (-not (Test-Path -LiteralPath $directory)) {
        return "missing"
    }
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
        return "unrecognized"
    }
    $directoryItem = Get-Item -LiteralPath $directory -Force
    if (($directoryItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        New-Failure "RestartCalibration retired state path contains a reparse point"
    }
    $entries = @(Get-ChildItem -LiteralPath $directory -Force)
    if ($entries.Count -eq 0) {
        return "empty"
    }
    $expectedName = [System.IO.Path]::GetFileName([string]$Journal.state_path)
    if ($entries.Count -eq 1 -and
        ($entries[0].Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        New-Failure "RestartCalibration retired state file contains a reparse point"
    }
    if ($entries.Count -ne 1 -or
        $entries[0] -isnot [System.IO.FileInfo] -or
        $entries[0].Name -cne $expectedName -or
        (Get-Sha256Hex -Path $entries[0].FullName) -cne $Journal.source_state.sha256 -or
        [int64]$entries[0].Length -ne [int64]$Journal.source_state.size -or
        (Format-UtcTimestamp -Value $entries[0].LastWriteTimeUtc) -cne $Journal.source_state.mtime_utc) {
        return "unrecognized"
    }
    return "retired"
}

function Assert-RestartRecoveryLayout {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Journal,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [string]$StatePathValue
    )

    $authorityState = Get-RestartAuthorityState -Journal $Journal -Plan $Plan -StatePathValue $StatePathValue
    $archiveExists = $false
    if (Test-Path -LiteralPath $Journal.archive_path) {
        $archiveItem = Get-Item -LiteralPath $Journal.archive_path -Force
        if (($archiveItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            New-Failure "RestartCalibration archive path contains a reparse point"
        }
        if ($archiveItem -isnot [System.IO.DirectoryInfo]) {
            New-Failure "RestartCalibration archive path has the wrong path type"
        }
        $archiveExists = $true
    }
    $archiveStagingLayout = Get-RejectedArchiveStagingLayout -Journal $Journal -Plan $Plan -AuthorityState $authorityState
    if ($archiveExists -and $archiveStagingLayout -cne "missing") {
        New-Failure "unrecognized RestartCalibration archive layout"
    }
    $activeLayout = Get-PairDirectoryLayout -Directory $Journal.active_directory -Plan $Plan -FreshIdentities $Journal.source_fresh
    $stagedLayout = Get-StagedOriginalLayout -Directory $Journal.staged_original_path -Plan $Plan -FreshIdentities $Journal.source_fresh
    $rollbackLayout = Get-PairDirectoryLayout -Directory $Journal.rollback_path -Plan $Plan -FreshIdentities $Journal.source_fresh
    $retiredDirectory = Join-Path $Journal.archive_path "retired-active-calibration"
    $retiredLayout = Get-PairDirectoryLayout -Directory $retiredDirectory -Plan $Plan -FreshIdentities $Journal.source_fresh
    $retiredStateLayout = Get-RetiredStateLayout -Journal $Journal
    $stateExists = Test-Path -LiteralPath $StatePathValue -PathType Leaf
    $receiptPath = Join-Path $Journal.archive_path "restart-receipt.json"
    $receiptExists = Test-Path -LiteralPath $receiptPath -PathType Leaf
    $receiptTempExists = Test-Path -LiteralPath "$receiptPath.restart-durable.tmp" -PathType Leaf
    $recognized = $false
    $allowedPhases = @()
    $establishedPhase = $null

    if (-not $archiveExists) {
        $recognized = (
            @("missing", "partial", "complete") -ccontains $archiveStagingLayout -and
            $activeLayout -ceq "fresh" -and
            $stagedLayout -ceq "missing" -and
            $rollbackLayout -ceq "missing" -and
            $retiredLayout -ceq "missing" -and
            $retiredStateLayout -ceq "missing" -and
            $stateExists -and
            -not $receiptExists -and
            -not $receiptTempExists
        )
        if ($recognized) {
            if ($archiveStagingLayout -ceq "complete") {
                $allowedPhases = @("initialized", "archive_staged")
                $establishedPhase = "archive_staged"
            }
            else {
                $allowedPhases = @("initialized")
                $establishedPhase = "initialized"
            }
        }
    }
    else {
        $record = Assert-RejectedArchiveCore -ActualRoot $Journal.archive_path -PublishedRoot $Journal.archive_path -Plan $Plan -ExpectedRecordSha256 ([string]$Journal.archive_record_sha256) -ExpectedStatePath ([string]$Journal.state_path)
        Assert-RejectedArchiveMatchesJournal -Record $record -Journal $Journal
        if ($activeLayout -ceq "fresh" -and @("missing", "partial_original", "original") -ccontains $stagedLayout -and $rollbackLayout -ceq "missing" -and $retiredLayout -ceq "missing" -and $stateExists -and $retiredStateLayout -ceq "missing" -and -not $receiptExists -and -not $receiptTempExists) {
            $recognized = $true
            $allowedPhases = if ($stagedLayout -ceq "missing") { @("archive_staged", "archive_published") } else { @("archive_published") }
            $establishedPhase = "archive_published"
        }
        elseif ($activeLayout -ceq "missing" -and $stagedLayout -ceq "original" -and $rollbackLayout -ceq "fresh" -and $retiredLayout -ceq "missing" -and $stateExists -and $retiredStateLayout -ceq "missing" -and -not $receiptExists -and -not $receiptTempExists) {
            $recognized = $true
            $allowedPhases = @("archive_published", "active_withdrawn")
            $establishedPhase = "active_withdrawn"
        }
        elseif ($activeLayout -ceq "original" -and $stagedLayout -ceq "missing" -and $rollbackLayout -ceq "fresh" -and $retiredLayout -ceq "missing" -and $stateExists -and $retiredStateLayout -ceq "missing" -and -not $receiptExists -and -not $receiptTempExists) {
            $recognized = $true
            $allowedPhases = @("active_withdrawn", "original_activated")
            $establishedPhase = "original_activated"
        }
        elseif ($activeLayout -ceq "original" -and $stagedLayout -ceq "missing" -and $rollbackLayout -ceq "missing" -and $retiredLayout -ceq "fresh" -and $stateExists -and @("missing", "empty") -ccontains $retiredStateLayout -and -not $receiptExists -and -not $receiptTempExists) {
            $recognized = $true
            $allowedPhases = @("original_activated", "fresh_pair_retired")
            $establishedPhase = "fresh_pair_retired"
        }
        elseif ($activeLayout -ceq "original" -and $stagedLayout -ceq "missing" -and $rollbackLayout -ceq "missing" -and $retiredLayout -ceq "fresh" -and -not $stateExists -and $retiredStateLayout -ceq "retired") {
            $recognized = $true
            $allowedPhases = if ($receiptExists -or $receiptTempExists) { @("state_retired") } else { @("fresh_pair_retired", "state_retired") }
            $establishedPhase = "state_retired"
        }
    }
    if (-not $recognized) {
        New-Failure "unrecognized RestartCalibration directory layout"
    }
    if ($allowedPhases -cnotcontains [string]$Journal.phase) {
        New-Failure "RestartCalibration journal phase does not match the recognized physical layout"
    }
    if ($archiveExists -and $null -eq $Journal.archive_record_sha256) {
        New-Failure "RestartCalibration published archive is not bound to the journal record hash"
    }
    if (-not $archiveExists -and $archiveStagingLayout -cne "complete" -and $null -ne $Journal.archive_record_sha256) {
        New-Failure "RestartCalibration partial archive staging has an unexpected journal record hash"
    }
    $establishedRecordSha256 = if ($archiveExists) {
        [string]$Journal.archive_record_sha256
    }
    elseif ($archiveStagingLayout -ceq "complete") {
        Get-Sha256Hex -Path (Join-Path $Journal.archive_staging_path "archive-record.json")
    }
    else {
        $null
    }
    return [ordered]@{
        phase                 = $establishedPhase
        archive_record_sha256 = $establishedRecordSha256
    }
}

function Add-RejectedArchivesToStatus {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Payload,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [string]$StatePathValue
    )

    $records = @(Get-VerifiedRejectedArchiveRecords -Plan $Plan -StatePathValue $StatePathValue)
    if ($records.Count -gt 0) {
        $Payload.rejected_archives = $records
    }
    return $Payload
}

function Get-IncompleteRestartStatus {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [string]$StatePathValue
    )

    $journalPath = Get-RestartJournalPath -StatePathValue $StatePathValue
    if (-not (Test-Path -LiteralPath $journalPath)) {
        return $null
    }
    try {
        $journal = Read-JsonFile -Path $journalPath
        [void](Assert-RestartJournal -Journal $journal -Plan $Plan -StatePathValue $StatePathValue)
        $layoutAuthority = Assert-RestartRecoveryLayout -Journal $journal -Plan $Plan -StatePathValue $StatePathValue
        return [ordered]@{
            classification      = "RESTART_CALIBRATION_RECOVERABLE"
            next_stage          = "RestartCalibration"
            report              = "incomplete RestartCalibration transaction; rerun the exact confirmed command"
            restart_transaction = [ordered]@{
                journal_path = $journalPath
                session_id   = $journal.session_id
                phase        = $layoutAuthority.phase
                reason       = $journal.reason
                archive_path = $journal.archive_path
            }
        }
    }
    catch {
        return [ordered]@{
            classification = "INVALID_OR_UNCERTAIN_STATE"
            next_stage     = $null
            report         = "RestartCalibration journal is invalid: $($_.Exception.Message)"
        }
    }
}

function Assert-NoIncompleteRestartTransaction {
    param(
        [Parameter(Mandatory = $true)]
        [string]$StatePathValue
    )

    $journalPath = Get-RestartJournalPath -StatePathValue $StatePathValue
    if (Test-Path -LiteralPath $journalPath) {
        New-Failure "Stage $Stage is blocked by an incomplete RestartCalibration transaction"
    }
}

function Invoke-RestartCalibrationStage {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [string]$StatePathValue
    )

    Assert-NoIncompleteInterruptedRecoveryTransaction -StatePathValue $StatePathValue
    $journalPath = Get-RestartJournalPath -StatePathValue $StatePathValue
    if (Test-Path -LiteralPath $journalPath) {
        $journal = Read-JsonFile -Path $journalPath
        $paths = Assert-RestartJournal -Journal $journal -Plan $Plan -StatePathValue $StatePathValue
        $layoutAuthority = Assert-RestartRecoveryLayout -Journal $journal -Plan $Plan -StatePathValue $StatePathValue
        if ($journal.phase -cne $layoutAuthority.phase -or
            [string]$journal.archive_record_sha256 -cne [string]$layoutAuthority.archive_record_sha256) {
            $journal.phase = $layoutAuthority.phase
            $journal.archive_record_sha256 = $layoutAuthority.archive_record_sha256
            Save-RestartJournal -Path $journalPath -Journal $journal -Plan $Plan
            $journal = Read-JsonFile -Path $journalPath
            $paths = Assert-RestartJournal -Journal $journal -Plan $Plan -StatePathValue $StatePathValue
            $revalidatedLayout = Assert-RestartRecoveryLayout -Journal $journal -Plan $Plan -StatePathValue $StatePathValue
            if ($journal.phase -cne $revalidatedLayout.phase -or
                [string]$journal.archive_record_sha256 -cne [string]$revalidatedLayout.archive_record_sha256) {
                New-Failure "RestartCalibration journal reconciliation did not durably match the physical layout"
            }
        }
    }
    else {
        $state = Load-State -Path $StatePathValue
        Assert-ExactRestartCandidate -State $state -Plan $Plan -StatePathValue $StatePathValue
        $paths = Get-RestartTransactionPaths -Plan $Plan -StatePathValue $StatePathValue -SessionId ([string]$state.session_id)
        foreach ($path in @($paths.archive, $paths.archive_staging, $paths.staged_original, $paths.rollback)) {
            Assert-PathMissing -Path $path
        }
        $layout = Get-PairDirectoryLayout -Directory $paths.active -Plan $Plan -FreshIdentities $state.post_calibration
        if ($layout -cne "fresh") {
            New-Failure "RestartCalibration active directory must contain exactly the verified fresh pair"
        }
        $journal = New-RestartJournal -State $state -Plan $Plan -StatePathValue $StatePathValue -Paths $paths
        Write-RestartJsonDurable `
            -Path $journalPath `
            -Value $journal `
            -Plan $Plan `
            -AfterFlushFailurePoint "after_initial_journal_temp_flush"
    }

    Assert-RepoAndEnvGuards -Plan $Plan
    Assert-ImmutableManifestAndBackups -Plan $Plan
    if ((Test-Path -LiteralPath $journal.archive_path) -and (Test-Path -LiteralPath $journal.archive_staging_path)) {
        New-Failure "RestartCalibration archive layout is unrecognized"
    }
    if (-not (Test-Path -LiteralPath $journal.archive_path)) {
        Assert-SourceStateIdentity -StatePathValue $StatePathValue -Expected $journal.source_state
        if ((Get-PairDirectoryLayout -Directory $journal.active_directory -Plan $Plan -FreshIdentities $journal.source_fresh) -cne "fresh") {
            New-Failure "RestartCalibration cannot stage an archive from a changed active pair"
        }
        $journal.archive_record_sha256 = New-RejectedArchiveStaging -Journal $journal -Plan $Plan
        $journal.phase = "archive_staged"
        Save-RestartJournal -Path $journalPath -Journal $journal -Plan $Plan
        $stagedArchiveRecord = Assert-RejectedArchiveCore -ActualRoot $journal.archive_staging_path -PublishedRoot $journal.archive_path -Plan $Plan -ExpectedRecordSha256 $journal.archive_record_sha256 -ExpectedStatePath ([string]$journal.state_path)
        Assert-RejectedArchiveMatchesJournal -Record $stagedArchiveRecord -Journal $journal
        Test-RestartFailurePoint -Plan $Plan -Point "before_archive_publish"
        Assert-RestartMoveSafe `
            -Source $journal.archive_staging_path `
            -Destination $journal.archive_path `
            -SourceRoot ([string]$Plan.rejected_archive_root) `
            -DestinationRoot ([string]$Plan.rejected_archive_root) `
            -Label "archive publication"
        Invoke-RestartDurableNamespaceMove -Source $journal.archive_staging_path -Destination $journal.archive_path -Label "archive publication"
        Test-RestartFailurePoint -Plan $Plan -Point "after_archive_namespace_publish"
        $journal.phase = "archive_published"
        Save-RestartJournal -Path $journalPath -Journal $journal -Plan $Plan
    }
    $publishedArchiveRecord = Assert-RejectedArchiveCore -ActualRoot $journal.archive_path -PublishedRoot $journal.archive_path -Plan $Plan -ExpectedRecordSha256 $journal.archive_record_sha256 -ExpectedStatePath ([string]$journal.state_path)
    Assert-RejectedArchiveMatchesJournal -Record $publishedArchiveRecord -Journal $journal

    $activeLayout = Get-PairDirectoryLayout -Directory $journal.active_directory -Plan $Plan -FreshIdentities $journal.source_fresh
    $stagedLayout = Get-StagedOriginalLayout -Directory $journal.staged_original_path -Plan $Plan -FreshIdentities $journal.source_fresh
    $rollbackLayout = Get-PairDirectoryLayout -Directory $journal.rollback_path -Plan $Plan -FreshIdentities $journal.source_fresh
    $retiredDirectory = Join-Path $journal.archive_path "retired-active-calibration"
    $retiredLayout = Get-PairDirectoryLayout -Directory $retiredDirectory -Plan $Plan -FreshIdentities $journal.source_fresh

    if ($activeLayout -ceq "fresh" -and @("missing", "partial_original") -ccontains $stagedLayout -and $rollbackLayout -ceq "missing" -and $retiredLayout -ceq "missing") {
        New-StagedOriginalPair -Journal $journal -Plan $Plan
        $stagedLayout = "original"
    }
    if ($activeLayout -ceq "fresh" -and $stagedLayout -ceq "original" -and $rollbackLayout -ceq "missing" -and $retiredLayout -ceq "missing") {
        $activeParent = [System.IO.Path]::GetDirectoryName([string]$journal.active_directory)
        Assert-RestartMoveSafe `
            -Source $journal.active_directory `
            -Destination $journal.rollback_path `
            -SourceRoot $activeParent `
            -DestinationRoot $activeParent `
            -Label "active directory withdrawal"
        Invoke-RestartDurableNamespaceMove -Source $journal.active_directory -Destination $journal.rollback_path -Label "active directory withdrawal"
        Test-RestartFailurePoint -Plan $Plan -Point "after_active_directory_move"
        $journal.phase = "active_withdrawn"
        Save-RestartJournal -Path $journalPath -Journal $journal -Plan $Plan
        $activeLayout = "missing"
        $rollbackLayout = "fresh"
    }
    if ($activeLayout -ceq "missing" -and $stagedLayout -ceq "original" -and $rollbackLayout -ceq "fresh" -and $retiredLayout -ceq "missing") {
        $activeParent = [System.IO.Path]::GetDirectoryName([string]$journal.active_directory)
        Assert-RestartMoveSafe `
            -Source $journal.staged_original_path `
            -Destination $journal.active_directory `
            -SourceRoot $activeParent `
            -DestinationRoot $activeParent `
            -Label "original directory activation"
        Invoke-RestartDurableNamespaceMove -Source $journal.staged_original_path -Destination $journal.active_directory -Label "original directory activation"
        Test-RestartFailurePoint -Plan $Plan -Point "after_original_directory_move"
        $journal.phase = "original_activated"
        Save-RestartJournal -Path $journalPath -Journal $journal -Plan $Plan
        $activeLayout = "original"
        $stagedLayout = "missing"
    }
    if ($activeLayout -cne "original" -or $stagedLayout -cne "missing") {
        New-Failure "unrecognized RestartCalibration directory layout"
    }
    Assert-OriginalCalibrationIdentities -Plan $Plan

    if ($rollbackLayout -ceq "fresh" -and $retiredLayout -ceq "missing") {
        $activeParent = [System.IO.Path]::GetDirectoryName([string]$journal.active_directory)
        Assert-RestartMoveSafe `
            -Source $journal.rollback_path `
            -Destination $retiredDirectory `
            -SourceRoot $activeParent `
            -DestinationRoot ([string]$Plan.rejected_archive_root) `
            -Label "fresh-pair retirement"
        Invoke-RestartDurableNamespaceMove -Source $journal.rollback_path -Destination $retiredDirectory -Label "fresh-pair retirement"
        Test-RestartFailurePoint -Plan $Plan -Point "after_fresh_pair_namespace_publish"
        $rollbackLayout = "missing"
        $retiredLayout = "fresh"
    }
    if ($rollbackLayout -cne "missing" -or $retiredLayout -cne "fresh") {
        New-Failure "unrecognized RestartCalibration retired-pair layout"
    }
    $journal.phase = "fresh_pair_retired"
    Save-RestartJournal -Path $journalPath -Journal $journal -Plan $Plan

    $retiredStateDirectory = Join-Path $journal.archive_path "retired-state"
    $retiredStatePath = Join-Path $retiredStateDirectory ([System.IO.Path]::GetFileName($StatePathValue))
    if (Test-Path -LiteralPath $StatePathValue) {
        Assert-SourceStateIdentity -StatePathValue $StatePathValue -Expected $journal.source_state
        Assert-PathMissing -Path $retiredStatePath
        [void][System.IO.Directory]::CreateDirectory($retiredStateDirectory)
        Assert-RestartMoveSafe `
            -Source $StatePathValue `
            -Destination $retiredStatePath `
            -SourceRoot ([string]$Plan.state_root) `
            -DestinationRoot ([string]$Plan.rejected_archive_root) `
            -Label "state retirement"
        Invoke-RestartDurableNamespaceMove -Source $StatePathValue -Destination $retiredStatePath -Label "state retirement"
        Test-RestartFailurePoint -Plan $Plan -Point "after_state_namespace_publish"
    }
    if (-not (Test-Path -LiteralPath $retiredStatePath -PathType Leaf) -or
        (Get-Sha256Hex -Path $retiredStatePath) -cne $journal.source_state.sha256 -or
        [int64](Get-Item -LiteralPath $retiredStatePath).Length -ne [int64]$journal.source_state.size) {
        New-Failure "RestartCalibration retired state identity mismatch"
    }
    $journal.phase = "state_retired"
    Save-RestartJournal -Path $journalPath -Journal $journal -Plan $Plan

    $receiptPath = Join-Path $journal.archive_path "restart-receipt.json"
    if (-not (Test-Path -LiteralPath $receiptPath)) {
        $receipt = Get-RestartReceiptPayload -Journal $journal
        Write-RestartJsonDurable `
            -Path $receiptPath `
            -Value $receipt `
            -Plan $Plan `
            -AfterFlushFailurePoint "after_receipt_temp_flush"
    }
    [void](Assert-CompletedRejectedArchive `
        -ArchivePath $journal.archive_path `
        -Plan $Plan `
        -ExpectedStatePath ([string]$journal.state_path))
    Test-RestartFailurePoint -Plan $Plan -Point "after_receipt_publish"
    [System.IO.File]::Delete($journalPath)
    [Console]::Out.WriteLine("RESTART_CALIBRATION_COMPLETE")
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

    $useDirectNative = (-not [bool]$Plan.is_test_mode) -or (Test-UseDirectNativeExitProbe -StageName $StageName -Plan $Plan)
    if ($useDirectNative) {
        if (-not (Test-Path -LiteralPath $command.executable -PathType Leaf)) {
            New-Failure "Native executable is missing: $($command.executable)"
        }
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
        $nativeTranscriptStarted = $false
        $capturedExitCode = $null
        try {
            if ($OutputPath) {
                Start-Transcript -Path $OutputPath -Append | Out-Null
                $nativeTranscriptStarted = $true
            }
            Remove-Variable -Name LASTEXITCODE -Scope Global -ErrorAction SilentlyContinue
            $nativePrimaryException = $null
            $nativeErrorPreference = Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue
            $nativeErrorPreferenceExisted = $null -ne $nativeErrorPreference
            $savedNativeErrorPreference = if ($nativeErrorPreferenceExisted) { $nativeErrorPreference.Value } else { $null }
            $localNativeErrorPreference = Get-Variable -Name PSNativeCommandUseErrorActionPreference -Scope Local -ErrorAction SilentlyContinue
            $localNativeErrorPreferenceExisted = $null -ne $localNativeErrorPreference
            $savedLocalNativeErrorPreference = if ($localNativeErrorPreferenceExisted) { $localNativeErrorPreference.Value } else { $null }
            try {
                Set-Variable -Name PSNativeCommandUseErrorActionPreference -Scope Local -Value $false
                & $command.executable @($command.arguments)
                $State.stages[$StageName].native.launched = $true
                $lastExitVariable = Get-Variable -Name LASTEXITCODE -ErrorAction SilentlyContinue
                if ($null -ne $lastExitVariable -and $null -ne $lastExitVariable.Value) {
                    $capturedExitCode = [int]$lastExitVariable.Value
                }
                $State.stages[$StageName].native.real_exit_code = $capturedExitCode
                Save-State -Path $StatePathValue -State $State
            }
            catch {
                $nativePrimaryException = $_.Exception
            }
            finally {
                try {
                    if ($localNativeErrorPreferenceExisted) {
                        Set-Variable -Name PSNativeCommandUseErrorActionPreference -Scope Local -Value $savedLocalNativeErrorPreference
                    }
                    else {
                        Remove-Variable -Name PSNativeCommandUseErrorActionPreference -Scope Local -ErrorAction SilentlyContinue
                    }
                    $restoredNativeErrorPreference = Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue
                    if ($nativeErrorPreferenceExisted) {
                        if ($null -eq $restoredNativeErrorPreference -or $restoredNativeErrorPreference.Value -ne $savedNativeErrorPreference) {
                            New-Failure "Native error preference restoration failed"
                        }
                    }
                    elseif ($null -ne $restoredNativeErrorPreference) {
                        New-Failure "Native error preference restoration introduced a variable"
                    }
                }
                catch {
                    if ($null -eq $nativePrimaryException) {
                        $nativePrimaryException = $_.Exception
                    }
                }
                if ($nativeTranscriptStarted) {
                    try {
                        Stop-Transcript | Out-Null
                        $nativeTranscriptStarted = $false
                    }
                    catch {
                        if ($null -eq $nativePrimaryException) {
                            $nativePrimaryException = $_.Exception
                        }
                    }
                }
            }
            if ($null -ne $nativePrimaryException) {
                throw $nativePrimaryException
            }
            if ($null -eq $capturedExitCode) {
                New-Failure "Native command returned no exit code"
            }
            if ($capturedExitCode -ne 0) {
                New-Failure "$StageName native command failed with exit code $capturedExitCode"
            }
            return $capturedExitCode
        }
        catch {
            if ($nativeTranscriptStarted) {
                try {
                    Stop-Transcript | Out-Null
                }
                catch {
                }
            }
            if ($null -ne $capturedExitCode) {
                $State.stages[$StageName].native.real_exit_code = $capturedExitCode
            }
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
    foreach ($metadataLine in @(Get-ExpectedMapMetadataLines -State $State)) {
        $lines.Add($metadataLine)
    }
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
    return ($lines -join [Environment]::NewLine) + [Environment]::NewLine
}

function Get-ExpectedMapMetadataLines {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State
    )

    return @(
        "PACKET2N_R5_SESSION_ID=$($State.session_id)",
        "PACKET2N_R5_SESSION_STARTED_UTC=$($State.utc_start)",
        "PACKET2N_R5_BEHAVIOR_SHA=$($State.behavior_sha)",
        "PACKET2N_R5_STATE_PATH=$($State.state_path)",
        "PACKET2N_R5_STATE_BINDING_SHA256=$($State.session_binding_sha256)",
        "PACKET2N_R5_GUARD_SUCCESS=1",
        "PACKET2N_R5_EVIDENCE_PATH=$($State.artifacts.evidence.path)",
        "PACKET2N_R5_EVIDENCE_SHA256=$($State.artifacts.evidence.sha256)",
        "PACKET2N_R5_TRANSCRIPT_PATH=$($State.artifacts.transcript.path)",
        "PACKET2N_R5_TRANSCRIPT_SHA256=$($State.artifacts.transcript.sha256)",
        "PACKET2N_R5_POST_SOURCE_LEFT_JSON=$(ConvertTo-CompactJson -Value (ConvertTo-SortedCanonicalObject -Value $State.post_calibration.left))",
        "PACKET2N_R5_POST_SOURCE_RIGHT_JSON=$(ConvertTo-CompactJson -Value (ConvertTo-SortedCanonicalObject -Value $State.post_calibration.right))"
    )
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
        [object[]]$ActionPairsCollection,

        [switch]$RequireBodyKeys
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
            $value = 0.0
            if (-not [double]::TryParse([string]$pair.value, [ref]$value) -or [double]::IsNaN($value) -or [double]::IsInfinity($value)) {
                New-Failure "Map log validation failed for ${StageName}: nonnumeric value for $key"
            }
            if ($ExpectedMapKeys -ccontains $key) {
                $seen[$key] = $value
                continue
            }
            if ($ExpectedBodyKeys -ccontains $key) {
                if (-not $RequireBodyKeys) {
                    New-Failure "Map log validation failed for ${StageName}: unexpected key $key"
                }
                if ($value -ne 0.0) {
                    New-Failure "Map log validation failed for ${StageName}: body key $key must be exactly 0"
                }
                $seen[$key] = $value
                continue
            }
            New-Failure "Map log validation failed for ${StageName}: unexpected key $key"
        }
        foreach ($expectedKey in $ExpectedMapKeys) {
            if (-not $seen.ContainsKey($expectedKey)) {
                New-Failure "Map log validation failed for ${StageName}: missing key $expectedKey"
            }
            $ranges[$expectedKey].Add([double]$seen[$expectedKey])
        }
        if ($RequireBodyKeys) {
            foreach ($expectedBodyKey in $ExpectedBodyKeys) {
                if (-not $seen.ContainsKey($expectedBodyKey)) {
                    New-Failure "Map log validation failed for ${StageName}: missing body key $expectedBodyKey"
                }
            }
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
        [hashtable]$State,

        [switch]$AllowMissingSuccessTerminator,

        [switch]$AllowSyntheticTestGrammar
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
    $successRecords = @($lines | Where-Object { ([string]$_).StartsWith("CLIENT_EXIT_CODE=", [System.StringComparison]::OrdinalIgnoreCase) })
    if ($AllowMissingSuccessTerminator) {
        if ($successRecords.Count -ne 0) {
            New-Failure "Map log validation failed for ${StageName}: raw log contains a preexisting success terminator record"
        }
    }
    elseif ($successRecords.Count -ne 1 -or [string]$successRecords[0] -cne "CLIENT_EXIT_CODE=0" -or [string]$lines[-1] -cne "CLIENT_EXIT_CODE=0") {
        New-Failure "Map log validation failed for ${StageName}: success terminator count mismatch"
    }

    $expectedMetadata = @(Get-ExpectedMapMetadataLines -State $State)
    $actualMetadata = @($lines | Where-Object { ([string]$_).StartsWith("PACKET2N_R5_", [System.StringComparison]::OrdinalIgnoreCase) })
    if ($actualMetadata.Count -ne $expectedMetadata.Count) {
        New-Failure "Map log validation failed for ${StageName}: Packet map metadata count mismatch"
    }
    foreach ($line in $actualMetadata) {
        if ($expectedMetadata -cnotcontains [string]$line) {
            New-Failure "Map log validation failed for ${StageName}: Packet map metadata is unexpected or mismatched"
        }
    }
    for ($index = 0; $index -lt $expectedMetadata.Count; $index++) {
        $matching = @($actualMetadata | Where-Object { [string]$_ -ceq [string]$expectedMetadata[$index] })
        if ($matching.Count -ne 1 -or [string]$lines[$index + 1] -cne [string]$expectedMetadata[$index]) {
            New-Failure "Map log validation failed for ${StageName}: Packet map metadata is missing, duplicated, mismatched, or out of order"
        }
    }

    $expectedActualMarker = if ($StageName -eq "MapLeft") { "MAP_RUN=PHYSICAL_LEFT_ONLY" } else { "MAP_RUN=PHYSICAL_RIGHT_ONLY" }
    if (([string]$lines[0]).StartsWith("MAP_RUN=", [System.StringComparison]::Ordinal)) {
        if ([string]$lines[0] -cne $expectedActualMarker) {
            New-Failure "Map log validation failed for ${StageName}: first marker mismatch"
        }
        if ((@($lines | Where-Object { [string]$_ -ceq $ActualNoRobotProof })).Count -ne 1) {
            New-Failure "Map log validation failed for ${StageName}: no-robot proof count mismatch"
        }
        if ((@($lines | Where-Object { ([string]$_).StartsWith($ActualCleanupPrefix, [System.StringComparison]::Ordinal) })).Count -ne 1) {
            New-Failure "Map log validation failed for ${StageName}: cleanup proof count mismatch"
        }
        $actionLines = @($lines | Where-Object { ([string]$_).StartsWith("[NO_ROBOT] action -> ", [System.StringComparison]::Ordinal) })
        $collection = [System.Collections.Generic.List[object]]::new()
        foreach ($actionLine in $actionLines) {
            $collection.Add((Parse-PythonActionPairs -Line $actionLine))
        }
        Validate-ActionPairs -StageName $StageName -ActionPairsCollection @($collection) -RequireBodyKeys
        return
    }
    if (-not $AllowSyntheticTestGrammar) {
        New-Failure "Map log validation failed for ${StageName}: synthetic map grammar is test-only"
    }
    if ([string]$lines[0] -cne "RUN_MARKER=$StageName") {
        New-Failure "Map log validation failed for ${StageName}: first marker mismatch"
    }
    if ((@($lines | Where-Object { [string]$_ -ceq "NO_ROBOT_PROOF=1" })).Count -ne 1) {
        New-Failure "Map log validation failed for ${StageName}: no-robot proof count mismatch"
    }
    if ((@($lines | Where-Object { [string]$_ -ceq "CLEANUP_PROOF=1" })).Count -ne 1) {
        New-Failure "Map log validation failed for ${StageName}: cleanup proof count mismatch"
    }

    $samples = @($lines | Where-Object { ([string]$_).StartsWith("SAMPLE ", [System.StringComparison]::Ordinal) })
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

function Get-AllowSyntheticMapGrammar {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Plan
    )

    return (
        [bool]$Plan.is_test_mode -and
        $Plan.ContainsKey("allow_synthetic_map_logs") -and
        [bool]$Plan.allow_synthetic_map_logs
    )
}

function Assert-CompletedMapArtifacts {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan
    )

    $allowSynthetic = Get-AllowSyntheticMapGrammar -Plan $Plan
    foreach ($entry in @(
        [ordered]@{ stage = "MapLeft"; artifact = "map_left" },
        [ordered]@{ stage = "MapRight"; artifact = "map_right" }
    )) {
        if ($State.completed_stages -cnotcontains $entry.stage) {
            continue
        }
        $artifact = $State.artifacts[$entry.artifact]
        if ($null -eq $artifact -or [string]::IsNullOrEmpty([string]$artifact.sha256)) {
            New-Failure "Map log validation failed for $($entry.stage): stored artifact identity is missing"
        }
        if ((Get-Sha256Hex -Path $artifact.path) -cne $artifact.sha256) {
            New-Failure "Map log validation failed for $($entry.stage): stored hash mismatch"
        }
        Validate-MapLog -StageName $entry.stage -Path $artifact.path -State $State -AllowSyntheticTestGrammar:$allowSynthetic
    }
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

function Assert-PostCalibrationFreshness {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan
    )

    if ($null -eq $State.post_calibration) {
        New-Failure "Post-calibration identities are missing"
    }
    $sessionStart = [datetime]::Parse($State.utc_start, $null, [System.Globalization.DateTimeStyles]::RoundtripKind)
    $originalHashes = @(
        [string]$Plan.calibration.left.backup_sha256,
        [string]$Plan.calibration.right.backup_sha256
    )
    foreach ($side in @("left", "right")) {
        $post = $State.post_calibration[$side]
        $pre = $State.pre_calibration[$side]
        Assert-CalibrationSchema -Calibration $post.calibration -Label "$side post-calibration"
        if ($post.path -cne $Plan.calibration[$side].path) {
            New-Failure "$side post-calibration path is invalid"
        }
        if ($post.size -le 0) {
            New-Failure "$side post-calibration size is invalid"
        }
        if ($originalHashes -ccontains [string]$post.sha256) {
            New-Failure "$side post-calibration hash must differ from both immutable originals"
        }
        $postTime = [datetime]::Parse($post.mtime_utc, $null, [System.Globalization.DateTimeStyles]::RoundtripKind)
        $preTime = [datetime]::Parse($pre.mtime_utc, $null, [System.Globalization.DateTimeStyles]::RoundtripKind)
        if ($postTime -le $preTime -or $postTime -le $sessionStart) {
            New-Failure "$side post-calibration timestamp is not fresh"
        }
    }
    if ($State.post_calibration.left.sha256 -ceq $State.post_calibration.right.sha256) {
        New-Failure "Post-calibration identities must differ from each other"
    }
}

function Persist-StateFailurePreservingPrimary {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State,

        [Parameter(Mandatory = $true)]
        [string]$StageName,

        [Parameter(Mandatory = $true)]
        [string]$StatePathValue,

        [Parameter(Mandatory = $true)]
        [System.Exception]$PrimaryException
    )

    try {
        Update-StateForFailure -State $State -StageName $StageName -StatePathValue $StatePathValue -Message $PrimaryException.Message
    }
    catch {
    }
    throw $PrimaryException
}

function Invoke-CalibrateStage {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [string]$StatePathValue
    )

    $sessionId = if ($Plan.ContainsKey("session_id")) { [string]$Plan.session_id } else { $null }
    Assert-TestModeMutablePaths -Plan $Plan -StatePathValue $StatePathValue -SessionId $sessionId
    Assert-RepoAndEnvGuards -Plan $Plan
    Assert-ImmutableManifestAndBackups -Plan $Plan
    Assert-OriginalCalibrationIdentities -Plan $Plan
    if (Test-Path -LiteralPath $StatePathValue) {
        New-Failure "Calibrate refuses when the state path already exists"
    }
    if ([string]::IsNullOrEmpty($sessionId)) {
        New-Failure "Calibrate requires a reserved session ID"
    }
    $reservedArtifacts = Get-ReservedArtifactPaths -Plan $Plan -SessionId $sessionId
    foreach ($artifactName in @("transcript", "evidence", "map_left", "map_right")) {
        Assert-PathMissing -Path ([string]$reservedArtifacts[$artifactName])
    }
    $state = New-InitialState -Plan $Plan -StatePathValue $StatePathValue
    Save-State -Path $StatePathValue -State $state
    try {
        $transcriptPath = $state.artifacts.transcript.path
        $evidencePath = $state.artifacts.evidence.path
        Assert-TestModePath -Plan $Plan -Path $StatePathValue
        Assert-TestModePath -Plan $Plan -Path $transcriptPath
        Assert-TestModePath -Plan $Plan -Path $evidencePath
        Assert-PathMissing -Path $transcriptPath
        Assert-PathMissing -Path $evidencePath
        $command = Build-StageCommand -StageName "Calibrate" -Plan $Plan
        $state.stages.Calibrate.native.executable = $command.executable
        $state.stages.Calibrate.native.arguments = @($command.arguments)
        Save-State -Path $StatePathValue -State $state
        $headerLines = @(Get-CalibrationTranscriptHeaderLines -State $state -Executable $command.executable -Arguments @($command.arguments))
        $useDirectNativeProbe = Test-UseDirectNativeExitProbe -StageName "Calibrate" -Plan $Plan
        if ([bool]$Plan.is_test_mode -and -not $useDirectNativeProbe) {
            Invoke-SharedExecutor -StageName "Calibrate" -Plan $Plan -State $state -StatePathValue $StatePathValue
            $rawTranscriptLines = @(([string]$Plan.stage_plan.Calibrate.transcript_text) -split "`r?`n" | Where-Object {
                $_ -ne "" -and -not ([string]$_).StartsWith("CALIBRATION_EXIT_CODE=", [System.StringComparison]::Ordinal)
            })
            $transcriptLines = @($headerLines + $rawTranscriptLines)
            Write-TextAtomic -Path $transcriptPath -Text (($transcriptLines -join [Environment]::NewLine) + [Environment]::NewLine)
            Write-JsonAtomic -Path $Plan.calibration.left.path -Value $Plan.stage_plan.Calibrate.post_calibration.left -Overwrite
            Write-JsonAtomic -Path $Plan.calibration.right.path -Value $Plan.stage_plan.Calibrate.post_calibration.right -Overwrite
        }
        else {
            Invoke-SharedExecutor -StageName "Calibrate" -Plan $Plan -State $state -StatePathValue $StatePathValue -OutputPath $transcriptPath -HeaderLines $headerLines
        }
        $postIdentities = Get-CurrentIdentities -Plan $Plan
        $state.post_calibration = [ordered]@{
            left  = $postIdentities.left
            right = $postIdentities.right
        }
        Assert-PostCalibrationFreshness -State $state -Plan $Plan
        Append-TextLine -Path $transcriptPath -Text "CALIBRATION_EXIT_CODE=0"
        $state.classification = "VALID_FRESH_CALIBRATION"
        $state.completed_stages = @("Calibrate")
        $state.next_stage = "MapLeft"
        $state.artifacts.transcript = Get-FileInfoSnapshot -Path $transcriptPath
        $evidencePayload = Build-EvidencePayload -State $state -Executable $command.executable -Arguments @($command.arguments) -TranscriptPath $transcriptPath
        Write-JsonAtomic -Path $evidencePath -Value $evidencePayload
        $state.artifacts.evidence = Get-FileInfoSnapshot -Path $evidencePath
        Assert-EvidenceSemantics -State $state -Plan $Plan
        $state.stages.Calibrate.result = "completed"
        $state.summaries.Calibrate = "Calibration completed"
        Save-State -Path $StatePathValue -State $state
        return
    }
    catch {
        Persist-StateFailurePreservingPrimary -State $state -StageName "Calibrate" -StatePathValue $StatePathValue -PrimaryException $_.Exception
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
    $issues = @(Get-StateValidationIssues -State $state -Plan $Plan)
    if ($issues.Count -gt 0) {
        New-Failure ("INVALID_OR_UNCERTAIN_STATE: " + ($issues -join ", "))
    }
    Assert-TestModeMutablePaths -Plan $Plan -StatePathValue $StatePathValue -SessionId ([string]$state.session_id)
    Assert-StateIdentity -State $state
    Assert-StateProvenance -State $state -StatePathValue $StatePathValue -Plan $Plan
    Assert-RepoAndEnvGuards -Plan $Plan
    Assert-ImmutableManifestAndBackups -Plan $Plan
    if ($StageName -ceq "MapLeft" -and ($state.completed_stages -cnotcontains "Calibrate")) {
        New-Failure "Calibrate must complete before MapLeft"
    }
    if ($StageName -ceq "MapRight" -and ($state.completed_stages -cnotcontains "MapLeft")) {
        New-Failure "MapLeft must complete before MapRight"
    }
    Assert-EvidenceAndCalibrationStillMatch -State $state -Plan $Plan
    Assert-CompletedMapArtifacts -State $state -Plan $Plan
    try {
        $mapArtifactKey = if ($StageName -eq "MapLeft") { "map_left" } else { "map_right" }
        $mapPath = $state.artifacts[$mapArtifactKey].path
        Assert-TestModePath -Plan $Plan -Path $mapPath
        Assert-PathMissing -Path $mapPath
        $command = Build-StageCommand -StageName $StageName -Plan $Plan
        $state.stages[$StageName].native.executable = $command.executable
        $state.stages[$StageName].native.arguments = @($command.arguments)
        Save-State -Path $StatePathValue -State $state
        if ([bool]$Plan.is_test_mode) {
            Invoke-SharedExecutor -StageName $StageName -Plan $Plan -State $state -StatePathValue $StatePathValue
            $mapText = New-MapLogText -StageName $StageName -State $state -PhysicalSide $Plan.stage_plan[$StageName].physical_side
            if ($Plan.stage_plan[$StageName].ContainsKey("raw_extra_lines")) {
                $mapText += (@($Plan.stage_plan[$StageName].raw_extra_lines) -join [Environment]::NewLine) + [Environment]::NewLine
            }
            Write-TextAtomic -Path $mapPath -Text $mapText
        }
        else {
            $marker = if ($StageName -eq "MapLeft") { "PHYSICAL_LEFT_ONLY" } else { "PHYSICAL_RIGHT_ONLY" }
            $headerLines = @("MAP_RUN=$marker") + @(Get-ExpectedMapMetadataLines -State $state)
            Invoke-SharedExecutor -StageName $StageName -Plan $Plan -State $state -StatePathValue $StatePathValue -OutputPath $mapPath -HeaderLines $headerLines
        }
        $allowSynthetic = Get-AllowSyntheticMapGrammar -Plan $Plan
        Validate-MapLog -StageName $StageName -Path $mapPath -State $state -AllowMissingSuccessTerminator -AllowSyntheticTestGrammar:$allowSynthetic
        Append-TextLine -Path $mapPath -Text "CLIENT_EXIT_CODE=0"
        $state.artifacts[$mapArtifactKey] = [ordered]@{
            path   = $mapPath
            sha256 = Get-Sha256Hex -Path $mapPath
        }
        Validate-MapLog -StageName $StageName -Path $mapPath -State $state -AllowSyntheticTestGrammar:$allowSynthetic
        $state.stages[$StageName].result = "completed"
        if ($state.completed_stages -cnotcontains $StageName) {
            $state.completed_stages = @($state.completed_stages + $StageName)
        }
        $state.summaries[$StageName] = "$StageName completed"
        $state.next_stage = if ($StageName -eq "MapLeft") { "MapRight" } else { "Verify" }
        Save-State -Path $StatePathValue -State $state
        return
    }
    catch {
        Persist-StateFailurePreservingPrimary -State $state -StageName $StageName -StatePathValue $StatePathValue -PrimaryException $_.Exception
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
    $issues = @(Get-StateValidationIssues -State $state -Plan $Plan)
    if ($issues.Count -gt 0) {
        New-Failure ("INVALID_OR_UNCERTAIN_STATE: " + ($issues -join ", "))
    }
    Assert-TestModeMutablePaths -Plan $Plan -StatePathValue $StatePathValue -SessionId ([string]$state.session_id)
    Assert-StateIdentity -State $state
    Assert-StateProvenance -State $state -StatePathValue $StatePathValue -Plan $Plan
    Assert-RepoAndEnvGuards -Plan $Plan
    Assert-ImmutableManifestAndBackups -Plan $Plan
    Assert-EvidenceAndCalibrationStillMatch -State $state -Plan $Plan
    if ($state.completed_stages -cnotcontains "MapLeft" -or $state.completed_stages -cnotcontains "MapRight") {
        New-Failure "Verify requires both map artifacts"
    }
    Assert-CompletedMapArtifacts -State $state -Plan $Plan
    $state.stages.Verify.result = "completed"
    if ($state.completed_stages -cnotcontains "Verify") {
        $state.completed_stages = @($state.completed_stages + "Verify")
    }
    $state.final_result = "MAPPING_RESULT=CORRECT"
    $state.next_stage = $null
    $state.summaries.Verify = "Mapping verified"
    Save-State -Path $StatePathValue -State $state
    [Console]::Out.WriteLine("MAPPING_RESULT=CORRECT")
}

function Get-InterruptedRecoveryJournalPath {
    param([Parameter(Mandatory = $true)][string]$StatePathValue)

    return "$StatePathValue.recover-interrupted-calibration.json"
}

function Get-InterruptedRecoveryPaths {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Plan,
        [Parameter(Mandatory = $true)][string]$StatePathValue,
        [Parameter(Mandatory = $true)][string]$SessionId
    )

    $active = Get-ActiveCalibrationDirectory -Plan $Plan
    $activeParent = [System.IO.Path]::GetDirectoryName($active)
    $archive = Join-Path ([string]$Plan.rejected_archive_root) "packet2n-r5-interrupted-$SessionId"
    $paths = [ordered]@{
        journal         = Get-InterruptedRecoveryJournalPath -StatePathValue $StatePathValue
        archive         = $archive
        archive_staging = "$archive.staging"
        active          = $active
        staged_original = Join-Path $activeParent ".packet2n-r5-interrupted-original-$SessionId"
        rollback        = Join-Path $activeParent ".packet2n-r5-interrupted-rejected-$SessionId"
    }
    $calibrationRoot = [System.IO.Path]::GetFullPath([string]$Plan.calibration_root)
    $stateRoot = [System.IO.Path]::GetFullPath([string]$Plan.state_root)
    $archiveRoot = [System.IO.Path]::GetFullPath([string]$Plan.rejected_archive_root)
    Assert-RestartPathConfined -Path $paths.active -Root $calibrationRoot -Label "interrupted active calibration path"
    Assert-RestartPathConfined -Path $paths.staged_original -Root $activeParent -Label "interrupted staged-original path"
    Assert-RestartPathConfined -Path $paths.rollback -Root $activeParent -Label "interrupted rollback path"
    Assert-RestartPathConfined -Path $StatePathValue -Root $stateRoot -Label "interrupted source state path"
    Assert-RestartPathConfined -Path $paths.journal -Root $stateRoot -Label "interrupted journal path"
    Assert-RestartPathConfined -Path $paths.archive -Root $archiveRoot -Label "interrupted archive path"
    Assert-RestartPathConfined -Path $paths.archive_staging -Root $archiveRoot -Label "interrupted archive staging path"
    Assert-RestartSameVolume -First $paths.active -Second $paths.staged_original -Label "interrupted original activation"
    Assert-RestartSameVolume -First $paths.active -Second $paths.rollback -Label "interrupted active withdrawal"
    Assert-RestartSameVolume -First $paths.active -Second $paths.archive -Label "interrupted active retirement"
    Assert-RestartSameVolume -First $StatePathValue -Second $paths.archive -Label "interrupted state retirement"
    foreach ($path in $paths.Values) {
        Assert-TestModePath -Plan $Plan -Path ([string]$path)
    }
    return $paths
}

function Get-InterruptedPinnedAuthority {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Plan,
        [Parameter(Mandatory = $true)][string]$StatePathValue
    )

    if ([bool]$Plan.is_test_mode) {
        if (-not $Plan.ContainsKey("interrupted_legacy_fixture")) {
            New-Failure "Test-mode interrupted-calibration authority is missing"
        }
        $fixture = $Plan.interrupted_legacy_fixture
        Assert-ExactKeySet -Value $fixture -ExpectedKeys @(
            "schema_version", "repo_head", "runner_sha256", "behavior_sha", "session_id", "state",
            "active", "transcript", "source_evidence_present", "traceback_text_present"
        ) -Message "Test-mode interrupted-calibration fixture schema is invalid"
        Assert-ExactKeySet -Value $fixture.state -ExpectedKeys @("path", "sha256", "size") -Message "Test-mode interrupted state fixture is invalid"
        Assert-ExactKeySet -Value $fixture.active -ExpectedKeys @("left", "right") -Message "Test-mode interrupted active fixture is invalid"
        foreach ($side in @("left", "right")) {
            Assert-ExactKeySet -Value $fixture.active[$side] -ExpectedKeys @("path", "sha256", "size", "mtime_utc", "calibration") -Message "Test-mode interrupted $side fixture is invalid"
            Assert-CalibrationSchema -Calibration $fixture.active[$side].calibration -Label "test-mode interrupted $side fixture"
        }
        Assert-ExactKeySet -Value $fixture.transcript -ExpectedKeys @("path", "sha256", "size", "mtime_utc") -Message "Test-mode interrupted transcript fixture is invalid"
        if ($fixture.schema_version -cne "1" -or
            $fixture.behavior_sha -cne $BehaviorBaseline -or
            $fixture.state.path -cne $StatePathValue -or
            $fixture.source_evidence_present -ne $false -or
            $fixture.traceback_text_present -ne $false) {
            New-Failure "Test-mode interrupted-calibration fixture identity is invalid"
        }
        return $fixture
    }

    if ($StatePathValue -cne (Join-Path $RealLogsDirectory "packet2n-r5-state.json")) {
        New-Failure "Interrupted-calibration recovery state path is not the pinned production path"
    }
    return [ordered]@{
        schema_version          = "1"
        repo_head               = $InterruptedRepoHead
        runner_sha256           = $InterruptedRunnerSha256
        behavior_sha            = $BehaviorBaseline
        session_id              = $InterruptedSessionId
        state                   = [ordered]@{ path = $StatePathValue; sha256 = $InterruptedStateSha256; size = $InterruptedStateSize }
        active                  = [ordered]@{
            left = [ordered]@{
                path = $Plan.calibration.left.path; sha256 = $InterruptedActiveLeftSha256; size = $InterruptedActiveLeftSize
                mtime_utc = $InterruptedActiveLeftMtimeUtc
            }
            right = [ordered]@{
                path = $Plan.calibration.right.path; sha256 = $RealBackupMetadata.right.sha256; size = $RealBackupMetadata.right.size
                mtime_utc = $RealBackupMetadata.right.source_mtime
            }
        }
        transcript              = [ordered]@{
            path = Join-Path $RealLogsDirectory "packet2n-r5-calibration-$InterruptedSessionId.log"
            sha256 = $InterruptedTranscriptSha256
            size = $InterruptedTranscriptSize
            mtime_utc = $InterruptedTranscriptMtimeUtc
        }
        source_evidence_present = $false
        traceback_text_present  = $false
    }
}

function Get-InterruptedRecoveryAuthority {
    param(
        [Parameter(Mandatory = $true)][hashtable]$State,
        [Parameter(Mandatory = $true)][hashtable]$Plan,
        [Parameter(Mandatory = $true)][string]$StatePathValue
    )

    $authority = Get-InterruptedPinnedAuthority -Plan $Plan -StatePathValue $StatePathValue
    if ($authority.repo_head -cne $State.repo_head -or
        $authority.runner_sha256 -cne $State.runner_sha -or
        $authority.session_id -cne $State.session_id) {
        New-Failure "Interrupted-calibration source state does not match pinned authority"
    }
    if (-not [bool]$Plan.is_test_mode) {
        foreach ($side in @("left", "right")) {
            $snapshot = Get-CalibrationSnapshot -Path $Plan.calibration[$side].path -Label "interrupted active $side"
            $authority.active[$side]["calibration"] = $snapshot.calibration
        }
    }
    return $authority
}

function Assert-InterruptedCalibrationCandidate {
    param(
        [Parameter(Mandatory = $true)][hashtable]$State,
        [Parameter(Mandatory = $true)][hashtable]$Plan,
        [Parameter(Mandatory = $true)][string]$StatePathValue
    )

    Assert-TestModeMutablePaths -Plan $Plan -StatePathValue $StatePathValue -SessionId ([string]$State.session_id)
    Assert-RepoAndEnvGuards -Plan $Plan
    Assert-ImmutableManifestAndBackups -Plan $Plan
    $issues = @(Get-StateValidationIssues -State $State -Plan $Plan)
    if ($issues.Count -gt 0) {
        New-Failure ("Interrupted-calibration state is invalid: " + ($issues -join ", "))
    }
    Assert-StateIdentity -State $State
    Assert-ReservedArtifactPaths -State $State -Plan $Plan
    $authority = Get-InterruptedRecoveryAuthority -State $State -Plan $Plan -StatePathValue $StatePathValue
    if ((Get-Sha256Hex -Path $StatePathValue) -cne $authority.state.sha256 -or
        [int64](Get-Item -LiteralPath $StatePathValue).Length -ne [int64]$authority.state.size) {
        New-Failure "Interrupted-calibration source state identity is invalid"
    }
    if ($State.session_id -cne $authority.session_id -or
        $State.repo_head -cne $authority.repo_head -or
        $State.runner_sha -cne $authority.runner_sha256 -or
        $State.behavior_sha -cne $authority.behavior_sha -or
        $State.utc_start -cne $(if ([bool]$Plan.is_test_mode) { $State.utc_start } else { $InterruptedSessionStartUtc }) -or
        $State.classification -cne "ORIGINAL_CALIBRATION_INTACT" -or
        $State.next_stage -cne "Calibrate" -or
        $null -ne $State.final_result -or
        $null -ne $State.post_calibration -or
        @($State.completed_stages).Count -ne 0 -or
        @($State.failed_stages).Count -ne 1 -or [string]$State.failed_stages[0] -cne "Calibrate") {
        New-Failure "Interrupted-calibration source state is not the exact failed Calibrate authority"
    }
    if (@($State.summaries.Keys).Count -ne 1 -or @($State.summaries.Keys) -cnotcontains "Calibrate" -or
        [string]::IsNullOrWhiteSpace([string]$State.summaries.Calibrate)) {
        New-Failure "Interrupted-calibration failed summary is invalid"
    }
    $native = $State.stages.Calibrate.native
    $expectedCommand = Build-StageCommand -StageName "Calibrate" -Plan $Plan
    if ($State.stages.Calibrate.result -cne "failed" -or $native.attempted -ne $true -or $native.launched -ne $true -or
        -not (Test-IsJsonInteger -Value $native.real_exit_code) -or [int64]$native.real_exit_code -eq 0) {
        New-Failure "Interrupted-calibration native failure truth is invalid"
    }
    Assert-ExactCommand -Actual $native -Expected $expectedCommand -Message "Interrupted-calibration command is invalid"
    foreach ($stageName in @("MapLeft", "MapRight", "Verify")) {
        $stage = $State.stages[$stageName]
        if ($stage.result -cne "pending" -or $stage.native.attempted -ne $false -or $stage.native.launched -ne $false -or
            $null -ne $stage.native.real_exit_code -or $null -ne $stage.native.executable -or @($stage.native.arguments).Count -ne 0) {
            New-Failure "Interrupted-calibration state records an attempted map or verify stage"
        }
    }
    foreach ($side in @("left", "right")) {
        $current = Get-CalibrationSnapshot -Path $Plan.calibration[$side].path -Label "interrupted active $side"
        if (-not (Test-SnapshotMatchesIdentity -Snapshot $current -Identity $authority.active[$side])) {
            New-Failure "Interrupted-calibration active $side identity changed"
        }
        $pre = $State.pre_calibration[$side]
        $backupCalibration = Read-JsonFile -Path $Plan.calibration[$side].backup_path
        $original = [ordered]@{
            path = $Plan.calibration[$side].path; sha256 = $Plan.calibration[$side].backup_sha256
            size = [int64]$Plan.calibration[$side].backup_size; mtime_utc = $Plan.calibration[$side].source_mtime_utc
            calibration = $backupCalibration
        }
        if (-not (Test-ExactValue -Actual $pre -Expected $original)) {
            New-Failure "Interrupted-calibration pre-calibration $side identity is invalid"
        }
    }
    if (Test-CurrentIdentitiesAreExactOriginals -Current (Get-CurrentIdentities -Plan $Plan) -Plan $Plan) {
        New-Failure "Interrupted-calibration active pair is not interrupted"
    }
    foreach ($artifactName in @("evidence", "map_left", "map_right")) {
        if (Test-Path -LiteralPath ([string]$State.artifacts[$artifactName].path)) {
            New-Failure "Interrupted-calibration $artifactName artifact must be absent"
        }
        if ($null -ne $State.artifacts[$artifactName].sha256) {
            New-Failure "Interrupted-calibration $artifactName identity must be empty"
        }
    }
    $transcriptPath = [string]$State.artifacts.transcript.path
    if ($transcriptPath -cne $authority.transcript.path -or -not (Test-Path -LiteralPath $transcriptPath -PathType Leaf) -or
        (Get-Sha256Hex -Path $transcriptPath) -cne $authority.transcript.sha256 -or
        [int64](Get-Item -LiteralPath $transcriptPath).Length -ne [int64]$authority.transcript.size -or
        (Get-FileTimestampUtc -Path $transcriptPath) -cne $authority.transcript.mtime_utc -or
        $null -ne $State.artifacts.transcript.sha256 -or $null -ne $State.artifacts.transcript.size) {
        New-Failure "Interrupted-calibration failed transcript identity is invalid"
    }
    $expectedHeader = @(Get-CalibrationTranscriptHeaderLines -State $State -Executable $expectedCommand.executable -Arguments @($expectedCommand.arguments))
    $actualLines = @([System.IO.File]::ReadAllLines($transcriptPath))
    if ($actualLines.Count -lt $expectedHeader.Count) {
        New-Failure "Interrupted-calibration transcript header is incomplete"
    }
    for ($index = 0; $index -lt $expectedHeader.Count; $index++) {
        if ([string]$actualLines[$index] -cne [string]$expectedHeader[$index]) {
            New-Failure "Interrupted-calibration transcript header is invalid"
        }
    }
    if ($actualLines -ccontains "CALIBRATION_EXIT_CODE=0") {
        New-Failure "Interrupted-calibration transcript contains a success terminator"
    }
    return $authority
}

function Get-InterruptedPairLayout {
    param(
        [Parameter(Mandatory = $true)][string]$Directory,
        [Parameter(Mandatory = $true)][hashtable]$Plan,
        [Parameter(Mandatory = $true)][hashtable]$SourceActive
    )

    if (-not (Test-Path -LiteralPath $Directory)) { return "missing" }
    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) { return "unrecognized" }
    $item = Get-Item -LiteralPath $Directory -Force
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { return "unrecognized" }
    $entries = @(Get-ChildItem -LiteralPath $Directory -Force)
    $names = @(
        [System.IO.Path]::GetFileName([string]$Plan.calibration.left.path),
        [System.IO.Path]::GetFileName([string]$Plan.calibration.right.path)
    )
    if ($entries.Count -ne 2) { return "unrecognized" }
    foreach ($entry in $entries) {
        if ($entry -isnot [System.IO.FileInfo] -or
            ($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            $names -cnotcontains $entry.Name) { return "unrecognized" }
    }
    $allInterrupted = $true
    $allOriginal = $true
    foreach ($side in @("left", "right")) {
        $name = [System.IO.Path]::GetFileName([string]$Plan.calibration[$side].path)
        try { $snapshot = Get-CalibrationSnapshot -Path (Join-Path $Directory $name) -Label "interrupted $side pair" }
        catch { return "unrecognized" }
        if (-not (Test-SnapshotMatchesIdentity -Snapshot $snapshot -Identity $SourceActive[$side])) {
            $allInterrupted = $false
        }
        $original = [ordered]@{
            sha256 = $Plan.calibration[$side].backup_sha256; size = [int64]$Plan.calibration[$side].backup_size
            mtime_utc = $Plan.calibration[$side].source_mtime_utc
            calibration = Read-JsonFile -Path $Plan.calibration[$side].backup_path
        }
        if (-not (Test-SnapshotMatchesIdentity -Snapshot $snapshot -Identity $original)) { $allOriginal = $false }
    }
    if ($allInterrupted -and -not $allOriginal) { return "interrupted" }
    if ($allOriginal -and -not $allInterrupted) { return "original" }
    return "unrecognized"
}

function New-InterruptedRecoveryJournal {
    param(
        [Parameter(Mandatory = $true)][hashtable]$State,
        [Parameter(Mandatory = $true)][hashtable]$Plan,
        [Parameter(Mandatory = $true)][string]$StatePathValue,
        [Parameter(Mandatory = $true)][hashtable]$Paths,
        [Parameter(Mandatory = $true)][hashtable]$Authority
    )

    return [ordered]@{
        schema_version         = "1"
        transaction_type       = "packet2n-r5-interrupted-calibration"
        status                 = "in_progress"
        phase                  = "initialized"
        reason                 = $InterruptedRecoveryReason
        session_id             = $State.session_id
        session_start_utc      = $State.utc_start
        state_binding_sha256   = $State.session_binding_sha256
        state_path             = $StatePathValue
        archive_path           = $Paths.archive
        archive_staging_path   = $Paths.archive_staging
        active_directory       = $Paths.active
        staged_original_path   = $Paths.staged_original
        rollback_path          = $Paths.rollback
        source_state           = [ordered]@{
            sha256 = $Authority.state.sha256; size = [int64]$Authority.state.size
            mtime_utc = Get-FileTimestampUtc -Path $StatePathValue
        }
        source_active          = $Authority.active
        source_transcript      = $Authority.transcript
        source_evidence_present = $false
        traceback_text_present = $false
        native_stage_truth     = $State.stages
        ports                  = $State.ports
        command                = [ordered]@{
            executable = $State.stages.Calibrate.native.executable
            arguments = @($State.stages.Calibrate.native.arguments)
        }
        source_provenance      = [ordered]@{
            repo_head = $State.repo_head; runner_sha256 = $State.runner_sha; behavior_sha = $State.behavior_sha
        }
        recovery_provenance    = [ordered]@{
            repo_head = $Plan.head; runner_sha256 = Get-RunnerSha256; behavior_sha = $BehaviorBaseline
        }
        archive_record_sha256  = $null
    }
}

function Assert-InterruptedRecoveryJournal {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Journal,
        [Parameter(Mandatory = $true)][hashtable]$Plan,
        [Parameter(Mandatory = $true)][string]$StatePathValue
    )

    Assert-ExactKeySet -Value $Journal -ExpectedKeys @(
        "schema_version", "transaction_type", "status", "phase", "reason", "session_id", "session_start_utc",
        "state_binding_sha256", "state_path", "archive_path", "archive_staging_path", "active_directory",
        "staged_original_path", "rollback_path", "source_state", "source_active", "source_transcript",
        "source_evidence_present", "traceback_text_present", "native_stage_truth", "ports", "command",
        "source_provenance", "recovery_provenance", "archive_record_sha256"
    ) -Message "Interrupted-calibration recovery journal schema is invalid"
    $authority = Get-InterruptedPinnedAuthority -Plan $Plan -StatePathValue $StatePathValue
    if ($Journal.schema_version -cne "1" -or
        $Journal.transaction_type -cne "packet2n-r5-interrupted-calibration" -or
        $Journal.status -cne "in_progress" -or
        $Journal.reason -cne $InterruptedRecoveryReason -or
        $Journal.session_id -cne $authority.session_id -or
        $Journal.source_evidence_present -ne $false -or $Journal.traceback_text_present -ne $false) {
        New-Failure "Interrupted-calibration recovery journal identity is invalid"
    }
    if (@("initialized", "archive_staged", "archive_published", "active_withdrawn", "original_activated", "rejected_active_retired", "state_retired") -cnotcontains [string]$Journal.phase) {
        New-Failure "Interrupted-calibration recovery journal phase is invalid"
    }
    Assert-ExactKeySet -Value $Journal.source_state -ExpectedKeys @("sha256", "size", "mtime_utc") -Message "Interrupted-calibration journal source state is invalid"
    Assert-ExactKeySet -Value $Journal.source_active -ExpectedKeys @("left", "right") -Message "Interrupted-calibration journal active pair is invalid"
    Assert-ExactKeySet -Value $Journal.source_transcript -ExpectedKeys @("path", "sha256", "size", "mtime_utc") -Message "Interrupted-calibration journal transcript is invalid"
    Assert-ExactKeySet -Value $Journal.command -ExpectedKeys @("executable", "arguments") -Message "Interrupted-calibration journal command is invalid"
    foreach ($side in @("left", "right")) {
        Assert-ExactKeySet -Value $Journal.source_active[$side] -ExpectedKeys @("path", "sha256", "size", "mtime_utc", "calibration") -Message "Interrupted-calibration journal $side active identity is invalid"
        $expectedActive = $authority.active[$side]
        if ($Journal.source_active[$side].path -cne $expectedActive.path -or
            $Journal.source_active[$side].sha256 -cne $expectedActive.sha256 -or
            [int64]$Journal.source_active[$side].size -ne [int64]$expectedActive.size -or
            $Journal.source_active[$side].mtime_utc -cne $expectedActive.mtime_utc) {
            New-Failure "Interrupted-calibration journal active identity does not match pinned authority"
        }
        Assert-CalibrationSchema -Calibration $Journal.source_active[$side].calibration -Label "interrupted journal $side calibration"
    }
    if ($Journal.source_state.sha256 -cne $authority.state.sha256 -or
        [int64]$Journal.source_state.size -ne [int64]$authority.state.size -or
        -not (Test-ExactValue -Actual $Journal.source_transcript -Expected $authority.transcript)) {
        New-Failure "Interrupted-calibration journal source artifacts do not match pinned authority"
    }
    $paths = Get-InterruptedRecoveryPaths -Plan $Plan -StatePathValue $StatePathValue -SessionId ([string]$Journal.session_id)
    foreach ($binding in @(
        @($Journal.state_path, $StatePathValue), @($Journal.archive_path, $paths.archive),
        @($Journal.archive_staging_path, $paths.archive_staging), @($Journal.active_directory, $paths.active),
        @($Journal.staged_original_path, $paths.staged_original), @($Journal.rollback_path, $paths.rollback)
    )) {
        if ([string]$binding[0] -cne [string]$binding[1]) { New-Failure "Interrupted-calibration journal path binding is invalid" }
    }
    $stateName = [System.IO.Path]::GetFileName($StatePathValue)
    $pinnedStatePath = if (Test-Path -LiteralPath $StatePathValue -PathType Leaf) {
        $StatePathValue
    }
    elseif (Test-Path -LiteralPath (Join-Path $paths.archive (Join-Path "state-snapshot" $stateName)) -PathType Leaf) {
        Join-Path $paths.archive (Join-Path "state-snapshot" $stateName)
    }
    elseif (Test-Path -LiteralPath (Join-Path $paths.archive_staging (Join-Path "state-snapshot" $stateName)) -PathType Leaf) {
        Join-Path $paths.archive_staging (Join-Path "state-snapshot" $stateName)
    }
    else {
        New-Failure "Interrupted-calibration pinned source state is unavailable"
    }
    if ((Get-Sha256Hex -Path $pinnedStatePath) -cne $authority.state.sha256 -or
        [int64](Get-Item -LiteralPath $pinnedStatePath).Length -ne [int64]$authority.state.size -or
        (Get-FileTimestampUtc -Path $pinnedStatePath) -cne $Journal.source_state.mtime_utc) {
        New-Failure "Interrupted-calibration journal source state does not match pinned authority"
    }
    $pinnedState = Read-JsonFile -Path $pinnedStatePath
    $expectedCommand = [ordered]@{
        executable = $pinnedState.stages.Calibrate.native.executable
        arguments = @($pinnedState.stages.Calibrate.native.arguments)
    }
    $expectedProvenance = [ordered]@{
        repo_head = $pinnedState.repo_head
        runner_sha256 = $pinnedState.runner_sha
        behavior_sha = $pinnedState.behavior_sha
    }
    if ($Journal.session_start_utc -cne $pinnedState.utc_start -or
        $Journal.state_binding_sha256 -cne $pinnedState.session_binding_sha256 -or
        -not (Test-ExactValue -Actual $Journal.native_stage_truth -Expected $pinnedState.stages) -or
        -not (Test-ExactValue -Actual $Journal.ports -Expected $pinnedState.ports) -or
        -not (Test-ExactValue -Actual $Journal.command -Expected $expectedCommand) -or
        -not (Test-ExactValue -Actual $Journal.source_provenance -Expected $expectedProvenance)) {
        New-Failure "Interrupted-calibration journal does not match pinned authority"
    }
    if ($Journal.recovery_provenance.repo_head -cne $Plan.head -or
        $Journal.recovery_provenance.runner_sha256 -cne (Get-RunnerSha256) -or
        $Journal.recovery_provenance.behavior_sha -cne $BehaviorBaseline) {
        New-Failure "Interrupted-calibration recovery provenance is invalid"
    }
    return $paths
}

function Save-InterruptedRecoveryJournal {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][hashtable]$Journal,
        [Parameter(Mandatory = $true)][hashtable]$Plan
    )

    Write-RestartJsonDurable -Path $Path -Value $Journal -Plan $Plan -Overwrite
}

function Get-InterruptedDerivedEvidence {
    param([Parameter(Mandatory = $true)][hashtable]$Journal)

    return [ordered]@{
        schema_version          = "1"
        evidence_type           = "packet2n-r5-interrupted-calibration-derived"
        reason                  = $InterruptedRecoveryReason
        session_id              = $Journal.session_id
        session_start_utc       = $Journal.session_start_utc
        source_evidence_present = $false
        traceback_text_present  = $false
        native_attempted        = $true
        native_launched         = $true
        native_exit_code        = [int64]$Journal.native_stage_truth.Calibrate.native.real_exit_code
        command                 = $Journal.command
        ports                   = $Journal.ports
        rejected                = $true
        mapping_eligible        = $false
    }
}

function New-InterruptedArchiveStaging {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Journal,
        [Parameter(Mandatory = $true)][hashtable]$Plan
    )

    Assert-PathMissing -Path $Journal.archive_path
    if (-not (Test-Path -LiteralPath $Journal.archive_staging_path)) {
        [void][System.IO.Directory]::CreateDirectory($Journal.archive_staging_path)
    }
    $stagingItem = Get-Item -LiteralPath $Journal.archive_staging_path -Force
    if ($stagingItem -isnot [System.IO.DirectoryInfo] -or
        ($stagingItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        New-Failure "Interrupted-calibration archive staging is not a regular directory"
    }
    $stateName = [System.IO.Path]::GetFileName([string]$Journal.state_path)
    $transcriptName = [System.IO.Path]::GetFileName([string]$Journal.source_transcript.path)
    $manifestName = [System.IO.Path]::GetFileName([string]$Plan.manifest.path)
    $sources = [ordered]@{
        left_calibration = [ordered]@{ source = $Plan.calibration.left.path; relative = Join-Path "interrupted-active-calibration" ([System.IO.Path]::GetFileName([string]$Plan.calibration.left.path)) }
        right_calibration = [ordered]@{ source = $Plan.calibration.right.path; relative = Join-Path "interrupted-active-calibration" ([System.IO.Path]::GetFileName([string]$Plan.calibration.right.path)) }
        transcript = [ordered]@{ source = $Journal.source_transcript.path; relative = Join-Path "failed-transcript" $transcriptName }
        state = [ordered]@{ source = $Journal.state_path; relative = Join-Path "state-snapshot" $stateName }
        manifest = [ordered]@{ source = $Plan.manifest.path; relative = Join-Path "immutable-backup" $manifestName }
        original_left = [ordered]@{ source = $Plan.calibration.left.backup_path; relative = Join-Path "immutable-backup" ([System.IO.Path]::GetFileName([string]$Plan.calibration.left.backup_path)) }
        original_right = [ordered]@{ source = $Plan.calibration.right.backup_path; relative = Join-Path "immutable-backup" ([System.IO.Path]::GetFileName([string]$Plan.calibration.right.backup_path)) }
    }
    $allowed = @("interrupted-evidence.json", "archive-record.json", "interrupted-evidence.json.restart-durable.tmp", "archive-record.json.restart-durable.tmp")
    foreach ($entry in $sources.Values) { $allowed += $entry.relative; $allowed += "$($entry.relative).restart-copy.tmp" }
    $allowedDirectories = @("interrupted-active-calibration", "failed-transcript", "state-snapshot", "immutable-backup")
    foreach ($entry in @(Get-ChildItem -LiteralPath $Journal.archive_staging_path -Recurse -Force)) {
        $relative = [System.IO.Path]::GetRelativePath($Journal.archive_staging_path, $entry.FullName)
        if (($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            New-Failure "Interrupted-calibration archive staging contains a reparse point"
        }
        if ($entry -is [System.IO.FileInfo] -and $allowed -cnotcontains $relative) {
            New-Failure "Interrupted-calibration archive staging contains an unexpected file: $relative"
        }
        if ($entry -is [System.IO.DirectoryInfo] -and $allowedDirectories -cnotcontains $relative) {
            New-Failure "Interrupted-calibration archive staging contains an unexpected directory: $relative"
        }
    }
    $artifacts = [ordered]@{}
    $copyIndex = 0
    foreach ($name in $sources.Keys) {
        $entry = $sources[$name]
        $staged = Join-Path $Journal.archive_staging_path $entry.relative
        $published = Join-Path $Journal.archive_path $entry.relative
        Copy-RestartStagedFile -Source $entry.source -Destination $staged -Plan $Plan
        $artifacts[$name] = Get-ArchiveArtifactRecord -Source $entry.source -StagedPath $staged -PublishedPath $published
        $copyIndex++
        if ($copyIndex -eq 1) { Test-RestartFailurePoint -Plan $Plan -Point "after_first_archive_copy" }
    }
    $derivedPath = Join-Path $Journal.archive_staging_path "interrupted-evidence.json"
    Write-RestartJsonDurable -Path $derivedPath -Value (Get-InterruptedDerivedEvidence -Journal $Journal) -Plan $Plan -Overwrite
    $artifacts.interrupted_evidence = Get-ArchiveArtifactRecord -Source $derivedPath -StagedPath $derivedPath -PublishedPath (Join-Path $Journal.archive_path "interrupted-evidence.json")
    $artifacts.interrupted_evidence.source_path = "RECOVERY_DERIVED"
    $record = [ordered]@{
        schema_version       = "1"
        record_type          = "packet2n-r5-interrupted-calibration"
        reason               = $InterruptedRecoveryReason
        session_id           = $Journal.session_id
        session_start_utc    = $Journal.session_start_utc
        archive_path         = $Journal.archive_path
        archive_created_utc  = [DateTime]::UtcNow.ToString("o")
        state_binding_sha256 = $Journal.state_binding_sha256
        ports                = $Journal.ports
        command              = $Journal.command
        native_stage_truth   = $Journal.native_stage_truth
        source_provenance    = $Journal.source_provenance
        recovery_provenance  = $Journal.recovery_provenance
        source_evidence_present = $false
        traceback_text_present = $false
        rejected             = $true
        mapping_eligible     = $false
        artifacts            = $artifacts
    }
    $recordPath = Join-Path $Journal.archive_staging_path "archive-record.json"
    Write-RestartJsonDurable -Path $recordPath -Value $record -Plan $Plan -Overwrite -AfterFlushFailurePoint "after_archive_record_write"
    return Get-Sha256Hex -Path $recordPath
}

function Assert-InterruptedArchiveCore {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][hashtable]$Journal,
        [Parameter(Mandatory = $true)][hashtable]$Plan,
        [switch]$Completed
    )

    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        New-Failure "Interrupted-calibration archive is missing"
    }
    Assert-RestartPathHasNoReparsePoint -Path $Root -Boundary ([System.IO.Path]::GetFullPath([string]$Plan.rejected_archive_root)) -Label "interrupted archive"
    $recordPath = Join-Path $Root "archive-record.json"
    if (-not (Test-Path -LiteralPath $recordPath -PathType Leaf)) { New-Failure "Interrupted-calibration archive record is missing" }
    if ($null -ne $Journal.archive_record_sha256 -and (Get-Sha256Hex -Path $recordPath) -cne $Journal.archive_record_sha256) {
        New-Failure "Interrupted-calibration archive record hash mismatch"
    }
    $record = Read-JsonFile -Path $recordPath
    if ($record.schema_version -cne "1" -or $record.record_type -cne "packet2n-r5-interrupted-calibration" -or
        $record.reason -cne $InterruptedRecoveryReason -or $record.session_id -cne $Journal.session_id -or
        $record.session_start_utc -cne $Journal.session_start_utc -or $record.archive_path -cne $Journal.archive_path -or
        $record.state_binding_sha256 -cne $Journal.state_binding_sha256 -or $record.rejected -ne $true -or
        $record.mapping_eligible -ne $false -or $record.source_evidence_present -ne $false -or
        $record.traceback_text_present -ne $false -or
        -not (Test-ExactValue -Actual $record.ports -Expected $Journal.ports) -or
        -not (Test-ExactValue -Actual $record.command -Expected $Journal.command) -or
        -not (Test-ExactValue -Actual $record.native_stage_truth -Expected $Journal.native_stage_truth) -or
        -not (Test-ExactValue -Actual $record.source_provenance -Expected $Journal.source_provenance) -or
        -not (Test-ExactValue -Actual $record.recovery_provenance -Expected $Journal.recovery_provenance)) {
        New-Failure "Interrupted-calibration archive record semantics are invalid"
    }
    $expectedArtifactKeys = @("left_calibration", "right_calibration", "transcript", "state", "manifest", "original_left", "original_right", "interrupted_evidence")
    Assert-ExactKeySet -Value $record.artifacts -ExpectedKeys $expectedArtifactKeys -Message "Interrupted-calibration archive artifact schema is invalid"
    foreach ($name in $expectedArtifactKeys) {
        $artifact = $record.artifacts[$name]
        $archivePath = [string]$artifact.archive_path
        $expectedActual = if ($Root -ceq $Journal.archive_staging_path) {
            Join-Path $Root ([System.IO.Path]::GetRelativePath([string]$Journal.archive_path, $archivePath))
        }
        else { $archivePath }
        Assert-RestartPathConfined -Path $expectedActual -Root $Root -Label "interrupted archived $name"
        if (-not (Test-Path -LiteralPath $expectedActual -PathType Leaf) -or
            (Get-Sha256Hex -Path $expectedActual) -cne $artifact.sha256 -or
            [int64](Get-Item -LiteralPath $expectedActual).Length -ne [int64]$artifact.size -or
            (Get-FileTimestampUtc -Path $expectedActual) -cne $artifact.archive_mtime_utc) {
            New-Failure "Interrupted-calibration archived $name identity is invalid"
        }
    }
    $sourceExpectations = [ordered]@{
        left_calibration = [ordered]@{
            source_path = $Journal.source_active.left.path
            archive_path = Join-Path $Journal.archive_path (Join-Path "interrupted-active-calibration" ([System.IO.Path]::GetFileName([string]$Journal.source_active.left.path)))
            sha256 = $Journal.source_active.left.sha256
            size = [int64]$Journal.source_active.left.size
            source_mtime_utc = $Journal.source_active.left.mtime_utc
            archive_mtime_utc = $Journal.source_active.left.mtime_utc
        }
        right_calibration = [ordered]@{
            source_path = $Journal.source_active.right.path
            archive_path = Join-Path $Journal.archive_path (Join-Path "interrupted-active-calibration" ([System.IO.Path]::GetFileName([string]$Journal.source_active.right.path)))
            sha256 = $Journal.source_active.right.sha256
            size = [int64]$Journal.source_active.right.size
            source_mtime_utc = $Journal.source_active.right.mtime_utc
            archive_mtime_utc = $Journal.source_active.right.mtime_utc
        }
        state = [ordered]@{
            source_path = $Journal.state_path
            archive_path = Join-Path $Journal.archive_path (Join-Path "state-snapshot" ([System.IO.Path]::GetFileName([string]$Journal.state_path)))
            sha256 = $Journal.source_state.sha256
            size = [int64]$Journal.source_state.size
            source_mtime_utc = $Journal.source_state.mtime_utc
            archive_mtime_utc = $Journal.source_state.mtime_utc
        }
        transcript = [ordered]@{
            source_path = $Journal.source_transcript.path
            archive_path = Join-Path $Journal.archive_path (Join-Path "failed-transcript" ([System.IO.Path]::GetFileName([string]$Journal.source_transcript.path)))
            sha256 = $Journal.source_transcript.sha256
            size = [int64]$Journal.source_transcript.size
            source_mtime_utc = $Journal.source_transcript.mtime_utc
            archive_mtime_utc = $Journal.source_transcript.mtime_utc
        }
    }
    foreach ($name in $sourceExpectations.Keys) {
        if (-not (Test-ExactValue -Actual $record.artifacts[$name] -Expected $sourceExpectations[$name])) {
            New-Failure "Interrupted-calibration archived $name does not match pinned authority"
        }
    }
    $immutableExpectations = [ordered]@{
        manifest = [ordered]@{
            source_path = $Plan.manifest.path
            archive_path = Join-Path $Journal.archive_path (Join-Path "immutable-backup" ([System.IO.Path]::GetFileName([string]$Plan.manifest.path)))
            sha256 = $Plan.manifest.sha256
            size = [int64](Get-Item -LiteralPath $Plan.manifest.path).Length
            source_mtime_utc = Get-FileTimestampUtc -Path $Plan.manifest.path
            archive_mtime_utc = Get-FileTimestampUtc -Path $Plan.manifest.path
        }
    }
    foreach ($side in @("left", "right")) {
        $backupPath = [string]$Plan.calibration[$side].backup_path
        $backupMtime = Get-FileTimestampUtc -Path $backupPath
        $immutableExpectations["original_$side"] = [ordered]@{
            source_path = $backupPath
            archive_path = Join-Path $Journal.archive_path (Join-Path "immutable-backup" ([System.IO.Path]::GetFileName($backupPath)))
            sha256 = $Plan.calibration[$side].backup_sha256
            size = [int64]$Plan.calibration[$side].backup_size
            source_mtime_utc = $backupMtime
            archive_mtime_utc = $backupMtime
        }
    }
    foreach ($name in $immutableExpectations.Keys) {
        if (-not (Test-ExactValue -Actual $record.artifacts[$name] -Expected $immutableExpectations[$name])) {
            New-Failure "Interrupted-calibration archived $name does not match pinned immutable authority"
        }
    }
    $derivedPath = if ($Root -ceq $Journal.archive_staging_path) { Join-Path $Root "interrupted-evidence.json" } else { Join-Path $Journal.archive_path "interrupted-evidence.json" }
    $derived = Read-JsonFile -Path $derivedPath
    $expectedDerived = Get-InterruptedDerivedEvidence -Journal $Journal
    if (-not (Test-ExactValue -Actual $derived -Expected $expectedDerived)) {
        New-Failure "Interrupted-calibration derived evidence is invalid"
    }
    $expectedFiles = @(
        "archive-record.json", "interrupted-evidence.json",
        [System.IO.Path]::GetRelativePath([string]$Journal.archive_path, [string]$record.artifacts.left_calibration.archive_path),
        [System.IO.Path]::GetRelativePath([string]$Journal.archive_path, [string]$record.artifacts.right_calibration.archive_path),
        [System.IO.Path]::GetRelativePath([string]$Journal.archive_path, [string]$record.artifacts.transcript.archive_path),
        [System.IO.Path]::GetRelativePath([string]$Journal.archive_path, [string]$record.artifacts.state.archive_path),
        [System.IO.Path]::GetRelativePath([string]$Journal.archive_path, [string]$record.artifacts.manifest.archive_path),
        [System.IO.Path]::GetRelativePath([string]$Journal.archive_path, [string]$record.artifacts.original_left.archive_path),
        [System.IO.Path]::GetRelativePath([string]$Journal.archive_path, [string]$record.artifacts.original_right.archive_path)
    )
    $stateName = [System.IO.Path]::GetFileName([string]$Journal.state_path)
    $leftName = [System.IO.Path]::GetFileName([string]$Plan.calibration.left.path)
    $rightName = [System.IO.Path]::GetFileName([string]$Plan.calibration.right.path)
    $retiredLeftRelative = Join-Path "retired-active-calibration" $leftName
    $retiredRightRelative = Join-Path "retired-active-calibration" $rightName
    $retiredStateRelative = Join-Path "retired-state" $stateName
    $retiredStateDirectory = Join-Path $Root "retired-state"
    $retiredPairPresent = (Test-Path -LiteralPath (Join-Path $Root $retiredLeftRelative) -PathType Leaf) -or
        (Test-Path -LiteralPath (Join-Path $Root $retiredRightRelative) -PathType Leaf)
    $retiredStatePresent = Test-Path -LiteralPath (Join-Path $Root $retiredStateRelative) -PathType Leaf
    $retiredStateDirectoryItem = if (Test-Path -LiteralPath $retiredStateDirectory -PathType Container) {
        Get-Item -LiteralPath $retiredStateDirectory -Force
    } else { $null }
    if ($null -ne $retiredStateDirectoryItem -and
        ($retiredStateDirectoryItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        New-Failure "Interrupted-calibration retired-state directory is a reparse point"
    }
    $emptyRetiredStateDirectory = (Test-Path -LiteralPath $Journal.state_path -PathType Leaf) -and
        $null -ne $retiredStateDirectoryItem -and
        @(Get-ChildItem -LiteralPath $retiredStateDirectory -Force).Count -eq 0
    $receiptPresent = Test-Path -LiteralPath (Join-Path $Root "recovery-receipt.json") -PathType Leaf
    $receiptTempRelative = "recovery-receipt.json.restart-durable.tmp"
    $receiptTempPresent = Test-Path -LiteralPath (Join-Path $Root $receiptTempRelative) -PathType Leaf
    if ($retiredPairPresent) { $expectedFiles += @($retiredLeftRelative, $retiredRightRelative) }
    if ($retiredStatePresent) { $expectedFiles += $retiredStateRelative }
    if ($receiptPresent) { $expectedFiles += "recovery-receipt.json" }
    if ($receiptTempPresent) { $expectedFiles += $receiptTempRelative }
    if ($Completed -and (-not $retiredPairPresent -or -not $retiredStatePresent -or -not $receiptPresent -or $receiptTempPresent)) {
        New-Failure "Completed interrupted-calibration archive is incomplete"
    }
    $expectedDirectories = @("interrupted-active-calibration", "failed-transcript", "state-snapshot", "immutable-backup")
    if ($retiredPairPresent) { $expectedDirectories += "retired-active-calibration" }
    if ($retiredStatePresent -or $emptyRetiredStateDirectory) { $expectedDirectories += "retired-state" }
    $actualDirectories = @(
        Get-ChildItem -LiteralPath $Root -Recurse -Force | Where-Object { $_ -is [System.IO.DirectoryInfo] } |
            ForEach-Object { [System.IO.Path]::GetRelativePath($Root, $_.FullName) }
    )
    if ($actualDirectories.Count -ne $expectedDirectories.Count) { New-Failure "Interrupted-calibration archive directory layout is invalid" }
    foreach ($relative in $actualDirectories) {
        if ($expectedDirectories -cnotcontains $relative) { New-Failure "Interrupted-calibration archive contains an unexpected directory: $relative" }
    }
    $actualFiles = @(
        Get-ChildItem -LiteralPath $Root -Recurse -Force | Where-Object { $_ -is [System.IO.FileInfo] } |
            ForEach-Object { [System.IO.Path]::GetRelativePath($Root, $_.FullName) }
    )
    if ($actualFiles.Count -ne $expectedFiles.Count) { New-Failure "Interrupted-calibration archive layout is invalid" }
    foreach ($relative in $actualFiles) {
        if ($expectedFiles -cnotcontains $relative) { New-Failure "Interrupted-calibration archive contains an unexpected file: $relative" }
    }
    return $record
}

function New-InterruptedStagedOriginalPair {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Journal,
        [Parameter(Mandatory = $true)][hashtable]$Plan
    )

    if (-not (Test-Path -LiteralPath $Journal.staged_original_path)) {
        [void][System.IO.Directory]::CreateDirectory($Journal.staged_original_path)
    }
    $copyIndex = 0
    foreach ($side in @("left", "right")) {
        $name = [System.IO.Path]::GetFileName([string]$Plan.calibration[$side].path)
        $destination = Join-Path $Journal.staged_original_path $name
        Copy-RestartStagedFile -Source $Plan.calibration[$side].backup_path -Destination $destination -Plan $Plan
        $expectedTime = [datetime]::Parse($Plan.calibration[$side].source_mtime_utc, $null, [System.Globalization.DateTimeStyles]::RoundtripKind)
        [System.IO.File]::SetLastWriteTimeUtc($destination, $expectedTime.ToUniversalTime())
        $copyIndex++
        if ($copyIndex -eq 1) { Test-RestartFailurePoint -Plan $Plan -Point "after_first_original_copy" }
    }
    if ((Get-InterruptedPairLayout -Directory $Journal.staged_original_path -Plan $Plan -SourceActive $Journal.source_active) -cne "original") {
        New-Failure "Interrupted-calibration staged original pair is invalid"
    }
}

function Get-InterruptedStagedLayout {
    param(
        [Parameter(Mandatory = $true)][string]$Directory,
        [Parameter(Mandatory = $true)][hashtable]$Plan,
        [Parameter(Mandatory = $true)][hashtable]$SourceActive
    )

    if (-not (Test-Path -LiteralPath $Directory)) { return "missing" }
    $layout = Get-InterruptedPairLayout -Directory $Directory -Plan $Plan -SourceActive $SourceActive
    if ($layout -ceq "original") { return "original" }
    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) { return "unrecognized" }
    $allowed = @()
    foreach ($side in @("left", "right")) {
        $name = [System.IO.Path]::GetFileName([string]$Plan.calibration[$side].path)
        $allowed += @($name, "$name.restart-copy.tmp")
    }
    foreach ($entry in @(Get-ChildItem -LiteralPath $Directory -Force)) {
        if ($entry -isnot [System.IO.FileInfo] -or $allowed -cnotcontains $entry.Name -or
            ($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { return "unrecognized" }
    }
    return "partial_original"
}

function Get-InterruptedReceiptPayload {
    param([Parameter(Mandatory = $true)][hashtable]$Journal)

    return [ordered]@{
        schema_version = "1"; receipt_type = "packet2n-r5-interrupted-calibration-recovery"
        status = "completed"; reason = $InterruptedRecoveryReason; session_id = $Journal.session_id
        completed_utc = [DateTime]::UtcNow.ToString("o"); archive_path = $Journal.archive_path
        archive_record_sha256 = $Journal.archive_record_sha256; verified = $true; offline = $true
        active_classification = "ORIGINAL_CALIBRATION_INTACT"; next_stage = "Calibrate"; mapping_eligible = $false
        source_provenance = $Journal.source_provenance; recovery_provenance = $Journal.recovery_provenance
    }
}

function Assert-CompletedInterruptedArchive {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Journal,
        [Parameter(Mandatory = $true)][hashtable]$Plan
    )

    $record = Assert-InterruptedArchiveCore -Root $Journal.archive_path -Journal $Journal -Plan $Plan -Completed
    $archivedPairPath = Join-Path $Journal.archive_path "interrupted-active-calibration"
    $retiredPairPath = Join-Path $Journal.archive_path "retired-active-calibration"
    if ((Get-InterruptedPairLayout -Directory $archivedPairPath -Plan $Plan -SourceActive $Journal.source_active) -cne "interrupted" -or
        (Get-InterruptedPairLayout -Directory $retiredPairPath -Plan $Plan -SourceActive $Journal.source_active) -cne "interrupted") {
        New-Failure "Completed interrupted-calibration active identities do not match pinned authority"
    }
    $stateName = [System.IO.Path]::GetFileName([string]$Journal.state_path)
    foreach ($stateArtifact in @(
        (Join-Path $Journal.archive_path (Join-Path "state-snapshot" $stateName)),
        (Join-Path $Journal.archive_path (Join-Path "retired-state" $stateName))
    )) {
        if (-not (Test-Path -LiteralPath $stateArtifact -PathType Leaf) -or
            (Get-Sha256Hex -Path $stateArtifact) -cne $Journal.source_state.sha256 -or
            [int64](Get-Item -LiteralPath $stateArtifact).Length -ne [int64]$Journal.source_state.size -or
            (Get-FileTimestampUtc -Path $stateArtifact) -cne $Journal.source_state.mtime_utc) {
            New-Failure "Completed interrupted-calibration state identity does not match pinned authority"
        }
    }
    $transcriptPath = Join-Path $Journal.archive_path (Join-Path "failed-transcript" ([System.IO.Path]::GetFileName([string]$Journal.source_transcript.path)))
    if (-not (Test-Path -LiteralPath $transcriptPath -PathType Leaf) -or
        (Get-Sha256Hex -Path $transcriptPath) -cne $Journal.source_transcript.sha256 -or
        [int64](Get-Item -LiteralPath $transcriptPath).Length -ne [int64]$Journal.source_transcript.size -or
        (Get-FileTimestampUtc -Path $transcriptPath) -cne $Journal.source_transcript.mtime_utc) {
        New-Failure "Completed interrupted-calibration transcript identity does not match pinned authority"
    }
    $receiptPath = Join-Path $Journal.archive_path "recovery-receipt.json"
    $receipt = Read-JsonFile -Path $receiptPath
    if ($receipt.schema_version -cne "1" -or $receipt.receipt_type -cne "packet2n-r5-interrupted-calibration-recovery" -or
        $receipt.status -cne "completed" -or $receipt.reason -cne $InterruptedRecoveryReason -or
        $receipt.session_id -cne $Journal.session_id -or $receipt.archive_path -cne $Journal.archive_path -or
        $receipt.archive_record_sha256 -cne $Journal.archive_record_sha256 -or $receipt.verified -ne $true -or
        $receipt.offline -ne $true -or $receipt.active_classification -cne "ORIGINAL_CALIBRATION_INTACT" -or
        $receipt.next_stage -cne "Calibrate" -or $receipt.mapping_eligible -ne $false -or
        -not (Test-ExactValue -Actual $receipt.source_provenance -Expected $Journal.source_provenance) -or
        -not (Test-ExactValue -Actual $receipt.recovery_provenance -Expected $Journal.recovery_provenance)) {
        New-Failure "Interrupted-calibration recovery receipt is invalid"
    }
    return $record
}

function Assert-InterruptedRecoveryLayout {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Journal,
        [Parameter(Mandatory = $true)][hashtable]$Plan
    )

    $archiveExists = Test-Path -LiteralPath $Journal.archive_path -PathType Container
    $stagingExists = Test-Path -LiteralPath $Journal.archive_staging_path -PathType Container
    if ($archiveExists -and $stagingExists) { New-Failure "Interrupted-calibration archive layout is ambiguous" }
    $active = Get-InterruptedPairLayout -Directory $Journal.active_directory -Plan $Plan -SourceActive $Journal.source_active
    $staged = Get-InterruptedStagedLayout -Directory $Journal.staged_original_path -Plan $Plan -SourceActive $Journal.source_active
    $rollback = Get-InterruptedPairLayout -Directory $Journal.rollback_path -Plan $Plan -SourceActive $Journal.source_active
    $retiredPath = Join-Path $Journal.archive_path "retired-active-calibration"
    $retired = Get-InterruptedPairLayout -Directory $retiredPath -Plan $Plan -SourceActive $Journal.source_active
    $stateExists = Test-Path -LiteralPath $Journal.state_path -PathType Leaf
    $retiredStatePath = Join-Path $Journal.archive_path (Join-Path "retired-state" ([System.IO.Path]::GetFileName([string]$Journal.state_path)))
    $retiredStateExists = Test-Path -LiteralPath $retiredStatePath -PathType Leaf
    $receiptExists = Test-Path -LiteralPath (Join-Path $Journal.archive_path "recovery-receipt.json") -PathType Leaf
    $receiptTempExists = Test-Path -LiteralPath (Join-Path $Journal.archive_path "recovery-receipt.json.restart-durable.tmp") -PathType Leaf
    if ($receiptExists -and $receiptTempExists) { New-Failure "Interrupted-calibration receipt layout is ambiguous" }

    if (-not $archiveExists) {
        if ($active -cne "interrupted" -or $staged -cne "missing" -or $rollback -cne "missing" -or
            $retired -cne "missing" -or -not $stateExists -or $retiredStateExists -or $receiptExists -or $receiptTempExists) {
            New-Failure "unrecognized interrupted-calibration recovery layout"
        }
        return "initialized"
    }
    [void](Assert-InterruptedArchiveCore -Root $Journal.archive_path -Journal $Journal -Plan $Plan)
    if ($active -ceq "interrupted" -and @("missing", "partial_original", "original") -ccontains $staged -and
        $rollback -ceq "missing" -and $retired -ceq "missing" -and $stateExists -and -not $retiredStateExists -and
        -not $receiptExists -and -not $receiptTempExists) {
        return "archive_published"
    }
    if ($active -ceq "missing" -and $staged -ceq "original" -and $rollback -ceq "interrupted" -and
        $retired -ceq "missing" -and $stateExists -and -not $retiredStateExists -and -not $receiptExists -and
        -not $receiptTempExists) {
        return "active_withdrawn"
    }
    if ($active -ceq "original" -and $staged -ceq "missing" -and $rollback -ceq "interrupted" -and
        $retired -ceq "missing" -and $stateExists -and -not $retiredStateExists -and -not $receiptExists -and
        -not $receiptTempExists) {
        return "original_activated"
    }
    if ($active -ceq "original" -and $staged -ceq "missing" -and $rollback -ceq "missing" -and
        $retired -ceq "interrupted" -and $stateExists -and -not $retiredStateExists -and -not $receiptExists -and
        -not $receiptTempExists) {
        return "rejected_active_retired"
    }
    if ($active -ceq "original" -and $staged -ceq "missing" -and $rollback -ceq "missing" -and
        $retired -ceq "interrupted" -and -not $stateExists -and $retiredStateExists) {
        if ($receiptExists) { return "receipt_published" }
        return "state_retired"
    }
    New-Failure "unrecognized interrupted-calibration recovery layout"
}

function Get-IncompleteInterruptedRecoveryStatus {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Plan,
        [Parameter(Mandatory = $true)][string]$StatePathValue
    )

    $journalPath = Get-InterruptedRecoveryJournalPath -StatePathValue $StatePathValue
    if (-not (Test-Path -LiteralPath $journalPath)) { return $null }
    try {
        $journal = Read-JsonFile -Path $journalPath
        [void](Assert-InterruptedRecoveryJournal -Journal $journal -Plan $Plan -StatePathValue $StatePathValue)
        $phase = Assert-InterruptedRecoveryLayout -Journal $journal -Plan $Plan
        return [ordered]@{
            classification = "INTERRUPTED_CALIBRATION_RECOVERABLE"
            next_stage = "RecoverInterruptedCalibration"
            report = "incomplete interrupted-calibration recovery transaction; rerun the exact confirmed command"
            interrupted_transaction = [ordered]@{
                journal_path = $journalPath; session_id = $journal.session_id; phase = $phase
                reason = $journal.reason; archive_path = $journal.archive_path
            }
        }
    }
    catch {
        return [ordered]@{
            classification = "INVALID_OR_UNCERTAIN_STATE"; next_stage = $null
            report = "Interrupted-calibration recovery journal is invalid: $($_.Exception.Message)"
        }
    }
}

function Assert-NoIncompleteInterruptedRecoveryTransaction {
    param([Parameter(Mandatory = $true)][string]$StatePathValue)

    if (Test-Path -LiteralPath (Get-InterruptedRecoveryJournalPath -StatePathValue $StatePathValue)) {
        New-Failure "Stage $Stage is blocked by an incomplete interrupted-calibration recovery transaction"
    }
}

function Add-InterruptedArchivesToStatus {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Payload,
        [Parameter(Mandatory = $true)][hashtable]$Plan,
        [Parameter(Mandatory = $true)][string]$StatePathValue
    )

    $records = [System.Collections.Generic.List[object]]::new()
    if (Test-Path -LiteralPath ([string]$Plan.rejected_archive_root) -PathType Container) {
        foreach ($archive in @(Get-ChildItem -LiteralPath ([string]$Plan.rejected_archive_root) -Directory -Filter "packet2n-r5-interrupted-*" -Force)) {
            try {
                $recordPath = Join-Path $archive.FullName "archive-record.json"
                $record = Read-JsonFile -Path $recordPath
                $paths = Get-InterruptedRecoveryPaths -Plan $Plan -StatePathValue $StatePathValue -SessionId ([string]$record.session_id)
                if ($archive.FullName -cne $paths.archive) { New-Failure "Interrupted archive path is invalid" }
                $retiredStatePath = Join-Path $archive.FullName (Join-Path "retired-state" ([System.IO.Path]::GetFileName($StatePathValue)))
                $retiredState = Read-JsonFile -Path $retiredStatePath
                $journal = [ordered]@{
                    schema_version = "1"; transaction_type = "packet2n-r5-interrupted-calibration"; status = "in_progress"; phase = "state_retired"
                    reason = $record.reason; session_id = $record.session_id; session_start_utc = $record.session_start_utc
                    state_binding_sha256 = $record.state_binding_sha256; state_path = $StatePathValue; archive_path = $paths.archive
                    archive_staging_path = $paths.archive_staging; active_directory = $paths.active; staged_original_path = $paths.staged_original
                    rollback_path = $paths.rollback
                    source_state = [ordered]@{ sha256 = Get-Sha256Hex -Path $retiredStatePath; size = [int64](Get-Item -LiteralPath $retiredStatePath).Length; mtime_utc = Get-FileTimestampUtc -Path $retiredStatePath }
                    source_active = [ordered]@{ left = $retiredState.pre_calibration.left; right = $retiredState.pre_calibration.right }
                    source_transcript = [ordered]@{
                        path = $record.artifacts.transcript.source_path; sha256 = $record.artifacts.transcript.sha256
                        size = $record.artifacts.transcript.size; mtime_utc = $record.artifacts.transcript.source_mtime_utc
                    }
                    source_evidence_present = $false; traceback_text_present = $false; native_stage_truth = $record.native_stage_truth
                    ports = $record.ports; command = $record.command; source_provenance = $record.source_provenance
                    recovery_provenance = $record.recovery_provenance; archive_record_sha256 = Get-Sha256Hex -Path $recordPath
                }
                $journal.source_active = [ordered]@{
                    left = [ordered]@{
                        path = $Plan.calibration.left.path; sha256 = $record.artifacts.left_calibration.sha256; size = $record.artifacts.left_calibration.size
                        mtime_utc = $record.artifacts.left_calibration.source_mtime_utc
                        calibration = (Read-JsonFile -Path $record.artifacts.left_calibration.archive_path)
                    }
                    right = [ordered]@{
                        path = $Plan.calibration.right.path; sha256 = $record.artifacts.right_calibration.sha256; size = $record.artifacts.right_calibration.size
                        mtime_utc = $record.artifacts.right_calibration.source_mtime_utc
                        calibration = (Read-JsonFile -Path $record.artifacts.right_calibration.archive_path)
                    }
                }
                [void](Assert-InterruptedRecoveryJournal -Journal $journal -Plan $Plan -StatePathValue $StatePathValue)
                [void](Assert-CompletedInterruptedArchive -Journal $journal -Plan $Plan)
                $records.Add([ordered]@{
                    archive_path = $archive.FullName; reason = $record.reason; session_id = $record.session_id
                    verified = $true; mapping_eligible = $false
                })
            }
            catch {
                New-Failure "Interrupted archive validation failed: $($_.Exception.Message)"
            }
        }
    }
    if ($records.Count -gt 0) { $Payload.interrupted_archives = @($records.ToArray()) }
    return $Payload
}

function Invoke-RecoverInterruptedCalibrationStage {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Plan,
        [Parameter(Mandatory = $true)][string]$StatePathValue
    )

    $journalPath = Get-InterruptedRecoveryJournalPath -StatePathValue $StatePathValue
    if (Test-Path -LiteralPath (Get-RestartJournalPath -StatePathValue $StatePathValue)) {
        New-Failure "RecoverInterruptedCalibration is blocked by an incomplete RestartCalibration transaction"
    }
    if (Test-Path -LiteralPath $journalPath) {
        $journal = Read-JsonFile -Path $journalPath
        $paths = Assert-InterruptedRecoveryJournal -Journal $journal -Plan $Plan -StatePathValue $StatePathValue
        $physicalPhase = Assert-InterruptedRecoveryLayout -Journal $journal -Plan $Plan
        if ($physicalPhase -cne "receipt_published" -and $journal.phase -cne $physicalPhase) {
            $journal.phase = $physicalPhase
            Save-InterruptedRecoveryJournal -Path $journalPath -Journal $journal -Plan $Plan
        }
    }
    else {
        $state = Load-State -Path $StatePathValue
        $authority = Assert-InterruptedCalibrationCandidate -State $state -Plan $Plan -StatePathValue $StatePathValue
        $paths = Get-InterruptedRecoveryPaths -Plan $Plan -StatePathValue $StatePathValue -SessionId ([string]$state.session_id)
        foreach ($path in @($paths.archive, $paths.archive_staging, $paths.staged_original, $paths.rollback)) {
            Assert-PathMissing -Path $path
        }
        if ((Get-InterruptedPairLayout -Directory $paths.active -Plan $Plan -SourceActive $authority.active) -cne "interrupted") {
            New-Failure "Interrupted-calibration active directory must contain exactly the validated mixed pair"
        }
        $journal = New-InterruptedRecoveryJournal -State $state -Plan $Plan -StatePathValue $StatePathValue -Paths $paths -Authority $authority
        Write-RestartJsonDurable -Path $journalPath -Value $journal -Plan $Plan -AfterFlushFailurePoint "after_initial_journal_temp_flush"
    }

    Assert-RepoAndEnvGuards -Plan $Plan
    Assert-ImmutableManifestAndBackups -Plan $Plan
    if (-not (Test-Path -LiteralPath $journal.archive_path)) {
        Assert-SourceStateIdentity -StatePathValue $StatePathValue -Expected $journal.source_state
        if ((Get-InterruptedPairLayout -Directory $journal.active_directory -Plan $Plan -SourceActive $journal.source_active) -cne "interrupted") {
            New-Failure "Interrupted-calibration active pair changed before archive publication"
        }
        $journal.archive_record_sha256 = New-InterruptedArchiveStaging -Journal $journal -Plan $Plan
        $journal.phase = "archive_staged"
        Save-InterruptedRecoveryJournal -Path $journalPath -Journal $journal -Plan $Plan
        [void](Assert-InterruptedArchiveCore -Root $journal.archive_staging_path -Journal $journal -Plan $Plan)
        Test-RestartFailurePoint -Plan $Plan -Point "before_archive_publish"
        Assert-RestartMoveSafe -Source $journal.archive_staging_path -Destination $journal.archive_path `
            -SourceRoot ([string]$Plan.rejected_archive_root) -DestinationRoot ([string]$Plan.rejected_archive_root) `
            -Label "interrupted archive publication"
        Invoke-RestartDurableNamespaceMove -Source $journal.archive_staging_path -Destination $journal.archive_path -Label "interrupted archive publication"
        Test-RestartFailurePoint -Plan $Plan -Point "after_archive_namespace_publish"
        $journal.phase = "archive_published"
        Save-InterruptedRecoveryJournal -Path $journalPath -Journal $journal -Plan $Plan
    }
    [void](Assert-InterruptedArchiveCore -Root $journal.archive_path -Journal $journal -Plan $Plan)

    $active = Get-InterruptedPairLayout -Directory $journal.active_directory -Plan $Plan -SourceActive $journal.source_active
    $staged = Get-InterruptedStagedLayout -Directory $journal.staged_original_path -Plan $Plan -SourceActive $journal.source_active
    $rollback = Get-InterruptedPairLayout -Directory $journal.rollback_path -Plan $Plan -SourceActive $journal.source_active
    $retiredPath = Join-Path $journal.archive_path "retired-active-calibration"
    $retired = Get-InterruptedPairLayout -Directory $retiredPath -Plan $Plan -SourceActive $journal.source_active
    if ($active -ceq "interrupted" -and @("missing", "partial_original") -ccontains $staged -and $rollback -ceq "missing" -and $retired -ceq "missing") {
        New-InterruptedStagedOriginalPair -Journal $journal -Plan $Plan
        $staged = "original"
    }
    if ($active -ceq "interrupted" -and $staged -ceq "original" -and $rollback -ceq "missing" -and $retired -ceq "missing") {
        $parent = [System.IO.Path]::GetDirectoryName([string]$journal.active_directory)
        Assert-RestartMoveSafe -Source $journal.active_directory -Destination $journal.rollback_path -SourceRoot $parent -DestinationRoot $parent -Label "interrupted active withdrawal"
        Invoke-RestartDurableNamespaceMove -Source $journal.active_directory -Destination $journal.rollback_path -Label "interrupted active withdrawal"
        Test-RestartFailurePoint -Plan $Plan -Point "after_active_directory_move"
        $journal.phase = "active_withdrawn"
        Save-InterruptedRecoveryJournal -Path $journalPath -Journal $journal -Plan $Plan
        $active = "missing"; $rollback = "interrupted"
    }
    if ($active -ceq "missing" -and $staged -ceq "original" -and $rollback -ceq "interrupted" -and $retired -ceq "missing") {
        $parent = [System.IO.Path]::GetDirectoryName([string]$journal.active_directory)
        Assert-RestartMoveSafe -Source $journal.staged_original_path -Destination $journal.active_directory -SourceRoot $parent -DestinationRoot $parent -Label "interrupted original activation"
        Invoke-RestartDurableNamespaceMove -Source $journal.staged_original_path -Destination $journal.active_directory -Label "interrupted original activation"
        Test-RestartFailurePoint -Plan $Plan -Point "after_original_directory_move"
        $journal.phase = "original_activated"
        Save-InterruptedRecoveryJournal -Path $journalPath -Journal $journal -Plan $Plan
        $active = "original"; $staged = "missing"
    }
    if ($active -cne "original" -or $staged -cne "missing") { New-Failure "unrecognized interrupted-calibration recovery layout" }
    Assert-OriginalCalibrationIdentities -Plan $Plan
    if ($rollback -ceq "interrupted" -and $retired -ceq "missing") {
        $parent = [System.IO.Path]::GetDirectoryName([string]$journal.active_directory)
        Assert-RestartMoveSafe -Source $journal.rollback_path -Destination $retiredPath -SourceRoot $parent -DestinationRoot ([string]$Plan.rejected_archive_root) -Label "interrupted active retirement"
        Invoke-RestartDurableNamespaceMove -Source $journal.rollback_path -Destination $retiredPath -Label "interrupted active retirement"
        Test-RestartFailurePoint -Plan $Plan -Point "after_fresh_pair_namespace_publish"
        $rollback = "missing"; $retired = "interrupted"
    }
    if ($rollback -cne "missing" -or $retired -cne "interrupted") { New-Failure "unrecognized interrupted-calibration retired-active layout" }
    $journal.phase = "rejected_active_retired"
    Save-InterruptedRecoveryJournal -Path $journalPath -Journal $journal -Plan $Plan

    $retiredStateDirectory = Join-Path $journal.archive_path "retired-state"
    $retiredStatePath = Join-Path $retiredStateDirectory ([System.IO.Path]::GetFileName($StatePathValue))
    if (Test-Path -LiteralPath $StatePathValue) {
        Assert-SourceStateIdentity -StatePathValue $StatePathValue -Expected $journal.source_state
        Assert-PathMissing -Path $retiredStatePath
        [void][System.IO.Directory]::CreateDirectory($retiredStateDirectory)
        Test-RestartFailurePoint -Plan $Plan -Point "after_retired_state_directory_create"
        Assert-RestartMoveSafe -Source $StatePathValue -Destination $retiredStatePath -SourceRoot ([string]$Plan.state_root) -DestinationRoot ([string]$Plan.rejected_archive_root) -Label "interrupted state retirement"
        Invoke-RestartDurableNamespaceMove -Source $StatePathValue -Destination $retiredStatePath -Label "interrupted state retirement"
        Test-RestartFailurePoint -Plan $Plan -Point "after_state_namespace_publish"
    }
    if (-not (Test-Path -LiteralPath $retiredStatePath -PathType Leaf) -or
        (Get-Sha256Hex -Path $retiredStatePath) -cne $journal.source_state.sha256 -or
        [int64](Get-Item -LiteralPath $retiredStatePath).Length -ne [int64]$journal.source_state.size) {
        New-Failure "Interrupted-calibration retired state identity mismatch"
    }
    $journal.phase = "state_retired"
    Save-InterruptedRecoveryJournal -Path $journalPath -Journal $journal -Plan $Plan
    $receiptPath = Join-Path $journal.archive_path "recovery-receipt.json"
    if (-not (Test-Path -LiteralPath $receiptPath)) {
        Write-RestartJsonDurable -Path $receiptPath -Value (Get-InterruptedReceiptPayload -Journal $journal) -Plan $Plan -AfterFlushFailurePoint "after_receipt_temp_flush"
    }
    [void](Assert-CompletedInterruptedArchive -Journal $journal -Plan $Plan)
    Test-RestartFailurePoint -Plan $Plan -Point "after_receipt_publish"
    [System.IO.File]::Delete($journalPath)
    [Console]::Out.WriteLine("INTERRUPTED_CALIBRATION_RECOVERY_COMPLETE")
}

function Invoke-CheckLeaderBusesStage {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Plan,
        [Parameter(Mandatory = $true)][string]$StatePathValue
    )

    Assert-NoIncompleteRestartTransaction -StatePathValue $StatePathValue
    Assert-NoIncompleteInterruptedRecoveryTransaction -StatePathValue $StatePathValue
    Assert-RepoAndEnvGuards -Plan $Plan
    Assert-ImmutableManifestAndBackups -Plan $Plan
    $status = Get-StatusPayload -Plan $Plan -StatePathValue $StatePathValue
    if ($status.classification -cne "ORIGINAL_CALIBRATION_INTACT" -or $status.next_stage -cne "Calibrate") {
        New-Failure "CheckLeaderBuses requires offline Status ORIGINAL_CALIBRATION_INTACT / Calibrate"
    }
    $command = Build-StageCommand -StageName "CheckLeaderBuses" -Plan $Plan
    if ([bool]$Plan.is_test_mode) {
        $stagePlan = $Plan.stage_plan.CheckLeaderBuses
        if ($null -eq $stagePlan -or -not [bool]$stagePlan.launched) { New-Failure "CheckLeaderBuses test command did not launch" }
        if (-not (Test-IsJsonInteger -Value $stagePlan.exit_code) -or [int64]$stagePlan.exit_code -ne 0) {
            New-Failure "CheckLeaderBuses test command failed with exit code $($stagePlan.exit_code)"
        }
    }
    else {
        if (-not (Test-Path -LiteralPath $command.executable -PathType Leaf) -or -not (Test-Path -LiteralPath $command.arguments[0] -PathType Leaf)) {
            New-Failure "CheckLeaderBuses reviewed command is missing"
        }
        & $command.executable @($command.arguments)
        $exitCode = $LASTEXITCODE
        if ($null -eq $exitCode -or [int]$exitCode -ne 0) { New-Failure "CheckLeaderBuses failed with exit code $exitCode" }
    }
    [Console]::Out.WriteLine("LEADER_BUS_CHECK_STAGE=PASS")
}

function Get-StatusPayload {
    param(
        [hashtable]$Plan,
        [string]$StatePathValue
    )

    try {
        Assert-TestModeMutablePaths -Plan $Plan -StatePathValue $StatePathValue
        $incompleteInterrupted = Get-IncompleteInterruptedRecoveryStatus -Plan $Plan -StatePathValue $StatePathValue
        if ($null -ne $incompleteInterrupted) {
            return $incompleteInterrupted
        }
        $incompleteRestart = Get-IncompleteRestartStatus -Plan $Plan -StatePathValue $StatePathValue
        if ($null -ne $incompleteRestart) {
            return $incompleteRestart
        }
        Assert-ImmutableManifestAndBackups -Plan $Plan
        $current = Get-CurrentIdentities -Plan $Plan
        $exactOriginals = Test-CurrentIdentitiesAreExactOriginals -Current $current -Plan $Plan
        $originalHashes = Test-CurrentHashesAreOriginals -Current $current -Plan $Plan
        if (-not (Test-Path -LiteralPath $StatePathValue -PathType Leaf)) {
            if ($exactOriginals) {
                $payload = [ordered]@{
                    classification = "ORIGINAL_CALIBRATION_INTACT"
                    next_stage     = "Calibrate"
                }
                $payload = Add-RejectedArchivesToStatus -Payload $payload -Plan $Plan -StatePathValue $StatePathValue
                return Add-InterruptedArchivesToStatus -Payload $payload -Plan $Plan -StatePathValue $StatePathValue
            }
            if ($originalHashes) {
                return [ordered]@{
                    classification = "INVALID_OR_UNCERTAIN_STATE"
                    next_stage     = $null
                    report         = "original content exists but its exact pinned identity is not intact"
                }
            }
            return [ordered]@{
                classification = "ORPHANED_FRESH_CALIBRATION"
                next_stage     = $null
                report         = "dry-run-only recovery plan: preserve orphaned files, then restore immutable originals only under later exact reviewed authorization"
            }
        }
        $state = Read-JsonFile -Path $StatePathValue
        $issues = @(Get-StateValidationIssues -State $state -Plan $Plan)
        if ($issues.Count -gt 0) {
            return [ordered]@{
                classification = "INVALID_OR_UNCERTAIN_STATE"
                next_stage     = $null
                report         = ($issues -join ", ")
            }
        }
        Assert-StateIdentity -State $state
        Assert-StateProvenance -State $state -StatePathValue $StatePathValue -Plan $Plan -AllowRestartCandidate -AllowInterruptedCandidate
        Assert-TestModeMutablePaths -Plan $Plan -StatePathValue $StatePathValue -SessionId ([string]$state.session_id)
        if ($state.completed_stages -cnotcontains "Calibrate") {
            if (-not $exactOriginals -and -not $originalHashes) {
                return [ordered]@{
                    classification = "ORPHANED_FRESH_CALIBRATION"
                    next_stage     = $null
                    report         = "dry-run-only recovery plan: preserve orphaned files, then restore immutable originals only under later exact reviewed authorization"
                }
            }
            return [ordered]@{
                classification = "INVALID_OR_UNCERTAIN_STATE"
                next_stage     = $null
                report         = "persisted calibration session is incomplete; preserve its state and artifacts for review"
            }
        }
        foreach ($entry in @(
            [ordered]@{ stage = "MapLeft"; artifact = "map_left" },
            [ordered]@{ stage = "MapRight"; artifact = "map_right" }
        )) {
            if ($state.completed_stages -cnotcontains $entry.stage -and (Test-Path -LiteralPath $state.artifacts[$entry.artifact].path)) {
                return [ordered]@{
                    classification = "INVALID_OR_UNCERTAIN_STATE"
                    next_stage     = $null
                    report         = "uncompleted reserved map artifact exists for $($entry.stage); preserve it for review"
                }
            }
        }
        Assert-EvidenceSemantics -State $state -Plan $Plan
        Assert-CompletedMapArtifacts -State $state -Plan $Plan
        $nextStage = if ($state.completed_stages -cnotcontains "MapLeft") { "MapLeft" } elseif ($state.completed_stages -cnotcontains "MapRight") { "MapRight" } elseif ($state.completed_stages -cnotcontains "Verify") { "Verify" } else { $null }
        $payload = [ordered]@{
            classification = "VALID_FRESH_CALIBRATION"
            next_stage     = $nextStage
            final_result   = $state.final_result
        }
        $payload = Add-RejectedArchivesToStatus -Payload $payload -Plan $Plan -StatePathValue $StatePathValue
        return Add-InterruptedArchivesToStatus -Payload $payload -Plan $Plan -StatePathValue $StatePathValue
    }
    catch {
        return [ordered]@{
            classification = "INVALID_OR_UNCERTAIN_STATE"
            next_stage     = $null
            report         = $_.Exception.Message
        }
    }
}

function Invoke-DiagnoseImportsStage {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Plan
    )

    $diagnostic = Get-PlanImportSourceDiagnostic -Plan $Plan
    [Console]::Out.WriteLine((ConvertTo-CanonicalJson -Value $diagnostic))
    if (-not [bool]$diagnostic.matches) {
        $details = Get-ImportSourceFailureMessage -Diagnostic $diagnostic
        New-Failure "Import source refusal: $details"
    }
}

$plan = $null
try {
    Require-Confirmation -StageName $Stage -ConfirmValue $Confirm
    $plan = if ($Stage -eq "DiagnoseImports") { Get-ImportDiagnosisPlan } else { Get-ExecutionPlan }
    if ($Stage -eq "Status") {
        $payload = Get-StatusPayload -Plan $plan -StatePathValue $StatePath
        [Console]::Out.WriteLine((ConvertTo-CanonicalJson -Value $payload))
        exit 0
    }
    if (@("Calibrate", "MapLeft", "MapRight", "Verify", "CheckLeaderBuses") -ccontains $Stage) {
        Assert-NoIncompleteRestartTransaction -StatePathValue $StatePath
        Assert-NoIncompleteInterruptedRecoveryTransaction -StatePathValue $StatePath
    }
    switch ($Stage) {
        "DiagnoseImports" { Invoke-DiagnoseImportsStage -Plan $plan }
        "RestartCalibration" { Invoke-RestartCalibrationStage -Plan $plan -StatePathValue $StatePath }
        "RecoverInterruptedCalibration" { Invoke-RecoverInterruptedCalibrationStage -Plan $plan -StatePathValue $StatePath }
        "CheckLeaderBuses" { Invoke-CheckLeaderBusesStage -Plan $plan -StatePathValue $StatePath }
        "Calibrate" { Invoke-CalibrateStage -Plan $plan -StatePathValue $StatePath }
        "MapLeft" { Invoke-MapStage -StageName "MapLeft" -Plan $plan -StatePathValue $StatePath }
        "MapRight" { Invoke-MapStage -StageName "MapRight" -Plan $plan -StatePathValue $StatePath }
        "Verify" { Invoke-VerifyStage -Plan $plan -StatePathValue $StatePath }
        default { New-Failure "Unhandled stage $Stage" }
    }
    exit 0
}
catch {
    $primaryMessage = $_.Exception.Message
    [Console]::Error.WriteLine($primaryMessage)
    if ($Stage -ne "Status" -and $Stage -ne "DiagnoseImports" -and $null -ne $plan) {
        try {
            $recovery = Get-StatusPayload -Plan $plan -StatePathValue $StatePath
            [Console]::Error.WriteLine("RECOVERY_CLASSIFICATION=$($recovery.classification)")
            $recoveryNext = if ($null -eq $recovery.next_stage) { "NONE" } else { [string]$recovery.next_stage }
            [Console]::Error.WriteLine("RECOVERY_NEXT_STAGE=$recoveryNext")
        }
        catch {
        }
        if ($Stage -ceq "Calibrate") {
            [Console]::Error.WriteLine("pwsh -NoLogo -NoProfile -File .\tools\packet2n_r5_leader_mapping.ps1 -Stage RecoverInterruptedCalibration -Confirm RECOVER")
        }
    }
    exit 1
}
