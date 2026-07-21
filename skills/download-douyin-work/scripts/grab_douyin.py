#!/usr/bin/env python3
"""Download one Douyin work through the bundled DouK-Downloader backend.

The backend cookie is read from TikTokDownloader/Volume/settings.json.  Secrets
are never accepted as command-line arguments or copied into generated files.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_REPO = Path(__file__).resolve().parent / "TikTokDownloader"
VIDEO_ID_PATTERNS = (
    re.compile(r"douyin\.com/video/(?P<id>\d{19})", re.IGNORECASE),
    re.compile(r"[?&]modal_id=(?P<id>\d{19})(?:&|$)", re.IGNORECASE),
)


class GrabError(RuntimeError):
    """A user-facing download error."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="输入一个抖音链接，输出媒体文件、metadata.json 和 manifest.json。",
    )
    parser.add_argument("url", help="抖音作品链接或包含分享链接的文本")
    parser.add_argument(
        "output", type=Path, help="输出根目录；作品会保存到 <output>/<作品ID>"
    )
    parser.add_argument(
        "--comments",
        action="store_true",
        help="同时采集评论并保存为 comments.jsonl",
    )
    parser.add_argument(
        "--comment-pages",
        type=int,
        default=1,
        metavar="N",
        help="评论最大页数，默认 1",
    )
    parser.add_argument(
        "--comment-replies",
        action="store_true",
        help="请求评论回复；会增加请求量和风控风险",
    )
    parser.add_argument(
        "--cover",
        action="store_true",
        help="同时下载静态封面",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖已经存在的媒体文件",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=DEFAULT_REPO,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()
    if args.comment_pages < 1:
        parser.error("--comment-pages 必须大于或等于 1")
    return args


def extract_content_id(text: str) -> str | None:
    for pattern in VIDEO_ID_PATTERNS:
        if match := pattern.search(text):
            return match.group("id")
    return None


def read_backend_cookie(repo: Path) -> str | dict[str, Any]:
    settings_path = repo / "Volume" / "settings.json"
    if not settings_path.is_file():
        raise GrabError(
            f"未找到 {settings_path}；请先运行 TikTokDownloader 并写入抖音 Cookie。"
        )
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GrabError(f"无法读取 TikTokDownloader 配置：{exc}") from exc
    cookie = settings.get("cookie")
    if not cookie:
        raise GrabError(
            "TikTokDownloader 的 cookie 仍为空；请先在交互菜单选择“从剪贴板读取 Cookie (抖音)”。"
        )
    return cookie


def install_backend_import_path(repo: Path) -> None:
    if not (repo / "src").is_dir():
        raise GrabError(f"TikTokDownloader 仓库不存在或不完整：{repo}")
    repo_text = str(repo.resolve())
    if repo_text not in sys.path:
        sys.path.insert(0, repo_text)


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def extension_from_content_type(content_type: str, fallback: str) -> str:
    content_type = content_type.split(";", 1)[0].strip().lower()
    return {
        "video/mp4": ".mp4",
        "video/quicktime": ".mov",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }.get(content_type, fallback)


async def download_file(
    client: Any,
    url: str,
    destination_without_suffix: Path,
    headers: dict[str, str],
    fallback_suffix: str,
    overwrite: bool,
) -> Path:
    existing = list(
        destination_without_suffix.parent.glob(destination_without_suffix.name + ".*")
    )
    if existing and not overwrite:
        return existing[0]

    async with client.stream(
        "GET",
        url,
        headers=headers,
        follow_redirects=True,
        timeout=60,
    ) as response:
        response.raise_for_status()
        suffix = extension_from_content_type(
            response.headers.get("content-type", ""), fallback_suffix
        )
        destination = destination_without_suffix.with_suffix(suffix)
        temporary = destination.with_suffix(destination.suffix + ".part")
        with temporary.open("wb") as stream:
            async for chunk in response.aiter_bytes(1024 * 1024):
                stream.write(chunk)
        temporary.replace(destination)
        return destination


async def resolve_and_extract(
    url: str,
    repo: Path,
    include_comments: bool,
    comment_pages: int,
    comment_replies: bool,
) -> tuple[str, dict[str, Any], list[dict[str, Any]], dict[str, str]]:
    install_backend_import_path(repo)

    # Imports are deferred so --help works even when the backend is not installed.
    from src.application import TikTokDownloader  # type: ignore[import-not-found]
    from src.application.main_terminal import TikTok  # type: ignore[import-not-found]
    from src.interface import Detail  # type: ignore[import-not-found]
    from src.storage.text import BaseTextLogger  # type: ignore[import-not-found]

    async with TikTokDownloader() as application:
        application.check_config()
        await application.check_settings(False)
        worker = TikTok(application.parameter, application.database, server_mode=True)

        content_ids = [extract_content_id(url)] if extract_content_id(url) else []
        if not content_ids:
            content_ids = await worker.links.run(url)
        content_ids = [item for item in content_ids if item]
        if len(content_ids) != 1:
            raise GrabError(
                f"输入必须唯一对应一个抖音作品，当前解析到 {len(content_ids)} 个作品。"
            )
        content_id = content_ids[0]

        raw_detail = await worker.handle_detail_single(
            Detail,
            None,
            None,
            content_id,
        )
        if not raw_detail:
            raise GrabError(
                "抖音作品详情获取失败；Cookie 可能已失效，或后端加密参数已被抖音更新。"
            )
        details = await worker.extractor.run(
            [raw_detail],
            BaseTextLogger(),
            type_="detail",
            tiktok=False,
        )
        if not details:
            raise GrabError("作品详情返回成功，但未能提取结构化数据。")

        comments: list[dict[str, Any]] = []
        if include_comments:
            try:
                comments = await worker.comment_handle_single(
                    content_id,
                    source=False,
                    pages=comment_pages,
                    count=20,
                    reply=comment_replies,
                    count_reply=3,
                )
            except (
                Exception
            ) as exc:  # Comments are optional; preserve the video result.
                print(f"警告：评论采集失败：{exc}", file=sys.stderr)

        headers = dict(application.parameter.headers_download)
        return content_id, details[0], comments, headers


async def run(args: argparse.Namespace) -> dict[str, Any]:
    repo = args.repo.resolve()
    read_backend_cookie(repo)  # Fail before making any network request.

    content_id, metadata, comments, headers = await resolve_and_extract(
        args.url,
        repo,
        args.comments,
        args.comment_pages,
        args.comment_replies,
    )

    work_dir = args.output.resolve() / content_id
    work_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = work_dir / "metadata.json"
    comments_path = work_dir / "comments.jsonl"
    manifest_path = work_dir / "manifest.json"
    write_json(metadata_path, metadata)
    if args.comments:
        write_jsonl(comments_path, comments)

    import httpx

    media_paths: list[Path] = []
    cover_path: Path | None = None
    async with httpx.AsyncClient() as client:
        downloads = metadata.get("downloads")
        if isinstance(downloads, str) and downloads:
            media_paths.append(
                await download_file(
                    client,
                    downloads,
                    work_dir / "video",
                    headers,
                    ".mp4",
                    args.overwrite,
                )
            )
        elif isinstance(downloads, list) and downloads:
            for index, image_url in enumerate(downloads, start=1):
                if image_url:
                    media_paths.append(
                        await download_file(
                            client,
                            image_url,
                            work_dir / f"image_{index:03d}",
                            headers,
                            ".jpg",
                            args.overwrite,
                        )
                    )
        else:
            raise GrabError("作品详情中没有可用的媒体下载地址。")

        if args.cover and metadata.get("static_cover"):
            cover_path = await download_file(
                client,
                metadata["static_cover"],
                work_dir / "cover",
                headers,
                ".jpg",
                args.overwrite,
            )

    manifest = {
        "status": "success",
        "platform": "douyin",
        "content_id": content_id,
        "source_url": args.url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "output_dir": str(work_dir),
        "media_paths": [str(path) for path in media_paths],
        "metadata_path": str(metadata_path),
        "comments_path": str(comments_path) if args.comments else None,
        "comment_count": len(comments),
        "cover_path": str(cover_path) if cover_path else None,
    }
    write_json(manifest_path, manifest)
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def main() -> int:
    args = parse_args()
    try:
        result = asyncio.run(run(args))
    except GrabError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"status": "cancelled"}, ensure_ascii=False))
        return 130
    except Exception as exc:
        print(
            json.dumps(
                {"status": "error", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
