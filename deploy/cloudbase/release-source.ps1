[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$EnvironmentId,
    [Parameter(Mandatory)][string]$CommitSha,
    [Parameter(Mandatory)][string]$ExpectedCurrentVersion,
    [string]$Region = 'ap-shanghai',
    [string]$ServiceName = 'trainpal-demo',
    [string]$AuthPath = '',
    [string]$OutputPath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http

function Get-Sha256Hex {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Value)

    $bytes = [Text.Encoding]::UTF8.GetBytes($Value)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return (
            [BitConverter]::ToString($sha.ComputeHash($bytes)) -replace '-', ''
        ).ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Get-HmacBytes {
    param(
        [Parameter(Mandatory)][byte[]]$Key,
        [Parameter(Mandatory)][string]$Value
    )

    $hmac = [Security.Cryptography.HMACSHA256]::new($Key)
    try {
        return $hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($Value))
    } finally {
        $hmac.Dispose()
    }
}

function Get-PropertyValue {
    param(
        [AllowNull()][object]$Object,
        [Parameter(Mandatory)][string]$Name
    )

    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Invoke-TencentApi {
    param(
        [Parameter(Mandatory)][string]$Service,
        [Parameter(Mandatory)][string]$Version,
        [Parameter(Mandatory)][string]$Action,
        [Parameter(Mandatory)][hashtable]$Payload,
        [Parameter(Mandatory)][object]$Credential
    )

    $hostName = "${Service}.tencentcloudapi.com"
    $timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $date = [DateTimeOffset]::FromUnixTimeSeconds($timestamp).UtcDateTime.ToString(
        'yyyy-MM-dd'
    )
    $body = $Payload | ConvertTo-Json -Compress -Depth 30
    $canonicalHeaders = (
        "content-type:application/json; charset=utf-8`n" +
        "host:${hostName}`n" +
        "x-tc-action:$($Action.ToLowerInvariant())`n"
    )
    $signedHeaders = 'content-type;host;x-tc-action'
    $canonicalRequest = (
        "POST`n/`n`n${canonicalHeaders}`n${signedHeaders}`n$(Get-Sha256Hex $body)"
    )
    $credentialScope = "${date}/${Service}/tc3_request"
    $stringToSign = (
        "TC3-HMAC-SHA256`n${timestamp}`n${credentialScope}`n" +
        (Get-Sha256Hex $canonicalRequest)
    )
    $secretId = [string](Get-PropertyValue $Credential 'tmpSecretId')
    $secretKey = [string](Get-PropertyValue $Credential 'tmpSecretKey')
    $token = [string](Get-PropertyValue $Credential 'tmpToken')
    if (-not $secretId -or -not $secretKey -or -not $token) {
        throw 'CloudBase temporary credential is missing; run tcb login again.'
    }
    $secretDate = Get-HmacBytes (
        [Text.Encoding]::UTF8.GetBytes("TC3${secretKey}")
    ) $date
    $secretService = Get-HmacBytes $secretDate $Service
    $secretSigning = Get-HmacBytes $secretService 'tc3_request'
    $signature = (
        [BitConverter]::ToString((Get-HmacBytes $secretSigning $stringToSign)) -replace '-', ''
    ).ToLowerInvariant()
    $authorization = (
        "TC3-HMAC-SHA256 Credential=${secretId}/${credentialScope}, " +
        "SignedHeaders=${signedHeaders}, Signature=${signature}"
    )
    $headers = @{
        Authorization = $authorization
        Host = $hostName
        'X-TC-Action' = $Action
        'X-TC-Timestamp' = [string]$timestamp
        'X-TC-Version' = $Version
        'X-TC-Region' = $Region
        'X-TC-Token' = $token
    }
    $result = Invoke-RestMethod `
        -Uri "https://${hostName}" `
        -Method Post `
        -Headers $headers `
        -ContentType 'application/json; charset=utf-8' `
        -Body $body
    $response = Get-PropertyValue $result 'Response'
    $apiError = Get-PropertyValue $response 'Error'
    if ($null -ne $apiError) {
        $code = [string](Get-PropertyValue $apiError 'Code')
        throw "Tencent API ${Action} failed with code ${code}."
    }
    return $response
}

function Convert-EnvironmentParameters {
    param([AllowNull()][object]$EnvironmentParameters)

    if ($null -eq $EnvironmentParameters) {
        throw 'CloudBase service has no environment parameters.'
    }
    $parsed = $EnvironmentParameters
    if ($EnvironmentParameters -is [string]) {
        if (-not $EnvironmentParameters.Trim()) {
            throw 'CloudBase service has empty environment parameters.'
        }
        $parsed = $EnvironmentParameters | ConvertFrom-Json
    }
    $values = @{}
    if ($parsed -is [System.Array]) {
        foreach ($item in $parsed) {
            $name = [string](Get-PropertyValue $item 'Name')
            if ($name) {
                $values[$name] = [string](Get-PropertyValue $item 'Value')
            }
        }
    } else {
        foreach ($property in $parsed.PSObject.Properties) {
            $values[$property.Name] = [string]$property.Value
        }
    }
    return $values
}

function Send-SourcePackage {
    param(
        [Parameter(Mandatory)][string]$ArchivePath,
        [Parameter(Mandatory)][string]$UploadUrl,
        [AllowNull()][object[]]$UploadHeaders
    )

    $client = [Net.Http.HttpClient]::new()
    $stream = [IO.File]::OpenRead($ArchivePath)
    $content = [Net.Http.StreamContent]::new($stream)
    $request = [Net.Http.HttpRequestMessage]::new(
        [Net.Http.HttpMethod]::Put,
        $UploadUrl
    )
    $request.Content = $content
    try {
        foreach ($item in @($UploadHeaders)) {
            $name = [string](Get-PropertyValue $item 'Key')
            $value = [string](Get-PropertyValue $item 'Value')
            if (-not $name) { continue }
            if (-not $request.Headers.TryAddWithoutValidation($name, $value)) {
                if (-not $content.Headers.TryAddWithoutValidation($name, $value)) {
                    throw "CloudBase package upload header is unsupported: ${name}."
                }
            }
        }
        $response = $client.SendAsync($request).GetAwaiter().GetResult()
        if (-not $response.IsSuccessStatusCode) {
            throw "CloudBase source package upload failed with HTTP $([int]$response.StatusCode)."
        }
    } finally {
        $request.Dispose()
        $client.Dispose()
    }
}

if ($EnvironmentId -notmatch '^[a-z0-9-]{3,64}$') {
    throw 'EnvironmentId has an invalid format.'
}
if ($CommitSha -notmatch '^[0-9a-f]{40}$') {
    throw 'CommitSha must be a full lowercase Git SHA.'
}
if ($ExpectedCurrentVersion -notmatch '^[a-zA-Z0-9_-]{3,80}$') {
    throw 'ExpectedCurrentVersion has an invalid format.'
}
if (-not $AuthPath) {
    $userProfile = [Environment]::GetFolderPath('UserProfile')
    $AuthPath = Join-Path $userProfile '.config\.cloudbase\auth.json'
}

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$headSha = (& git -C $repositoryRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $headSha -ne $CommitSha) {
    throw 'CommitSha must match the checked-out HEAD.'
}
& git -C $repositoryRoot diff --quiet
if ($LASTEXITCODE -ne 0) { throw 'Working tree must be clean before release.' }
& git -C $repositoryRoot diff --cached --quiet
if ($LASTEXITCODE -ne 0) { throw 'Git index must be clean before release.' }

$resolvedAuthPath = (Resolve-Path -LiteralPath $AuthPath).Path
$auth = Get-Content -Raw -LiteralPath $resolvedAuthPath | ConvertFrom-Json
$credential = Get-PropertyValue $auth 'credential'
if ($null -eq $credential) {
    throw 'CloudBase auth file does not contain a credential; run tcb login again.'
}

$detail = Invoke-TencentApi `
    -Service 'tcbr' `
    -Version '2022-02-17' `
    -Action 'DescribeCloudRunServerDetail' `
    -Payload @{ EnvId = $EnvironmentId; ServerName = $ServiceName } `
    -Credential $credential
$baseInfo = Get-PropertyValue $detail 'BaseInfo'
$serverConfig = Get-PropertyValue $detail 'ServerConfig'
$onlineVersions = @(
    Get-PropertyValue $detail 'OnlineVersionInfos' |
        ForEach-Object { [string](Get-PropertyValue $_ 'VersionName') }
)
if ($onlineVersions -notcontains $ExpectedCurrentVersion) {
    throw 'The expected rollback version is not the current online version.'
}
$accessTypes = @((Get-PropertyValue $baseInfo 'AccessTypes'))
if ($accessTypes.Count -ne 1 -or [string]$accessTypes[0] -ne 'OA') {
    throw 'Source release requires the service to remain OA-only.'
}

$environment = Convert-EnvironmentParameters (Get-PropertyValue $serverConfig 'EnvParams')
$foundation = Get-Content -Raw -LiteralPath (
    Join-Path $PSScriptRoot 'foundation-plan.json'
) | ConvertFrom-Json
$requiredKeys = @($foundation.required_secret_environment_keys) + @(
    $foundation.required_nonsecret_environment_keys
)
foreach ($key in $requiredKeys) {
    if (-not $environment.ContainsKey([string]$key)) {
        throw "Required production environment key is missing: ${key}."
    }
}
foreach ($key in @($foundation.required_secret_environment_keys)) {
    if (-not [string]$environment[[string]$key]) {
        throw "Required production secret is empty: ${key}."
    }
}

$overrides = [ordered]@{
    APP_ENV = 'production'
    ANALYSIS_PROVIDER = 'cloud'
    LOCAL_UPLOAD_ENABLED = 'true'
    LOCAL_ANALYSIS_MAX_SECONDS = '300'
    LOCAL_UPLOAD_MAX_BYTES = '268435456'
    ANALYSIS_EVIDENCE_TIMEOUT_SECONDS = '170'
    ANALYSIS_CHUNK_TIMEOUT_SECONDS = '20'
    ANALYSIS_VISUAL_CHUNK_SECONDS = '60'
    ANALYSIS_VISUAL_OVERLAP_SECONDS = '10'
    RUN_TIMEOUT_SECONDS = '180'
    PUBLIC_ANALYSIS_CONCURRENCY = '0'
    JUDGE_ANALYSIS_CONCURRENCY = '3'
    TRUSTED_PROXY_CIDRS = ''
    IMAGEIO_FFMPEG_EXE = '/opt/trainpal/ffmpeg/bin/ffmpeg'
    FFMPEG_BUILD_RECEIPT_PATH = '/opt/trainpal/ffmpeg/receipt.json'
    WEB_STATIC_ROOT = '/workspace/apps/web/dist'
}
foreach ($entry in $overrides.GetEnumerator()) {
    $environment[$entry.Key] = $entry.Value
}
$orderedEnvironment = [ordered]@{}
foreach ($key in @($environment.Keys | Sort-Object)) {
    $orderedEnvironment[$key] = $environment[$key]
}
$environmentJson = $orderedEnvironment | ConvertTo-Json -Compress -Depth 5

$workDirectory = Join-Path (
    [IO.Path]::GetTempPath()
) ("trainpal-cloudbase-release-{0}" -f [Guid]::NewGuid().ToString('N'))
try {
    New-Item -ItemType Directory -Path $workDirectory | Out-Null
    $archivePath = Join-Path $workDirectory 'source.zip'
    & git -C $repositoryRoot archive --format=zip --output=$archivePath $CommitSha
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $archivePath)) {
        throw 'Could not create the immutable Git source archive.'
    }
    $sourceSha256 = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()

    $build = Invoke-TencentApi `
        -Service 'tcb' `
        -Version '2018-06-08' `
        -Action 'DescribeCloudBaseBuildService' `
        -Payload @{ EnvId = $EnvironmentId; ServiceName = $ServiceName } `
        -Credential $credential
    $uploadUrl = [string](Get-PropertyValue $build 'UploadUrl')
    $packageName = [string](Get-PropertyValue $build 'PackageName')
    $packageVersion = [string](Get-PropertyValue $build 'PackageVersion')
    if (-not $uploadUrl -or -not $packageName -or -not $packageVersion) {
        throw 'CloudBase did not return a complete source upload contract.'
    }
    Send-SourcePackage `
        -ArchivePath $archivePath `
        -UploadUrl $uploadUrl `
        -UploadHeaders @(Get-PropertyValue $build 'UploadHeaders')

    $items = @(
        @{ Key = 'CpuSpecs'; FloatValue = 2.0 },
        @{ Key = 'MemSpecs'; FloatValue = 4.0 },
        @{ Key = 'MinNum'; IntValue = 0 },
        @{ Key = 'MaxNum'; IntValue = 1 },
        @{ Key = 'Port'; IntValue = 8000 },
        @{ Key = 'AccessTypes'; ArrayValue = @('OA') },
        @{ Key = 'EnvParam'; Value = $environmentJson },
        @{ Key = 'BuildDir'; Value = '.' },
        @{ Key = 'Dockerfile'; Value = 'Dockerfile' },
        @{ Key = 'HasDockerfile'; BoolValue = $true }
    )
    $release = Invoke-TencentApi `
        -Service 'tcbr' `
        -Version '2022-02-17' `
        -Action 'UpdateCloudRunServer' `
        -Payload @{
            EnvId = $EnvironmentId
            ServerName = $ServiceName
            DeployInfo = @{
                DeployType = 'package'
                PackageName = $packageName
                PackageVersion = $packageVersion
                ReleaseType = 'GRAY'
                DeployRemark = "git:$($CommitSha.Substring(0, 12))"
            }
            Items = $items
        } `
        -Credential $credential

    $receipt = [ordered]@{
        schema_version = 1
        service = $ServiceName
        region = $Region
        commit_sha = $CommitSha
        source_sha256 = $sourceSha256
        previous_version = $ExpectedCurrentVersion
        release_type = 'GRAY'
        package_version = $packageVersion
        environment_key_names = @($orderedEnvironment.Keys)
        request_id = [string](Get-PropertyValue $release 'RequestId')
    }
    $json = $receipt | ConvertTo-Json -Depth 10
    if ($OutputPath) {
        $parent = Split-Path -Parent $OutputPath
        if ($parent) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        Set-Content -LiteralPath $OutputPath -Value $json -Encoding utf8NoBOM
    }
    $json
} finally {
    if (Test-Path -LiteralPath $workDirectory) {
        $resolvedWorkDirectory = (Resolve-Path -LiteralPath $workDirectory).Path
        $resolvedTempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        if (-not $resolvedWorkDirectory.StartsWith(
            $resolvedTempRoot,
            [StringComparison]::OrdinalIgnoreCase
        )) {
            throw 'Refusing to remove a release directory outside the system temp root.'
        }
        Remove-Item -LiteralPath $resolvedWorkDirectory -Recurse -Force
    }
}
