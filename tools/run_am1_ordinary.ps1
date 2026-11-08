#Requires -Version 7
[CmdletBinding()]
param(
    [double]$SessionMinutes,
    [string]$ProjectRoot,
    [string]$OrdinaryConfig,
    [string]$ConsoleConfig,
    [ValidateSet(16, 32)][int]$RightElbowP,
    [switch]$Start
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'run_am1_console.ps1')

function Invoke-Am1Ordinary {
    param(
        [double]$SessionMinutes = 15,
        [string]$ProjectRoot = $env:AM1_PROJECT_ROOT,
        [string]$OrdinaryConfig,
        [string]$ConsoleConfig,
        [ValidateSet(16, 32)][int]$RightElbowP,
        [switch]$Start
    )

    $seconds = $SessionMinutes * 60
    if (-not [double]::IsFinite($seconds) -or $seconds -lt 1 -or $seconds -gt 3600 -or $seconds -ne [Math]::Truncate($seconds)) {
        throw 'Choose between 1 second and 60 minutes, in whole seconds.'
    }
    if (-not $Start -and [Console]::IsInputRedirected) {
        throw 'Use a Windows terminal for the Start prompt, or pass -Start when the working view is ready.'
    }
    $worktree = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    if (-not $ProjectRoot) {
        $ProjectRoot = Join-Path $env:USERPROFILE 'lerobot_alohamini_client'
    }
    $project = (Resolve-Path -LiteralPath $ProjectRoot).ProviderPath
    if (-not (Test-Path -LiteralPath (Join-Path $project 'pyproject.toml') -PathType Leaf)) {
        throw 'The shared AM1 Python project is missing. Set -ProjectRoot to its existing folder.'
    }
    if (-not $OrdinaryConfig) {
        $OrdinaryConfig = Join-Path $worktree '.cache/am1-knowledge-reuse-18/ordinary-am1-arms.private.json'
    }
    if (-not $ConsoleConfig) {
        $ConsoleConfig = Join-Path $project '.worktrees/am1-console-implementation/config/am1.session.json'
    }
    $ordinaryPath = (Resolve-Path -LiteralPath $OrdinaryConfig).ProviderPath
    $ordinary = Get-Content -LiteralPath $ordinaryPath -Raw | ConvertFrom-Json
    $consolePath = (Resolve-Path -LiteralPath $ConsoleConfig).ProviderPath
    $uv = Get-Command uv -CommandType Application
    $minutes = $SessionMinutes.ToString('R', [System.Globalization.CultureInfo]::InvariantCulture)

    $viewerExit = Invoke-Am1Console -ConfigPath $consolePath -SessionUrl $ordinary.url -SoftwareVideoDecode
    if ($viewerExit -ne 0) { return $viewerExit }

    if (-not $Start) {
        $choice = Read-Host "With Chest visible, press Enter to start the $minutes-minute session or type q to cancel"
        if ($choice -ne '') { return 0 }
    }
    Write-Host 'Wait for Ready before typing a movement command.'
    Write-Host 'Move: move SECONDS VALID_FOR joint=value... | Hold: hold VALID_FOR | Return: return SECONDS VALID_FOR'
    Write-Host 'Pause: pause VALID_FOR | Resume: resume VALID_FOR, then type a fresh move | Finish: finish'
    Write-Host 'Use Stop in the viewer to stop the session immediately.'
    $command = [pscustomobject]@{
        executable = $uv.Source
        directory = $worktree
        arguments = @('run', '--no-sync', '--project', $project, 'python', '-u', '-B', '-m',
            'tools.am1_leader_adapter', '--ordinary-config', $ordinaryPath, '--virtual-input', 'interactive',
            '--session-minutes', $minutes, '--operator-status')
    }
    if ($PSBoundParameters.ContainsKey('RightElbowP')) {
        $command.arguments += @('--right-elbow-p', "$RightElbowP")
    }
    $pythonPath = @((Join-Path $worktree 'src'), $worktree, (Join-Path $worktree '.cache/am1-session-pi-01/http-overlay'))
    Invoke-Am1ConsoleProcess -Command $command -ProcessName 'Ordinary AM1 adapter' -Environment @{
        AM1_PROJECT_ROOT = $project
        PYTHONUTF8 = '1'
        PYTHONDONTWRITEBYTECODE = '1'
        PYTHONPATH = $pythonPath -join [System.IO.Path]::PathSeparator
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    exit (Invoke-Am1Ordinary @PSBoundParameters)
}
