import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from time import monotonic
from typing import Any, Protocol
from uuid import uuid4

from hakimi_analysis.fusion import (
    EVIDENCE_RECONCILER_VERSION,
    CandidateFusionResult,
    action_name_can_join_group,
    fuse_candidate_evidence,
    preferred_action_name,
)
from hakimi_analysis.media import (
    AnalysisWindow,
    LocalMediaProcessor,
    MediaProcessingError,
    PreparedMedia,
    PreparedVisualChunk,
)
from hakimi_analysis.models import (
    AnalysisWarning,
    CoverageGap,
    CoverageGapReason,
    CoverageStatus,
    RunStage,
    Segment,
    SegmentRole,
    SpeechSignal,
    SpeechUnderstandingResult,
    Transcript,
    VisualLocalizationResult,
    VisualSegment,
)
from hakimi_analysis.pipeline import EmitCallback, PipelineFailure, PipelineOutput
from hakimi_analysis.provider_contracts import PromptContractRegistry
from hakimi_analysis.providers.base import ProviderError
from hakimi_analysis.sources import VideoSource
from hakimi_analysis.visual_routing import (
    DirectVisualProvider,
    SequentialVisualRouter,
)


class MediaProcessor(Protocol):
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

@dataclass(slots=True)
class VisualChunkResults:
    segments: list[VisualSegment]
    successful_windows: list[AnalysisWindow]
    failures: list[tuple[AnalysisWindow, ProviderError]]


def build_visual_chunks(
    duration_seconds: float,
    *,
    chunk_seconds: float,
    overlap_seconds: float,
) -> list[AnalysisWindow]:
    if duration_seconds <= 0 or chunk_seconds <= 0:
        raise ValueError("visual chunk durations must be positive")
    if overlap_seconds < 0 or overlap_seconds >= chunk_seconds:
        raise ValueError("visual chunk overlap must be shorter than the chunk")
    windows: list[AnalysisWindow] = []
    start_seconds = 0.0
    while start_seconds < duration_seconds:
        end_seconds = min(duration_seconds, start_seconds + chunk_seconds)
        windows.append(
            AnalysisWindow(
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                expanded=False,
            )
        )
        if end_seconds >= duration_seconds:
            break
        start_seconds = end_seconds - overlap_seconds
    return windows


