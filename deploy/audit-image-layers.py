#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import re
import sys
import tarfile
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any


SECRET_ENV_NAMES = {
    "ACCESS_COOKIE_SECRET",
    "ARK_API_KEY",
    "DEEPSEEK_API_KEY",
    "GYMTI_LLM_API_KEY",
    "JUDGE_ACCESS_CODE",
    "VOLC_ASR_API_KEY",
}
MEDIA_SUFFIXES = {
    ".aac",
    ".avi",
    ".flac",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".trace",
    ".wav",
    ".webm",
}
VISUAL_MEDIA_SUFFIXES = {
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}
SUBTITLE_SUFFIXES = {".ass", ".srt", ".ssa", ".vtt"}
REGISTERED_VISUAL_MANIFEST = Path(__file__).with_name("registered-visual-assets.json")
FORBIDDEN_PREFIXES = tuple(
    PurePosixPath(value)
    for value in (
        "workspace/.git",
        "workspace/.github",
        "workspace/docs",
        "workspace/node_modules",
        "workspace/apps/web/tests",
        "workspace/services/analysis-api/scripts",
        "workspace/services/analysis-api/tests",
    )
)
WEB_DIST = PurePosixPath("workspace/apps/web/dist")
ANALYSIS_TEMP = PurePosixPath("workspace/tmp/analysis-runs")
REGISTERED_FFMPEG = PurePosixPath("opt/trainpal/ffmpeg/bin/ffmpeg")


class ImageAuditError(RuntimeError):
    pass


@cache
def _load_registered_visual_sha256() -> frozenset[str]:
    try:
        payload = json.loads(REGISTERED_VISUAL_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ImageAuditError("registered visual manifest could not be loaded") from error
    if not isinstance(payload, dict) or set(payload) != {"version", "assets"}:
        raise ImageAuditError("registered visual manifest shape is invalid")
    if payload["version"] != 1 or not isinstance(payload["assets"], list):
        raise ImageAuditError("registered visual manifest version is invalid")

    source_paths: set[str] = set()
    hashes: set[str] = set()
    for item in payload["assets"]:
        if not isinstance(item, dict) or set(item) not in (
            {"sourcePath", "sha256"},
            {"sourcePath", "sha256", "legacy"},
        ):
            raise ImageAuditError("registered visual manifest entry is invalid")
        source_path = item.get("sourcePath")
        digest = item.get("sha256")
        legacy = item.get("legacy", False)
        if (
            not isinstance(source_path, str)
            or not source_path
            or "\\" in source_path
            or PurePosixPath(source_path).is_absolute()
            or ".." in PurePosixPath(source_path).parts
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or not isinstance(legacy, bool)
        ):
            raise ImageAuditError("registered visual manifest entry value is invalid")
        if source_path in source_paths or digest in hashes:
            raise ImageAuditError("registered visual manifest contains a duplicate")
        source_paths.add(source_path)
        hashes.add(digest)
    if not hashes:
        raise ImageAuditError("registered visual manifest is empty")
    return frozenset(hashes)


def _normalize(member_name: str) -> PurePosixPath:
    normalized = member_name.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return PurePosixPath(normalized.lstrip("/"))


def _is_under(path: PurePosixPath, parent: PurePosixPath) -> bool:
    return path == parent or parent in path.parents


def _whiteout_target(path: PurePosixPath) -> PurePosixPath | None:
    if not path.name.startswith(".wh.") or path.name == ".wh..wh..opq":
        return None
    return path.parent / path.name.removeprefix(".wh.")


def _violation(
    path: PurePosixPath,
    *,
    content_sha256: str | None = None,
    is_file: bool = True,
) -> str | None:
    lowered = str(path).lower()
    basename = path.name.lower()

    if is_file and basename in {"ffmpeg", "ffmpeg.exe"}:
        if path == REGISTERED_FFMPEG:
            return None
        return "bundled FFmpeg executable"
    if "imageio_ffmpeg/binaries/ffmpeg-" in lowered:
        return "imageio-ffmpeg wheel binary"

    workspace = PurePosixPath("workspace")
    if not _is_under(path, workspace):
        return None
    if basename == ".env" or basename.startswith(".env."):
        return "environment file"
    if path.suffix.lower() in MEDIA_SUFFIXES:
        return "raw media or trace"
    if path.suffix.lower() in SUBTITLE_SUFFIXES:
        return "subtitle or transcript artifact"
    if any(token in basename for token in ("transcript", "transcription", "subtitle")):
        return "transcript artifact"
    if path.suffix.lower() in VISUAL_MEDIA_SUFFIXES:
        if content_sha256 in _load_registered_visual_sha256():
            return None
        return "raw frame or unregistered visual asset"
    if path.suffix.lower() == ".map" and _is_under(path, WEB_DIST):
        return "frontend source map"
    if any(_is_under(path, prefix) for prefix in FORBIDDEN_PREFIXES):
        return "repository-only content"
    if path != ANALYSIS_TEMP and _is_under(path, ANALYSIS_TEMP):
        return "analysis temporary material"
    return None


def _load_json(archive: tarfile.TarFile, member_name: str) -> Any:
    extracted = archive.extractfile(member_name)
    if extracted is None:
        raise ImageAuditError(f"missing image archive member: {member_name}")
    return json.load(extracted)


def _audit_config(archive: tarfile.TarFile, config_name: str) -> None:
    config = _load_json(archive, config_name)
    configured_env = config.get("config", {}).get("Env", [])
    for entry in configured_env:
        name = str(entry).partition("=")[0]
        if name in SECRET_ENV_NAMES:
            raise ImageAuditError(f"secret environment key baked into image config: {name}")


def _audit_layer(archive: tarfile.TarFile, layer_name: str) -> None:
    extracted = archive.extractfile(layer_name)
    if extracted is None:
        raise ImageAuditError(f"missing image layer: {layer_name}")
    with tarfile.open(fileobj=extracted, mode="r|*") as layer:
        for member in layer:
            path = _normalize(member.name)
            content_sha256 = None
            if member.isfile() and path.suffix.lower() in VISUAL_MEDIA_SUFFIXES:
                extracted_member = layer.extractfile(member)
                if extracted_member is None:
                    raise ImageAuditError(f"could not read visual asset: {path}")
                digest = hashlib.sha256()
                for chunk in iter(lambda: extracted_member.read(1024 * 1024), b""):
                    digest.update(chunk)
                content_sha256 = digest.hexdigest()
            candidates = (path, _whiteout_target(path))
            for candidate in candidates:
                if candidate is None:
                    continue
                reason = _violation(
                    candidate,
                    content_sha256=content_sha256 if candidate == path else None,
                    is_file=member.isfile() if candidate == path else True,
                )
                if reason is not None:
                    raise ImageAuditError(f"{reason} found in image layer: {candidate}")


def audit_image_archive(image_archive: Path) -> None:
    with tarfile.open(image_archive, mode="r:*") as archive:
        manifest = _load_json(archive, "manifest.json")
        if not isinstance(manifest, list) or not manifest:
            raise ImageAuditError("image archive has no manifest entries")
        for image in manifest:
            _audit_config(archive, str(image["Config"]))
            for layer_name in image["Layers"]:
                _audit_layer(archive, str(layer_name))


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: audit-image-layers.py <docker-image-archive>", file=sys.stderr)
        return 2
    try:
        audit_image_archive(Path(sys.argv[1]))
    except (ImageAuditError, KeyError, OSError, tarfile.TarError, ValueError) as error:
        print(f"competition image layer audit failed: {error}", file=sys.stderr)
        return 1
    print("competition image layer audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
