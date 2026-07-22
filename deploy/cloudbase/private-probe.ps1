[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$EnvironmentId,
    [string]$ServiceName = 'trainpal-demo',
    [string]$AuthPath = '',
    [ValidateRange(5, 180)][int]$TimeoutSeconds = 45,
    [ValidateRange(1, 5)][int]$ColdStartAttempts = 3,
    [string]$OutputPath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

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

function New-Tc3Authorization {
    param(
        [Parameter(Mandatory)][string]$HostName,
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][object]$Credential
    )

    $secretId = [string](Get-PropertyValue $Credential 'tmpSecretId')
    $secretKey = [string](Get-PropertyValue $Credential 'tmpSecretKey')
    $token = [string](Get-PropertyValue $Credential 'tmpToken')
    if (-not $secretId -or -not $secretKey -or -not $token) {
        throw 'CloudBase temporary credential is missing; run tcb login again.'
    }

    $timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $date = [DateTimeOffset]::FromUnixTimeSeconds($timestamp).UtcDateTime.ToString(
        'yyyy-MM-dd'
    )
    $canonicalHeaders = "content-type:application/json`nhost:${HostName}`n"
    $signedHeaders = 'content-type;host'
    $canonicalRequest = (
        "GET`n${Path}`n`n${canonicalHeaders}`n${signedHeaders}`n" +
        (Get-Sha256Hex '')
    )
    $credentialScope = "${date}/tcb/tc3_request"
    $stringToSign = (
        "TC3-HMAC-SHA256`n${timestamp}`n${credentialScope}`n" +
        (Get-Sha256Hex $canonicalRequest)
    )
    $secretDate = Get-HmacBytes (
        [Text.Encoding]::UTF8.GetBytes("TC3${secretKey}")
    ) $date
    $secretService = Get-HmacBytes $secretDate 'tcb'
    $secretSigning = Get-HmacBytes $secretService 'tc3_request'
    $signature = (
        [BitConverter]::ToString((Get-HmacBytes $secretSigning $stringToSign)) -replace '-', ''
    ).ToLowerInvariant()

    return (
        "TC3-HMAC-SHA256 Credential=${secretId}/${credentialScope}, " +
        "SignedHeaders=${signedHeaders}, Signature=${signature}, " +
        "Timestamp=${timestamp}, Token=${token}"
    )
}

function Read-WebErrorBody {
    param([Parameter(Mandatory)][object]$Response)

    try {
        $stream = $Response.GetResponseStream()
        if ($null -eq $stream) { return $null }
        $reader = [IO.StreamReader]::new($stream)
        try {
            $text = $reader.ReadToEnd()
        } finally {
            $reader.Dispose()
        }
        if (-not $text) { return $null }
        return $text | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Invoke-PrivateGet {
    param(
        [Parameter(Mandatory)][string]$HostName,
        [Parameter(Mandatory)][string]$ApplicationPath,
        [Parameter(Mandatory)][object]$Credential
    )

    $gatewayPath = "/v1/cloudrun/${ServiceName}${ApplicationPath}"
    for ($attempt = 1; $attempt -le $ColdStartAttempts; $attempt += 1) {
        $authorization = New-Tc3Authorization $HostName $gatewayPath $Credential
        try {
            $response = Invoke-WebRequest `
                -UseBasicParsing `
                -Uri "https://${HostName}${gatewayPath}" `
                -Method Get `
                -Headers @{
                    Authorization = $authorization
                    'Content-Type' = 'application/json'
                } `
                -TimeoutSec $TimeoutSeconds
            $body = $response.Content | ConvertFrom-Json
            return [pscustomobject]@{
                http_status = [int]$response.StatusCode
                body = $body
            }
        } catch [System.Net.WebException] {
            $response = $_.Exception.Response
            $status = if ($null -ne $response) {
                [int]$response.StatusCode
            } else {
                0
            }
            $body = if ($null -ne $response) {
                Read-WebErrorBody $response
            } else {
                $null
            }
            $gatewayCode = [string](Get-PropertyValue $body 'code')
            if (
                $gatewayCode -eq 'SERVICE_NOT_READY' -and
                $attempt -lt $ColdStartAttempts
            ) {
                Start-Sleep -Seconds ([Math]::Min(8, [Math]::Pow(2, $attempt)))
                continue
            }
            return [pscustomobject]@{
                http_status = $status
                body = $body
            }
        }
    }
    throw 'CloudBase private probe exhausted its retry budget.'
}

if ($EnvironmentId -notmatch '^[a-z0-9-]{3,64}$') {
    throw 'EnvironmentId has an invalid format.'
}
if ($ServiceName -notmatch '^[a-zA-Z][a-zA-Z0-9_-]{1,59}$') {
    throw 'ServiceName has an invalid format.'
}
if (-not $AuthPath) {
    $userProfile = [Environment]::GetFolderPath('UserProfile')
    $AuthPath = Join-Path $userProfile '.config\.cloudbase\auth.json'
}

$resolvedAuthPath = (Resolve-Path -LiteralPath $AuthPath).Path
$auth = Get-Content -Raw -LiteralPath $resolvedAuthPath | ConvertFrom-Json
$credential = Get-PropertyValue $auth 'credential'
if ($null -eq $credential) {
    throw 'CloudBase auth file does not contain a credential; run tcb login again.'
}

$hostName = "${EnvironmentId}.api.tcloudbasegateway.com"
$health = Invoke-PrivateGet $hostName '/api/v1/health' $credential
$healthStatus = [string](Get-PropertyValue $health.body 'status')
if ($health.http_status -ne 200 -or $healthStatus -ne 'ok') {
    throw "CloudBase private health probe failed with HTTP $($health.http_status)."
}

$ready = Invoke-PrivateGet $hostName '/api/v1/ready' $credential
$readyStatus = [string](Get-PropertyValue $ready.body 'status')
if ($ready.http_status -ne 200 -or $readyStatus -ne 'ready') {
    $failureCode = [string](Get-PropertyValue $ready.body 'code')
    if (-not $failureCode) { $failureCode = 'unknown' }
    throw (
        "CloudBase private readiness probe failed with HTTP $($ready.http_status) " +
        "and code ${failureCode}."
    )
}

$receipt = [ordered]@{
    schema_version = 1
    service = $ServiceName
    access = 'signed-private-http-api'
    probes = @(
        [ordered]@{
            path = '/api/v1/health'
            http_status = $health.http_status
            application_status = $healthStatus
        },
        [ordered]@{
            path = '/api/v1/ready'
            http_status = $ready.http_status
            application_status = $readyStatus
        }
    )
}
$json = $receipt | ConvertTo-Json -Depth 8
if ($OutputPath) {
    $parent = Split-Path -Parent $OutputPath
    if ($parent) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    Set-Content -LiteralPath $OutputPath -Value $json -Encoding utf8
}
$json
