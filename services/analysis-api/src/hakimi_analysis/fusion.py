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


def _overlap_seconds(
    left_start: float,
    left_end: float,
    right_start: float,
    right_end: float,
) -> float:
    return max(0.0, min(left_end, right_end) - max(left_start, right_start))


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
            (index, _overlap_seconds(
                speech.start_seconds,
                speech.end_seconds,
                visual.start_seconds,
                visual.end_seconds,
            ))
            for index, visual in enumerate(visual_segments)
        ]
        matched_index, overlap = max(matches, key=lambda item: item[1], default=(-1, 0.0))
        visual = visual_segments[matched_index] if overlap > 0 else None
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
                needs_confirmation=visual is None,
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
