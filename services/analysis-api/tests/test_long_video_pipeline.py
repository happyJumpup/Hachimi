import asyncio
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from hakimi_analysis.benchmark.long_execution import LongCheckpointStore
from hakimi_analysis.benchmark.long_models import ArmId, LongVideoChunk
from hakimi_analysis.benchmark.long_pipeline import (
    LongArmExecutor,
    LongExecutionFailure,
    LongPreparedChunk,
    LongPreparedSource,
    LongProviderBundle,
    _MutableMetrics,
)
from hakimi_analysis.benchmark.long_runtime import LongRunKey, ProviderPermits
from hakimi_analysis.benchmark.long_scoring import LongMediaLifecycleRecord
from hakimi_analysis.benchmark.models import (
    BenchmarkCandidate,
    CandidateEnvelope,
    CleanupOutcome,
    CleanupPolicy,
    EventKind,
    EvidenceChannel,
    MediaHandle,
    ProviderInference,
)
from hakimi_analysis.models import Transcript, TranscriptUtterance


def _candidate(
    start: float,
    end: float,
    *,
    weight_kg: float | None = None,
) -> BenchmarkCandidate:
    return BenchmarkCandidate(
        action_name="Romanian Deadlift",
        start_seconds=start,
        end_seconds=end,
        kind=EventKind.ACTION,
        weight_kg=weight_kg,
        evidence_channels=[EvidenceChannel.VISUAL],
    )


def _candidate_for_prompt(prompt: str) -> BenchmarkCandidate:
    match = re.search(
        r'"(?:allowed_window|window)"\s*:\s*\{\s*"start_seconds"\s*:\s*([0-9.]+)',
        prompt,
    )
    assert match is not None
    start_seconds = float(match.group(1))
    return _candidate(start_seconds + 5, start_seconds + 15)


class FakeAsr:
    def __init__(self) -> None:
        self.request_ids: list[str] = []

    async def upload(self, _model_id: str, path: Path, _media_kind: str) -> MediaHandle:
        return MediaHandle(
            provider="asr",
            model_id="asr",
            media_kind="audio",
            handle_id=str(path),
            cleanup_policy=CleanupPolicy.LOCAL_RELEASE,
        )

    async def transcribe(self, _handle: MediaHandle, *, request_id: str) -> Transcript:
        self.request_ids.append(request_id)
        return Transcript(
            text="lift now",
            utterances=[TranscriptUtterance(text="lift now", start_seconds=5, end_seconds=15)],
        )

    async def cleanup(self, _handle: MediaHandle) -> None:
        return None

    async def verify_cleanup(self, _handle: MediaHandle) -> CleanupOutcome:
        return CleanupOutcome.LOCAL_RELEASED


class FakeSeed:
    def __init__(self) -> None:
        self.contact_windows: list[tuple[float, float]] = []
        self.fusion_prompts: list[str] = []

    async def analyze_contact_sheet(
        self,
        _image_path: Path,
        *,
        frame_times_seconds: tuple[float, ...],
        window_start_seconds: float,
        window_end_seconds: float,
        prompt: str,
    ) -> ProviderInference:
        assert frame_times_seconds
        assert "visual" in prompt
        self.contact_windows.append((window_start_seconds, window_end_seconds))
        return ProviderInference(
            envelope=CandidateEnvelope(
                actions=[_candidate(window_start_seconds + 5, window_start_seconds + 15)]
            )
        )

    async def complete_text(self, prompt: str) -> ProviderInference:
        assert "transcript" in prompt
        assert '"coordinate_system": "chunk_relative_seconds"' in prompt
        self.fusion_prompts.append(prompt)
        return ProviderInference(
            envelope=CandidateEnvelope(actions=[_candidate_for_prompt(prompt)])
        )


class FakeQwen:
    def __init__(self) -> None:
        self.cleaned: list[str] = []

    async def upload(
        self,
        _model_id: str,
        path: Path,
        _media_kind: str,
        *,
        on_expiry: Callable[[str], None] | None = None,
    ) -> MediaHandle:
        expires_at = "2099-01-01T00:00:00+00:00"
        if on_expiry is not None:
            on_expiry(expires_at)
        return MediaHandle(
            provider="qwen",
            model_id="qwen",
            media_kind="video",
            handle_id=f"oss://{path.name}",
            cleanup_policy=CleanupPolicy.EXPIRE_AUTOMATICALLY,
            expires_at=expires_at,
        )

    async def analyze(self, _handle: MediaHandle, prompt: str) -> ProviderInference:
        assert '"coordinate_system": "chunk_relative_seconds"' in prompt
        return ProviderInference(
            envelope=CandidateEnvelope(actions=[_candidate(5, 15)])
        )

    async def cleanup(self, handle: MediaHandle) -> None:
        self.cleaned.append(handle.handle_id)

    async def verify_cleanup(self, _handle: MediaHandle) -> CleanupOutcome:
        return CleanupOutcome.EXPIRY_RECORDED


