"""Sanitized, aggregate-only reports for the long-video A/B experiment."""

import json
import re
from collections.abc import Mapping, Sequence
from math import isfinite

from hakimi_analysis.benchmark.long_scoring import LongArmAggregate, LongVideoDecision

_COMPARISON_ARM = {
    "seed_contact_sheet": "A",
    "qwen_video": "B",
}

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SAFE_PROTOCOL_NUMBERS = {
    "chunk_seconds",
    "overlap_seconds",
    "repetitions_per_source_arm",
    "retry_limit",
}
_SAFE_PROTOCOL_IDENTIFIERS = {
    "prompt_version",
    "prompt_sha256",
    "gold_version",
    "asr_model_id",
    "asr_resource",
    "seed_visual_model_id",
    "seed_visual_model",
    "qwen_visual_model_id",
    "qwen_visual_model",
    "fusion_model_id",
    "seed_fusion_model",
    "scheduler_version",
    "chunk_policy_version",
    "retry_policy_version",
}
_SAFE_DIAGNOSTIC_VALUES = {
    "proxy_mode": {"explicit-socks5h", "direct", "disabled"},
    "dns_mode": {"proxy", "system", "fake-ip-guarded"},
    "preflight_status": {"passed", "failed", "not_run"},
    "experiment_status": {"completed", "incomplete", "failed", "cancelled"},
}
_SAFE_DIAGNOSTIC_NUMBERS = {"provider_concurrency_limit"}


def _safe_protocol(protocol: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key in sorted(_SAFE_PROTOCOL_NUMBERS):
        value = protocol.get(key)
        if (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and isfinite(value)
            and value >= 0
        ):
            safe[key] = value
    for key in sorted(_SAFE_PROTOCOL_IDENTIFIERS):
        value = protocol.get(key)
        if isinstance(value, str) and _SAFE_IDENTIFIER.fullmatch(value):
            safe[key] = value
    return safe


def _safe_diagnostics(diagnostics: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, allowed_values in _SAFE_DIAGNOSTIC_VALUES.items():
        value = diagnostics.get(key)
        if isinstance(value, str) and value in allowed_values:
            safe[key] = value
    for key in _SAFE_DIAGNOSTIC_NUMBERS:
        value = diagnostics.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 3:
            safe[key] = value
    return safe


def sanitized_long_video_report(
    aggregates: Sequence[LongArmAggregate],
    decision: LongVideoDecision,
    *,
    protocol: Mapping[str, object],
    diagnostics: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Return a report that cannot contain media content or provider output.

    Callers can only supply aggregate models plus a narrow allow-list of public
    protocol and environment diagnostics. Unknown fields are deliberately
    discarded rather than recursively redacted.
    """

    return {
        "version": 1,
        "scope": "local_long_video_quality_only",
        # A local winner is only a research result. Every comparison includes
        # Qwen temporary OSS objects, whose provider-managed 48-hour expiry is
        # recorded but cannot be immediately verified through this API.
        "promotion_status": "blocked_pending_qwen_48h_expiry_audit",
        "protocol": _safe_protocol(protocol),
        "arms": [
            {
                "comparison_arm": _COMPARISON_ARM[aggregate.arm.value],
                **aggregate.model_dump(mode="json"),
            }
            for aggregate in aggregates
        ],
        "decision": decision.model_dump(mode="json"),
        "diagnostics": _safe_diagnostics(diagnostics or {}),
    }


def render_long_video_report(
    aggregates: Sequence[LongArmAggregate],
    decision: LongVideoDecision,
    *,
    protocol: Mapping[str, object],
    diagnostics: Mapping[str, object] | None = None,
) -> str:
    """Render a deterministic JSON artifact from the sanitized report only."""

    return (
        json.dumps(
            sanitized_long_video_report(
                aggregates,
                decision,
                protocol=protocol,
                diagnostics=diagnostics,
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
