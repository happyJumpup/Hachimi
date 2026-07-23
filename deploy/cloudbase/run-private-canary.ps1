[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$EnvironmentId,
    [Parameter(Mandatory)][string]$PublicBaseUrl,
    [Parameter(Mandatory)][string]$ExpectedStableVersion,
    [Parameter(Mandatory)][string]$ExpectedCandidateCommitSha,
    [Parameter(Mandatory)][string]$OutputDirectory,
    [string]$Region = 'ap-shanghai',
    [string]$ServiceName = 'trainpal-demo',
    [string]$AuthPath = '',
    [ValidateRange(1, 120)][int]$TimeoutSeconds = 30
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($ExpectedCandidateCommitSha -notmatch '^[0-9a-f]{40}$') {
    throw 'ExpectedCandidateCommitSha must be a full lowercase Git SHA.'
}

[Uri]$publicUri = $null
if (
    -not [Uri]::TryCreate(
        $PublicBaseUrl,
        [UriKind]::Absolute,
        [ref]$publicUri
    ) -or
    $publicUri.Scheme -ne [Uri]::UriSchemeHttps -or
    $publicUri.UserInfo -or
    $publicUri.Query -or
    $publicUri.Fragment -or
    $publicUri.AbsolutePath -ne '/'
) {
    throw 'PublicBaseUrl must be an HTTPS origin without credentials, path, query, or fragment.'
}

$resolvedOutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$repositoryPrefix = $repositoryRoot.TrimEnd('\') + '\'
if (
    $resolvedOutputDirectory.Equals(
        $repositoryRoot,
        [StringComparison]::OrdinalIgnoreCase
    ) -or
    $resolvedOutputDirectory.StartsWith(
        $repositoryPrefix,
        [StringComparison]::OrdinalIgnoreCase
    )
) {
    throw 'Deployment receipts must be stored outside the Git repository.'
}
New-Item -ItemType Directory -Path $resolvedOutputDirectory -Force | Out-Null
$receiptPath = Join-Path $resolvedOutputDirectory 'private-canary-transaction.json'
if (Test-Path -LiteralPath $receiptPath) {
    if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) {
        throw 'The candidate transaction receipt path is not a file.'
    }
    Remove-Item -LiteralPath $receiptPath -Force
}

$routeScript = Join-Path $PSScriptRoot 'set-canary-route.ps1'
$routeArguments = @{
    EnvironmentId = $EnvironmentId
    ExpectedStableVersion = $ExpectedStableVersion
    ExpectedCandidateCommitSha = $ExpectedCandidateCommitSha
    Region = $Region
    ServiceName = $ServiceName
}
if ($AuthPath) {
    $routeArguments['AuthPath'] = $AuthPath
}

$healthUri = [Uri]::new($publicUri, "/api/v1/health")
$readyUri = [Uri]::new($publicUri, "/api/v1/ready")
$routeMutationAttempted = $false
$primaryError = $null
$restoreError = $null
$healthVerified = $false
$readyVerified = $false

try {
    $routeMutationAttempted = $true
    & $routeScript @routeArguments -Mode promote | Out-Null

    try {
        $health = Invoke-RestMethod `
            -Method Get `
            -Uri $healthUri.AbsoluteUri `
            -Headers @{ Accept = 'application/json' } `
            -MaximumRedirection 0 `
            -TimeoutSec $TimeoutSeconds `
            -ErrorAction Stop
    } catch {
        throw 'Public candidate health request failed.'
    }
    $healthStatus = $health.PSObject.Properties['status']
    $healthReleaseSha = $health.PSObject.Properties['release_sha']
    if (
        $null -eq $healthStatus -or
        [string]$healthStatus.Value -ne 'ok' -or
        $null -eq $healthReleaseSha -or
        [string]$healthReleaseSha.Value -cne $ExpectedCandidateCommitSha
    ) {
        throw 'Public candidate health identity did not match the expected release.'
    }
    $healthVerified = $true

    try {
        $ready = Invoke-RestMethod `
            -Method Get `
            -Uri $readyUri.AbsoluteUri `
            -Headers @{ Accept = 'application/json' } `
            -MaximumRedirection 0 `
            -TimeoutSec $TimeoutSeconds `
            -ErrorAction Stop
    } catch {
        throw 'Public candidate readiness request failed.'
    }
    $readyStatus = $ready.PSObject.Properties['status']
    if ($null -eq $readyStatus -or [string]$readyStatus.Value -ne 'ready') {
        throw 'Public candidate is not ready.'
    }
    $readyVerified = $true
} catch {
    $primaryError = $_
} finally {
    if ($routeMutationAttempted) {
        try {
            & $routeScript @routeArguments -Mode restore | Out-Null
        } catch {
            $restoreError = $_
        }
    }
}

if ($null -ne $restoreError) {
    throw 'Candidate transaction failed to restore verified stable traffic.'
}
if ($null -ne $primaryError -or -not $healthVerified -or -not $readyVerified) {
    throw 'Candidate full-FLOW identity transaction failed; stable traffic was restored.'
}

$receipt = [ordered]@{
    service = $ServiceName
    candidate_commit_sha = $ExpectedCandidateCommitSha
    stable_version = $ExpectedStableVersion
    health = 'ok'
    ready = 'ready'
    route_restored = $true
}
$json = $receipt | ConvertTo-Json -Depth 5
[IO.File]::WriteAllText(
    $receiptPath,
    $json,
    [Text.UTF8Encoding]::new($false)
)
$json
