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

import imageio_ffmpeg
import pytest

PROJECT_ROOT = Path(__file__).parents[3]


@contextmanager
def controlled_media_server(media: dict[str, bytes]) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            content = media.get(self.path)
            if content is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(content)))
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


def write_manifest(
    tmp_path: Path,
    *,
    declared_duration_seconds: float = 1.0,
) -> tuple[Path, dict[str, bytes]]:
    fixture = tmp_path / "download-fixture.mp4"
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=16x16:r=1",
            "-t",
            "1",
            "-c:v",
            "mpeg4",
            "-an",
            str(fixture),
        ],
        check=True,
    )
    content = fixture.read_bytes()
    media: dict[str, bytes] = {}
    sources: list[dict[str, object]] = []
    for index in range(1, 6):
        relative_path = f"assets/{index:032x}.mp4"
        media[f"/media/{relative_path}"] = content
        sources.append(
            {
                "id": f"source-{index:012x}",
                "title": "Controlled training video",
                "media_path": relative_path,
                "duration_seconds": declared_duration_seconds,
                "sha256": hashlib.sha256(content).hexdigest(),
                "origin_url": None,
            }
        )
    manifest = tmp_path / "media-manifest.json"
    manifest.write_text(json.dumps({"version": 1, "sources": sources}), encoding="utf-8")
    return manifest, media


def write_valid_cache(tmp_path: Path) -> tuple[Path, Path]:
    manifest, media = write_manifest(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    target = tmp_path / "cache"
    for source in payload["sources"]:
        relative_path = source["media_path"]
        destination = target / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(media[f"/media/{relative_path}"])
    return manifest, target


def run_startup(
    manifest: Path,
    target: Path,
    base_url: str,
) -> subprocess.CompletedProcess[str]:
    server_marker = target.parent / "server-started"
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "test",
            "SOURCE_MANIFEST_PATH": str(manifest),
            "SOURCE_MEDIA_ROOT": str(target),
            "PUBLIC_MEDIA_BASE_URL": base_url,
        }
    )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "hakimi_analysis.startup",
            "--",
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('started')",
            str(server_marker),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )


def test_startup_synchronizes_exactly_five_sources_before_exec(tmp_path: Path) -> None:
    manifest, media = write_manifest(tmp_path)
    target = tmp_path / "cache"

    with controlled_media_server(media) as base_url:
        result = run_startup(manifest, target, base_url)

    assert result.returncode == 0
    assert result.stdout == "controlled_source_startup_pass source_count=5\n"
    assert result.stderr == ""
    assert len(list(target.rglob("*.mp4"))) == 5
    assert (tmp_path / "server-started").read_text() == "started"


def test_startup_does_not_exec_when_any_controlled_source_fails(tmp_path: Path) -> None:
    manifest, media = write_manifest(tmp_path)
    media.pop("/media/assets/00000000000000000000000000000003.mp4")

    with controlled_media_server(media) as base_url:
        result = run_startup(manifest, tmp_path / "cache", base_url)

    assert result.returncode == 3
    assert result.stdout == ""
    assert result.stderr == "controlled_source_startup_failed code=transfer_failed\n"
    assert not (tmp_path / "server-started").exists()


def test_startup_does_not_exec_when_newly_synced_media_duration_is_wrong(
    tmp_path: Path,
) -> None:
    manifest, media = write_manifest(tmp_path, declared_duration_seconds=3.0)

    with controlled_media_server(media) as base_url:
        result = run_startup(manifest, tmp_path / "cache", base_url)

    assert result.returncode == 3
    assert result.stdout == ""
    assert result.stderr == "controlled_source_startup_failed code=media_cache_invalid\n"
    assert not (tmp_path / "server-started").exists()


def test_startup_skips_transfer_when_the_exact_five_source_cache_is_valid(
    tmp_path: Path,
) -> None:
    manifest, target = write_valid_cache(tmp_path)

    result = run_startup(manifest, target, "http://127.0.0.1:1/media")

    assert result.returncode == 0
    assert result.stdout == "controlled_source_startup_pass source_count=5\n"
    assert result.stderr == ""
    assert (tmp_path / "server-started").read_text() == "started"


def test_startup_does_not_exec_when_valid_cache_contains_an_unlisted_file(
    tmp_path: Path,
) -> None:
    manifest, target = write_valid_cache(tmp_path)
    unexpected = target / "unlisted-private-video.mp4"
    unexpected.write_bytes(b"synthetic-unlisted-media")

    result = run_startup(manifest, target, "http://127.0.0.1:1/media")

    assert result.returncode == 3
    assert result.stdout == ""
    assert result.stderr == "controlled_source_startup_failed code=cache_contents_invalid\n"
    assert unexpected.read_bytes() == b"synthetic-unlisted-media"
    assert not (tmp_path / "server-started").exists()


def test_startup_does_not_exec_when_valid_cache_contains_a_symbolic_link(
    tmp_path: Path,
) -> None:
    manifest, target = write_valid_cache(tmp_path)
    outside = tmp_path / "outside-cache"
    outside.mkdir()
    link = target / "unlisted-link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("symbolic links are unavailable for this test user")
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(outside)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            pytest.skip("directory links are unavailable for this test user")

    result = run_startup(manifest, target, "http://127.0.0.1:1/media")

    assert result.returncode == 3
    assert result.stdout == ""
    assert result.stderr == "controlled_source_startup_failed code=cache_symlink_rejected\n"
    assert not (tmp_path / "server-started").exists()


def test_container_runs_controlled_source_startup_before_uvicorn() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY competition/media-manifest.json competition/media-manifest.json" in dockerfile
    assert "SOURCE_MANIFEST_PATH=/workspace/competition/media-manifest.json" in dockerfile
    assert "SOURCE_MEDIA_ROOT=/workspace/tmp/controlled-media" in dockerfile
    assert (
        'CMD ["services/analysis-api/.venv/bin/python", "-m", '
        '"hakimi_analysis.startup", "--", '
        '"services/analysis-api/.venv/bin/uvicorn"'
    ) in dockerfile
    assert "COPY *.mp4" not in dockerfile
