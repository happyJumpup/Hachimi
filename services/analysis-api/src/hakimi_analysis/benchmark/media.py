import asyncio
import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

import imageio_ffmpeg

from hakimi_analysis.benchmark.manifest import BenchmarkManifest, write_manifest


class MediaPreparationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProbeMedia:
    av_path: Path
    silent_video_path: Path
    audio_path: Path


class ProbeMediaDirectory:
    def __init__(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="hachimi-native-av-probe-")
        self.path = Path(self._temporary.name)

    def cleanup(self) -> None:
        self._temporary.cleanup()


async def create_probe_media(
    source_av: Path,
    *,
    duration_seconds: int,
    synthetic: bool,
    root: Path,
) -> ProbeMedia:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    prefix = "synthetic" if synthetic else "real"
    av_path = root / f"{prefix}-{duration_seconds}s-av.mp4"
    silent_path = root / f"{prefix}-{duration_seconds}s-silent.mp4"
    audio_path = root / f"{prefix}-{duration_seconds}s-audio.wav"
    if synthetic:
        await _run_ffmpeg(
            ffmpeg,
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=12",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000",
            "-t",
            str(duration_seconds),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-y",
            str(av_path),
        )
    else:
        await _run_ffmpeg(
            ffmpeg,
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


async def prepare_manifest_media(
    manifest: BenchmarkManifest,
    *,
    manifest_path: Path,
) -> None:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    for sample in manifest.samples:
        for path in (sample.av_path, sample.silent_video_path, sample.audio_path):
            path.parent.mkdir(parents=True, exist_ok=True)
        await _run_ffmpeg(
            ffmpeg,
            "-ss",
            str(sample.window_start_seconds),
            "-t",
            str(sample.duration_seconds),
            "-i",
            str(sample.source_path),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "22",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            "-y",
            str(sample.av_path),
        )
        await _run_ffmpeg(
            ffmpeg,
            "-i",
            str(sample.av_path),
            "-map",
            "0:v:0",
            "-c:v",
            "copy",
            "-an",
            "-y",
            str(sample.silent_video_path),
        )
        if await _has_audio_stream(ffmpeg, sample.av_path):
            await _run_ffmpeg(
                ffmpeg,
                "-i",
                str(sample.av_path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                "-y",
                str(sample.audio_path),
            )
        else:
            await _run_ffmpeg(
                ffmpeg,
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=16000:cl=mono",
                "-t",
                str(sample.duration_seconds),
                "-c:a",
                "pcm_s16le",
                "-y",
                str(sample.audio_path),
            )
        sample.av_sha256 = await asyncio.to_thread(_sha256, sample.av_path)
        sample.silent_video_sha256 = await asyncio.to_thread(
            _sha256, sample.silent_video_path
        )
        sample.audio_sha256 = await asyncio.to_thread(_sha256, sample.audio_path)
    write_manifest(manifest_path, manifest)


async def _run_ffmpeg(ffmpeg: str, *arguments: str) -> None:
    returncode, _ = await _run_process(
        ffmpeg,
        arguments,
    )
    if returncode != 0:
        raise MediaPreparationError("ffmpeg_failed")


async def _has_audio_stream(ffmpeg: str, path: Path) -> bool:
    returncode, stderr = await _run_process(
        ffmpeg,
        (
            "-hide_banner",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-frames:a",
            "1",
            "-f",
            "null",
            "-",
        ),
    )
    if returncode == 0:
        return True
    if "matches no streams" in stderr or "does not contain any stream" in stderr:
        return False
    raise MediaPreparationError("ffmpeg_audio_probe_failed")


async def _run_process(
    executable: str,
    arguments: tuple[str, ...],
) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        executable,
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await process.communicate()
    except asyncio.CancelledError:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()
        raise
    return process.returncode or 0, stderr.decode("utf-8", errors="replace")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
