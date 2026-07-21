import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from hakimi_analysis.media import AnalysisWindow, PreparedMedia
from hakimi_analysis.models import (
    RunStage,
    SpeechSignal,
    SpeechUnderstandingResult,
    Transcript,
    TranscriptUtterance,
    VisualLocalizationResult,
    VisualSegment,
)
from hakimi_analysis.orchestration import OrchestratedAnalysisPipeline, SkillRepository
from hakimi_analysis.pipeline import PipelineFailure
from hakimi_analysis.providers.base import ProviderError
from hakimi_analysis.sources import VideoSource


class FakeMediaProcessor:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.windows: list[AnalysisWindow] = []
        self.exited = asyncio.Event()

    @asynccontextmanager
    async def prepare_source(
        self, source_path: Path, duration_seconds: float
    ) -> AsyncIterator[PreparedMedia]:
        window = AnalysisWindow(
            start_seconds=0,
            end_seconds=duration_seconds,
            expanded=False,
        )
        self.windows.append(window)
        try:
            yield PreparedMedia(
                directory=self.directory,
                video_path=self.directory / "video.mp4",
                audio_path=self.directory / "audio.wav",
                window=window,
                contact_sheet_path=self.directory / "contact-sheet.jpg",
                contact_sheet_timestamps=(0, 3, 6),
            )
        finally:
            self.exited.set()


class FakeAsr:
    async def recognize(
        self, *, audio_path: Path, window_start_seconds: float, request_id: str
    ) -> Transcript:
        await asyncio.sleep(0)
        return Transcript(
            text="做拖拽弯举",
            utterances=[
                TranscriptUtterance(
                    text="做拖拽弯举",
                    start_seconds=42,
                    end_seconds=49,
                )
            ],
        )


class SlowAsr(FakeAsr):
    async def recognize(self, **kwargs: object) -> Transcript:
        del kwargs
        await asyncio.sleep(30)
        raise AssertionError("slow ASR should be cancelled by the evidence budget")


class EmptyAsr(FakeAsr):
    async def recognize(self, **kwargs: object) -> Transcript:
        del kwargs
        await asyncio.sleep(0)
        return Transcript(text="", utterances=[])


class BlockingAsr(FakeAsr):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def recognize(self, **kwargs: object) -> Transcript:
        del kwargs
        self.started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise

        raise AssertionError("blocking ASR should never complete")


class DelayedCancellationAsr(FakeAsr):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cleanup_started = asyncio.Event()
        self.release_cleanup = asyncio.Event()
        self.cleanup_finished = asyncio.Event()

    async def recognize(self, **kwargs: object) -> Transcript:
        del kwargs
        self.started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError as cancellation:
            self.cleanup_started.set()
            while not self.release_cleanup.is_set():
                try:
                    await self.release_cleanup.wait()
                except asyncio.CancelledError:
                    continue
            self.cleanup_finished.set()
            raise cancellation

        raise AssertionError("delayed ASR should never complete")


class FakeArk:
    def __init__(self, *, empty: bool = False, fail_visual: bool = False) -> None:
        self.empty = empty
        self.fail_visual = fail_visual
        self.active_branches = 0
        self.max_active_branches = 0
        self.speech_transcript: dict[str, object] | None = None

    async def understand_speech(self, **kwargs: object) -> SpeechUnderstandingResult:
        transcript = kwargs.get("transcript")
        self.speech_transcript = transcript if isinstance(transcript, dict) else None
        self.active_branches += 1
        self.max_active_branches = max(self.max_active_branches, self.active_branches)
        await asyncio.sleep(0.01)
        self.active_branches -= 1
        if self.empty:
            return SpeechUnderstandingResult(signals=[])
        return SpeechUnderstandingResult(
            signals=[
                SpeechSignal(
                    action_name="拖拽弯举",
                    sets=None,
                    reps=None,
                    duration_seconds=None,
                    rest_seconds=None,
                    start_seconds=42,
                    end_seconds=49,
                    evidence_text="拖拽弯举",
                )
            ]
        )

    async def locate_visual(self, **_: object) -> VisualLocalizationResult:
        self.active_branches += 1
        self.max_active_branches = max(self.max_active_branches, self.active_branches)
        await asyncio.sleep(0.01)
        self.active_branches -= 1
        if self.fail_visual:
            raise ProviderError("provider_error", "visual failed", retryable=True)
        if self.empty:
            return VisualLocalizationResult(segments=[])
        return VisualLocalizationResult(
            segments=[
                VisualSegment(
                    action_name="Drag Curl",
                    start_seconds=41,
                    end_seconds=51,
                    visual_cue="哑铃沿躯干后拉",
                )
            ]
        )

    async def locate_visual_contact_sheet(self, **kwargs: object) -> VisualLocalizationResult:
        return await self.locate_visual(**kwargs)


