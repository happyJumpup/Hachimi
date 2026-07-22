import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from hakimi_analysis.content_understanding import (
    AnalysisRequest,
    ContentUnderstandingProvider,
)
from hakimi_analysis.media import AnalysisWindow, PreparedMedia, PreparedVisualChunk
from hakimi_analysis.models import (
    CoverageStatus,
    RunStage,
    Segment,
    SegmentRole,
    SpeechSignal,
    SpeechUnderstandingResult,
    Transcript,
    TranscriptUtterance,
    VisualLocalizationResult,
    VisualSegment,
)
from hakimi_analysis.orchestration import (
    OrchestratedAnalysisPipeline,
    build_visual_chunks,
    deduplicate_visual_segments,
)
from hakimi_analysis.provider_contracts import PromptContractRegistry
from hakimi_analysis.providers.base import ProviderError
from hakimi_analysis.sources import VideoSource


class FakeMediaProcessor:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.windows: list[AnalysisWindow] = []
        self.prepare_calls: list[tuple[Path, float, float]] = []
        self.visual_prepare_calls: list[AnalysisWindow] = []
        self.exited = asyncio.Event()

    @asynccontextmanager
    async def prepare_source(
        self,
        source_path: Path,
        duration_seconds: float,
        *,
        start_seconds: float = 0,
    ) -> AsyncIterator[PreparedMedia]:
        self.prepare_calls.append((source_path, duration_seconds, start_seconds))
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
            )
        finally:
            self.exited.set()

    async def prepare_visual_chunk(
        self,
        source_path: Path,
        window: AnalysisWindow,
        directory: Path,
        *,
        source_offset_seconds: float = 0,
    ) -> PreparedVisualChunk:
        del source_path, directory, source_offset_seconds
        self.visual_prepare_calls.append(window)
        return PreparedVisualChunk(
            video_path=self.directory / f"chunk-{len(self.visual_prepare_calls)}.mp4",
            window=window,
        )


class FakeAsr:
    def __init__(self) -> None:
        self.calls = 0

    async def recognize(
        self, *, audio_path: Path, window_start_seconds: float, request_id: str
    ) -> Transcript:
        self.calls += 1
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


class FailingAsr(FakeAsr):
    async def recognize(self, **kwargs: object) -> Transcript:
        del kwargs
        raise ProviderError("provider_error", "speech failed", retryable=True)


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
        self.visual_video_paths: list[Path] = []

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

    async def locate_visual(self, **kwargs: object) -> VisualLocalizationResult:
        video_path = kwargs.get("video_path")
        if isinstance(video_path, Path):
            self.visual_video_paths.append(video_path)
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

class ChunkAwareArk(FakeArk):
    def __init__(self, *, failed_start: float | None = None, fail_all: bool = False) -> None:
        super().__init__()
        self.failed_start = failed_start
        self.fail_all = fail_all
        self.visual_windows: list[AnalysisWindow] = []

    async def locate_visual(self, **kwargs: object) -> VisualLocalizationResult:
        video_path = kwargs.get("video_path")
        if isinstance(video_path, Path):
            self.visual_video_paths.append(video_path)
        window = kwargs["window"]
        assert isinstance(window, Segment)
        chunk_number = (
            int(video_path.stem.removeprefix("chunk-"))
            if isinstance(video_path, Path)
            else len(self.visual_windows) + 1
        )
        source_start_seconds = (chunk_number - 1) * 50
        self.visual_windows.append(
            AnalysisWindow(
                start_seconds=window.start_seconds,
                end_seconds=window.end_seconds,
                expanded=False,
            )
        )
        if self.fail_all or self.failed_start == source_start_seconds:
            raise ProviderError("provider_error", "visual failed", retryable=True)
        if source_start_seconds == 0:
            segment = VisualSegment(
                action_name="深蹲",
                start_seconds=55,
                end_seconds=59,
                visual_cue="下蹲后站起",
            )
        elif source_start_seconds == 50:
            segment = VisualSegment(
                action_name="深蹲",
                start_seconds=5,
                end_seconds=11,
                visual_cue="下蹲后站起",
            )
        else:
            segment = VisualSegment(
                action_name="平板支撑",
                start_seconds=10,
                end_seconds=15,
                visual_cue="保持躯干稳定",
            )
        return VisualLocalizationResult(segments=[segment])


