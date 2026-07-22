import json
from typing import cast

import pytest

from hakimi_analysis.benchmark.long_report import (
    render_long_video_report,
    sanitized_long_video_report,
)
from hakimi_analysis.benchmark.long_results import LongExpiryAuditStatus
from hakimi_analysis.benchmark.long_scoring import (
    LongArmAggregate,
    LongVideoArm,
    LongVideoDecision,
)


def _aggregate() -> LongArmAggregate:
    return LongArmAggregate(
        arm=LongVideoArm.QWEN_VIDEO,
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
        median_cost_cny=1.2,
        total_cost_cny=7.2,
        eligible=True,
    )


def test_sanitized_long_video_report_whitelists_only_aggregate_and_safe_protocol_fields() -> None:
    report = sanitized_long_video_report(
        [_aggregate()],
        LongVideoDecision(
            winner=LongVideoArm.QWEN_VIDEO,
            conclusion="qwen_candidate_wins",
            reason="quality_gap",
        ),
        protocol={
            "chunk_seconds": 60,
            "overlap_seconds": 10,
            "manifest_sha256": "a" * 64,
            "gold_sha256": "b" * 64,
            "asr_model_id": "volc.seedasr.sauc.duration",
            "qwen_visual_model_id": "qwen3-vl-flash",
            "media_path": "C:\\Users\\private\\video.mp4",
            "ARK_API_KEY": "secret-value",
        },
        diagnostics={
            "proxy_mode": "explicit-socks5h",
            "raw_response": "private model output",
            "transcript": "private speech",
            "source_path": "C:\\Users\\private\\video.mp4",
        },
    )

    serialized = json.dumps(report, ensure_ascii=False)

    assert report["scope"] == "local_long_video_quality_only"
    assert report["promotion_status"] == "pending"
    assert report["protocol"] == {
        "chunk_seconds": 60,
        "overlap_seconds": 10,
        "manifest_sha256": "a" * 64,
        "gold_sha256": "b" * 64,
        "asr_model_id": "volc.seedasr.sauc.duration",
        "qwen_visual_model_id": "qwen3-vl-flash",
    }
    assert report["diagnostics"] == {"proxy_mode": "explicit-socks5h"}
    decision = cast(dict[str, object], report["decision"])
    assert decision["conclusion"] == "qwen_candidate_wins"
    assert "secret-value" not in serialized
    assert "private model output" not in serialized
    assert "private speech" not in serialized
    assert "C:\\Users\\private\\video.mp4" not in serialized


def test_render_long_video_report_serializes_the_same_sanitized_shape() -> None:
    rendered = render_long_video_report(
        [_aggregate()],
        LongVideoDecision(
            winner=LongVideoArm.QWEN_VIDEO,
            conclusion="qwen_candidate_wins",
            reason="quality_gap",
        ),
        protocol={"chunk_seconds": 60},
    )

    report = json.loads(rendered)

    assert report["arms"][0]["arm"] == "qwen_video"
    assert report["arms"][0]["comparison_arm"] == "B"
    assert report["promotion_status"] == "pending"
    assert report["protocol"] == {"chunk_seconds": 60}


@pytest.mark.parametrize(
    ("audit_status", "promotion_status"),
    [
        ("pending", "pending"),
        ("lifecycle_failed", "lifecycle_failed"),
        ("provider_ttl_elapsed", "provider_ttl_elapsed_research_only"),
    ],
)
def test_report_promotion_status_tracks_the_expiry_audit_without_claiming_deletion(
    audit_status: LongExpiryAuditStatus,
    promotion_status: str,
) -> None:
    report = sanitized_long_video_report(
        [_aggregate()],
        LongVideoDecision(
            winner=LongVideoArm.QWEN_VIDEO,
            conclusion="qwen_candidate_wins",
            reason="quality_gap",
        ),
        protocol={"chunk_seconds": 60},
        expiry_audit_status=audit_status,
    )

    assert report["promotion_status"] == promotion_status
    assert "deleted" not in json.dumps(report).lower()
