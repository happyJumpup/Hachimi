[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string]$CommitSha,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$outputPath = [System.IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $outputPath) {
    throw "Source package output already exists: $outputPath"
}

Push-Location $repositoryRoot
try {
    $resolvedCommit = (& git rev-parse "$CommitSha^{commit}").Trim()
    if ($LASTEXITCODE -ne 0 -or $resolvedCommit -ne $CommitSha) {
        throw 'Commit SHA does not resolve to the requested immutable commit.'
    }

    $packageParent = Split-Path -Parent $outputPath
    New-Item -ItemType Directory -Path $packageParent -Force | Out-Null
    New-Item -ItemType Directory -Path $outputPath | Out-Null
    $archivePath = Join-Path $packageParent ("trainpal-source-{0}.zip" -f ([guid]::NewGuid().ToString('N')))
    try {
        & git archive --format=zip --output=$archivePath $resolvedCommit
        if ($LASTEXITCODE -ne 0) {
            throw 'Could not create the immutable Git source archive.'
        }
        Expand-Archive -LiteralPath $archivePath -DestinationPath $outputPath
    } finally {
        if (Test-Path -LiteralPath $archivePath) {
            try {
                Remove-Item -LiteralPath $archivePath -Force -ErrorAction Stop
            } catch {
                Write-Warning 'Temporary immutable source archive cleanup failed.'
            }
        }
    }

    $forbiddenMediaExtensions = @('.mp4', '.mov', '.mkv', '.wav', '.mp3', '.ogg', '.webm')
    $forbiddenSecretExtensions = @('.key', '.pem', '.p12', '.pfx')
    $forbiddenFiles = @(
        Get-ChildItem -LiteralPath $outputPath -Recurse -File | Where-Object {
            $name = $_.Name.ToLowerInvariant()
            $extension = $_.Extension.ToLowerInvariant()
            ($name -eq '.env') -or
            ($name.StartsWith('.env.') -and $name -ne '.env.example') -or
            ($forbiddenMediaExtensions -contains $extension) -or
            ($forbiddenSecretExtensions -contains $extension)
        }
    )
    if ($forbiddenFiles.Count -gt 0) {
        throw 'Immutable source package contains a forbidden secret or media path.'
    }

    $metadata = [ordered]@{
        schema_version = 1
        commit_sha = $resolvedCommit
    } | ConvertTo-Json -Compress
    $metadataPath = Join-Path $outputPath '.trainpal-build-metadata.json'
    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($metadataPath, $metadata, $utf8WithoutBom)

    Write-Output $outputPath
} catch {
    if (Test-Path -LiteralPath $outputPath) {
        Remove-Item -LiteralPath $outputPath -Recurse -Force
    }
    throw
} finally {
    Pop-Location
}
