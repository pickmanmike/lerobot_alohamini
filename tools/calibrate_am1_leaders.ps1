[CmdletBinding()]
param(
    [Parameter()][switch]$Status,
    [Parameter()][switch]$Calibrate,
    [Parameter()][string]$Confirm,
    [Parameter()][string]$LeftPort = "COM8",
    [Parameter()][string]$RightPort = "COM7",
    [Parameter()][string]$LeaderId = "so101_leader_bi",
    [Parameter()][string]$ArmProfile = "so-arm-5dof"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-Am1CalibrationWrapperVersion {
    "am1-simple-leader-calibration-v1"
}

function Test-Am1JsonInteger {
    param([Parameter(Mandatory = $true)]$Value)

    return (
        $Value -is [sbyte] -or
        $Value -is [byte] -or
        $Value -is [int16] -or
        $Value -is [uint16] -or
        $Value -is [int32] -or
        $Value -is [uint32] -or
        $Value -is [int64] -or
        $Value -is [uint64]
    )
}

function Assert-Am1ExactKeys {
    param(
        [Parameter(Mandatory = $true)][System.Collections.IDictionary]$Table,
        [Parameter(Mandatory = $true)][string[]]$Expected,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $actual = @($Table.Keys | ForEach-Object { [string]$_ })
    if ($actual.Count -ne $Expected.Count) {
        throw "$Label must contain exactly these keys: $($Expected -join ', ')"
    }
    foreach ($name in $Expected) {
        if (-not ($actual -ccontains $name)) {
            throw "$Label must contain exactly these keys: $($Expected -join ', ')"
        }
    }
    foreach ($name in $actual) {
        if (-not ($Expected -ccontains $name)) {
            throw "$Label must contain exactly these keys: $($Expected -join ', ')"
        }
    }
}

function Get-Am1FileIdentity {
    param([Parameter(Mandatory = $true)][string]$Path)

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    if (-not (Test-Path -LiteralPath $fullPath)) {
        return [pscustomobject][ordered]@{
            path         = $fullPath
            exists       = $false
            size         = $null
            mtime_utc    = $null
            sha256       = $null
            schema_valid = $false
            schema_error = "File is missing: $fullPath"
        }
    }

    $item = Get-Item -LiteralPath $fullPath -Force
    if ($item.PSIsContainer) {
        throw "Calibration path is not a regular file: $fullPath"
    }
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Calibration path is a reparse point: $fullPath"
    }
    if (-not [System.IO.File]::Exists($fullPath)) {
        throw "Calibration path is not a regular file: $fullPath"
    }

    return [pscustomobject][ordered]@{
        path         = $item.FullName
        exists       = $true
        size         = [int64]$item.Length
        mtime_utc    = $item.LastWriteTimeUtc.ToString(
            "yyyy-MM-ddTHH:mm:ss.fffffffZ",
            [System.Globalization.CultureInfo]::InvariantCulture
        )
        sha256       = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToUpperInvariant()
        schema_valid = $false
        schema_error = $null
    }
}

function Assert-Am1CalibrationFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    $facts = Get-Am1FileIdentity -Path $Path
    if (-not $facts.exists) {
        throw $facts.schema_error
    }
    if ($facts.size -le 0) {
        throw "Calibration file is empty: $($facts.path)"
    }

    try {
        $payload = Get-Content -LiteralPath $facts.path -Raw | ConvertFrom-Json -AsHashtable -Depth 100
    }
    catch {
        throw "Calibration JSON is malformed at $($facts.path): $($_.Exception.Message)"
    }
    if ($payload -isnot [System.Collections.IDictionary]) {
        throw "Calibration JSON root must be an object: $($facts.path)"
    }

    $jointIds = [ordered]@{
        shoulder_pan  = 1
        shoulder_lift = 2
        elbow_flex    = 3
        wrist_flex    = 4
        wrist_roll    = 5
        gripper       = 6
    }
    $fields = @("id", "drive_mode", "homing_offset", "range_min", "range_max")
    Assert-Am1ExactKeys -Table $payload -Expected @($jointIds.Keys) -Label "Calibration joints"

    $canonical = [ordered]@{}
    foreach ($joint in $jointIds.Keys) {
        $record = $payload[$joint]
        if ($record -isnot [System.Collections.IDictionary]) {
            throw "Calibration joint $joint must be an object"
        }
        Assert-Am1ExactKeys -Table $record -Expected $fields -Label "Calibration joint $joint fields"
        foreach ($field in $fields) {
            if (-not (Test-Am1JsonInteger -Value $record[$field])) {
                throw "Calibration joint $joint field $field must be a JSON integer, not a boolean or float"
            }
        }

        $jointId = [int64]$record["id"]
        if ($jointId -ne [int64]$jointIds[$joint]) {
            throw "Calibration joint $joint has wrong or duplicate ID $jointId; expected $($jointIds[$joint])"
        }
        if ([int64]$record["drive_mode"] -ne 0) {
            throw "Calibration joint $joint drive_mode must be 0"
        }

        $minimum = [int64]$record["range_min"]
        $maximum = [int64]$record["range_max"]
        if ($joint -ceq "wrist_roll") {
            if ($minimum -ne 0 -or $maximum -ne 4095) {
                throw "Calibration wrist_roll range must be exactly 0..4095"
            }
        }
        elseif ($minimum -lt 0 -or $minimum -ge $maximum -or $maximum -gt 4095) {
            throw "Calibration joint $joint range must satisfy 0 <= range_min < range_max <= 4095"
        }

        $canonical[$joint] = [ordered]@{}
        foreach ($field in $fields) {
            $canonical[$joint][$field] = [int64]$record[$field]
        }
    }

    $facts.schema_valid = $true
    $facts.schema_error = $null
    return [pscustomobject][ordered]@{
        facts     = $facts
        canonical = ($canonical | ConvertTo-Json -Depth 100 -Compress)
    }
}

