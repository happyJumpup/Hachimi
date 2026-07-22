import pytest

from hakimi_analysis.benchmark.long_scoring import (
    LongArmAggregate,
    LongRunStatus,
    LongVideoArm,
    LongVideoRun,
    LongVideoSourceGold,
    choose_long_video_arm,
    score_long_video_runs,
)
from hakimi_analysis.benchmark.models import (
    BenchmarkCandidate,
    BenchmarkRunStatus,
    EventKind,
    EvidenceChannel,
    GoldEvent,
)


def _gold(name: str, start: float, end: float) -> GoldEvent:
    return GoldEvent(
        canonical_name=name,
        accepted_aliases=[name],
        start_seconds=start,
        end_seconds=end,
        kind=EventKind.ACTION,
        evidence_channels=[EvidenceChannel.VISUAL],
    )


def _candidate(name: str, start: float, end: float) -> BenchmarkCandidate:
    return BenchmarkCandidate(
        action_name=name,
        start_seconds=start,
        end_seconds=end,
        kind=EventKind.ACTION,
        evidence_channels=[EvidenceChannel.VISUAL],
    )


def _run(
    source_id: str,
    arm: LongVideoArm,
    run_index: int,
    candidates: list[BenchmarkCandidate],
    *,
    model_weight_violation_count: int = 0,
) -> LongVideoRun:
    return LongVideoRun(
        source_id=source_id,
        arm=arm,
        run_index=run_index,
        status=BenchmarkRunStatus.COMPLETED,
        candidates=candidates,
        full_coverage=True,
        cleanup_ok=True,
        model_weight_violation_count=model_weight_violation_count,
        completed_seconds=42,
        cost_cny=0.2,
    )


def test_long_video_source_gold_requires_an_explicit_review_lock() -> None:
    with pytest.raises(ValueError, match="reviewed gold"):
        LongVideoSourceGold(
            source_id="unreviewed-source",
            duration_seconds=420,
            events=[_gold("Romanian deadlift", 120, 150)],
        )


def test_score_long_video_runs_aggregates_three_repetitions_per_source_and_arm() -> None:
    source = LongVideoSourceGold(
        source_id="seven-minute-rdl",
        duration_seconds=420,
        reviewed=True,
        events=[_gold("Romanian deadlift", 120, 150)],
    )
    runs = [
        _run(
            "seven-minute-rdl",
            LongVideoArm.SEED_CONTACT_SHEET,
            run_index,
            [_candidate("Romanian deadlift", 120, 150)],
        )
        for run_index in range(1, 4)
    ]

    aggregate = score_long_video_runs([source], runs)[0]

    assert aggregate.arm is LongVideoArm.SEED_CONTACT_SHEET
    assert aggregate.expected_runs == 3
    assert aggregate.completed_runs == 3
    assert aggregate.precision == 1
    assert aggregate.recall == 1
    assert aggregate.f1_tiou_05 == 1
    assert aggregate.stability_f1 == 1
    assert aggregate.eligible is True


def test_score_long_video_runs_keeps_tiou_parameter_and_safety_metrics() -> None:
    source = LongVideoSourceGold(
        source_id="nineteen-minute-full-workout",
        duration_seconds=1_140,
        reviewed=True,
        events=[_gold("Bench press", 1_026, 1_036)],
    )
    borderline = BenchmarkCandidate(
        action_name="Bench press",
        start_seconds=1_031,
        end_seconds=1_041,
        kind=EventKind.ACTION,
        reps=10,
        weight_kg=10,
        evidence_channels=[EvidenceChannel.VISUAL],
    )
    runs = [
        _run(
            "nineteen-minute-full-workout",
            LongVideoArm.SEED_CONTACT_SHEET,
            run_index,
            [borderline],
        )
        for run_index in range(1, 4)
    ]

    aggregate = score_long_video_runs([source], runs)[0]

    assert aggregate.f1_tiou_03 == 1
    assert aggregate.f1_tiou_05 == 0
    assert aggregate.tail_recall == 0
    assert aggregate.unsupported_parameter_rate == 1
    assert aggregate.weight_violation_count == 3
    assert aggregate.eligible is False


