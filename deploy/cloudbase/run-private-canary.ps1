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
    [ValidateSet('exercise', 'finalize')][string]$Mode = 'exercise',
    [ValidateRange(1, 300)][int]$TimeoutSeconds = 240,
    [ValidateRange(1, 600)][int]$RecoveryTimeoutSeconds = 300
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

    $integerTypes = @(
        [byte], [sbyte], [int16], [uint16], [int32], [uint32], [int64], [uint64]
    )
    $isInteger = $false
    foreach ($integerType in $integerTypes) {
        if ($Value -is $integerType) {
            $isInteger = $true
            break
        }
    }
    if ($Value -isnot [string] -and -not $isInteger) {
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
        [Parameter(Mandatory)][string]$Action,
        [Parameter(Mandatory)][hashtable]$Payload,
        [Parameter(Mandatory)][object]$Credential
    )

    $hostName = 'tcbr.tencentcloudapi.com'
    $service = 'tcbr'
    $version = '2022-02-17'
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
    $credentialScope = "${date}/${service}/tc3_request"
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
    try {
        $result = Invoke-RestMethod `
            -Uri "https://${hostName}" `
            -Method Post `
            -Headers $headers `
            -ContentType 'application/json; charset=utf-8' `
            -Body $body `
            -ErrorAction Stop
    } catch {
        throw "Tencent API ${Action} request failed."
    }
    $response = Get-PropertyValue $result 'Response'
    $apiError = Get-PropertyValue $response 'Error'
    if ($null -ne $apiError) {
        $code = [string](Get-PropertyValue $apiError 'Code')
        throw "Tencent API ${Action} failed with code ${code}."
    }
    return $response
}

function Get-OnlineTraffic {
    param([Parameter(Mandatory)][object]$Credential)

    $response = Invoke-TencentApi `
        -Action 'DescribeCloudRunServerDetail' `
        -Payload @{ EnvId = $EnvironmentId; ServerName = $ServiceName } `
        -Credential $Credential
    return @(
        foreach ($version in @(Get-PropertyValue $response 'OnlineVersionInfos')) {
            [ordered]@{
                VersionName = [string](Get-PropertyValue $version 'VersionName')
                FlowRatio = Convert-ExactTrafficRatio (
                    Get-PropertyValue $version 'FlowRatio'
                )
            }
        }
    )
}

function Test-ExactOnlineTraffic {
    param(
        [Parameter(Mandatory)][object[]]$OnlineVersions,
        [Parameter(Mandatory)][string]$StableVersion,
        [Parameter(Mandatory)][string]$CandidateVersion,
        [Parameter(Mandatory)][ValidateSet('stable', 'candidate')][string]$Target
    )

    $ratios = @{}
    foreach ($version in $OnlineVersions) {
        $name = [string]$version.VersionName
        if (
            -not $name -or
            $name -notin @($StableVersion, $CandidateVersion) -or
            $ratios.ContainsKey($name)
        ) {
            return $false
        }
        $ratios[$name] = [int]$version.FlowRatio
    }
    $stableRatio = if ($ratios.ContainsKey($StableVersion)) {
        [int]$ratios[$StableVersion]
    } else { 0 }
    $candidateRatio = if ($ratios.ContainsKey($CandidateVersion)) {
        [int]$ratios[$CandidateVersion]
    } else { 0 }
    if ($Target -eq 'stable') {
        return $stableRatio -eq 100 -and $candidateRatio -eq 0
    }
    return $stableRatio -eq 0 -and $candidateRatio -eq 100
}

function Wait-ExactOnlineTraffic {
    param(
        [Parameter(Mandatory)][object]$Credential,
        [Parameter(Mandatory)][string]$StableVersion,
        [Parameter(Mandatory)][string]$CandidateVersion,
        [Parameter(Mandatory)][ValidateSet('stable', 'candidate')][string]$Target,
        [Parameter(Mandatory)][int]$WaitSeconds
    )

    $timer = [Diagnostics.Stopwatch]::StartNew()
    do {
        $onlineVersions = @(Get-OnlineTraffic -Credential $Credential)
        if (
            Test-ExactOnlineTraffic `
                -OnlineVersions $onlineVersions `
                -StableVersion $StableVersion `
                -CandidateVersion $CandidateVersion `
                -Target $Target
        ) {
            return
        }
        if ($timer.Elapsed.TotalSeconds -lt $WaitSeconds) {
            Start-Sleep -Seconds 1
        }
    } while ($timer.Elapsed.TotalSeconds -lt $WaitSeconds)
    throw "CloudBase did not converge to verified ${Target} traffic."
}

function Get-ManagementTask {
    param(
        [Parameter(Mandatory)][object]$Credential,
        [Parameter(Mandatory)][long]$TaskId
    )

    return Invoke-TencentApi `
        -Action 'DescribeServerManageTask' `
        -Payload @{
            EnvId = $EnvironmentId
            ServerName = $ServiceName
            TaskId = $TaskId
        } `
        -Credential $Credential
}

