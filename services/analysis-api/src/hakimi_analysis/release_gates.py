import hashlib
import json
import math
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from hakimi_analysis.fusion import EVIDENCE_RECONCILER_VERSION

SEED_MINI_VISUAL_MODEL_ID = "doubao-seed-2-0-mini-260428"
SEED_LITE_VISUAL_MODEL_ID = "doubao-seed-2-0-lite-260428"
QWEN_VISUAL_MODEL_ID = "qwen3-vl-flash-2026-01-22"
SPEECH_MODEL_ID = "doubao-seed-2-0-mini-260428"

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
    "five_minute_median_seconds",
    "five_minute_max_seconds",
    "fallback_fault_max_seconds",
    "seed_mini_visual_model_id",
    "seed_lite_visual_model_id",
    "qwen_visual_model_id",
    "speech_model_id",
    "visual_prompt_sha256",
    "speech_prompt_sha256",
    "asr_context_sha256",
    "asr_hotwords_sha256",
    "provider_config_sha256",
    "ffmpeg_binary_sha256",
    "ffmpeg_configuration_sha256",
}

_BUILD_METADATA_KEYS = {"schema_version", "commit_sha"}


def load_immutable_build_commit(path: Path) -> str | None:
    """Read the image-baked commit identity from its fixed runtime path."""
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or set(raw) != _BUILD_METADATA_KEYS:
        return None
    commit_sha = raw.get("commit_sha")
    if (
        type(raw.get("schema_version")) is not int
        or raw["schema_version"] != 1
        or not isinstance(commit_sha, str)
        or re.fullmatch(r"[0-9a-f]{40}", commit_sha) is None
    ):
        return None
    return commit_sha


def validate_five_minute_canary_receipt(
    path: Path | None,
    *,
    expected_commit_sha: str,
    expected_primary_model_id: str,
    expected_fallback_model_id: str,
    expected_conformance_report_sha256: str,
    expected_visual_prompt_sha256: str,
    expected_speech_prompt_sha256: str,
    expected_asr_context_sha256: str,
    expected_asr_hotwords_sha256: str,
    expected_provider_config_sha256: str,
    expected_ffmpeg_binary_sha256: str,
    expected_ffmpeg_configuration_sha256: str,
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
        expected_visual_prompt_sha256=expected_visual_prompt_sha256,
        expected_speech_prompt_sha256=expected_speech_prompt_sha256,
        expected_asr_context_sha256=expected_asr_context_sha256,
        expected_asr_hotwords_sha256=expected_asr_hotwords_sha256,
        expected_provider_config_sha256=expected_provider_config_sha256,
        expected_ffmpeg_binary_sha256=expected_ffmpeg_binary_sha256,
        expected_ffmpeg_configuration_sha256=expected_ffmpeg_configuration_sha256,
    )


