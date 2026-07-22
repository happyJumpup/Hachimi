import hashlib
from itertools import pairwise
from pathlib import Path

import pytest
from test_long_video_manifest import valid_manifest_payload

from hakimi_analysis.benchmark.long_manifest import LongExperimentManifest
from hakimi_analysis.benchmark.long_media import (
    LongVideoMediaValidationError,
    build_complete_source_chunks,
    map_chunk_candidate_to_source,
    verify_long_source_media,
)
from hakimi_analysis.benchmark.models import BenchmarkCandidate, EventKind, EvidenceChannel


def test_complete_chunks_cover_seven_and_nineteen_minute_sources_without_gaps(
    tmp_path: Path,
) -> None:
    manifest = LongExperimentManifest.model_validate(valid_manifest_payload(tmp_path))

    seven_chunks = build_complete_source_chunks(manifest.sources[0], manifest.chunk)
    nineteen_chunks = build_complete_source_chunks(manifest.sources[1], manifest.chunk)

    assert len(seven_chunks) == 9
    assert len(nineteen_chunks) == 23
    for source, chunks in zip(manifest.sources, (seven_chunks, nineteen_chunks), strict=True):
        assert chunks[0].start_seconds == 0
        assert chunks[-1].end_seconds == source.duration_seconds
        assert [chunk.index for chunk in chunks] == list(range(len(chunks)))
        assert all(
            chunk.duration_seconds == chunk.end_seconds - chunk.start_seconds for chunk in chunks
        )
        assert all(
            right.start_seconds <= left.end_seconds
            and left.end_seconds - right.start_seconds == 10
            for left, right in pairwise(chunks)
        )


def test_relative_chunk_candidates_map_to_absolute_source_time(tmp_path: Path) -> None:
    manifest = LongExperimentManifest.model_validate(valid_manifest_payload(tmp_path))
    chunk = build_complete_source_chunks(manifest.sources[0], manifest.chunk)[1]
    candidate = BenchmarkCandidate(
        action_name="Romanian Deadlift",
        start_seconds=2,
        end_seconds=16,
        kind=EventKind.ACTION,
        evidence_channels=[EvidenceChannel.VISUAL],
    )

    mapped = map_chunk_candidate_to_source(chunk, candidate)

    assert candidate.start_seconds == 2
    assert candidate.end_seconds == 16
    assert mapped.start_seconds == 52
    assert mapped.end_seconds == 66


def test_relative_chunk_candidate_cannot_extend_past_its_chunk(tmp_path: Path) -> None:
    manifest = LongExperimentManifest.model_validate(valid_manifest_payload(tmp_path))
    chunk = build_complete_source_chunks(manifest.sources[0], manifest.chunk)[0]
    candidate = BenchmarkCandidate(
        action_name="Romanian Deadlift",
        start_seconds=59,
        end_seconds=61,
        kind=EventKind.ACTION,
        evidence_channels=[EvidenceChannel.VISUAL],
    )

    with pytest.raises(ValueError, match="outside its source chunk"):
        map_chunk_candidate_to_source(chunk, candidate)


def test_source_media_validation_checks_hash_and_declared_duration_without_media_processing(
    tmp_path: Path,
) -> None:
    payload = valid_manifest_payload(tmp_path)
    source_path = tmp_path.parent / f"{tmp_path.name}-downloads" / "seven.mp4"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"seven source")
    sources = payload["sources"]
    assert isinstance(sources, list)
    sources[0]["sha256"] = hashlib.sha256(source_path.read_bytes()).hexdigest()
    manifest = LongExperimentManifest.model_validate(payload)

    observed_paths: list[Path] = []

    def duration_probe(path: Path) -> float:
        observed_paths.append(path)
        return 424.31 if path == source_path else 1157.46

    second_source_path = tmp_path.parent / f"{tmp_path.name}-downloads" / "nineteen.mp4"
    second_source_path.write_bytes(b"nineteen source")
    sources[1]["sha256"] = hashlib.sha256(second_source_path.read_bytes()).hexdigest()
    manifest = LongExperimentManifest.model_validate(payload)

    verify_long_source_media(manifest, duration_probe=duration_probe)

    assert observed_paths == [source_path, second_source_path]


def test_source_media_validation_rejects_hash_or_duration_mismatch(tmp_path: Path) -> None:
    payload = valid_manifest_payload(tmp_path)
    source_path = tmp_path.parent / f"{tmp_path.name}-downloads" / "seven.mp4"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"seven source")
    second_source_path = tmp_path.parent / f"{tmp_path.name}-downloads" / "nineteen.mp4"
    second_source_path.write_bytes(b"nineteen source")
    sources = payload["sources"]
    assert isinstance(sources, list)
    sources[1]["sha256"] = hashlib.sha256(second_source_path.read_bytes()).hexdigest()
    manifest = LongExperimentManifest.model_validate(payload)

    with pytest.raises(LongVideoMediaValidationError, match="source hash mismatch"):
        verify_long_source_media(
            manifest,
            duration_probe=lambda _path: 424.31,
        )

    sources[0]["sha256"] = hashlib.sha256(source_path.read_bytes()).hexdigest()
    manifest = LongExperimentManifest.model_validate(payload)
    with pytest.raises(LongVideoMediaValidationError, match="source duration mismatch"):
        verify_long_source_media(
            manifest,
            duration_probe=lambda _path: 1,
        )
