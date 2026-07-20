import asyncio
import sys
from pathlib import Path
from typing import Any

import imageio_ffmpeg
import pytest

from hakimi_analysis.media import AnalysisWindow, LocalMediaProcessor, MediaProcessingError


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


async def create_colour_transition_video(path: Path) -> None:
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    process = await asyncio.create_subprocess_exec(
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=red:s=64x64:r=10:d=1.5",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=64x64:r=10:d=1.5",
        "-filter_complex",
        "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map",
        "[v]",
        "-c:v",
        "mpeg4",
        "-g",
        "100",
        "-pix_fmt",
        "yuv420p",
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


@pytest.mark.asyncio
async def test_visual_window_uses_builtin_mpeg4_encoder_for_accurate_seeking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    output = tmp_path / "window.mp4"
    source.write_bytes(b"not-read")
    processor = LocalMediaProcessor()
    captured: tuple[str, ...] = ()

    async def capture(_operation: str, *arguments: str) -> None:
        nonlocal captured
        captured = arguments

    monkeypatch.setattr(processor, "_run_ffmpeg", capture)
    await processor._extract_video(
        source,
        output,
        AnalysisWindow(start_seconds=5, end_seconds=12, expanded=False),
    )

    assert "libx264" not in captured
    assert captured[captured.index("-c:v") + 1] == "mpeg4"
    assert captured[captured.index("-pix_fmt") + 1] == "yuv420p"


@pytest.mark.asyncio
async def test_visual_window_starts_at_requested_non_keyframe_content(tmp_path: Path) -> None:
    source = tmp_path / "colour-transition.mp4"
    output = tmp_path / "window.mp4"
    await create_colour_transition_video(source)
    processor = LocalMediaProcessor()
    await processor._extract_video(
        source,
        output,
        AnalysisWindow(start_seconds=1.75, end_seconds=2.25, expanded=False),
    )
    reader: Any = imageio_ffmpeg.read_frames(str(output), pix_fmt="rgb24")
    try:
        metadata = next(reader)
        first_frame = next(reader)
    finally:
        reader.close()

    assert 0.4 <= float(metadata["duration"]) <= 0.6
    red = first_frame[0]
    blue = first_frame[2]
    assert blue > 180
    assert red < 80


@pytest.mark.asyncio
async def test_prepared_media_and_ffmpeg_are_stopped_when_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    both_started = asyncio.Event()
    processes: list[asyncio.subprocess.Process] = []
    real_create_subprocess_exec = asyncio.create_subprocess_exec

    async def create_blocking_process(*_args: Any, **_kwargs: Any) -> asyncio.subprocess.Process:
        process = await real_create_subprocess_exec(
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        processes.append(process)
        if len(processes) == 2:
            both_started.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_blocking_process)
    processor = LocalMediaProcessor(temp_root=tmp_path / "runs")
    prepared_directories: list[Path] = []

    async def prepare() -> None:
        async with processor.prepare(
            source,
            AnalysisWindow(start_seconds=0, end_seconds=1, expanded=False),
        ) as prepared:
            prepared_directories.append(prepared.directory)
            raise AssertionError("cancelled extraction must not yield prepared media")

    task = asyncio.create_task(prepare())
    await asyncio.wait_for(both_started.wait(), timeout=1)
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(processes) == 2
    assert all(process.returncode is not None for process in processes)
    assert not any((tmp_path / "runs").iterdir())
    assert prepared_directories == []


@pytest.mark.asyncio
async def test_failed_extraction_stops_the_other_ffmpeg_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    processes: list[asyncio.subprocess.Process] = []
    real_create_subprocess_exec = asyncio.create_subprocess_exec
    call_count = 0

    async def create_test_process(*_args: Any, **_kwargs: Any) -> asyncio.subprocess.Process:
        nonlocal call_count
        call_count += 1
        code = (
            "import sys, time; time.sleep(0.1); sys.exit(1)"
            if call_count == 1
            else "import time; time.sleep(30)"
        )
        process = await real_create_subprocess_exec(
            sys.executable,
            "-c",
            code,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_test_process)
    processor = LocalMediaProcessor(temp_root=tmp_path / "runs")

    with pytest.raises(MediaProcessingError):
        async with processor.prepare(
            source,
            AnalysisWindow(start_seconds=0, end_seconds=1, expanded=False),
        ):
            raise AssertionError("failed extraction must not yield prepared media")

    assert len(processes) == 2
    assert all(process.returncode is not None for process in processes)
    assert not any((tmp_path / "runs").iterdir())