class UploadTrackingQwen(FakeQwen):
    def __init__(self) -> None:
        super().__init__()
        self.upload_started = False

    async def upload(
        self,
        model_id: str,
        path: Path,
        media_kind: str,
        *,
        on_expiry: Callable[[str], None] | None = None,
    ) -> MediaHandle:
        self.upload_started = True
        return await super().upload(model_id, path, media_kind, on_expiry=on_expiry)


class WeightedSeed(FakeSeed):
    """A model output that violates the no-weight contract in both stages."""

    async def analyze_contact_sheet(
        self,
        image_path: Path,
        *,
        frame_times_seconds: tuple[float, ...],
        window_start_seconds: float,
        window_end_seconds: float,
        prompt: str,
    ) -> ProviderInference:
        await super().analyze_contact_sheet(
            image_path,
            frame_times_seconds=frame_times_seconds,
            window_start_seconds=window_start_seconds,
            window_end_seconds=window_end_seconds,
            prompt=prompt,
        )
        return ProviderInference(
            envelope=CandidateEnvelope(actions=[_candidate(5, 15, weight_kg=20)])
        )

    async def complete_text(self, prompt: str) -> ProviderInference:
        await super().complete_text(prompt)
        return ProviderInference(
            envelope=CandidateEnvelope(actions=[_candidate(5, 15, weight_kg=20)])
        )


class RelativeVisualSeed(FakeSeed):
    async def analyze_contact_sheet(
        self,
        _image_path: Path,
        *,
        frame_times_seconds: tuple[float, ...],
        window_start_seconds: float,
        window_end_seconds: float,
        prompt: str,
    ) -> ProviderInference:
        del frame_times_seconds, window_start_seconds, window_end_seconds, prompt
        return ProviderInference(envelope=CandidateEnvelope(actions=[_candidate(5, 15)]))


class RelativeFusionSeed(FakeSeed):
    async def complete_text(self, _prompt: str) -> ProviderInference:
        return ProviderInference(envelope=CandidateEnvelope(actions=[_candidate(5, 15)]))


class OutOfRangeFusionSeed(FakeSeed):
    async def complete_text(self, _prompt: str) -> ProviderInference:
        return ProviderInference(envelope=CandidateEnvelope(actions=[_candidate(65, 75)]))


class SlowVisualSeed(FakeSeed):
    def __init__(self) -> None:
        super().__init__()
        self.visual_active = 0
        self.maximum_visual_active = 0

    async def analyze_contact_sheet(
        self,
        image_path: Path,
        *,
        frame_times_seconds: tuple[float, ...],
        window_start_seconds: float,
        window_end_seconds: float,
        prompt: str,
    ) -> ProviderInference:
        self.visual_active += 1
        self.maximum_visual_active = max(self.maximum_visual_active, self.visual_active)
        try:
            await asyncio.sleep(0.01)
            return await super().analyze_contact_sheet(
                image_path,
                frame_times_seconds=frame_times_seconds,
                window_start_seconds=window_start_seconds,
                window_end_seconds=window_end_seconds,
                prompt=prompt,
            )
        finally:
            self.visual_active -= 1


class FailingQwen(FakeQwen):
    async def analyze(self, _handle: MediaHandle, _prompt: str) -> ProviderInference:
        raise RuntimeError("provider failure")


class CleanupFailingQwen(FakeQwen):
    async def cleanup(self, _handle: MediaHandle) -> None:
        raise RuntimeError("cleanup failure")


class InvalidExpiryQwen(FakeQwen):
    async def upload(
        self,
        model_id: str,
        path: Path,
        media_kind: str,
        *,
        on_expiry: Callable[[str], None] | None = None,
    ) -> MediaHandle:
        del on_expiry
        handle = await super().upload(model_id, path, media_kind, on_expiry=None)
        return handle.model_copy(update={"expires_at": None})


