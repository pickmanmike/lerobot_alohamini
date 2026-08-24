[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Status", "DiagnoseImports", "Calibrate", "MapLeft", "MapRight", "Verify")]
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

    $resolvedPath = [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    $resolvedRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    $rootWithSeparator = $resolvedRoot + [System.IO.Path]::DirectorySeparatorChar
    return ($resolvedPath.Equals($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase) -or $resolvedPath.StartsWith($rootWithSeparator, [System.StringComparison]::OrdinalIgnoreCase))
}

function Assert-TestModePathHasNoReparsePoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Boundary
    )

    $currentPath = [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    $boundaryPath = [System.IO.Path]::GetFullPath($Boundary).TrimEnd('\', '/')
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
        $currentPath = $parent.FullName.TrimEnd('\', '/')
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
    if ($calibrationRoot.Equals([System.IO.Path]::GetFullPath($RealCalibrationRoot).TrimEnd('\', '/'), [System.StringComparison]::OrdinalIgnoreCase)) {
        New-Failure "Test-mode sandbox refuses the production calibration root"
    }
    if ($stateRoot.Equals([System.IO.Path]::GetFullPath($RealLogsDirectory).TrimEnd('\', '/'), [System.StringComparison]::OrdinalIgnoreCase)) {
        New-Failure "Test-mode sandbox refuses the production logs root"
    }

    foreach ($protectedPath in @($RepositoryRoot, $RealCalibrationRoot, $RealLogsDirectory)) {
        if ((Test-PathIsSameOrDescendant -Path $testRoot -Root $protectedPath) -or (Test-PathIsSameOrDescendant -Path $protectedPath -Root $testRoot)) {
            New-Failure "Test-mode sandbox overlaps a protected production or repository path"
        }
    }
    foreach ($entry in @(
        [ordered]@{ name = "calibration root"; path = $calibrationRoot },
        [ordered]@{ name = "state root"; path = $stateRoot }
    )) {
        if ($entry.path.Equals($testRoot, [System.StringComparison]::OrdinalIgnoreCase) -or -not (Test-PathIsSameOrDescendant -Path $entry.path -Root $testRoot)) {
            New-Failure "Test-mode $($entry.name) escaped the test-mode sandbox"
        }
        Assert-TestModePathHasNoReparsePoint -Path $entry.path -Boundary $testRoot
    }
    if ((Test-PathIsSameOrDescendant -Path $calibrationRoot -Root $stateRoot) -or (Test-PathIsSameOrDescendant -Path $stateRoot -Root $calibrationRoot)) {
        New-Failure "Test-mode calibration and state roots must be separate subtrees"
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

function Assert-StateProvenance {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State,

        [Parameter(Mandatory = $true)]
        [string]$StatePathValue,

        [hashtable]$Plan
    )

    Assert-ReservedArtifactPaths -State $State -Plan $Plan
    if ($State.repo_head -cne $Plan.head -or $State.state_path -cne $StatePathValue -or $State.runner_sha -cne (Get-RunnerSha256)) {
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
        [hashtable]$Command
    )

    $transcript = $State.artifacts.transcript
    if ($null -eq $transcript) {
        New-Failure "Transcript semantic validation failed: transcript is required"
    }
    if ((Get-Sha256Hex -Path $transcript.path) -cne $transcript.sha256) {
        New-Failure "Transcript hash mismatch"
    }
    $actualSize = [int64](Get-Item -LiteralPath $transcript.path).Length
    if ($actualSize -ne [int64]$transcript.size -or $actualSize -le 0) {
        New-Failure "Transcript semantic validation failed: size mismatch"
    }
    $lines = @(Get-Content -LiteralPath $transcript.path)
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

function Get-StatusPayload {
    param(
        [hashtable]$Plan,
        [string]$StatePathValue
    )

    try {
        Assert-TestModeMutablePaths -Plan $Plan -StatePathValue $StatePathValue
        Assert-ImmutableManifestAndBackups -Plan $Plan
        $current = Get-CurrentIdentities -Plan $Plan
        $exactOriginals = Test-CurrentIdentitiesAreExactOriginals -Current $current -Plan $Plan
        $originalHashes = Test-CurrentHashesAreOriginals -Current $current -Plan $Plan
        if (-not (Test-Path -LiteralPath $StatePathValue -PathType Leaf)) {
            if ($exactOriginals) {
                return [ordered]@{
                    classification = "ORIGINAL_CALIBRATION_INTACT"
                    next_stage     = "Calibrate"
                }
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
        Assert-StateProvenance -State $state -StatePathValue $StatePathValue -Plan $Plan
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
        return [ordered]@{
            classification = "VALID_FRESH_CALIBRATION"
            next_stage     = $nextStage
            final_result   = $state.final_result
        }
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
    switch ($Stage) {
        "DiagnoseImports" { Invoke-DiagnoseImportsStage -Plan $plan }
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
    }
    exit 1
}
