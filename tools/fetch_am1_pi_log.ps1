[CmdletBinding()]
param(
    [string]$RemoteHost = 'pickmanmike@192.168.1.134',
    [string]$RemotePath,
    [string]$LocalDirectory = (Join-Path $HOME 'AlohaMini1Logs'),
    [switch]$DryRun,
    [string]$DryRunRemoteListing = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-ValidRemoteHost {
    param([Parameter(Mandatory)][string]$Value)

    if ($Value -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9_.-]*$') {
        throw 'RemoteHost must be a plain user@host value.'
    }
}

function Assert-ValidRemotePath {
    param([Parameter(Mandatory)][string]$Value)

    if ($Value -notmatch '^/home/pickmanmike/am1-[A-Za-z0-9._-]+\.log$') {
        throw 'RemotePath must name /home/pickmanmike/am1-*.log.'
    }
}

function Select-NewestRemotePath {
    param([string[]]$Candidates)

    $parsed = @(
        foreach ($candidate in $Candidates) {
            if ($candidate -notmatch '^(?<Timestamp>[0-9]+(?:\.[0-9]+)?)\s+(?<Path>/\S+)$') {
                throw "Invalid remote log candidate: $candidate"
            }
            Assert-ValidRemotePath -Value $Matches.Path
            [pscustomobject]@{
                Timestamp = [double]$Matches.Timestamp
                Path = $Matches.Path
            }
        }
    )
    if ($parsed.Count -eq 0) {
        throw 'No /home/pickmanmike/am1-*.log candidate was supplied or found.'
    }
    return ($parsed | Sort-Object -Property Timestamp -Descending | Select-Object -First 1).Path
}

Assert-ValidRemoteHost -Value $RemoteHost

if ($RemotePath) {
    Assert-ValidRemotePath -Value $RemotePath
    $selectedRemotePath = $RemotePath
} elseif ($DryRun) {
    $dryRunCandidates = @($DryRunRemoteListing -split '[\r\n]+' | Where-Object { $_ })
    $selectedRemotePath = Select-NewestRemotePath -Candidates $dryRunCandidates
} else {
    $discoverCommand = "find /home/pickmanmike -maxdepth 1 -type f -name 'am1-*.log' -printf '%T@ %p\n'"
    $remoteCandidates = @(& ssh $RemoteHost $discoverCommand)
    if ($LASTEXITCODE -ne 0) {
        throw "SSH log discovery failed with exit code $LASTEXITCODE."
    }
    $selectedRemotePath = Select-NewestRemotePath -Candidates $remoteCandidates
}

$localFileName = [System.IO.Path]::GetFileName($selectedRemotePath)
$selectedLocalPath = Join-Path $LocalDirectory $localFileName
Write-Output "Remote path: $selectedRemotePath"
Write-Output "Local path: $selectedLocalPath"

if ($DryRun) {
    Write-Output 'DRY RUN: no SSH, SCP, or local write was performed.'
    exit 0
}

$null = New-Item -ItemType Directory -Path $LocalDirectory -Force
$temporaryPath = Join-Path $LocalDirectory ".$localFileName.$([guid]::NewGuid().ToString('N')).part"
try {
    & scp "$RemoteHost`:$selectedRemotePath" $temporaryPath
    if ($LASTEXITCODE -ne 0) {
        throw "SCP failed with exit code $LASTEXITCODE."
    }
    $copiedFile = Get-Item -LiteralPath $temporaryPath
    if ($copiedFile.Length -le 0) {
        throw "Copied log is empty: $selectedRemotePath"
    }
    Move-Item -LiteralPath $temporaryPath -Destination $selectedLocalPath -Force
} finally {
    if (Test-Path -LiteralPath $temporaryPath) {
        Remove-Item -LiteralPath $temporaryPath -Force
    }
}

$savedFile = Get-Item -LiteralPath $selectedLocalPath
if ($savedFile.Length -le 0) {
    throw "Saved log is empty: $selectedLocalPath"
}
Write-Output "Saved $($savedFile.Length) bytes."