class MismatchedExpiryQwen(FakeQwen):
    async def upload(
        self,
        model_id: str,
        path: Path,
        media_kind: str,
        *,
        on_expiry: Callable[[str], None] | None = None,
    ) -> MediaHandle:
        recorded_expiry = "2099-01-01T00:00:00+00:00"
        if on_expiry is not None:
            on_expiry(recorded_expiry)
        handle = await super().upload(model_id, path, media_kind, on_expiry=None)
        return handle.model_copy(update={"expires_at": "2099-01-02T00:00:00+00:00"})


class RetryableUploadError(RuntimeError):
    retryable = True
    expires_at = "2099-01-01T00:00:00+00:00"


class RetryableUploadQwen(FakeQwen):
    def __init__(self) -> None:
        super().__init__()
        self.upload_attempts = 0

    async def upload(
        self,
        model_id: str,
        path: Path,
        media_kind: str,
        *,
        on_expiry: Callable[[str], None] | None = None,
    ) -> MediaHandle:
        self.upload_attempts += 1
        if on_expiry is not None:
            on_expiry(RetryableUploadError.expires_at)
        if self.upload_attempts == 1:
            raise RetryableUploadError("temporary upload transport failure")
        return await super().upload(model_id, path, media_kind, on_expiry=on_expiry)


class ExpiringCancelledError(asyncio.CancelledError):
    expires_at = "2099-01-01T00:00:00+00:00"


class CancelledUploadQwen(FakeQwen):
    async def upload(
        self,
        _model_id: str,
        _path: Path,
        _media_kind: str,
        *,
        on_expiry: Callable[[str], None] | None = None,
    ) -> MediaHandle:
        if on_expiry is not None:
            on_expiry(ExpiringCancelledError.expires_at)
        raise ExpiringCancelledError


class RetryableInferenceError(RuntimeError):
    retryable = True


class RetryingTokenSeed(FakeSeed):
    def __init__(self) -> None:
        super().__init__()
        self.fusion_attempts = 0

    async def analyze_contact_sheet(
        self,
        image_path: Path,
        *,
        frame_times_seconds: tuple[float, ...],
        window_start_seconds: float,
        window_end_seconds: float,
        prompt: str,
    ) -> ProviderInference:
        inference = await super().analyze_contact_sheet(
            image_path,
            frame_times_seconds=frame_times_seconds,
            window_start_seconds=window_start_seconds,
            window_end_seconds=window_end_seconds,
            prompt=prompt,
        )
        return inference.model_copy(update={"input_tokens": 10, "output_tokens": 5})

    async def complete_text(self, prompt: str) -> ProviderInference:
        self.fusion_attempts += 1
        if self.fusion_attempts == 1:
            raise RetryableInferenceError("retryable provider timeout")
        inference = await super().complete_text(prompt)
        return inference.model_copy(update={"input_tokens": 10, "output_tokens": 5})


class BlockingAsr(FakeAsr):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def transcribe(self, _handle: MediaHandle, *, request_id: str) -> Transcript:
        self.request_ids.append(request_id)
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


def _prepared_source(tmp_path: Path) -> LongPreparedSource:
    audio = tmp_path / "audio.wav"
    image = tmp_path / "sheet.jpg"
    video = tmp_path / "chunk.mp4"
    for path in (audio, image, video):
        path.write_bytes(b"content")
    chunk = LongVideoChunk(
        source_id="seven",
        index=0,
        start_seconds=0,
        end_seconds=60,
        duration_seconds=60,
    )
    return LongPreparedSource(
        source_id="seven",
        duration_seconds=60,
        audio_path=audio,
        chunks=(
            LongPreparedChunk(
                chunk=chunk,
                silent_video_path=video,
                contact_sheet_path=image,
                frame_times_seconds=(0, 15, 30, 45),
            ),
        ),
    )


