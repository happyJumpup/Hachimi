import json

from hakimi_analysis.benchmark.models import RouteAggregate
from hakimi_analysis.benchmark.report import sanitized_report


def test_report_contains_only_aggregate_safe_fields() -> None:
    report = sanitized_report(
        [
            RouteAggregate(
                route_id="S-AV",
                scored_units=9,
                precision=0.95,
                recall=0.85,
                precision_tiou_03=0.96,
                recall_tiou_03=0.9,
                f1_tiou_03=0.93,
                f1_tiou_05=0.88,
                duplicate_count=0,
                fragment_count=0,
                parameter_accuracy=1,
                parameter_accuracy_by_name={
                    "sets": 1,
                    "reps": 1,
                    "duration_seconds": None,
                    "rest_seconds": None,
                },
                median_inference_seconds=8.2,
                inference_seconds_range=(7.9, 9.1),
                unsupported_parameter_rate=0,
                weight_violation_count=0,
                schema_violation_count=0,
                cleanup_violation_count=0,
                eligible=True,
            )
        ],
        diagnostics={
            "ARK_API_KEY": "secret-value",
            "raw_response": "private model output",
            "transcript": "private speech",
            "request_id": "safe-request-1",
        },
    )

    serialized = json.dumps(report, ensure_ascii=False)
    assert "secret-value" not in serialized
    assert "private model output" not in serialized
    assert "private speech" not in serialized
    assert "safe-request-1" in serialized
    assert "p95" not in serialized
