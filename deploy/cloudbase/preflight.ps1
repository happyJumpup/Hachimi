[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$validator = Join-Path $PSScriptRoot 'verify-foundation.py'

Push-Location $repositoryRoot
try {
    & python $validator
    if ($LASTEXITCODE -ne 0) {
        throw 'CloudBase foundation contract validation failed.'
    }

    $globalHelp = & npx --yes --package '@cloudbase/cli@3.6.4' tcb --help | Out-String
    if ($LASTEXITCODE -ne 0) {
        throw 'CloudBase CLI global command check failed.'
    }
    $deployHelp = & npx --yes --package '@cloudbase/cli@3.6.4' tcb cloudrun deploy --help | Out-String
    if ($LASTEXITCODE -ne 0) {
        throw 'CloudBase CLI deploy command check failed.'
    }
    foreach ($requiredOption in @('--env-id', '--serviceName', '--port', '--source', '--imageUrl', '--traffic')) {
        $helpText = if ($requiredOption -eq '--env-id') { $globalHelp } else { $deployHelp }
        if (-not $helpText.Contains($requiredOption)) {
            throw "CloudBase CLI 3.6.4 is missing required option: $requiredOption"
        }
    }

    $commitSha = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $commitSha -notmatch '^[0-9a-f]{40}$') {
        throw 'Could not resolve the current immutable commit.'
    }
    $packageRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("trainpal-preflight-{0}" -f ([guid]::NewGuid().ToString('N')))
    $sourceDirectory = Join-Path $packageRoot 'source'
    try {
        & (Join-Path $PSScriptRoot 'package-source.ps1') `
            -CommitSha $commitSha `
            -OutputDirectory $sourceDirectory | Out-Null
        $metadataPath = Join-Path $sourceDirectory '.trainpal-build-metadata.json'
        $metadata = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
        if (
            $metadata.schema_version -ne 1 -or
            $metadata.commit_sha -ne $commitSha -or
            (Test-Path -LiteralPath (Join-Path $sourceDirectory '.git'))
        ) {
            throw 'CloudBase source package build metadata is invalid.'
        }
    } finally {
        $resolvedPackageRoot = [System.IO.Path]::GetFullPath($packageRoot)
        $systemTemporaryRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
        if (
            $resolvedPackageRoot.StartsWith($systemTemporaryRoot, [System.StringComparison]::OrdinalIgnoreCase) -and
            (Split-Path -Leaf $resolvedPackageRoot).StartsWith('trainpal-preflight-', [System.StringComparison]::Ordinal)
        ) {
            try {
                Remove-Item -LiteralPath $resolvedPackageRoot -Recurse -Force -ErrorAction Stop
            } catch {
                Write-Warning 'Temporary CloudBase preflight package cleanup failed.'
            }
        }
    }

    Write-Output 'CloudBase CLI and immutable source-package validation passed.'
} finally {
    Pop-Location
}
