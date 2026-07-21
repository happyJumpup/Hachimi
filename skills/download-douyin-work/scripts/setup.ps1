param(
    [switch]$ConfigureCookie,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$backend = Join-Path $PSScriptRoot "TikTokDownloader"
$python = Join-Path $backend ".venv\Scripts\python.exe"
$settings = Join-Path $backend "Volume\settings.json"

function Get-CookieConfigured {
    if (-not (Test-Path -LiteralPath $settings)) {
        return $false
    }
    try {
        $config = Get-Content -LiteralPath $settings -Raw -Encoding utf8 | ConvertFrom-Json
        return [bool]$config.cookie
    }
    catch {
        return $false
    }
}

if (-not (Test-Path -LiteralPath $backend)) {
    @{ status = "setup_failed"; message = "bundled TikTokDownloader backend is missing" } |
        ConvertTo-Json -Compress
    exit 2
}

if ($CheckOnly) {
    $ready = Test-Path -LiteralPath $python
    @{
        status = if ($ready) { "ready" } else { "environment_required" }
        environment_ready = $ready
        cookie_configured = Get-CookieConfigured
    } | ConvertTo-Json -Compress
    exit $(if ($ready) { 0 } else { 2 })
}

$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    @{
        status = "setup_failed"
        message = "uv is required; install it from https://docs.astral.sh/uv/getting-started/installation/"
    } | ConvertTo-Json -Compress
    exit 2
}

if (-not (Test-Path -LiteralPath $python)) {
    Push-Location $backend
    try {
        & $uv.Source sync --no-dev --frozen
        if ($LASTEXITCODE -ne 0) {
            throw "uv sync failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}

& $python -c "import sys; sys.path.insert(0, r'$backend'); from src.application import TikTokDownloader; from src.interface import Detail; print('backend imports OK')"
if ($LASTEXITCODE -ne 0) {
    @{ status = "setup_failed"; message = "backend import check failed" } |
        ConvertTo-Json -Compress
    exit 2
}

if ($ConfigureCookie) {
    Push-Location $backend
    try {
        & $python main.py
    }
    finally {
        Pop-Location
    }
}

$cookieReady = Get-CookieConfigured
@{
    status = if ($cookieReady) { "ready" } else { "cookie_required" }
    environment_ready = $true
    cookie_configured = $cookieReady
    message = if ($cookieReady) {
        "environment and Cookie are ready"
    } else {
        "run setup.ps1 -ConfigureCookie in an interactive terminal"
    }
} | ConvertTo-Json -Compress
exit $(if ($cookieReady) { 0 } else { 3 })
