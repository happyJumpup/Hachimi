import asyncio
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from hakimi_analysis.fusion import fuse_candidates
from hakimi_analysis.media import (
    AnalysisWindow,
    LocalMediaProcessor,
    MediaProcessingError,
    PreparedMedia,
    calculate_analysis_window,
)
from hakimi_analysis.models import (
    AnalysisWarning,
    RunStage,
    Segment,
    SpeechSignal,
    SpeechUnderstandingResult,
    Transcript,
    VisualLocalizationResult,
    VisualSegment,
)
from hakimi_analysis.pipeline import EmitCallback, PipelineFailure, PipelineOutput
from hakimi_analysis.providers.base import ProviderError
from hakimi_analysis.sources import VideoSource


class MediaProcessor(Protocol):
    def prepare(
        self,
        source_path: Path,
        window: AnalysisWindow,
    ) -> AbstractAsyncContextManager[PreparedMedia]: ...


class SpeechRecognizer(Protocol):
    async def recognize(
        self,
        *,
        audio_path: Path,
        window_start_seconds: float,
        request_id: str,
    ) -> Transcript: ...


class ArkAnalyzer(Protocol):
    async def understand_speech(
        self,
        *,
        transcript: dict[str, Any],
        trigger_seconds: float,
        window: Segment,
        instructions: str,
    ) -> SpeechUnderstandingResult: ...

    async def locate_visual(
        self,
        *,
        video_path: Path,
        window: Segment,
        trigger_seconds: float,
        instructions: str,
    ) -> VisualLocalizationResult: ...


@dataclass(frozen=True, slots=True)
class SkillRepository:
    speech_instructions: str
    visual_instructions: str

    @classmethod
    def load(cls, skills_root: Path) -> "SkillRepository":
        speech_path = skills_root / "training-speech-understanding" / "SKILL.md"
        visual_path = skills_root / "visual-action-localization" / "SKILL.md"
        return cls(
            speech_instructions=speech_path.read_text(encoding="utf-8"),
            visual_instructions=visual_path.read_text(encoding="utf-8"),
        )


@dataclass(slots=True)
class BranchResults:
    speech: list[SpeechSignal] | ProviderError
    visual: list[VisualSegment] | ProviderError

    @property
    def has_evidence(self) -> bool:
        speech_has_evidence = isinstance(self.speech, list) and bool(self.speech)
        visual_has_evidence = isinstance(self.visual, list) and bool(self.visual)
        return speech_has_evidence or visual_has_evidence

    @property
    def both_successful(self) -> bool:
        return isinstance(self.speech, list) and isinstance(self.visual, list)