function Assert-TaskIdentity {
    param(
        [Parameter(Mandatory)][object]$Task,
        [Parameter(Mandatory)][long]$ExpectedTaskId,
        [Parameter(Mandatory)][string]$ExpectedPreviousVersion,
        [Parameter(Mandatory)][string]$ExpectedVersion
    )

    $taskId = [long](Get-PropertyValue $Task 'Id')
    $taskService = [string](Get-PropertyValue $Task 'ServerName')
    $previousVersion = [string](Get-PropertyValue $Task 'PreVersionName')
    $version = [string](Get-PropertyValue $Task 'VersionName')
    if (
        ($ExpectedTaskId -ne 0 -and $taskId -ne $ExpectedTaskId) -or
        $taskService -ne $ServiceName -or
        $previousVersion -ne $ExpectedPreviousVersion -or
        $version -ne $ExpectedVersion
    ) {
        throw 'CloudBase management task identity did not match the release transaction.'
    }
}

function Wait-ManagementTaskTerminal {
    param(
        [Parameter(Mandatory)][object]$Credential,
        [Parameter(Mandatory)][long]$TaskId,
        [Parameter(Mandatory)][string]$ExpectedPreviousVersion,
        [Parameter(Mandatory)][string]$ExpectedVersion,
        [Parameter(Mandatory)][int]$WaitSeconds
    )

    $timer = [Diagnostics.Stopwatch]::StartNew()
    do {
        $response = Get-ManagementTask -Credential $Credential -TaskId $TaskId
        if ((Get-PropertyValue $response 'IsExist') -eq $true) {
            $task = Get-PropertyValue $response 'Task'
            if ($null -ne $task) {
                $identityMatches = $false
                try {
                    Assert-TaskIdentity `
                        -Task $task `
                        -ExpectedTaskId $TaskId `
                        -ExpectedPreviousVersion $ExpectedPreviousVersion `
                        -ExpectedVersion $ExpectedVersion
                    $identityMatches = $true
                } catch {
                    if ($TaskId -ne 0) { throw }
                }
                if ($identityMatches) {
                    $status = (
                        [string](Get-PropertyValue $task 'Status')
                    ).ToLowerInvariant()
                    if ($status -in @('finished', 'failed')) {
                        return [pscustomobject]@{
                            Status = $status
                            Task = $task
                        }
                    }
                    if ($status -notin @('todo', 'running')) {
                        throw 'CloudBase management task returned an unknown status.'
                    }
                }
            }
        }
        if ($timer.Elapsed.TotalSeconds -lt $WaitSeconds) {
            Start-Sleep -Seconds 1
        }
    } while ($timer.Elapsed.TotalSeconds -lt $WaitSeconds)
    throw 'CloudBase management task did not reach a terminal state.'
}

function Assert-ManagementTaskSucceeded {
    param([Parameter(Mandatory)][object]$TerminalTask)

    if ([string]$TerminalTask.Status -ne 'finished') {
        throw 'CloudBase management task failed.'
    }
    $failReason = [string](Get-PropertyValue $TerminalTask.Task 'FailReason')
    if ($failReason) {
        throw 'CloudBase management task reported a failure reason.'
    }
}