class BlockingVisualArk(FakeArk):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def locate_visual_contact_sheet(self, **kwargs: object) -> VisualLocalizationResult:
        del kwargs
        self.started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise

        raise AssertionError("blocking visual analysis should never complete")


class SlowVisualArk(FakeArk):
    async def locate_visual_contact_sheet(self, **kwargs: object) -> VisualLocalizationResult:
        del kwargs
        await asyncio.sleep(30)
        raise AssertionError("slow visual analysis should be cancelled by the evidence budget")


async def no_op_emit(stage: RunStage, event_type: str, data: dict[str, object]) -> None:
    return None


def source(tmp_path: Path) -> VideoSource:
    return VideoSource(
        id="legacy-arm-workout",
        title="本地联调视频",
        path=tmp_path / "source.mp4",
        duration_seconds=54,
    )


def skills() -> SkillRepository:
    return SkillRepository(
        speech_instructions="speech skill",
        speech_version="test-speech",
        visual_instructions="visual skill",
        visual_version="test-visual",
        fusion_instructions="fusion skill",
        fusion_version="test-fusion",
    )


def test_skill_repository_loads_all_three_versioned_contracts() -> None:
    repository = SkillRepository.load(Path(__file__).parents[3] / "skills")

    assert repository.speech_version == "1.3.0"
    assert repository.visual_version == "1.2.0"
    assert repository.fusion_version == "1.1.0"
    assert "Merge temporally overlapping evidence" in repository.fusion_instructions


@pytest.mark.asyncio
async def test_speech_and_visual_branches_run_in_parallel_and_fuse(tmp_path: Path) -> None:
    ark = FakeArk()
    pipeline = OrchestratedAnalysisPipeline(
        media=FakeMediaProcessor(tmp_path),
        asr=FakeAsr(),
        ark=ark,
        skills=skills(),
    )

    output = await pipeline.analyze(source(tmp_path), 45, no_op_emit)

    assert ark.max_active_branches == 2
    assert output.candidates[0].name == "拖拽弯举"
    assert len(output.candidates[0].evidence) == 2
    assert output.warnings == []

    assert ark.speech_transcript is not None
    utterances = ark.speech_transcript["utterances"]
    assert isinstance(utterances, list)
    assert all(isinstance(utterance, dict) and "words" not in utterance for utterance in utterances)


@pytest.mark.asyncio
async def test_one_failed_branch_can_complete_from_the_other_evidence(tmp_path: Path) -> None:
    pipeline = OrchestratedAnalysisPipeline(
        media=FakeMediaProcessor(tmp_path),
        asr=FakeAsr(),
        ark=FakeArk(fail_visual=True),
        skills=skills(),
    )

    output = await pipeline.analyze(source(tmp_path), 45, no_op_emit)

    assert output.candidates[0].needs_confirmation is True
    assert [warning.code for warning in output.warnings] == ["visual_unavailable"]


@pytest.mark.asyncio
async def test_slow_speech_branch_returns_visual_evidence_with_warning(tmp_path: Path) -> None:
    pipeline = OrchestratedAnalysisPipeline(
        media=FakeMediaProcessor(tmp_path),
        asr=SlowAsr(),
        ark=FakeArk(),
        skills=skills(),
        evidence_timeout_seconds=0.1,
    )

    output = await pipeline.analyze(source(tmp_path), None, no_op_emit)

    assert output.candidates[0].name == "Drag Curl"
    assert output.candidates[0].needs_confirmation is True
    assert [warning.code for warning in output.warnings] == ["speech_unavailable"]


@pytest.mark.asyncio
async def test_slow_visual_branch_returns_speech_evidence_with_warning(tmp_path: Path) -> None:
    pipeline = OrchestratedAnalysisPipeline(
        media=FakeMediaProcessor(tmp_path),
        asr=FakeAsr(),
        ark=SlowVisualArk(),
        skills=skills(),
        evidence_timeout_seconds=0.1,
    )

    output = await pipeline.analyze(source(tmp_path), None, no_op_emit)

    assert output.candidates[0].needs_confirmation is True
    assert [warning.code for warning in output.warnings] == ["visual_unavailable"]


