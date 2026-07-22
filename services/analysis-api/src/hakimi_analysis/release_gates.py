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
    "three_way_concurrency_passed",
    "cancellation_cleanup_passed",
    "circuit_breaker_passed",
    "cos_cleanup_passed",
    "rollback_sequence",
    "published_max_seconds",
}


def validate_five_minute_canary_receipt(
    path: Path | None,
    *,
    expected_commit_sha: str,
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
    )


def validate_five_minute_canary_receipt_json(
    payload: str,
    *,
    expected_commit_sha: str,
) -> bool:
    if re.fullmatch(r"[0-9a-f]{40}", expected_commit_sha) is None:
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
        and raw["three_way_concurrency_passed"] is True
        and raw["cancellation_cleanup_passed"] is True
        and raw["circuit_breaker_passed"] is True
        and raw["cos_cleanup_passed"] is True
        and raw["rollback_sequence"] == "B-C-B-C"
        and raw["published_max_seconds"] == 300
    )


__all__ = [
    "validate_five_minute_canary_receipt",
    "validate_five_minute_canary_receipt_json",
]
