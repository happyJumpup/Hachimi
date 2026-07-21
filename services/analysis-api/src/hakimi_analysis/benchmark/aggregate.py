from collections import defaultdict
from itertools import combinations
from statistics import median

from hakimi_analysis.benchmark.manifest import BenchmarkManifest
from hakimi_analysis.benchmark.models import (
    BenchmarkRunResult,
    BenchmarkRunStatus,
    GoldAnnotation,
    GoldEvent,
    GoldStatus,
    ParameterName,
    RouteAggregate,
    RouteId,
)
from hakimi_analysis.benchmark.scoring import score_candidates


def aggregate_results(
    manifest: BenchmarkManifest,
    results: list[BenchmarkRunResult],
) -> list[RouteAggregate]:
    samples = {sample.sample_id: sample for sample in manifest.samples}
    gold = {}
    for sample in manifest.samples:
        annotation = GoldAnnotation.model_validate_json(
            sample.gold_path.read_text(encoding="utf-8")
        )
        if (
            annotation.status != GoldStatus.REVIEWED
            or sample.gold_status != annotation.status
        ):
            raise ValueError("aggregate scoring requires human-reviewed gold")
        if annotation.sample_id != sample.sample_id:
            raise ValueError("gold sample identity does not match manifest")
        if any(event.end_seconds > sample.duration_seconds for event in annotation.events):
            raise ValueError("gold event falls outside the prepared sample")
        gold[sample.sample_id] = annotation.events

    grouped: dict[RouteId, list[BenchmarkRunResult]] = defaultdict(list)
    for result in results:
        grouped[result.route_id].append(result)

    aggregates = []
    for route_id in RouteId:
        all_route_results = grouped[route_id]
        route_results = [
            result
            for result in all_route_results
            if result.status == BenchmarkRunStatus.COMPLETED
        ]
        scores = [
            score_candidates(
                gold[result.sample_id],
                result.candidates,
                sample_duration_seconds=samples[result.sample_id].duration_seconds,
            )
            for result in route_results
        ]
        true_positives = sum(score.tiou_05.true_positives for score in scores)
        predicted = sum(
            score.tiou_05.true_positives + score.tiou_05.false_positives
            for score in scores
        )
        expected = sum(
            score.tiou_05.true_positives + score.tiou_05.false_negatives
            for score in scores
        )
        precision = true_positives / predicted if predicted else 0.0
        recall = true_positives / expected if expected else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        true_positives_03 = sum(score.tiou_03.true_positives for score in scores)
        predicted_03 = sum(
            score.tiou_03.true_positives + score.tiou_03.false_positives
            for score in scores
        )
        expected_03 = sum(
            score.tiou_03.true_positives + score.tiou_03.false_negatives
            for score in scores
        )
        precision_03 = true_positives_03 / predicted_03 if predicted_03 else 0.0
        recall_03 = true_positives_03 / expected_03 if expected_03 else 0.0
        f1_03 = (
            2 * precision_03 * recall_03 / (precision_03 + recall_03)
            if precision_03 + recall_03
            else 0.0
        )
        reported = sum(score.reported_parameter_count for score in scores)
        unsupported = sum(score.unsupported_parameter_count for score in scores)
        latencies = [result.inference_seconds for result in route_results] or [0.0]
        weight_violations = sum(score.weight_violation_count for score in scores)
        unsupported_rate = unsupported / reported if reported else 0.0
        correct_parameters = sum(score.correct_parameter_count for score in scores)
        supported_parameters = sum(score.supported_parameter_count for score in scores)
        parameter_accuracy = (
            correct_parameters / supported_parameters if supported_parameters else 1.0
        )
        parameter_accuracy_by_name = {}
        for name in ParameterName:
            supported_for_name = sum(
                score.parameter_supported_count_by_name[name] for score in scores
            )
            correct_for_name = sum(
                score.parameter_correct_count_by_name[name] for score in scores
            )
            parameter_accuracy_by_name[name] = (
                correct_for_name / supported_for_name if supported_for_name else None
            )
        start_errors = [
            score.mean_start_error_seconds
            for score in scores
            if score.mean_start_error_seconds is not None
        ]
        end_errors = [
            score.mean_end_error_seconds
            for score in scores
            if score.mean_end_error_seconds is not None
        ]
        tail_recalls = [
            score.tail_recall for score in scores if score.tail_recall is not None
        ]
        stability_values = []
        for sample_id in samples:
            repetitions = [
                result for result in route_results if result.sample_id == sample_id
            ]
            if len(repetitions) != 3:
                continue
            for left, right in combinations(repetitions, 2):
                right_as_gold = [_candidate_as_gold(candidate) for candidate in right.candidates]
                stability_values.append(
                    score_candidates(
                        right_as_gold,
                        left.candidates,
                        sample_duration_seconds=samples[sample_id].duration_seconds,
                    ).tiou_05.f1
                )
        action_counts = [len(result.candidates) for result in route_results]
        f1_values = [score.tiou_05.f1 for score in scores]
        input_tokens = [
            result.input_tokens
            for result in route_results
            if result.input_tokens is not None
        ]
        output_tokens = [
            result.output_tokens
            for result in route_results
            if result.output_tokens is not None
        ]
        eligible = (
            len(route_results) == 9
            and precision >= 0.9
            and recall >= 0.8
            and f1 >= 0.75
            and unsupported_rate <= 0.02
            and weight_violations == 0
        )
        aggregates.append(
            RouteAggregate(
                route_id=route_id,
                scored_units=len(route_results),
                precision=precision,
                recall=recall,
                precision_tiou_03=precision_03,
                recall_tiou_03=recall_03,
                f1_tiou_03=f1_03,
                f1_tiou_05=f1,
                mean_start_error_seconds=(
                    sum(start_errors) / len(start_errors) if start_errors else None
                ),
                mean_end_error_seconds=(
                    sum(end_errors) / len(end_errors) if end_errors else None
                ),
                tail_recall=(
                    sum(tail_recalls) / len(tail_recalls) if tail_recalls else None
                ),
                duplicate_count=sum(score.duplicate_count for score in scores),
                fragment_count=sum(score.fragment_count for score in scores),
                parameter_accuracy=parameter_accuracy,
                parameter_accuracy_by_name=parameter_accuracy_by_name,
                median_inference_seconds=median(latencies),
                inference_seconds_range=(min(latencies), max(latencies)),
                unsupported_parameter_rate=unsupported_rate,
                weight_violation_count=weight_violations,
                schema_violation_count=sum(
                    result.status == BenchmarkRunStatus.SCHEMA_ERROR
                    for result in all_route_results
                ),
                cleanup_violation_count=0,
                eligible=eligible,
                stability_f1=(
                    sum(stability_values) / len(stability_values)
                    if stability_values
                    else None
                ),
                action_count_range=(min(action_counts), max(action_counts))
                if action_counts
                else None,
                gold_f1_range=max(f1_values) - min(f1_values) if f1_values else None,
                total_input_tokens=(
                    sum(input_tokens) if len(input_tokens) == len(route_results) else None
                ),
                total_output_tokens=(
                    sum(output_tokens) if len(output_tokens) == len(route_results) else None
                ),
                estimated_cost_cny=None,
            )
        )
    return aggregates


def _candidate_as_gold(candidate: object) -> GoldEvent:
    from hakimi_analysis.benchmark.models import BenchmarkCandidate

    assert isinstance(candidate, BenchmarkCandidate)
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
