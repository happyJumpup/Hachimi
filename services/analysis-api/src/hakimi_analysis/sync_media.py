import argparse
import hashlib
import os
import sys
from contextlib import suppress
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlparse
from uuid import uuid4

import httpx

from hakimi_analysis.sources import SourceManifestError, _safe_media_path, load_source_manifest


class MediaSyncError(RuntimeError):
    def __init__(self, code: str, *, exit_code: int) -> None:
        super().__init__(code)
        self.code = code
        self.exit_code = exit_code


def sync_media(*, manifest_path: Path, target_root: Path, base_url: str) -> int:
    try:
        manifest = load_source_manifest(manifest_path)
    except SourceManifestError as error:
        raise MediaSyncError("manifest_invalid", exit_code=2) from error
    normalized_base = _validated_base_url(base_url)
    if target_root.is_symlink():
        raise MediaSyncError("cache_symlink_rejected", exit_code=3)
    resolved_target = target_root.expanduser().resolve()
    relative_paths = [_safe_media_path(source.media_path) for source in manifest.sources]
    try:
        resolved_target.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise MediaSyncError("cache_unavailable", exit_code=3) from error
    _validate_existing_cache(resolved_target, relative_paths)

    try:
        with httpx.Client(
            timeout=httpx.Timeout(180, connect=15),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            for source, relative_path in zip(manifest.sources, relative_paths, strict=True):
                destination = (resolved_target / Path(*relative_path.parts)).resolve()
                try:
                    destination.relative_to(resolved_target)
                except ValueError as error:
                    raise MediaSyncError("path_invalid", exit_code=2) from error
                _download_one(
                    client=client,
                    url=f"{normalized_base}/{quote(relative_path.as_posix(), safe='/')}",
                    destination=destination,
                    expected_sha256=source.sha256,
                )
    except MediaSyncError:
        raise
    except (httpx.HTTPError, OSError) as error:
        raise MediaSyncError("transfer_failed", exit_code=3) from error
    return len(manifest.sources)


def _validate_existing_cache(root: Path, allowed_paths: list[PurePosixPath]) -> None:
    allowed = {path.as_posix().casefold() for path in allowed_paths}
    try:
        for path in root.rglob("*"):
            if path.is_symlink():
                raise MediaSyncError("cache_symlink_rejected", exit_code=3)
            if path.is_file():
                relative = path.relative_to(root).as_posix().casefold()
                if relative not in allowed:
                    raise MediaSyncError("cache_contents_invalid", exit_code=3)
    except MediaSyncError:
        raise
    except OSError as error:
        raise MediaSyncError("cache_unavailable", exit_code=3) from error


def _validated_base_url(value: str) -> str:
    parsed = urlparse(value)
    test_loopback = (
        os.environ.get("APP_ENV") == "test"
        and parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
    )
    if (
        (parsed.scheme != "https" and not test_loopback)
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise MediaSyncError("media_base_url_invalid", exit_code=2)
    return value.rstrip("/")


def _download_one(
    *,
    client: httpx.Client,
    url: str,
    destination: Path,
    expected_sha256: str,
) -> None:
    if destination.is_symlink():
        raise MediaSyncError("cache_symlink_rejected", exit_code=3)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    digest = hashlib.sha256()
    byte_count = 0
    try:
        with client.stream("GET", url, headers={"Accept-Encoding": "identity"}) as response:
            response.raise_for_status()
            declared_length = _content_length(response.headers.get("Content-Length"))
            with temporary.open("xb") as output:
                for chunk in response.iter_raw():
                    output.write(chunk)
                    digest.update(chunk)
                    byte_count += len(chunk)
                output.flush()
                os.fsync(output.fileno())
        if declared_length is not None and byte_count != declared_length:
            raise MediaSyncError("content_length_mismatch", exit_code=3)
        if digest.hexdigest() != expected_sha256:
            raise MediaSyncError("sha256_mismatch", exit_code=3)
        os.replace(temporary, destination)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def _content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        length = int(value)
    except ValueError as error:
        raise MediaSyncError("content_length_invalid", exit_code=3) from error
    if length < 0:
        raise MediaSyncError("content_length_invalid", exit_code=3)
    return length


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synchronize controlled competition media")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    arguments = parser.parse_args(argv)
    base_url = os.environ.get("PUBLIC_MEDIA_BASE_URL", "")
    try:
        count = sync_media(
            manifest_path=arguments.manifest,
            target_root=arguments.target,
            base_url=base_url,
        )
    except MediaSyncError as error:
        print(f"media_sync_failed code={error.code}", file=sys.stderr)
        return error.exit_code
    except Exception:
        print("media_sync_failed code=unexpected_error", file=sys.stderr)
        return 3
    print(f"media_sync_pass source_count={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
