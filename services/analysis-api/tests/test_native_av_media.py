from pathlib import Path

import pytest

from hakimi_analysis.benchmark.media import MediaPreparationError, _has_audio_stream


@pytest.mark.asyncio
async def test_audio_probe_only_treats_confirmed_missing_stream_as_silence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def no_audio(*args: object, **kwargs: object) -> tuple[int, str]:
        del args, kwargs
        return 1, "Stream map '0:a:0' matches no streams"

    monkeypatch.setattr("hakimi_analysis.benchmark.media._run_process", no_audio)
    assert not await _has_audio_stream("ffmpeg", tmp_path / "silent.mp4")


@pytest.mark.asyncio
async def test_audio_probe_propagates_corrupt_media_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def corrupt(*args: object, **kwargs: object) -> tuple[int, str]:
        del args, kwargs
        return 1, "Invalid data found when processing input"

    monkeypatch.setattr("hakimi_analysis.benchmark.media._run_process", corrupt)
    with pytest.raises(MediaPreparationError, match="audio_probe_failed"):
        await _has_audio_stream("ffmpeg", tmp_path / "corrupt.mp4")
