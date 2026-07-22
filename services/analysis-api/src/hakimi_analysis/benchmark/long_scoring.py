"""Offline, aggregate-only scoring for the long-video A/B experiment.

This module deliberately accepts already structured candidates and safe run
metadata. It never opens media, reads transcripts, or retains provider output.
"""

import re
from collections import defaultdict
from enum import StrEnum
from itertools import combinations
from statistics import median
from typing import Literal

from pydantic import Field, model_validator

from hakimi_analysis.benchmark.long_contract import MAX_LONG_PRIMARY_DEMO_SECONDS
from hakimi_analysis.benchmark.long_models import ArmId, LongExperimentSource
from hakimi_analysis.benchmark.models import (
    BenchmarkCandidate,
    CleanupOutcome,
    GoldEvent,
    ParameterName,
    StrictModel,
)
from hakimi_analysis.benchmark.scoring import score_candidates, temporal_iou

# Compatibility name for the scoring seam; use the manifest's canonical arm ID.
LongVideoArm = ArmId


class LongRunStatus(StrEnum):
    """Terminal status local to the long-video experiment."""

    COMPLETED = "completed"
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"
    SCHEMA_ERROR = "schema_error"
    CANCELLED = "cancelled"


class LongMediaLifecycleRecord(StrictModel):
    """Long-study-only lifecycle audit without provider object identifiers."""

    provider: str
    model_id: str
    media_kind: str
    upload_seconds: float = Field(ge=0)
    cleanup_seconds: float = Field(default=0, ge=0)
    cleanup_outcome: CleanupOutcome | None = None
    expires_at: str | None = None


class LongVideoSourceGold(StrictModel):
    """Reviewed, path-free gold for a complete long video."""

    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    duration_seconds: float = Field(gt=0)
    events: list[GoldEvent]
    gold_version: str = Field(default="v1", min_length=1, max_length=64)
    reviewed: bool = False

    @model_validator(mode="after")
    def validate_events_are_in_bounds(self) -> "LongVideoSourceGold":
        if not self.reviewed:
            raise ValueError("long-video scoring requires reviewed gold")
        if any(event.end_seconds > self.duration_seconds for event in self.events):
            raise ValueError("gold event falls outside the full source")
        if self.gold_version == "long-video-gold-v2":
            if not self.events:
                raise ValueError("long-video gold v2 requires at least one trainable action")
            if any(
                event.end_seconds - event.start_seconds > MAX_LONG_PRIMARY_DEMO_SECONDS
                for event in self.events
            ):
                raise ValueError("long-video gold v2 primary demos cannot exceed 120 seconds")
            identities = [
                re.sub(r"[^\w]", "", event.canonical_name.casefold(), flags=re.UNICODE)
                for event in self.events
            ]
            if any(event.kind.value != "action" for event in self.events) or len(identities) != len(
                set(identities)
            ):
                raise ValueError(
                    "long-video gold v2 requires one event per unique trainable action"
                )
        return self


def validate_gold_against_source_contract(
    source: LongExperimentSource,
    gold: LongVideoSourceGold,
) -> LongVideoSourceGold:
    """Bind reviewed gold to the source's offline-only action contract."""

    if (
        gold.source_id != source.source_id
        or abs(gold.duration_seconds - source.duration_seconds) > 1e-6
        or gold.gold_version != source.gold_version
    ):
        raise ValueError("long-video gold does not match its manifest source")
    expected = {action.canonical_name: action for action in source.gold_contract.actions}
    actual = {event.canonical_name: event for event in gold.events}
    if set(actual) != set(expected):
        raise ValueError("long-video gold actions do not match the manifest source contract")
    for canonical_name, event in actual.items():
        if set(event.accepted_aliases) != set(expected[canonical_name].accepted_aliases):
            raise ValueError("long-video gold aliases do not match the manifest source contract")
    return gold


