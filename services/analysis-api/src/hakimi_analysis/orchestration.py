import asyncio
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from hakimi_analysis.fusion import CandidateFusionSkill
from hakimi_analysis.media import (
    LocalMediaProcessor,
    MediaProcessingError,
    PreparedMedia,
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
    def prepare_source(
        self,
        source_path: Path,
        duration_seconds: float,
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
        window: Segment,
        instructions: str,
    ) -> SpeechUnderstandingResult: ...

    async def locate_visual(
        self,
        *,
        video_path: Path,
        window: Segment,
        instructions: str,
    ) -> VisualLocalizationResult: ...

    async def locate_visual_contact_sheet(
        self,
        *,
        image_path: Path,
        frame_times_seconds: tuple[float, ...],
        window: Segment,
        instructions: str,
    ) -> VisualLocalizationResult: ...


@dataclass(frozen=True, slots=True)
class SkillRepository:
    speech_instructions: str
    speech_version: str
    visual_instructions: str
    visual_version: str
    fusion_instructions: str
    fusion_version: str

    @classmethod
    def load(cls, skills_root: Path) -> "SkillRepository":
        speech_path = skills_root / "training-speech-understanding" / "SKILL.md"
        visual_path = skills_root / "visual-action-localization" / "SKILL.md"
        fusion_path = skills_root / "candidate-fusion" / "SKILL.md"
        speech_instructions = speech_path.read_text(encoding="utf-8")
        visual_instructions = visual_path.read_text(encoding="utf-8")
        fusion_instructions = fusion_path.read_text(encoding="utf-8")
        return cls(
            speech_instructions=speech_instructions,
            speech_version=_skill_version(speech_instructions),
            visual_instructions=visual_instructions,
            visual_version=_skill_version(visual_instructions),
            fusion_instructions=fusion_instructions,
            fusion_version=_skill_version(fusion_instructions),
        )


def _skill_version(instructions: str) -> str:
    for line in instructions.splitlines():
        if line.startswith("version:"):
            version = line.partition(":")[2].strip()
            if version:
                return version
    raise ValueError("Skill contract is missing a version")


@dataclass(slots=True)
class BranchResults:
    speech: list[SpeechSignal] | ProviderError
    visual: list[VisualSegment] | ProviderError

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
        evidence_timeout_seconds: float = 11.5,
    ) -> None:
        self._media = media
        self._asr = asr
        self._ark = ark
        self._skills = skills
        self._evidence_timeout_seconds = evidence_timeout_seconds
        self._fusion = CandidateFusionSkill(
            instructions=skills.fusion_instructions,
            version=skills.fusion_version,
        )

    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float | None,
        emit: EmitCallback,
    ) -> PipelineOutput:
        del trigger_seconds
        await emit(
            RunStage.PREPARING_MEDIA,
            "stage.changed",
            {"analysis_scope": "full_source"},
        )
        try:
            async with self._media.prepare_source(
                source.path,
                source.duration_seconds,
            ) as prepared:
                results = await self._analyze_branches(prepared, emit)
        except MediaProcessingError as error:
            raise PipelineFailure(
                "media_error",
                "视频准备失败，请重试",
                retryable=False,
            ) from error

        speech_signals = results.speech if isinstance(results.speech, list) else []
        visual_segments = results.visual if isinstance(results.visual, list) else []
        if speech_signals or visual_segments:
            await emit(
                RunStage.FUSING_CANDIDATES,
                "stage.changed",
                {"skill_version": self._fusion.version},
            )
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
                candidates=self._fusion.run(
                    source_id=source.id,
                    speech_signals=speech_signals,
                    visual_segments=visual_segments,
                ),
                warnings=warnings,
            )

        if results.both_successful:
            return PipelineOutput(candidates=[], empty_reason="no_evidence")

        branch_error = (
            results.speech if isinstance(results.speech, ProviderError) else results.visual
        )
        if not isinstance(branch_error, ProviderError):
            raise AssertionError("failed analysis has no provider error")
        raise PipelineFailure(
            branch_error.code,
            str(branch_error),
            retryable=branch_error.retryable,
        ) from branch_error

    async def _analyze_branches(
        self,
        prepared: PreparedMedia,
        emit: EmitCallback,
    ) -> BranchResults:
        await emit(RunStage.ANALYZING_EVIDENCE, "stage.changed", {})
        speech_task = asyncio.create_task(self._speech_branch(prepared, emit))
        visual_task = asyncio.create_task(self._visual_branch(prepared, emit))
        tasks = {speech_task, visual_task}
        try:
            _done, pending = await asyncio.wait(
                tasks,
                timeout=self._evidence_timeout_seconds,
            )
            for task in pending:
                task.cancel()
            speech_result, visual_result = await asyncio.gather(
                speech_task,
                visual_task,
                return_exceptions=True,
            )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        if speech_task in pending:
            speech_result = ProviderError(
                "timeout", "speech evidence budget exceeded", retryable=True
            )
        if visual_task in pending:
            visual_result = ProviderError(
                "timeout", "visual evidence budget exceeded", retryable=True
            )
        return BranchResults(
            speech=self._normalize_speech_result(speech_result),
            visual=self._normalize_visual_result(visual_result),
        )

    async def _speech_branch(
        self,
        prepared: PreparedMedia,
        emit: EmitCallback,
    ) -> list[SpeechSignal]:
        await emit(
            RunStage.ANALYZING_EVIDENCE,
            "branch.started",
            {"branch": "speech", "skill_version": self._skills.speech_version},
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
            transcript={
                "text": transcript.text,
                "utterances": [
                    {
                        "text": utterance.text,
                        "start_seconds": utterance.start_seconds,
                        "end_seconds": utterance.end_seconds,
                    }
                    for utterance in transcript.utterances
                ],
            },
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
        emit: EmitCallback,
    ) -> list[VisualSegment]:
        await emit(
            RunStage.ANALYZING_EVIDENCE,
            "branch.started",
            {"branch": "visual", "skill_version": self._skills.visual_version},
        )
        if prepared.contact_sheet_ready is not None:
            await prepared.contact_sheet_ready
        if prepared.contact_sheet_path is None:
            raise ProviderError(
                "media_error",
                "visual contact sheet is unavailable",
                retryable=False,
            )
        result = await self._ark.locate_visual_contact_sheet(
            image_path=prepared.contact_sheet_path,
            frame_times_seconds=prepared.contact_sheet_timestamps,
            window=Segment(
                start_seconds=prepared.window.start_seconds,
                end_seconds=prepared.window.end_seconds,
            ),
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
