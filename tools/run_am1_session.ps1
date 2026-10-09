[CmdletBinding(DefaultParameterSetName = 'Start')]
param(
    [Parameter(Mandatory, ParameterSetName = 'Start')]
    [AllowEmptyString()]
    [string]$DurationSeconds,
    [Parameter(ParameterSetName = 'Start')]
    [ValidateSet('Physical', 'Scripted')]
    [string]$LeaderSource = 'Physical',
    [Parameter(ParameterSetName = 'Start')]
    [string]$MotionProfile,
    [Parameter(Mandatory, ParameterSetName = 'Stop')]
    [switch]$Stop,
    [Parameter(Mandatory, ParameterSetName = 'Collect')]
    [switch]$CollectOnly,
    [Parameter(Mandatory, ParameterSetName = 'Collect')]
    [string]$SessionId,
    [string]$ConfigPath = (Join-Path $PSScriptRoot '..\config\am1.session.json')
)

# Copyright 2026 The HuggingFace Inc. team. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function ConvertTo-Am1SessionDuration {
    param([AllowNull()][object]$Value)

    if ($null -eq $Value) {
        throw 'DurationSeconds must be a whole number from 1 through 1800.'
    }
    $text = [string]$Value
    if ($text -cnotmatch '^(?:[1-9]|[1-9][0-9]{1,2}|1[0-7][0-9]{2}|1800)$') {
        throw 'DurationSeconds must be a whole number from 1 through 1800.'
    }
    return [int]::Parse($text, [System.Globalization.CultureInfo]::InvariantCulture)
}

function Invoke-Am1SessionEntrypoint {
    [CmdletBinding(DefaultParameterSetName = 'Start')]
    param(
        [Parameter(Mandatory, ParameterSetName = 'Start')]
        [AllowEmptyString()]
        [string]$DurationSeconds,
        [Parameter(ParameterSetName = 'Start')]
        [ValidateSet('Physical', 'Scripted')]
        [string]$LeaderSource = 'Physical',
        [Parameter(ParameterSetName = 'Start')]
        [string]$MotionProfile,
        [Parameter(Mandatory, ParameterSetName = 'Stop')]
        [switch]$Stop,
        [Parameter(Mandatory, ParameterSetName = 'Collect')]
        [switch]$CollectOnly,
        [Parameter(Mandatory, ParameterSetName = 'Collect')]
        [string]$SessionId,
        [Parameter(Mandatory)]
        [string]$ConfigPath
    )

    # Validate the raw token before PowerShell or Python can round/coerce it and
    # before opening SSH, cameras, COM ports, or motor buses.
    $validatedDuration = $null
    if ($PSCmdlet.ParameterSetName -eq 'Start') {
        $validatedDuration = ConvertTo-Am1SessionDuration -Value $DurationSeconds
        if ($LeaderSource -eq 'Scripted' -and $MotionProfile -cnotin @('ArmSmoke', 'ArmSmokeRepeat', 'ArmHoldBody')) {
            throw 'Scripted leader input requires -MotionProfile ArmSmoke, ArmSmokeRepeat or ArmHoldBody.'
        }
        if ($MotionProfile -ceq 'ArmSmokeRepeat' -and $validatedDuration -ne 420) {
            throw 'ArmSmokeRepeat requires the finite -DurationSeconds 420 ceiling.'
        }
        if ($MotionProfile -ceq 'ArmHoldBody' -and $validatedDuration -ne 12) {
            throw 'ArmHoldBody requires the finite -DurationSeconds 12 ceiling.'
        }
        if ($LeaderSource -eq 'Physical' -and -not [string]::IsNullOrEmpty($MotionProfile)) {
            throw '-MotionProfile is available only for Scripted leader input.'
        }
    }
    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        throw "Private AM1 session config is missing: $ConfigPath"
    }
    $config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    $python = [string]$config.windows_python
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Configured Windows Python is missing: $python"
    }
    $script = Join-Path $PSScriptRoot 'am1_session.py'
    $arguments = @($script, '--config', [System.IO.Path]::GetFullPath($ConfigPath))
    if ($PSCmdlet.ParameterSetName -eq 'Start') {
        $arguments += @('start', '--duration-seconds', [string]$validatedDuration)
        if ($LeaderSource -eq 'Scripted') {
            $arguments += @('--leader-source', 'scripted', '--motion-profile', $MotionProfile)
        }
    }
    elseif ($PSCmdlet.ParameterSetName -eq 'Stop') {
        $arguments += 'stop'
    }
    else {
        $arguments += @('collect', '--session-id', $SessionId)
    }

    # Bypass PowerShell's line-oriented native-output adapter and preserve the
    # interactive child's real standard handles, including when an outer shell
    # is collecting launcher output. This keeps prompts visible before input.
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $python
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardInput = $false
    $startInfo.RedirectStandardOutput = $false
    $startInfo.RedirectStandardError = $false
    foreach ($argument in $arguments) {
        $null = $startInfo.ArgumentList.Add([string]$argument)
    }
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) {
            throw 'Configured Windows Python did not start.'
        }
        $process.WaitForExit()
        $exitCode = [int]$process.ExitCode
    }
    finally {
        $process.Dispose()
    }
    $global:LASTEXITCODE = $exitCode
    return $exitCode
}

if ($MyInvocation.InvocationName -ne '.') {
    if ($PSCmdlet.ParameterSetName -eq 'Start') {
        $exitCode = Invoke-Am1SessionEntrypoint -DurationSeconds $DurationSeconds -ConfigPath $ConfigPath `
            -LeaderSource $LeaderSource -MotionProfile $MotionProfile
    }
    elseif ($PSCmdlet.ParameterSetName -eq 'Stop') {
        $exitCode = Invoke-Am1SessionEntrypoint -Stop -ConfigPath $ConfigPath
    }
    else {
        $exitCode = Invoke-Am1SessionEntrypoint -CollectOnly -SessionId $SessionId -ConfigPath $ConfigPath
    }
    exit $exitCode
}
