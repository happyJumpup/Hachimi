import asyncio
from pathlib import Path

import imageio_ffmpeg
import pytest

from hakimi_analysis.media import AnalysisWindow, LocalMediaProcessor


async def create_synthetic_video(path: Path) -> None:
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    process = await asyncio.create_subprocess_exec(
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=0x101820:s=320x568:r=15",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=16000",
        "-t",
        "2",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-y",
        str(path),
    )
    _, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode("utf-8", errors="replace")


@pytest.mark.asyncio
async def test_prepared_media_is_bounded_and_removed_after_use(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    await create_synthetic_video(source)
    processor = LocalMediaProcessor(temp_root=tmp_path / "runs")

    duration = await processor.probe_duration(source)
    assert 1.9 <= duration <= 2.1

    prepared_directory: Path | None = None
    async with processor.prepare(
        source,
        AnalysisWindow(start_seconds=0.25, end_seconds=1.75, expanded=False),
    ) as prepared:
        prepared_directory = prepared.directory
        assert prepared.video_path.is_file()
        assert prepared.audio_path.is_file()
        assert prepared.window.start_seconds == 0.25

    assert prepared_directory is not None
    assert not prepared_directory.exists()
