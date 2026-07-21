import re
from dataclasses import dataclass
from functools import cache
from statistics import mean

from hakimi_analysis.benchmark.models import (
    BenchmarkCandidate,
    CandidateScore,
    GoldEvent,
    MatchCounts,
    ParameterName,
)

_PARAMETER_FIELDS = ("sets", "reps", "duration_seconds", "rest_seconds", "weight_kg")


def _normalize_name(value: str) -> str:
    return re.sub(r"[^\w]", "", value.casefold(), flags=re.UNICODE)


def _names_match(gold: GoldEvent, prediction: BenchmarkCandidate) -> bool:
    accepted = {_normalize_name(gold.canonical_name)} | {
        _normalize_name(alias) for alias in gold.accepted_aliases
    }
    return _normalize_name(prediction.action_name) in accepted


def temporal_iou(gold: GoldEvent, prediction: BenchmarkCandidate) -> float:
    intersection = max(
        0.0,
        min(gold.end_seconds, prediction.end_seconds)
        - max(gold.start_seconds, prediction.start_seconds),
    )
    union = max(gold.end_seconds, prediction.end_seconds) - min(
        gold.start_seconds, prediction.start_seconds
    )
    return intersection / union if union else 0.0


@dataclass(frozen=True, slots=True)
class _Matches:
    pairs: tuple[tuple[int, int], ...]
    unmatched_gold: tuple[int, ...]
    unmatched_predictions: tuple[int, ...]


def _match(
    gold: list[GoldEvent],
    predictions: list[BenchmarkCandidate],
    threshold: float,
) -> _Matches:
    edges = {
        (gold_index, prediction_index): temporal_iou(gold_event, prediction)
        for gold_index, gold_event in enumerate(gold)
        for prediction_index, prediction in enumerate(predictions)
        if gold_event.kind == prediction.kind
        and _names_match(gold_event, prediction)
        and temporal_iou(gold_event, prediction) >= threshold
    }

    @cache
    def solve(
        gold_index: int,
        used_predictions: int,
    ) -> tuple[int, float, tuple[tuple[int, int], ...]]:
        if gold_index == len(gold):
            return (0, 0.0, ())
        best = solve(gold_index + 1, used_predictions)
        for prediction_index in range(len(predictions)):
            overlap = edges.get((gold_index, prediction_index))
            if overlap is None or used_predictions & (1 << prediction_index):
                continue
            count, total, pairs = solve(
                gold_index + 1,
                used_predictions | (1 << prediction_index),
            )
            candidate = (count + 1, total + overlap, ((gold_index, prediction_index), *pairs))
            if candidate[:2] > best[:2] or (
                candidate[:2] == best[:2] and candidate[2] < best[2]
            ):
                best = candidate
        return best

    _, _, selected_pairs = solve(0, 0)
    pairs = list(selected_pairs)
    matched_gold = {gold_index for gold_index, _ in pairs}
    matched_predictions = {prediction_index for _, prediction_index in pairs}
    return _Matches(
        pairs=tuple(pairs),
        unmatched_gold=tuple(index for index in range(len(gold)) if index not in matched_gold),
        unmatched_predictions=tuple(
            index for index in range(len(predictions)) if index not in matched_predictions
        ),
    )


def _counts(matches: _Matches, gold_count: int, prediction_count: int) -> MatchCounts:
    true_positives = len(matches.pairs)
    false_positives = prediction_count - true_positives
    false_negatives = gold_count - true_positives
    precision = true_positives / prediction_count if prediction_count else 0.0
    recall = true_positives / gold_count if gold_count else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return MatchCounts(
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        precision=precision,
        recall=recall,
        f1=f1,
    )


def score_candidates(
    gold: list[GoldEvent],
    predictions: list[BenchmarkCandidate],
    *,
    sample_duration_seconds: float,
) -> CandidateScore:
    matches_03 = _match(gold, predictions, 0.3)
    matches_05 = _match(gold, predictions, 0.5)
    start_errors = [
        abs(gold[gold_index].start_seconds - predictions[prediction_index].start_seconds)
        for gold_index, prediction_index in matches_05.pairs
    ]
    end_errors = [
        abs(gold[gold_index].end_seconds - predictions[prediction_index].end_seconds)
        for gold_index, prediction_index in matches_05.pairs
    ]

    tail_gold = {
        index
        for index, event in enumerate(gold)
        if event.end_seconds > sample_duration_seconds * 0.9
    }
    matched_tail = {gold_index for gold_index, _ in matches_05.pairs} & tail_gold
    tail_recall = len(matched_tail) / len(tail_gold) if tail_gold else None

    duplicate_count = 0
    for prediction_index in matches_05.unmatched_predictions:
        prediction = predictions[prediction_index]
        if any(
            _names_match(gold[gold_index], prediction)
            and temporal_iou(gold[gold_index], prediction) >= 0.3
            for gold_index, _ in matches_05.pairs
        ):
            duplicate_count += 1

    fragment_count = sum(
        1
        for gold_index in matches_05.unmatched_gold
        if sum(
            1
            for prediction in predictions
            if _names_match(gold[gold_index], prediction)
            and temporal_iou(gold[gold_index], prediction) > 0
        )
        >= 2
    )

    reported = supported = correct = unsupported = weight_violations = 0
    supported_by_name = {name: 0 for name in ParameterName}
    correct_by_name = {name: 0 for name in ParameterName}
    matched_prediction_indexes = {
        prediction_index for _, prediction_index in matches_03.pairs
    }
    for gold_index, prediction_index in matches_03.pairs:
        gold_event = gold[gold_index]
        prediction = predictions[prediction_index]
        for field in _PARAMETER_FIELDS:
            predicted_value = getattr(prediction, field)
            gold_value = getattr(gold_event, field)
            if predicted_value is None:
                continue
            reported += 1
            if field == "weight_kg":
                weight_violations += 1
            if field != "weight_kg" and field in {
                parameter.value for parameter in gold_event.uncertain_parameters
            }:
                reported -= 1
                continue
            if gold_value is None:
                unsupported += 1
            else:
                supported += 1
                correct += int(predicted_value == gold_value)
                parameter_name = ParameterName(field)
                supported_by_name[parameter_name] += 1
                correct_by_name[parameter_name] += int(predicted_value == gold_value)

    for prediction_index, prediction in enumerate(predictions):
        if prediction_index in matched_prediction_indexes:
            continue
        for field in _PARAMETER_FIELDS:
            if getattr(prediction, field) is None:
                continue
            reported += 1
            unsupported += 1
            if field == "weight_kg":
                weight_violations += 1

    return CandidateScore(
        tiou_03=_counts(matches_03, len(gold), len(predictions)),
        tiou_05=_counts(matches_05, len(gold), len(predictions)),
        mean_start_error_seconds=mean(start_errors) if start_errors else None,
        mean_end_error_seconds=mean(end_errors) if end_errors else None,
        tail_recall=tail_recall,
        duplicate_count=duplicate_count,
        fragment_count=fragment_count,
        reported_parameter_count=reported,
        supported_parameter_count=supported,
        correct_parameter_count=correct,
        parameter_accuracy=correct / supported if supported else 1.0,
        parameter_supported_count_by_name=supported_by_name,
        parameter_correct_count_by_name=correct_by_name,
        unsupported_parameter_count=unsupported,
        unsupported_parameter_rate=unsupported / reported if reported else 0.0,
        weight_violation_count=weight_violations,
    )
