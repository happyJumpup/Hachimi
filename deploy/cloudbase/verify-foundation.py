#!/usr/bin/env python3
"""Validate the non-secret CloudBase competition foundation contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

EXPECTED_ENVIRONMENT = "bizhao-d8grp8yqd81759fbb"
EXPECTED_REGION = "ap-shanghai"
EXPECTED_SERVICE = "trainpal-demo"
EXPECTED_SECRET_KEYS = [
    "ACCESS_COOKIE_SECRET",
    "ARK_API_KEY",
    "JUDGE_ACCESS_CODE",
    "VOLC_ASR_API_KEY",
]
EXPECTED_NONSECRET_KEYS = [
    "ANALYSIS_PROVIDER",
    "APP_ENV",
    "ARK_BASE_URL",
    "ARK_MODEL_ID",
    "ARK_VISUAL_MODEL_ID",
    "CORS_ORIGINS",
    "FFMPEG_EXPECTED_CONFIGURATION_SHA256",
    "FFMPEG_EXPECTED_SHA256",
    "IMAGEIO_FFMPEG_EXE",
    "JUDGE_ANALYSIS_CONCURRENCY",
    "LOCAL_ANALYSIS_MAX_SECONDS",
    "LOCAL_UPLOAD_ENABLED",
    "PUBLIC_ANALYSIS_CONCURRENCY",
    "PUBLIC_MEDIA_BASE_URL",
    "RUN_TIMEOUT_SECONDS",
    "RUN_TTL_SECONDS",
    "SMOKE_ANNOTATIONS_PATH",
    "SOURCE_MANIFEST_PATH",
    "SOURCE_MEDIA_ROOT",
    "TRUSTED_PROXY_CIDRS",
    "VOLC_ASR_RESOURCE_ID",
    "VOLC_ASR_URL",
    "WEB_STATIC_ROOT",
]
SECRET_VALUE_MARKERS = (
    "secretid",
    "secretkey",
    "api key value",
    "judge code value",
)


def fail(message: str) -> None:
    raise ValueError(message)


def load_plan(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        fail("foundation plan must be a JSON object")
    return data


def expect_keys(value: Any, expected: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{path} must be a JSON object")
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        fail(f"{path} schema mismatch; missing={missing}, unknown={unknown}")
    return value


def validate(plan: dict[str, Any]) -> None:
    expect_keys(
        plan,
        {
            "schema_version",
            "purpose",
            "environment_id",
            "region",
            "service_name",
            "container",
            "capacity",
            "network",
            "runtime",
            "asset_contract",
            "release_gates",
            "rollback",
            "required_secret_environment_keys",
            "required_nonsecret_environment_keys",
            "budget",
            "submission_deadline",
        },
        "plan",
    )
    if plan.get("schema_version") != 1:
        fail("unsupported schema_version")
    if plan.get("purpose") != (
        "audited CloudBase Run foundation plan; not an importable Tencent Cloud API payload"
    ):
        fail("unexpected plan purpose")
    if plan.get("environment_id") != EXPECTED_ENVIRONMENT:
        fail("unexpected CloudBase environment")
    if plan.get("region") != EXPECTED_REGION:
        fail("unexpected CloudBase region")
    if plan.get("service_name") != EXPECTED_SERVICE:
        fail("unexpected CloudBase service name")

    container = expect_keys(
        plan["container"],
        {"port", "command_contract", "serves_web_and_api_same_origin", "logs"},
        "container",
    )
    capacity = expect_keys(
        plan["capacity"],
        {
            "cpu_cores",
            "memory_gib",
            "minimum_instances_idle",
            "minimum_instances_judging",
            "maximum_instances",
            "request_timeout_seconds",
        },
        "capacity",
    )
    network = expect_keys(
        plan["network"],
        {
            "public_https",
            "public_https_enabled_only_after_private_gates",
            "public_canary_required_before_link_announcement",
            "public_https_disabled_after_confirmed_demo_end",
            "default_domain_classification",
            "custom_domain_required",
        },
        "network",
    )
    runtime = expect_keys(
        plan["runtime"],
        {
            "app_env",
            "analysis_provider",
            "judge_analysis_concurrency",
            "public_analysis_concurrency",
            "run_timeout_seconds",
            "local_upload_enabled",
            "local_analysis_max_seconds",
            "unauthenticated_page_browse",
            "judge_code_required_for_paid_analysis",
            "anonymous_paid_analysis",
            "raw_media_persistence",
            "active_run_state",
        },
        "runtime",
    )
    assets = expect_keys(
        plan["asset_contract"],
        {
            "source_manifest_path",
            "source_media_root",
            "ffmpeg_path",
            "smoke_annotations_path",
            "must_verify_ffmpeg_sha256",
            "must_verify_ffmpeg_configuration_sha256",
            "assets_deferred_until_content_approval",
        },
        "asset_contract",
    )
    release_gates = expect_keys(
        plan["release_gates"], {"private", "public_canary"}, "release_gates"
    )
    private_gates = expect_keys(
        release_gates["private"],
        {
            "immutable_image_verified",
            "configuration_snapshot_recorded",
            "internal_health_ready",
            "rollback_target_retained",
        },
        "release_gates.private",
    )
    public_canary_gates = expect_keys(
        release_gates["public_canary"],
        {
            "public_health_ready",
            "judge_analysis_sse_result",
            "second_browser_entry",
            "concurrent_judge_requests",
            "over_capacity_http_status",
            "memory_pressure_observed",
            "rollback_smoke",
        },
        "release_gates.public_canary",
    )
    rollback = expect_keys(
        plan["rollback"],
        {
            "minimum_previous_versions_retained",
            "configuration_snapshot_required",
            "switch_target",
            "post_switch_ready_probe_required",
        },
        "rollback",
    )
    budget = expect_keys(
        plan["budget"],
        {
            "currency",
            "hard_ceiling",
            "pricing_source",
            "assumed_ccu_price_cny_per_hour",
            "ccu_per_warm_instance",
            "maximum_public_window_hours",
            "maximum_warm_hours",
            "estimated_compute_ceiling_cny",
            "estimated_continuous_compute_ceiling_cny",
            "confirmed_demo_end",
            "unpriced_resources_require_console_confirmation",
            "purchase_requires_fresh_confirmation",
        },
        "budget",
    )

    if container.get("port") != 8000:
        fail("container port must remain 8000")
    if container.get("command_contract") != "one-uvicorn-worker":
        fail("deployment must use one Uvicorn worker")
    if container.get("serves_web_and_api_same_origin") is not True:
        fail("web and API must be served from one origin")
    if container.get("logs") != ["stdout"]:
        fail("application logs must go only to stdout")

    cpu = capacity.get("cpu_cores")
    memory = capacity.get("memory_gib")
    if not isinstance(cpu, (int, float)) or not isinstance(memory, (int, float)):
        fail("CPU and memory must be numeric")
    if cpu != 2 or memory != 4:
        fail("judging capacity must remain 2 vCPU and 4 GiB")
    if memory != cpu * 2:
        fail("CloudBase Run CPU to memory ratio must remain 1:2")
    if capacity.get("minimum_instances_idle") != 0:
        fail("idle minimum instances must be zero")
    if capacity.get("minimum_instances_judging") != 1:
        fail("judging minimum instances must be one")
    if capacity.get("maximum_instances") != 1:
        fail("in-memory run state requires exactly one maximum instance")
    if capacity.get("request_timeout_seconds") != 240:
        fail("platform request timeout must remain 240 seconds")

    if network.get("public_https") is not True:
        fail("public HTTPS must be enabled")
    if network.get("public_https_enabled_only_after_private_gates") is not True:
        fail("public HTTPS must remain disabled until private gates pass")
    if network.get("public_canary_required_before_link_announcement") is not True:
        fail("a public canary must pass before the link is announced")
    if network.get("public_https_disabled_after_confirmed_demo_end") is not True:
        fail("public HTTPS must be disabled after the confirmed demo end")
    if network.get("default_domain_classification") != "development-demo-only":
        fail("default domain must not be classified as production")
    if network.get("custom_domain_required") is not False:
        fail("a custom domain must not be required for this demo")
    if runtime.get("app_env") != "production":
        fail("runtime APP_ENV must be production")
    if runtime.get("analysis_provider") != "cloud":
        fail("runtime analysis provider must be cloud")
    if runtime.get("judge_analysis_concurrency") != 3:
        fail("judge analysis concurrency must remain three")
    if runtime.get("public_analysis_concurrency") != 0:
        fail("anonymous paid analysis must remain disabled")
    if runtime.get("unauthenticated_page_browse") is not True:
        fail("the public product pages must remain browseable")
    if runtime.get("judge_code_required_for_paid_analysis") is not True:
        fail("paid analysis must require the judge code")
    if runtime.get("anonymous_paid_analysis") is not False:
        fail("anonymous paid analysis must remain disabled")
    if runtime.get("run_timeout_seconds") != 180:
        fail("application analysis timeout must remain 180 seconds")
    if runtime.get("local_upload_enabled") is not True:
        fail("local upload must remain enabled")
    if runtime.get("local_analysis_max_seconds") != 60:
        fail("local analysis must remain capped at 60 seconds")
    if runtime.get("active_run_state") != "single-process-memory":
        fail("run-state contract changed without an architecture decision")
    if runtime.get("raw_media_persistence") != "forbidden":
        fail("raw media persistence must remain forbidden")

    required_paths = (
        "source_manifest_path",
        "source_media_root",
        "ffmpeg_path",
        "smoke_annotations_path",
    )
    for key in required_paths:
        value = assets.get(key)
        if not isinstance(value, str) or not value.startswith("/"):
            fail(f"{key} must be an absolute container path")
    expected_paths = {
        "source_manifest_path": "/config/media-manifest.json",
        "source_media_root": "/mnt/trainpal/media",
        "ffmpeg_path": "/opt/trainpal/bin/ffmpeg",
        "smoke_annotations_path": "/config/smoke-annotations.json",
    }
    for key, expected in expected_paths.items():
        if assets.get(key) != expected:
            fail(f"unexpected {key}")
    if assets.get("assets_deferred_until_content_approval") is not True:
        fail("content assets must remain deferred")
    if assets.get("must_verify_ffmpeg_sha256") is not True:
        fail("FFmpeg binary hash verification must remain enabled")
    if assets.get("must_verify_ffmpeg_configuration_sha256") is not True:
        fail("FFmpeg configuration hash verification must remain enabled")

    if private_gates != {
        "immutable_image_verified": True,
        "configuration_snapshot_recorded": True,
        "internal_health_ready": True,
        "rollback_target_retained": True,
    }:
        fail("private release gates must protect public exposure")
    if public_canary_gates != {
        "public_health_ready": True,
        "judge_analysis_sse_result": True,
        "second_browser_entry": True,
        "concurrent_judge_requests": 3,
        "over_capacity_http_status": 429,
        "memory_pressure_observed": True,
        "rollback_smoke": True,
    }:
        fail("release gates must include public, capacity, and rollback evidence")
    if rollback != {
        "minimum_previous_versions_retained": 1,
        "configuration_snapshot_required": True,
        "switch_target": "previous-immutable-commit-version",
        "post_switch_ready_probe_required": True,
    }:
        fail("rollback contract changed without an architecture decision")

    secret_keys = plan.get("required_secret_environment_keys")
    nonsecret_keys = plan.get("required_nonsecret_environment_keys")
    if secret_keys != EXPECTED_SECRET_KEYS:
        fail("secret environment key contract changed")
    if nonsecret_keys != EXPECTED_NONSECRET_KEYS:
        fail("non-secret environment key contract changed")
    if budget.get("currency") != "CNY" or budget.get("hard_ceiling") != 300:
        fail("authorized budget ceiling must remain CNY 300")
    if budget.get("pricing_source") != (
        "https://cloud.tencent.com/document/product/1243/90265"
    ):
        fail("unexpected pricing source")
    if budget.get("assumed_ccu_price_cny_per_hour") != 0.22:
        fail("CCU price assumption must remain the conservative rounded value")
    if budget.get("ccu_per_warm_instance") != 2:
        fail("warm instance must remain two CCU")
    if budget.get("maximum_warm_hours") != 18:
        fail("warm window must remain bounded to 18 hours")
    calculated_compute_ceiling = (
        budget.get("assumed_ccu_price_cny_per_hour", 0)
        * budget.get("ccu_per_warm_instance", 0)
        * budget.get("maximum_warm_hours", 0)
    )
    if round(calculated_compute_ceiling, 2) != budget.get(
        "estimated_compute_ceiling_cny"
    ):
        fail("estimated compute ceiling does not match the pricing assumptions")
    if budget.get("estimated_compute_ceiling_cny") != 7.92:
        fail("unexpected compute cost ceiling")
    if calculated_compute_ceiling > budget["hard_ceiling"]:
        fail("estimated compute ceiling exceeds the authorized budget")
    if budget.get("maximum_public_window_hours") != 52:
        fail("public access window must remain bounded to 52 hours")
    continuous_compute_ceiling = (
        budget.get("assumed_ccu_price_cny_per_hour", 0)
        * budget.get("ccu_per_warm_instance", 0)
        * budget.get("maximum_public_window_hours", 0)
    )
    if round(continuous_compute_ceiling, 2) != budget.get(
        "estimated_continuous_compute_ceiling_cny"
    ):
        fail("continuous compute ceiling does not match the pricing assumptions")
    if budget.get("estimated_continuous_compute_ceiling_cny") != 22.88:
        fail("unexpected continuous compute cost ceiling")
    if continuous_compute_ceiling > budget["hard_ceiling"]:
        fail("continuous compute ceiling exceeds the authorized budget")
    if budget.get("confirmed_demo_end") != "2026-07-25T00:00:00+08:00":
        fail("unexpected confirmed demo end")
    if budget.get("unpriced_resources_require_console_confirmation") is not True:
        fail("unpriced resources must require console confirmation")
    if budget.get("purchase_requires_fresh_confirmation") is not True:
        fail("paid purchases must require fresh confirmation")
    if plan.get("submission_deadline") != "2026-07-23T11:00:00+08:00":
        fail("unexpected submission deadline")

    serialized = json.dumps(plan, ensure_ascii=False).lower()
    for marker in SECRET_VALUE_MARKERS:
        if marker in serialized:
            fail(f"possible secret value marker found: {marker}")


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name(
        "foundation-plan.json"
    )
    try:
        validate(load_plan(path))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"CloudBase foundation validation failed: {exc}", file=sys.stderr)
        return 1
    print(f"CloudBase foundation validation passed: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
