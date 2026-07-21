import asyncio
import math
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import imageio_ffmpeg

from hakimi_analysis.sources import MAX_ANALYZABLE_SOURCE_DURATION_SECONDS

VISUAL_SAMPLE_INTERVAL_SECONDS = 3.5
MAX_VISUAL_FRAME_COUNT = 18


@dataclass(frozen=True, slots=True)
class AnalysisWindow:
    start_seconds: float
    end_seconds: float
    expanded: bool

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


def calculate_analysis_window(
    *,
    trigger_seconds: float,
    duration_seconds: float,
    expanded: bool = False,
) -> AnalysisWindow:
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")
    if trigger_seconds < 0 or trigger_seconds > duration_seconds:
        raise ValueError("trigger_seconds must be inside the source video")

    seconds_before = 30 if expanded else 15
    seconds_after = 40 if expanded else 20
    return AnalysisWindow(
        start_seconds=max(0, trigger_seconds - seconds_before),
        end_seconds=min(duration_seconds, trigger_seconds + seconds_after),
        expanded=expanded,
    )


@dataclass(frozen=True, slots=True)
class PreparedMedia:
    directory: Path
    video_path: Path
    audio_path: Path
    window: AnalysisWindow
    contact_sheet_path: Path | None = None
    contact_sheet_timestamps: tuple[float, ...] = ()
    contact_sheet_ready: asyncio.Task[None] | None = None


class MediaProcessingError(RuntimeError):
    pass