function Wait-PublicCandidate {
    param(
        [Parameter(Mandatory)][Uri]$BaseUri,
        [Parameter(Mandatory)][string]$ExpectedCommitSha,
        [Parameter(Mandatory)][int]$WaitSeconds
    )

    $healthUri = [Uri]::new($BaseUri, '/api/v1/health')
    $readyUri = [Uri]::new($BaseUri, '/api/v1/ready')
    $timer = [Diagnostics.Stopwatch]::StartNew()
    do {
        $health = $null
        try {
            $health = Invoke-RestMethod `
                -Method Get `
                -Uri $healthUri.AbsoluteUri `
                -Headers @{ Accept = 'application/json' } `
                -MaximumRedirection 0 `
                -TimeoutSec ([Math]::Min(30, $WaitSeconds)) `
                -ErrorAction Stop
        } catch {
            $health = $null
        }
        $healthStatus = Get-PropertyValue $health 'status'
        $healthReleaseSha = Get-PropertyValue $health 'release_sha'
        if (
            [string]$healthStatus -eq 'ok' -and
            [string]$healthReleaseSha -ceq $ExpectedCommitSha
        ) {
            $ready = $null
            try {
                $ready = Invoke-RestMethod `
                    -Method Get `
                    -Uri $readyUri.AbsoluteUri `
                    -Headers @{ Accept = 'application/json' } `
                    -MaximumRedirection 0 `
                    -TimeoutSec ([Math]::Min(30, $WaitSeconds)) `
                    -ErrorAction Stop
            } catch {
                $ready = $null
            }
            if ([string](Get-PropertyValue $ready 'status') -eq 'ready') {
                return
            }
        }
        if ($timer.Elapsed.TotalSeconds -lt $WaitSeconds) {
            Start-Sleep -Seconds 1
        }
    } while ($timer.Elapsed.TotalSeconds -lt $WaitSeconds)
    throw 'Public candidate identity or readiness did not converge.'
}

function Write-TransactionReceipt {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][bool]$RouteRestored,
        [Parameter(Mandatory)][bool]$RouteFinalized
    )

    $receipt = [ordered]@{
        service = $ServiceName
        candidate_commit_sha = $ExpectedCandidateCommitSha
        stable_version = $ExpectedStableVersion
        health = 'ok'
        ready = 'ready'
        route_restored = $RouteRestored
        route_finalized = $RouteFinalized
    }
    $json = $receipt | ConvertTo-Json -Depth 5
    $temporaryPath = "${Path}.$([Guid]::NewGuid().ToString('N')).tmp"
    try {
        [IO.File]::WriteAllText(
            $temporaryPath,
            $json,
            [Text.UTF8Encoding]::new($false)
        )
        if (Test-Path -LiteralPath $Path) {
            throw 'The candidate transaction receipt path became unavailable.'
        }
        [IO.File]::Move($temporaryPath, $Path)
    } finally {
        if (Test-Path -LiteralPath $temporaryPath -PathType Leaf) {
            Remove-Item -LiteralPath $temporaryPath -Force
        }
    }
    return $json
}

if ($EnvironmentId -notmatch '^[a-z0-9-]{3,64}$') {
    throw 'EnvironmentId has an invalid format.'
}
if ($ServiceName -notmatch '^[A-Za-z0-9_-]{3,80}$') {
    throw 'ServiceName has an invalid format.'
}
if ($ExpectedStableVersion -notmatch '^[A-Za-z0-9_-]{3,80}$') {
    throw 'ExpectedStableVersion has an invalid format.'
}
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
$receiptPath = Join-Path (
    $resolvedOutputDirectory
) "private-canary-${Mode}-transaction.json"
if (Test-Path -LiteralPath $receiptPath) {
    if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) {
        throw 'The candidate transaction receipt path is not a file.'
    }
    Remove-Item -LiteralPath $receiptPath -Force
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
    -Action 'DescribeReleaseOrder' `
    -Payload @{ EnvId = $EnvironmentId; ServerName = $ServiceName } `
    -Credential $credential
