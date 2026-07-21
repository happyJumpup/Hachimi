param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Url,

    [Parameter(Mandatory = $true, Position = 1)]
    [string]$Output,

    [switch]$Comments,
    [ValidateRange(1, 100)]
    [int]$CommentPages = 1,
    [switch]$CommentReplies,
    [switch]$Cover,
    [switch]$Overwrite
)

$python = Join-Path $PSScriptRoot "TikTokDownloader\.venv\Scripts\python.exe"
$script = Join-Path $PSScriptRoot "grab_douyin.py"

if (-not (Test-Path -LiteralPath $python)) {
    Write-Error "TikTokDownloader 环境不存在。请先在仓库目录运行: uv sync --no-dev"
    exit 2
}

$arguments = @($script, $Url, $Output, "--comment-pages", $CommentPages)
if ($Comments) { $arguments += "--comments" }
if ($CommentReplies) { $arguments += "--comment-replies" }
if ($Cover) { $arguments += "--cover" }
if ($Overwrite) { $arguments += "--overwrite" }

& $python @arguments
exit $LASTEXITCODE
