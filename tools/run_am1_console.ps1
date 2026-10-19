[CmdletBinding()]
param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot '..\config\am1.session.json'),
    [switch]$NoBrowser,
    [switch]$DirectBrowser,
    [ValidateNotNullOrEmpty()][string]$SessionUrl,
    [switch]$SoftwareVideoDecode
)

# Opens the legacy loopback console or only an explicit existing Pi session page.
# Browser opening never starts a camera, SSH connection or motor session.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function New-Am1ConsoleCommand {
    param([Parameter(Mandatory)][string]$ConfigPath, [switch]$NoBrowser, [switch]$DirectBrowser,
          [ValidateNotNullOrEmpty()][string]$SessionUrl, [switch]$SoftwareVideoDecode)

    if ($NoBrowser -and $DirectBrowser) { throw 'NoBrowser and DirectBrowser are mutually exclusive.' }
    $sessionOpen = $PSBoundParameters.ContainsKey('SessionUrl')
    if ($sessionOpen -and ($NoBrowser -or $DirectBrowser)) { throw 'SessionUrl cannot combine with legacy browser modes.' }
    if ($SoftwareVideoDecode -and -not $sessionOpen) { throw 'SoftwareVideoDecode requires SessionUrl.' }

    $exactConfig = [System.IO.Path]::GetFullPath($ConfigPath)
    if (-not (Test-Path -LiteralPath $exactConfig -PathType Leaf)) {
        throw "Private AM1 session config is missing: $exactConfig"
    }
    $config = Get-Content -LiteralPath $exactConfig -Raw | ConvertFrom-Json
    $python = [string]$config.windows_python
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Configured Windows Python is missing: $python"
    }
    if ($sessionOpen) {
        $stateDirectory = [string]$config.local_state_directory
        if ([string]::IsNullOrWhiteSpace($stateDirectory) -or -not [System.IO.Path]::IsPathRooted($stateDirectory)) {
            throw 'Private local_state_directory must be configured as an absolute path.'
        }
    }
    else {
        $auth = [string]$config.console_camera_auth_file
        if ([string]::IsNullOrWhiteSpace($auth) -or -not [System.IO.Path]::IsPathRooted($auth) -or
            -not (Test-Path -LiteralPath $auth -PathType Leaf)) {
            throw 'Private console camera-auth file is not configured or accessible.'
        }
    }
    $arguments = @('-m', 'tools.am1_console', '--config', $exactConfig)
    if ($NoBrowser) { $arguments += '--no-browser' }
    if ($DirectBrowser) { $arguments += '--direct-browser' }
    if ($sessionOpen) { $arguments += @('--session-url', $SessionUrl) }
    if ($SoftwareVideoDecode) { $arguments += '--software-video-decode' }
    return [pscustomobject]@{ executable = $python; arguments = $arguments;
                             directory = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')) }
}

function Invoke-Am1Console {
    param([Parameter(Mandatory)][string]$ConfigPath, [switch]$NoBrowser, [switch]$DirectBrowser,
          [ValidateNotNullOrEmpty()][string]$SessionUrl, [switch]$SoftwareVideoDecode)
    $commandArguments = @{ ConfigPath = $ConfigPath; NoBrowser = $NoBrowser;
                           DirectBrowser = $DirectBrowser; SoftwareVideoDecode = $SoftwareVideoDecode }
    if ($PSBoundParameters.ContainsKey('SessionUrl')) { $commandArguments.SessionUrl = $SessionUrl }
    $command = New-Am1ConsoleCommand @commandArguments
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
    $invokeArguments = @{ ConfigPath = $ConfigPath; NoBrowser = $NoBrowser;
                          DirectBrowser = $DirectBrowser; SoftwareVideoDecode = $SoftwareVideoDecode }
    if ($PSBoundParameters.ContainsKey('SessionUrl')) { $invokeArguments.SessionUrl = $SessionUrl }
    exit (Invoke-Am1Console @invokeArguments)
}