function Get-Am1CalibrationPairStatus {
    param(
        [Parameter(Mandatory = $true)][string]$DirectoryPath,
        [Parameter(Mandatory = $true)][string]$LeaderIdValue
    )

    $directory = [System.IO.Path]::GetFullPath($DirectoryPath)
    $leftPath = Join-Path $directory "${LeaderIdValue}_left.json"
    $rightPath = Join-Path $directory "${LeaderIdValue}_right.json"
    $leftFacts = Get-Am1FileIdentity -Path $leftPath
    $rightFacts = Get-Am1FileIdentity -Path $rightPath
    $errors = [System.Collections.Generic.List[string]]::new()
    $leftRecord = $null
    $rightRecord = $null

    if (-not $leftFacts.exists) {
        $errors.Add($leftFacts.schema_error)
    }
    else {
        try {
            $leftRecord = Assert-Am1CalibrationFile -Path $leftPath
            $leftFacts = $leftRecord.facts
        }
        catch {
            $leftFacts.schema_error = $_.Exception.Message
            $errors.Add($_.Exception.Message)
        }
    }
    if (-not $rightFacts.exists) {
        $errors.Add($rightFacts.schema_error)
    }
    else {
        try {
            $rightRecord = Assert-Am1CalibrationFile -Path $rightPath
            $rightFacts = $rightRecord.facts
        }
        catch {
            $rightFacts.schema_error = $_.Exception.Message
            $errors.Add($_.Exception.Message)
        }
    }

    if (
        $null -ne $leftRecord -and
        $null -ne $rightRecord -and
        [System.StringComparer]::Ordinal.Equals($leftRecord.canonical, $rightRecord.canonical)
    ) {
        $errors.Add("Left and right calibration payloads must be distinct")
    }

    $classification = if ($errors.Count -eq 0) {
        "VALID_COMPLETE_PAIR"
    }
    else {
        "INCOMPLETE_OR_INVALID_PAIR"
    }
    $failureReason = if ($errors.Count -eq 0) { $null } else { $errors[0] }
    return [pscustomobject][ordered]@{
        classification = $classification
        failure_reason = $failureReason
        left           = $leftFacts
        right          = $rightFacts
    }
}