$order = Get-PropertyValue $orderResponse 'ReleaseOrderInfo'
$stable = Get-PropertyValue $order 'CurrentVersion'
$candidate = Get-PropertyValue $order 'ReleaseVersion'
$stableVersion = [string](Get-PropertyValue $stable 'VersionName')
$candidateVersion = [string](Get-PropertyValue $candidate 'VersionName')
if (
    $stableVersion -ne $ExpectedStableVersion -or
    -not $candidateVersion -or
    $candidateVersion -eq $stableVersion
) {
    throw 'CloudBase release order did not match the expected stable and candidate identities.'
}
if ([string](Get-PropertyValue $order 'TrafficType') -ne 'FLOW') {
    throw 'CloudBase release order is not using FLOW traffic.'
}
if (
    @('normal', 'running') -notcontains (
        [string](Get-PropertyValue $stable 'Status')
    ).ToLowerInvariant() -or
    @('normal', 'running') -notcontains (
        [string](Get-PropertyValue $candidate 'Status')
    ).ToLowerInvariant()
) {
    throw 'CloudBase stable or candidate version is not running.'
}

$candidateDetail = Invoke-TencentApi `
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
        throw 'The candidate environment is not valid JSON.'
    }
}
if (
    [string](Get-PropertyValue $candidateDetail 'Name') -ne $candidateVersion -or
    [string](Get-PropertyValue $candidateEnvironment 'APP_RELEASE_SHA') -cne
        $ExpectedCandidateCommitSha
) {
    throw 'The candidate does not match the expected Git commit.'
}

$baselineTaskResponse = Get-ManagementTask -Credential $credential -TaskId 0
if ((Get-PropertyValue $baselineTaskResponse 'IsExist') -ne $true) {
    throw 'CloudBase does not expose an active gray release task.'
}
$baselineTask = Get-PropertyValue $baselineTaskResponse 'Task'
$promotionTaskId = [long](Get-PropertyValue $baselineTask 'Id')
Assert-TaskIdentity `
    -Task $baselineTask `
    -ExpectedTaskId $promotionTaskId `
    -ExpectedPreviousVersion $stableVersion `
    -ExpectedVersion $candidateVersion
if (
    [string](Get-PropertyValue $baselineTask 'ReleaseType') -ne 'GRAY' -or
    [string](Get-PropertyValue $baselineTask 'Status') -ne 'running'
) {
    throw 'CloudBase gray release task is not open for promotion.'
}
Wait-ExactOnlineTraffic `
    -Credential $credential `
    -StableVersion $stableVersion `
    -CandidateVersion $candidateVersion `
    -Target stable `
    -WaitSeconds 1

$promotionAttempted = $false
$promotionTerminal = $null
$primaryError = $null
$restoreError = $null
$healthVerified = $false
$readyVerified = $false
$routeRestored = $false
$routeFinalized = $false
$receiptJson = $null

