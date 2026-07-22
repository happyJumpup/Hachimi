[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ImageRef
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http

$workDirectory = Join-Path (
    [IO.Path]::GetTempPath()
) ("trainpal-image-audit-{0}" -f [Guid]::NewGuid().ToString('N'))
$suffix = [Guid]::NewGuid().ToString('N')
$containerName = "trainpal-image-smoke-$suffix"
$modelTrapName = "trainpal-gymti-model-trap-$suffix"
$networkName = "trainpal-image-smoke-$suffix"
$containerStarted = $false
$modelTrapStarted = $false
$networkCreated = $false

function Invoke-DockerChecked {
    param([Parameter(Mandatory)][string[]]$Arguments)
    $output = & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker command failed: $($Arguments[0])"
    }
    return $output
}

$containerAudit = @'
if [ ! -f /workspace/contracts/gymti-questionnaire.v1.json ]; then
  printf '%s\n' /workspace/contracts/gymti-questionnaire.v1.json
fi

find /workspace -type f \( \
  -name '.env' -o -name '.env.*' -o \
  -name '*.mp4' -o -name '*.mov' -o -name '*.mkv' -o \
  -name '*.avi' -o -name '*.webm' -o -name '*.wav' -o \
  -name '*.mp3' -o -name '*.ogg' -o -name '*.m4a' -o \
  -name '*.aac' -o -name '*.flac' -o -name '*.trace' \
\) -print

# WebP files are checked against the registered SHA-256 manifest by the layer
# audit. This runtime pass rejects every other image and subtitle format.
find /workspace -type f \( \
  -name '*.bmp' -o -name '*.gif' -o -name '*.jpeg' -o \
  -name '*.jpg' -o -name '*.png' -o -name '*.tif' -o \
  -name '*.tiff' -o -name '*.ass' -o -name '*.srt' -o \
  -name '*.ssa' -o -name '*.vtt' \
\) -print

find /workspace -type f \( \
  -iname '*transcript*' -o -iname '*transcription*' -o -iname '*subtitle*' \
\) -print
find /workspace/apps/web/dist -type f -name '*.map' -print

for forbidden_path in \
  /workspace/.git \
  /workspace/.github \
  /workspace/docs \
  /workspace/node_modules \
  /workspace/apps/web/tests \
  /workspace/services/analysis-api/tests \
  /workspace/services/analysis-api/scripts; do
  if [ -e "${forbidden_path}" ]; then
    printf '%s\n' "${forbidden_path}"
  fi
done

find / -xdev -type f -perm /111 \( \
  -name 'ffmpeg' -o -name 'ffmpeg.exe' -o \
  -path '*/imageio_ffmpeg/binaries/ffmpeg-*' \
\) -print 2>/dev/null | while IFS= read -r ffmpeg_path; do
  if [ "${ffmpeg_path}" != '/opt/trainpal/ffmpeg/bin/ffmpeg' ]; then
    printf '%s\n' "${ffmpeg_path}"
  fi
done

find /workspace/tmp/analysis-runs -mindepth 1 -print -quit
'@
$containerAudit = $containerAudit.Replace("`r`n", "`n")
$encodedContainerAudit = [Convert]::ToBase64String(
    [Text.Encoding]::UTF8.GetBytes($containerAudit)
)
$containerAuditCommand = "printf '%s' '$encodedContainerAudit' | base64 -d | /bin/sh -eu"

$modelTrapCode = @'
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class TrapHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        print("unexpected-gymti-model-call", flush=True)
        self.send_response(503)
        self.end_headers()

    def log_message(self, _format, *_args):
        return

ThreadingHTTPServer(("0.0.0.0", 8081), TrapHandler).serve_forever()
'@
$encodedModelTrap = [Convert]::ToBase64String(
    [Text.Encoding]::UTF8.GetBytes($modelTrapCode)
)
$modelTrapCommand = "import base64;exec(base64.b64decode('$encodedModelTrap'))"

