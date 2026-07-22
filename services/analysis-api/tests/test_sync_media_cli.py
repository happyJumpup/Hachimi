import hashlib
import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

import pytest


@contextmanager
def media_server(content: bytes, *, declared_length: int | None = None) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/media/competition/arm-01.mp4":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header(
                "Content-Length",
                str(len(content) if declared_length is None else declared_length),
            )
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = cast(tuple[str, int], server.server_address)
        yield f"http://{host}:{port}/media"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def write_manifest(tmp_path: Path, content: bytes) -> Path:
    manifest = tmp_path / "media-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "sources": [
                    {
                        "id": "arm-01",
                        "title": "手臂训练 01",
                        "media_path": "competition/arm-01.mp4",
                        "duration_seconds": 60,
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "origin_url": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest


def run_sync(manifest: Path, target: Path, base_url: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "test",
            "PUBLIC_MEDIA_BASE_URL": base_url,
        }
    )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "hakimi_analysis.sync_media",
            "--manifest",
            str(manifest),
            "--target",
            str(target),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )


def test_sync_media_cli_downloads_and_verifies_authorized_manifest_media(
    tmp_path: Path,
) -> None:
    content = b"team-owned-competition-video"
    manifest = write_manifest(tmp_path, content)
    target = tmp_path / "cache"

    with media_server(content) as base_url:
        result = run_sync(manifest, target, base_url)

    assert result.returncode == 0
    assert result.stdout == "media_sync_pass source_count=1\n"
    assert result.stderr == ""
    assert (target / "competition" / "arm-01.mp4").read_bytes() == content


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("id", "../private-source"),
        ("media_path", "../doubao-key.txt"),
        ("sha256", "NOT-A-SHA256"),
    ],
)
def test_sync_media_cli_rejects_unsafe_manifest_fields_without_echoing_them(
    tmp_path: Path,
    field: str,
    unsafe_value: str,
) -> None:
    manifest = write_manifest(tmp_path, b"authorized")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["sources"][0][field] = unsafe_value
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    result = run_sync(
        manifest,
        tmp_path / "cache",
        "https://media.example.invalid/private-deployment-prefix",
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "media_sync_failed code=manifest_invalid\n"
    assert unsafe_value not in result.stderr
    assert "private-deployment-prefix" not in result.stderr


def test_sync_media_cli_fails_closed_when_cache_contains_unlisted_media(tmp_path: Path) -> None:
    content = b"team-owned-competition-video"
    manifest = write_manifest(tmp_path, content)
    target = tmp_path / "cache"
    target.mkdir()
    unexpected = target / "unlisted-private-video.mp4"
    unexpected.write_bytes(b"must-not-be-touched")

    with media_server(content) as base_url:
        result = run_sync(manifest, target, base_url)

    assert result.returncode == 3
    assert result.stdout == ""
    assert result.stderr == "media_sync_failed code=cache_contents_invalid\n"
    assert unexpected.read_bytes() == b"must-not-be-touched"
    assert not (target / "competition" / "arm-01.mp4").exists()


def test_sync_media_cli_removes_partial_file_when_transfer_size_is_wrong(tmp_path: Path) -> None:
    content = b"team-owned-competition-video"
    manifest = write_manifest(tmp_path, content)
    target = tmp_path / "cache"

    with media_server(content, declared_length=len(content) + 5) as base_url:
        result = run_sync(manifest, target, base_url)

    assert result.returncode == 3
    assert result.stdout == ""
    assert result.stderr == "media_sync_failed code=transfer_failed\n"
    assert not (target / "competition" / "arm-01.mp4").exists()
    assert list(target.rglob("*.tmp")) == []


def test_sync_media_cli_rejects_sha_mismatch_without_replacing_cache(tmp_path: Path) -> None:
    content = b"team-owned-competition-video"
    manifest = write_manifest(tmp_path, content)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["sources"][0]["sha256"] = hashlib.sha256(b"different-video").hexdigest()
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    target = tmp_path / "cache"

    with media_server(content) as base_url:
        result = run_sync(manifest, target, base_url)

    assert result.returncode == 3
    assert result.stdout == ""
    assert result.stderr == "media_sync_failed code=sha256_mismatch\n"
    assert not (target / "competition" / "arm-01.mp4").exists()
    assert list(target.rglob("*.tmp")) == []