try {
    $promotionAttempted = $true
    Invoke-TencentApi `
        -Action 'ReleaseGray' `
        -Payload @{
            EnvId = $EnvironmentId
            ServerName = $ServiceName
            GrayType = 'gray'
            TrafficType = 'FLOW'
            GrayFlowRatio = 100
            VersionFlowItems = @(
                @{
                    VersionName = $candidateVersion
                    FlowRatio = 100
                    Priority = 0
                    IsDefaultPriority = $true
                }
            )
            CloseGrayRelease = $true
            OperatorRemark = (
                "git:$($ExpectedCandidateCommitSha.Substring(0, 12)):promote"
            )
        } `
        -Credential $credential | Out-Null
    $promotionTerminal = Wait-ManagementTaskTerminal `
        -Credential $credential `
        -TaskId $promotionTaskId `
        -ExpectedPreviousVersion $stableVersion `
        -ExpectedVersion $candidateVersion `
        -WaitSeconds $TimeoutSeconds
    Assert-ManagementTaskSucceeded -TerminalTask $promotionTerminal
    Wait-ExactOnlineTraffic `
        -Credential $credential `
        -StableVersion $stableVersion `
        -CandidateVersion $candidateVersion `
        -Target candidate `
        -WaitSeconds $TimeoutSeconds
    Wait-PublicCandidate `
        -BaseUri $publicUri `
        -ExpectedCommitSha $ExpectedCandidateCommitSha `
        -WaitSeconds $TimeoutSeconds
    $healthVerified = $true
    $readyVerified = $true
    if ($Mode -eq 'finalize') {
        $routeFinalized = $true
        $receiptJson = Write-TransactionReceipt `
            -Path $receiptPath `
            -RouteRestored $false `
            -RouteFinalized $true
    }
} catch {
    $primaryError = $_
} finally {
    if (
        $promotionAttempted -and
        ($Mode -eq 'exercise' -or $null -ne $primaryError)
    ) {
        try {
            if ($null -eq $promotionTerminal) {
                $promotionTerminal = Wait-ManagementTaskTerminal `
                    -Credential $credential `
                    -TaskId $promotionTaskId `
                    -ExpectedPreviousVersion $stableVersion `
                    -ExpectedVersion $candidateVersion `
                    -WaitSeconds $RecoveryTimeoutSeconds
            }
            $shouldSubmitRollback = [string]$promotionTerminal.Status -eq 'finished'
            if ([string]$promotionTerminal.Status -eq 'failed') {
                $failedPromotionTraffic = @(Get-OnlineTraffic -Credential $credential)
                $failedPromotionAlreadyStable = Test-ExactOnlineTraffic `
                    -OnlineVersions $failedPromotionTraffic `
                    -StableVersion $stableVersion `
                    -CandidateVersion $candidateVersion `
                    -Target stable
                $failedPromotionReachedCandidate = Test-ExactOnlineTraffic `
                    -OnlineVersions $failedPromotionTraffic `
                    -StableVersion $stableVersion `
                    -CandidateVersion $candidateVersion `
                    -Target candidate
                if (-not $failedPromotionAlreadyStable -and -not $failedPromotionReachedCandidate) {
                    throw 'Failed promotion left an ambiguous online traffic state.'
                }
                $shouldSubmitRollback = $failedPromotionReachedCandidate
            } elseif ([string]$promotionTerminal.Status -ne 'finished') {
                throw 'CloudBase promotion task did not reach a recoverable terminal state.'
            }
            if ($shouldSubmitRollback) {
                $rollbackResponse = Invoke-TencentApi `
                    -Action 'SubmitServerRollback' `
                    -Payload @{
                        EnvId = $EnvironmentId
                        ServerName = $ServiceName
                        CurrentVersionName = $candidateVersion
                        RollbackVersionName = $stableVersion
                        OperatorRemark = (
                            "git:$($ExpectedCandidateCommitSha.Substring(0, 12)):rollback"
                        )
                    } `
                    -Credential $credential
                $rollbackTaskIdValue = Get-PropertyValue $rollbackResponse 'TaskId'
                if ($null -eq $rollbackTaskIdValue) {
                    throw 'CloudBase rollback did not return a management task.'
                }
                $rollbackTaskId = [long]$rollbackTaskIdValue
                $rollbackTerminal = Wait-ManagementTaskTerminal `
                    -Credential $credential `
                    -TaskId $rollbackTaskId `
                    -ExpectedPreviousVersion $candidateVersion `
                    -ExpectedVersion $stableVersion `
                    -WaitSeconds $RecoveryTimeoutSeconds
                Assert-ManagementTaskSucceeded -TerminalTask $rollbackTerminal
            }
            Wait-ExactOnlineTraffic `
                -Credential $credential `
                -StableVersion $stableVersion `
                -CandidateVersion $candidateVersion `
                -Target stable `
                -WaitSeconds $RecoveryTimeoutSeconds
            $routeRestored = $true
        } catch {
            $restoreError = $_
        }
    }
}

if ($null -ne $restoreError -or -not $routeRestored) {
    if ($Mode -eq 'exercise' -or $null -ne $primaryError) {
        throw 'Candidate transaction failed to restore verified stable traffic.'
    }
}
if (
    $null -ne $primaryError -or
    -not $healthVerified -or
    -not $readyVerified
) {
    if ($Mode -eq 'finalize') {
        throw 'Candidate finalization failed; stable traffic was restored.'
    }
    throw 'Candidate full-FLOW identity transaction failed; stable traffic was restored.'
}
if ($Mode -eq 'exercise' -and -not $routeRestored) {
    throw 'Candidate exercise did not restore verified stable traffic.'
}
if ($Mode -eq 'finalize' -and -not $routeFinalized) {
    throw 'Candidate finalization did not retain verified candidate traffic.'
}

if ($Mode -eq 'exercise') {
    $receiptJson = Write-TransactionReceipt `
        -Path $receiptPath `
        -RouteRestored $true `
        -RouteFinalized $false
}
$receiptJson