class OrchestratedAnalysisPipeline:
    def __init__(
        self,
        *,
        media: MediaProcessor,
        asr: SpeechRecognizer,
        ark: ArkAnalyzer,
        contracts: PromptContractRegistry,
        visual_router: SequentialVisualRouter | None = None,
        speech_timeout_seconds: float = 45,
        visual_chunk_timeout_seconds: float | None = None,
        visual_chunk_seconds: float = 60,
        visual_overlap_seconds: float = 10,
        evidence_deadline_seconds: float = 170,
        run_timeout_seconds: float = 180,
        cleanup_reserve_seconds: float = 10,
        max_attempts_per_visual_provider: int = 2,
        max_visual_calls: int = 12,
        candidate_reconciler: Callable[
            [str, list[SpeechSignal], list[VisualSegment]], CandidateFusionResult
        ]
        | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._media = media
        self._asr = asr
        self._ark = ark
        self._visual_router = visual_router or SequentialVisualRouter(
            primary=DirectVisualProvider(ark),
            attempt_timeout_seconds=(
                20
                if visual_chunk_timeout_seconds is None
                else visual_chunk_timeout_seconds
            ),
            max_attempts_per_provider=max_attempts_per_visual_provider,
            max_visual_calls=max_visual_calls,
            clock=clock,
        )
        self._contracts = contracts
        self._speech_timeout_seconds = speech_timeout_seconds
        self._visual_chunk_timeout_seconds = (
            20
            if visual_chunk_timeout_seconds is None
            else visual_chunk_timeout_seconds
        )
        self._visual_chunk_seconds = visual_chunk_seconds
        self._visual_overlap_seconds = visual_overlap_seconds
        if (
            run_timeout_seconds <= 0
            or evidence_deadline_seconds <= 0
            or cleanup_reserve_seconds < 0
            or speech_timeout_seconds <= 0
        ):
            raise ValueError("provider budgets must be positive")
        if evidence_deadline_seconds + cleanup_reserve_seconds > run_timeout_seconds:
            raise ValueError("evidence deadline must leave the cleanup reserve")
        self._run_timeout_seconds = run_timeout_seconds
        self._evidence_deadline_seconds = evidence_deadline_seconds
        self._cleanup_reserve_seconds = cleanup_reserve_seconds
        self._candidate_reconciler = candidate_reconciler or (
            lambda source_id, speech, visual: fuse_candidate_evidence(
                source_id=source_id,
                speech_signals=speech,
                visual_segments=visual,
            )
        )
        self._clock = clock
        build_visual_chunks(
            1,
            chunk_seconds=self._visual_chunk_seconds,
            overlap_seconds=self._visual_overlap_seconds,
        )

    async def analyze(
        self,
        source: VideoSource,
        emit: EmitCallback,
    ) -> PipelineOutput:
        evidence_deadline = self._clock() + self._evidence_deadline_seconds
        await emit(
            RunStage.PREPARING_MEDIA,
            "stage.changed",
            {"analysis_scope": "full_source"},
        )
        try:
            async with self._media.prepare_source(
                source.path,
                source.analysis_duration_seconds,
                start_seconds=source.analysis_start_seconds,
            ) as prepared:
                speech_task = asyncio.create_task(
                    self._timed_speech_branch(prepared, emit, deadline=evidence_deadline)
                )
                try:
                    visual_results = await self._analyze_visual_chunks(
                        source,
                        prepared,
                        emit,
                        deadline=evidence_deadline,
                    )
                    speech_result = await speech_task
                finally:
                    if not speech_task.done():
                        speech_task.cancel()
                    await asyncio.gather(speech_task, return_exceptions=True)
        except MediaProcessingError as error:
            raise PipelineFailure(
                "media_error",
                "视频准备失败，请重试",
                retryable=False,
            ) from error

        speech_signals = speech_result if isinstance(speech_result, list) else []
        visual_segments = deduplicate_visual_segments(visual_results.segments)
        coverage_gaps = _coverage_gaps(
            source.analysis_duration_seconds,
            visual_results.successful_windows,
            visual_results.failures,
        )
        if isinstance(speech_result, ProviderError) and not visual_segments and not coverage_gaps:
            coverage_gaps = [
                CoverageGap(
                    start_seconds=0,
                    end_seconds=source.analysis_duration_seconds,
                    reason=_coverage_gap_reason(speech_result),
                    retryable=True,
                )
            ]
        processed_seconds = source.analysis_duration_seconds - sum(
            gap.end_seconds - gap.start_seconds for gap in coverage_gaps
        )
        warnings: list[AnalysisWarning] = []
        if isinstance(speech_result, ProviderError):
            warnings.append(
                AnalysisWarning(
                    code="speech_unavailable",
                    message="语音依据暂不可用，请重点确认动作名称和参数",
                )
            )
        if visual_results.failures:
            warnings.append(
                AnalysisWarning(
                    code="visual_unavailable",
                    message="部分画面依据暂不可用，请重试覆盖缺口",
                )
            )
        if speech_signals or visual_segments:
            await emit(
                RunStage.FUSING_CANDIDATES,
                "stage.changed",
                {"reconciler_version": EVIDENCE_RECONCILER_VERSION},
            )
            reconciliation = self._candidate_reconciler(
                source.id, speech_signals, visual_segments
            )
            return PipelineOutput(
                candidates=reconciliation.candidates,
                candidate_parameter_evidence=reconciliation.parameter_evidence,
                warnings=warnings,
                coverage_status=(
                    CoverageStatus.PARTIAL if coverage_gaps else CoverageStatus.COMPLETE
                ),
                coverage_gaps=coverage_gaps,
                processed_seconds=processed_seconds if coverage_gaps else None,
            )

        if coverage_gaps:
            return PipelineOutput(
                candidates=[],
                warnings=warnings,
                empty_reason="insufficient_evidence",
                coverage_status=CoverageStatus.INSUFFICIENT,
                coverage_gaps=coverage_gaps,
                processed_seconds=processed_seconds,
            )
        return PipelineOutput(candidates=[], warnings=warnings, empty_reason="no_evidence")

    async def _timed_speech_branch(
        self,
        prepared: PreparedMedia,
        emit: EmitCallback,
        *,
        deadline: float,
    ) -> list[SpeechSignal] | ProviderError:
        remaining_seconds = deadline - self._clock()
        if remaining_seconds <= 0:
            return ProviderError("timeout", "run budget exhausted before speech", retryable=True)
        try:
            async with asyncio.timeout(min(self._speech_timeout_seconds, remaining_seconds)):
                return await self._speech_branch(prepared, emit)
        except TimeoutError:
            return ProviderError("timeout", "speech evidence budget exceeded", retryable=True)
        except ProviderError as error:
            return error
        except Exception:
            return ProviderError("provider_error", "speech branch failed", retryable=True)

    async def _analyze_visual_chunks(
        self,
        source: VideoSource,
        prepared: PreparedMedia,
        emit: EmitCallback,
        *,
        deadline: float,
    ) -> VisualChunkResults:
        windows = build_visual_chunks(
            source.analysis_duration_seconds,
            chunk_seconds=self._visual_chunk_seconds,
            overlap_seconds=self._visual_overlap_seconds,
        )
        segments: list[VisualSegment] = []
        successful_windows: list[AnalysisWindow] = []
        failures: list[tuple[AnalysisWindow, ProviderError]] = []
        route_state = self._visual_router.new_run_state()
        await emit(
            RunStage.ANALYZING_EVIDENCE,
            "branch.started",
            {
                "branch": "visual",
                "contract_version": self._contracts.visual.contract_version,
                "chunk_count": len(windows),
            },
        )
        for index, window in enumerate(windows, start=1):
            remaining_seconds = deadline - self._clock()
            if remaining_seconds < self._visual_chunk_timeout_seconds:
                budget_error = ProviderError(
                    "timeout", "run budget exhausted before visual chunk", retryable=True
                )
                failures.extend((remaining, budget_error) for remaining in windows[index - 1 :])
                break
            await emit(
                RunStage.ANALYZING_EVIDENCE,
                "visual_chunk.started",
                {
                    "chunk_index": index,
                    "chunk_count": len(windows),
                    "start_seconds": window.start_seconds,
                    "end_seconds": window.end_seconds,
                },
            )
            try:
                async with asyncio.timeout(remaining_seconds):
                    chunk = await self._media.prepare_visual_chunk(
                        source.path,
                        window,
                        prepared.directory,
                        source_offset_seconds=source.analysis_start_seconds,
                    )
                    result = await self._visual_router.locate_visual(
                        video_path=chunk.video_path,
                        window=Segment(
                            start_seconds=0,
                            end_seconds=window.duration_seconds,
                        ),
                        instructions=self._contracts.visual.prompt,
                        state=route_state,
                        deadline=deadline,
                        chunk_index=index,
                    )
            except TimeoutError:
                timeout_error = ProviderError(
                    "timeout", "visual chunk evidence budget exceeded", retryable=True
                )
                failures.append((window, timeout_error))
            except MediaProcessingError:
                media_error = ProviderError("media_error", "visual chunk failed", retryable=True)
                failures.append((window, media_error))
            except ProviderError as provider_error:
                failures.append((window, provider_error))
            except Exception:
                unexpected_error = ProviderError(
                    "provider_error", "visual chunk failed", retryable=True
                )
                failures.append((window, unexpected_error))
            else:
                segments.extend(
                    _offset_visual_segments(result.segments, window.start_seconds)
                )
                successful_windows.append(window)
                processed_seconds = _covered_seconds(successful_windows)
                await emit(
                    RunStage.ANALYZING_EVIDENCE,
                    "visual_chunk.completed",
                    {
                        "chunk_index": index,
                        "chunk_count": len(windows),
                        "processed_seconds": processed_seconds,
                        "evidence_count": len(result.segments),
                    },
                )
        if successful_windows:
            await emit(
                RunStage.ANALYZING_EVIDENCE,
                "branch.completed",
                {
                    "branch": "visual",
                    "chunk_count": len(windows),
                    "successful_chunk_count": len(successful_windows),
                    "processed_seconds": _covered_seconds(successful_windows),
                    "evidence_count": len(segments),
                },
            )
        return VisualChunkResults(
            segments=segments,
            successful_windows=successful_windows,
            failures=failures,
        )

    async def _speech_branch(
        self,
        prepared: PreparedMedia,
        emit: EmitCallback,
    ) -> list[SpeechSignal]:
        await emit(
            RunStage.ANALYZING_EVIDENCE,
            "branch.started",
            {
                "branch": "speech",
                "contract_version": self._contracts.speech.contract_version,
            },
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
            instructions=self._contracts.speech.prompt,
        )
        await emit(
            RunStage.ANALYZING_EVIDENCE,
            "branch.completed",
            {"branch": "speech", "evidence_count": len(result.signals)},
        )
        return result.signals


def _covered_seconds(windows: list[AnalysisWindow]) -> float:
    if not windows:
        return 0.0
    ordered = sorted(windows, key=lambda window: (window.start_seconds, window.end_seconds))
    covered = 0.0
    current_start = ordered[0].start_seconds
    current_end = ordered[0].end_seconds
    for window in ordered[1:]:
        if window.start_seconds <= current_end:
            current_end = max(current_end, window.end_seconds)
            continue
        covered += current_end - current_start
        current_start = window.start_seconds
        current_end = window.end_seconds
    return covered + current_end - current_start


def _coverage_gaps(
    duration_seconds: float,
    successful_windows: list[AnalysisWindow],
    failures: list[tuple[AnalysisWindow, ProviderError]],
) -> list[CoverageGap]:
    if not failures:
        return []
    boundaries = {0.0, duration_seconds}
    for window in successful_windows:
        boundaries.update((window.start_seconds, window.end_seconds))
    for window, _error in failures:
        boundaries.update((window.start_seconds, window.end_seconds))
    ordered = sorted(boundaries)
    gaps: list[CoverageGap] = []
    for start_seconds, end_seconds in pairwise(ordered):
        if end_seconds <= start_seconds:
            continue
        midpoint = (start_seconds + end_seconds) / 2
        if any(
            window.start_seconds <= midpoint < window.end_seconds
            for window in successful_windows
        ):
            continue
        error = next(
            (
                failed_error
                for window, failed_error in failures
                if window.start_seconds <= midpoint < window.end_seconds
            ),
            ProviderError("provider_error", "visual coverage unavailable", retryable=True),
        )
        reason = _coverage_gap_reason(error)
        if gaps and gaps[-1].end_seconds == start_seconds and gaps[-1].reason == reason:
            gaps[-1] = gaps[-1].model_copy(update={"end_seconds": end_seconds})
        else:
            gaps.append(
                CoverageGap(
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                    reason=reason,
                    retryable=True,
                )
            )
    return gaps


def _coverage_gap_reason(error: ProviderError) -> CoverageGapReason:
    if error.code == "timeout":
        return CoverageGapReason.TIMEOUT
    if error.code == "media_error":
        return CoverageGapReason.MEDIA_ERROR
    if error.code == "provider_error":
        return CoverageGapReason.PROVIDER_ERROR
    return CoverageGapReason.UNKNOWN


def _normalized_visual_action(name: str | None) -> str:
    if name is None:
        return ""
    return "".join(character for character in name.casefold() if character.isalnum())


def _offset_visual_segments(
    segments: list[VisualSegment],
    offset_seconds: float,
) -> list[VisualSegment]:
    if offset_seconds == 0:
        return segments
    return [
        segment.model_copy(
            update={
                "start_seconds": segment.start_seconds + offset_seconds,
                "end_seconds": segment.end_seconds + offset_seconds,
            }
        )
        for segment in segments
    ]


def deduplicate_visual_segments(segments: list[VisualSegment]) -> list[VisualSegment]:
    grouped: list[list[VisualSegment]] = []
    ungrouped: list[VisualSegment] = []
    for segment in sorted(segments, key=lambda item: (item.start_seconds, item.end_seconds)):
        action_key = _normalized_visual_action(segment.action_name)
        if not action_key:
            ungrouped.append(segment)
            continue
        action_segments = next(
            (
                group
                for group in grouped
                if action_name_can_join_group(
                    [item.action_name or "" for item in group],
                    segment.action_name,
                )
            ),
            None,
        )
        if action_segments is None:
            action_segments = []
            grouped.append(action_segments)
        if segment.segment_role == SegmentRole.TEACHING_DEMO:
            teaching_index = next(
                (
                    index
                    for index, existing in enumerate(action_segments)
                    if existing.segment_role == SegmentRole.TEACHING_DEMO
                ),
                None,
            )
            if teaching_index is not None:
                existing = action_segments[teaching_index]
                selected = max(
                    (existing, segment),
                    key=lambda item: (
                        item.end_seconds - item.start_seconds,
                        item.visual_cue,
                    ),
                )
                action_segments[teaching_index] = selected.model_copy(
                    update={
                        "action_name": preferred_action_name(
                            existing.action_name or "",
                            segment.action_name,
                        )
                    }
                )
                continue
        overlapping_index = next(
            (
                index
                for index in range(len(action_segments) - 1, -1, -1)
                if segment.start_seconds <= action_segments[index].end_seconds
                and action_segments[index].start_seconds <= segment.end_seconds
            ),
            None,
        )
        if overlapping_index is not None:
            previous = action_segments[overlapping_index]
            role = (
                previous.segment_role
                if previous.segment_role == segment.segment_role
                else SegmentRole.UNKNOWN
            )
            action_segments[overlapping_index] = VisualSegment(
                action_name=preferred_action_name(
                    previous.action_name or "",
                    segment.action_name,
                ),
                start_seconds=min(previous.start_seconds, segment.start_seconds),
                end_seconds=max(previous.end_seconds, segment.end_seconds),
                visual_cue=previous.visual_cue,
                segment_role=role,
            )
            continue
        action_segments.append(segment)
    return sorted(
        [segment for action_segments in grouped for segment in action_segments]
        + ungrouped,
        key=lambda item: (
            item.start_seconds,
            item.end_seconds,
            _normalized_visual_action(item.action_name),
            item.visual_cue,
        ),
    )


__all__ = [
    "LocalMediaProcessor",
    "OrchestratedAnalysisPipeline",
    "build_visual_chunks",
    "deduplicate_visual_segments",
]
