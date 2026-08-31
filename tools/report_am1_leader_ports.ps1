[CmdletBinding()]
param(
    [string]$PortMapFile,
    [string]$DeviceSnapshotPath,
    [switch]$AsJson
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Read-JsonFile {
    param(
        [Parameter(Mandatory)]
        [string]$Path
    )

    $resolved = (Resolve-Path -LiteralPath $Path).Path
    Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json
}

function Get-CurrentPortDevices {
    if ($DeviceSnapshotPath) {
        return @(Read-JsonFile -Path $DeviceSnapshotPath)
    }

    if (-not (Get-Command -Name Get-PnpDevice -ErrorAction SilentlyContinue)) {
        throw 'Get-PnpDevice is unavailable; run this helper from Windows PowerShell or PowerShell 7 with the PnpDevice module.'
    }

    return @(
        Get-PnpDevice -Class Ports -PresentOnly | ForEach-Object {
            [pscustomobject][ordered]@{
                FriendlyName = [string]$_.FriendlyName
                InstanceId   = [string]$_.InstanceId
                Status       = [string]$_.Status
            }
        }
    )
}

$devices = @(
    foreach ($device in Get-CurrentPortDevices) {
        $friendlyName = [string]$device.FriendlyName
        $instanceId = [string]$device.InstanceId
        $status = [string]$device.Status
        $isCh343 =
            $instanceId -match '(?i)VID_1A86&PID_55D3' -or
            $friendlyName -match '(?i)\bCH343\b'
        if (-not $isCh343) {
            continue
        }

        $portMatch = [regex]::Match($friendlyName, '(?i)\((COM\d+)\)')
        $port = if ($portMatch.Success) { $portMatch.Groups[1].Value.ToUpperInvariant() } else { $null }
        [pscustomobject][ordered]@{
            port          = $port
            friendly_name = $friendlyName
            instance_id   = $instanceId
            status        = $status
            matched_role  = $null
        }
    }
) | Sort-Object port, instance_id

$roleMatches = [ordered]@{}
foreach ($role in @('left', 'right')) {
    $roleMatches[$role] = [ordered]@{
        status      = 'unmapped'
        port        = $null
        instance_id = $null
    }
}

if ($PortMapFile) {
    $portMap = Read-JsonFile -Path $PortMapFile
    foreach ($role in @('left', 'right')) {
        $propertyName = "physical_$role"
        $property = $portMap.PSObject.Properties[$propertyName]
        if ($null -eq $property) {
            throw "Port map is missing $propertyName."
        }
        $expectedId = [string]$property.Value.pnp_device_id
        if ([string]::IsNullOrWhiteSpace($expectedId)) {
            throw "Port map $propertyName.pnp_device_id is empty."
        }

        $matches = @($devices | Where-Object { $_.instance_id -ieq $expectedId })
        $status = if ($matches.Count -eq 0) {
            'missing'
        }
        elseif ($matches.Count -gt 1) {
            'ambiguous'
        }
        elseif ([string]::IsNullOrWhiteSpace([string]$matches[0].port)) {
            'unusable'
        }
        else {
            'matched'
        }

        $roleMatches[$role] = [ordered]@{
            status      = $status
            port        = if ($status -eq 'matched') { [string]$matches[0].port } else { $null }
            instance_id = $expectedId
        }
        if ($status -eq 'matched') {
            $matches[0].matched_role = $role
        }
    }
}

$autoSelectionPermitted =
    $roleMatches.left.status -eq 'matched' -and
    $roleMatches.right.status -eq 'matched' -and
    $roleMatches.left.port -ine $roleMatches.right.port

$report = [pscustomobject][ordered]@{
    schema_version             = 1
    source                     = if ($DeviceSnapshotPath) { 'snapshot' } else { 'windows-pnp' }
    devices                    = @($devices)
    role_matches               = [pscustomobject]$roleMatches
    auto_selection_permitted   = $autoSelectionPermitted
}

if ($AsJson) {
    $report | ConvertTo-Json -Depth 8
    exit 0
}

$report.devices |
    Select-Object @{ Name = 'COM Port'; Expression = { $_.port } },
        @{ Name = 'FriendlyName'; Expression = { $_.friendly_name } },
        @{ Name = 'PnP InstanceId'; Expression = { $_.instance_id } },
        @{ Name = 'Status'; Expression = { $_.status } },
        @{ Name = 'MatchedRole'; Expression = { $_.matched_role } } |
    Format-Table -AutoSize | Out-Host

if ($PortMapFile) {
    Write-Output ("Left match: {0}; Right match: {1}; unique stored identities: {2}" -f
        $report.role_matches.left.status,
        $report.role_matches.right.status,
        $report.auto_selection_permitted)
}
else {
    Write-Output 'No port map supplied; roles were not selected. Pass explicit runtime LeftPort and RightPort values.'
}