class InterleavedDuplicateArk(FakeArk):
    async def locate_visual(self, **kwargs: object) -> VisualLocalizationResult:
        window = kwargs["window"]
        assert isinstance(window, Segment)
        return VisualLocalizationResult(
            segments=[
                VisualSegment(
                    action_name="深蹲",
                    start_seconds=4,
                    end_seconds=12,
                    visual_cue="下蹲后站起",
                ),
                VisualSegment(
                    action_name="开合跳",
                    start_seconds=6,
                    end_seconds=9,
                    visual_cue="双脚开合",
                ),
                VisualSegment(
                    action_name="深蹲",
                    start_seconds=10,
                    end_seconds=16,
                    visual_cue="重复下蹲",
                ),
            ]
        )


class LocalClockArk(FakeArk):
    def __init__(self) -> None:
        super().__init__()
        self.windows: list[Segment] = []

    async def locate_visual(self, **kwargs: object) -> VisualLocalizationResult:
        window = kwargs["window"]
        assert isinstance(window, Segment)
        self.windows.append(window)
        return VisualLocalizationResult(
            segments=[
                VisualSegment(
                    action_name=f"chunk-{len(self.windows)}",
                    start_seconds=5,
                    end_seconds=11,
                    visual_cue="visible movement",
                )
            ]
        )


class RangeLocalArk(FakeArk):
    async def locate_visual(self, **kwargs: object) -> VisualLocalizationResult:
        window = kwargs["window"]
        assert window == Segment(start_seconds=0, end_seconds=10)
        return VisualLocalizationResult(
            segments=[
                VisualSegment(
                    action_name="深蹲",
                    start_seconds=2,
                    end_seconds=6,
                    visual_cue="下蹲后站起",
                )
            ]
        )


class SplitParameterArk(FakeArk):
    async def understand_speech(self, **kwargs: object) -> SpeechUnderstandingResult:
        del kwargs
        return SpeechUnderstandingResult(
            signals=[
                SpeechSignal(
                    action_name="杠铃卧推",
                    sets=4,
                    start_seconds=5,
                    end_seconds=10,
                    evidence_text="杠铃卧推做四组",
                    segment_role=SegmentRole.TEACHING_DEMO,
                ),
                SpeechSignal(
                    action_name="卧推",
                    reps=10,
                    start_seconds=20,
                    end_seconds=25,
                    evidence_text="每组做十次",
                    segment_role=SegmentRole.TEACHING_DEMO,
                ),
            ]
        )

    async def locate_visual(self, **kwargs: object) -> VisualLocalizationResult:
        del kwargs
        return VisualLocalizationResult(segments=[])


class BlockingVisualArk(FakeArk):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def locate_visual(self, **kwargs: object) -> VisualLocalizationResult:
        del kwargs
        self.started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise

        raise AssertionError("blocking visual analysis should never complete")


class SlowVisualArk(FakeArk):
    async def locate_visual(self, **kwargs: object) -> VisualLocalizationResult:
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


def contracts() -> PromptContractRegistry:
    return PromptContractRegistry.load(
        Path(__file__).parents[1] / "provider-contracts",
        speech_model_id="doubao-seed-2-0-mini-260428",
        visual_model_ids=("doubao-seed-2-0-mini-260428",),
    )


def long_source(tmp_path: Path) -> VideoSource:
    return VideoSource(
        id="local:62f31c4b-bd1c-4b6a-8ad8-c6121b56135a",
        title="本地导入视频",
        path=tmp_path / "source.mp4",
        duration_seconds=130,
    )


def test_visual_chunk_windows_cover_the_complete_source_with_bounded_overlap() -> None:
    assert build_visual_chunks(130, chunk_seconds=60, overlap_seconds=10) == [
        AnalysisWindow(start_seconds=0, end_seconds=60, expanded=False),
        AnalysisWindow(start_seconds=50, end_seconds=110, expanded=False),
        AnalysisWindow(start_seconds=100, end_seconds=130, expanded=False),
    ]