function Assert-Am1FixedIdentity {
    param(
        [Parameter(Mandatory = $true)][string]$LeftPortValue,
        [Parameter(Mandatory = $true)][string]$RightPortValue,
        [Parameter(Mandatory = $true)][string]$LeaderIdValue,
        [Parameter(Mandatory = $true)][string]$ArmProfileValue,
        [Parameter()][AllowNull()][string]$Confirmation,
        [Parameter()][switch]$RequireCalibrationConfirmation
    )

    if ($LeftPortValue -cne "COM8") {
        throw "AM1 left port must be exactly COM8"
    }
    if ($RightPortValue -cne "COM7") {
        throw "AM1 right port must be exactly COM7"
    }
    if ($LeaderIdValue -cne "so101_leader_bi") {
        throw "AM1 leader ID must be exactly so101_leader_bi"
    }
    if ($ArmProfileValue -cne "so-arm-5dof") {
        throw "AM1 arm profile must be exactly so-arm-5dof"
    }
    if ($RequireCalibrationConfirmation -and $Confirmation -cne "CALIBRATE") {
        throw "Calibration confirmation must be exact uppercase CALIBRATE"
    }
}

function Get-Am1RegularFileSnapshot {
    param([Parameter(Mandatory = $true)][string]$DirectoryPath)

    $directory = Get-Item -LiteralPath ([System.IO.Path]::GetFullPath($DirectoryPath)) -Force
    if (-not $directory.PSIsContainer) {
        throw "Active calibration path is not a directory: $($directory.FullName)"
    }
    if (($directory.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Active calibration directory is a forbidden reparse point: $($directory.FullName)"
    }

    $byName = [System.Collections.Generic.Dictionary[string, object]]::new(
        [System.StringComparer]::Ordinal
    )
    foreach ($item in @(Get-ChildItem -LiteralPath $directory.FullName -Force)) {
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Active calibration tree contains forbidden reparse point: $($item.FullName)"
        }
        if ($item.PSIsContainer) {
            throw "Active calibration tree contains forbidden directory: $($item.FullName)"
        }
        if (-not [System.IO.File]::Exists($item.FullName)) {
            throw "Active calibration tree contains a nonregular entry: $($item.FullName)"
        }
        $byName.Add($item.Name, $item)
    }

    $names = [string[]]@($byName.Keys)
    [System.Array]::Sort($names, [System.StringComparer]::Ordinal)
    $snapshot = foreach ($name in $names) {
        $item = $byName[$name]
        [pscustomobject][ordered]@{
            relative_path = $name
            size          = [int64]$item.Length
            sha256        = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToUpperInvariant()
        }
    }
    return ,@($snapshot)
}

function Assert-Am1SnapshotMatches {
    param(
        [Parameter(Mandatory = $true)][object[]]$ExpectedSnapshot,
        [Parameter(Mandatory = $true)][string]$DirectoryPath
    )

    $actual = @(Get-Am1RegularFileSnapshot -DirectoryPath $DirectoryPath)
    if ($actual.Count -ne $ExpectedSnapshot.Count) {
        throw "Active calibration tree changed: regular-file count differs"
    }
    for ($index = 0; $index -lt $actual.Count; $index += 1) {
        $expected = $ExpectedSnapshot[$index]
        $observed = $actual[$index]
        if (
            $expected.relative_path -cne $observed.relative_path -or
            [int64]$expected.size -ne [int64]$observed.size -or
            $expected.sha256 -cne $observed.sha256
        ) {
            throw "Active calibration tree changed at $($observed.relative_path)"
        }
    }
}

function New-Am1NativeCalibrationCommand {
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$StagingLeaf
    )

    $repository = [System.IO.Path]::GetFullPath($RepositoryRoot)
    $python = [System.IO.Path]::GetFullPath($PythonPath)
    $staging = [System.IO.Path]::GetFullPath($StagingLeaf)
    return [pscustomobject][ordered]@{
        executable        = $python
        arguments         = @(
            (Join-Path $repository "examples\alohamini\calibrate_bi.py")
            "--teleop.left_port"
            "COM8"
            "--teleop.right_port"
            "COM7"
            "--teleop.id"
            "so101_leader_bi"
            "--teleop.arm_profile"
            "so-arm-5dof"
            "--teleop.calibration_dir"
            $staging
            "--force_fresh_calibration"
        )
        working_directory = $repository
    }
}

function Test-Am1PathEqual {
    param(
        [Parameter(Mandatory = $true)][string]$Left,
        [Parameter(Mandatory = $true)][string]$Right
    )

    return [System.StringComparer]::OrdinalIgnoreCase.Equals(
        [System.IO.Path]::GetFullPath($Left),
        [System.IO.Path]::GetFullPath($Right)
    )
}

