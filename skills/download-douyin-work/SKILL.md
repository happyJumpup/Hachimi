---
name: download-douyin-work
description: Download one authorized public Douyin work from a link into local media, normalized metadata, an optional cover, and optional comments using the bundled backend and a user-configured Cookie. Use when an agent or teammate receives a Douyin video, jingxuan modal, or share link and needs a reproducible from-zero setup, credential-safe Cookie check, deterministic download command, or machine-readable output manifest.
---

# Download a Douyin work

Use the bundled wrapper as the public interface. Do not call `TikTokDownloader` directly unless diagnosing a backend failure.

Only process public content the user is authorized to save. Do not bypass login, payment, privacy, DRM, CAPTCHA, or platform controls.

## Locate the bundle

Treat the directory containing this `SKILL.md` as `<skill-root>`. Required files are:

```text
<skill-root>/scripts/setup.ps1
<skill-root>/scripts/grab_douyin.ps1
<skill-root>/scripts/grab_douyin.py
<skill-root>/scripts/TikTokDownloader/
```

Never assume the current working directory. Use absolute paths derived from `<skill-root>`.

## Input and output contract

Require exactly:

- one Douyin work/share URL or share text containing one URL;
- one output root directory.

Accept these optional switches:

- `-Comments`: save comments as JSONL;
- `-CommentPages N`: cap comment pages; default and recommended value is `1`;
- `-CommentReplies`: request replies, with additional rate-limit risk;
- `-Cover`: save the static cover;
- `-Overwrite`: replace existing media.

Write `<output>/<work-id>/` containing:

```text
video.mp4                  # video work
image_001.* ...            # image work instead of video.mp4
metadata.json
comments.jsonl             # only with -Comments
cover.*                    # only with -Cover
manifest.json
```

The command's final line is JSON. `status: success` means the wrapper completed. Still verify every returned file path exists before reporting success.

## Prepare the environment

Run the non-interactive setup first:

```powershell
powershell -ExecutionPolicy Bypass -File "<skill-root>\scripts\setup.ps1"
```

Interpret the final JSON:

- `ready`: continue to download;
- `cookie_required`: pause and ask the user to configure Cookie interactively;
- `setup_failed`: report the message and stop;
- `environment_required`: rerun without `-CheckOnly`.

The setup requires `uv` and creates a Python 3.12 virtual environment inside the bundled backend. Do not invent another environment or install dependencies globally.

## Configure Cookie safely

Cookie configuration requires the user. Never request the Cookie in chat, print it, copy it into a command, or inspect its value.

Ask the user to run this in their own interactive PowerShell terminal:

```powershell
powershell -ExecutionPolicy Bypass -File "<skill-root>\scripts\setup.ps1" -ConfigureCookie
```

In the opened program, instruct the user to:

1. Select Simplified Chinese and accept the disclaimer on first run.
2. Select `从剪贴板读取 Cookie (抖音)`.
3. Ensure the complete Douyin Cookie is already on the clipboard, then press Enter.
4. Confirm the program reports a valid Cookie.
5. Enter `Q` to exit.

The Cookie stays in `scripts/TikTokDownloader/Volume/settings.json`, which is Git-ignored. Do not copy this file when redistributing the Skill. Reconfigure only when the backend reports an invalid or expired Cookie.

## Download

Run:

```powershell
powershell -ExecutionPolicy Bypass -File "<skill-root>\scripts\grab_douyin.ps1" `
  -Url "<douyin-link-or-share-text>" `
  -Output "<absolute-output-root>"
```

When requested, append conservative options:

```powershell
-Comments -CommentPages 1 -Cover
```

Do not enable replies or large comment page counts unless the user explicitly needs them.

## Return results

Parse the final JSON object and report clickable absolute paths for:

- the MP4 or image files;
- `metadata.json`;
- `comments.jsonl`, if requested;
- the cover, if requested;
- `manifest.json`.

Also report the work ID, downloaded media count, and collected comment count. Do not expose expiring CDN URLs from `metadata.json` unless the user explicitly asks.

## Handle failures

- Cookie empty or missing: return `cookie_required` and use the interactive configuration workflow once.
- Work detail unavailable: explain that Cookie may have expired or Douyin may have changed its request parameters; do not retry indefinitely.
- Comments fail after media succeeds: preserve and return the media result; comments are optional.
- No media path exists: treat the run as failed even if a subprocess exits successfully.
- CAPTCHA or account verification appears: stop and hand control to the user.
- Unsupported multi-work input: ask for exactly one work link.

The bundled backend may print `加密参数代码文件不存在！`; treat it as informational only when the final wrapper JSON reports success and the media file exists.