def test_visual_deduplication_merges_generic_and_specific_names() -> None:
    segments = deduplicate_visual_segments(
        [
            VisualSegment(
                action_name="卧推",
                start_seconds=50,
                end_seconds=59,
                visual_cue="仰卧推起",
            ),
            VisualSegment(
                action_name="杠铃卧推",
                start_seconds=55,
                end_seconds=63,
                visual_cue="杠铃下放后推起",
            ),
        ]
    )

    assert len(segments) == 1
    assert segments[0].action_name == "杠铃卧推"
    assert segments[0].start_seconds == 50
    assert segments[0].end_seconds == 63


def test_visual_deduplication_keeps_conflicting_equipment_separate() -> None:
    segments = deduplicate_visual_segments(
        [
            VisualSegment(
                action_name="杠铃卧推",
                start_seconds=50,
                end_seconds=59,
                visual_cue="杠铃推起",
            ),
            VisualSegment(
                action_name="哑铃卧推",
                start_seconds=55,
                end_seconds=63,
                visual_cue="哑铃推起",
            ),
        ]
    )

    assert [segment.action_name for segment in segments] == ["杠铃卧推", "哑铃卧推"]


@pytest.mark.asyncio
async def test_visual_provider_uses_chunk_local_time_and_pipeline_offsets_once(
    tmp_path: Path,
) -> None:
    ark = LocalClockArk()
    pipeline = OrchestratedAnalysisPipeline(
        media=FakeMediaProcessor(tmp_path),
        asr=EmptyAsr(),
        ark=ark,
        contracts=contracts(),
        visual_chunk_seconds=60,
        visual_overlap_seconds=10,
    )

    output = await pipeline.analyze(long_source(tmp_path), no_op_emit)

    assert ark.windows == [
        Segment(start_seconds=0, end_seconds=60),
        Segment(start_seconds=0, end_seconds=60),
        Segment(start_seconds=0, end_seconds=30),
    ]
    assert [candidate.segment for candidate in output.candidates] == [
        Segment(start_seconds=5, end_seconds=11),
        Segment(start_seconds=55, end_seconds=61),
        Segment(start_seconds=105, end_seconds=111),
    ]


@pytest.mark.asyncio
async def test_content_understanding_provider_returns_absolute_evidence_references(
    tmp_path: Path,
) -> None:
    range_source = VideoSource(
        id="local:range",
        title="局部重试",
        path=tmp_path / "source.mp4",
        duration_seconds=30,
        analysis_start_seconds=10,
        analysis_end_seconds=20,
    )
    provider = ContentUnderstandingProvider(
        media=FakeMediaProcessor(tmp_path),
        transcriber=EmptyAsr(),
        interpreter=RangeLocalArk(),
        visual_locator=RangeLocalArk(),
        contracts=contracts(),
    )

    result = await provider.analyze(AnalysisRequest(source=range_source), no_op_emit)

    assert result.source_id == "local:range"
    assert result.analysis_range == Segment(start_seconds=10, end_seconds=20)
    assert result.coverage_status == CoverageStatus.COMPLETE
    assert result.actions[0].source_clip == Segment(start_seconds=12, end_seconds=16)
    assert result.actions[0].evidence[0].id == "candidate-1-visual-001"
    assert result.actions[0].field_evidence.name == ["candidate-1-visual-001"]
    assert result.actions[0].field_evidence.segment == ["candidate-1-visual-001"]
    assert result.actions[0].parameters.sets is None
    assert {branch.branch: branch.status for branch in result.branches} == {
        "speech": "empty",
        "visual": "complete",
    }


@pytest.mark.asyncio
async def test_content_understanding_preserves_parameter_specific_evidence(
    tmp_path: Path,
) -> None:
    ark = SplitParameterArk()
    provider = ContentUnderstandingProvider(
        media=FakeMediaProcessor(tmp_path),
        transcriber=FakeAsr(),
        interpreter=ark,
        visual_locator=ark,
        contracts=contracts(),
    )

    result = await provider.analyze(AnalysisRequest(source=source(tmp_path)), no_op_emit)

    action = result.actions[0]
    assert action.name == "杠铃卧推"
    assert action.parameters.sets == 4
    assert action.parameters.reps == 10
    assert action.field_evidence.sets == ["candidate-1-speech-001"]
    assert action.field_evidence.reps == ["candidate-1-speech-002"]
    assert action.field_evidence.duration_seconds == []
    assert action.field_evidence.rest_seconds == []