class OrchestratedAnalysisPipeline:
    def __init__(
        self,
        *,
        media: MediaProcessor,
        asr: SpeechRecognizer,
        ark: ArkAnalyzer,
        skills: SkillRepository,
    ) -> None:
        self._media = media
        self._asr = asr
        self._ark = ark
        self._skills = skills

    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float,
        emit: EmitCallback,
    ) -> PipelineOutput:
        for expanded in (False, True):
            window = calculate_analysis_window(
                trigger_seconds=trigger_seconds,
                duration_seconds=source.duration_seconds,
                expanded=expanded,
            )
            await emit(
                RunStage.EXPANDING_WINDOW if expanded else RunStage.PREPARING_MEDIA,
                "stage.changed",
                {"expanded": expanded},
            )
            try:
                async with self._media.prepare(source.path, window) as prepared:
                    results = await self._analyze_branches(
                        prepared,
                        trigger_seconds,
                        emit,
                    )
            except MediaProcessingError as error:
                raise PipelineFailure(
                    "media_error",
                    "视频片段准备失败，请重试",
                    retryable=False,
                ) from error

            speech_signals = results.speech if isinstance(results.speech, list) else []
            visual_segments = results.visual if isinstance(results.visual, list) else []
            if speech_signals or visual_segments:
                await emit(RunStage.FUSING_CANDIDATES, "stage.changed", {})
                warnings: list[AnalysisWarning] = []
                if isinstance(results.speech, ProviderError):
                    warnings.append(
                        AnalysisWarning(
                            code="speech_unavailable",
                            message="语音依据暂不可用，请重点确认动作名称和参数",
                        )
                    )
                if isinstance(results.visual, ProviderError):
                    warnings.append(
                        AnalysisWarning(
                            code="visual_unavailable",
                            message="画面依据暂不可用，请重点确认演示时间段",
                        )
                    )
                return PipelineOutput(
                    candidates=fuse_candidates(
                        source_id=source.id,
                        speech_signals=speech_signals,
                        visual_segments=visual_segments,
                    ),
                    warnings=warnings,
                )

            if results.both_successful:
                if not expanded:
                    continue
                return PipelineOutput(candidates=[], empty_reason="no_evidence")

            branch_error = (
                results.speech
                if isinstance(results.speech, ProviderError)
                else results.visual
            )
            if not isinstance(branch_error, ProviderError):
                raise AssertionError("failed analysis has no provider error")
            raise PipelineFailure(
                branch_error.code,
                str(branch_error),
                retryable=branch_error.retryable,
            ) from branch_error

        raise AssertionError("analysis attempts exhausted")

    async def _analyze_branches(
        self,
        prepared: PreparedMedia,
        trigger_seconds: float,
        emit: EmitCallback,
    ) -> BranchResults:
        await emit(RunStage.ANALYZING_EVIDENCE, "stage.changed", {})
        speech_task = asyncio.create_task(
            self._speech_branch(prepared, trigger_seconds, emit)
        )
        visual_task = asyncio.create_task(
            self._visual_branch(prepared, trigger_seconds, emit)
        )
        speech_result, visual_result = await asyncio.gather(
            speech_task,
            visual_task,
            return_exceptions=True,
        )
        return BranchResults(
            speech=self._normalize_speech_result(speech_result),
            visual=self._normalize_visual_result(visual_result),
        )

    async def _speech_branch(
        self,
        prepared: PreparedMedia,
        trigger_seconds: float,
        emit: EmitCallback,
    ) -> list[SpeechSignal]:
        await emit(
            RunStage.ANALYZING_EVIDENCE,
            "branch.started",
            {"branch": "speech"},
        )
        transcript = await self._asr.recognize(
            audio_path=prepared.audio_path,
            window_start_seconds=prepared.window.start_seconds,
            request_id=str(uuid4()),
        )
        if not transcript.text.strip():
            await emit(
                RunStage.ANALYZING_EVIDENCE,
                "branch.completed",
                {"branch": "speech", "evidence_count": 0},
            )
            return []
        window = Segment(
            start_seconds=prepared.window.start_seconds,
            end_seconds=prepared.window.end_seconds,
        )
        result = await self._ark.understand_speech(
            transcript=transcript.model_dump(
                mode="json",
                exclude={"provider_request_id"},
            ),
            trigger_seconds=trigger_seconds,
            window=window,
            instructions=self._skills.speech_instructions,
        )
        await emit(
            RunStage.ANALYZING_EVIDENCE,
            "branch.completed",
            {"branch": "speech", "evidence_count": len(result.signals)},
        )
        return result.signals

    async def _visual_branch(
        self,
        prepared: PreparedMedia,
        trigger_seconds: float,
        emit: EmitCallback,
    ) -> list[VisualSegment]:
        await emit(
            RunStage.ANALYZING_EVIDENCE,
            "branch.started",
            {"branch": "visual"},
        )
        result = await self._ark.locate_visual(
            video_path=prepared.video_path,
            window=Segment(
                start_seconds=prepared.window.start_seconds,
                end_seconds=prepared.window.end_seconds,
            ),
            trigger_seconds=trigger_seconds,
            instructions=self._skills.visual_instructions,
        )
        await emit(
            RunStage.ANALYZING_EVIDENCE,
            "branch.completed",
            {"branch": "visual", "evidence_count": len(result.segments)},
        )
        return result.segments

    @staticmethod
    def _normalize_speech_result(
        result: list[SpeechSignal] | BaseException,
    ) -> list[SpeechSignal] | ProviderError:
        if isinstance(result, ProviderError):
            return result
        if isinstance(result, BaseException):
            return ProviderError(
                "provider_error",
                "speech branch failed",
                retryable=True,
            )
        return result

    @staticmethod
    def _normalize_visual_result(
        result: list[VisualSegment] | BaseException,
    ) -> list[VisualSegment] | ProviderError:
        if isinstance(result, ProviderError):
            return result
        if isinstance(result, BaseException):
            return ProviderError(
                "provider_error",
                "visual branch failed",
                retryable=True,
            )
        return result


__all__ = ["LocalMediaProcessor", "OrchestratedAnalysisPipeline", "SkillRepository"]