function Invoke-Am1CapturedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
    )

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $Executable
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.CreateNoWindow = $true
    foreach ($argument in $Arguments) {
        $startInfo.ArgumentList.Add($argument)
    }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    try {
        $process.Start() | Out-Null
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        $exitCode = $process.ExitCode
    }
    finally {
        $process.Dispose()
    }

    return [pscustomobject][ordered]@{
        exit_code = [int]$exitCode
        stdout    = $stdout.TrimEnd([char[]]"`r`n")
        stderr    = $stderr.TrimEnd([char[]]"`r`n")
    }
}

function Get-Am1SuccessfulProcessOutput {
    param(
        [Parameter(Mandatory = $true)]$Result,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if ([int]$Result.exit_code -ne 0) {
        throw "$Label failed with exit code $($Result.exit_code): $($Result.stderr)"
    }
    if (-not [string]::IsNullOrWhiteSpace([string]$Result.stderr)) {
        throw "$Label wrote unexpected stderr: $($Result.stderr)"
    }
    return [string]$Result.stdout
}

function Get-Am1RepositoryProvenance {
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter()][scriptblock]$PythonProbeInvoker,
        [Parameter()][scriptblock]$GitInvoker
    )

    foreach ($name in @("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE")) {
        $value = [System.Environment]::GetEnvironmentVariable($name, "Process")
        if ($null -ne $value -and $value.Length -gt 0) {
            throw "Import environment variable $name must be unset"
        }
    }

    $repository = [System.IO.Path]::GetFullPath($RepositoryRoot)
    $python = [System.IO.Path]::GetFullPath($PythonPath)
    $expectedPython = Join-Path $repository ".venv\Scripts\python.exe"
    if (-not (Test-Am1PathEqual -Left $python -Right $expectedPython)) {
        throw "Repository Python must be exactly $expectedPython"
    }
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Repository Python is missing: $python"
    }
    $pythonItem = Get-Item -LiteralPath $python -Force
    if (($pythonItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Repository Python must not be a reparse point: $python"
    }

    if ($null -eq $PythonProbeInvoker) {
        $PythonProbeInvoker = ${function:Invoke-Am1CapturedProcess}
    }
    if ($null -eq $GitInvoker) {
        $GitInvoker = ${function:Invoke-Am1CapturedProcess}
    }

    $probeCode = @'
import importlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "examples" / "alohamini"))
import calibrate_bi
import leader_client_utils
import lerobot
from lerobot.utils.constants import HF_LEROBOT_CALIBRATION