def test_prompt_contract_registry_loads_the_two_provider_contracts() -> None:
    registry = contracts()

    assert registry.speech.contract_version == "1.0.0"
    assert registry.visual.contract_version == "1.0.0"
    assert registry.visual.input_media_type == "continuous_silent_mp4"


@pytest.mark.asyncio
async def test_speech_and_visual_branches_run_in_parallel_and_fuse(tmp_path: Path) -> None:
    ark = FakeArk()
    pipeline = OrchestratedAnalysisPipeline(
        media=FakeMediaProcessor(tmp_path),
        asr=FakeAsr(),
        ark=ark,
        contracts=contracts(),
    )

    output = await pipeline.analyze(source(tmp_path), no_op_emit)

    assert ark.max_active_branches == 2
    assert output.candidates[0].name == "拖拽弯举"
    assert len(output.candidates[0].evidence) == 2
    assert output.warnings == []
    # The current non-chunked provider path never fabricates gap locations.
    assert output.coverage_status == CoverageStatus.COMPLETE
    assert output.coverage_gaps == []

    assert ark.speech_transcript is not None
    utterances = ark.speech_transcript["utterances"]
    assert isinstance(utterances, list)
    assert all(isinstance(utterance, dict) and "words" not in utterance for utterance in utterances)


@pytest.mark.asyncio
async def test_long_source_runs_asr_once_and_deduplicates_overlapping_visual_chunks(
    tmp_path: Path,
) -> None:
    media = FakeMediaProcessor(tmp_path)
    asr = FakeAsr()
    ark = ChunkAwareArk()
    pipeline = OrchestratedAnalysisPipeline(
        media=media,
        asr=asr,
        ark=ark,
        contracts=contracts(),
        visual_chunk_seconds=60,
        visual_overlap_seconds=10,
    )

    output = await pipeline.analyze(long_source(tmp_path), no_op_emit)

    assert asr.calls == 1
    assert all(path.suffix == ".mp4" for path in ark.visual_video_paths)
    assert media.visual_prepare_calls == [
        AnalysisWindow(start_seconds=0, end_seconds=60, expanded=False),
        AnalysisWindow(start_seconds=50, end_seconds=110, expanded=False),
        AnalysisWindow(start_seconds=100, end_seconds=130, expanded=False),
    ]
    assert output.coverage_status == CoverageStatus.COMPLETE
    assert [candidate.name for candidate in output.candidates].count("深蹲") == 1


@pytest.mark.asyncio
async def test_visual_deduplication_is_not_changed_by_interleaved_actions(
    tmp_path: Path,
) -> None:
    pipeline = OrchestratedAnalysisPipeline(
        media=FakeMediaProcessor(tmp_path),
        asr=EmptyAsr(),
        ark=InterleavedDuplicateArk(),
        contracts=contracts(),
    )

    output = await pipeline.analyze(source(tmp_path), no_op_emit)

    assert [candidate.name for candidate in output.candidates] == ["深蹲", "开合跳"]
    assert output.candidates[0].segment == Segment(start_seconds=4, end_seconds=16)


@pytest.mark.asyncio
async def test_failed_middle_visual_chunk_keeps_results_and_reports_only_uncovered_core(
    tmp_path: Path,
) -> None:
    pipeline = OrchestratedAnalysisPipeline(
        media=FakeMediaProcessor(tmp_path),
        asr=FakeAsr(),
        ark=ChunkAwareArk(failed_start=50),
        contracts=contracts(),
        visual_chunk_seconds=60,
        visual_overlap_seconds=10,
    )

    output = await pipeline.analyze(long_source(tmp_path), no_op_emit)

    assert output.coverage_status == CoverageStatus.PARTIAL
    assert output.processed_seconds == 90
    assert [gap.model_dump(mode="json") for gap in output.coverage_gaps] == [
        {
            "start_seconds": 60.0,
            "end_seconds": 100.0,
            "reason": "provider_error",
            "retryable": True,
        }
    ]
    assert output.candidates


@pytest.mark.asyncio
async def test_range_source_prepares_only_the_requested_absolute_media_range(
    tmp_path: Path,
) -> None:
    media = FakeMediaProcessor(tmp_path)
    pipeline = OrchestratedAnalysisPipeline(
        media=media,
        asr=EmptyAsr(),
        ark=FakeArk(empty=True),
        contracts=contracts(),
    )
    range_source = VideoSource(
        id="local:62f31c4b-bd1c-4b6a-8ad8-c6121b56135a",
        title="本地导入视频",
        path=tmp_path / "source.mp4",
        duration_seconds=30,
        analysis_start_seconds=10,
        analysis_end_seconds=20,
    )

    await pipeline.analyze(range_source, no_op_emit)

    assert media.prepare_calls == [(range_source.path, 10.0, 10.0)]


