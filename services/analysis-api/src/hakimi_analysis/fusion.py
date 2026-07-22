from dataclasses import dataclass

from hakimi_analysis.models import (
    ActionMode,
    AnalysisCandidate,
    CandidateParameters,
    EvidenceSpan,
    EvidenceType,
    Segment,
    SegmentRole,
    SpeechSignal,
    VisualSegment,
)

_ACTION_ALIASES = {
    "dragcurl": "drag-curl",
    "拖拽弯举": "drag-curl",
    "拖拽式弯举": "drag-curl",
}

_ACTION_MODIFIER_GROUPS = (
    ("杠铃", "barbell"),
    ("哑铃", "dumbbell"),
    ("器械", "machine"),
    ("绳索", "cable"),
    ("史密斯", "smith"),
    ("上斜", "incline"),
    ("下斜", "decline"),
    ("平板", "flat"),
    ("坐姿", "seated"),
    ("站姿", "standing"),
    ("俯身", "bentover"),
    ("反手", "underhand"),
    ("正手", "overhand"),
    ("窄距", "closegrip"),
    ("宽距", "widegrip"),
    ("单臂", "singlearm"),
    ("双臂", "doublearm"),
)

EVIDENCE_RECONCILER_VERSION = "deterministic-v2"

_MUTUALLY_EXCLUSIVE_MODIFIER_GROUPS = (
    _ACTION_MODIFIER_GROUPS[0:5],
    _ACTION_MODIFIER_GROUPS[5:8],
    _ACTION_MODIFIER_GROUPS[8:11],
    _ACTION_MODIFIER_GROUPS[11:13],
    _ACTION_MODIFIER_GROUPS[13:15],
    _ACTION_MODIFIER_GROUPS[15:17],
)


def _normalized_action_name(name: str) -> str:
    normalized = "".join(character for character in name.casefold() if character.isalnum())
    return _ACTION_ALIASES.get(normalized, normalized)


def _same_action(speech_name: str, visual_name: str | None) -> bool:
    if visual_name is None:
        return True
    speech = _normalized_action_name(speech_name)
    visual = _normalized_action_name(visual_name)
    return bool(speech and visual) and speech == visual


def _modifier_tokens(name: str) -> set[str]:
    normalized = _normalized_action_name(name)
    return {
        aliases[0]
        for aliases in _ACTION_MODIFIER_GROUPS
        if any(alias in normalized for alias in aliases)
    }


def _modifier_conflict(left_name: str, right_name: str) -> bool:
    left = _modifier_tokens(left_name)
    right = _modifier_tokens(right_name)
    for exclusive_group in _MUTUALLY_EXCLUSIVE_MODIFIER_GROUPS:
        canonical = {aliases[0] for aliases in exclusive_group}
        left_values = left & canonical
        right_values = right & canonical
        if left_values and right_values and left_values != right_values:
            return True
    return False


def _compatible_action_names(left_name: str, right_name: str | None) -> bool:
    if right_name is None:
        return True
    left = _normalized_action_name(left_name)
    right = _normalized_action_name(right_name)
    if not left or not right or _modifier_conflict(left_name, right_name):
        return False
    return left == right or left in right or right in left


