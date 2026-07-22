import json
from pathlib import Path

from hakimi_analysis.release_gates import (
    validate_five_minute_canary_receipt,
    validate_five_minute_canary_receipt_json,
)


def write_receipt(path: Path, commit_sha: str) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profile_version": "five-minute-production-v1",
                "commit_sha": commit_sha,
                "completed_at": "2026-07-22T01:00:00Z",
                "private_smoke_passed": True,
                "three_way_concurrency_passed": True,
                "cancellation_cleanup_passed": True,
                "circuit_breaker_passed": True,
                "cos_cleanup_passed": True,
                "rollback_sequence": "B-C-B-C",
                "published_max_seconds": 300,
            }
        ),
        encoding="utf-8",
    )


def test_five_minute_capability_requires_receipt_for_the_deployed_commit(
    tmp_path: Path,
) -> None:
    receipt = tmp_path / "provider-canary.json"
    commit_sha = "a" * 40
    write_receipt(receipt, commit_sha)

    assert validate_five_minute_canary_receipt(
        receipt,
        expected_commit_sha=commit_sha,
    )
    assert not validate_five_minute_canary_receipt(
        receipt,
        expected_commit_sha="b" * 40,
    )
    assert validate_five_minute_canary_receipt_json(
        receipt.read_text(encoding="utf-8"),
        expected_commit_sha=commit_sha,
    )


def test_canary_receipt_rejects_an_incomplete_rollback(tmp_path: Path) -> None:
    receipt = tmp_path / "provider-canary.json"
    commit_sha = "a" * 40
    write_receipt(receipt, commit_sha)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["rollback_sequence"] = "B-C"
    receipt.write_text(json.dumps(payload), encoding="utf-8")

    assert not validate_five_minute_canary_receipt(
        receipt,
        expected_commit_sha=commit_sha,
    )
