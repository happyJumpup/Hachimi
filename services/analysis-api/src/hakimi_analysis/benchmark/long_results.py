"""Ignored, path-free checkpoint records for the long-video A/B runner."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from hakimi_analysis.benchmark.long_models import ArmId
from hakimi_analysis.benchmark.long_scoring import LongMediaLifecycleRecord, LongVideoRun
from hakimi_analysis.benchmark.models import StrictModel

LongExpiryAuditStatus = Literal["pending", "lifecycle_failed", "provider_ttl_elapsed"]
LONG_EXPIRY_AUDIT_MARGIN_SECONDS = 300


class LongExperimentRawResult(StrictModel):
    """Resumable local results without media, transcripts, or provider responses."""

    version: Literal[1]
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    gold_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    experiment_status: Literal["completed", "incomplete", "failed", "cancelled"]
    provider_concurrency_limit: int = Field(ge=1, le=3)
    preprocessing_seconds_by_source: dict[str, float] = Field(default_factory=dict)
    runs: list[LongVideoRun] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_runs_and_safe_timings(self) -> "LongExperimentRawResult":
        if any(seconds < 0 for seconds in self.preprocessing_seconds_by_source.values()):
            raise ValueError("preprocessing durations must not be negative")
        keys = {(run.source_id, run.arm, run.run_index) for run in self.runs}
        if len(keys) != len(self.runs):
            raise ValueError("long-video raw results require unique runs")
        return self


class LongLifecycleJournalEntry(StrictModel):
    """One durable, sanitized provider-media lifecycle observation."""

    phase: Literal["preflight", "calibration", "experiment"]
    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    arm: ArmId
    run_index: int | None = Field(default=None, ge=1, le=3)
    stage: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,64}$")
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    lifecycle: LongMediaLifecycleRecord


class LongLifecycleJournalPayload(StrictModel):
    version: Literal[1] = 1
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    entries: list[LongLifecycleJournalEntry] = Field(default_factory=list)


class LongExpiryAuditReceipt(StrictModel):
    """Sanitized proof that the provider-declared TTL boundary has elapsed."""

    version: Literal[1] = 1
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    journal_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    checked_at: datetime
    margin_seconds: Literal[300] = 300
    status: LongExpiryAuditStatus
    record_count: int = Field(ge=0)
    blocking_record_count: int = Field(ge=0)
    max_expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_audit_state(self) -> "LongExpiryAuditReceipt":
        if self.checked_at.tzinfo is None:
            raise ValueError("long-video expiry audit checked_at must include a timezone")
        if self.max_expires_at is not None and self.max_expires_at.tzinfo is None:
            raise ValueError("long-video expiry audit expiry must include a timezone")
        if self.blocking_record_count > self.record_count:
            raise ValueError("long-video expiry audit blocking count is invalid")
        if self.status == "lifecycle_failed" and self.blocking_record_count == 0:
            raise ValueError("long-video failed expiry audit requires a blocking record")
        if self.status != "lifecycle_failed" and self.blocking_record_count != 0:
            raise ValueError("long-video non-failed expiry audit cannot contain blocking records")
        if self.status == "provider_ttl_elapsed" and (
            self.journal_sha256 is None
            or self.record_count == 0
            or self.max_expires_at is None
            or self.checked_at < self.max_expires_at + timedelta(seconds=self.margin_seconds)
        ):
            raise ValueError("long-video provider TTL has not safely elapsed")
        return self


class LongLifecycleJournal:
    """Atomically persists expiry metadata at each upload boundary."""

    def __init__(self, path: Path, *, manifest_sha256: str) -> None:
        self._path = path
        self._snapshot_sha256: str | None
        if path.is_file():
            try:
                serialized = path.read_bytes()
                payload = LongLifecycleJournalPayload.model_validate_json(serialized)
            except (OSError, ValueError) as error:
                raise RuntimeError("long-video lifecycle journal is invalid") from error
            if payload.manifest_sha256 != manifest_sha256:
                raise RuntimeError("long-video lifecycle journal manifest mismatch")
            self._payload = payload
            self._snapshot_sha256 = hashlib.sha256(serialized).hexdigest()
        else:
            self._payload = LongLifecycleJournalPayload(manifest_sha256=manifest_sha256)
            self._snapshot_sha256 = None

    def append(self, entry: LongLifecycleJournalEntry) -> None:
        self._payload.entries.append(entry)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        serialized = (
            json.dumps(
                self._payload.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(self._path)
        self._snapshot_sha256 = hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def snapshot(self) -> LongLifecycleJournalPayload:
        """Return a detached view so audits cannot mutate journal state."""

        return self._payload.model_copy(deep=True)

    @property
    def snapshot_sha256(self) -> str | None:
        """Hash of the exact journal bytes represented by the loaded snapshot."""

        return self._snapshot_sha256
