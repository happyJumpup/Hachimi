import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any

import imageio_ffmpeg
import pytest

import hakimi_analysis.media as media_module
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


def test_full_source_prepares_without_async_subprocess_support(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    asyncio.run(create_synthetic_video(source))
    processor = LocalMediaProcessor(temp_root=tmp_path / "runs")
    loop = asyncio.new_event_loop()

    async def unsupported_subprocess_exec(*_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError

    loop.subprocess_exec = unsupported_subprocess_exec  # type: ignore[assignment]

    async def prepare() -> None:
        async with processor.prepare_source(source, duration_seconds=2) as prepared:
            assert prepared.audio_path.is_file()
            assert prepared.video_path == source

    try:
        loop.run_until_complete(prepare())
    finally:
        loop.close()


@pytest.mark.asyncio
async def test_prepare_source_extracts_one_audio_and_cleans_transient_media(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    await create_synthetic_video(source)
    runs = tmp_path / "runs"
    processor = LocalMediaProcessor(temp_root=runs)

    prepared_directory: Path | None = None
    async with processor.prepare_source(source, duration_seconds=2) as prepared:
        prepared_directory = prepared.directory
        assert prepared.window == AnalysisWindow(0, 2, False)
        assert prepared.video_path == source
        assert prepared.audio_path.is_file()
        assert set(prepared.directory.iterdir()) == {prepared.audio_path}

    assert prepared_directory is not None and not prepared_directory.exists()
    assert list(runs.iterdir()) == []


@pytest.mark.asyncio
async def test_prepare_source_maps_the_requested_source_range_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    processor = LocalMediaProcessor(temp_root=tmp_path / "runs")
    observed: list[AnalysisWindow] = []

    async def fake_audio(
        source_path: Path,
        output_path: Path,
        window: AnalysisWindow,
    ) -> None:
        assert source_path == source
        observed.append(window)
        output_path.write_bytes(b"audio")

    monkeypatch.setattr(processor, "_extract_audio", fake_audio)
    async with processor.prepare_source(
        source,
        duration_seconds=10,
        start_seconds=20,
    ) as prepared:
        assert prepared.window == AnalysisWindow(0, 10, False)

    assert observed == [AnalysisWindow(20, 30, False)]


@pytest.mark.asyncio
async def test_full_source_rejects_duration_beyond_the_profile(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    processor = LocalMediaProcessor(
        temp_root=tmp_path / "runs",
        max_source_duration_seconds=60,
    )

    with pytest.raises(MediaProcessingError):
        async with processor.prepare_source(source, duration_seconds=60.1):
            pass


@pytest.mark.asyncio
async def test_visual_chunk_uses_the_frozen_continuous_mp4_profile(
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
    assert "min(1280,iw)" in captured[captured.index("-vf") + 1]
    assert captured[captured.index("-r") + 1] == "15"
    assert captured[captured.index("-b:v") + 1] == "4000k"
    assert captured[captured.index("-maxrate") + 1] == "4000k"
    assert captured[captured.index("-bufsize") + 1] == "8000k"


@pytest.mark.asyncio
async def test_visual_chunk_starts_at_requested_non_keyframe_content(tmp_path: Path) -> None:
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
    assert first_frame[2] > 180
    assert first_frame[0] < 80


@pytest.mark.asyncio
async def test_prepare_source_cancellation_stops_ffmpeg_and_removes_temp_media(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    started = asyncio.Event()
    processes: list[subprocess.Popen[bytes]] = []
    real_popen = subprocess.Popen

    def create_blocking_process(*_args: Any, **_kwargs: Any) -> subprocess.Popen[bytes]:
        process = real_popen(
            (sys.executable, "-c", "import time; time.sleep(30)"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        processes.append(process)
        started.set()
        return process

    monkeypatch.setattr(subprocess, "Popen", create_blocking_process)
    processor = LocalMediaProcessor(temp_root=tmp_path / "runs")

    async def prepare() -> None:
        async with processor.prepare_source(source, duration_seconds=1):
            raise AssertionError("cancelled extraction must not yield media")

    task = asyncio.create_task(prepare())
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert len(processes) == 1
    assert processes[0].returncode is not None
    assert not any((tmp_path / "runs").iterdir())


@pytest.mark.asyncio
async def test_prepare_source_timeout_stops_ffmpeg_and_removes_temp_media(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    processes: list[subprocess.Popen[bytes]] = []
    real_popen = subprocess.Popen

    def create_blocking_process(*_args: Any, **_kwargs: Any) -> subprocess.Popen[bytes]:
        process = real_popen(
            (sys.executable, "-c", "import time; time.sleep(30)"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", create_blocking_process)
    processor = LocalMediaProcessor(
        temp_root=tmp_path / "runs",
        command_timeout_seconds=0.05,
    )

    with pytest.raises(MediaProcessingError, match="extraction timed out"):
        async with processor.prepare_source(source, duration_seconds=1):
            pass

    assert len(processes) == 1
    assert processes[0].returncode is not None
    assert not any((tmp_path / "runs").iterdir())


@pytest.mark.asyncio
async def test_kill_failure_uses_native_fallback_and_cleans_temp_media(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    children: list[subprocess.Popen[bytes]] = []
    real_popen = subprocess.Popen

    class KillFailsProcess:
        def __init__(self) -> None:
            self._child = real_popen(
                (sys.executable, "-c", "import time; time.sleep(30)"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            children.append(self._child)

        @property
        def returncode(self) -> int | None:
            return self._child.returncode

        @property
        def pid(self) -> int:
            return self._child.pid

        def poll(self) -> int | None:
            return self._child.poll()

        def communicate(self) -> tuple[bytes, bytes]:
            return self._child.communicate()

        def kill(self) -> None:
            raise OSError("simulated termination failure")

    monkeypatch.setattr(media_module, "PROCESS_STOP_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: KillFailsProcess())
    processor = LocalMediaProcessor(
        temp_root=tmp_path / "runs",
        command_timeout_seconds=0.05,
    )

    with pytest.raises(MediaProcessingError, match="extraction timed out"):
        async with processor.prepare_source(source, duration_seconds=1):
            pass

    assert len(children) == 1
    assert children[0].returncode is not None
    assert not any((tmp_path / "runs").iterdir())


@pytest.mark.asyncio
async def test_visual_chunk_preserves_continuous_video_and_source_offset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    work = tmp_path / "work"
    work.mkdir()
    processor = LocalMediaProcessor(temp_root=tmp_path / "runs")
    extracted_windows: list[AnalysisWindow] = []

    async def extract_video(
        source_path: Path,
        output_path: Path,
        window: AnalysisWindow,
    ) -> None:
        assert source_path == source
        extracted_windows.append(window)
        output_path.write_bytes(b"continuous-video-chunk")

    monkeypatch.setattr(processor, "_extract_video", extract_video)
    window = AnalysisWindow(start_seconds=50, end_seconds=110, expanded=False)

    prepared = await processor.prepare_visual_chunk(
        source,
        window,
        work,
        source_offset_seconds=10,
    )

    assert prepared.video_path.read_bytes() == b"continuous-video-chunk"
    assert extracted_windows == [AnalysisWindow(60, 120, False)]