bi_module = importlib.import_module("lerobot.teleoperators.bi_so_leader.bi_so_leader")
so_module = importlib.import_module("lerobot.teleoperators.so_leader.so_leader")
print(json.dumps({
    "cwd": os.getcwd(),
    "executable": sys.executable,
    "prefix": sys.prefix,
    "calibration_root": str(HF_LEROBOT_CALIBRATION),
    "modules": {
        "lerobot": lerobot.__file__,
        "calibrate_bi": calibrate_bi.__file__,
        "leader_client_utils": leader_client_utils.__file__,
        "bi_so_leader": bi_module.__file__,
        "so_leader": so_module.__file__,
    },
}, separators=(",", ":")))
'@
    $probeArguments = [string[]]@("-B", "-c", $probeCode)
    $probeResult = & $PythonProbeInvoker $python $probeArguments $repository
    $probeText = Get-Am1SuccessfulProcessOutput -Result $probeResult -Label "Repository Python probe"
    $probeLines = @($probeText -split "`r?`n" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($probeLines.Count -ne 1) {
        throw "Repository Python probe must return exactly one JSON line"
    }
    try {
        $probe = $probeLines[0] | ConvertFrom-Json -AsHashtable -Depth 100
    }
    catch {
        throw "Repository Python probe returned malformed JSON: $($_.Exception.Message)"
    }
    Assert-Am1ExactKeys -Table $probe `
        -Expected @("cwd", "executable", "prefix", "calibration_root", "modules") `
        -Label "Repository Python probe"

    if (-not (Test-Am1PathEqual -Left ([string]$probe["cwd"]) -Right $repository)) {
        throw "Repository Python probe working directory does not match $repository"
    }
    if (-not (Test-Am1PathEqual -Left ([string]$probe["executable"]) -Right $python)) {
        throw "Repository Python probe sys.executable does not match $python"
    }
    $expectedPrefix = Join-Path $repository ".venv"
    if (-not (Test-Am1PathEqual -Left ([string]$probe["prefix"]) -Right $expectedPrefix)) {
        throw "Repository Python probe sys.prefix does not match $expectedPrefix"
    }
    if ($probe["modules"] -isnot [System.Collections.IDictionary]) {
        throw "Repository Python probe modules must be an object"
    }
    $expectedModules = [ordered]@{
        lerobot             = Join-Path $repository "src\lerobot\__init__.py"
        calibrate_bi        = Join-Path $repository "examples\alohamini\calibrate_bi.py"
        leader_client_utils = Join-Path $repository "examples\alohamini\leader_client_utils.py"
        bi_so_leader        = Join-Path $repository "src\lerobot\teleoperators\bi_so_leader\bi_so_leader.py"
        so_leader           = Join-Path $repository "src\lerobot\teleoperators\so_leader\so_leader.py"
    }
    Assert-Am1ExactKeys -Table $probe["modules"] -Expected @($expectedModules.Keys) `
        -Label "Repository Python probe module paths"
    foreach ($name in $expectedModules.Keys) {
        if (-not (Test-Am1PathEqual -Left ([string]$probe["modules"][$name]) -Right $expectedModules[$name])) {
            throw "Repository module path for $name does not match $($expectedModules[$name])"
        }
    }

    $calibrationRoot = [string]$probe["calibration_root"]
    if ([string]::IsNullOrWhiteSpace($calibrationRoot) -or -not [System.IO.Path]::IsPathFullyQualified($calibrationRoot)) {
        throw "Repository Python probe returned an invalid calibration root"
    }
    $calibrationRoot = [System.IO.Path]::GetFullPath($calibrationRoot)

    $branchArguments = [string[]]@("-C", $repository, "branch", "--show-current")
    $headArguments = [string[]]@("-C", $repository, "rev-parse", "HEAD")
    $statusArguments = [string[]]@(
        "--no-optional-locks", "-C", $repository, "status", "--porcelain=v1", "--untracked-files=all"
    )
    $branch = Get-Am1SuccessfulProcessOutput `
        -Result (& $GitInvoker "git" $branchArguments $repository) -Label "Git branch query"
    $head = Get-Am1SuccessfulProcessOutput `
        -Result (& $GitInvoker "git" $headArguments $repository) -Label "Git HEAD query"
    $porcelain = Get-Am1SuccessfulProcessOutput `
        -Result (& $GitInvoker "git" $statusArguments $repository) -Label "Git status query"
    $branch = $branch.Trim()
    $head = $head.Trim()
    if ([string]::IsNullOrWhiteSpace($branch)) {
        throw "Git branch query returned no branch"
    }
    if ($head -notmatch "\A[0-9a-fA-F]{40}\z") {
        throw "Git HEAD query did not return one full commit SHA"
    }

    return [pscustomobject][ordered]@{
        repository_root = $repository
        python          = $python
        calibration_root = $calibrationRoot
        branch          = $branch
        head            = $head
        porcelain       = $porcelain
    }
}

function Get-Am1LeaderCalibrationStatus {
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$LeftPortValue,
        [Parameter(Mandatory = $true)][string]$RightPortValue,
        [Parameter(Mandatory = $true)][string]$LeaderIdValue,
        [Parameter(Mandatory = $true)][string]$ArmProfileValue,
        [Parameter()][scriptblock]$PythonProbeInvoker,
        [Parameter()][scriptblock]$GitInvoker
    )

    Assert-Am1FixedIdentity -LeftPortValue $LeftPortValue -RightPortValue $RightPortValue `
        -LeaderIdValue $LeaderIdValue -ArmProfileValue $ArmProfileValue
    $provenance = Get-Am1RepositoryProvenance -RepositoryRoot $RepositoryRoot -PythonPath $PythonPath `
        -PythonProbeInvoker $PythonProbeInvoker -GitInvoker $GitInvoker
    $activeDirectory = Join-Path $provenance.calibration_root "teleoperators\so_leader"
    $pair = Get-Am1CalibrationPairStatus -DirectoryPath $activeDirectory -LeaderIdValue $LeaderIdValue
    return [pscustomobject][ordered]@{
        provenance       = $provenance
        active_directory = $activeDirectory
        pair             = $pair
    }
}

