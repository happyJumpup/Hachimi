import hashlib
from collections.abc import Callable
from pathlib import Path

import imageio_ffmpeg

from hakimi_analysis.benchmark.long_models import (
    LongExperimentManifest,
    LongExperimentSource,
    LongVideoChunk,
    LongVideoChunkPolicy,
)
from hakimi_analysis.benchmark.models import BenchmarkCandidate

SOURCE_DURATION_TOLERANCE_SECONDS = 1.0


class LongVideoMediaValidationError(RuntimeError):
    pass


def build_complete_source_chunks(
    source: LongExperimentSource,
    chunk_policy: LongVideoChunkPolicy,
) -> tuple[LongVideoChunk, ...]:
    chunks: list[LongVideoChunk] = []
    start_seconds = 0.0
    index = 0
    while True:
        end_seconds = min(
            source.duration_seconds,
            start_seconds + chunk_policy.duration_seconds,
        )
        chunks.append(
            LongVideoChunk(
                source_id=source.source_id,
                index=index,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                duration_seconds=end_seconds - start_seconds,
            )
        )
        if end_seconds >= source.duration_seconds:
            return tuple(chunks)
        start_seconds += chunk_policy.stride_seconds
        index += 1


def map_chunk_candidate_to_source(
    chunk: LongVideoChunk,
    candidate: BenchmarkCandidate,
) -> BenchmarkCandidate:
    if candidate.end_seconds > chunk.duration_seconds + 1e-6:
        raise ValueError("candidate time range is outside its source chunk")
    return candidate.model_copy(
        update={
            "start_seconds": chunk.start_seconds + candidate.start_seconds,
            "end_seconds": chunk.start_seconds + candidate.end_seconds,
        }
    )


def verify_long_source_media(
    manifest: LongExperimentManifest,
    *,
    duration_probe: Callable[[Path], float] | None = None,
) -> None:
    probe = duration_probe or probe_long_source_duration
    for source in manifest.sources:
        if not source.source_path.is_file():
            raise LongVideoMediaValidationError("configured source video is unavailable")
        if sha256_file(source.source_path) != source.sha256:
            raise LongVideoMediaValidationError("source hash mismatch")
        actual_duration = probe(source.source_path)
        if abs(actual_duration - source.duration_seconds) > SOURCE_DURATION_TOLERANCE_SECONDS:
            raise LongVideoMediaValidationError("source duration mismatch")


def probe_long_source_duration(source_path: Path) -> float:
    if not source_path.is_file():
        raise LongVideoMediaValidationError("configured source video is unavailable")
    _, duration_seconds = imageio_ffmpeg.count_frames_and_secs(str(source_path))
    if duration_seconds <= 0:
        raise LongVideoMediaValidationError("could not determine source duration")
    return float(duration_seconds)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