@pytest.mark.asyncio
async def test_one_failed_branch_can_complete_from_the_other_evidence(tmp_path: Path) -> None:
    pipeline = OrchestratedAnalysisPipeline(
        media=FakeMediaProcessor(tmp_path),
        asr=FakeAsr(),
        ark=FakeArk(fail_visual=True),
        contracts=contracts(),
    )

    output = await pipeline.analyze(source(tmp_path), no_op_emit)

    assert output.candidates[0].needs_confirmation is True
    assert [warning.code for warning in output.warnings] == ["visual_unavailable"]


@pytest.mark.asyncio
async def test_slow_speech_branch_returns_visual_evidence_with_warning(tmp_path: Path) -> None:
    pipeline = OrchestratedAnalysisPipeline(
        media=FakeMediaProcessor(tmp_path),
        asr=SlowAsr(),
        ark=FakeArk(),
        contracts=contracts(),
        speech_timeout_seconds=0.1,
    )

    output = await pipeline.analyze(source(tmp_path), no_op_emit)

    assert output.candidates[0].name == "Drag Curl"
    assert output.candidates[0].needs_confirmation is True
    assert [warning.code for warning in output.warnings] == ["speech_unavailable"]


@pytest.mark.asyncio
async def test_slow_visual_branch_returns_speech_evidence_with_warning(tmp_path: Path) -> None:
    pipeline = OrchestratedAnalysisPipeline(
        media=FakeMediaProcessor(tmp_path),
        asr=FakeAsr(),
        ark=SlowVisualArk(),
        contracts=contracts(),
        visual_chunk_timeout_seconds=0.1,
    )

    output = await pipeline.analyze(source(tmp_path), no_op_emit)

    assert output.candidates[0].needs_confirmation is True
    assert [warning.code for warning in output.warnings] == ["visual_unavailable"]


@pytest.mark.asyncio
async def test_both_branches_timing_out_is_explicitly_insufficient(tmp_path: Path) -> None:
    pipeline = OrchestratedAnalysisPipeline(
        media=FakeMediaProcessor(tmp_path),
        asr=SlowAsr(),
        ark=SlowVisualArk(),
        contracts=contracts(),
        speech_timeout_seconds=0.1,
        visual_chunk_timeout_seconds=0.1,
    )

    output = await pipeline.analyze(source(tmp_path), no_op_emit)

    assert output.coverage_status == CoverageStatus.INSUFFICIENT
    assert output.empty_reason == "insufficient_evidence"
    assert output.processed_seconds == 0
    assert output.coverage_gaps[0].reason == "timeout"


@pytest.mark.asyncio
async def test_empty_speech_and_visual_timeout_is_not_reported_as_no_evidence(
    tmp_path: Path,
) -> None:
    pipeline = OrchestratedAnalysisPipeline(
        media=FakeMediaProcessor(tmp_path),
        asr=EmptyAsr(),
        ark=SlowVisualArk(),
        contracts=contracts(),
        visual_chunk_timeout_seconds=0.1,
    )

    output = await pipeline.analyze(source(tmp_path), no_op_emit)

    assert output.coverage_status == CoverageStatus.INSUFFICIENT
    assert output.empty_reason == "insufficient_evidence"
    assert output.coverage_gaps[0].reason == "timeout"


@pytest.mark.asyncio
async def test_timeout_reaps_branch_before_media_exit(tmp_path: Path) -> None:
    media = FakeMediaProcessor(tmp_path)
    asr = DelayedCancellationAsr()
    pipeline = OrchestratedAnalysisPipeline(
        media=media,
        asr=asr,
        ark=FakeArk(),
        contracts=contracts(),
        speech_timeout_seconds=0.05,
    )

    task = asyncio.create_task(pipeline.analyze(source(tmp_path), no_op_emit))
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
        contracts=contracts(),
        speech_timeout_seconds=0.05,
    )

    task = asyncio.create_task(pipeline.analyze(source(tmp_path), no_op_emit))
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
        contracts=contracts(),
        speech_timeout_seconds=30,
    )

    task = asyncio.create_task(pipeline.analyze(source(tmp_path), no_op_emit))
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
        contracts=contracts(),
    )

    output = await pipeline.analyze(source(tmp_path), no_op_emit)

    assert media.windows == [AnalysisWindow(start_seconds=0, end_seconds=54, expanded=False)]
    assert output.candidates == []
    assert output.empty_reason == "no_evidence"


