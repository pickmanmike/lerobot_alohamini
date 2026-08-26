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
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Calibration path is a reparse point: $fullPath"
    }
    if ($item.PSIsContainer) {
        throw "Calibration path is not a regular file: $fullPath"
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

function New-Am1InvalidFileIdentity {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Reason
    )

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $exists = [System.IO.File]::Exists($fullPath) -or [System.IO.Directory]::Exists($fullPath)
    return [pscustomobject][ordered]@{
        path         = $fullPath
        exists       = [bool]$exists
        size         = $null
        mtime_utc    = $null
        sha256       = $null
        schema_valid = $false
        schema_error = $Reason
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
    $errors = [System.Collections.Generic.List[string]]::new()
    $leftRecord = $null
    $rightRecord = $null
    $leftIdentityError = $null
    $rightIdentityError = $null

    try {
        $leftFacts = Get-Am1FileIdentity -Path $leftPath
    }
    catch {
        $leftIdentityError = $_.Exception.Message
        $leftFacts = New-Am1InvalidFileIdentity -Path $leftPath -Reason $leftIdentityError
    }
    try {
        $rightFacts = Get-Am1FileIdentity -Path $rightPath
    }
    catch {
        $rightIdentityError = $_.Exception.Message
        $rightFacts = New-Am1InvalidFileIdentity -Path $rightPath -Reason $rightIdentityError
    }

    if ($null -ne $leftIdentityError) {
        $errors.Add($leftIdentityError)
    }
    elseif (-not $leftFacts.exists) {
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
    if ($null -ne $rightIdentityError) {
        $errors.Add($rightIdentityError)
    }
    elseif (-not $rightFacts.exists) {
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
    return @($snapshot)
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

function Copy-Am1RegularFile {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    [System.IO.File]::Copy($Source, $Destination, $false)
}

function New-Am1PairBackup {
    param(
        [Parameter(Mandatory = $true)][string]$ActiveDirectory,
        [Parameter(Mandatory = $true)][string]$BackupDirectory,
        [Parameter(Mandatory = $true)][string]$LeaderIdValue,
        [Parameter(Mandatory = $true)]$ActivePair,
        [Parameter(Mandatory = $true)][object[]]$ExpectedActiveSnapshot,
        [Parameter()][scriptblock]$CopyFileInvoker
    )

    $backup = [System.IO.Path]::GetFullPath($BackupDirectory)
    if (Test-Path -LiteralPath $backup) {
        throw "Pair backup path already exists: $backup"
    }
    [System.IO.Directory]::CreateDirectory($backup) | Out-Null
    if ($null -eq $CopyFileInvoker) {
        $CopyFileInvoker = ${function:Copy-Am1RegularFile}
    }

    foreach ($side in @("left", "right")) {
        $source = [string]$ActivePair.$side.path
        $destination = Join-Path $backup "${LeaderIdValue}_${side}.json"
        & $CopyFileInvoker $source $destination | Out-Null
        $copied = Get-Am1FileIdentity -Path $destination
        if (
            $copied.sha256 -cne $ActivePair.$side.sha256 -or
            [int64]$copied.size -ne [int64]$ActivePair.$side.size
        ) {
            throw "Pair backup hash mismatch for $side calibration: $destination"
        }
    }

    $backupPair = Get-Am1CalibrationPairStatus -DirectoryPath $backup -LeaderIdValue $LeaderIdValue
    if ($backupPair.classification -cne "VALID_COMPLETE_PAIR") {
        throw "Pair backup is invalid: $($backupPair.failure_reason)"
    }
    Assert-Am1SnapshotMatches -ExpectedSnapshot $ExpectedActiveSnapshot -DirectoryPath $ActiveDirectory
    return [pscustomobject][ordered]@{
        backup_directory = $backup
        left             = $backupPair.left
        right            = $backupPair.right
    }
}

function Assert-Am1OrdinaryDirectoryChain {
    param(
        [Parameter(Mandatory = $true)][string]$RootDirectory,
        [Parameter(Mandatory = $true)][string]$DescendantDirectory
    )

    $root = [System.IO.Path]::GetFullPath($RootDirectory).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $descendant = [System.IO.Path]::GetFullPath($DescendantDirectory).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $relative = [System.IO.Path]::GetRelativePath($root, $descendant)
    if (
        [System.IO.Path]::IsPathRooted($relative) -or
        $relative -eq ".." -or
        $relative.StartsWith("..$([System.IO.Path]::DirectorySeparatorChar)", [System.StringComparison]::Ordinal)
    ) {
        throw "Directory is outside the required calibration root: $descendant"
    }

    $paths = [System.Collections.Generic.List[string]]::new()
    $paths.Add($root)
    $current = $root
    if ($relative -ne ".") {
        foreach ($segment in $relative.Split([System.IO.Path]::DirectorySeparatorChar)) {
            $current = Join-Path $current $segment
            $paths.Add($current)
        }
    }
    foreach ($path in $paths) {
        if (-not (Test-Path -LiteralPath $path -PathType Container)) {
            throw "Required calibration directory is missing: $path"
        }
        $item = Get-Item -LiteralPath $path -Force
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Required calibration directory is a forbidden reparse point: $path"
        }
    }
}

function Assert-Am1DirectSiblingPaths {
    param(
        [Parameter(Mandatory = $true)][string]$ActiveDirectory,
        [Parameter(Mandatory = $true)][string]$CandidatePath,
        [Parameter(Mandatory = $true)][string]$WithdrawalPath,
        [Parameter(Mandatory = $true)][string]$RunId,
        [Parameter()][switch]$RequireCandidateExists
    )

    $active = [System.IO.Path]::GetFullPath($ActiveDirectory)
    $candidate = [System.IO.Path]::GetFullPath($CandidatePath)
    $withdrawal = [System.IO.Path]::GetFullPath($WithdrawalPath)
    if (-not (Test-Path -LiteralPath $active -PathType Container)) {
        throw "Active calibration directory is missing: $active"
    }
    $activeItem = Get-Item -LiteralPath $active -Force
    if (($activeItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Active calibration directory is a forbidden reparse point: $active"
    }
    $parent = [System.IO.Directory]::GetParent($active)
    if ($null -eq $parent) {
        throw "Active calibration directory has no parent: $active"
    }
    $parentItem = Get-Item -LiteralPath $parent.FullName -Force
    if (($parentItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Active calibration parent is a forbidden reparse point: $($parent.FullName)"
    }
    foreach ($entry in @(
        [pscustomobject]@{ label = "candidate"; path = $candidate; expected = ".am1-candidate-$RunId" }
        [pscustomobject]@{ label = "withdrawal"; path = $withdrawal; expected = ".am1-withdrawn-$RunId" }
    )) {
        $entryParent = [System.IO.Directory]::GetParent($entry.path)
        if ($null -eq $entryParent -or -not (Test-Am1PathEqual -Left $entryParent.FullName -Right $parent.FullName)) {
            throw "AM1 $($entry.label) path must be a direct sibling of the active directory"
        }
        if ([System.IO.Path]::GetFileName($entry.path) -cne $entry.expected) {
            throw "AM1 $($entry.label) path has an unexpected leaf name"
        }
        $exists = Test-Path -LiteralPath $entry.path
        if ($entry.label -ceq "candidate" -and $RequireCandidateExists) {
            if (-not $exists) {
                throw "AM1 candidate path must exist before promotion: $($entry.path)"
            }
            $candidateItem = Get-Item -LiteralPath $entry.path -Force
            if (-not $candidateItem.PSIsContainer -or ($candidateItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "AM1 candidate path must be an ordinary directory: $($entry.path)"
            }
        }
        elseif ($exists) {
            throw "AM1 $($entry.label) path must not already exist: $($entry.path)"
        }
        if (
            [System.IO.Path]::GetPathRoot($entry.path) -cne [System.IO.Path]::GetPathRoot($active)
        ) {
            throw "AM1 $($entry.label) path must be on the active directory volume"
        }
    }
    if (
        (Test-Am1PathEqual -Left $candidate -Right $withdrawal) -or
        (Test-Am1PathEqual -Left $candidate -Right $active) -or
        (Test-Am1PathEqual -Left $withdrawal -Right $active)
    ) {
        throw "Active, candidate, and withdrawal paths must be distinct"
    }
}

function Assert-Am1PairHashesMatch {
    param(
        [Parameter(Mandatory = $true)]$ObservedPair,
        [Parameter(Mandatory = $true)]$ExpectedPair,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if ($ObservedPair.classification -cne "VALID_COMPLETE_PAIR") {
        throw "$Label pair is invalid: $($ObservedPair.failure_reason)"
    }
    foreach ($side in @("left", "right")) {
        if (
            $ObservedPair.$side.sha256 -cne $ExpectedPair.$side.sha256 -or
            [int64]$ObservedPair.$side.size -ne [int64]$ExpectedPair.$side.size
        ) {
            throw "$Label hash mismatch for $side calibration"
        }
    }
}

function New-Am1PromotionCandidate {
    param(
        [Parameter(Mandatory = $true)][string]$ActiveDirectory,
        [Parameter(Mandatory = $true)][string]$CandidatePath,
        [Parameter(Mandatory = $true)][object[]]$ExpectedActiveSnapshot,
        [Parameter(Mandatory = $true)][string]$StagingLeaf,
        [Parameter(Mandatory = $true)][string]$LeaderIdValue,
        [Parameter()][scriptblock]$CopyFileInvoker
    )

    Assert-Am1SnapshotMatches -ExpectedSnapshot $ExpectedActiveSnapshot -DirectoryPath $ActiveDirectory
    if (Test-Path -LiteralPath $CandidatePath) {
        throw "Promotion candidate already exists: $CandidatePath"
    }
    [System.IO.Directory]::CreateDirectory($CandidatePath) | Out-Null
    if ($null -eq $CopyFileInvoker) {
        $CopyFileInvoker = ${function:Copy-Am1RegularFile}
    }
    foreach ($entry in $ExpectedActiveSnapshot) {
        $source = Join-Path $ActiveDirectory $entry.relative_path
        $destination = Join-Path $CandidatePath $entry.relative_path
        & $CopyFileInvoker $source $destination | Out-Null
    }

    $stagedPair = Get-Am1CalibrationPairStatus -DirectoryPath $StagingLeaf -LeaderIdValue $LeaderIdValue
    if ($stagedPair.classification -cne "VALID_COMPLETE_PAIR") {
        throw "Staged calibration pair is invalid: $($stagedPair.failure_reason)"
    }
    foreach ($side in @("left", "right")) {
        $destination = Join-Path $CandidatePath "${LeaderIdValue}_${side}.json"
        [System.IO.File]::Copy([string]$stagedPair.$side.path, $destination, $true)
    }

    $candidatePair = Get-Am1CalibrationPairStatus -DirectoryPath $CandidatePath -LeaderIdValue $LeaderIdValue
    Assert-Am1PairHashesMatch -ObservedPair $candidatePair -ExpectedPair $stagedPair -Label "Candidate"
    $expectedCandidateSnapshot = foreach ($entry in $ExpectedActiveSnapshot) {
        $side = if ($entry.relative_path -ceq "${LeaderIdValue}_left.json") {
            "left"
        }
        elseif ($entry.relative_path -ceq "${LeaderIdValue}_right.json") {
            "right"
        }
        else {
            $null
        }
        if ($null -eq $side) {
            [pscustomobject][ordered]@{
                relative_path = $entry.relative_path
                size          = [int64]$entry.size
                sha256        = $entry.sha256
            }
        }
        else {
            [pscustomobject][ordered]@{
                relative_path = $entry.relative_path
                size          = [int64]$stagedPair.$side.size
                sha256        = $stagedPair.$side.sha256
            }
        }
    }
    Assert-Am1SnapshotMatches -ExpectedSnapshot @($expectedCandidateSnapshot) -DirectoryPath $CandidatePath
    return [pscustomobject][ordered]@{
        candidate_path    = [System.IO.Path]::GetFullPath($CandidatePath)
        expected_snapshot = @($expectedCandidateSnapshot)
        staged_pair       = $stagedPair
    }
}

function Move-Am1Directory {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$Operation
    )

    $null = $Operation
    [System.IO.Directory]::Move($Source, $Destination)
}

function Remove-Am1Directory {
    param([Parameter(Mandatory = $true)][string]$Path)

    [System.IO.Directory]::Delete($Path, $true)
}

function Test-Am1PathExists {
    param([Parameter(Mandatory = $true)][string]$Path)

    return Test-Path -LiteralPath $Path
}

function Invoke-Am1DirectoryPromotion {
    param(
        [Parameter(Mandatory = $true)][string]$ActiveDirectory,
        [Parameter(Mandatory = $true)][string]$CandidatePath,
        [Parameter(Mandatory = $true)][string]$WithdrawalPath,
        [Parameter(Mandatory = $true)][string]$RunId,
        [Parameter(Mandatory = $true)][object[]]$ExpectedOldSnapshot,
        [Parameter(Mandatory = $true)][object[]]$ExpectedNewSnapshot,
        [Parameter(Mandatory = $true)]$ExpectedStagedPair,
        [Parameter(Mandatory = $true)]$BackupFacts,
        [Parameter()][scriptblock]$MoveDirectoryInvoker,
        [Parameter()][scriptblock]$RemoveDirectoryInvoker,
        [Parameter()][scriptblock]$RollbackPathExistsInvoker,
        [Parameter()][scriptblock]$AfterSecondMoveHook
    )

    if ($null -eq $MoveDirectoryInvoker) {
        $MoveDirectoryInvoker = ${function:Move-Am1Directory}
    }
    if ($null -eq $RemoveDirectoryInvoker) {
        $RemoveDirectoryInvoker = ${function:Remove-Am1Directory}
    }
    if ($null -eq $RollbackPathExistsInvoker) {
        $RollbackPathExistsInvoker = ${function:Test-Am1PathExists}
    }
    Assert-Am1DirectSiblingPaths -ActiveDirectory $ActiveDirectory -CandidatePath $CandidatePath `
        -WithdrawalPath $WithdrawalPath -RunId $RunId -RequireCandidateExists
    Assert-Am1SnapshotMatches -ExpectedSnapshot $ExpectedOldSnapshot -DirectoryPath $ActiveDirectory
    Assert-Am1SnapshotMatches -ExpectedSnapshot $ExpectedNewSnapshot -DirectoryPath $CandidatePath
    $backupPair = Get-Am1CalibrationPairStatus -DirectoryPath $BackupFacts.backup_directory `
        -LeaderIdValue "so101_leader_bi"
    Assert-Am1PairHashesMatch -ObservedPair $backupPair -ExpectedPair $BackupFacts -Label "Persistent backup"

    [Console]::Out.WriteLine("ACTIVE_DIRECTORY=$ActiveDirectory")
    [Console]::Out.WriteLine("CANDIDATE_DIRECTORY=$CandidatePath")
    [Console]::Out.WriteLine("WITHDRAWAL_DIRECTORY=$WithdrawalPath")
    [Console]::Out.WriteLine("PAIR_BACKUP=$($BackupFacts.backup_directory)")
    $quotedWithdrawalPath = $WithdrawalPath.Replace("'", "''")
    [Console]::Out.WriteLine(
        "FAIL_CLOSED_RECOVERY=Rename-Item -LiteralPath '$quotedWithdrawalPath' -NewName 'so_leader'"
    )

    try {
        & $MoveDirectoryInvoker $ActiveDirectory $WithdrawalPath "withdraw-active" | Out-Null
        & $MoveDirectoryInvoker $CandidatePath $ActiveDirectory "promote-candidate" | Out-Null
        if ($null -ne $AfterSecondMoveHook) {
            & $AfterSecondMoveHook ([pscustomobject]@{
                active = $ActiveDirectory
                candidate = $CandidatePath
                withdrawal = $WithdrawalPath
            }) | Out-Null
        }
        Assert-Am1SnapshotMatches -ExpectedSnapshot $ExpectedNewSnapshot -DirectoryPath $ActiveDirectory
        $activePair = Get-Am1CalibrationPairStatus -DirectoryPath $ActiveDirectory -LeaderIdValue "so101_leader_bi"
        Assert-Am1PairHashesMatch -ObservedPair $activePair -ExpectedPair $ExpectedStagedPair -Label "Final active"
        $backupPair = Get-Am1CalibrationPairStatus -DirectoryPath $BackupFacts.backup_directory `
            -LeaderIdValue "so101_leader_bi"
        Assert-Am1PairHashesMatch -ObservedPair $backupPair -ExpectedPair $BackupFacts -Label "Persistent backup"
    }
    catch {
        $primary = $_.Exception
        $rollbackFailures = [System.Collections.Generic.List[string]]::new()
        $firstState = $null
        try {
            $firstState = [pscustomobject]@{
                active     = [bool](& $RollbackPathExistsInvoker $ActiveDirectory)
                candidate  = [bool](& $RollbackPathExistsInvoker $CandidatePath)
                withdrawal = [bool](& $RollbackPathExistsInvoker $WithdrawalPath)
            }
        }
        catch {
            $rollbackFailures.Add("Rollback state inspection failed: $($_.Exception.Message)")
        }
        if ($null -ne $firstState -and $firstState.active -and -not $firstState.candidate -and $firstState.withdrawal) {
            try {
                & $MoveDirectoryInvoker $ActiveDirectory $CandidatePath "return-promoted-active" | Out-Null
            }
            catch {
                $rollbackFailures.Add("Return promoted active failed: $($_.Exception.Message)")
            }
        }
        $secondState = $null
        try {
            $secondState = [pscustomobject]@{
                active     = [bool](& $RollbackPathExistsInvoker $ActiveDirectory)
                withdrawal = [bool](& $RollbackPathExistsInvoker $WithdrawalPath)
            }
        }
        catch {
            $rollbackFailures.Add("Rollback state inspection failed: $($_.Exception.Message)")
        }
        if ($null -ne $secondState -and -not $secondState.active -and $secondState.withdrawal) {
            try {
                & $MoveDirectoryInvoker $WithdrawalPath $ActiveDirectory "restore-withdrawal" | Out-Null
            }
            catch {
                $rollbackFailures.Add("Restore withdrawal failed: $($_.Exception.Message)")
            }
        }
        try {
            Assert-Am1SnapshotMatches -ExpectedSnapshot $ExpectedOldSnapshot -DirectoryPath $ActiveDirectory
        }
        catch {
            $rollbackFailures.Add("Restored active verification failed: $($_.Exception.Message)")
        }
        if ($rollbackFailures.Count -gt 0) {
            $primary.Data["AM1_ROLLBACK_FAILURES"] = [string[]]@($rollbackFailures)
        }
        throw $primary
    }

    try {
        Assert-Am1SnapshotMatches -ExpectedSnapshot $ExpectedOldSnapshot -DirectoryPath $WithdrawalPath
        & $RemoveDirectoryInvoker $WithdrawalPath | Out-Null
    }
    catch {
        $cleanupPrimary = $_.Exception
        $cleanupPrimary.Data["AM1_PROMOTED_VERIFIED_PAIR"] = $activePair
        $cleanupPrimary.Data["AM1_WITHDRAWAL_CLEANUP_STATE"] = "FAILED_OR_PARTIAL"
        throw $cleanupPrimary
    }
    return $activePair
}

function Start-Am1CalibrationTranscript {
    param([Parameter(Mandatory = $true)][string]$Path)

    $encoding = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText(
        $Path,
        "AM1_CALIBRATION_TRANSCRIPT_BEGIN$([System.Environment]::NewLine)",
        $encoding
    )
}

function Stop-Am1CalibrationTranscript {
    param([Parameter(Mandatory = $true)][string]$Path)

    $encoding = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::AppendAllText(
        $Path,
        "AM1_CALIBRATION_TRANSCRIPT_END$([System.Environment]::NewLine)",
        $encoding
    )
}

function Write-Am1CalibrationTranscriptLine {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Line
    )

    $encoding = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::AppendAllText($Path, "$Line$([System.Environment]::NewLine)", $encoding)
}

function Invoke-Am1InteractiveCalibrationCommand {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Command,
        [Parameter(Mandatory = $true)][string]$StagingLeaf,
        [Parameter(Mandatory = $true)][ref]$Launched,
        [Parameter(Mandatory = $true)][ref]$ExitCode,
        [Parameter()][scriptblock]$ProcessFactory,
        [Parameter()][scriptblock]$ProcessWaiter
    )

    $null = $StagingLeaf
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = [string]$Command.executable
    $startInfo.WorkingDirectory = [string]$Command.working_directory
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardInput = $false
    $startInfo.RedirectStandardOutput = $false
    $startInfo.RedirectStandardError = $false
    $transcriptProperty = $Command.PSObject.Properties["transcript_path"]
    if ($null -ne $transcriptProperty) {
        $startInfo.Environment["AM1_CALIBRATION_TRANSCRIPT_PATH"] = [string]$transcriptProperty.Value
    }
    foreach ($argument in @($Command.arguments)) {
        $startInfo.ArgumentList.Add([string]$argument)
    }
    if ($null -eq $ProcessFactory) {
        $ProcessFactory = {
            param($ChildStartInfo)
            $child = [System.Diagnostics.Process]::new()
            $child.StartInfo = $ChildStartInfo
            return $child
        }
    }
    if ($null -eq $ProcessWaiter) {
        $ProcessWaiter = {
            param($ChildProcess)
            Wait-Process -Id $ChildProcess.Id -ErrorAction Stop
        }
    }

    $process = $null
    $started = $false
    $primary = $null
    $cleanupFailures = [System.Collections.Generic.List[string]]::new()
    try {
        $process = & $ProcessFactory $startInfo
        if ($null -eq $process) {
            throw "Interactive calibration process factory returned no process"
        }
        if (-not $process.Start()) {
            throw "Interactive calibration process did not start"
        }
        $started = $true
        $Launched.Value = $true
        & $ProcessWaiter $process | Out-Null
        if (-not $process.HasExited) {
            throw "Interactive calibration process waiter returned before the child exited"
        }
        $ExitCode.Value = [int]$process.ExitCode
    }
    catch {
        $primary = $_
    }
    finally {
        if ($null -ne $process -and $started) {
            try {
                if (-not $process.HasExited) {
                    $process.Kill($true)
                    if (-not $process.WaitForExit(5000)) {
                        throw "Interactive calibration child did not exit after process-tree termination"
                    }
                }
            }
            catch {
                $cleanupFailures.Add("Interactive child termination failed: $($_.Exception.Message)")
            }
        }
        if ($null -ne $process) {
            try {
                $process.Dispose()
            }
            catch {
                $cleanupFailures.Add("Interactive child disposal failed: $($_.Exception.Message)")
            }
        }
    }
    if ($null -ne $primary) {
        if ($cleanupFailures.Count -gt 0) {
            $primary.Exception.Data["AM1_CHILD_CLEANUP_FAILURES"] = [string[]]@($cleanupFailures)
        }
        $PSCmdlet.ThrowTerminatingError($primary)
    }
    if ($cleanupFailures.Count -gt 0) {
        throw [System.InvalidOperationException]::new([string]$cleanupFailures[0])
    }
}

function Invoke-Am1NativeCalibration {
    param(
        [Parameter(Mandatory = $true)]$Command,
        [Parameter(Mandatory = $true)][string]$StagingLeaf,
        [Parameter(Mandatory = $true)][string]$TranscriptPath,
        [Parameter()][scriptblock]$StartTranscriptInvoker,
        [Parameter()][scriptblock]$CommandInvoker,
        [Parameter()][scriptblock]$StopTranscriptInvoker
    )

    if (Test-Path -LiteralPath $TranscriptPath) {
        throw "Calibration transcript path already exists: $TranscriptPath"
    }
    if ($null -eq $StartTranscriptInvoker) {
        $StartTranscriptInvoker = ${function:Start-Am1CalibrationTranscript}
    }
    if ($null -eq $CommandInvoker) {
        $CommandInvoker = ${function:Invoke-Am1InteractiveCalibrationCommand}
    }
    if ($null -eq $StopTranscriptInvoker) {
        $StopTranscriptInvoker = ${function:Stop-Am1CalibrationTranscript}
    }

    $launched = $false
    $exitCode = $null
    $interrupted = $false
    $failureReason = $null
    $transcriptStarted = $false
    $transcriptStopped = $false
    $cleanupFailures = [System.Collections.Generic.List[string]]::new()
    try {
        & $StartTranscriptInvoker $TranscriptPath | Out-Null
        $transcriptStarted = $true
        $commandLine = "CALIBRATION_COMMAND=$($Command.executable) $($Command.arguments -join ' ')"
        [Console]::Out.WriteLine($commandLine)
        Write-Am1CalibrationTranscriptLine -Path $TranscriptPath -Line $commandLine
        $interactiveCommand = $Command.PSObject.Copy()
        $interactiveCommand | Add-Member -NotePropertyName "transcript_path" `
            -NotePropertyValue $TranscriptPath -Force
        try {
            & $CommandInvoker $interactiveCommand $StagingLeaf ([ref]$launched) ([ref]$exitCode) | Out-Null
        }
        catch {
            $failureReason = $_.Exception.Message
            $currentException = $_.Exception
            while ($null -ne $currentException) {
                $failureReason = $currentException.Message
                if ($currentException.Data.Contains("AM1_CHILD_CLEANUP_FAILURES")) {
                    foreach ($failure in @($currentException.Data["AM1_CHILD_CLEANUP_FAILURES"])) {
                        $cleanupFailures.Add([string]$failure)
                    }
                }
                if (
                    $currentException -is [System.OperationCanceledException] -or
                    $currentException -is [System.Management.Automation.PipelineStoppedException]
                ) {
                    $interrupted = $true
                    $exitCode = 130
                }
                $currentException = $currentException.InnerException
            }
        }
        if ($null -ne $exitCode) {
            $exitLine = "CALIBRATION_EXIT_CODE=$exitCode"
            [Console]::Out.WriteLine($exitLine)
            Write-Am1CalibrationTranscriptLine -Path $TranscriptPath -Line $exitLine
        }
    }
    catch {
        $failureReason = $_.Exception.Message
    }
    finally {
        if ($transcriptStarted) {
            try {
                & $StopTranscriptInvoker $TranscriptPath | Out-Null
                $transcriptStopped = $true
            }
            catch {
                $cleanupFailures.Add("Transcript stop failed: $($_.Exception.Message)")
            }
        }
    }

    return [pscustomobject][ordered]@{
        launched           = [bool]$launched
        exit_code          = $exitCode
        interrupted        = [bool]$interrupted
        failure_reason     = $failureReason
        transcript_started = [bool]$transcriptStarted
        transcript_stopped = [bool]$transcriptStopped
        cleanup_failures   = @($cleanupFailures)
    }
}

function New-Am1RunDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$CalibrationRoot,
        [Parameter(Mandatory = $true)][string]$RunId
    )

    if ($RunId -notmatch "\A[A-Za-z0-9-]+\z") {
        throw "Calibration run ID contains forbidden characters"
    }
    $calibration = [System.IO.Path]::GetFullPath($CalibrationRoot)
    $calibrationParent = [System.IO.Directory]::GetParent($calibration)
    if ($null -eq $calibrationParent) {
        throw "Calibration root has no parent: $calibration"
    }
    $runsRoot = Join-Path $calibrationParent.FullName "am1-leader-calibration-runs"
    if (Test-Path -LiteralPath $runsRoot) {
        $runsItem = Get-Item -LiteralPath $runsRoot -Force
        if (-not $runsItem.PSIsContainer -or ($runsItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Calibration run root must be an ordinary directory: $runsRoot"
        }
    }
    else {
        [System.IO.Directory]::CreateDirectory($runsRoot) | Out-Null
    }
    $runDirectory = Join-Path $runsRoot $RunId
    if (Test-Path -LiteralPath $runDirectory) {
        throw "Calibration run directory already exists: $runDirectory"
    }
    [System.IO.Directory]::CreateDirectory($runDirectory) | Out-Null
    return $runDirectory
}

function New-Am1CalibrationFailureOutcome {
    param(
        [Parameter(Mandatory = $true)][string]$PrimaryReason,
        [Parameter(Mandatory = $true)][System.Collections.IDictionary]$Facts,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$SecondaryFailures
    )

    $left = $null
    $right = $null
    if ($null -ne $Facts.active_pair) {
        $left = $Facts.active_pair.left
        $right = $Facts.active_pair.right
    }
    return [pscustomobject][ordered]@{
        success             = $false
        primary_reason      = $PrimaryReason
        secondary_failures  = @($SecondaryFailures)
        launched            = [bool]$Facts.launched
        exit_code           = $Facts.exit_code
        interrupted         = [bool]$Facts.interrupted
        active_unchanged    = $Facts.active_unchanged
        active_pair_state   = $Facts.active_pair_state
        withdrawal_cleanup_state = $Facts.withdrawal_cleanup_state
        run_directory       = $Facts.run_directory
        backup_directory    = $Facts.backup_directory
        staging_leaf        = $Facts.staging_leaf
        transcript_path     = $Facts.transcript_path
        candidate_path      = $Facts.candidate_path
        withdrawal_path     = $Facts.withdrawal_path
        left                = $left
        right               = $right
    }
}

function New-Am1CalibrationSuccessOutcome {
    param(
        [Parameter(Mandatory = $true)][System.Collections.IDictionary]$Facts,
        [Parameter(Mandatory = $true)]$ActivePair
    )

    return [pscustomobject][ordered]@{
        success             = $true
        primary_reason      = $null
        secondary_failures  = @()
        launched            = [bool]$Facts.launched
        exit_code           = $Facts.exit_code
        interrupted         = $false
        active_unchanged    = $false
        active_pair_state   = "PROMOTED_VERIFIED"
        withdrawal_cleanup_state = "COMPLETE"
        run_directory       = $Facts.run_directory
        backup_directory    = $Facts.backup_directory
        staging_leaf        = $Facts.staging_leaf
        transcript_path     = $Facts.transcript_path
        candidate_path      = $Facts.candidate_path
        withdrawal_path     = $Facts.withdrawal_path
        left                = $ActivePair.left
        right               = $ActivePair.right
    }
}

function Invoke-Am1CalibrationAttempt {
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$LeftPortValue,
        [Parameter(Mandatory = $true)][string]$RightPortValue,
        [Parameter(Mandatory = $true)][string]$LeaderIdValue,
        [Parameter(Mandatory = $true)][string]$ArmProfileValue,
        [Parameter(Mandatory = $true)][string]$Confirmation,
        [Parameter(Mandatory = $true)][string]$RunId,
        [Parameter()][scriptblock]$PythonProbeInvoker,
        [Parameter()][scriptblock]$GitInvoker,
        [Parameter()][scriptblock]$CopyFileInvoker,
        [Parameter()][scriptblock]$StartTranscriptInvoker,
        [Parameter()][scriptblock]$NativeCommandInvoker,
        [Parameter()][scriptblock]$StopTranscriptInvoker,
        [Parameter()][scriptblock]$MoveDirectoryInvoker,
        [Parameter()][scriptblock]$RemoveDirectoryInvoker,
        [Parameter()][scriptblock]$RollbackPathExistsInvoker,
        [Parameter()][scriptblock]$BeforePromotionHook,
        [Parameter()][scriptblock]$AfterSecondMoveHook
    )

    $facts = [ordered]@{
        launched         = $false
        exit_code        = $null
        interrupted      = $false
        active_unchanged = $null
        active_pair_state = $null
        withdrawal_cleanup_state = $null
        active_pair       = $null
        run_directory    = $null
        backup_directory = $null
        staging_leaf      = $null
        transcript_path   = $null
        candidate_path    = $null
        withdrawal_path   = $null
    }
    $secondaryFailures = [System.Collections.Generic.List[string]]::new()
    $activeDirectory = $null
    $activeSnapshot = $null
    try {
        Assert-Am1FixedIdentity -LeftPortValue $LeftPortValue -RightPortValue $RightPortValue `
            -LeaderIdValue $LeaderIdValue -ArmProfileValue $ArmProfileValue `
            -Confirmation $Confirmation -RequireCalibrationConfirmation
        $statusFacts = Get-Am1LeaderCalibrationStatus -RepositoryRoot $RepositoryRoot -PythonPath $PythonPath `
            -LeftPortValue $LeftPortValue -RightPortValue $RightPortValue `
            -LeaderIdValue $LeaderIdValue -ArmProfileValue $ArmProfileValue `
            -PythonProbeInvoker $PythonProbeInvoker -GitInvoker $GitInvoker
        if ($statusFacts.pair.classification -cne "VALID_COMPLETE_PAIR") {
            throw "Active calibration pair is invalid: $($statusFacts.pair.failure_reason)"
        }
        $activeDirectory = [string]$statusFacts.active_directory
        Assert-Am1OrdinaryDirectoryChain -RootDirectory $statusFacts.provenance.calibration_root `
            -DescendantDirectory $activeDirectory
        $activeSnapshot = @(Get-Am1RegularFileSnapshot -DirectoryPath $activeDirectory)

        $runDirectory = New-Am1RunDirectory -CalibrationRoot $statusFacts.provenance.calibration_root -RunId $RunId
        $facts.run_directory = $runDirectory
        $backupDirectory = Join-Path $runDirectory "backup-active-pair"
        $backup = New-Am1PairBackup -ActiveDirectory $activeDirectory -BackupDirectory $backupDirectory `
            -LeaderIdValue $LeaderIdValue -ActivePair $statusFacts.pair `
            -ExpectedActiveSnapshot $activeSnapshot -CopyFileInvoker $CopyFileInvoker
        $facts.backup_directory = $backup.backup_directory

        $stagingLeaf = Join-Path $runDirectory "staged-calibration\teleoperators\so_leader"
        [System.IO.Directory]::CreateDirectory($stagingLeaf) | Out-Null
        $facts.staging_leaf = $stagingLeaf
        $transcriptPath = Join-Path $runDirectory "calibration-transcript.txt"
        $facts.transcript_path = $transcriptPath
        $command = New-Am1NativeCalibrationCommand -RepositoryRoot $RepositoryRoot `
            -PythonPath $PythonPath -StagingLeaf $stagingLeaf
        if (-not (Test-Path -LiteralPath $command.arguments[0] -PathType Leaf)) {
            throw "Aloha Mini calibration script is missing: $($command.arguments[0])"
        }

        $native = Invoke-Am1NativeCalibration -Command $command -StagingLeaf $stagingLeaf `
            -TranscriptPath $transcriptPath -StartTranscriptInvoker $StartTranscriptInvoker `
            -CommandInvoker $NativeCommandInvoker -StopTranscriptInvoker $StopTranscriptInvoker
        $facts.launched = $native.launched
        $facts.exit_code = $native.exit_code
        $facts.interrupted = $native.interrupted
        $nativePrimaryReason = $null
        if ($null -ne $native.failure_reason) {
            $nativePrimaryReason = [string]$native.failure_reason
        }
        elseif (-not $native.launched) {
            $nativePrimaryReason = "Native calibration command did not launch"
        }
        elseif ($null -eq $native.exit_code -or [int]$native.exit_code -ne 0) {
            $nativePrimaryReason = "Native calibration exited with exit code $($native.exit_code)"
        }
        if ($null -ne $nativePrimaryReason) {
            foreach ($failure in $native.cleanup_failures) {
                $secondaryFailures.Add([string]$failure)
            }
            throw [System.InvalidOperationException]::new($nativePrimaryReason)
        }
        if ($native.cleanup_failures.Count -gt 0) {
            for ($index = 1; $index -lt $native.cleanup_failures.Count; $index += 1) {
                $secondaryFailures.Add([string]$native.cleanup_failures[$index])
            }
            throw [System.InvalidOperationException]::new([string]$native.cleanup_failures[0])
        }

        $stagedPair = Get-Am1CalibrationPairStatus -DirectoryPath $stagingLeaf -LeaderIdValue $LeaderIdValue
        if ($stagedPair.classification -cne "VALID_COMPLETE_PAIR") {
            throw "Staged calibration pair is invalid: $($stagedPair.failure_reason)"
        }
        Assert-Am1SnapshotMatches -ExpectedSnapshot $activeSnapshot -DirectoryPath $activeDirectory
        if ($null -ne $BeforePromotionHook) {
            & $BeforePromotionHook ([pscustomobject]@{
                active = $activeDirectory
                staging = $stagingLeaf
                run = $runDirectory
            }) | Out-Null
        }
        Assert-Am1SnapshotMatches -ExpectedSnapshot $activeSnapshot -DirectoryPath $activeDirectory

        $activeParent = [System.IO.Directory]::GetParent($activeDirectory)
        if ($null -eq $activeParent) {
            throw "Active calibration directory has no parent"
        }
        $candidatePath = Join-Path $activeParent.FullName ".am1-candidate-$RunId"
        $withdrawalPath = Join-Path $activeParent.FullName ".am1-withdrawn-$RunId"
        $facts.candidate_path = $candidatePath
        $facts.withdrawal_path = $withdrawalPath
        Assert-Am1DirectSiblingPaths -ActiveDirectory $activeDirectory -CandidatePath $candidatePath `
            -WithdrawalPath $withdrawalPath -RunId $RunId
        $candidate = New-Am1PromotionCandidate -ActiveDirectory $activeDirectory `
            -CandidatePath $candidatePath -ExpectedActiveSnapshot $activeSnapshot `
            -StagingLeaf $stagingLeaf -LeaderIdValue $LeaderIdValue -CopyFileInvoker $CopyFileInvoker
        Assert-Am1SnapshotMatches -ExpectedSnapshot $activeSnapshot -DirectoryPath $activeDirectory
        $finalPair = Invoke-Am1DirectoryPromotion -ActiveDirectory $activeDirectory `
            -CandidatePath $candidatePath -WithdrawalPath $withdrawalPath -RunId $RunId `
            -ExpectedOldSnapshot $activeSnapshot -ExpectedNewSnapshot $candidate.expected_snapshot `
            -ExpectedStagedPair $candidate.staged_pair -BackupFacts $backup `
            -MoveDirectoryInvoker $MoveDirectoryInvoker -RemoveDirectoryInvoker $RemoveDirectoryInvoker `
            -RollbackPathExistsInvoker $RollbackPathExistsInvoker `
            -AfterSecondMoveHook $AfterSecondMoveHook
        return New-Am1CalibrationSuccessOutcome -Facts $facts -ActivePair $finalPair
    }
    catch {
        $primaryReason = $_.Exception.Message
        if ($_.Exception.Data.Contains("AM1_PROMOTED_VERIFIED_PAIR")) {
            $facts.active_pair_state = "PROMOTED_VERIFIED"
            $facts.withdrawal_cleanup_state = [string]$_.Exception.Data["AM1_WITHDRAWAL_CLEANUP_STATE"]
            $facts.active_pair = $_.Exception.Data["AM1_PROMOTED_VERIFIED_PAIR"]
            $facts.active_unchanged = $false
        }
        if ($_.Exception.Data.Contains("AM1_ROLLBACK_FAILURES")) {
            foreach ($failure in @($_.Exception.Data["AM1_ROLLBACK_FAILURES"])) {
                $secondaryFailures.Add("Promotion rollback failed: $failure")
            }
        }
        if (
            $null -eq $facts.active_pair -and
            $null -ne $activeSnapshot -and
            $null -ne $activeDirectory
        ) {
            try {
                Assert-Am1SnapshotMatches -ExpectedSnapshot $activeSnapshot -DirectoryPath $activeDirectory
                $facts.active_unchanged = $true
            }
            catch {
                $facts.active_unchanged = $false
                if ($primaryReason -notlike "Active calibration tree changed*") {
                    $secondaryFailures.Add("Active tree verification failed: $($_.Exception.Message)")
                }
            }
        }
        return New-Am1CalibrationFailureOutcome -PrimaryReason $primaryReason -Facts $facts `
            -SecondaryFailures @($secondaryFailures)
    }
}

function Assert-Am1SuccessfulCalibrationOutcome {
    param([Parameter(Mandatory = $true)]$Outcome)

    if (-not [bool]$Outcome.launched) {
        throw "Refusing calibration PASS because the native command did not launch"
    }
    if ($null -eq $Outcome.exit_code -or [int]$Outcome.exit_code -ne 0) {
        throw "Refusing calibration PASS because the native exit code is not zero"
    }
    if ($Outcome.active_pair_state -cne "PROMOTED_VERIFIED") {
        throw "Refusing calibration PASS because the promoted active pair is not verified"
    }
    if ($Outcome.withdrawal_cleanup_state -cne "COMPLETE") {
        throw "Refusing calibration PASS because withdrawal cleanup is incomplete"
    }
    foreach ($field in @("run_directory", "backup_directory", "staging_leaf", "transcript_path")) {
        if ([string]::IsNullOrWhiteSpace([string]$Outcome.$field)) {
            throw "Refusing calibration PASS because evidence field $field is missing"
        }
    }
    foreach ($side in @("left", "right")) {
        if (
            $null -eq $Outcome.$side -or
            [string]::IsNullOrWhiteSpace([string]$Outcome.$side.path) -or
            [string]::IsNullOrWhiteSpace([string]$Outcome.$side.sha256)
        ) {
            throw "Refusing calibration PASS because verified $side active-pair facts are missing"
        }
    }
}

function Write-Am1CalibrationOutcome {
    param([Parameter(Mandatory = $true)]$Outcome)

    if ($null -ne $Outcome.run_directory) {
        [Console]::Out.WriteLine("RUN_DIRECTORY=$($Outcome.run_directory)")
    }
    if ($null -ne $Outcome.backup_directory) {
        [Console]::Out.WriteLine("PAIR_BACKUP=$($Outcome.backup_directory)")
    }
    if ($null -ne $Outcome.staging_leaf) {
        [Console]::Out.WriteLine("STAGED_EVIDENCE=$($Outcome.staging_leaf)")
    }
    if ($null -ne $Outcome.transcript_path) {
        [Console]::Out.WriteLine("TRANSCRIPT_PATH=$($Outcome.transcript_path)")
    }
    if (-not $Outcome.success) {
        if ($null -ne $Outcome.active_pair_state) {
            [Console]::Out.WriteLine("ACTIVE_PAIR_STATE=$($Outcome.active_pair_state)")
        }
        if ($null -ne $Outcome.left) {
            [Console]::Out.WriteLine("ACTIVE_LEFT_PATH=$($Outcome.left.path)")
            [Console]::Out.WriteLine("ACTIVE_LEFT_SHA256=$($Outcome.left.sha256)")
        }
        if ($null -ne $Outcome.right) {
            [Console]::Out.WriteLine("ACTIVE_RIGHT_PATH=$($Outcome.right.path)")
            [Console]::Out.WriteLine("ACTIVE_RIGHT_SHA256=$($Outcome.right.sha256)")
        }
        if ($null -ne $Outcome.withdrawal_cleanup_state) {
            [Console]::Out.WriteLine("WITHDRAWAL_CLEANUP_STATE=$($Outcome.withdrawal_cleanup_state)")
            [Console]::Out.WriteLine("WITHDRAWAL_PATH=$($Outcome.withdrawal_path)")
        }
        [Console]::Out.WriteLine("CALIBRATION_FAILURE_REASON=$($Outcome.primary_reason)")
        foreach ($failure in $Outcome.secondary_failures) {
            [Console]::Out.WriteLine("CALIBRATION_SECONDARY_FAILURE=$failure")
        }
        [Console]::Out.WriteLine("CALIBRATION_RESULT=FAIL")
        return
    }
    Assert-Am1SuccessfulCalibrationOutcome -Outcome $Outcome
    [Console]::Out.WriteLine("ACTIVE_LEFT_PATH=$($Outcome.left.path)")
    [Console]::Out.WriteLine("ACTIVE_RIGHT_PATH=$($Outcome.right.path)")
    [Console]::Out.WriteLine("ACTIVE_LEFT_SHA256=$($Outcome.left.sha256)")
    [Console]::Out.WriteLine("ACTIVE_RIGHT_SHA256=$($Outcome.right.sha256)")
    [Console]::Out.WriteLine(
        "NEXT_COMMAND=.\.venv\Scripts\python.exe .\examples\alohamini\teleoperate_bi.py --no_robot " +
        "--robot.robot_model alohamini1 --teleop.left_port COM8 --teleop.right_port COM7 " +
        "--teleop.id so101_leader_bi --teleop.arm_profile so-arm-5dof --require_calibration_match " +
        "--duration_s 30 --fps 5 --no_keyboard --no_rerun"
    )
    [Console]::Out.WriteLine("CALIBRATION_RESULT=PASS")
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
        [Parameter(Mandatory = $true)][string]$ArmProfileValue,
        [Parameter()][AllowNull()][string]$RunIdValue,
        [Parameter()][scriptblock]$CalibrationAttemptInvoker
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
        return 0
    }

    Assert-Am1FixedIdentity -LeftPortValue $LeftPortValue -RightPortValue $RightPortValue `
        -LeaderIdValue $LeaderIdValue -ArmProfileValue $ArmProfileValue `
        -Confirmation $Confirmation -RequireCalibrationConfirmation
    if ($null -eq $CalibrationAttemptInvoker) {
        $CalibrationAttemptInvoker = ${function:Invoke-Am1CalibrationAttempt}
    }
    if ([string]::IsNullOrWhiteSpace($RunIdValue)) {
        $RunIdValue = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss") + `
            "-" + [System.Guid]::NewGuid().ToString("N")
    }
    $outcome = & $CalibrationAttemptInvoker -RepositoryRoot $repository -PythonPath $python `
        -LeftPortValue $LeftPortValue -RightPortValue $RightPortValue `
        -LeaderIdValue $LeaderIdValue -ArmProfileValue $ArmProfileValue `
        -Confirmation $Confirmation -RunId $RunIdValue
    Write-Am1CalibrationOutcome -Outcome $outcome
    if ($outcome.success) {
        return 0
    }
    return 1
}

if ($MyInvocation.InvocationName -ne ".") {
    try {
        $mainExitCode = Invoke-Am1LeaderCalibrationMain -StatusMode ([bool]$Status) -CalibrateMode ([bool]$Calibrate) `
            -Confirmation $Confirm -LeftPortValue $LeftPort -RightPortValue $RightPort `
            -LeaderIdValue $LeaderId -ArmProfileValue $ArmProfile
        exit $mainExitCode
    }
    catch {
        [Console]::Error.WriteLine($_.Exception.Message)
        if ($Calibrate) {
            [Console]::Out.WriteLine("CALIBRATION_RESULT=FAIL")
        }
        exit 1
    }
}
