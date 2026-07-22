from pathlib import Path

import pytest

from hakimi_analysis.benchmark.long_models import LongExperimentSource, LongVideoChunkPolicy
from hakimi_analysis.benchmark.long_preparation import (
    LongMediaPreparationError,
    LongMediaPreparer,
)


def _source(tmp_path: Path, *, duration: float = 120) -> LongExperimentSource:
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")
    return LongExperimentSource(
        source_id="source-a",
        source_path=source_path,
        sha256="a" * 64,
        duration_seconds=duration,
        gold_path=tmp_path / "gold.json",
        gold_version="v1",
    )


@pytest.mark.asyncio
async def test_preparer_creates_full_audio_and_complete_silent_chunk_contact_pairs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []

    async def fake_ffmpeg(self: LongMediaPreparer, operation: str, *arguments: str) -> None:
        del self
        calls.append((operation, arguments))
        Path(arguments[-1]).write_bytes(b"generated")

    monkeypatch.setattr(LongMediaPreparer, "_run_ffmpeg", fake_ffmpeg)
    preparer = LongMediaPreparer(
        temp_root=tmp_path / "temporary",
        media_probe=lambda _path: (1_800, 60),
    )
    policy = LongVideoChunkPolicy(version="v1", duration_seconds=60, overlap_seconds=10)

    async with preparer.prepare_source(_source(tmp_path), policy) as prepared:
        assert prepared.audio_path.is_file()
        assert len(prepared.chunks) == 3
        assert [(item.chunk.start_seconds, item.chunk.end_seconds) for item in prepared.chunks] == [
            (0, 60),
            (50, 110),
            (100, 120),
        ]
        assert all(item.silent_video_path.is_file() for item in prepared.chunks)
        assert all(item.contact_sheet_path.is_file() for item in prepared.chunks)
        assert prepared.chunks[1].frame_times_seconds[0] == 50
        temporary_directory = prepared.audio_path.parent

    assert not temporary_directory.exists()
    assert [operation for operation, _ in calls].count("audio") == 1
    assert [operation for operation, _ in calls].count("silent video") == 3
    assert [operation for operation, _ in calls].count("contact sheet") == 3


@pytest.mark.asyncio
async def test_preparer_cleans_partial_media_after_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_ffmpeg(self: LongMediaPreparer, operation: str, *arguments: str) -> None:
        del self, arguments
        if operation == "contact sheet":
            raise RuntimeError("ffmpeg failed")

    monkeypatch.setattr(LongMediaPreparer, "_run_ffmpeg", failing_ffmpeg)
    preparer = LongMediaPreparer(
        temp_root=tmp_path / "temporary",
        media_probe=lambda _path: (1_800, 60),
    )
    policy = LongVideoChunkPolicy(version="v1", duration_seconds=60, overlap_seconds=10)

    with pytest.raises(RuntimeError, match="ffmpeg failed"):
        async with preparer.prepare_source(_source(tmp_path), policy):
            pass

    assert list((tmp_path / "temporary").iterdir()) == []


@pytest.mark.asyncio
async def test_preparer_rejects_a_truncated_silent_chunk_before_it_counts_as_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ffmpeg(self: LongMediaPreparer, operation: str, *arguments: str) -> None:
        del self, operation
        Path(arguments[-1]).write_bytes(b"generated")

    monkeypatch.setattr(LongMediaPreparer, "_run_ffmpeg", fake_ffmpeg)
    preparer = LongMediaPreparer(
        temp_root=tmp_path / "temporary",
        media_probe=lambda _path: (180, 5),
    )
    policy = LongVideoChunkPolicy(version="v1", duration_seconds=60, overlap_seconds=10)

    with pytest.raises(LongMediaPreparationError, match="truncated"):
        async with preparer.prepare_source(_source(tmp_path), policy):
            pass


@pytest.mark.asyncio
async def test_preparer_rejects_too_few_source_frames_for_the_contact_sheet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ffmpeg(self: LongMediaPreparer, operation: str, *arguments: str) -> None:
        del self, operation
        Path(arguments[-1]).write_bytes(b"generated")

    monkeypatch.setattr(LongMediaPreparer, "_run_ffmpeg", fake_ffmpeg)
    preparer = LongMediaPreparer(
        temp_root=tmp_path / "temporary",
        media_probe=lambda _path: (1, 60),
    )
    policy = LongVideoChunkPolicy(version="v1", duration_seconds=60, overlap_seconds=10)

    with pytest.raises(LongMediaPreparationError, match="too few frames"):
        async with preparer.prepare_source(_source(tmp_path), policy):
            pass
