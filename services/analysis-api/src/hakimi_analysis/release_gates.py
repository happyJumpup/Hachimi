import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_CANARY_KEYS = {
    "schema_version",
    "profile_version",
    "commit_sha",
    "completed_at",
    "private_smoke_passed",
    "qwen_preflight_passed",
    "full_pipeline_quality_passed",
    "parameter_provenance_passed",
    "five_minute_end_coverage_passed",
    "three_way_concurrency_passed",
    "cancellation_cleanup_passed",
    "circuit_breaker_passed",
    "cos_cleanup_passed",
    "rollback_sequence",
    "published_max_seconds",
    "primary_model_id",
    "fallback_model_id",
    "provider_conformance_report_sha256",
}


def validate_five_minute_canary_receipt(
    path: Path | None,
    *,
    expected_commit_sha: str,
    expected_primary_model_id: str,
    expected_fallback_model_id: str,
    expected_conformance_report_sha256: str,
) -> bool:
    if path is None:
        return False
    try:
        payload = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return validate_five_minute_canary_receipt_json(
        payload,
        expected_commit_sha=expected_commit_sha,
        expected_primary_model_id=expected_primary_model_id,
        expected_fallback_model_id=expected_fallback_model_id,
        expected_conformance_report_sha256=expected_conformance_report_sha256,
    )


def validate_five_minute_canary_receipt_json(
    payload: str,
    *,
    expected_commit_sha: str,
    expected_primary_model_id: str,
    expected_fallback_model_id: str,
    expected_conformance_report_sha256: str,
) -> bool:
    if (
        re.fullmatch(r"[0-9a-f]{40}", expected_commit_sha) is None
        or re.fullmatch(r"[0-9a-f]{64}", expected_conformance_report_sha256) is None
    ):
        return False
    try:
        raw: Any = json.loads(payload)
        completed_at = datetime.fromisoformat(str(raw["completed_at"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(raw, dict) or set(raw) != _CANARY_KEYS:
        return False
    if completed_at.tzinfo is None or completed_at > datetime.now(UTC):
        return False
    return (
        raw["schema_version"] == 1
        and raw["profile_version"] == "five-minute-production-v1"
        and raw["commit_sha"] == expected_commit_sha
        and raw["private_smoke_passed"] is True
        and raw["qwen_preflight_passed"] is True
        and raw["full_pipeline_quality_passed"] is True
        and raw["parameter_provenance_passed"] is True
        and raw["five_minute_end_coverage_passed"] is True
        and raw["three_way_concurrency_passed"] is True
        and raw["cancellation_cleanup_passed"] is True
        and raw["circuit_breaker_passed"] is True
        and raw["cos_cleanup_passed"] is True
        and raw["rollback_sequence"] == "B-C-B-C"
        and raw["published_max_seconds"] == 300
        and raw["primary_model_id"] == expected_primary_model_id
        and raw["fallback_model_id"] == expected_fallback_model_id
        and raw["provider_conformance_report_sha256"]
        == expected_conformance_report_sha256
    )


def validate_provider_conformance_report_json(
    payload: str,
    *,
    expected_prompt_sha256: str,
    expected_primary_model_id: str,
    expected_fallback_model_id: str,
) -> str | None:
    try:
        raw: Any = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version",
        "manifest_version",
        "prompt_contract_sha256",
        "routes",
        "selection",
        "seed_primary_selection",
        "qwen_fallback_qualified",
        "version_c_decision",
    }:
        return None
    if (
        raw["schema_version"] != 1
        or not isinstance(raw["manifest_version"], str)
        or not raw["manifest_version"].strip()
        or raw["prompt_contract_sha256"] != expected_prompt_sha256
        or raw["selection"] != raw["seed_primary_selection"]
        or raw["selection"] not in {"seed-mini", "seed-lite"}
        or raw["qwen_fallback_qualified"] is not True
        or raw["version_c_decision"] != "eligible-for-version-c-canary"
    ):
        return None
    routes = raw["routes"]
    if not isinstance(routes, list) or len(routes) != 3:
        return None
    route_by_name = {
        route.get("route"): route
        for route in routes
        if isinstance(route, dict) and isinstance(route.get("route"), str)
    }
    if set(route_by_name) != {"seed-mini", "seed-lite", "qwen-vl"}:
        return None
    primary = route_by_name[raw["seed_primary_selection"]]
    fallback = route_by_name["qwen-vl"]
    if (
        primary.get("model_id") != expected_primary_model_id
        or fallback.get("model_id") != expected_fallback_model_id
        or not _qualified_route_summary(primary)
        or not _qualified_route_summary(fallback)
    ):
        return None
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _qualified_route_summary(summary: dict[str, Any]) -> bool:
    return (
        summary.get("units") == 24
        and summary.get("complete") is True
        and summary.get("success_rate") == 1
        and isinstance(summary.get("precision"), int | float)
        and summary["precision"] >= 0.90
        and isinstance(summary.get("recall"), int | float)
        and summary["recall"] >= 0.80
        and isinstance(summary.get("f1_tiou_05"), int | float)
        and summary["f1_tiou_05"] >= 0.75
        and summary.get("schema_or_clock_violations") == 0
        and summary.get("passes_quality_gate") is True
    )


__all__ = [
    "validate_five_minute_canary_receipt",
    "validate_five_minute_canary_receipt_json",
    "validate_provider_conformance_report_json",
]
