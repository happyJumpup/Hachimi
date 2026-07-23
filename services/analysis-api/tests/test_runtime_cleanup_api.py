import json
import subprocess
from pathlib import Path
from typing import cast

import httpx
import pytest

from hakimi_analysis.app import create_app
from hakimi_analysis.runtime_cleanup import RuntimeCleanupMonitor


@pytest.mark.asyncio
async def test_runtime_cleanup_reports_only_redacted_counts_and_clean_state(
    tmp_path: Path,
) -> None:
    residue = tmp_path / "private-analysis-run"
    residue.mkdir()
    (residue / "private-audio.wav").write_bytes(b"private")
    monitor = RuntimeCleanupMonitor(tmp_path, descendant_ffmpeg_probe=lambda: set())
    app = create_app(runtime_cleanup=monitor)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get("/api/v1/runtime-cleanup")
        openapi = await client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json() == {
        "clean": False,
        "residue_count": 1,
        "ffmpeg_process_count": 0,
    }
    encoded = json.dumps(response.json())
    assert "private-analysis-run" not in encoded
    assert "private-audio.wav" not in encoded
    assert str(tmp_path) not in encoded
    assert response.headers["cache-control"] == "no-store"
    assert "/api/v1/runtime-cleanup" not in openapi.json()["paths"]


@pytest.mark.asyncio
async def test_runtime_cleanup_counts_tracked_and_descendant_ffmpeg_without_exposing_pids(
    tmp_path: Path,
) -> None:
    class RunningProcess:
        pid = 1234
        returncode: int | None = None

        def poll(self) -> int | None:
            return self.returncode

    running_process = RunningProcess()
    process = cast(subprocess.Popen[bytes], running_process)
    descendants = {5678}
    monitor = RuntimeCleanupMonitor(
        tmp_path,
        descendant_ffmpeg_probe=lambda: descendants.copy(),
    )
    monitor.register_ffmpeg_process(process)
    app = create_app(runtime_cleanup=monitor)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        active = await client.get("/api/v1/runtime-cleanup")
        monitor.unregister_ffmpeg_process(process)
        still_active = await client.get("/api/v1/runtime-cleanup")
        running_process.returncode = 0
        monitor.unregister_ffmpeg_process(process)
        descendants.clear()
        clean = await client.get("/api/v1/runtime-cleanup")

    assert active.json() == {
        "clean": False,
        "residue_count": 0,
        "ffmpeg_process_count": 2,
    }
    assert still_active.json() == active.json()
    assert clean.json() == {
        "clean": True,
        "residue_count": 0,
        "ffmpeg_process_count": 0,
    }
    assert "1234" not in active.text
    assert "5678" not in active.text


@pytest.mark.asyncio
async def test_runtime_cleanup_fails_closed_when_the_probe_is_not_configured() -> None:
    app = create_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get("/api/v1/runtime-cleanup")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "code": "runtime_cleanup_probe_unavailable",
    }
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_runtime_cleanup_fails_closed_when_the_temp_root_cannot_be_scanned(
    tmp_path: Path,
) -> None:
    invalid_root = tmp_path / "not-a-directory"
    invalid_root.write_bytes(b"private")
    app = create_app(
        runtime_cleanup=RuntimeCleanupMonitor(
            invalid_root,
            descendant_ffmpeg_probe=lambda: set(),
        )
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get("/api/v1/runtime-cleanup")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "code": "runtime_cleanup_probe_unavailable",
    }
    assert str(invalid_root) not in response.text