class LongVideoRun(StrictModel):
    """Safe result metadata for one source/arm/repetition run."""

    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    arm: LongVideoArm
    run_index: int = Field(ge=1, le=3)
    status: LongRunStatus
    error_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,64}$")
    candidates: list[BenchmarkCandidate] = Field(default_factory=list)
    full_coverage: bool = False
    cleanup_ok: bool = False
    resumed_from_checkpoint: bool = False
    model_weight_violation_count: int = Field(default=0, ge=0)
    lifecycle_audit: list[LongMediaLifecycleRecord] = Field(default_factory=list)
    queue_seconds: float = Field(default=0, ge=0)
    upload_seconds: float = Field(default=0, ge=0)
    preprocessing_seconds: float = Field(default=0, ge=0)
    asr_seconds: float = Field(default=0, ge=0)
    visual_seconds: float = Field(default=0, ge=0)
    fusion_seconds: float = Field(default=0, ge=0)
    cleanup_seconds: float = Field(default=0, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    first_candidate_seconds: float | None = Field(default=None, ge=0)
    completed_seconds: float | None = Field(default=None, ge=0)
    cost_cny: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_terminal_outcome(self) -> "LongVideoRun":
        if self.status == LongRunStatus.COMPLETED and self.completed_seconds is None:
            raise ValueError("completed long-video run requires elapsed time")
        if self.status != LongRunStatus.COMPLETED and self.candidates:
            raise ValueError("failed long-video run cannot include candidates")
        return self


class LongSourceArmAggregate(StrictModel):
    """Sanitized aggregate for all three repetitions of one source and arm."""

    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    duration_seconds: float = Field(gt=0)
    expected_runs: int = Field(ge=0)
    completed_runs: int = Field(ge=0)
    success_rate: float = Field(ge=0, le=1)
    precision: float = Field(ge=0, le=1)
    recall: float = Field(ge=0, le=1)
    f1_tiou_03: float = Field(ge=0, le=1)
    f1_tiou_05: float = Field(ge=0, le=1)
    action_type_recall: float | None = Field(default=None, ge=0, le=1)
    tail_recall: float | None = Field(default=None, ge=0, le=1)
    unsupported_parameter_rate: float = Field(ge=0, le=1)
    weight_violation_count: int = Field(ge=0)
    schema_violation_count: int = Field(ge=0)
    cleanup_violation_count: int = Field(ge=0)
    coverage_violation_count: int = Field(ge=0)
    resumed_run_count: int = Field(default=0, ge=0)
    cancelled_run_count: int = Field(ge=0)
    median_first_candidate_seconds: float | None = Field(default=None, ge=0)
    median_completed_seconds: float | None = Field(default=None, ge=0)
    median_seconds_per_video_minute: float | None = Field(default=None, ge=0)
    seconds_per_video_minute_range: tuple[float, float] | None = None
    stability_f1: float | None = Field(default=None, ge=0, le=1)
    eligible: bool


class LongArmAggregate(StrictModel):
    """Safe metrics across every source and all three repetitions of one arm."""

    arm: LongVideoArm
    source_count: int = Field(ge=0)
    expected_runs: int = Field(ge=0)
    completed_runs: int = Field(ge=0)
    success_rate: float = Field(ge=0, le=1)
    precision: float = Field(ge=0, le=1)
    recall: float = Field(ge=0, le=1)
    precision_tiou_03: float = Field(ge=0, le=1)
    recall_tiou_03: float = Field(ge=0, le=1)
    f1_tiou_03: float = Field(ge=0, le=1)
    f1_tiou_05: float = Field(ge=0, le=1)
    action_type_recall: float | None = Field(default=None, ge=0, le=1)
    mean_start_error_seconds: float | None = Field(default=None, ge=0)
    mean_end_error_seconds: float | None = Field(default=None, ge=0)
    tail_recall: float | None = Field(default=None, ge=0, le=1)
    duplicate_count: int = Field(ge=0)
    fragment_count: int = Field(ge=0)
    parameter_accuracy: float = Field(ge=0, le=1)
    parameter_accuracy_by_name: dict[ParameterName, float | None]
    unsupported_parameter_rate: float = Field(ge=0, le=1)
    weight_violation_count: int = Field(ge=0)
    schema_violation_count: int = Field(ge=0)
    cleanup_violation_count: int = Field(ge=0)
    coverage_violation_count: int = Field(ge=0)
    resumed_run_count: int = Field(default=0, ge=0)
    cancelled_run_count: int = Field(default=0, ge=0)
    median_first_candidate_seconds: float | None = Field(default=None, ge=0)
    median_completed_seconds: float | None = Field(default=None, ge=0)
    completed_seconds_range: tuple[float, float] | None = None
    median_seconds_per_video_minute: float | None = Field(default=None, ge=0)
    seconds_per_video_minute_range: tuple[float, float] | None = None
    median_queue_seconds: float | None = Field(default=None, ge=0)
    median_upload_seconds: float | None = Field(default=None, ge=0)
    median_preprocessing_seconds: float | None = Field(default=None, ge=0)
    median_asr_seconds: float | None = Field(default=None, ge=0)
    median_visual_seconds: float | None = Field(default=None, ge=0)
    median_fusion_seconds: float | None = Field(default=None, ge=0)
    median_cleanup_seconds: float | None = Field(default=None, ge=0)
    median_cost_cny: float | None = Field(default=None, ge=0)
    total_cost_cny: float | None = Field(default=None, ge=0)
    total_input_tokens: int | None = Field(default=None, ge=0)
    total_output_tokens: int | None = Field(default=None, ge=0)
    stability_f1: float | None = Field(default=None, ge=0, le=1)
    source_results: list[LongSourceArmAggregate] = Field(default_factory=list)
    eligible: bool


DecisionConclusion = Literal["qwen_candidate_wins", "seed_remains_baseline", "no_valid_conclusion"]
DecisionReason = Literal[
    "only_eligible",
    "quality_gap",
    "success_rate",
    "completed_latency",
    "cost",
    "tie_or_missing_cost",
    "no_eligible_arm",
]


class LongVideoDecision(StrictModel):
    """A conservative, deterministic A/B selection result."""

    winner: LongVideoArm | None = None
    conclusion: DecisionConclusion
    reason: DecisionReason


def _safe_median(values: list[float]) -> float | None:
    return median(values) if values else None


def _f1(true_positives: int, predictions: int, expected: int) -> float:
    precision = true_positives / predictions if predictions else 0.0
    recall = true_positives / expected if expected else 0.0
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _candidate_as_gold(candidate: BenchmarkCandidate) -> GoldEvent:
    return GoldEvent(
        canonical_name=candidate.action_name,
        accepted_aliases=[candidate.action_name],
        start_seconds=candidate.start_seconds,
        end_seconds=candidate.end_seconds,
        kind=candidate.kind,
        sets=candidate.sets,
        reps=candidate.reps,
        duration_seconds=candidate.duration_seconds,
        rest_seconds=candidate.rest_seconds,
        evidence_channels=candidate.evidence_channels,
    )


def _normalized_action_name(value: str) -> str:
    return re.sub(r"[^\w]", "", value.casefold(), flags=re.UNICODE)


def _action_type_recall(
    gold_events: list[GoldEvent],
    candidates: list[BenchmarkCandidate],
) -> float | None:
    """Recall of canonical trainable action names with a valid time match."""
    action_events = [event for event in gold_events if event.kind.value == "action"]
    canonical_names = {_normalized_action_name(event.canonical_name) for event in action_events}
    if not canonical_names:
        return None
    recognized: set[str] = set()
    for event in action_events:
        accepted_names = {
            _normalized_action_name(event.canonical_name),
            *(_normalized_action_name(alias) for alias in event.accepted_aliases),
        }
        if any(
            candidate.kind == event.kind
            and _normalized_action_name(candidate.action_name) in accepted_names
            and temporal_iou(event, candidate) >= 0.5
            for candidate in candidates
        ):
            recognized.add(_normalized_action_name(event.canonical_name))
    return len(recognized) / len(canonical_names)


def _stability_f1(
    source_gold: dict[str, LongVideoSourceGold],
    completed_runs: list[LongVideoRun],
) -> float | None:
    grouped: dict[str, list[LongVideoRun]] = defaultdict(list)
    for run in completed_runs:
        grouped[run.source_id].append(run)

    values: list[float] = []
    for source_id, repetitions in grouped.items():
        if {run.run_index for run in repetitions} != {1, 2, 3}:
            continue
        for left, right in combinations(repetitions, 2):
            right_as_gold = [_candidate_as_gold(candidate) for candidate in right.candidates]
            values.append(
                score_candidates(
                    right_as_gold,
                    left.candidates,
                    sample_duration_seconds=source_gold[source_id].duration_seconds,
                ).tiou_05.f1
            )
    return sum(values) / len(values) if values else None


def _aggregate_source(
    source: LongVideoSourceGold,
    arm_runs: list[LongVideoRun],
) -> LongSourceArmAggregate:
    completed = [
        run
        for run in arm_runs
        if run.status == LongRunStatus.COMPLETED and not run.resumed_from_checkpoint
    ]
    scores = [
        score_candidates(
            source.events,
            run.candidates,
            sample_duration_seconds=source.duration_seconds,
        )
        for run in completed
    ]

    def total(metric: str, threshold: str) -> int:
        return sum(getattr(getattr(score, threshold), metric) for score in scores)

    tp_05 = total("true_positives", "tiou_05")
    predicted_05 = tp_05 + total("false_positives", "tiou_05")
    expected_05 = tp_05 + total("false_negatives", "tiou_05")
    precision = tp_05 / predicted_05 if predicted_05 else 0.0
    recall = tp_05 / expected_05 if expected_05 else 0.0
    tp_03 = total("true_positives", "tiou_03")
    predicted_03 = tp_03 + total("false_positives", "tiou_03")
    expected_03 = tp_03 + total("false_negatives", "tiou_03")
    reported_parameters = sum(score.reported_parameter_count for score in scores)
    unsupported_parameters = sum(score.unsupported_parameter_count for score in scores)
    tail_recalls = [score.tail_recall for score in scores if score.tail_recall is not None]
    action_type_recalls = [
        value
        for run in completed
        if (value := _action_type_recall(source.events, run.candidates)) is not None
    ]
    completed_seconds = [
        run.completed_seconds for run in completed if run.completed_seconds is not None
    ]
    seconds_per_video_minute = [
        elapsed / (source.duration_seconds / 60) for elapsed in completed_seconds
    ]
    schema_violations = sum(run.status == LongRunStatus.SCHEMA_ERROR for run in arm_runs)
    cleanup_violations = sum(not run.cleanup_ok for run in arm_runs)
    coverage_violations = sum(
        run.status == LongRunStatus.COMPLETED and not run.full_coverage for run in arm_runs
    )
    resumed_runs = sum(run.resumed_from_checkpoint for run in arm_runs)
    cancelled_runs = sum(run.status == LongRunStatus.CANCELLED for run in arm_runs)
    weight_violations = sum(score.weight_violation_count for score in scores) + sum(
        run.model_weight_violation_count for run in arm_runs
    )
    eligible = (
        len(completed) == 3
        and len(arm_runs) == 3
        and precision >= 0.9
        and recall >= 0.8
        and _f1(tp_05, predicted_05, expected_05) >= 0.75
        and (unsupported_parameters / reported_parameters if reported_parameters else 0.0) <= 0.02
        and weight_violations == 0
        and schema_violations == 0
        and cleanup_violations == 0
        and coverage_violations == 0
        and resumed_runs == 0
        and cancelled_runs == 0
    )
    return LongSourceArmAggregate(
        source_id=source.source_id,
        duration_seconds=source.duration_seconds,
        expected_runs=3,
        completed_runs=len(completed),
        success_rate=len(completed) / 3,
        precision=precision,
        recall=recall,
        f1_tiou_03=_f1(tp_03, predicted_03, expected_03),
        f1_tiou_05=_f1(tp_05, predicted_05, expected_05),
        action_type_recall=(
            sum(action_type_recalls) / len(action_type_recalls) if action_type_recalls else None
        ),
        tail_recall=sum(tail_recalls) / len(tail_recalls) if tail_recalls else None,
        unsupported_parameter_rate=(
            unsupported_parameters / reported_parameters if reported_parameters else 0.0
        ),
        weight_violation_count=weight_violations,
        schema_violation_count=schema_violations,
        cleanup_violation_count=cleanup_violations,
        coverage_violation_count=coverage_violations,
        resumed_run_count=resumed_runs,
        cancelled_run_count=cancelled_runs,
        median_first_candidate_seconds=_safe_median(
            [
                run.first_candidate_seconds
                for run in completed
                if run.first_candidate_seconds is not None
            ]
        ),
        median_completed_seconds=_safe_median(completed_seconds),
        median_seconds_per_video_minute=_safe_median(seconds_per_video_minute),
        seconds_per_video_minute_range=(
            (min(seconds_per_video_minute), max(seconds_per_video_minute))
            if seconds_per_video_minute
            else None
        ),
        stability_f1=_stability_f1({source.source_id: source}, completed),
        eligible=eligible,
    )


def _aggregate_arm(
    arm: LongVideoArm,
    sources: dict[str, LongVideoSourceGold],
    arm_runs: list[LongVideoRun],
) -> LongArmAggregate:
    source_results = [
        _aggregate_source(
            source,
            [run for run in arm_runs if run.source_id == source.source_id],
        )
        for source in sources.values()
    ]
    completed = [
        run
        for run in arm_runs
        if run.status == LongRunStatus.COMPLETED and not run.resumed_from_checkpoint
    ]
    scores = [
        score_candidates(
            sources[run.source_id].events,
            run.candidates,
            sample_duration_seconds=sources[run.source_id].duration_seconds,
        )
        for run in completed
    ]

    def total(metric: str, threshold: str) -> int:
        return sum(getattr(getattr(score, threshold), metric) for score in scores)

    tp_05 = total("true_positives", "tiou_05")
    predicted_05 = tp_05 + total("false_positives", "tiou_05")
    expected_05 = tp_05 + total("false_negatives", "tiou_05")
    precision = tp_05 / predicted_05 if predicted_05 else 0.0
    recall = tp_05 / expected_05 if expected_05 else 0.0
    tp_03 = total("true_positives", "tiou_03")
    predicted_03 = tp_03 + total("false_positives", "tiou_03")
    expected_03 = tp_03 + total("false_negatives", "tiou_03")
    precision_03 = tp_03 / predicted_03 if predicted_03 else 0.0
    recall_03 = tp_03 / expected_03 if expected_03 else 0.0

    reported_parameters = sum(score.reported_parameter_count for score in scores)
    unsupported_parameters = sum(score.unsupported_parameter_count for score in scores)
    supported_parameters = sum(score.supported_parameter_count for score in scores)
    correct_parameters = sum(score.correct_parameter_count for score in scores)
    parameter_accuracy_by_name: dict[ParameterName, float | None] = {}
    for name in ParameterName:
        supported = sum(score.parameter_supported_count_by_name[name] for score in scores)
        correct = sum(score.parameter_correct_count_by_name[name] for score in scores)
        parameter_accuracy_by_name[name] = correct / supported if supported else None

    start_errors = [
        score.mean_start_error_seconds
        for score in scores
        if score.mean_start_error_seconds is not None
    ]
    end_errors = [
        score.mean_end_error_seconds for score in scores if score.mean_end_error_seconds is not None
    ]
    tail_recalls = [score.tail_recall for score in scores if score.tail_recall is not None]
    action_type_recalls = [
        value
        for run in completed
        if (
            value := _action_type_recall(
                sources[run.source_id].events,
                run.candidates,
            )
        )
        is not None
    ]
    first_candidate_seconds = [
        run.first_candidate_seconds for run in completed if run.first_candidate_seconds is not None
    ]
    completed_seconds = [
        run.completed_seconds for run in completed if run.completed_seconds is not None
    ]
    seconds_per_video_minute = [
        elapsed / (sources[run.source_id].duration_seconds / 60)
        for run in completed
        if (elapsed := run.completed_seconds) is not None
    ]
    costs = [run.cost_cny for run in completed if run.cost_cny is not None]
    input_tokens = [run.input_tokens for run in completed if run.input_tokens is not None]
    output_tokens = [run.output_tokens for run in completed if run.output_tokens is not None]
    expected_runs = len(sources) * 3
    schema_violations = sum(run.status == LongRunStatus.SCHEMA_ERROR for run in arm_runs)
    cleanup_violations = sum(not run.cleanup_ok for run in arm_runs)
    coverage_violations = sum(
        run.status == LongRunStatus.COMPLETED and not run.full_coverage for run in arm_runs
    )
    resumed_runs = sum(run.resumed_from_checkpoint for run in arm_runs)
    cancelled_runs = sum(run.status == LongRunStatus.CANCELLED for run in arm_runs)
    weight_violations = sum(score.weight_violation_count for score in scores) + sum(
        run.model_weight_violation_count for run in arm_runs
    )
    eligible = (
        len(completed) == expected_runs
        and len(arm_runs) == expected_runs
        and precision >= 0.9
        and recall >= 0.8
        and _f1(tp_05, predicted_05, expected_05) >= 0.75
        and (unsupported_parameters / reported_parameters if reported_parameters else 0.0) <= 0.02
        and weight_violations == 0
        and schema_violations == 0
        and cleanup_violations == 0
        and coverage_violations == 0
        and resumed_runs == 0
        and cancelled_runs == 0
        and all(result.eligible for result in source_results)
    )

    return LongArmAggregate(
        arm=arm,
        source_count=len(sources),
        expected_runs=expected_runs,
        completed_runs=len(completed),
        success_rate=len(completed) / expected_runs if expected_runs else 0.0,
        precision=precision,
        recall=recall,
        precision_tiou_03=precision_03,
        recall_tiou_03=recall_03,
        f1_tiou_03=_f1(tp_03, predicted_03, expected_03),
        f1_tiou_05=_f1(tp_05, predicted_05, expected_05),
        action_type_recall=(
            sum(action_type_recalls) / len(action_type_recalls) if action_type_recalls else None
        ),
        mean_start_error_seconds=(sum(start_errors) / len(start_errors) if start_errors else None),
        mean_end_error_seconds=(sum(end_errors) / len(end_errors) if end_errors else None),
        tail_recall=sum(tail_recalls) / len(tail_recalls) if tail_recalls else None,
        duplicate_count=sum(score.duplicate_count for score in scores),
        fragment_count=sum(score.fragment_count for score in scores),
        parameter_accuracy=(
            correct_parameters / supported_parameters if supported_parameters else 1.0
        ),
        parameter_accuracy_by_name=parameter_accuracy_by_name,
        unsupported_parameter_rate=(
            unsupported_parameters / reported_parameters if reported_parameters else 0.0
        ),
        weight_violation_count=weight_violations,
        schema_violation_count=schema_violations,
        cleanup_violation_count=cleanup_violations,
        coverage_violation_count=coverage_violations,
        resumed_run_count=resumed_runs,
        cancelled_run_count=cancelled_runs,
        median_first_candidate_seconds=_safe_median(first_candidate_seconds),
        median_completed_seconds=_safe_median(completed_seconds),
        completed_seconds_range=(min(completed_seconds), max(completed_seconds))
        if completed_seconds
        else None,
        median_seconds_per_video_minute=_safe_median(seconds_per_video_minute),
        seconds_per_video_minute_range=(
            (min(seconds_per_video_minute), max(seconds_per_video_minute))
            if seconds_per_video_minute
            else None
        ),
        median_queue_seconds=_safe_median([run.queue_seconds for run in completed]),
        median_upload_seconds=_safe_median([run.upload_seconds for run in completed]),
        median_preprocessing_seconds=_safe_median([run.preprocessing_seconds for run in completed]),
        median_asr_seconds=_safe_median([run.asr_seconds for run in completed]),
        median_visual_seconds=_safe_median([run.visual_seconds for run in completed]),
        median_fusion_seconds=_safe_median([run.fusion_seconds for run in completed]),
        median_cleanup_seconds=_safe_median([run.cleanup_seconds for run in completed]),
        median_cost_cny=_safe_median(costs),
        total_cost_cny=sum(costs) if len(costs) == len(completed) else None,
        total_input_tokens=(sum(input_tokens) if len(input_tokens) == len(completed) else None),
        total_output_tokens=(sum(output_tokens) if len(output_tokens) == len(completed) else None),
        stability_f1=_stability_f1(sources, completed),
        source_results=source_results,
        eligible=eligible,
    )


def score_long_video_runs(
    sources: list[LongVideoSourceGold],
    runs: list[LongVideoRun],
) -> list[LongArmAggregate]:
    """Score a complete, path-free A/B experiment.

    Every source/arm/repetition tuple is unique. Missing repetitions are kept in
    the aggregate as an ineligible arm rather than silently dropped.
    """

    source_by_id = {source.source_id: source for source in sources}
    if len(source_by_id) != len(sources):
        raise ValueError("long-video source IDs must be unique")
    seen: set[tuple[str, LongVideoArm, int]] = set()
    by_arm: dict[LongVideoArm, list[LongVideoRun]] = defaultdict(list)
    for run in runs:
        if run.source_id not in source_by_id:
            raise ValueError("long-video run has an unknown source")
        key = (run.source_id, run.arm, run.run_index)
        if key in seen:
            raise ValueError("long-video run repetition must be unique")
        seen.add(key)
        by_arm[run.arm].append(run)
    return [_aggregate_arm(arm, source_by_id, by_arm[arm]) for arm in LongVideoArm]


def choose_long_video_arm(aggregates: list[LongArmAggregate]) -> LongVideoDecision:
    """Apply the frozen quality-first, then success/latency/cost rule."""

    eligible = [aggregate for aggregate in aggregates if aggregate.eligible]
    if not eligible:
        return LongVideoDecision(conclusion="no_valid_conclusion", reason="no_eligible_arm")
    if len(eligible) == 1:
        return _winner(eligible[0].arm, "only_eligible")

    baseline = next(
        (aggregate for aggregate in eligible if aggregate.arm == LongVideoArm.SEED_CONTACT_SHEET),
        None,
    )
    candidate = next(
        (aggregate for aggregate in eligible if aggregate.arm == LongVideoArm.QWEN_VIDEO),
        None,
    )
    if baseline is None or candidate is None:
        return LongVideoDecision(conclusion="no_valid_conclusion", reason="no_eligible_arm")

    quality_delta = candidate.f1_tiou_05 - baseline.f1_tiou_05
    if abs(quality_delta) > 0.03:
        return _winner(
            candidate.arm if quality_delta > 0 else baseline.arm,
            "quality_gap",
        )
    if candidate.success_rate != baseline.success_rate:
        return _winner(
            candidate.arm if candidate.success_rate > baseline.success_rate else baseline.arm,
            "success_rate",
        )
    if (
        candidate.median_completed_seconds is not None
        and baseline.median_completed_seconds is not None
        and candidate.median_completed_seconds != baseline.median_completed_seconds
    ):
        return _winner(
            candidate.arm
            if candidate.median_completed_seconds < baseline.median_completed_seconds
            else baseline.arm,
            "completed_latency",
        )
    if (
        candidate.median_cost_cny is None
        or baseline.median_cost_cny is None
        or candidate.total_cost_cny is None
        or baseline.total_cost_cny is None
    ):
        return LongVideoDecision(conclusion="no_valid_conclusion", reason="tie_or_missing_cost")
    if candidate.median_cost_cny != baseline.median_cost_cny:
        return _winner(
            candidate.arm if candidate.median_cost_cny < baseline.median_cost_cny else baseline.arm,
            "cost",
        )
    return LongVideoDecision(conclusion="no_valid_conclusion", reason="tie_or_missing_cost")


def _winner(arm: LongVideoArm, reason: DecisionReason) -> LongVideoDecision:
    return LongVideoDecision(
        winner=arm,
        conclusion=(
            "seed_remains_baseline"
            if arm == LongVideoArm.SEED_CONTACT_SHEET
            else "qwen_candidate_wins"
        ),
        reason=reason,
    )
