from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from hakimi_analysis.fusion import (
    EVIDENCE_RECONCILER_VERSION,
    CandidateFusionResult,
    fuse_candidate_evidence,
)
from hakimi_analysis.media import (
    AnalysisWindow,
    PreparedMedia,
    PreparedVisualChunk,
)
from hakimi_analysis.models import (
    AnalysisCandidate,
    AnalysisWarning,
    CandidateParameters,
    CoverageGap,
    CoverageStatus,
    EvidenceSpan,
    EvidenceType,
    Segment,
    SpeechSignal,
    SpeechUnderstandingResult,
    StrictModel,
    Transcript,
    VisualLocalizationResult,
    VisualSegment,
)
from hakimi_analysis.orchestration import OrchestratedAnalysisPipeline
from hakimi_analysis.pipeline import EmitCallback, PipelineOutput
from hakimi_analysis.provider_contracts import PromptContractRegistry
from hakimi_analysis.sources import VideoSource
from hakimi_analysis.visual_routing import SequentialVisualRouter


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    source: VideoSource


class MediaNormalizer(Protocol):
    def prepare_source(
        self,
        source_path: Path,
        duration_seconds: float,
        *,
        start_seconds: float = 0,
    ) -> AbstractAsyncContextManager[PreparedMedia]: ...

    async def prepare_visual_chunk(
        self,
        source_path: Path,
        window: AnalysisWindow,
        directory: Path,
        *,
        source_offset_seconds: float = 0,
    ) -> PreparedVisualChunk: ...


class TimestampedTranscriber(Protocol):
    async def recognize(
        self,
        *,
        audio_path: Path,
        window_start_seconds: float,
        request_id: str,
    ) -> Transcript: ...


class SpeechEvidenceInterpreter(Protocol):
    async def understand_speech(
        self,
        *,
        transcript: dict[str, Any],
        window: Segment,
        instructions: str,
    ) -> SpeechUnderstandingResult: ...


class VisualEvidenceLocator(Protocol):
    async def locate_visual(
        self,
        *,
        video_path: Path,
        window: Segment,
        instructions: str,
    ) -> VisualLocalizationResult: ...


class ContentEvidence(StrictModel):
    id: str = Field(min_length=1)
    type: EvidenceType
    segment: Segment


class FieldEvidence(StrictModel):
    name: list[str] = Field(default_factory=list)
    segment: list[str] = Field(default_factory=list)
    sets: list[str] = Field(default_factory=list)
    reps: list[str] = Field(default_factory=list)
    duration_seconds: list[str] = Field(default_factory=list)
    rest_seconds: list[str] = Field(default_factory=list)


