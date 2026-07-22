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
    foreach ($requiredOption in @('--env-id', '--serviceName', '--port', '--imageUrl', '--traffic')) {
        $helpText = if ($requiredOption -eq '--env-id') { $globalHelp } else { $deployHelp }
        if (-not $helpText.Contains($requiredOption)) {
            throw "CloudBase CLI 3.6.4 is missing required option: $requiredOption"
        }
    }

    Write-Output 'CloudBase CLI 3.6.4 command-surface validation passed.'
} finally {
    Pop-Location
}
