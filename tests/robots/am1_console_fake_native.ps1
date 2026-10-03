# Test-only PS7 child boundary. Never invoke run_am1.ps1 or a hardware config.
param(
    [Parameter(Mandatory = $true)][string]$Python,
    [Parameter(Mandatory = $true)][string]$Pipe,
    [Parameter(Mandatory = $true)][string]$Auth,
    [Parameter(Mandatory = $true)][string]$SessionId,
    [Parameter(Mandatory = $true)][string]$StopPath,
    [Parameter(Mandatory = $true)][double]$LiveDurationSeconds,
    [double]$StartupDurationSeconds = 0,
    [double]$MarkerDelaySeconds = 0,
    [switch]$NoOutput
)
$NativeArguments = @()
if ($NoOutput) { $NativeArguments += '--no-output' }
@{ event = 'test_wrapper_started'; session_id = $SessionId; wrapper_pid = $PID } | ConvertTo-Json -Compress
$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $Python
$startInfo.UseShellExecute = $false
foreach ($argument in @('-u', (Join-Path $PSScriptRoot 'am1_console_fake_native.py'),
    '--pipe', $Pipe, '--auth', $Auth, '--session', $SessionId, '--stop', $StopPath,
    '--live-duration', [string]$LiveDurationSeconds, '--startup-duration', [string]$StartupDurationSeconds,
    '--marker-delay-s', [string]$MarkerDelaySeconds) + $NativeArguments) {
    $null = $startInfo.ArgumentList.Add($argument)
}
$child = [System.Diagnostics.Process]::new()
$child.StartInfo = $startInfo
try {
    if (-not $child.Start()) { throw 'Synthetic Python child did not start.' }
    @{ event = 'test_child_launched'; session_id = $SessionId; wrapper_pid = $PID;
       launch_pid = $child.Id } | ConvertTo-Json -Compress
    $child.WaitForExit()
    $exitCode = $child.ExitCode
}
finally { $child.Dispose() }
exit $exitCode
