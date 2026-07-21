import pytest

from hakimi_analysis.benchmark.models import (
    BenchmarkCandidate,
    EventKind,
    EvidenceChannel,
    GoldEvent,
)
from hakimi_analysis.benchmark.scoring import score_candidates


def gold_event(
    name: str,
    start: float,
    end: float,
    *,
    reps: int | None = None,
) -> GoldEvent:
    return GoldEvent(
        canonical_name=name,
        accepted_aliases=[name],
        start_seconds=start,
        end_seconds=end,
        kind=EventKind.ACTION,
        reps=reps,
        evidence_channels=[EvidenceChannel.VISUAL],
    )


def candidate(
    name: str,
    start: float,
    end: float,
    *,
    reps: int | None = None,
    sets: int | None = None,
    weight_kg: float | None = None,
) -> BenchmarkCandidate:
    return BenchmarkCandidate(
        action_name=name,
        start_seconds=start,
        end_seconds=end,
        kind=EventKind.ACTION,
        reps=reps,
        sets=sets,
        weight_kg=weight_kg,
        evidence_channels=[EvidenceChannel.VISUAL],
    )


def test_scoring_uses_semantic_one_to_one_tiou_matching() -> None:
    gold = [gold_event("拖拽弯举", 10, 20), gold_event("锤式弯举", 45, 55)]
    predictions = [
        candidate("拖拽弯举", 11, 20),
        candidate("拖拽弯举", 12, 19),
        candidate("锤式弯举", 46, 54),
    ]

    score = score_candidates(gold, predictions, sample_duration_seconds=60)

    assert score.tiou_05.true_positives == 2
    assert score.tiou_05.false_positives == 1
    assert score.tiou_05.false_negatives == 0
    assert score.tiou_05.precision == pytest.approx(2 / 3)
    assert score.tiou_05.recall == 1
    assert score.duplicate_count == 1
    assert score.tail_recall == 1


def test_scoring_counts_unsupported_parameters_and_weight_violations() -> None:
    gold = [gold_event("平板支撑", 0, 30)]
    predictions = [candidate("平板支撑", 0, 30, reps=10, sets=3, weight_kg=8)]

    score = score_candidates(gold, predictions, sample_duration_seconds=60)

    assert score.reported_parameter_count == 3
    assert score.unsupported_parameter_count == 3
    assert score.unsupported_parameter_rate == 1
    assert score.weight_violation_count == 1


def test_scoring_parameter_accuracy_ignores_gold_nulls() -> None:
    gold = [gold_event("深蹲", 0, 20, reps=12)]
    predictions = [candidate("深蹲", 0, 20, reps=12)]

    score = score_candidates(gold, predictions, sample_duration_seconds=60)

    assert score.supported_parameter_count == 1
    assert score.correct_parameter_count == 1
    assert score.parameter_accuracy == 1


def test_scoring_maximizes_cardinality_before_total_tiou() -> None:
    gold = [gold_event("弯举", 0, 10), gold_event("弯举", 5, 15)]
    predictions = [candidate("弯举", 0, 12), candidate("弯举", 0, 7)]

    score = score_candidates(gold, predictions, sample_duration_seconds=15)

    assert score.tiou_03.true_positives == 2
