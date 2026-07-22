[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_-]+$')]
    [string]$EnvironmentId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string]$CommitSha,

    [ValidatePattern('^[a-z][a-z0-9-]{0,62}$')]
    [string]$ServiceName = 'trainpal-demo',

    [ValidateSet('ap-shanghai')]
    [string]$Region = 'ap-shanghai'
)

$ErrorActionPreference = 'Stop'

$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("trainpal-cloudbase-{0}" -f ([guid]::NewGuid().ToString('N')))
$sourceDirectory = Join-Path $temporaryRoot 'source'
New-Item -ItemType Directory -Path $temporaryRoot | Out-Null

try {
    $packager = Join-Path $PSScriptRoot 'package-source.ps1'
    & $packager -CommitSha $CommitSha -OutputDirectory $sourceDirectory | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'CloudBase source package preparation failed.'
    }

    Write-Output "Deploying immutable source commit $CommitSha to service $ServiceName."
    & npx --yes --package '@cloudbase/cli@3.6.4' tcb `
        --env-id $EnvironmentId `
        --region $Region `
        cloudrun deploy `
        --serviceName $ServiceName `
        --port 8000 `
        --source $sourceDirectory
    if ($LASTEXITCODE -ne 0) {
        throw 'CloudBase source deployment failed.'
    }
} finally {
    $resolvedTemporaryRoot = [System.IO.Path]::GetFullPath($temporaryRoot)
    $systemTemporaryRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    if (
        $resolvedTemporaryRoot.StartsWith($systemTemporaryRoot, [System.StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $resolvedTemporaryRoot).StartsWith('trainpal-cloudbase-', [System.StringComparison]::Ordinal)
    ) {
        try {
            Remove-Item -LiteralPath $resolvedTemporaryRoot -Recurse -Force -ErrorAction Stop
        } catch {
            Write-Warning 'Temporary CloudBase source package cleanup failed.'
        }
    }
}