def test_score_long_video_runs_keeps_scrubbed_model_weight_violations_as_a_gate() -> None:
    source = LongVideoSourceGold(
        source_id="seven-minute-model-weight",
        duration_seconds=420,
        reviewed=True,
        events=[_gold("Romanian deadlift", 120, 150)],
    )
    runs = [
        _run(
            "seven-minute-model-weight",
            LongVideoArm.SEED_CONTACT_SHEET,
            run_index,
            [_candidate("Romanian deadlift", 120, 150)],
            model_weight_violation_count=1,
        )
        for run_index in range(1, 4)
    ]

    aggregate = score_long_video_runs([source], runs)[0]

    assert aggregate.weight_violation_count == 3
    assert aggregate.eligible is False


def test_score_long_video_runs_reports_canonical_action_type_recall() -> None:
    source = LongVideoSourceGold(
        source_id="nineteen-minute-action-types",
        duration_seconds=1_140,
        reviewed=True,
        events=[
            _gold("Bench press", 240, 270),
            _gold("Incline bench press", 300, 330),
        ],
    )
    runs = [
        _run(
            "nineteen-minute-action-types",
            LongVideoArm.SEED_CONTACT_SHEET,
            run_index,
            [_candidate("Bench press", 240, 270)],
        )
        for run_index in range(1, 4)
    ]

    aggregate = score_long_video_runs([source], runs)[0]

    assert aggregate.action_type_recall == 0.5
    assert aggregate.source_results[0].action_type_recall == 0.5


def test_score_long_video_runs_aggregates_boundary_duplicate_and_fragment_metrics() -> None:
    source = LongVideoSourceGold(
        source_id="seven-minute-rdl-detail",
        duration_seconds=420,
        reviewed=True,
        events=[
            _gold("Romanian deadlift", 0, 10),
            _gold("Romanian deadlift", 20, 30),
            _gold("Romanian deadlift", 40, 50),
        ],
    )
    candidates = [
        _candidate("Romanian deadlift", 1, 11),
        _candidate("Romanian deadlift", 20, 30),
        _candidate("Romanian deadlift", 21, 29),
        _candidate("Romanian deadlift", 40, 44),
        _candidate("Romanian deadlift", 46, 50),
    ]
    runs = [
        _run(
            "seven-minute-rdl-detail",
            LongVideoArm.SEED_CONTACT_SHEET,
            run_index,
            candidates,
        )
        for run_index in range(1, 4)
    ]

    aggregate = score_long_video_runs([source], runs)[0]

    assert aggregate.mean_start_error_seconds == pytest.approx(0.5)
    assert aggregate.mean_end_error_seconds == pytest.approx(0.5)
    assert aggregate.duplicate_count == 3
    assert aggregate.fragment_count == 3


def test_score_long_video_runs_marks_a_missing_chunk_coverage_as_ineligible() -> None:
    source = LongVideoSourceGold(
        source_id="seven-minute-rdl-coverage",
        duration_seconds=420,
        reviewed=True,
        events=[_gold("Romanian deadlift", 120, 150)],
    )
    runs = [
        _run(
            "seven-minute-rdl-coverage",
            LongVideoArm.SEED_CONTACT_SHEET,
            run_index,
            [_candidate("Romanian deadlift", 120, 150)],
        )
        for run_index in range(1, 4)
    ]
    runs[1] = runs[1].model_copy(update={"full_coverage": False})

    aggregate = score_long_video_runs([source], runs)[0]

    assert aggregate.coverage_violation_count == 1
    assert aggregate.eligible is False


def test_score_long_video_runs_excludes_resumed_checkpoints_from_a_valid_repetition_set() -> None:
    source = LongVideoSourceGold(
        source_id="seven-minute-rdl-resume",
        duration_seconds=420,
        reviewed=True,
        events=[_gold("Romanian deadlift", 120, 150)],
    )
    runs = [
        _run(
            "seven-minute-rdl-resume",
            LongVideoArm.SEED_CONTACT_SHEET,
            run_index,
            [_candidate("Romanian deadlift", 120, 150)],
        )
        for run_index in range(1, 4)
    ]
    runs[1] = runs[1].model_copy(update={"resumed_from_checkpoint": True})

    aggregate = score_long_video_runs([source], runs)[0]

    assert aggregate.resumed_run_count == 1
    assert aggregate.completed_runs == 2
    assert aggregate.eligible is False


