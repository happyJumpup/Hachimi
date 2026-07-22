import asyncio
import math
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import imageio_ffmpeg

from hakimi_analysis.benchmark.long_media import build_complete_source_chunks
from hakimi_analysis.benchmark.long_models import (
    LongExperimentSource,
    LongVideoChunk,
    LongVideoChunkPolicy,
)
from hakimi_analysis.benchmark.long_pipeline import (
    LongPreparedChunk,
    LongPreparedSource,
)
from hakimi_analysis.benchmark.media import (
    ProbeMedia,
    _has_audio_stream,
    _run_ffmpeg,
    create_probe_media,
)
from hakimi_analysis.media import MAX_VISUAL_FRAME_COUNT, VISUAL_SAMPLE_INTERVAL_SECONDS

_DERIVED_DURATION_TOLERANCE_SECONDS = 1.0


class LongMediaPreparationError(RuntimeError):
    pass


class LongMediaPreparer:
    """Creates ephemeral full-audio and per-chunk visual inputs for one source."""

    def __init__(
        self,
        *,
        temp_root: Path,
        command_timeout_seconds: float = 180,
        max_parallel_ffmpeg: int = 2,
        media_probe: Callable[[Path], tuple[int, float]] | None = None,
    ) -> None:
        if command_timeout_seconds <= 0 or max_parallel_ffmpeg <= 0:
            raise ValueError("long media preparation limits must be positive")
        self._temp_root = temp_root
        self._command_timeout_seconds = command_timeout_seconds
        self._ffmpeg_permits = asyncio.Semaphore(max_parallel_ffmpeg)
        self._media_probe = media_probe or _probe_derived_video
        self._temp_root.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def prepare_source(
        self,
        source: LongExperimentSource,
        chunk_policy: LongVideoChunkPolicy,
    ) -> AsyncIterator[LongPreparedSource]:
        if not source.source_path.is_file():
            raise LongMediaPreparationError("configured source video is unavailable")
        with tempfile.TemporaryDirectory(
            prefix=f"hachimi-long-{source.source_id}-",
            dir=self._temp_root,
        ) as temporary_directory:
            directory = Path(temporary_directory)
            audio_path = directory / "source-audio.wav"
            chunks = build_complete_source_chunks(source, chunk_policy)
            audio_task = asyncio.create_task(self._extract_audio(source, audio_path))
            chunk_tasks = [
                asyncio.create_task(self._prepare_chunk(source, chunk, directory))
                for chunk in chunks
            ]
            tasks = [audio_task, *chunk_tasks]
            try:
                await asyncio.gather(*tasks)
                prepared_chunks = tuple(task.result() for task in chunk_tasks)
                yield LongPreparedSource(
                    source_id=source.source_id,
                    duration_seconds=source.duration_seconds,
                    audio_path=audio_path,
                    chunks=prepared_chunks,
                )
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _prepare_chunk(
        self,
        source: LongExperimentSource,
        chunk: LongVideoChunk,
        directory: Path,
    ) -> LongPreparedChunk:
        chunk_directory = directory / f"chunk-{chunk.index:03d}"
        chunk_directory.mkdir()
        silent_video_path = chunk_directory / "silent.mp4"
        contact_sheet_path = chunk_directory / "contact-sheet.jpg"
        frame_count = min(
            MAX_VISUAL_FRAME_COUNT,
            max(1, math.ceil(chunk.duration_seconds / VISUAL_SAMPLE_INTERVAL_SECONDS)),
        )
        frame_interval_seconds = chunk.duration_seconds / frame_count
        frame_times_seconds = tuple(
            round(chunk.start_seconds + index * frame_interval_seconds, 3)
            for index in range(frame_count)
        )
        await asyncio.gather(
            self._extract_silent_video(source, chunk, silent_video_path),
            self._extract_contact_sheet(
                source,
                chunk,
                contact_sheet_path,
                frame_interval_seconds=frame_interval_seconds,
                frame_count=frame_count,
            ),
        )
        await self._validate_chunk_outputs(
            chunk,
            silent_video_path,
            contact_sheet_path,
            expected_contact_sheet_frames=frame_count,
        )
        return LongPreparedChunk(
            chunk=chunk,
            silent_video_path=silent_video_path,
            contact_sheet_path=contact_sheet_path,
            frame_times_seconds=frame_times_seconds,
        )

    async def _validate_chunk_outputs(
        self,
        chunk: LongVideoChunk,
        silent_video_path: Path,
        contact_sheet_path: Path,
        *,
        expected_contact_sheet_frames: int,
    ) -> None:
        """Reject truncated derived media before it can count as coverage."""

        if not contact_sheet_path.is_file() or contact_sheet_path.stat().st_size <= 0:
            raise LongMediaPreparationError("contact sheet extraction produced no output")
        try:
            frame_count, duration_seconds = await asyncio.to_thread(
                self._media_probe,
                silent_video_path,
            )
        except Exception as error:
            raise LongMediaPreparationError("could not inspect silent video chunk") from error
        if duration_seconds + _DERIVED_DURATION_TOLERANCE_SECONDS < chunk.duration_seconds:
            raise LongMediaPreparationError("silent video chunk is truncated")
        if frame_count < expected_contact_sheet_frames:
            raise LongMediaPreparationError("silent video chunk has too few frames")

    async def _extract_audio(self, source: LongExperimentSource, output_path: Path) -> None:
        await self._run_ffmpeg(
            "audio",
            "-i",
            str(source.source_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        )

    async def _extract_silent_video(
        self,
        source: LongExperimentSource,
        chunk: LongVideoChunk,
        output_path: Path,
    ) -> None:
        await self._run_ffmpeg(
            "silent video",
            "-ss",
            f"{chunk.start_seconds:.3f}",
            "-i",
            str(source.source_path),
            "-t",
            f"{chunk.duration_seconds:.3f}",
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

    async def _extract_contact_sheet(
        self,
        source: LongExperimentSource,
        chunk: LongVideoChunk,
        output_path: Path,
        *,
        frame_interval_seconds: float,
        frame_count: int,
    ) -> None:
        columns = 4
        rows = math.ceil(frame_count / columns)
        await self._run_ffmpeg(
            "contact sheet",
            "-ss",
            f"{chunk.start_seconds:.3f}",
            "-i",
            str(source.source_path),
            "-t",
            f"{chunk.duration_seconds:.3f}",
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
        async with self._ffmpeg_permits:
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
                    await process.communicate()
            except TimeoutError as error:
                await _stop_process(process)
                raise LongMediaPreparationError(f"{operation} extraction timed out") from error
            except asyncio.CancelledError:
                await _stop_process(process)
                raise
            if process.returncode != 0:
                raise LongMediaPreparationError(f"{operation} extraction failed")
            output_path = Path(arguments[-1])
            if not output_path.is_file() or output_path.stat().st_size == 0:
                raise LongMediaPreparationError(f"{operation} extraction produced no output")


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.kill()
    await process.wait()


def _probe_derived_video(path: Path) -> tuple[int, float]:
    if not path.is_file():
        raise LongMediaPreparationError("silent video chunk is unavailable")
    frame_count, duration_seconds = imageio_ffmpeg.count_frames_and_secs(str(path))
    if frame_count <= 0 or duration_seconds <= 0:
        raise LongMediaPreparationError("could not inspect silent video chunk")
    return int(frame_count), float(duration_seconds)


async def create_long_probe_media(
    source_av: Path,
    *,
    duration_seconds: int,
    synthetic: bool,
    root: Path,
    start_seconds: float = 0,
) -> ProbeMedia:
    """Make a preflight probe without changing frozen native-AV media helpers."""

    if start_seconds < 0:
        raise ValueError("long probe start time must not be negative")
    if synthetic and start_seconds != 0:
        raise ValueError("synthetic long probe must start at zero")
    root.mkdir(parents=True, exist_ok=True)
    if synthetic:
        return await create_probe_media(
            source_av,
            duration_seconds=duration_seconds,
            synthetic=True,
            root=root,
        )

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    prefix = f"real-{start_seconds:.3f}-{duration_seconds}s"
    av_path = root / f"{prefix}-av.mp4"
    silent_path = root / f"{prefix}-silent.mp4"
    audio_path = root / f"{prefix}-audio.wav"
    await _run_ffmpeg(
        ffmpeg,
        "-ss",
        f"{start_seconds:.3f}",
        "-i",
        str(source_av),
        "-t",
        str(duration_seconds),
        "-c",
        "copy",
        "-y",
        str(av_path),
    )
    await _run_ffmpeg(
        ffmpeg,
        "-i",
        str(av_path),
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
        "-an",
        "-y",
        str(silent_path),
    )
    if await _has_audio_stream(ffmpeg, av_path):
        await _run_ffmpeg(
            ffmpeg,
            "-i",
            str(av_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-y",
            str(audio_path),
        )
    else:
        await _run_ffmpeg(
            ffmpeg,
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono",
            "-t",
            str(duration_seconds),
            "-c:a",
            "pcm_s16le",
            "-y",
            str(audio_path),
        )
    return ProbeMedia(av_path, silent_path, audio_path)
