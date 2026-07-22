import hashlib
import json
import math
from pathlib import Path

import pytest

from hakimi_analysis.release_gates import (
    load_immutable_build_commit,
    provider_config_sha256,
    validate_five_minute_canary_receipt,
    validate_five_minute_canary_receipt_json,
    validate_provider_conformance_report_json,
)

PRIMARY_MODEL = "doubao-seed-2-0-mini-260428"
FALLBACK_MODEL = "qwen3-vl-flash-2026-01-22"
SEED_LITE_MODEL = "doubao-seed-2-0-lite-260428"
VISUAL_PROMPT_SHA256 = "b" * 64
SPEECH_PROMPT_SHA256 = "d" * 64
ASR_CONTEXT_SHA256 = "e" * 64
ASR_HOTWORDS_SHA256 = "f" * 64
PROVIDER_CONFIG_SHA256 = "1" * 64


def receipt_expectations() -> dict[str, str]:
    return {
        "expected_visual_prompt_sha256": VISUAL_PROMPT_SHA256,
        "expected_speech_prompt_sha256": SPEECH_PROMPT_SHA256,
        "expected_asr_context_sha256": ASR_CONTEXT_SHA256,
        "expected_asr_hotwords_sha256": ASR_HOTWORDS_SHA256,
        "expected_provider_config_sha256": PROVIDER_CONFIG_SHA256,
    }


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
                route("seed-lite", SEED_LITE_MODEL),
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
                "five_minute_median_seconds": 70.0,
                "five_minute_max_seconds": 110.0,
                "fallback_fault_max_seconds": 140.0,
                "seed_mini_visual_model_id": PRIMARY_MODEL,
                "seed_lite_visual_model_id": SEED_LITE_MODEL,
                "qwen_visual_model_id": FALLBACK_MODEL,
                "speech_model_id": PRIMARY_MODEL,
                "visual_prompt_sha256": VISUAL_PROMPT_SHA256,
                "speech_prompt_sha256": SPEECH_PROMPT_SHA256,
                "asr_context_sha256": ASR_CONTEXT_SHA256,
                "asr_hotwords_sha256": ASR_HOTWORDS_SHA256,
                "provider_config_sha256": PROVIDER_CONFIG_SHA256,
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
        **receipt_expectations(),
    )
    assert not validate_five_minute_canary_receipt(
        receipt,
        expected_commit_sha="b" * 40,
        expected_primary_model_id=PRIMARY_MODEL,
        expected_fallback_model_id=FALLBACK_MODEL,
        expected_conformance_report_sha256=report_sha256,
        **receipt_expectations(),
    )
    assert validate_five_minute_canary_receipt_json(
        receipt.read_text(encoding="utf-8"),
        expected_commit_sha=commit_sha,
        expected_primary_model_id=PRIMARY_MODEL,
        expected_fallback_model_id=FALLBACK_MODEL,
        expected_conformance_report_sha256=report_sha256,
        **receipt_expectations(),
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
        **receipt_expectations(),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("five_minute_median_seconds", True),
        ("five_minute_median_seconds", 75.01),
        ("five_minute_median_seconds", math.nan),
        ("five_minute_max_seconds", False),
        ("five_minute_max_seconds", 120.01),
        ("five_minute_max_seconds", math.inf),
        ("fallback_fault_max_seconds", True),
        ("fallback_fault_max_seconds", 150.01),
        ("fallback_fault_max_seconds", -math.inf),
    ],
)
def test_canary_receipt_rejects_invalid_latency_metrics(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    receipt = tmp_path / "provider-canary.json"
    commit_sha = "a" * 40
    report_sha256 = "c" * 64
    write_receipt(receipt, commit_sha, report_sha256)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload[field] = value

    assert not validate_five_minute_canary_receipt_json(
        json.dumps(payload),
        expected_commit_sha=commit_sha,
        expected_primary_model_id=PRIMARY_MODEL,
        expected_fallback_model_id=FALLBACK_MODEL,
        expected_conformance_report_sha256=report_sha256,
        **receipt_expectations(),
    )


def test_canary_receipt_rejects_median_above_observed_max(tmp_path: Path) -> None:
    receipt = tmp_path / "provider-canary.json"
    commit_sha = "a" * 40
    report_sha256 = "c" * 64
    write_receipt(receipt, commit_sha, report_sha256)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["five_minute_median_seconds"] = 70
    payload["five_minute_max_seconds"] = 60

    assert not validate_five_minute_canary_receipt_json(
        json.dumps(payload),
        expected_commit_sha=commit_sha,
        expected_primary_model_id=PRIMARY_MODEL,
        expected_fallback_model_id=FALLBACK_MODEL,
        expected_conformance_report_sha256=report_sha256,
        **receipt_expectations(),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("seed_mini_visual_model_id", "doubao-seed-2-0-mini-latest"),
        ("seed_lite_visual_model_id", "doubao-seed-2-0-lite-latest"),
        ("qwen_visual_model_id", "qwen3-vl-flash"),
        ("speech_model_id", "doubao-seed-2-0-lite-260428"),
        ("visual_prompt_sha256", "0" * 64),
        ("speech_prompt_sha256", "0" * 64),
        ("asr_context_sha256", "0" * 64),
        ("asr_hotwords_sha256", "0" * 64),
    ],
)
def test_canary_receipt_binds_the_complete_frozen_provider_configuration(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    receipt = tmp_path / "provider-canary.json"
    commit_sha = "a" * 40
    report_sha256 = "c" * 64
    write_receipt(receipt, commit_sha, report_sha256)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload[field] = value

    assert not validate_five_minute_canary_receipt_json(
        json.dumps(payload),
        expected_commit_sha=commit_sha,
        expected_primary_model_id=PRIMARY_MODEL,
        expected_fallback_model_id=FALLBACK_MODEL,
        expected_conformance_report_sha256=report_sha256,
        **receipt_expectations(),
    )


def test_load_immutable_build_commit_requires_exact_metadata(tmp_path: Path) -> None:
    metadata = tmp_path / "build-metadata.json"
    commit_sha = "a" * 40
    metadata.write_text(
        json.dumps({"schema_version": 1, "commit_sha": commit_sha}),
        encoding="utf-8",
    )
    assert load_immutable_build_commit(metadata) == commit_sha

    metadata.write_text(
        json.dumps(
            {"schema_version": 1, "commit_sha": commit_sha, "environment": "forged"}
        ),
        encoding="utf-8",
    )
    assert load_immutable_build_commit(metadata) is None

    metadata.write_text(
        json.dumps({"schema_version": True, "commit_sha": commit_sha}),
        encoding="utf-8",
    )
    assert load_immutable_build_commit(metadata) is None


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


@pytest.mark.parametrize(
    ("route_name", "model_id"),
    [
        ("seed-mini", "doubao-seed-2-0-mini-latest"),
        ("seed-lite", "doubao-seed-2-0-lite-latest"),
        ("qwen-vl", "qwen3-vl-flash"),
    ],
)
def test_conformance_report_requires_exact_frozen_route_model_ids(
    route_name: str,
    model_id: str,
) -> None:
    payload = json.loads(conformance_report_json())
    route = next(route for route in payload["routes"] if route["route"] == route_name)
    route["model_id"] = model_id

    assert (
        validate_provider_conformance_report_json(
            json.dumps(payload, separators=(",", ":")),
            expected_prompt_sha256=VISUAL_PROMPT_SHA256,
            expected_primary_model_id=(
                model_id if route_name == "seed-mini" else PRIMARY_MODEL
            ),
            expected_fallback_model_id=(
                model_id if route_name == "qwen-vl" else FALLBACK_MODEL
            ),
        )
        is None
    )


def test_conformance_report_rejects_boolean_numeric_metrics() -> None:
    payload = json.loads(conformance_report_json())
    qwen = next(route for route in payload["routes"] if route["route"] == "qwen-vl")
    qwen["precision"] = True

    assert (
        validate_provider_conformance_report_json(
            json.dumps(payload, separators=(",", ":")),
            expected_prompt_sha256="b" * 64,
            expected_primary_model_id=PRIMARY_MODEL,
            expected_fallback_model_id=FALLBACK_MODEL,
        )
        is None
    )


def test_all_release_documents_reject_boolean_schema_versions(tmp_path: Path) -> None:
    receipt = tmp_path / "provider-canary.json"
    commit_sha = "a" * 40
    report = conformance_report_json()
    report_sha256 = hashlib.sha256(report.encode()).hexdigest()
    write_receipt(receipt, commit_sha, report_sha256)
    receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))
    receipt_payload["schema_version"] = True
    assert not validate_five_minute_canary_receipt_json(
        json.dumps(receipt_payload),
        expected_commit_sha=commit_sha,
        expected_primary_model_id=PRIMARY_MODEL,
        expected_fallback_model_id=FALLBACK_MODEL,
        expected_conformance_report_sha256=report_sha256,
        **receipt_expectations(),
    )

    report_payload = json.loads(report)
    report_payload["schema_version"] = True
    assert (
        validate_provider_conformance_report_json(
            json.dumps(report_payload),
            expected_prompt_sha256="b" * 64,
            expected_primary_model_id=PRIMARY_MODEL,
            expected_fallback_model_id=FALLBACK_MODEL,
        )
        is None
    )