def validate_five_minute_canary_receipt_json(
    payload: str,
    *,
    expected_commit_sha: str,
    expected_primary_model_id: str,
    expected_fallback_model_id: str,
    expected_conformance_report_sha256: str,
    expected_visual_prompt_sha256: str,
    expected_speech_prompt_sha256: str,
    expected_asr_context_sha256: str,
    expected_asr_hotwords_sha256: str,
    expected_provider_config_sha256: str,
    expected_ffmpeg_binary_sha256: str,
    expected_ffmpeg_configuration_sha256: str,
) -> bool:
    if (
        re.fullmatch(r"[0-9a-f]{40}", expected_commit_sha) is None
        or re.fullmatch(r"[0-9a-f]{64}", expected_conformance_report_sha256) is None
        or any(
            re.fullmatch(r"[0-9a-f]{64}", digest) is None
            for digest in (
                expected_visual_prompt_sha256,
                expected_speech_prompt_sha256,
                expected_asr_context_sha256,
                expected_asr_hotwords_sha256,
                expected_provider_config_sha256,
                expected_ffmpeg_binary_sha256,
                expected_ffmpeg_configuration_sha256,
            )
        )
    ):
        return False
    try:
        raw: Any = json.loads(payload)
        completed_at = datetime.fromisoformat(str(raw["completed_at"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(raw, dict) or set(raw) != _CANARY_KEYS:
        return False
    now = datetime.now(UTC)
    if (
        completed_at.tzinfo is None
        or completed_at > now
        or now - completed_at > timedelta(days=7)
    ):
        return False
    median_seconds = _strict_finite_number(raw["five_minute_median_seconds"])
    max_seconds = _strict_finite_number(raw["five_minute_max_seconds"])
    fallback_max_seconds = _strict_finite_number(raw["fallback_fault_max_seconds"])
    if (
        median_seconds is None
        or max_seconds is None
        or fallback_max_seconds is None
        or not 0 < median_seconds <= 75
        or not median_seconds <= max_seconds <= 120
        or not 0 < fallback_max_seconds <= 150
    ):
        return False
    return (
        type(raw["schema_version"]) is int
        and raw["schema_version"] == 1
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
        and raw["seed_mini_visual_model_id"] == SEED_MINI_VISUAL_MODEL_ID
        and raw["seed_lite_visual_model_id"] == SEED_LITE_VISUAL_MODEL_ID
        and raw["qwen_visual_model_id"] == QWEN_VISUAL_MODEL_ID
        and raw["speech_model_id"] == SPEECH_MODEL_ID
        and raw["visual_prompt_sha256"] == expected_visual_prompt_sha256
        and raw["speech_prompt_sha256"] == expected_speech_prompt_sha256
        and raw["asr_context_sha256"] == expected_asr_context_sha256
        and raw["asr_hotwords_sha256"] == expected_asr_hotwords_sha256
        and raw["provider_config_sha256"] == expected_provider_config_sha256
        and raw["ffmpeg_binary_sha256"] == expected_ffmpeg_binary_sha256
        and raw["ffmpeg_configuration_sha256"]
        == expected_ffmpeg_configuration_sha256
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
        "evidence_reconciler_version",
        "routes",
        "selection",
        "seed_primary_selection",
        "qwen_fallback_qualified",
        "version_c_decision",
    }:
        return None
    if (
        type(raw["schema_version"]) is not int
        or raw["schema_version"] != 1
        or not isinstance(raw["manifest_version"], str)
        or not raw["manifest_version"].strip()
        or raw["prompt_contract_sha256"] != expected_prompt_sha256
        or raw["evidence_reconciler_version"] != EVIDENCE_RECONCILER_VERSION
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
    if {
        name: route_by_name[name].get("model_id") for name in route_by_name
    } != {
        "seed-mini": SEED_MINI_VISUAL_MODEL_ID,
        "seed-lite": SEED_LITE_VISUAL_MODEL_ID,
        "qwen-vl": QWEN_VISUAL_MODEL_ID,
    }:
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
    success_rate = _strict_finite_number(summary.get("success_rate"))
    precision = _strict_finite_number(summary.get("precision"))
    recall = _strict_finite_number(summary.get("recall"))
    f1 = _strict_finite_number(summary.get("f1_tiou_05"))
    return (
        type(summary.get("units")) is int
        and summary["units"] == 24
        and summary.get("complete") is True
        and success_rate == 1
        and precision is not None
        and 0.90 <= precision <= 1
        and recall is not None
        and 0.80 <= recall <= 1
        and f1 is not None
        and 0.75 <= f1 <= 1
        and type(summary.get("schema_or_clock_violations")) is int
        and summary["schema_or_clock_violations"] == 0
        and summary.get("passes_quality_gate") is True
    )


def _strict_finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    normalized = float(value)
    return normalized if math.isfinite(normalized) else None


def provider_config_sha256(
    *,
    ark_base_url: str,
    ark_speech_model_id: str,
    ark_visual_model_id: str,
    qwen_base_url: str,
    qwen_visual_model_id: str,
    asr_resource_id: str,
    asr_url: str,
    visual_fallback_enabled: bool,
    cos_region: str,
    cos_bucket: str,
    cos_object_prefix: str,
    cos_signed_url_ttl_seconds: int,
    cos_lifecycle_days: int,
) -> str:
    """Hash the non-secret Provider identity used by a production canary."""
    payload = {
        "schema_version": 1,
        "ark_base_url": ark_base_url.rstrip("/"),
        "ark_speech_model_id": ark_speech_model_id,
        "ark_visual_model_id": ark_visual_model_id,
        "qwen_base_url": qwen_base_url.rstrip("/"),
        "qwen_visual_model_id": qwen_visual_model_id,
        "asr_resource_id": asr_resource_id,
        "asr_url": asr_url,
        "visual_fallback_enabled": visual_fallback_enabled,
        "cos_region": cos_region,
        "cos_bucket": cos_bucket,
        "cos_object_prefix": cos_object_prefix.strip("/"),
        "cos_signed_url_ttl_seconds": cos_signed_url_ttl_seconds,
        "cos_lifecycle_days": cos_lifecycle_days,
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


__all__ = [
    "QWEN_VISUAL_MODEL_ID",
    "SEED_LITE_VISUAL_MODEL_ID",
    "SEED_MINI_VISUAL_MODEL_ID",
    "SPEECH_MODEL_ID",
    "load_immutable_build_commit",
    "provider_config_sha256",
    "validate_five_minute_canary_receipt",
    "validate_five_minute_canary_receipt_json",
    "validate_provider_conformance_report_json",
]
