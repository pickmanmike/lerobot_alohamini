[CmdletBinding()]
param(
    [ValidateSet('Arms', 'Base', 'Lift', 'Local')]
    [string]$Mode,
    [string]$ConfigPath = (Join-Path $PSScriptRoot '..\config\am1.local.json'),
    [string]$DurationSeconds,
    [string]$LogPath,
    [string]$StopRequestPath,
    [switch]$Preflight,
    [switch]$PrintCommand
)

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

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:Am1WindowsInput = '30609a4597b8b6fca49bc1018024fd29dfb55127'
$script:Am1PiInput = 'ee3a6f5dd813be82780a6a9b1789966357542d2f'
$script:Am1LeftCalibrationSha256 = '34D06E15F6768A3290B85BBE3507D9B14A8CCED263A40C575E02010560E13FBE'
$script:Am1RightCalibrationSha256 = 'C5F04F97B2B4B371EF4C4292616E7BBCAAE3987805930DE46CAEB3C614D2950C'

function Assert-Am1ValidatedEnvelope {
    param(
        [Parameter(Mandatory)][psobject]$Config,
        [Parameter(Mandatory)][ValidateSet('Arms', 'Base', 'Lift', 'Local')][string]$Mode
    )

    $settings = $Config.arm_settings
    $settingsMatch =
        [double]$settings.client_fps -eq 10 -and
        [double]$settings.client_duration_s -eq 45 -and
        [double]$settings.startup_sync_duration_s -eq 120 -and
        [double]$settings.max_start_mismatch -eq 10 -and
        [double]$settings.host_max_relative_target -eq 20
    $localSettingsMatch = $Mode -ne 'Local' -or [double]$settings.local_startup_sync_duration_s -eq 30
    $hashesMatch =
        [string]$Config.leader_calibration_sha256.left -ceq $script:Am1LeftCalibrationSha256 -and
        [string]$Config.leader_calibration_sha256.right -ceq $script:Am1RightCalibrationSha256
    if (-not $settingsMatch -or -not $localSettingsMatch -or -not $hashesMatch) {
        throw 'Local config must retain the validated AM1 arm settings and calibration identities.'
    }
}

function ConvertTo-Am1LocalDurationSeconds {
    param([AllowNull()][object]$Value)

    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) {
        return 30
    }
    $text = [string]$Value
    if ($text -cnotmatch '^(?:[1-9]|[1-9][0-9]{1,2}|1[0-7][0-9]{2}|1800)$') {
        throw 'Local duration must be a whole number from 1 through 1800 seconds.'
    }
    return [int]::Parse($text, [System.Globalization.CultureInfo]::InvariantCulture)
}

function Resolve-Am1ConfiguredPath {
    param(
        [Parameter(Mandatory)]
        [string]$Value,
        [Parameter(Mandatory)]
        [string]$RepositoryRoot
    )

    if ([System.IO.Path]::IsPathRooted($Value)) {
        return [System.IO.Path]::GetFullPath($Value)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $RepositoryRoot $Value))
}

function ConvertTo-Am1CommandText {
    param(
        [Parameter(Mandatory)]
        [string]$Executable,
        [Parameter(Mandatory)]
        [string[]]$Arguments
    )

    $quoted = @($Executable) + $Arguments | ForEach-Object {
        $value = [string]$_
        if ($value -match '[\s"]') {
            '"' + $value.Replace('"', '\"') + '"'
        }
        else {
            $value
        }
    }
    return $quoted -join ' '
}

