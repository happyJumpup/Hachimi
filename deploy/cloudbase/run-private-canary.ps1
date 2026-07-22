[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$EnvironmentId,
    [Parameter(Mandatory)][string]$ExpectedStableVersion,
    [Parameter(Mandatory)][string]$ExpectedCandidateCommitSha,
    [Parameter(Mandatory)][string]$Media,
    [Parameter(Mandatory)][string]$OutputDirectory,
    [string]$Region = 'ap-shanghai',
    [string]$ServiceName = 'trainpal-demo',
    [string]$AuthPath = '',
    [string]$JudgeCredentialTarget = 'HakimiFitness.CloudBase.JudgeCode',
    [double]$TimeoutSeconds = 240
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($ExpectedCandidateCommitSha -notmatch '^[0-9a-f]{40}$') {
    throw 'ExpectedCandidateCommitSha must be a full lowercase Git SHA.'
}
if ($TimeoutSeconds -le 0 -or $TimeoutSeconds -gt 600) {
    throw 'TimeoutSeconds must be between 0 and 600.'
}

$resolvedMedia = (Resolve-Path -LiteralPath $Media).Path
if (-not (Test-Path -LiteralPath $resolvedMedia -PathType Leaf)) {
    throw 'Private canary media is unavailable.'
}
$resolvedOutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $resolvedOutputDirectory -Force | Out-Null
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
    throw 'Private deployment receipts must be stored outside the Git repository.'
}

$routeScript = Join-Path $PSScriptRoot 'set-canary-route.ps1'
$canaryScript = Join-Path $PSScriptRoot 'private-canary.py'
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

$canaryRouteToken = [Guid]::NewGuid().ToString('N')
$routeMutationAttempted = $false
$primaryError = $null
$restoreError = $null
$privateCanaryJson = $null
try {
    $routeMutationAttempted = $true
    & $routeScript @routeArguments `
        -Mode enable `
        -RoutingHeaderName 'X-TrainPal-Canary' `
        -RoutingHeaderValue $canaryRouteToken `
        -OutputPath (Join-Path $resolvedOutputDirectory 'route-enable.json') |
        Out-Null

    $pythonArguments = @(
        $canaryScript,
        '--environment-id', $EnvironmentId,
        '--media', $resolvedMedia,
        '--service-name', $ServiceName,
        '--judge-credential-target', $JudgeCredentialTarget,
        '--timeout-seconds', [string]$TimeoutSeconds,
        '--routing-header-name', 'X-TrainPal-Canary',
        '--routing-header-value-stdin',
        '--expected-commit-sha', $ExpectedCandidateCommitSha,
        '--output', (Join-Path $resolvedOutputDirectory 'private-canary.json')
    )
    if ($AuthPath) {
        $pythonArguments += @('--auth-path', $AuthPath)
    }
    $privateCanaryJson = $canaryRouteToken | & python @pythonArguments
    if ($LASTEXITCODE -ne 0) {
        throw 'Private candidate canary failed.'
    }
} catch {
    $primaryError = $_
} finally {
    if ($routeMutationAttempted) {
        try {
            & $routeScript @routeArguments `
                -Mode restore `
                -OutputPath (Join-Path $resolvedOutputDirectory 'route-restore.json') |
                Out-Null
        } catch {
            $restoreError = $_
        }
    }
    Remove-Variable -Name canaryRouteToken -ErrorAction SilentlyContinue
}

if ($null -ne $restoreError) {
    throw 'Private canary failed to restore verified stable traffic.'
}
if ($null -ne $primaryError) {
    throw $primaryError
}

try {
    $privateCanary = $privateCanaryJson | ConvertFrom-Json
} catch {
    throw 'Private canary returned invalid receipt JSON.'
}
$receipt = [ordered]@{
    schema_version = 1
    service = $ServiceName
    candidate_commit_sha = $ExpectedCandidateCommitSha
    stable_version = $ExpectedStableVersion
    route_restored = $true
    private_canary = $privateCanary
}
$json = $receipt | ConvertTo-Json -Depth 20
[IO.File]::WriteAllText(
    (Join-Path $resolvedOutputDirectory 'private-canary-transaction.json'),
    $json,
    [Text.UTF8Encoding]::new($false)
)
$json