@pytest.mark.asyncio
async def test_both_branches_timing_out_is_an_explicit_failure(tmp_path: Path) -> None:
    pipeline = OrchestratedAnalysisPipeline(
        media=FakeMediaProcessor(tmp_path),
        asr=SlowAsr(),
        ark=SlowVisualArk(),
        skills=skills(),
        evidence_timeout_seconds=0.1,
    )

    with pytest.raises(PipelineFailure) as caught:
        await pipeline.analyze(source(tmp_path), None, no_op_emit)

    assert caught.value.code == "timeout"


@pytest.mark.asyncio
async def test_empty_speech_and_visual_timeout_is_not_reported_as_no_evidence(
    tmp_path: Path,
) -> None:
    pipeline = OrchestratedAnalysisPipeline(
        media=FakeMediaProcessor(tmp_path),
        asr=EmptyAsr(),
        ark=SlowVisualArk(),
        skills=skills(),
        evidence_timeout_seconds=0.1,
    )

    with pytest.raises(PipelineFailure) as caught:
        await pipeline.analyze(source(tmp_path), None, no_op_emit)

    assert caught.value.code == "timeout"


@pytest.mark.asyncio
async def test_timeout_reaps_branch_before_media_exit(tmp_path: Path) -> None:
    media = FakeMediaProcessor(tmp_path)
    asr = DelayedCancellationAsr()
    pipeline = OrchestratedAnalysisPipeline(
        media=media,
        asr=asr,
        ark=FakeArk(),
        skills=skills(),
        evidence_timeout_seconds=0.05,
    )

    task = asyncio.create_task(pipeline.analyze(source(tmp_path), None, no_op_emit))
    await asyncio.wait_for(asr.cleanup_started.wait(), timeout=1)

    assert task.done() is False
    assert media.exited.is_set() is False

    asr.release_cleanup.set()
    output = await asyncio.wait_for(task, timeout=1)

    assert [warning.code for warning in output.warnings] == ["speech_unavailable"]
    assert asr.cleanup_finished.is_set()
    assert media.exited.is_set()


@pytest.mark.asyncio
async def test_parent_cancel_during_timeout_cleanup_reaps_before_media_exit(
    tmp_path: Path,
) -> None:
    media = FakeMediaProcessor(tmp_path)
    asr = DelayedCancellationAsr()
    pipeline = OrchestratedAnalysisPipeline(
        media=media,
        asr=asr,
        ark=FakeArk(),
        skills=skills(),
        evidence_timeout_seconds=0.05,
    )

    task = asyncio.create_task(pipeline.analyze(source(tmp_path), None, no_op_emit))
    await asyncio.wait_for(asr.cleanup_started.wait(), timeout=1)

    task.cancel()
    await asyncio.sleep(0)

    assert task.done() is False
    assert media.exited.is_set() is False

    asr.release_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert asr.cleanup_finished.is_set()
    assert media.exited.is_set()


@pytest.mark.asyncio
async def test_parent_cancellation_reaps_both_provider_branches(tmp_path: Path) -> None:
    asr = BlockingAsr()
    ark = BlockingVisualArk()
    pipeline = OrchestratedAnalysisPipeline(
        media=FakeMediaProcessor(tmp_path),
        asr=asr,
        ark=ark,
        skills=skills(),
        evidence_timeout_seconds=30,
    )

    task = asyncio.create_task(pipeline.analyze(source(tmp_path), None, no_op_emit))
    await asyncio.wait_for(asr.started.wait(), timeout=1)
    await asyncio.wait_for(ark.started.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await asyncio.wait_for(asr.cancelled.wait(), timeout=1)
    await asyncio.wait_for(ark.cancelled.wait(), timeout=1)


@pytest.mark.asyncio
async def test_full_source_is_analyzed_once_then_no_evidence_returns_empty(tmp_path: Path) -> None:
    media = FakeMediaProcessor(tmp_path)
    pipeline = OrchestratedAnalysisPipeline(
        media=media,
        asr=FakeAsr(),
        ark=FakeArk(empty=True),
        skills=skills(),
    )

    output = await pipeline.analyze(source(tmp_path), 45, no_op_emit)

    assert media.windows == [AnalysisWindow(start_seconds=0, end_seconds=54, expanded=False)]
    assert output.candidates == []
    assert output.empty_reason == "no_evidence"


@pytest.mark.asyncio
async def test_system_failure_without_evidence_is_not_reported_as_empty(tmp_path: Path) -> None:
    pipeline = OrchestratedAnalysisPipeline(
        media=FakeMediaProcessor(tmp_path),
        asr=FakeAsr(),
        ark=FakeArk(empty=True, fail_visual=True),
        skills=skills(),
    )

    with pytest.raises(PipelineFailure) as caught:
        await pipeline.analyze(source(tmp_path), 45, no_op_emit)

    assert caught.value.code == "provider_error"