function New-Am1WindowsCommand {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [ValidateSet('Arms', 'Base', 'Lift', 'Local')]
        [string]$Mode,
        [Parameter(Mandatory)]
        [psobject]$Config,
        [Parameter(Mandatory)]
        [string]$RepositoryRoot,
        [string]$LeftPort,
        [string]$RightPort,
        [Nullable[int]]$LocalDurationSeconds,
        [string]$StopRequestPath
    )

    Assert-Am1ValidatedEnvelope -Config $Config -Mode $Mode
    $pythonPath = Resolve-Am1ConfiguredPath -Value ([string]$Config.windows_python_path) `
        -RepositoryRoot $RepositoryRoot
    $teleoperationPath = Join-Path $RepositoryRoot 'examples\alohamini\teleoperate_bi.py'

    if ($Mode -eq 'Base') {
        $arguments = [string[]]@(
            $teleoperationPath
            '--base_only'
            '--no_leader'
            '--start_paused'
            '--no_cameras'
            '--no_rerun'
            '--robot.robot_model'
            'alohamini1'
            '--robot.remote_ip'
            [string]$Config.pi_host
            '--fps'
            '10'
            '--duration_s'
            '30'
        )
    }
    elseif ($Mode -eq 'Lift') {
        $arguments = [string[]]@(
            $teleoperationPath
            '--lift_only'
            '--no_leader'
            '--start_paused'
            '--no_cameras'
            '--no_rerun'
            '--robot.robot_model'
            'alohamini1'
            '--robot.remote_ip'
            [string]$Config.pi_host
            '--fps'
            '10'
            '--duration_s'
            '30'
        )
    }
    else {
        if ($LeftPort -notmatch '^COM\d+$' -or $RightPort -notmatch '^COM\d+$' -or $LeftPort -eq $RightPort) {
            throw 'Arms and Local modes require two distinct uppercase runtime COM addresses.'
        }
        $settings = $Config.arm_settings
        $syncDuration = if ($Mode -eq 'Local') {
            [string]$settings.local_startup_sync_duration_s
        }
        else {
            [string]$settings.startup_sync_duration_s
        }
        $liveDuration = if ($Mode -eq 'Local') {
            if ($null -eq $LocalDurationSeconds) { '30' } else { [string]$LocalDurationSeconds }
        }
        else {
            [string]$settings.client_duration_s
        }
        $arguments = @(
            $teleoperationPath
            '--robot.remote_ip'
            [string]$Config.pi_host
            '--robot.robot_model'
            'alohamini1'
            '--teleop.left_port'
            $LeftPort
            '--teleop.right_port'
            $RightPort
            '--teleop.id'
            'so101_leader_bi'
            '--teleop.arm_profile'
            'so-arm-5dof'
            '--startup_mode'
            'sync'
            '--startup_sync_side'
            'both'
            '--startup_sync_duration_s'
            $syncDuration
            '--max_start_mismatch'
            [string]$settings.max_start_mismatch
            '--live_arm_scope'
            'both'
            '--fps'
            [string]$settings.client_fps
            '--duration_s'
            $liveDuration
            '--start_paused'
        )
        if ($Mode -eq 'Local') {
            $arguments += '--local_mode'
            if (-not [string]::IsNullOrWhiteSpace($StopRequestPath)) {
                if (-not [System.IO.Path]::IsPathRooted($StopRequestPath)) {
                    throw '-StopRequestPath must be an absolute path.'
                }
                $arguments += @('--external_stop_file', [System.IO.Path]::GetFullPath($StopRequestPath))
                $arguments += '--unified_session_enter_confirmations'
            }
        }
        else {
            if (-not [string]::IsNullOrWhiteSpace($StopRequestPath)) {
                throw '-StopRequestPath is available only for Local mode.'
            }
            $arguments += '--no_keyboard'
        }
        $arguments += @(
            '--no_cameras'
            '--profile_cadence'
            '--no_rerun'
        )
        $arguments = [string[]]$arguments
    }

    return [pscustomobject][ordered]@{
        executable        = $pythonPath
        arguments         = $arguments
        working_directory = [System.IO.Path]::GetFullPath($RepositoryRoot)
    }
}

function Invoke-Am1LoggedCommand {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Executable,
        [Parameter(Mandatory)]
        [string[]]$Arguments,
        [Parameter(Mandatory)]
        [string]$LogPath,
        [Nullable[int]]$DisplayDurationSeconds
    )

    $parent = Split-Path -Parent $LogPath
    if ($parent) {
        $null = New-Item -ItemType Directory -Path $parent -Force
    }
    # Keep durable logging independent of console rendering. The helper's
    # disposable viewer may block without back-pressuring the client or its
    # cleanup path.
    $logger = Join-Path $PSScriptRoot 'am1_logged_process.py'
    if (-not (Test-Path -LiteralPath $logger -PathType Leaf)) {
        throw "AM1 direct logger is missing: $logger"
    }
    $loggerArguments = [string[]]@($logger, '--log', $LogPath, '--', $Executable) + $Arguments
    if ($null -ne $DisplayDurationSeconds) {
        $loggerArguments = [string[]]@(
            $logger
            '--log'
            $LogPath
            '--duration-seconds'
            [string]$DisplayDurationSeconds
            '--'
            $Executable
        ) + $Arguments
    }
    $PSNativeCommandUseErrorActionPreference = $false
    & $Executable @loggerArguments
    return [int]$LASTEXITCODE
}

function Assert-Am1ReviewedWorktree {
    param([Parameter(Mandatory)][string]$RepositoryRoot)

    $status = @(& git -C $RepositoryRoot status --porcelain)
    if ($LASTEXITCODE -ne 0) {
        throw 'Unable to inspect the Windows worktree.'
    }
    if ($status.Count -ne 0) {
        throw 'Windows worktree is not clean.'
    }
    foreach ($inputCommit in @($script:Am1WindowsInput, $script:Am1PiInput)) {
        & git -C $RepositoryRoot merge-base --is-ancestor $inputCommit HEAD
        if ($LASTEXITCODE -ne 0) {
            throw "Reviewed AM1 input is not an ancestor of HEAD: $inputCommit"
        }
    }
}

function Assert-Am1ImportRoot {
    param(
        [Parameter(Mandatory)][string]$Executable,
        [Parameter(Mandatory)][string]$RepositoryRoot
    )

    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        throw "Configured Python executable is missing: $Executable"
    }
    $sourceRoot = (Resolve-Path -LiteralPath (Join-Path $RepositoryRoot 'src')).Path
    $expected = (Resolve-Path -LiteralPath (
        Join-Path $sourceRoot 'lerobot\robots\alohamini\config_alohamini.py'
    )).Path
    $previousPythonPath = $env:PYTHONPATH
    $previousNoBytecode = $env:PYTHONDONTWRITEBYTECODE
    try {
        $env:PYTHONPATH = $sourceRoot
        $env:PYTHONDONTWRITEBYTECODE = '1'
        $actual = (& $Executable -c `
            'from pathlib import Path; import lerobot.robots.alohamini.config_alohamini as m; print(Path(m.__file__).resolve())').Trim()
        if ($LASTEXITCODE -ne 0 -or $actual -ne $expected) {
            throw "Wrong Python import root: $actual"
        }
    }
    finally {
        $env:PYTHONPATH = $previousPythonPath
        $env:PYTHONDONTWRITEBYTECODE = $previousNoBytecode
    }
}

