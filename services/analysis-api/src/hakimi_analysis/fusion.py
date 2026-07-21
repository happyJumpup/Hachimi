from dataclasses import dataclass

from hakimi_analysis.models import (
    ActionMode,
    AnalysisCandidate,
    CandidateParameters,
    EvidenceSpan,
    EvidenceType,
    Segment,
    SpeechSignal,
    VisualSegment,
)

_ACTION_ALIASES = {
    "dragcurl": "drag-curl",
    "拖拽弯举": "drag-curl",
    "拖拽式弯举": "drag-curl",
}


def _normalized_action_name(name: str) -> str:
    normalized = "".join(character for character in name.casefold() if character.isalnum())
    return _ACTION_ALIASES.get(normalized, normalized)


def _same_action(speech_name: str, visual_name: str | None) -> bool:
    if visual_name is None:
        return True
    speech = _normalized_action_name(speech_name)
    visual = _normalized_action_name(visual_name)
    return bool(speech and visual) and speech == visual


@dataclass(frozen=True, slots=True)
class CandidateFusionSkill:
    instructions: str
    version: str

    def run(
        self,
        *,
        source_id: str,
        speech_signals: list[SpeechSignal],
        visual_segments: list[VisualSegment],
    ) -> list[AnalysisCandidate]:
        if not self.instructions.strip() or not self.version.strip():
            raise ValueError("candidate fusion Skill must be versioned")
        return fuse_candidates(
            source_id=source_id,
            speech_signals=speech_signals,
            visual_segments=visual_segments,
        )


def _overlap_seconds(
    left_start: float,
    left_end: float,
    right_start: float,
    right_end: float,
) -> float:
    return max(0.0, min(left_end, right_end) - max(left_start, right_start))


def _gap_seconds(
    left_start: float,
    left_end: float,
    right_start: float,
    right_end: float,
) -> float:
    return max(0.0, max(left_start, right_start) - min(left_end, right_end))


def _parameters(signal: SpeechSignal | None) -> CandidateParameters:
    if signal is None:
        return CandidateParameters()
    mode: ActionMode | None = None
    reps = signal.reps
    duration_seconds = signal.duration_seconds
    if reps is not None:
        mode = ActionMode.REPS
        duration_seconds = None
    elif duration_seconds is not None:
        mode = ActionMode.DURATION
        reps = None
    return CandidateParameters(
        mode=mode,
        sets=signal.sets,
        reps=reps,
        duration_seconds=duration_seconds,
        rest_seconds=signal.rest_seconds,
    )


def fuse_candidates(
    *,
    source_id: str,
    speech_signals: list[SpeechSignal],
    visual_segments: list[VisualSegment],
) -> list[AnalysisCandidate]:
    candidates: list[AnalysisCandidate] = []
    matched_visual_indexes: set[int] = set()

    for speech in speech_signals:
        matches = [
            (
                index,
                _overlap_seconds(
                    speech.start_seconds,
                    speech.end_seconds,
                    visual.start_seconds,
                    visual.end_seconds,
                ),
                _gap_seconds(
                    speech.start_seconds,
                    speech.end_seconds,
                    visual.start_seconds,
                    visual.end_seconds,
                ),
            )
            for index, visual in enumerate(visual_segments)
            if index not in matched_visual_indexes
            and _same_action(speech.action_name, visual.action_name)
        ]
        matched_index, overlap, gap = min(
            matches,
            key=lambda item: (item[2], -item[1]),
            default=(-1, 0.0, float("inf")),
        )
        visual = visual_segments[matched_index] if overlap > 0 or gap <= 6 else None
        if visual is not None:
            matched_visual_indexes.add(matched_index)

        start_seconds = min(
            speech.start_seconds,
            visual.start_seconds if visual is not None else speech.start_seconds,
        )
        end_seconds = max(
            speech.end_seconds,
            visual.end_seconds if visual is not None else speech.end_seconds,
        )
        evidence = [
            EvidenceSpan(
                type=EvidenceType.SPEECH,
                start_seconds=speech.start_seconds,
                end_seconds=speech.end_seconds,
            )
        ]
        if visual is not None:
            evidence.append(
                EvidenceSpan(
                    type=EvidenceType.VISUAL,
                    start_seconds=visual.start_seconds,
                    end_seconds=visual.end_seconds,
                )
            )
        candidates.append(
            AnalysisCandidate(
                id="pending",
                name=speech.action_name,
                source_id=source_id,
                segment=Segment(start_seconds=start_seconds, end_seconds=end_seconds),
                parameters=_parameters(speech),
                evidence=evidence,
                needs_confirmation=visual is None or visual.action_name is None,
            )
        )

    for index, visual in enumerate(visual_segments):
        if index in matched_visual_indexes:
            continue
        candidates.append(
            AnalysisCandidate(
                id="pending",
                name=visual.action_name or "待确认动作",
                source_id=source_id,
                segment=Segment(
                    start_seconds=visual.start_seconds,
                    end_seconds=visual.end_seconds,
                ),
                parameters=CandidateParameters(),
                evidence=[
                    EvidenceSpan(
                        type=EvidenceType.VISUAL,
                        start_seconds=visual.start_seconds,
                        end_seconds=visual.end_seconds,
                    )
                ],
                needs_confirmation=True,
            )
        )

    candidates.sort(
        key=lambda candidate: (
            candidate.segment.start_seconds if candidate.segment is not None else float("inf")
        )
    )
    return [
        candidate.model_copy(update={"id": f"candidate-{index}"})
        for index, candidate in enumerate(candidates, start=1)
    ]