@pytest.mark.asyncio
async def test_system_failure_without_evidence_is_reported_as_insufficient(
    tmp_path: Path,
) -> None:
    pipeline = OrchestratedAnalysisPipeline(
        media=FakeMediaProcessor(tmp_path),
        asr=FakeAsr(),
        ark=FakeArk(empty=True, fail_visual=True),
        contracts=contracts(),
    )

    output = await pipeline.analyze(source(tmp_path), no_op_emit)

    assert output.coverage_status == CoverageStatus.INSUFFICIENT
    assert output.empty_reason == "insufficient_evidence"
    assert output.coverage_gaps[0].reason == "provider_error"


@pytest.mark.asyncio
async def test_failed_speech_with_reliably_empty_visual_is_insufficient(tmp_path: Path) -> None:
    pipeline = OrchestratedAnalysisPipeline(
        media=FakeMediaProcessor(tmp_path),
        asr=FailingAsr(),
        ark=FakeArk(empty=True),
        contracts=contracts(),
    )

    output = await pipeline.analyze(source(tmp_path), no_op_emit)

    assert output.coverage_status == CoverageStatus.INSUFFICIENT
    assert output.empty_reason == "insufficient_evidence"
    assert output.processed_seconds == 0
    assert [gap.model_dump(mode="json") for gap in output.coverage_gaps] == [
        {
            "start_seconds": 0,
            "end_seconds": 54,
            "reason": "provider_error",
            "retryable": True,
        }
    ]


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class BudgetAdvancingArk(ChunkAwareArk):
    def __init__(self, clock: FakeClock) -> None:
        super().__init__()
        self._clock = clock

    async def locate_visual(self, **kwargs: object) -> VisualLocalizationResult:
        result = await super().locate_visual(**kwargs)
        self._clock.value += 25
        return result


@pytest.mark.asyncio
async def test_run_budget_returns_completed_chunks_as_partial_before_outer_timeout(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    ark = BudgetAdvancingArk(clock)
    pipeline = OrchestratedAnalysisPipeline(
        media=FakeMediaProcessor(tmp_path),
        asr=EmptyAsr(),
        ark=ark,
        contracts=contracts(),
        visual_chunk_seconds=60,
        visual_overlap_seconds=10,
        run_timeout_seconds=70,
        evidence_deadline_seconds=60,
        cleanup_reserve_seconds=10,
        clock=clock,
    )

    output = await pipeline.analyze(long_source(tmp_path), no_op_emit)

    assert len(ark.visual_windows) == 2
    assert output.coverage_status == CoverageStatus.PARTIAL
    assert output.processed_seconds == 110
    assert [gap.model_dump(mode="json") for gap in output.coverage_gaps] == [
        {
            "start_seconds": 110,
            "end_seconds": 130,
            "reason": "timeout",
            "retryable": True,
        }
    ]
    assert output.candidates


@pytest.mark.asyncio
async def test_run_budget_bounds_speech_and_preserves_completed_visual_evidence(
    tmp_path: Path,
) -> None:
    asr = BlockingAsr()
    pipeline = OrchestratedAnalysisPipeline(
        media=FakeMediaProcessor(tmp_path),
        asr=asr,
        ark=FakeArk(),
        contracts=contracts(),
        speech_timeout_seconds=30,
        visual_chunk_timeout_seconds=0.02,
        run_timeout_seconds=0.2,
        evidence_deadline_seconds=0.12,
        cleanup_reserve_seconds=0.08,
    )

    output = await asyncio.wait_for(
        pipeline.analyze(source(tmp_path), no_op_emit),
        timeout=0.5,
    )

    assert asr.cancelled.is_set()
    assert output.coverage_status == CoverageStatus.COMPLETE
    assert output.candidates
    assert {warning.code for warning in output.warnings} == {"speech_unavailable"}