try {
    New-Item -ItemType Directory -Path $workDirectory | Out-Null

    $configuredUser = (
        Invoke-DockerChecked @('image', 'inspect', '--format', '{{.Config.User}}', $ImageRef)
    ).Trim()
    if ($configuredUser -ne '10001:10001') {
        throw "runtime user must be 10001:10001, got '$configuredUser'"
    }

    $archivePath = Join-Path $workDirectory 'image.tar'
    Invoke-DockerChecked @('image', 'save', '--output', $archivePath, $ImageRef) | Out-Null
    & python (Join-Path $PSScriptRoot 'audit-image-layers.py') $archivePath
    if ($LASTEXITCODE -ne 0) {
        throw 'image layer audit failed'
    }

    $auditOutput = @(
        @(
            & docker run --rm --entrypoint /bin/sh $ImageRef -eu -c $containerAuditCommand
        ) | Where-Object { $_ -and $_.Trim() }
    )
    if ($LASTEXITCODE -ne 0) {
        throw 'container filesystem audit failed to execute'
    }
    if ($auditOutput.Count -gt 0) {
        $auditOutput | ForEach-Object { Write-Error $_ }
        throw 'forbidden release content is present'
    }

    Invoke-DockerChecked @(
        'run', '--rm',
        '--entrypoint', '/workspace/services/analysis-api/.venv/bin/python',
        $ImageRef,
        '-c',
        "from pathlib import Path; from hakimi_analysis.readiness import validate_ffmpeg_build_receipt; raise SystemExit(0 if validate_ffmpeg_build_receipt(Path('/opt/trainpal/ffmpeg/bin/ffmpeg'), Path('/opt/trainpal/ffmpeg/receipt.json')) else 1)"
    ) | Out-Null

    Invoke-DockerChecked @('network', 'create', $networkName) | Out-Null
    $networkCreated = $true
    Invoke-DockerChecked @(
        'run', '--detach',
        '--name', $modelTrapName,
        '--network', $networkName,
        '--read-only',
        '--tmpfs', '/tmp:rw,noexec,nosuid,size=8m,mode=1777',
        '--entrypoint', '/workspace/services/analysis-api/.venv/bin/python',
        $ImageRef,
        '-c', $modelTrapCommand
    ) | Out-Null
    $modelTrapStarted = $true
    $modelTrapRunning = (
        Invoke-DockerChecked @('inspect', '--format', '{{.State.Running}}', $modelTrapName)
    ).Trim()
    if ($modelTrapRunning -ne 'true') {
        throw 'GYMTI model trap exited before the image smoke'
    }

    Invoke-DockerChecked @(
        'run', '--detach',
        '--name', $containerName,
        '--network', $networkName,
        '--read-only',
        '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m,mode=1777',
        '--tmpfs', '/workspace/tmp/analysis-runs:rw,noexec,nosuid,size=128m,uid=10001,gid=10001,mode=0700',
        '--env', 'APP_ENV=production',
        '--env', 'ANALYSIS_PROVIDER=cloud',
        '--env', 'ARK_API_KEY=image-audit-provider-key',
        '--env', 'VOLC_ASR_API_KEY=image-audit-asr-key',
        '--env', 'JUDGE_ACCESS_CODE=image-audit-judge-code-32-bytes',
        '--env', 'ACCESS_COOKIE_SECRET=image-audit-cookie-secret-at-least-32-bytes',
        '--env', 'CORS_ORIGINS=https://image-audit.invalid',
        '--env', 'LOCAL_UPLOAD_ENABLED=true',
        '--env', 'TRUSTED_PROXY_CIDRS=',
        '--env', 'PUBLIC_ANALYSIS_CONCURRENCY=0',
        '--env', 'JUDGE_ANALYSIS_CONCURRENCY=3',
        '--env', 'GYMTI_LLM_ENABLED=true',
        '--env', 'GYMTI_LLM_RETENTION_CONFIRMED=true',
        '--env', 'GYMTI_LLM_API_KEY=release-smoke-trap-key',
        '--env', "GYMTI_LLM_BASE_URL=http://${modelTrapName}:8081/v1",
        '--env', 'GYMTI_LLM_MAX_ATTEMPTS=1',
        '--env', 'GYMTI_LLM_TIMEOUT_SECONDS=1',
        '--publish', '127.0.0.1::8000',
        $ImageRef
    ) | Out-Null
    $containerStarted = $true

    $portLine = (
        Invoke-DockerChecked @('port', $containerName, '8000/tcp') | Select-Object -First 1
    )
    if ($portLine -notmatch ':(\d+)$') {
        throw 'could not resolve the published API port'
    }
    $baseUrl = "http://127.0.0.1:$($Matches[1])"
    $http = [System.Net.Http.HttpClient]::new()
    try {
        $healthy = $false
        for ($attempt = 0; $attempt -lt 45; $attempt++) {
            try {
                $healthResponse = $http.GetAsync("$baseUrl/api/v1/health").GetAwaiter().GetResult()
                if ($healthResponse.IsSuccessStatusCode) {
                    $healthBody = $healthResponse.Content.ReadAsStringAsync().GetAwaiter().GetResult()
                    $health = $healthBody | ConvertFrom-Json
                    if ($health.status -eq 'ok') {
                        $healthy = $true
                        break
                    }
                }
            } catch [System.Net.Http.HttpRequestException] {
                # Uvicorn can refuse connections briefly while the container starts.
            }
            Start-Sleep -Seconds 1
        }
        if (-not $healthy) {
            throw 'health endpoint did not become ready within 45 seconds'
        }

        $readyResponse = $http.GetAsync("$baseUrl/api/v1/ready").GetAwaiter().GetResult()
        $readyBody = $readyResponse.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        $ready = $readyBody | ConvertFrom-Json
        if (-not $readyResponse.IsSuccessStatusCode -or $ready.status -ne 'ready') {
            throw 'production ready endpoint did not accept the local-upload-only image'
        }

        foreach ($route in @('/', '/mine')) {
            $response = $http.GetAsync("$baseUrl$route").GetAwaiter().GetResult()
            $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
            if (-not $response.IsSuccessStatusCode -or -not $body.Contains('<div id="app"></div>')) {
                throw "SPA route failed: $route"
            }
        }
        $missing = $http.GetAsync("$baseUrl/api/v1/not-a-route").GetAwaiter().GetResult()
        $missingBody = $missing.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        if ([int]$missing.StatusCode -ne 404 -or $missingBody.Contains('<div id="app"></div>')) {
            throw 'unknown API route did not preserve JSON 404 behavior'
        }
    } finally {
        $http.Dispose()
    }

    $gymtiSmoke = Get-Content -Raw (Join-Path $PSScriptRoot 'smoke-gymti-image.py')
    $gymtiSmoke | & docker exec --interactive $containerName `
        /workspace/services/analysis-api/.venv/bin/python -
    if ($LASTEXITCODE -ne 0) {
        throw 'anonymous GYMTI image smoke failed'
    }

    $modelLogs = (& docker logs $modelTrapName 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) {
        throw 'could not inspect GYMTI model trap logs'
    }
    if ($modelLogs.Contains('unexpected-gymti-model-call')) {
        throw 'anonymous GYMTI image smoke reached the configured model'
    }
    $modelTrapRunning = (
        Invoke-DockerChecked @('inspect', '--format', '{{.State.Running}}', $modelTrapName)
    ).Trim()
    if ($modelTrapRunning -ne 'true') {
        throw 'GYMTI model trap exited during the image smoke'
    }

    Write-Output "competition image verification passed: $ImageRef"
} catch {
    if ($containerStarted) {
        & docker logs $containerName 2>$null
    }
    if ($modelTrapStarted) {
        & docker logs $modelTrapName 2>$null
    }
    throw
} finally {
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    if ($containerStarted) {
        & docker rm --force $containerName 2>$null | Out-Null
    }
    if ($modelTrapStarted) {
        & docker rm --force $modelTrapName 2>$null | Out-Null
    }
    if ($networkCreated) {
        & docker network rm $networkName 2>$null | Out-Null
    }
    $ErrorActionPreference = $previousErrorActionPreference

    if (Test-Path -LiteralPath $workDirectory) {
        $resolvedWorkDirectory = (Resolve-Path -LiteralPath $workDirectory).Path
        $resolvedTempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        if (-not $resolvedWorkDirectory.StartsWith($resolvedTempRoot, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'refusing to remove an audit directory outside the system temp root'
        }
        Remove-Item -LiteralPath $resolvedWorkDirectory -Recurse -Force
    }
}
