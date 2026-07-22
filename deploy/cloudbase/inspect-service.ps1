[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$EnvironmentId,
    [string]$Region = 'ap-shanghai',
    [string]$ServiceName = 'trainpal-demo',
    [string]$AuthPath = '',
    [string]$CommitSha = '',
    [string]$OutputPath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-Sha256Hex {
    param([Parameter(Mandatory)][string]$Value)
    $bytes = [Text.Encoding]::UTF8.GetBytes($Value)
    try {
        $sha = [Security.Cryptography.SHA256]::Create()
        return ([BitConverter]::ToString($sha.ComputeHash($bytes)) -replace '-', '').ToLowerInvariant()
    } finally {
        if ($null -ne $sha) { $sha.Dispose() }
    }
}

function Get-HmacBytes {
    param(
        [Parameter(Mandatory)][byte[]]$Key,
        [Parameter(Mandatory)][string]$Value
    )
    try {
        $hmac = [Security.Cryptography.HMACSHA256]::new($Key)
        return $hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($Value))
    } finally {
        if ($null -ne $hmac) { $hmac.Dispose() }
    }
}

function Read-Property {
    param(
        [AllowNull()][object]$Object,
        [Parameter(Mandatory)][string]$Name
    )
    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Invoke-TcbrReadApi {
    param(
        [Parameter(Mandatory)][string]$Action,
        [Parameter(Mandatory)][hashtable]$Payload,
        [Parameter(Mandatory)][object]$Credential
    )
    $hostName = 'tcbr.tencentcloudapi.com'
    $service = 'tcbr'
    $version = '2022-02-17'
    $timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $date = [DateTimeOffset]::FromUnixTimeSeconds($timestamp).UtcDateTime.ToString('yyyy-MM-dd')
    $body = $Payload | ConvertTo-Json -Compress -Depth 20
    $canonicalHeaders = (
        "content-type:application/json; charset=utf-8`n" +
        "host:${hostName}`n" +
        "x-tc-action:$($Action.ToLowerInvariant())`n"
    )
    $signedHeaders = 'content-type;host;x-tc-action'
    $canonicalRequest = (
        "POST`n/`n`n${canonicalHeaders}`n${signedHeaders}`n$(Get-Sha256Hex $body)"
    )
    $credentialScope = "${date}/${service}/tc3_request"
    $stringToSign = (
        "TC3-HMAC-SHA256`n${timestamp}`n${credentialScope}`n" +
        (Get-Sha256Hex $canonicalRequest)
    )
    $secretId = [string](Read-Property $Credential 'tmpSecretId')
    $secretKey = [string](Read-Property $Credential 'tmpSecretKey')
    $token = [string](Read-Property $Credential 'tmpToken')
    if (-not $secretId -or -not $secretKey -or -not $token) {
        throw 'CloudBase temporary credential is missing; run tcb login again.'
    }
    $secretDate = Get-HmacBytes ([Text.Encoding]::UTF8.GetBytes("TC3${secretKey}")) $date
    $secretService = Get-HmacBytes $secretDate $service
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
        'X-TC-Version' = $version
        'X-TC-Region' = $Region
        'X-TC-Token' = $token
    }
    $request = @{
        Uri = "https://${hostName}"
        Method = 'Post'
        Headers = $headers
        ContentType = 'application/json; charset=utf-8'
        Body = $body
    }
    $result = Invoke-RestMethod @request
    $response = Read-Property $result 'Response'
    $apiError = Read-Property $response 'Error'
    if ($null -ne $apiError) {
        $code = [string](Read-Property $apiError 'Code')
        throw "CloudBase read-only API ${Action} failed with code ${code}."
    }
    return $response
}

function Get-EnvironmentKeyNames {
    param([AllowNull()][object]$EnvironmentParameters)
    if ($null -eq $EnvironmentParameters) { return @() }
    $parsed = $EnvironmentParameters
    if ($EnvironmentParameters -is [string]) {
        if (-not $EnvironmentParameters.Trim()) { return @() }
        try {
            $parsed = $EnvironmentParameters | ConvertFrom-Json
        } catch {
            throw 'CloudBase environment parameters are not valid JSON.'
        }
    }
    if ($parsed -is [System.Array]) {
        return @(
            $parsed | ForEach-Object {
                $name = Read-Property $_ 'Name'
                if ($name) { [string]$name }
            }
        ) | Sort-Object -Unique
    }
    return @(
        $parsed.PSObject.Properties | ForEach-Object { $_.Name }
    ) | Sort-Object -Unique
}

if ($CommitSha -and $CommitSha -notmatch '^[0-9a-f]{40}$') {
    throw 'CommitSha must be a full lowercase Git SHA.'
}
if (-not $AuthPath) {
    $userProfile = [Environment]::GetFolderPath('UserProfile')
    $AuthPath = Join-Path $userProfile '.config\.cloudbase\auth.json'
}
$resolvedAuthPath = (Resolve-Path -LiteralPath $AuthPath).Path
$auth = Get-Content -Raw -LiteralPath $resolvedAuthPath | ConvertFrom-Json
$credential = Read-Property $auth 'credential'
if ($null -eq $credential) {
    throw 'CloudBase auth file does not contain a credential; run tcb login again.'
}

$common = @{ EnvId = $EnvironmentId; ServerName = $ServiceName }
$detail = Invoke-TcbrReadApi 'DescribeCloudRunServerDetail' $common $credential
$deployResponse = Invoke-TcbrReadApi 'DescribeCloudRunDeployRecord' $common $credential
$onlineVersions = @(Read-Property $detail 'OnlineVersionInfos')
$deployRecords = @(Read-Property $deployResponse 'DeployRecords')

$baseInfo = Read-Property $detail 'BaseInfo'
$serverConfig = Read-Property $detail 'ServerConfig'
$publicNetConf = Read-Property $serverConfig 'PublicNetConf'
[string[]]$envKeys = @(Get-EnvironmentKeyNames (Read-Property $serverConfig 'EnvParams'))
$receipt = [ordered]@{
    schema_version = 1
    service = Read-Property $baseInfo 'ServerName'
    versions = @(
        $onlineVersions | ForEach-Object { Read-Property $_ 'VersionName' }
    )
    build_ids = @(
        $deployRecords |
            ForEach-Object { Read-Property $_ 'BuildId' } |
            Where-Object { $null -ne $_ -and [long]$_ -gt 0 }
    )
    commit_sha = if ($CommitSha) { $CommitSha } else { $null }
    resources = [ordered]@{
        cpu = Read-Property $serverConfig 'Cpu'
        memory = Read-Property $serverConfig 'Mem'
        container_port = Read-Property $serverConfig 'Port'
    }
    scaling = [ordered]@{
        minimum_instances = Read-Property $serverConfig 'MinNum'
        maximum_instances = Read-Property $serverConfig 'MaxNum'
    }
    access = [ordered]@{
        types = @((Read-Property $baseInfo 'AccessTypes'))
        public_network_status = Read-Property $publicNetConf 'PublicNetStatus'
    }
    environment_key_names = @($envKeys)
}

$json = $receipt | ConvertTo-Json -Depth 12
if ($OutputPath) {
    $parent = Split-Path -Parent $OutputPath
    if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    [IO.File]::WriteAllText(
        [IO.Path]::GetFullPath($OutputPath),
        $json,
        [Text.UTF8Encoding]::new($false)
    )
}
$json