def _prepared_overlapped_source(tmp_path: Path) -> LongPreparedSource:
    prepared = _prepared_source(tmp_path)
    second_chunk = LongVideoChunk(
        source_id="seven",
        index=1,
        start_seconds=50,
        end_seconds=100,
        duration_seconds=50,
    )
    first = prepared.chunks[0]
    return LongPreparedSource(
        source_id=prepared.source_id,
        duration_seconds=100,
        audio_path=prepared.audio_path,
        chunks=(
            first,
            LongPreparedChunk(
                chunk=second_chunk,
                silent_video_path=first.silent_video_path,
                contact_sheet_path=first.contact_sheet_path,
                frame_times_seconds=(50, 65, 80, 95),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_seed_arm_runs_full_asr_contact_sheet_fusion_and_maps_absolute_times(
    tmp_path: Path,
) -> None:
    seed = FakeSeed()
    executor = LongArmExecutor(
        prepared_sources={"seven": _prepared_source(tmp_path)},
        providers=LongProviderBundle(asr=FakeAsr(), seed=seed, qwen=FakeQwen()),
        asr_model_id="asr",
        qwen_model_id="qwen",
    )

    result = await executor.execute(
        LongRunKey("seven", ArmId.SEED_CONTACT_SHEET, 1),
        ProviderPermits({"asr": 1, "seed": 1, "qwen": 1}),
    )

    assert [(item.start_seconds, item.end_seconds) for item in result.candidates] == [(5, 15)]
    assert seed.contact_windows == [(0, 60)]
    assert result.completed_coverage_seconds == 60


@pytest.mark.asyncio
async def test_v3_seed_arm_returns_one_candidate_for_repeated_same_action_chunks(
    tmp_path: Path,
) -> None:
    executor = LongArmExecutor(
        prepared_sources={"seven": _prepared_overlapped_source(tmp_path)},
        providers=LongProviderBundle(asr=FakeAsr(), seed=FakeSeed(), qwen=FakeQwen()),
        asr_model_id="asr",
        qwen_model_id="qwen",
        prompt_version="long-video-ab-v3",
    )

    result = await executor.execute(
        LongRunKey("seven", ArmId.SEED_CONTACT_SHEET, 1),
        ProviderPermits({"asr": 1, "seed": 1, "qwen": 1}),
    )

    assert [
        (item.action_name, item.start_seconds, item.end_seconds) for item in result.candidates
    ] == [("Romanian Deadlift", 5, 15)]


@pytest.mark.asyncio
async def test_qwen_arm_fails_closed_before_upload_without_lifecycle_sink(
    tmp_path: Path,
) -> None:
    qwen = UploadTrackingQwen()
    executor = LongArmExecutor(
        prepared_sources={"seven": _prepared_source(tmp_path)},
        providers=LongProviderBundle(asr=FakeAsr(), seed=FakeSeed(), qwen=qwen),
        asr_model_id="asr",
        qwen_model_id="qwen",
    )

    with pytest.raises(LongExecutionFailure, match="qwen_lifecycle_sink_missing"):
        await executor.execute(
            LongRunKey("seven", ArmId.QWEN_VIDEO, 1),
            ProviderPermits({"asr": 1, "seed": 1, "qwen": 1}),
        )

    assert qwen.upload_started is False


@pytest.mark.asyncio
async def test_model_weight_violations_are_counted_before_safe_candidates_are_retained(
    tmp_path: Path,
) -> None:
    executor = LongArmExecutor(
        prepared_sources={"seven": _prepared_source(tmp_path)},
        providers=LongProviderBundle(
            asr=FakeAsr(),
            seed=WeightedSeed(),
            qwen=FakeQwen(),
        ),
        asr_model_id="asr",
        qwen_model_id="qwen",
    )

    result = await executor.execute(
        LongRunKey("seven", ArmId.SEED_CONTACT_SHEET, 1),
        ProviderPermits({"asr": 1, "seed": 1, "qwen": 1}),
    )

    assert result.weight_violation_count == 2
    assert all(candidate.weight_kg is None for candidate in result.candidates)


@pytest.mark.asyncio
async def test_qwen_arm_cleans_each_temporary_handle_and_keeps_absolute_times(
    tmp_path: Path,
) -> None:
    qwen = FakeQwen()
    persisted: list[LongMediaLifecycleRecord] = []
    executor = LongArmExecutor(
        prepared_sources={"seven": _prepared_source(tmp_path)},
        providers=LongProviderBundle(asr=FakeAsr(), seed=FakeSeed(), qwen=qwen),
        asr_model_id="asr",
        qwen_model_id="qwen",
        lifecycle_sink=lambda _key, record: persisted.append(record),
    )

    result = await executor.execute(
        LongRunKey("seven", ArmId.QWEN_VIDEO, 1),
        ProviderPermits({"asr": 1, "seed": 1, "qwen": 1}),
    )

    assert [(item.start_seconds, item.end_seconds) for item in result.candidates] == [(5, 15)]
    assert qwen.cleaned == ["oss://chunk.mp4"]
    assert result.cleanup_failed is False
    qwen_lifecycle = next(item for item in result.lifecycle if item.provider == "qwen")
    assert qwen_lifecycle.expires_at == "2099-01-01T00:00:00+00:00"
    assert len([item for item in persisted if item.provider == "qwen"]) == 1


@pytest.mark.asyncio
async def test_qwen_arm_maps_relative_clip_times_for_later_source_chunks(tmp_path: Path) -> None:
    executor = LongArmExecutor(
        prepared_sources={"seven": _prepared_overlapped_source(tmp_path)},
        providers=LongProviderBundle(asr=FakeAsr(), seed=FakeSeed(), qwen=FakeQwen()),
        asr_model_id="asr",
        qwen_model_id="qwen",
        lifecycle_sink=lambda _key, _record: None,
    )

    result = await executor.execute(
        LongRunKey("seven", ArmId.QWEN_VIDEO, 1),
        ProviderPermits({"asr": 1, "seed": 2, "qwen": 1}),
    )

    assert [(item.start_seconds, item.end_seconds) for item in result.candidates] == [
        (5, 15),
        (55, 65),
    ]


@pytest.mark.asyncio
async def test_seed_visual_time_contract_failure_identifies_the_visual_stage(
    tmp_path: Path,
) -> None:
    executor = LongArmExecutor(
        prepared_sources={"seven": _prepared_overlapped_source(tmp_path)},
        providers=LongProviderBundle(asr=FakeAsr(), seed=RelativeVisualSeed(), qwen=FakeQwen()),
        asr_model_id="asr",
        qwen_model_id="qwen",
    )

    with pytest.raises(LongExecutionFailure, match="seed_visual_candidate_time_invalid"):
        await executor.execute(
            LongRunKey("seven", ArmId.SEED_CONTACT_SHEET, 1),
            ProviderPermits({"asr": 1, "seed": 2, "qwen": 1}),
        )


@pytest.mark.asyncio
async def test_seed_fusion_time_contract_failure_identifies_the_fusion_stage(
    tmp_path: Path,
) -> None:
    executor = LongArmExecutor(
        prepared_sources={"seven": _prepared_overlapped_source(tmp_path)},
        providers=LongProviderBundle(asr=FakeAsr(), seed=OutOfRangeFusionSeed(), qwen=FakeQwen()),
        asr_model_id="asr",
        qwen_model_id="qwen",
    )

    with pytest.raises(LongExecutionFailure, match="fusion_relative_candidate_time_invalid"):
        await executor.execute(
            LongRunKey("seven", ArmId.SEED_CONTACT_SHEET, 1),
            ProviderPermits({"asr": 1, "seed": 2, "qwen": 1}),
        )


@pytest.mark.asyncio
async def test_seed_fusion_maps_relative_times_for_later_source_chunks(tmp_path: Path) -> None:
    executor = LongArmExecutor(
        prepared_sources={"seven": _prepared_overlapped_source(tmp_path)},
        providers=LongProviderBundle(asr=FakeAsr(), seed=RelativeFusionSeed(), qwen=FakeQwen()),
        asr_model_id="asr",
        qwen_model_id="qwen",
    )

    result = await executor.execute(
        LongRunKey("seven", ArmId.SEED_CONTACT_SHEET, 1),
        ProviderPermits({"asr": 1, "seed": 2, "qwen": 1}),
    )

    assert [(item.start_seconds, item.end_seconds) for item in result.candidates] == [
        (5, 15),
        (55, 65),
    ]

async def test_qwen_upload_is_not_replayed_after_an_ambiguous_transport_failure(
    tmp_path: Path,
) -> None:
    qwen = RetryableUploadQwen()
    persisted: list[LongMediaLifecycleRecord] = []
    executor = LongArmExecutor(
        prepared_sources={"seven": _prepared_source(tmp_path)},
        providers=LongProviderBundle(asr=FakeAsr(), seed=FakeSeed(), qwen=qwen),
        asr_model_id="asr",
        qwen_model_id="qwen",
        retry_delays=(0, 0),
        lifecycle_sink=lambda _key, record: persisted.append(record),
    )

    with pytest.raises(LongExecutionFailure) as caught:
        await executor.execute(
            LongRunKey("seven", ArmId.QWEN_VIDEO, 1),
            ProviderPermits({"asr": 1, "seed": 1, "qwen": 1}),
        )

    assert qwen.upload_attempts == 1
    assert caught.value.lifecycle_audit[0].cleanup_outcome == CleanupOutcome.FAILED
    assert caught.value.lifecycle_audit[0].expires_at == "2099-01-01T00:00:00+00:00"
    qwen_records = [record for record in persisted if record.provider == "qwen"]
    assert [record.cleanup_outcome for record in qwen_records] == [
        CleanupOutcome.EXPIRY_RECORDED,
        CleanupOutcome.FAILED,
    ]


@pytest.mark.asyncio
async def test_qwen_upload_cancellation_is_journaled_before_cancellation_propagates(
    tmp_path: Path,
) -> None:
    persisted: list[LongMediaLifecycleRecord] = []
    executor = LongArmExecutor(
        prepared_sources={"seven": _prepared_source(tmp_path)},
        providers=LongProviderBundle(
            asr=FakeAsr(),
            seed=FakeSeed(),
            qwen=CancelledUploadQwen(),
        ),
        asr_model_id="asr",
        qwen_model_id="qwen",
        lifecycle_sink=lambda _key, record: persisted.append(record),
    )

    with pytest.raises(asyncio.CancelledError):
        await executor.execute(
            LongRunKey("seven", ArmId.QWEN_VIDEO, 1),
            ProviderPermits({"asr": 1, "seed": 1, "qwen": 1}),
        )

    qwen_records = [record for record in persisted if record.provider == "qwen"]
    assert [record.cleanup_outcome for record in qwen_records] == [
        CleanupOutcome.EXPIRY_RECORDED,
        CleanupOutcome.FAILED,
    ]
    assert all(record.expires_at == "2099-01-01T00:00:00+00:00" for record in qwen_records)


@pytest.mark.asyncio
async def test_qwen_upload_cancellation_survives_a_terminal_journal_failure(
    tmp_path: Path,
) -> None:
    persisted: list[LongMediaLifecycleRecord] = []

    def lifecycle_sink(_key: LongRunKey, record: LongMediaLifecycleRecord) -> None:
        if persisted:
            raise RuntimeError("journal unavailable")
        persisted.append(record)

    executor = LongArmExecutor(
        prepared_sources={"seven": _prepared_source(tmp_path)},
        providers=LongProviderBundle(
            asr=FakeAsr(),
            seed=FakeSeed(),
            qwen=CancelledUploadQwen(),
        ),
        asr_model_id="asr",
        qwen_model_id="qwen",
        lifecycle_sink=lifecycle_sink,
    )

    with pytest.raises(asyncio.CancelledError):
        await executor.execute(
            LongRunKey("seven", ArmId.QWEN_VIDEO, 1),
            ProviderPermits({"asr": 1, "seed": 1, "qwen": 1}),
        )

    assert [record.cleanup_outcome for record in persisted] == [CleanupOutcome.EXPIRY_RECORDED]


@pytest.mark.asyncio
async def test_qwen_expiry_mismatch_is_a_persisted_cleanup_failure(
    tmp_path: Path,
) -> None:
    persisted: list[LongMediaLifecycleRecord] = []
    executor = LongArmExecutor(
        prepared_sources={"seven": _prepared_source(tmp_path)},
        providers=LongProviderBundle(
            asr=FakeAsr(),
            seed=FakeSeed(),
            qwen=MismatchedExpiryQwen(),
        ),
        asr_model_id="asr",
        qwen_model_id="qwen",
        lifecycle_sink=lambda _key, record: persisted.append(record),
    )

    with pytest.raises(LongExecutionFailure) as caught:
        await executor.execute(
            LongRunKey("seven", ArmId.QWEN_VIDEO, 1),
            ProviderPermits({"asr": 1, "seed": 1, "qwen": 1}),
        )

    qwen_records = [record for record in persisted if record.provider == "qwen"]
    assert caught.value.cleanup_failed is True
    assert caught.value.lifecycle_audit[-1].cleanup_outcome == CleanupOutcome.FAILED
    assert [record.cleanup_outcome for record in qwen_records] == [
        CleanupOutcome.EXPIRY_RECORDED,
        CleanupOutcome.FAILED,
    ]


@pytest.mark.asyncio
async def test_qwen_invalid_expiry_still_cleans_handle_and_journals_failure(
    tmp_path: Path,
) -> None:
    qwen = InvalidExpiryQwen()
    persisted: list[LongMediaLifecycleRecord] = []
    executor = LongArmExecutor(
        prepared_sources={"seven": _prepared_source(tmp_path)},
        providers=LongProviderBundle(asr=FakeAsr(), seed=FakeSeed(), qwen=qwen),
        asr_model_id="asr",
        qwen_model_id="qwen",
        lifecycle_sink=lambda _key, record: persisted.append(record),
    )

    with pytest.raises(LongExecutionFailure, match="qwen_expiry_missing"):
        await executor.execute(
            LongRunKey("seven", ArmId.QWEN_VIDEO, 1),
            ProviderPermits({"asr": 1, "seed": 1, "qwen": 1}),
        )

    assert qwen.cleaned == ["oss://chunk.mp4"]
    qwen_records = [record for record in persisted if record.provider == "qwen"]
    assert len(qwen_records) == 1
    assert qwen_records[0].cleanup_outcome == CleanupOutcome.FAILED
    assert qwen_records[0].expires_at is None


@pytest.mark.asyncio
async def test_billable_retry_makes_arm_cost_usage_unknown(tmp_path: Path) -> None:
    seed = RetryingTokenSeed()
    executor = LongArmExecutor(
        prepared_sources={"seven": _prepared_source(tmp_path)},
        providers=LongProviderBundle(asr=FakeAsr(), seed=seed, qwen=FakeQwen()),
        asr_model_id="asr",
        qwen_model_id="qwen",
        retry_delays=(0, 0),
    )

    result = await executor.execute(
        LongRunKey("seven", ArmId.SEED_CONTACT_SHEET, 1),
        ProviderPermits({"asr": 1, "seed": 1, "qwen": 1}),
    )

    assert seed.fusion_attempts == 2
    assert result.token_usage_by_provider["seed"] == (20, 10)
    assert result.token_usage_known is False


@pytest.mark.asyncio
async def test_long_runs_with_the_same_repetition_use_distinct_asr_connect_ids(
    tmp_path: Path,
) -> None:
    asr = FakeAsr()
    executor = LongArmExecutor(
        prepared_sources={"seven": _prepared_source(tmp_path)},
        providers=LongProviderBundle(asr=asr, seed=FakeSeed(), qwen=FakeQwen()),
        asr_model_id="asr",
        qwen_model_id="qwen",
        lifecycle_sink=lambda _key, _record: None,
    )
    permits = ProviderPermits({"asr": 2, "seed": 2, "qwen": 2})

    await asyncio.gather(
        executor.execute(LongRunKey("seven", ArmId.SEED_CONTACT_SHEET, 1), permits),
        executor.execute(LongRunKey("seven", ArmId.QWEN_VIDEO, 1), permits),
    )

    assert len(asr.request_ids) == 2
    assert len(set(asr.request_ids)) == 2
    assert all(request_id.startswith("long-") for request_id in asr.request_ids)


@pytest.mark.asyncio
async def test_completed_coverage_counts_the_source_union_not_chunk_overlap(
    tmp_path: Path,
) -> None:
    seed = FakeSeed()
    executor = LongArmExecutor(
        prepared_sources={"seven": _prepared_overlapped_source(tmp_path)},
        providers=LongProviderBundle(asr=FakeAsr(), seed=seed, qwen=FakeQwen()),
        asr_model_id="asr",
        qwen_model_id="qwen",
    )

    result = await executor.execute(
        LongRunKey("seven", ArmId.SEED_CONTACT_SHEET, 1),
        ProviderPermits({"asr": 1, "seed": 2, "qwen": 1}),
    )

    assert result.completed_coverage_seconds == 100
    assert any(
        '"coordinate_system": "chunk_relative_seconds"' in prompt
        for prompt in seed.fusion_prompts
    )
    assert all('"start_seconds": 50.0' not in prompt for prompt in seed.fusion_prompts)


@pytest.mark.asyncio
async def test_an_arm_does_not_queue_all_of_its_visual_chunks_at_once(tmp_path: Path) -> None:
    seed = SlowVisualSeed()
    executor = LongArmExecutor(
        prepared_sources={"seven": _prepared_overlapped_source(tmp_path)},
        providers=LongProviderBundle(asr=FakeAsr(), seed=seed, qwen=FakeQwen()),
        asr_model_id="asr",
        qwen_model_id="qwen",
    )

    await executor.execute(
        LongRunKey("seven", ArmId.SEED_CONTACT_SHEET, 1),
        ProviderPermits({"asr": 1, "seed": 3, "qwen": 1}),
    )

    assert seed.maximum_visual_active == 1


@pytest.mark.asyncio
async def test_checkpoint_resume_is_explicitly_marked_on_the_arm_result(tmp_path: Path) -> None:
    prepared = _prepared_source(tmp_path)
    checkpoint = LongCheckpointStore(tmp_path / "checkpoints", manifest_sha256="a" * 64)
    checkpoint.write(
        "seven",
        ArmId.SEED_CONTACT_SHEET.value,
        1,
        prepared.chunks[0].chunk,
        [_candidate(5, 15)],
    )
    executor = LongArmExecutor(
        prepared_sources={"seven": prepared},
        providers=LongProviderBundle(asr=FakeAsr(), seed=FakeSeed(), qwen=FakeQwen()),
        asr_model_id="asr",
        qwen_model_id="qwen",
        checkpoints=checkpoint,
    )

    result = await executor.execute(
        LongRunKey("seven", ArmId.SEED_CONTACT_SHEET, 1),
        ProviderPermits({"asr": 1, "seed": 1, "qwen": 1}),
    )

    assert result.resumed_from_checkpoint is True


@pytest.mark.asyncio
async def test_all_checkpointed_chunks_do_not_start_an_untracked_asr_call(tmp_path: Path) -> None:
    prepared = _prepared_source(tmp_path)
    checkpoint = LongCheckpointStore(tmp_path / "checkpoints", manifest_sha256="a" * 64)
    checkpoint.write(
        "seven",
        ArmId.SEED_CONTACT_SHEET.value,
        1,
        prepared.chunks[0].chunk,
        [_candidate(5, 15)],
    )
    asr = BlockingAsr()
    executor = LongArmExecutor(
        prepared_sources={"seven": prepared},
        providers=LongProviderBundle(asr=asr, seed=FakeSeed(), qwen=FakeQwen()),
        asr_model_id="asr",
        qwen_model_id="qwen",
        checkpoints=checkpoint,
    )

    result = await executor.execute(
        LongRunKey("seven", ArmId.SEED_CONTACT_SHEET, 1),
        ProviderPermits({"asr": 1, "seed": 1, "qwen": 1}),
    )

    assert result.resumed_from_checkpoint is True
    assert asr.started.is_set() is False


@pytest.mark.asyncio
async def test_failure_retains_only_safe_qwen_expiry_audit_metadata(tmp_path: Path) -> None:
    executor = LongArmExecutor(
        prepared_sources={"seven": _prepared_source(tmp_path)},
        providers=LongProviderBundle(asr=FakeAsr(), seed=FakeSeed(), qwen=FailingQwen()),
        asr_model_id="asr",
        qwen_model_id="qwen",
        lifecycle_sink=lambda _key, _record: None,
    )

    with pytest.raises(LongExecutionFailure) as caught:
        await executor.execute(
            LongRunKey("seven", ArmId.QWEN_VIDEO, 1),
            ProviderPermits({"asr": 1, "seed": 1, "qwen": 1}),
        )

    qwen_lifecycle = next(item for item in caught.value.lifecycle_audit if item.provider == "qwen")
    assert qwen_lifecycle.expires_at == "2099-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_cleanup_failure_is_recorded_before_the_arm_fails(tmp_path: Path) -> None:
    executor = LongArmExecutor(
        prepared_sources={"seven": _prepared_source(tmp_path)},
        providers=LongProviderBundle(asr=FakeAsr(), seed=FakeSeed(), qwen=CleanupFailingQwen()),
        asr_model_id="asr",
        qwen_model_id="qwen",
        lifecycle_sink=lambda _key, _record: None,
    )

    result = await executor.execute(
        LongRunKey("seven", ArmId.QWEN_VIDEO, 1),
        ProviderPermits({"asr": 1, "seed": 1, "qwen": 1}),
    )

    qwen_lifecycle = next(item for item in result.lifecycle if item.provider == "qwen")
    assert result.cleanup_failed is True
    assert qwen_lifecycle.cleanup_outcome == CleanupOutcome.FAILED


def test_partial_provider_token_usage_is_not_treated_as_a_zero_cost_call() -> None:
    metrics = _MutableMetrics()
    metrics.record_inference(
        "seed",
        ProviderInference(
            envelope=CandidateEnvelope(actions=[]),
            input_tokens=10,
            output_tokens=5,
        ),
    )
    metrics.record_inference(
        "seed",
        ProviderInference(
            envelope=CandidateEnvelope(actions=[]),
            input_tokens=10,
            output_tokens=None,
        ),
    )

    assert metrics.token_usage_known is True
    assert metrics.token_usage_complete is False