class LocalMediaProcessor:
    def __init__(
        self,
        *,
        temp_root: Path | None = None,
        command_timeout_seconds: float = 90,
        max_source_duration_seconds: float = MAX_ANALYZABLE_SOURCE_DURATION_SECONDS,
        visual_sample_interval_seconds: float = VISUAL_SAMPLE_INTERVAL_SECONDS,
    ) -> None:
        if max_source_duration_seconds <= 0 or visual_sample_interval_seconds <= 0:
            raise ValueError("media analysis limits must be positive")
        self._temp_root = temp_root
        self._command_timeout_seconds = command_timeout_seconds
        self._max_source_duration_seconds = max_source_duration_seconds
        self._visual_sample_interval_seconds = visual_sample_interval_seconds
        if self._temp_root is not None:
            self._temp_root.mkdir(parents=True, exist_ok=True)

    async def probe_duration(self, source_path: Path) -> float:
        return await asyncio.to_thread(probe_duration_sync, source_path)

    @asynccontextmanager
    async def prepare_source(
        self,
        source_path: Path,
        duration_seconds: float,
    ) -> AsyncIterator[PreparedMedia]:
        if not source_path.is_file():
            raise MediaProcessingError("configured source video is unavailable")
        if duration_seconds <= 0:
            raise MediaProcessingError("source video duration must be positive")
        if duration_seconds > self._max_source_duration_seconds:
            raise MediaProcessingError("source video exceeds the full-source analysis limit")

        window = AnalysisWindow(
            start_seconds=0,
            end_seconds=duration_seconds,
            expanded=False,
        )
        root = str(self._temp_root) if self._temp_root is not None else None
        with tempfile.TemporaryDirectory(prefix="hachimi-analysis-", dir=root) as temp_directory:
            directory = Path(temp_directory)
            audio_path = directory / "speech-source.wav"
            contact_sheet_path = directory / "visual-contact-sheet.jpg"
            frame_count = min(
                MAX_VISUAL_FRAME_COUNT,
                max(1, math.ceil(duration_seconds / self._visual_sample_interval_seconds)),
            )
            frame_interval_seconds = duration_seconds / frame_count
            timestamps = tuple(
                round(index * frame_interval_seconds, 3) for index in range(frame_count)
            )
            audio_task = asyncio.create_task(self._extract_audio(source_path, audio_path, window))
            contact_sheet_task = asyncio.create_task(
                self._extract_contact_sheet(
                    source_path,
                    contact_sheet_path,
                    frame_interval_seconds=frame_interval_seconds,
                    frame_count=frame_count,
                )
            )
            extraction_tasks = (audio_task, contact_sheet_task)
            try:
                await audio_task
                yield PreparedMedia(
                    directory=directory,
                    video_path=source_path,
                    audio_path=audio_path,
                    window=window,
                    contact_sheet_path=contact_sheet_path,
                    contact_sheet_timestamps=timestamps,
                    contact_sheet_ready=contact_sheet_task,
                )
            finally:
                for task in extraction_tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*extraction_tasks, return_exceptions=True)

    @asynccontextmanager
    async def prepare(
        self,
        source_path: Path,
        window: AnalysisWindow,
    ) -> AsyncIterator[PreparedMedia]:
        if not source_path.is_file():
            raise MediaProcessingError("configured source video is unavailable")
        if window.duration_seconds <= 0:
            raise MediaProcessingError("analysis window is empty")

        root = str(self._temp_root) if self._temp_root is not None else None
        with tempfile.TemporaryDirectory(prefix="hachimi-analysis-", dir=root) as temp_directory:
            directory = Path(temp_directory)
            video_path = directory / "visual-window.mp4"
            audio_path = directory / "speech-window.wav"
            extraction_tasks = [
                asyncio.create_task(self._extract_video(source_path, video_path, window)),
                asyncio.create_task(self._extract_audio(source_path, audio_path, window)),
            ]
            try:
                await asyncio.gather(*extraction_tasks)
            finally:
                for task in extraction_tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*extraction_tasks, return_exceptions=True)
            yield PreparedMedia(
                directory=directory,
                video_path=video_path,
                audio_path=audio_path,
                window=window,
            )

    async def _extract_video(
        self,
        source_path: Path,
        output_path: Path,
        window: AnalysisWindow,
    ) -> None:
        await self._run_ffmpeg(
            "video",
            "-ss",
            f"{window.start_seconds:.3f}",
            "-i",
            str(source_path),
            "-t",
            f"{window.duration_seconds:.3f}",
            "-an",
            "-c:v",
            "mpeg4",
            "-q:v",
            "4",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output_path),
        )

    async def _extract_audio(
        self,
        source_path: Path,
        output_path: Path,
        window: AnalysisWindow,
    ) -> None:
        await self._run_ffmpeg(
            "audio",
            "-ss",
            f"{window.start_seconds:.3f}",
            "-i",
            str(source_path),
            "-t",
            f"{window.duration_seconds:.3f}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        )

    async def _extract_contact_sheet(
        self,
        source_path: Path,
        output_path: Path,
        *,
        frame_interval_seconds: float,
        frame_count: int,
    ) -> None:
        columns = 4
        rows = math.ceil(frame_count / columns)
        await self._run_ffmpeg(
            "contact sheet",
            "-i",
            str(source_path),
            "-vf",
            (
                f"fps=1/{frame_interval_seconds:.6f},scale=160:-1,"
                f"tile={columns}x{rows}:padding=2:margin=2"
            ),
            "-frames:v",
            "1",
            "-q:v",
            "4",
            str(output_path),
        )

    async def _run_ffmpeg(self, operation: str, *arguments: str) -> None:
        executable = imageio_ffmpeg.get_ffmpeg_exe()
        process = await asyncio.create_subprocess_exec(
            executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            *arguments,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(self._command_timeout_seconds):
                _, _stderr = await process.communicate()
        except TimeoutError as error:
            await _stop_process(process)
            raise MediaProcessingError(f"{operation} extraction timed out") from error
        except asyncio.CancelledError:
            await _stop_process(process)
            raise
        if process.returncode != 0:
            raise MediaProcessingError(f"{operation} extraction failed")
        output_path = Path(arguments[-1])
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise MediaProcessingError(f"{operation} extraction produced no output")


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.kill()
    await process.wait()


def probe_duration_sync(source_path: Path) -> float:
    if not source_path.is_file():
        raise MediaProcessingError("configured source video is unavailable")
    reader: Any = imageio_ffmpeg.read_frames(str(source_path), pix_fmt="rgb24")
    try:
        metadata = next(reader)
        duration = float(metadata.get("duration", 0))
    finally:
        reader.close()
    if duration <= 0:
        raise MediaProcessingError("could not determine source duration")
    return duration