function Write-Am1LeaderCalibrationStatus {
    param([Parameter(Mandatory = $true)]$StatusFacts)

    $provenance = $StatusFacts.provenance
    $pair = $StatusFacts.pair
    [Console]::Out.WriteLine("REPOSITORY_PYTHON=$($provenance.python)")
    [Console]::Out.WriteLine("CALIBRATION_ROOT=$($provenance.calibration_root)")
    [Console]::Out.WriteLine("REPOSITORY_BRANCH=$($provenance.branch)")
    [Console]::Out.WriteLine("REPOSITORY_HEAD=$($provenance.head)")
    [Console]::Out.WriteLine("REPOSITORY_STATUS_BEGIN")
    if (-not [string]::IsNullOrEmpty([string]$provenance.porcelain)) {
        foreach ($line in ([string]$provenance.porcelain -split "`r?`n")) {
            [Console]::Out.WriteLine($line)
        }
    }
    [Console]::Out.WriteLine("REPOSITORY_STATUS_END")
    [Console]::Out.WriteLine("CLASSIFICATION=$($pair.classification)")
    if ($null -ne $pair.failure_reason) {
        [Console]::Out.WriteLine("FAILURE_REASON=$($pair.failure_reason)")
    }
    foreach ($side in @("left", "right")) {
        $label = $side.ToUpperInvariant()
        $facts = $pair.$side
        [Console]::Out.WriteLine("${label}_PATH=$($facts.path)")
        [Console]::Out.WriteLine("${label}_EXISTS=$($facts.exists)")
        [Console]::Out.WriteLine("${label}_SIZE=$($facts.size)")
        [Console]::Out.WriteLine("${label}_MTIME_UTC=$($facts.mtime_utc)")
        [Console]::Out.WriteLine("${label}_SHA256=$($facts.sha256)")
        [Console]::Out.WriteLine("${label}_SCHEMA_VALID=$($facts.schema_valid)")
        if ($null -ne $facts.schema_error) {
            [Console]::Out.WriteLine("${label}_SCHEMA_ERROR=$($facts.schema_error)")
        }
    }
}

function Invoke-Am1LeaderCalibrationMain {
    param(
        [Parameter(Mandatory = $true)][bool]$StatusMode,
        [Parameter(Mandatory = $true)][bool]$CalibrateMode,
        [Parameter()][AllowNull()][string]$Confirmation,
        [Parameter(Mandatory = $true)][string]$LeftPortValue,
        [Parameter(Mandatory = $true)][string]$RightPortValue,
        [Parameter(Mandatory = $true)][string]$LeaderIdValue,
        [Parameter(Mandatory = $true)][string]$ArmProfileValue
    )

    if ($StatusMode -eq $CalibrateMode) {
        throw "Exactly one of -Status and -Calibrate is required"
    }
    $repository = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
    $python = Join-Path $repository ".venv\Scripts\python.exe"
    if ($StatusMode) {
        $facts = Get-Am1LeaderCalibrationStatus -RepositoryRoot $repository -PythonPath $python `
            -LeftPortValue $LeftPortValue -RightPortValue $RightPortValue `
            -LeaderIdValue $LeaderIdValue -ArmProfileValue $ArmProfileValue
        Write-Am1LeaderCalibrationStatus -StatusFacts $facts
        return
    }

    Assert-Am1FixedIdentity -LeftPortValue $LeftPortValue -RightPortValue $RightPortValue `
        -LeaderIdValue $LeaderIdValue -ArmProfileValue $ArmProfileValue `
        -Confirmation $Confirmation -RequireCalibrationConfirmation
    throw "Leader calibration execution is not implemented"
}

if ($MyInvocation.InvocationName -ne ".") {
    try {
        Invoke-Am1LeaderCalibrationMain -StatusMode ([bool]$Status) -CalibrateMode ([bool]$Calibrate) `
            -Confirmation $Confirm -LeftPortValue $LeftPort -RightPortValue $RightPort `
            -LeaderIdValue $LeaderId -ArmProfileValue $ArmProfile
        exit 0
    }
    catch {
        [Console]::Error.WriteLine($_.Exception.Message)
        if ($Calibrate) {
            [Console]::Out.WriteLine("CALIBRATION_RESULT=FAIL")
        }
        exit 1
    }
}
