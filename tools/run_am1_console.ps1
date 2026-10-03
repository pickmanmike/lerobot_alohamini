[CmdletBinding()]
param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot '..\config\am1.session.json'),
    [switch]$NoBrowser,
    [switch]$DirectBrowser
)

# Opens only the Windows loopback console. A browser Start is still required
# before any camera or motor session can begin.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function New-Am1ConsoleCommand {
    param([Parameter(Mandatory)][string]$ConfigPath, [switch]$NoBrowser, [switch]$DirectBrowser)

    if ($NoBrowser -and $DirectBrowser) { throw 'NoBrowser and DirectBrowser are mutually exclusive.' }

    $exactConfig = [System.IO.Path]::GetFullPath($ConfigPath)
    if (-not (Test-Path -LiteralPath $exactConfig -PathType Leaf)) {
        throw "Private AM1 session config is missing: $exactConfig"
    }
    $config = Get-Content -LiteralPath $exactConfig -Raw | ConvertFrom-Json
    $python = [string]$config.windows_python
    $auth = [string]$config.console_camera_auth_file
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Configured Windows Python is missing: $python"
    }
    if ([string]::IsNullOrWhiteSpace($auth) -or -not [System.IO.Path]::IsPathRooted($auth) -or
        -not (Test-Path -LiteralPath $auth -PathType Leaf)) {
        throw 'Private console camera-auth file is not configured or accessible.'
    }
    $arguments = @('-m', 'tools.am1_console', '--config', $exactConfig)
    if ($NoBrowser) { $arguments += '--no-browser' }
    if ($DirectBrowser) { $arguments += '--direct-browser' }
    return [pscustomobject]@{ executable = $python; arguments = $arguments;
                             directory = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')) }
}

function Invoke-Am1Console {
    param([Parameter(Mandatory)][string]$ConfigPath, [switch]$NoBrowser, [switch]$DirectBrowser)
    $command = New-Am1ConsoleCommand -ConfigPath $ConfigPath -NoBrowser:$NoBrowser -DirectBrowser:$DirectBrowser
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $command.executable
    $startInfo.WorkingDirectory = $command.directory
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardInput = $false
    $startInfo.RedirectStandardOutput = $false
    $startInfo.RedirectStandardError = $false
    foreach ($argument in $command.arguments) {
        $null = $startInfo.ArgumentList.Add([string]$argument)
    }
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) { throw 'Configured Windows Python did not start.' }
        $process.WaitForExit()
        $exitCode = [int]$process.ExitCode
    }
    finally { $process.Dispose() }
    $global:LASTEXITCODE = $exitCode
    return $exitCode
}

if ($MyInvocation.InvocationName -ne '.') {
    exit (Invoke-Am1Console -ConfigPath $ConfigPath -NoBrowser:$NoBrowser -DirectBrowser:$DirectBrowser)
}
