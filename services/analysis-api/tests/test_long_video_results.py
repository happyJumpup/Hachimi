import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from hakimi_analysis.benchmark.long_models import ArmId
from hakimi_analysis.benchmark.long_results import (
    LongExperimentRawResult,
    LongExpiryAuditReceipt,
    LongLifecycleJournal,
    LongLifecycleJournalEntry,
    LongLifecycleJournalPayload,
)
from hakimi_analysis.benchmark.long_scoring import (
    LongMediaLifecycleRecord,
    LongRunStatus,
    LongVideoRun,
)
from hakimi_analysis.benchmark.models import CleanupOutcome


def _run() -> LongVideoRun:
    return LongVideoRun(
        source_id="seven-minute-rdl",
        arm=ArmId.SEED_CONTACT_SHEET,
        run_index=1,
        status=LongRunStatus.COMPLETED,
        full_coverage=True,
        cleanup_ok=True,
        completed_seconds=1,
    )


def test_long_video_raw_result_rejects_duplicate_runs_and_negative_preprocessing() -> None:
    base = {
        "version": 1,
        "manifest_sha256": "a" * 64,
        "prompt_sha256": "b" * 64,
        "gold_sha256": "c" * 64,
        "experiment_status": "incomplete",
        "provider_concurrency_limit": 1,
    }
    with pytest.raises(ValidationError, match="unique runs"):
        LongExperimentRawResult.model_validate({**base, "runs": [_run(), _run()]})
    with pytest.raises(ValidationError, match="must not be negative"):
        LongExperimentRawResult.model_validate(
            {**base, "preprocessing_seconds_by_source": {"seven-minute-rdl": -1}}
        )


def test_lifecycle_journal_atomically_persists_only_sanitized_expiry_metadata(
    tmp_path: Path,
) -> None:
    path = tmp_path / "long-video-lifecycle.json"
    journal = LongLifecycleJournal(path, manifest_sha256="a" * 64)
    journal.append(
        LongLifecycleJournalEntry(
            phase="preflight",
            source_id="seven-minute-rdl",
            arm=ArmId.QWEN_VIDEO,
            stage="real_8_seconds",
            recorded_at=datetime.now(UTC),
            lifecycle=LongMediaLifecycleRecord(
                provider="qwen",
                model_id="qwen3-vl-flash-2026-01-22",
                media_kind="video",
                upload_seconds=1.25,
                cleanup_outcome=CleanupOutcome.EXPIRY_RECORDED,
                expires_at=(datetime.now(UTC) + timedelta(hours=48)).isoformat(),
            ),
        )
    )

    payload = LongLifecycleJournalPayload.model_validate_json(path.read_text(encoding="utf-8"))
    assert len(payload.entries) == 1
    assert payload.entries[0].lifecycle.cleanup_outcome == CleanupOutcome.EXPIRY_RECORDED
    serialized = json.dumps(payload.model_dump(mode="json"), ensure_ascii=False).lower()
    assert all(term not in serialized for term in ("oss://", "object_key", "secret", "api_key"))

    reloaded = LongLifecycleJournal(path, manifest_sha256="a" * 64)
    reloaded.append(payload.entries[0].model_copy(update={"phase": "experiment"}))
    assert (
        len(
            LongLifecycleJournalPayload.model_validate_json(
                path.read_text(encoding="utf-8")
            ).entries
        )
        == 2
    )


def test_expiry_receipt_cannot_claim_deletion_or_elapsed_before_the_margin() -> None:
    expires_at = datetime(2026, 7, 25, 12, tzinfo=UTC)
    payload = {
        "manifest_sha256": "a" * 64,
        "journal_sha256": "b" * 64,
        "checked_at": expires_at + timedelta(minutes=4),
        "margin_seconds": 300,
        "status": "provider_ttl_elapsed",
        "record_count": 1,
        "blocking_record_count": 0,
        "max_expires_at": expires_at,
    }

    with pytest.raises(ValidationError, match="TTL has not safely elapsed"):
        LongExpiryAuditReceipt.model_validate(payload)
    with pytest.raises(ValidationError):
        LongExpiryAuditReceipt.model_validate({**payload, "status": "verified_deleted"})