def test_score_long_video_runs_records_schema_and_cleanup_violations() -> None:
    source = LongVideoSourceGold(
        source_id="nineteen-minute-schema-cleanup",
        duration_seconds=1_140,
        reviewed=True,
        events=[_gold("Bench press", 240, 270)],
    )
    completed_runs = [
        _run(
            "nineteen-minute-schema-cleanup",
            LongVideoArm.QWEN_VIDEO,
            run_index,
            [_candidate("Bench press", 240, 270)],
        )
        for run_index in (1, 2)
    ]
    failed_run = LongVideoRun(
        source_id="nineteen-minute-schema-cleanup",
        arm=LongVideoArm.QWEN_VIDEO,
        run_index=3,
        status=BenchmarkRunStatus.SCHEMA_ERROR,
        cleanup_ok=False,
    )

    aggregate = score_long_video_runs([source], [*completed_runs, failed_run])[1]

    assert aggregate.completed_runs == 2
    assert aggregate.schema_violation_count == 1
    assert aggregate.cleanup_violation_count == 1
    assert aggregate.eligible is False


def test_score_long_video_runs_keeps_each_source_gate_and_normalized_latency() -> None:
    sources = [
        LongVideoSourceGold(
            source_id="seven-minute-latency",
            duration_seconds=420,
            reviewed=True,
            events=[_gold("Romanian deadlift", 120, 150)],
        ),
        LongVideoSourceGold(
            source_id="nineteen-minute-latency",
            duration_seconds=1_140,
            reviewed=True,
            events=[_gold("Bench press", 240, 270)],
        ),
    ]
    runs = [
        _run(
            "seven-minute-latency",
            LongVideoArm.SEED_CONTACT_SHEET,
            run_index,
            [_candidate("Romanian deadlift", 120, 150)],
        ).model_copy(update={"completed_seconds": 70})
        for run_index in range(1, 4)
    ] + [
        _run(
            "nineteen-minute-latency",
            LongVideoArm.SEED_CONTACT_SHEET,
            run_index,
            [_candidate("Bench press", 240, 270)],
        ).model_copy(update={"completed_seconds": 190})
        for run_index in range(1, 4)
    ]

    aggregate = score_long_video_runs(sources, runs)[0]
    by_source = {result.source_id: result for result in aggregate.source_results}

    assert by_source["seven-minute-latency"].median_seconds_per_video_minute == 10
    assert by_source["nineteen-minute-latency"].median_seconds_per_video_minute == 10
    assert aggregate.median_seconds_per_video_minute == 10
    assert aggregate.eligible is True


def test_score_long_video_runs_aggregates_tokens_only_when_every_completed_run_reports_them() -> (
    None
):
    source = LongVideoSourceGold(
        source_id="seven-minute-tokens",
        duration_seconds=420,
        reviewed=True,
        events=[_gold("Romanian deadlift", 120, 150)],
    )
    complete = [
        _run(
            "seven-minute-tokens",
            LongVideoArm.SEED_CONTACT_SHEET,
            run_index,
            [_candidate("Romanian deadlift", 120, 150)],
        ).model_copy(update={"input_tokens": 10, "output_tokens": 5})
        for run_index in range(1, 4)
    ]

    aggregate = score_long_video_runs([source], complete)[0]

    assert aggregate.total_input_tokens == 30
    assert aggregate.total_output_tokens == 15
    incomplete = complete.copy()
    incomplete[1] = incomplete[1].model_copy(update={"input_tokens": None})
    assert score_long_video_runs([source], incomplete)[0].total_input_tokens is None


def test_score_long_video_runs_counts_cancelled_runs_as_an_explicit_gate_failure() -> None:
    source = LongVideoSourceGold(
        source_id="seven-minute-cancelled",
        duration_seconds=420,
        reviewed=True,
        events=[_gold("Romanian deadlift", 120, 150)],
    )
    completed_runs = [
        _run(
            "seven-minute-cancelled",
            LongVideoArm.QWEN_VIDEO,
            run_index,
            [_candidate("Romanian deadlift", 120, 150)],
        )
        for run_index in (1, 2)
    ]
    cancelled_run = LongVideoRun(
        source_id="seven-minute-cancelled",
        arm=LongVideoArm.QWEN_VIDEO,
        run_index=3,
        status=LongRunStatus.CANCELLED,
        cleanup_ok=True,
    )

    aggregate = score_long_video_runs([source], [*completed_runs, cancelled_run])[1]

    assert aggregate.cancelled_run_count == 1
    assert aggregate.eligible is False