function Get-Am1RuntimeLeaderPorts {
    param(
        [Parameter(Mandatory)][psobject]$Config,
        [Parameter(Mandatory)][string]$RepositoryRoot
    )

    $mapPath = [string]$Config.leader_pnp_map_path
    if (-not (Test-Path -LiteralPath $mapPath -PathType Leaf)) {
        throw "Leader PnP map is missing: $mapPath"
    }
    $reportHelper = Join-Path $RepositoryRoot 'tools\report_am1_leader_ports.ps1'
    $powerShell = (Get-Command pwsh -ErrorAction Stop).Source
    $json = (& $powerShell -NoLogo -NoProfile -NonInteractive -File $reportHelper `
        -PortMapFile $mapPath -AsJson | Out-String)
    if ($LASTEXITCODE -ne 0) {
        throw "Leader PnP resolution failed with exit code $LASTEXITCODE."
    }
    $report = $json | ConvertFrom-Json
    if (-not $report.auto_selection_permitted) {
        throw 'Leader PnP identities are missing, ambiguous, or resolve to the same port.'
    }
    return [pscustomobject]@{
        left  = [string]$report.role_matches.left.port
        right = [string]$report.role_matches.right.port
    }
}

function Assert-Am1LeaderCalibrationHashes {
    param([Parameter(Mandatory)][psobject]$Config)

    $directory = Join-Path $HOME '.cache\huggingface\lerobot\calibration\teleoperators\so_leader'
    foreach ($side in @('left', 'right')) {
        $path = Join-Path $directory "so101_leader_bi_$side.json"
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "AM1 $side leader calibration is missing: $path"
        }
        $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
        $expected = [string]$Config.leader_calibration_sha256.$side
        if ($actual -cne $expected) {
            throw "AM1 $side leader calibration identity mismatch."
        }
    }
}

function Invoke-Am1Launch {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [ValidateSet('Arms', 'Base', 'Lift', 'Local')]
        [string]$Mode,
        [Parameter(Mandatory)]
        [string]$ConfigPath,
        [AllowNull()][object]$DurationSeconds,
        [string]$LogPath,
        [string]$StopRequestPath,
        [switch]$Preflight,
        [switch]$PrintCommand
    )

    $repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        throw "Local AM1 config is missing. Copy config\am1.local.example.json to config\am1.local.json and edit the local paths."
    }
    $config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    if ($Preflight -and $PrintCommand) {
        throw '-Preflight and -PrintCommand cannot be combined.'
    }
    $localDuration = $null
    if ($Mode -eq 'Local') {
        $localDuration = ConvertTo-Am1LocalDurationSeconds -Value $DurationSeconds
    }
    elseif ($null -ne $DurationSeconds -and -not [string]::IsNullOrWhiteSpace([string]$DurationSeconds)) {
        throw '-DurationSeconds is available only for Local mode.'
    }

    if (-not $PrintCommand) {
        Assert-Am1ReviewedWorktree -RepositoryRoot $repositoryRoot
    }
    $ports = [pscustomobject]@{ left = $null; right = $null }
    if ($Mode -in @('Arms', 'Local')) {
        $ports = Get-Am1RuntimeLeaderPorts -Config $config -RepositoryRoot $repositoryRoot
        Assert-Am1LeaderCalibrationHashes -Config $config
    }
    $command = New-Am1WindowsCommand -Mode $Mode -Config $config -RepositoryRoot $repositoryRoot `
        -LeftPort $ports.left -RightPort $ports.right -LocalDurationSeconds $localDuration `
        -StopRequestPath $StopRequestPath
    $commandText = ConvertTo-Am1CommandText -Executable $command.executable -Arguments $command.arguments

    if ($PrintCommand) {
        Write-Output $commandText
        return
    }

    Assert-Am1ImportRoot -Executable $command.executable -RepositoryRoot $repositoryRoot

    if ($Preflight) {
        Write-Output "AM1_$($Mode.ToUpperInvariant())_PREFLIGHT_READY"
        Write-Output "WINDOWS_COMMAND=$commandText"
        return
    }

    $logDirectory = [string]$config.windows_log_directory
    $null = New-Item -ItemType Directory -Path $logDirectory -Force
    if ([string]::IsNullOrWhiteSpace($LogPath)) {
        $timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $resolvedLogPath = Join-Path $logDirectory "am1-$($Mode.ToLowerInvariant())-windows-$timestamp.log"
    }
    else {
        if (-not [System.IO.Path]::IsPathRooted($LogPath)) {
            throw '-LogPath must be an absolute path.'
        }
        $resolvedLogPath = [System.IO.Path]::GetFullPath($LogPath)
    }
    $isUnifiedSession = -not [string]::IsNullOrWhiteSpace($StopRequestPath)
    if (-not $isUnifiedSession) {
        Write-Output "WINDOWS_LOG=$resolvedLogPath"
        Write-Output "WINDOWS_COMMAND=$commandText"
    }
    "WINDOWS_COMMAND=$commandText" | Set-Content -LiteralPath $resolvedLogPath -Encoding utf8

    $previousPythonPath = $env:PYTHONPATH
    $previousNoBytecode = $env:PYTHONDONTWRITEBYTECODE
    $previousPythonUnbuffered = $env:PYTHONUNBUFFERED
    try {
        $env:PYTHONPATH = (Resolve-Path -LiteralPath (Join-Path $repositoryRoot 'src')).Path
        $env:PYTHONDONTWRITEBYTECODE = '1'
        $env:PYTHONUNBUFFERED = '1'
        Push-Location $command.working_directory
        try {
            $clientExit = Invoke-Am1LoggedCommand -Executable $command.executable `
                -Arguments $command.arguments -LogPath $resolvedLogPath `
                -DisplayDurationSeconds $localDuration
        }
        finally {
            Pop-Location
        }
    }
    finally {
        $env:PYTHONPATH = $previousPythonPath
        $env:PYTHONDONTWRITEBYTECODE = $previousNoBytecode
        $env:PYTHONUNBUFFERED = $previousPythonUnbuffered
    }

    if ($isUnifiedSession) {
        "AM1_CLIENT_EXIT_CODE=$clientExit" | Add-Content -LiteralPath $resolvedLogPath -Encoding utf8
    }
    else {
        "AM1_CLIENT_EXIT_CODE=$clientExit" | Tee-Object -FilePath $resolvedLogPath -Append | Out-Host
    }
    $global:LASTEXITCODE = $clientExit
    if ($clientExit -ne 0 -and $clientExit -ne 130) {
        throw "AM1 $Mode client failed with exit code $clientExit. Review $resolvedLogPath; this shell remains available."
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    if ([string]::IsNullOrWhiteSpace($Mode)) {
        throw 'Specify -Mode Arms, -Mode Base, -Mode Lift, or -Mode Local.'
    }
    Invoke-Am1Launch -Mode $Mode -ConfigPath $ConfigPath -DurationSeconds $DurationSeconds `
        -LogPath $LogPath -StopRequestPath $StopRequestPath -Preflight:$Preflight `
        -PrintCommand:$PrintCommand
}
