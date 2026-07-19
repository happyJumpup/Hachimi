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

    @asynccontextmanager
    async def prepare(
        self, source_path: Path, window: AnalysisWindow
    ) -> AsyncIterator[PreparedMedia]:
        self.windows.append(window)
        yield PreparedMedia(
            directory=self.directory,
            video_path=self.directory / "video.mp4",
            audio_path=self.directory / "audio.wav",
            window=window,
        )


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


class FakeArk:
    def __init__(self, *, empty: bool = False, fail_visual: bool = False) -> None:
        self.empty = empty
        self.fail_visual = fail_visual
        self.active_branches = 0
        self.max_active_branches = 0

    async def understand_speech(self, **_: object) -> SpeechUnderstandingResult:
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
        visual_instructions="visual skill",
    )


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
async def test_no_evidence_expands_the_window_once_then_returns_empty(tmp_path: Path) -> None:
    media = FakeMediaProcessor(tmp_path)
    pipeline = OrchestratedAnalysisPipeline(
        media=media,
        asr=FakeAsr(),
        ark=FakeArk(empty=True),
        skills=skills(),
    )

    output = await pipeline.analyze(source(tmp_path), 45, no_op_emit)

    assert [window.expanded for window in media.windows] == [False, True]
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