def test_provider_config_digest_changes_for_any_runtime_provider_drift() -> None:
    base: dict[str, object] = {
        "ark_base_url": "https://ark.example/api/v3",
        "ark_speech_model_id": PRIMARY_MODEL,
        "ark_visual_model_id": PRIMARY_MODEL,
        "qwen_base_url": "https://dashscope.example/v1",
        "qwen_visual_model_id": FALLBACK_MODEL,
        "asr_resource_id": "volc.seedasr.sauc.duration",
        "asr_url": "wss://asr.example/v3",
        "visual_fallback_enabled": True,
        "cos_region": "ap-guangzhou",
        "cos_bucket": "private-bucket-123",
        "cos_object_prefix": "trainpal/visual-fallback",
        "cos_signed_url_ttl_seconds": 600,
        "cos_lifecycle_days": 1,
    }

    def digest(values: dict[str, object]) -> str:
        signed_url_ttl = values["cos_signed_url_ttl_seconds"]
        lifecycle_days = values["cos_lifecycle_days"]
        assert type(signed_url_ttl) is int
        assert type(lifecycle_days) is int
        return provider_config_sha256(
            ark_base_url=str(values["ark_base_url"]),
            ark_speech_model_id=str(values["ark_speech_model_id"]),
            ark_visual_model_id=str(values["ark_visual_model_id"]),
            qwen_base_url=str(values["qwen_base_url"]),
            qwen_visual_model_id=str(values["qwen_visual_model_id"]),
            asr_resource_id=str(values["asr_resource_id"]),
            asr_url=str(values["asr_url"]),
            visual_fallback_enabled=values["visual_fallback_enabled"] is True,
            cos_region=str(values["cos_region"]),
            cos_bucket=str(values["cos_bucket"]),
            cos_object_prefix=str(values["cos_object_prefix"]),
            cos_signed_url_ttl_seconds=signed_url_ttl,
            cos_lifecycle_days=lifecycle_days,
        )

    baseline = digest(base)

    for field, changed in (
        ("ark_base_url", "https://ark-drift.example/api/v3"),
        ("asr_resource_id", "different-asr-right"),
        ("asr_url", "wss://different-asr.example/v3"),
        ("qwen_base_url", "https://different-qwen.example/v1"),
        ("cos_bucket", "different-private-bucket"),
        ("cos_object_prefix", "different/prefix"),
    ):
        drifted = {**base, field: changed}
        assert digest(drifted) != baseline
