import hashlib
import json
from pathlib import Path

from hakimi_analysis.release_gates import (
    validate_five_minute_canary_receipt,
    validate_five_minute_canary_receipt_json,
    validate_provider_conformance_report_json,
)

PRIMARY_MODEL = "doubao-seed-2-0-mini-260428"
FALLBACK_MODEL = "qwen3-vl-flash-2026-01-22"


def conformance_report_json() -> str:
    def route(name: str, model_id: str) -> dict[str, object]:
        return {
            "route": name,
            "model_id": model_id,
            "units": 24,
            "complete": True,
            "success_rate": 1.0,
            "precision": 0.95,
            "recall": 0.9,
            "f1_tiou_05": 0.9,
            "time_hit_rate": 0.9,
            "median_seconds_per_video_minute": 10.0,
            "schema_or_clock_violations": 0,
            "cost_status": "not-measured",
            "passes_quality_gate": True,
        }

    return json.dumps(
        {
            "schema_version": 1,
            "manifest_version": "provider-conformance-v1",
            "prompt_contract_sha256": "b" * 64,
            "routes": [
                route("seed-mini", PRIMARY_MODEL),
                route("seed-lite", "doubao-seed-2-0-lite-260428"),
                route("qwen-vl", FALLBACK_MODEL),
            ],
            "selection": "seed-mini",
            "seed_primary_selection": "seed-mini",
            "qwen_fallback_qualified": True,
            "version_c_decision": "eligible-for-version-c-canary",
        },
        separators=(",", ":"),
    )


def write_receipt(path: Path, commit_sha: str, conformance_sha256: str) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profile_version": "five-minute-production-v1",
                "commit_sha": commit_sha,
                "completed_at": "2026-07-22T01:00:00Z",
                "private_smoke_passed": True,
                "qwen_preflight_passed": True,
                "full_pipeline_quality_passed": True,
                "parameter_provenance_passed": True,
                "five_minute_end_coverage_passed": True,
                "three_way_concurrency_passed": True,
                "cancellation_cleanup_passed": True,
                "circuit_breaker_passed": True,
                "cos_cleanup_passed": True,
                "rollback_sequence": "B-C-B-C",
                "published_max_seconds": 300,
                "primary_model_id": PRIMARY_MODEL,
                "fallback_model_id": FALLBACK_MODEL,
                "provider_conformance_report_sha256": conformance_sha256,
            }
        ),
        encoding="utf-8",
    )


def test_five_minute_capability_requires_receipt_for_the_deployed_commit(
    tmp_path: Path,
) -> None:
    receipt = tmp_path / "provider-canary.json"
    commit_sha = "a" * 40
    report = conformance_report_json()
    report_sha256 = hashlib.sha256(report.encode()).hexdigest()
    write_receipt(receipt, commit_sha, report_sha256)

    assert validate_five_minute_canary_receipt(
        receipt,
        expected_commit_sha=commit_sha,
        expected_primary_model_id=PRIMARY_MODEL,
        expected_fallback_model_id=FALLBACK_MODEL,
        expected_conformance_report_sha256=report_sha256,
    )
    assert not validate_five_minute_canary_receipt(
        receipt,
        expected_commit_sha="b" * 40,
        expected_primary_model_id=PRIMARY_MODEL,
        expected_fallback_model_id=FALLBACK_MODEL,
        expected_conformance_report_sha256=report_sha256,
    )
    assert validate_five_minute_canary_receipt_json(
        receipt.read_text(encoding="utf-8"),
        expected_commit_sha=commit_sha,
        expected_primary_model_id=PRIMARY_MODEL,
        expected_fallback_model_id=FALLBACK_MODEL,
        expected_conformance_report_sha256=report_sha256,
    )
    assert (
        validate_provider_conformance_report_json(
            report,
            expected_prompt_sha256="b" * 64,
            expected_primary_model_id=PRIMARY_MODEL,
            expected_fallback_model_id=FALLBACK_MODEL,
        )
        == report_sha256
    )


def test_canary_receipt_rejects_an_incomplete_rollback(tmp_path: Path) -> None:
    receipt = tmp_path / "provider-canary.json"
    commit_sha = "a" * 40
    report_sha256 = "c" * 64
    write_receipt(receipt, commit_sha, report_sha256)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["rollback_sequence"] = "B-C"
    receipt.write_text(json.dumps(payload), encoding="utf-8")

    assert not validate_five_minute_canary_receipt(
        receipt,
        expected_commit_sha=commit_sha,
        expected_primary_model_id=PRIMARY_MODEL,
        expected_fallback_model_id=FALLBACK_MODEL,
        expected_conformance_report_sha256=report_sha256,
    )


def test_conformance_report_rejects_a_forged_qualified_fallback() -> None:
    payload = json.loads(conformance_report_json())
    qwen = next(route for route in payload["routes"] if route["route"] == "qwen-vl")
    qwen["recall"] = 0.2
    forged = json.dumps(payload, separators=(",", ":"))

    assert (
        validate_provider_conformance_report_json(
            forged,
            expected_prompt_sha256="b" * 64,
            expected_primary_model_id=PRIMARY_MODEL,
            expected_fallback_model_id=FALLBACK_MODEL,
        )
        is None
    )