class ContentAction(StrictModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    source_clip: Segment
    parameters: CandidateParameters
    evidence: list[ContentEvidence]
    field_evidence: FieldEvidence
    needs_confirmation: bool


class BranchResult(StrictModel):
    branch: Literal["speech", "visual"]
    status: Literal["complete", "partial", "empty", "failed"]
    error_code: str | None = None


class ContentUnderstandingResult(StrictModel):
    source_id: str = Field(min_length=1)
    analysis_range: Segment
    coverage_status: CoverageStatus
    coverage_gaps: list[CoverageGap] = Field(default_factory=list)
    processed_seconds: float
    actions: list[ContentAction] = Field(default_factory=list)
    branches: list[BranchResult]
    warnings: list[AnalysisWarning] = Field(default_factory=list)
    empty_reason: Literal["no_evidence", "insufficient_evidence"] | None = None
    source_rhythm: list[Segment] | None = None
    safety_warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_evidence_references(self) -> ContentUnderstandingResult:
        if self.coverage_status == CoverageStatus.COMPLETE and self.coverage_gaps:
            raise ValueError("complete content understanding cannot contain coverage gaps")
        if [action.source_clip.start_seconds for action in self.actions] != sorted(
            action.source_clip.start_seconds for action in self.actions
        ):
            raise ValueError("content actions must follow source order")
        result_evidence_ids: set[str] = set()
        for action in self.actions:
            evidence_by_id = {evidence.id: evidence for evidence in action.evidence}
            if len(evidence_by_id) != len(action.evidence):
                raise ValueError("evidence IDs must be unique within an action")
            if result_evidence_ids.intersection(evidence_by_id):
                raise ValueError("evidence IDs must be unique within a result")
            result_evidence_ids.update(evidence_by_id)
            for evidence in action.evidence:
                if not _within(evidence.segment, self.analysis_range):
                    raise ValueError("evidence is outside the analysis range")
            for field_name in (
                "name",
                "segment",
                "sets",
                "reps",
                "duration_seconds",
                "rest_seconds",
            ):
                for evidence_id in getattr(action.field_evidence, field_name):
                    if evidence_id not in evidence_by_id:
                        raise ValueError("field references an unknown evidence ID")
            for field_name in ("sets", "reps", "duration_seconds", "rest_seconds"):
                value = getattr(action.parameters, field_name)
                references = getattr(action.field_evidence, field_name)
                if value is not None and not references:
                    raise ValueError("non-null parameter must cite speech evidence")
                if any(
                    evidence_by_id[evidence_id].type != EvidenceType.SPEECH
                    for evidence_id in references
                ):
                    raise ValueError("training parameters may cite only speech evidence")
        return self

    def to_pipeline_output(self) -> PipelineOutput:
        return PipelineOutput(
            candidates=[
                AnalysisCandidate(
                    id=action.id,
                    name=action.name,
                    source_id=self.source_id,
                    segment=action.source_clip,
                    parameters=action.parameters,
                    evidence=[
                        EvidenceSpan(
                            type=evidence.type,
                            start_seconds=evidence.segment.start_seconds,
                            end_seconds=evidence.segment.end_seconds,
                        )
                        for evidence in action.evidence
                    ],
                    needs_confirmation=action.needs_confirmation,
                )
                for action in self.actions
            ],
            warnings=self.warnings,
            empty_reason=self.empty_reason,
            coverage_status=self.coverage_status,
            coverage_gaps=self.coverage_gaps,
            processed_seconds=(
                self.processed_seconds
                if self.coverage_status != CoverageStatus.COMPLETE
                else None
            ),
        )


class EvidenceReconciler:
    version = EVIDENCE_RECONCILER_VERSION

    def reconcile_candidates(
        self,
        source_id: str,
        speech_signals: list[SpeechSignal],
        visual_segments: list[VisualSegment],
    ) -> CandidateFusionResult:
        return fuse_candidate_evidence(
            source_id=source_id,
            speech_signals=speech_signals,
            visual_segments=visual_segments,
        )

    def reconcile(
        self,
        *,
        source: VideoSource,
        output: PipelineOutput,
    ) -> ContentUnderstandingResult:
        offset = source.analysis_start_seconds
        analysis_range = Segment(
            start_seconds=offset,
            end_seconds=offset + source.analysis_duration_seconds,
        )
        actions = [
            self._action(
                candidate,
                offset=offset,
                parameter_evidence=output.candidate_parameter_evidence.get(
                    candidate.id, {}
                ),
            )
            for candidate in output.candidates
        ]
        actions.sort(key=lambda action: (action.source_clip.start_seconds, action.id))
        gaps = [_offset_gap(gap, offset) for gap in output.coverage_gaps]
        speech_evidence = any(
            evidence.type == EvidenceType.SPEECH
            for action in actions
            for evidence in action.evidence
        )
        visual_evidence = any(
            evidence.type == EvidenceType.VISUAL
            for action in actions
            for evidence in action.evidence
        )
        warning_codes = {warning.code for warning in output.warnings}
        visual_failed = "visual_unavailable" in warning_codes
        speech_failed = "speech_unavailable" in warning_codes
        visual_status: Literal["complete", "partial", "empty", "failed"]
        if visual_failed and visual_evidence:
            visual_status = "partial"
        elif visual_failed:
            visual_status = "failed"
        else:
            visual_status = "complete" if visual_evidence else "empty"
        return ContentUnderstandingResult(
            source_id=source.id,
            analysis_range=analysis_range,
            coverage_status=output.coverage_status,
            coverage_gaps=gaps,
            processed_seconds=(
                source.analysis_duration_seconds
                if output.coverage_status == CoverageStatus.COMPLETE
                else float(output.processed_seconds or 0)
            ),
            actions=actions,
            branches=[
                BranchResult(
                    branch="speech",
                    status=(
                        "failed"
                        if speech_failed
                        else "complete" if speech_evidence else "empty"
                    ),
                    error_code="speech_unavailable" if speech_failed else None,
                ),
                BranchResult(
                    branch="visual",
                    status=visual_status,
                    error_code="visual_unavailable" if visual_failed else None,
                ),
            ],
            warnings=output.warnings,
            empty_reason=output.empty_reason,
        )

    def _action(
        self,
        candidate: AnalysisCandidate,
        *,
        offset: float,
        parameter_evidence: dict[str, list[EvidenceSpan]],
    ) -> ContentAction:
        source_clip = _offset_segment(candidate.segment, offset)
        evidence: list[ContentEvidence] = []
        type_counts = {EvidenceType.SPEECH: 0, EvidenceType.VISUAL: 0}
        evidence_ids_by_span: dict[tuple[EvidenceType, float, float], list[str]] = {}
        for span in candidate.evidence:
            type_counts[span.type] += 1
            evidence_id = (
                f"{candidate.id}-{span.type.value}-{type_counts[span.type]:03d}"
            )
            evidence.append(
                ContentEvidence(
                    id=evidence_id,
                    type=span.type,
                    segment=_offset_segment(span, offset),
                )
            )
            evidence_ids_by_span.setdefault(
                (span.type, span.start_seconds, span.end_seconds), []
            ).append(evidence_id)
        all_ids = [item.id for item in evidence]

        def evidence_ids(spans: list[EvidenceSpan]) -> list[str]:
            ids: list[str] = []
            for span in spans:
                matches = evidence_ids_by_span.get(
                    (span.type, span.start_seconds, span.end_seconds), []
                )
                if not matches:
                    raise ValueError("parameter evidence is absent from candidate evidence")
                for evidence_id in matches:
                    if evidence_id not in ids:
                        ids.append(evidence_id)
            return ids

        parameter_evidence_ids = {
            field_name: evidence_ids(parameter_evidence.get(field_name, []))
            for field_name in ("sets", "reps", "duration_seconds", "rest_seconds")
        }
        return ContentAction(
            id=candidate.id,
            name=candidate.name,
            source_clip=source_clip,
            parameters=candidate.parameters,
            evidence=evidence,
            field_evidence=FieldEvidence(
                name=all_ids,
                segment=all_ids,
                **parameter_evidence_ids,
            ),
            needs_confirmation=candidate.needs_confirmation,
        )


class _EvidenceInterpreterAdapter:
    def __init__(
        self,
        speech: SpeechEvidenceInterpreter,
        visual: VisualEvidenceLocator,
    ) -> None:
        self._speech = speech
        self._visual = visual

    async def understand_speech(self, **kwargs: Any) -> SpeechUnderstandingResult:
        return await self._speech.understand_speech(**kwargs)

    async def locate_visual(self, **kwargs: Any) -> VisualLocalizationResult:
        return await self._visual.locate_visual(**kwargs)


class ContentUnderstandingProvider:
    def __init__(
        self,
        *,
        media: MediaNormalizer,
        transcriber: TimestampedTranscriber,
        interpreter: SpeechEvidenceInterpreter,
        visual_locator: VisualEvidenceLocator,
        visual_router: SequentialVisualRouter | None = None,
        contracts: PromptContractRegistry,
        reconciler: EvidenceReconciler | None = None,
        speech_timeout_seconds: float = 45,
        visual_chunk_timeout_seconds: float = 20,
        visual_chunk_seconds: float = 60,
        visual_overlap_seconds: float = 10,
        evidence_deadline_seconds: float = 170,
        run_timeout_seconds: float = 180,
        cleanup_reserve_seconds: float = 10,
        max_attempts_per_visual_provider: int = 2,
        max_visual_calls: int = 12,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._reconciler = reconciler or EvidenceReconciler()
        self._pipeline = OrchestratedAnalysisPipeline(
            media=media,
            asr=transcriber,
            ark=_EvidenceInterpreterAdapter(interpreter, visual_locator),
            contracts=contracts,
            visual_router=visual_router,
            speech_timeout_seconds=speech_timeout_seconds,
            visual_chunk_timeout_seconds=visual_chunk_timeout_seconds,
            visual_chunk_seconds=visual_chunk_seconds,
            visual_overlap_seconds=visual_overlap_seconds,
            evidence_deadline_seconds=evidence_deadline_seconds,
            run_timeout_seconds=run_timeout_seconds,
            cleanup_reserve_seconds=cleanup_reserve_seconds,
            max_attempts_per_visual_provider=max_attempts_per_visual_provider,
            max_visual_calls=max_visual_calls,
            candidate_reconciler=self._reconciler.reconcile_candidates,
            clock=clock,
        )

    async def analyze(
        self,
        request: AnalysisRequest,
        emit: EmitCallback,
    ) -> ContentUnderstandingResult:
        output = await self._pipeline.analyze(request.source, emit)
        return self._reconciler.reconcile(source=request.source, output=output)


class ContentUnderstandingPipeline:
    def __init__(self, provider: ContentUnderstandingProvider) -> None:
        self._provider = provider

    async def analyze(
        self,
        source: VideoSource,
        emit: EmitCallback,
    ) -> PipelineOutput:
        result = await self._provider.analyze(AnalysisRequest(source=source), emit)
        return result.to_pipeline_output()


def _offset_segment(segment: Segment, offset: float) -> Segment:
    return Segment(
        start_seconds=segment.start_seconds + offset,
        end_seconds=segment.end_seconds + offset,
    )


def _offset_gap(gap: CoverageGap, offset: float) -> CoverageGap:
    return gap.model_copy(
        update={
            "start_seconds": gap.start_seconds + offset,
            "end_seconds": gap.end_seconds + offset,
        }
    )


def _within(segment: Segment, container: Segment) -> bool:
    return (
        container.start_seconds <= segment.start_seconds
        and segment.end_seconds <= container.end_seconds
    )


__all__ = [
    "AnalysisRequest",
    "BranchResult",
    "ContentAction",
    "ContentEvidence",
    "ContentUnderstandingPipeline",
    "ContentUnderstandingProvider",
    "ContentUnderstandingResult",
    "EvidenceReconciler",
    "FieldEvidence",
    "MediaNormalizer",
    "SpeechEvidenceInterpreter",
    "TimestampedTranscriber",
    "VisualEvidenceLocator",
]
