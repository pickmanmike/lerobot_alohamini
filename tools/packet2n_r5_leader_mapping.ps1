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
        Remove-Item -LiteralPath $Path -Force
    }
    Move-Item -LiteralPath $tempPath -Destination $Path
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
    return Read-JsonFile -Path $TestPlanPath
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
    return [ordered]@{
        schema_version   = $SchemaVersion
        runner_version   = $RunnerVersion
        packet_identity  = $PacketIdentity
        session_id       = Get-SessionId
        utc_start        = [DateTime]::UtcNow.ToString("o")
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
                }
            }
            MapLeft = [ordered]@{
                result = "pending"
                native = [ordered]@{
                    attempted      = $false
                    launched       = $false
                    real_exit_code = $null
                }
            }
            MapRight = [ordered]@{
                result = "pending"
                native = [ordered]@{
                    attempted      = $false
                    launched       = $false
                    real_exit_code = $null
                }
            }
            Verify = [ordered]@{
                result = "pending"
                native = [ordered]@{
                    attempted      = $false
                    launched       = $false
                    real_exit_code = $null
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
    $current = Get-CurrentIdentities -Plan $Plan
    if ($current.left.sha256 -ne $State.post_calibration.left.sha256 -or $current.right.sha256 -ne $State.post_calibration.right.sha256) {
        New-Failure "Current calibration does not match evidence"
    }
}

function Invoke-TestModeNative {
    param(
        [Parameter(Mandatory = $true)]
        [string]$StageName,

        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [hashtable]$State,

        [Parameter(Mandatory = $true)]
        [string]$StatePathValue
    )

    $stagePlan = $Plan.stage_plan[$StageName]
    if ($null -eq $stagePlan) {
        New-Failure "Missing test stage plan for $StageName"
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
    $State.stages[$StageName].native.real_exit_code = $exitCode
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

    switch ($StageName) {
        "Calibrate" {
            Assert-PathMissing -Path $stagePlan.transcript_path
            Assert-PathMissing -Path $stagePlan.evidence_path
            Write-TextAtomic -Path $stagePlan.transcript_path -Text $stagePlan.transcript_text
            Write-JsonAtomic -Path $Plan.calibration.left.path -Value $stagePlan.post_calibration.left -Overwrite
            Write-JsonAtomic -Path $Plan.calibration.right.path -Value $stagePlan.post_calibration.right -Overwrite
            Write-TextAtomic -Path $stagePlan.evidence_path -Text $stagePlan.evidence_text
        }
        "MapLeft" {
            Assert-PathMissing -Path $stagePlan.map_path
            $mapText = New-MapLogText -StageName $StageName -State $State -PhysicalSide $stagePlan.physical_side
            Write-TextAtomic -Path $stagePlan.map_path -Text $mapText
        }
        "MapRight" {
            Assert-PathMissing -Path $stagePlan.map_path
            $mapText = New-MapLogText -StageName $StageName -State $State -PhysicalSide $stagePlan.physical_side
            Write-TextAtomic -Path $stagePlan.map_path -Text $mapText
        }
    }
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
    if ($content -match "(?i)zmq|calibration") {
        New-Failure "Map log validation failed for ${StageName}: runtime text is forbidden"
    }
    $lines = @($content -split "`r?`n" | Where-Object { $_ -ne "" })
    if ($lines.Count -lt 7) {
        New-Failure "Map log validation failed for ${StageName}: log is incomplete"
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
    if ($samples.Count -ne 60) {
        New-Failure "Map log validation failed for ${StageName}: expected exactly 60 samples"
    }

    $leftGripper = [System.Collections.Generic.List[double]]::new()
    $rightGripper = [System.Collections.Generic.List[double]]::new()
    $leftOther = [System.Collections.Generic.List[double]]::new()
    $rightOther = [System.Collections.Generic.List[double]]::new()
    foreach ($sample in $samples) {
        $tokens = @($sample.Split(" ", [System.StringSplitOptions]::RemoveEmptyEntries))
        if ($tokens.Count -ne 14) {
            New-Failure "Map log validation failed for ${StageName}: sample field count mismatch"
        }
        $seen = @{}
        for ($index = 2; $index -lt $tokens.Count; $index++) {
            $pair = $tokens[$index].Split("=", 2)
            if ($pair.Count -ne 2) {
                New-Failure "Map log validation failed for ${StageName}: malformed sample token"
            }
            $key = $pair[0]
            if ($seen.ContainsKey($key)) {
                New-Failure "Map log validation failed for ${StageName}: duplicate key $key"
            }
            if ($ExpectedMapKeys -notcontains $key) {
                New-Failure "Map log validation failed for ${StageName}: unexpected key $key"
            }
            $value = 0.0
            if (-not [double]::TryParse($pair[1], [ref]$value) -or [double]::IsNaN($value) -or [double]::IsInfinity($value)) {
                New-Failure "Map log validation failed for ${StageName}: nonnumeric value for $key"
            }
            $seen[$key] = $value
        }
        foreach ($expectedKey in $ExpectedMapKeys) {
            if (-not $seen.ContainsKey($expectedKey)) {
                New-Failure "Map log validation failed for ${StageName}: missing key $expectedKey"
            }
        }
        $leftGripper.Add([double]$seen["arm_left_gripper.pos"])
        $rightGripper.Add([double]$seen["arm_right_gripper.pos"])
        foreach ($leftKey in @("arm_left_shoulder_pan.pos", "arm_left_shoulder_lift.pos", "arm_left_elbow_flex.pos", "arm_left_wrist_flex.pos", "arm_left_wrist_roll.pos")) {
            $leftOther.Add([double]$seen[$leftKey])
        }
        foreach ($rightKey in @("arm_right_shoulder_pan.pos", "arm_right_shoulder_lift.pos", "arm_right_elbow_flex.pos", "arm_right_wrist_flex.pos", "arm_right_wrist_roll.pos")) {
            $rightOther.Add([double]$seen[$rightKey])
        }
    }

    $leftSpan = ($leftGripper | Measure-Object -Maximum -Minimum)
    $rightSpan = ($rightGripper | Measure-Object -Maximum -Minimum)
    $leftOtherSpan = ($leftOther | Measure-Object -Maximum -Minimum)
    $rightOtherSpan = ($rightOther | Measure-Object -Maximum -Minimum)
    $leftGripperDelta = [double]$leftSpan.Maximum - [double]$leftSpan.Minimum
    $rightGripperDelta = [double]$rightSpan.Maximum - [double]$rightSpan.Minimum
    $leftFamilyDelta = [double]$leftOtherSpan.Maximum - [double]$leftOtherSpan.Minimum
    $rightFamilyDelta = [double]$rightOtherSpan.Maximum - [double]$rightOtherSpan.Minimum

    if ($StageName -eq "MapLeft") {
        if ($leftGripperDelta -lt 20.0 -or $rightGripperDelta -ge 2.0 -or $rightFamilyDelta -ge 2.0) {
            New-Failure "Map log validation failed for ${StageName}: logical-left classification failed"
        }
    }
    else {
        if ($rightGripperDelta -lt 20.0 -or $leftGripperDelta -ge 2.0 -or $leftFamilyDelta -ge 2.0) {
            New-Failure "Map log validation failed for ${StageName}: logical-right classification failed"
        }
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

function Invoke-CalibrateStage {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Plan,

        [Parameter(Mandatory = $true)]
        [string]$StatePathValue
    )

    Assert-RepoAndEnvGuards -Plan $Plan
    Assert-ManifestAndBackups -Plan $Plan
    if (Test-Path -LiteralPath $StatePathValue) {
        New-Failure "Calibrate refuses when the state path already exists"
    }
    $state = New-InitialState -Plan $Plan -StatePathValue $StatePathValue
    Save-State -Path $StatePathValue -State $state
    try {
        Invoke-TestModeNative -StageName "Calibrate" -Plan $Plan -State $state -StatePathValue $StatePathValue
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
        $state.artifacts.transcript = Get-FileInfoSnapshot -Path $Plan.stage_plan.Calibrate.transcript_path
        $state.artifacts.evidence = Get-FileInfoSnapshot -Path $Plan.stage_plan.Calibrate.evidence_path
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
    Assert-StateIdentity -State $state
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
        Invoke-TestModeNative -StageName $StageName -Plan $Plan -State $state -StatePathValue $StatePathValue
        $mapArtifactKey = if ($StageName -eq "MapLeft") { "map_left" } else { "map_right" }
        $mapPath = $Plan.stage_plan[$StageName].map_path
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
    Assert-StateIdentity -State $state
    Assert-EvidenceAndCalibrationStillMatch -State $state -Plan $Plan
    if ($null -eq $state.artifacts.map_left -or $null -eq $state.artifacts.map_right) {
        New-Failure "Verify requires both map artifacts"
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
        return [ordered]@{
            classification = "ORIGINAL_CALIBRATION_INTACT"
            next_stage     = "Calibrate"
        }
    }
    $state = Read-JsonFile -Path $StatePathValue
    return [ordered]@{
        classification = $state.classification
        next_stage     = $state.next_stage
        final_result   = $state.final_result
    }
}

try {
    Require-Confirmation -StageName $Stage -ConfirmValue $Confirm
    $plan = Get-TestModePlan
    if ($Stage -eq "Status") {
        $payload = Get-StatusPayload -Plan $plan -StatePathValue $StatePath
        [Console]::Out.WriteLine((ConvertTo-CanonicalJson -Value $payload))
        exit 0
    }
    if ($null -eq $plan) {
        New-Failure "Hardware-capable stages are intentionally disabled outside PACKET2N_R5_TEST_MODE=1 for this offline runner task"
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
