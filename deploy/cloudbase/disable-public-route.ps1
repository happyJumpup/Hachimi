[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$EnvironmentId,
    [Parameter(Mandatory)][string]$Domain,
    [string]$Region = 'ap-shanghai',
    [string]$ServiceName = 'trainpal-demo',
    [string]$OutputPath = '',
    [switch]$CheckOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function ConvertFrom-CliJson {
    param([Parameter(Mandatory)][string]$Raw)

    $objectStart = $Raw.IndexOf('{')
    $arrayStart = $Raw.IndexOf('[')
    $validStarts = @(
        @($objectStart, $arrayStart) | Where-Object { $_ -ge 0 }
    )
    if ($validStarts.Count -eq 0) {
        throw 'CloudBase CLI returned no JSON payload.'
    }
    $start = ($validStarts | Measure-Object -Minimum).Minimum
    try {
        return $Raw.Substring($start) | ConvertFrom-Json
    } catch {
        throw 'CloudBase CLI returned an invalid JSON payload.'
    }
}

function Invoke-CloudBaseCli {
    param(
        [Parameter(Mandatory)][string[]]$Arguments,
        [switch]$ExpectJson
    )

    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $raw = (& npx --yes --package '@cloudbase/cli@3.6.4' tcb @Arguments 2>$null) |
            Out-String
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0) {
        throw 'CloudBase CLI route operation failed.'
    }
    if ($ExpectJson) {
        return ConvertFrom-CliJson $raw
    }
    return $raw
}

function Get-TargetRoute {
    $filter = "Domain=${Domain}&UpstreamResourceType=CBR"
    $payload = Invoke-CloudBaseCli -ExpectJson -Arguments @(
        '--env-id', $EnvironmentId,
        '--region', $Region,
        'routes', 'list',
        '--limit', '20',
        '--filter', $filter,
        '--json'
    )
    $routes = @(
        @($payload.data) | Where-Object {
            [string]$_.domain -eq $Domain -and
            [string]$_.path -eq '/' -and
            [string]$_.upstreamResourceType -eq 'CBR' -and
            [string]$_.upstreamResourceName -eq $ServiceName
        }
    )
    if ($routes.Count -ne 1) {
        throw 'Expected exactly one matching CloudBase public root route.'
    }
    return $routes[0]
}

if ($EnvironmentId -notmatch '^[a-z0-9-]{3,64}$') {
    throw 'EnvironmentId has an invalid format.'
}
if (
    $Domain -notmatch '^[a-z0-9.-]{3,253}$' -or
    $Domain.StartsWith('.') -or
    $Domain.EndsWith('.') -or
    $Domain.Contains('..')
) {
    throw 'Domain has an invalid format.'
}
if ($Region -notmatch '^[a-z0-9-]{3,32}$') {
    throw 'Region has an invalid format.'
}
if ($ServiceName -notmatch '^[a-zA-Z0-9_-]{3,80}$') {
    throw 'ServiceName has an invalid format.'
}

$before = Get-TargetRoute
$changed = $false
if (-not $CheckOnly -and [bool]$before.enable) {
    $update = [ordered]@{
        domain = $Domain
        routes = @(
            [ordered]@{
                path = '/'
                enable = $false
            }
        )
    } | ConvertTo-Json -Compress -Depth 5
    $null = Invoke-CloudBaseCli -Arguments @(
        '--env-id', $EnvironmentId,
        '--region', $Region,
        'routes', 'edit',
        '--data', $update,
        '--json'
    )
    $changed = $true
}

$after = Get-TargetRoute
if (-not $CheckOnly -and [bool]$after.enable) {
    throw 'CloudBase public root route is still enabled after the update.'
}

$receipt = [ordered]@{
    schema_version = 1
    service = $ServiceName
    region = $Region
    route_path = '/'
    upstream_type = 'CBR'
    check_only = [bool]$CheckOnly
    route_enabled = [bool]$after.enable
    changed = $changed
    observed_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
}
$json = $receipt | ConvertTo-Json -Depth 5
if ($OutputPath) {
    $parent = Split-Path -Parent $OutputPath
    if ($parent) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    [IO.File]::WriteAllText(
        [IO.Path]::GetFullPath($OutputPath),
        $json,
        [Text.UTF8Encoding]::new($false)
    )
}
$json
