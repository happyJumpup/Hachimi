[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$EnvironmentId,
    [Parameter(Mandatory)][string]$ExpectedStableVersion,
    [Parameter(Mandatory)][string]$ExpectedCandidateCommitSha,
    [Parameter(Mandatory)][ValidateSet('enable', 'promote', 'restore')][string]$Mode,
    [string]$RoutingHeaderName = '',
    [string]$RoutingHeaderValue = '',
    [string]$Region = 'ap-shanghai',
    [string]$ServiceName = 'trainpal-demo',
    [string]$AuthPath = '',
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

function Convert-ExactTrafficRatio {
    param([Parameter(Mandatory)][AllowNull()][object]$Value)

    $isIntegerValue = (
        $Value -is [byte] -or
        $Value -is [sbyte] -or
        $Value -is [int16] -or
        $Value -is [uint16] -or
        $Value -is [int32] -or
        $Value -is [uint32] -or
        $Value -is [int64] -or
        $Value -is [uint64]
    )
    if ($Value -isnot [string] -and -not $isIntegerValue) {
        throw 'CloudBase returned a non-integer traffic ratio.'
    }
    $text = [string]$Value
    if ($text -notmatch '^(?:0|[1-9][0-9]?|100)$') {
        throw 'CloudBase returned an invalid traffic ratio.'
    }
    return [int]::Parse($text, [Globalization.CultureInfo]::InvariantCulture)
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

if ($EnvironmentId -notmatch '^[a-z0-9-]{3,64}$') {
    throw 'EnvironmentId has an invalid format.'
}
if ($ExpectedStableVersion -notmatch '^[A-Za-z0-9_-]{3,80}$') {
    throw 'ExpectedStableVersion has an invalid format.'
}
if ($ExpectedCandidateCommitSha -notmatch '^[0-9a-f]{40}$') {
    throw 'ExpectedCandidateCommitSha must be a full lowercase Git SHA.'
}
if ($Mode -eq 'enable' -and (-not $RoutingHeaderName -or -not $RoutingHeaderValue)) {
    throw 'Enabling a canary route requires a complete routing header pair.'
}
if ($Mode -ne 'enable' -and ($RoutingHeaderName -or $RoutingHeaderValue)) {
    throw 'FLOW route modes do not accept a routing header pair.'
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

$orderResponse = Invoke-TencentApi `
    -Service 'tcbr' `
    -Version '2022-02-17' `
    -Action 'DescribeReleaseOrder' `
    -Payload @{ EnvId = $EnvironmentId; ServerName = $ServiceName } `
    -Credential $credential
$order = Get-PropertyValue $orderResponse 'ReleaseOrderInfo'
$stable = Get-PropertyValue $order 'CurrentVersion'
$candidate = Get-PropertyValue $order 'ReleaseVersion'
$stableVersion = [string](Get-PropertyValue $stable 'VersionName')
$candidateVersion = [string](Get-PropertyValue $candidate 'VersionName')
if ($stableVersion -ne $ExpectedStableVersion) {
    throw 'The expected stable rollback version is not the current release version.'
}
if (-not $candidateVersion -or $candidateVersion -eq $stableVersion) {
    throw 'CloudBase does not expose a distinct gray candidate version.'
}
$candidateIdentityVerified = $Mode -eq 'restore'
if ($Mode -in @('enable', 'promote')) {
    $candidateDetail = Invoke-TencentApi `
        -Service 'tcbr' `
        -Version '2022-02-17' `
        -Action 'DescribeVersionDetail' `
        -Payload @{
            EnvId = $EnvironmentId
            ServerName = $ServiceName
            VersionName = $candidateVersion
        } `
        -Credential $credential
    $candidateEnvironment = Get-PropertyValue $candidateDetail 'EnvParams'
    if ($candidateEnvironment -is [string]) {
        try {
            $candidateEnvironment = $candidateEnvironment | ConvertFrom-Json
        } catch {
            throw 'The gray candidate environment is not valid JSON.'
        }
    }
    if (
        [string](Get-PropertyValue $candidateDetail 'Name') -ne $candidateVersion -or
        [string](Get-PropertyValue $candidateEnvironment 'APP_RELEASE_SHA') -ne
            $ExpectedCandidateCommitSha
    ) {
        throw 'The gray candidate does not match the expected Git commit.'
    }
    $candidateIdentityVerified = $true
}
if (
    $Mode -in @('enable', 'promote') -and
    @('normal', 'running') -notcontains [string](Get-PropertyValue $candidate 'Status')
) {
    throw 'The gray candidate is not running.'
}
$serviceDetail = $null
$stableRouteVerifiedBeforeMutation = $false
if ($Mode -in @('enable', 'promote')) {
    $serviceDetail = Invoke-TencentApi `
        -Service 'tcbr' `
        -Version '2022-02-17' `
        -Action 'DescribeCloudRunServerDetail' `
        -Payload @{ EnvId = $EnvironmentId; ServerName = $ServiceName } `
        -Credential $credential
    $onlineVersions = @(Get-PropertyValue $serviceDetail 'OnlineVersionInfos')
    $normalizedOnlineVersions = @(
        foreach ($version in $onlineVersions) {
            [ordered]@{
                VersionName = [string](Get-PropertyValue $version 'VersionName')
                FlowRatio = Convert-ExactTrafficRatio (
                    Get-PropertyValue $version 'FlowRatio'
                )
            }
        }
    )
    $stableRoutes = @(
        $normalizedOnlineVersions | Where-Object {
            [string]$_.VersionName -eq $ExpectedStableVersion
        }
    )
    $candidateRoutes = @(
        $normalizedOnlineVersions | Where-Object {
            [string]$_.VersionName -eq $candidateVersion
        }
    )
    if ($Mode -eq 'promote') {
        if (
            $normalizedOnlineVersions.Count -ne 2 -or
            $stableRoutes.Count -ne 1 -or
            $candidateRoutes.Count -ne 1 -or
            [int]$stableRoutes[0].FlowRatio -ne 100 -or
            [int]$candidateRoutes[0].FlowRatio -ne 0
        ) {
            throw 'Promotion can only start from exact stable 100 and candidate 0.'
        }
    } else {
        $positiveRoutes = @(
            $normalizedOnlineVersions | Where-Object {
                [int]$_.FlowRatio -gt 0
            }
        )
        $positiveStableRoutes = @(
            $positiveRoutes | Where-Object {
                [string]$_.VersionName -eq $ExpectedStableVersion
            }
        )
        if (
            $positiveRoutes.Count -ne 1 -or
            $positiveStableRoutes.Count -ne 1 -or
            [int]$positiveStableRoutes[0].FlowRatio -ne 100
        ) {
            throw 'Private header routing can only start from stable 100 and candidate 0.'
        }
    }
    $stableRouteVerifiedBeforeMutation = $true
}

$policyInput = [ordered]@{
    mode = $Mode
    stable_version = $stableVersion
    candidate_version = $candidateVersion
}
if ($Mode -eq 'enable') {
    $policyInput['routing_header_name'] = $RoutingHeaderName
    $policyInput['routing_header_value'] = $RoutingHeaderValue
}
$policyJson = (
    $policyInput | ConvertTo-Json -Compress -Depth 8
) | & python (Join-Path $PSScriptRoot 'canary-route-policy.py')
if ($LASTEXITCODE -ne 0) {
    throw 'Canary route policy validation failed.'
}
try {
    $policy = $policyJson | ConvertFrom-Json
} catch {
    throw 'Canary route policy returned invalid JSON.'
}
$versionFlowItems = @(
    foreach ($item in @($policy.VersionFlowItems)) {
        $value = @{
            VersionName = [string](Get-PropertyValue $item 'VersionName')
            IsDefaultPriority = [bool](Get-PropertyValue $item 'IsDefaultPriority')
            Priority = [int](Get-PropertyValue $item 'Priority')
        }
        $flowRatio = Get-PropertyValue $item 'FlowRatio'
        if ($null -ne $flowRatio) { $value.FlowRatio = [int]$flowRatio }
        $urlParam = Get-PropertyValue $item 'UrlParam'
        if ($null -ne $urlParam) {
            $value.UrlParam = @{
                Key = [string](Get-PropertyValue $urlParam 'Key')
                Value = [string](Get-PropertyValue $urlParam 'Value')
            }
        }
        $value
    }
)
$release = Invoke-TencentApi `
    -Service 'tcbr' `
    -Version '2022-02-17' `
    -Action 'ReleaseGray' `
    -Payload @{
        EnvId = $EnvironmentId
        ServerName = $ServiceName
        GrayType = [string]$policy.GrayType
        TrafficType = [string]$policy.TrafficType
        GrayFlowRatio = [int]$policy.GrayFlowRatio
        VersionFlowItems = $versionFlowItems
        OperatorRemark = "git:$($ExpectedCandidateCommitSha.Substring(0, 12)):${Mode}"
    } `
    -Credential $credential

$verifiedState = $null
for ($attempt = 1; $attempt -le 30; $attempt++) {
    $observedResponse = Invoke-TencentApi `
        -Service 'tcbr' `
        -Version '2022-02-17' `
        -Action 'DescribeReleaseOrder' `
        -Payload @{ EnvId = $EnvironmentId; ServerName = $ServiceName } `
        -Credential $credential
    $observedOrder = Get-PropertyValue $observedResponse 'ReleaseOrderInfo'
    $observedDetail = Invoke-TencentApi `
        -Service 'tcbr' `
        -Version '2022-02-17' `
        -Action 'DescribeCloudRunServerDetail' `
        -Payload @{ EnvId = $EnvironmentId; ServerName = $ServiceName } `
        -Credential $credential
    $observedOnlineVersions = @(
        foreach ($version in @(Get-PropertyValue $observedDetail 'OnlineVersionInfos')) {
            [ordered]@{
                VersionName = [string](Get-PropertyValue $version 'VersionName')
                FlowRatio = Convert-ExactTrafficRatio (
                    Get-PropertyValue $version 'FlowRatio'
                )
            }
        }
    )
    $stateInput = [ordered]@{
        mode = $Mode
        stable_version = $ExpectedStableVersion
        candidate_commit_sha = $ExpectedCandidateCommitSha
        candidate_identity_verified = $candidateIdentityVerified
        stable_route_verified_before_mutation = $stableRouteVerifiedBeforeMutation
        online_versions = $observedOnlineVersions
        release_order = $observedOrder
    }
    if ($Mode -eq 'enable') {
        $stateInput['routing_header_name'] = $RoutingHeaderName
        $stateInput['routing_header_value'] = $RoutingHeaderValue
    }
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $stateJson = (
            $stateInput | ConvertTo-Json -Compress -Depth 30
        ) | & python (Join-Path $PSScriptRoot 'canary-route-state.py') 2>$null
        $stateExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($stateExitCode -eq 0) {
        try {
            $verifiedState = $stateJson | ConvertFrom-Json
        } catch {
            throw 'Canary route state verifier returned invalid JSON.'
        }
        break
    }
    if ($attempt -lt 30) {
        Start-Sleep -Seconds 1
    }
}
if ($null -eq $verifiedState) {
    throw 'CloudBase did not reach the verified canary route state.'
}

$receipt = [ordered]@{
    schema_version = 1
    service = $ServiceName
    mode = $Mode
    stable_version = $stableVersion
    candidate_version = $candidateVersion
    traffic_type = [string]$verifiedState.traffic_type
    stable_flow_ratio = [int]$verifiedState.stable_flow_ratio
    candidate_flow_ratio = [int]$verifiedState.candidate_flow_ratio
    routing_header_name = $verifiedState.routing_header_name
    routing_header_value_sha256 = $verifiedState.routing_header_value_sha256
    request_id = [string](Get-PropertyValue $release 'RequestId')
}
$json = $receipt | ConvertTo-Json -Depth 10
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