def _eligible_aggregate(
    arm: LongVideoArm,
    **updates: object,
) -> LongArmAggregate:
    aggregate = LongArmAggregate(
        arm=arm,
        source_count=2,
        expected_runs=6,
        completed_runs=6,
        success_rate=1,
        precision=0.95,
        recall=0.85,
        precision_tiou_03=0.95,
        recall_tiou_03=0.85,
        f1_tiou_03=0.9,
        f1_tiou_05=0.8,
        duplicate_count=0,
        fragment_count=0,
        parameter_accuracy=1,
        parameter_accuracy_by_name={
            "sets": None,
            "reps": None,
            "duration_seconds": None,
            "rest_seconds": None,
        },
        unsupported_parameter_rate=0,
        weight_violation_count=0,
        schema_violation_count=0,
        cleanup_violation_count=0,
        coverage_violation_count=0,
        median_completed_seconds=120,
        median_cost_cny=1,
        total_cost_cny=6,
        eligible=True,
    )
    return aggregate.model_copy(update=updates)


def test_choose_long_video_arm_prioritizes_a_quality_gap_above_three_points() -> None:
    decision = choose_long_video_arm(
        [
            _eligible_aggregate(LongVideoArm.SEED_CONTACT_SHEET, f1_tiou_05=0.8),
            _eligible_aggregate(LongVideoArm.QWEN_VIDEO, f1_tiou_05=0.84),
        ]
    )

    assert decision.winner is LongVideoArm.QWEN_VIDEO
    assert decision.conclusion == "qwen_candidate_wins"
    assert decision.reason == "quality_gap"


def test_choose_long_video_arm_uses_latency_after_a_within_three_point_quality_tie() -> None:
    decision = choose_long_video_arm(
        [
            _eligible_aggregate(
                LongVideoArm.SEED_CONTACT_SHEET,
                f1_tiou_05=0.8,
                median_completed_seconds=100,
            ),
            _eligible_aggregate(
                LongVideoArm.QWEN_VIDEO,
                f1_tiou_05=0.83,
                median_completed_seconds=90,
            ),
        ]
    )

    assert decision.winner is LongVideoArm.QWEN_VIDEO
    assert decision.reason == "completed_latency"


def test_choose_long_video_arm_uses_success_rate_before_latency_and_cost_last() -> None:
    success_decision = choose_long_video_arm(
        [
            _eligible_aggregate(
                LongVideoArm.SEED_CONTACT_SHEET,
                success_rate=0.8,
                median_completed_seconds=80,
            ),
            _eligible_aggregate(
                LongVideoArm.QWEN_VIDEO,
                success_rate=1,
                median_completed_seconds=200,
            ),
        ]
    )
    cost_decision = choose_long_video_arm(
        [
            _eligible_aggregate(
                LongVideoArm.SEED_CONTACT_SHEET,
                median_completed_seconds=100,
                median_cost_cny=1,
            ),
            _eligible_aggregate(
                LongVideoArm.QWEN_VIDEO,
                median_completed_seconds=100,
                median_cost_cny=0.5,
            ),
        ]
    )

    assert success_decision.winner is LongVideoArm.QWEN_VIDEO
    assert success_decision.reason == "success_rate"
    assert cost_decision.winner is LongVideoArm.QWEN_VIDEO
    assert cost_decision.reason == "cost"


def test_choose_long_video_arm_refuses_a_winner_without_an_eligible_arm() -> None:
    decision = choose_long_video_arm(
        [_eligible_aggregate(LongVideoArm.SEED_CONTACT_SHEET, eligible=False)]
    )

    assert decision.winner is None
    assert decision.conclusion == "no_valid_conclusion"
    assert decision.reason == "no_eligible_arm"


def test_choose_long_video_arm_refuses_a_cost_tie_break_without_complete_cost_data() -> None:
    decision = choose_long_video_arm(
        [
            _eligible_aggregate(
                LongVideoArm.SEED_CONTACT_SHEET,
                median_completed_seconds=100,
                median_cost_cny=1,
                total_cost_cny=None,
            ),
            _eligible_aggregate(
                LongVideoArm.QWEN_VIDEO,
                median_completed_seconds=100,
                median_cost_cny=0.5,
                total_cost_cny=None,
            ),
        ]
    )

    assert decision.winner is None
    assert decision.reason == "tie_or_missing_cost"