def _preferred_action_name(left_name: str, right_name: str | None) -> str:
    if right_name is None or not _compatible_action_names(left_name, right_name):
        return left_name
    return max(
        (left_name, right_name),
        key=lambda name: (len(_normalized_action_name(name)), name.casefold()),
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


def _overlap_fraction_of_shorter(
    left_start: float,
    left_end: float,
    right_start: float,
    right_end: float,
) -> float:
    overlap = _overlap_seconds(left_start, left_end, right_start, right_end)
    shorter = min(left_end - left_start, right_end - right_start)
    return overlap / shorter if shorter > 0 else 0.0


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


def _segment_role(
    speech: SpeechSignal | None,
    visual: VisualSegment | None,
) -> SegmentRole:
    roles = {
        item.segment_role
        for item in (speech, visual)
        if item is not None and item.segment_role != SegmentRole.UNKNOWN
    }
    if len(roles) == 1:
        return next(iter(roles))
    return SegmentRole.UNKNOWN


def _speech_signal_quality(
    signal: SpeechSignal,
) -> tuple[int, int, tuple[int, int, int, int], str, str]:
    def sortable(value: int | None) -> int:
        return -1 if value is None else value

    parameter_values = (
        signal.sets,
        signal.reps,
        signal.duration_seconds,
        signal.rest_seconds,
    )
    return (
        sum(value is not None for value in parameter_values),
        int(signal.segment_role != SegmentRole.UNKNOWN),
        (
            sortable(signal.sets),
            sortable(signal.reps),
            sortable(signal.duration_seconds),
            sortable(signal.rest_seconds),
        ),
        signal.action_name.casefold(),
        signal.evidence_text,
    )


def action_name_can_join_group(
    existing_names: list[str],
    candidate_name: str | None,
) -> bool:
    if candidate_name is None or not existing_names:
        return False
    if any(_modifier_conflict(name, candidate_name) for name in existing_names):
        return False
    return any(_compatible_action_names(name, candidate_name) for name in existing_names)


def preferred_action_name(left_name: str, right_name: str | None) -> str:
    return _preferred_action_name(left_name, right_name)


@dataclass(slots=True)
class _MergedSpeechEvidence:
    signal: SpeechSignal
    evidence: list[EvidenceSpan]
    parameter_evidence: dict[str, list[EvidenceSpan]]
    needs_confirmation: bool


@dataclass(slots=True)
class CandidateFusionResult:
    candidates: list[AnalysisCandidate]
    parameter_evidence: dict[str, dict[str, list[EvidenceSpan]]]


def _evidence_span(signal: SpeechSignal) -> EvidenceSpan:
    return EvidenceSpan(
        type=EvidenceType.SPEECH,
        start_seconds=signal.start_seconds,
        end_seconds=signal.end_seconds,
    )


def _signals_overlap(left: SpeechSignal, right: SpeechSignal) -> bool:
    return (
        _overlap_fraction_of_shorter(
            left.start_seconds,
            left.end_seconds,
            right.start_seconds,
            right.end_seconds,
        )
        >= 0.5
    )


def _may_group_speech(group: list[SpeechSignal], signal: SpeechSignal) -> bool:
    if not action_name_can_join_group(
        [existing.action_name for existing in group], signal.action_name
    ):
        return False
    for existing in group:
        teaching = (
            existing.segment_role == SegmentRole.TEACHING_DEMO
            and signal.segment_role == SegmentRole.TEACHING_DEMO
        )
        if _compatible_action_names(existing.action_name, signal.action_name) and (
            teaching or _signals_overlap(existing, signal)
        ):
            return True
    return False


def _merge_parameter(
    signals: list[SpeechSignal],
    field_name: str,
) -> tuple[int | None, list[EvidenceSpan], bool]:
    with_values = [
        signal for signal in signals if getattr(signal, field_name) is not None
    ]
    values = {getattr(signal, field_name) for signal in with_values}
    if len(values) != 1:
        return None, [], len(values) > 1
    value = next(iter(values), None)
    return value, [_evidence_span(signal) for signal in with_values], False


def _merge_speech_group(signals: list[SpeechSignal]) -> _MergedSpeechEvidence:
    representative = max(signals, key=_speech_signal_quality)
    compatible_name = all(
        _compatible_action_names(signals[0].action_name, signal.action_name)
        for signal in signals[1:]
    )
    action_name = representative.action_name
    if compatible_name:
        for signal in signals:
            action_name = _preferred_action_name(action_name, signal.action_name)
    parameter_values: dict[str, int | None] = {}
    parameter_evidence: dict[str, list[EvidenceSpan]] = {}
    parameter_conflict = False
    for field_name in ("sets", "reps", "duration_seconds", "rest_seconds"):
        value, evidence, conflict = _merge_parameter(signals, field_name)
        parameter_values[field_name] = value
        parameter_evidence[field_name] = evidence
        parameter_conflict = parameter_conflict or conflict
    if (
        parameter_values["reps"] is not None
        and parameter_values["duration_seconds"] is not None
    ):
        parameter_values["reps"] = None
        parameter_values["duration_seconds"] = None
        parameter_evidence["reps"] = []
        parameter_evidence["duration_seconds"] = []
        parameter_conflict = True
    same_event = all(
        _signals_overlap(left, right)
        for index, left in enumerate(signals)
        for right in signals[index + 1 :]
    )
    merged = representative.model_copy(
        update={
            "action_name": action_name,
            "start_seconds": (
                min(signal.start_seconds for signal in signals)
                if same_event
                else representative.start_seconds
            ),
            "end_seconds": (
                max(signal.end_seconds for signal in signals)
                if same_event
                else representative.end_seconds
            ),
            **parameter_values,
        }
    )
    evidence_by_range = {
        (signal.start_seconds, signal.end_seconds): _evidence_span(signal)
        for signal in signals
    }
    evidence = sorted(
        evidence_by_range.values(),
        key=lambda span: (span.start_seconds, span.end_seconds),
    )
    return _MergedSpeechEvidence(
        signal=merged,
        evidence=evidence,
        parameter_evidence=parameter_evidence,
        needs_confirmation=not compatible_name or parameter_conflict,
    )


def _deduplicate_speech_signals(signals: list[SpeechSignal]) -> list[_MergedSpeechEvidence]:
    groups: list[list[SpeechSignal]] = []
    for signal in sorted(
        signals,
        key=lambda item: (
            item.start_seconds,
            item.end_seconds,
            _normalized_action_name(item.action_name),
            item.evidence_text,
        ),
    ):
        group = next((item for item in groups if _may_group_speech(item, signal)), None)
        if group is None:
            groups.append([signal])
        else:
            group.append(signal)
    return sorted(
        [_merge_speech_group(group) for group in groups],
        key=lambda item: (
            item.signal.start_seconds,
            item.signal.end_seconds,
            _normalized_action_name(item.signal.action_name),
        ),
    )


def fuse_candidate_evidence(
    *,
    source_id: str,
    speech_signals: list[SpeechSignal],
    visual_segments: list[VisualSegment],
) -> CandidateFusionResult:
    candidates: list[tuple[AnalysisCandidate, dict[str, list[EvidenceSpan]]]] = []
    matched_visual_indexes: set[int] = set()
    merged_speech_signals = _deduplicate_speech_signals(speech_signals)

    for speech_evidence in merged_speech_signals:
        speech = speech_evidence.signal
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
            and _compatible_action_names(speech.action_name, visual.action_name)
        ]
        matched_index, overlap, gap = min(
            matches,
            key=lambda item: (item[2], -item[1]),
            default=(-1, 0.0, float("inf")),
        )
        visual = visual_segments[matched_index] if overlap > 0 or gap <= 6 else None
        name_conflict = False
        if visual is None:
            conflicting_matches = [
                (
                    index,
                    _overlap_fraction_of_shorter(
                        speech.start_seconds,
                        speech.end_seconds,
                        candidate.start_seconds,
                        candidate.end_seconds,
                    ),
                )
                for index, candidate in enumerate(visual_segments)
                if index not in matched_visual_indexes
                and not _modifier_conflict(
                    speech.action_name,
                    candidate.action_name or "",
                )
            ]
            conflict_index, conflict_overlap = max(
                conflicting_matches,
                key=lambda item: item[1],
                default=(-1, 0.0),
            )
            if conflict_overlap >= 0.5:
                visual = visual_segments[conflict_index]
                matched_index = conflict_index
                name_conflict = True
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
        evidence = list(speech_evidence.evidence)
        if visual is not None:
            evidence.append(
                EvidenceSpan(
                    type=EvidenceType.VISUAL,
                    start_seconds=visual.start_seconds,
                    end_seconds=visual.end_seconds,
                )
            )
        segment_role = _segment_role(speech, visual)
        candidates.append(
            (
                AnalysisCandidate(
                id="pending",
                name=_preferred_action_name(
                    speech.action_name,
                    None if name_conflict or visual is None else visual.action_name,
                ),
                source_id=source_id,
                segment=Segment(start_seconds=start_seconds, end_seconds=end_seconds),
                parameters=_parameters(speech),
                evidence=evidence,
                needs_confirmation=(
                    visual is None
                    or visual.action_name is None
                    or name_conflict
                    or speech_evidence.needs_confirmation
                    or segment_role == SegmentRole.UNKNOWN
                ),
                ),
                speech_evidence.parameter_evidence,
            )
        )

    for index, visual in enumerate(visual_segments):
        if index in matched_visual_indexes:
            continue
        candidates.append(
            (
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
                ),
                {},
            )
        )

    candidates.sort(
        key=lambda item: (
            item[0].segment.start_seconds
            if item[0].segment is not None
            else float("inf")
        )
    )
    numbered: list[AnalysisCandidate] = []
    parameter_evidence: dict[str, dict[str, list[EvidenceSpan]]] = {}
    for index, (candidate, evidence_by_field) in enumerate(candidates, start=1):
        candidate_id = f"candidate-{index}"
        numbered.append(candidate.model_copy(update={"id": candidate_id}))
        parameter_evidence[candidate_id] = evidence_by_field
    return CandidateFusionResult(
        candidates=numbered,
        parameter_evidence=parameter_evidence,
    )


def fuse_candidates(
    *,
    source_id: str,
    speech_signals: list[SpeechSignal],
    visual_segments: list[VisualSegment],
) -> list[AnalysisCandidate]:
    return fuse_candidate_evidence(
        source_id=source_id,
        speech_signals=speech_signals,
        visual_segments=visual_segments,
    ).candidates
