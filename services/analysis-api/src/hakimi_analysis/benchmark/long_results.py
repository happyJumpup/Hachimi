"""Ignored, path-free checkpoint records for the long-video A/B runner."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from hakimi_analysis.benchmark.long_models import ArmId
from hakimi_analysis.benchmark.long_scoring import LongMediaLifecycleRecord, LongVideoRun
from hakimi_analysis.benchmark.models import StrictModel


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


class LongLifecycleJournal:
    """Atomically persists expiry metadata at each upload boundary."""

    def __init__(self, path: Path, *, manifest_sha256: str) -> None:
        self._path = path
        if path.is_file():
            try:
                payload = LongLifecycleJournalPayload.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError) as error:
                raise RuntimeError("long-video lifecycle journal is invalid") from error
            if payload.manifest_sha256 != manifest_sha256:
                raise RuntimeError("long-video lifecycle journal manifest mismatch")
            self._payload = payload
        else:
            self._payload = LongLifecycleJournalPayload(manifest_sha256=manifest_sha256)

    def append(self, entry: LongLifecycleJournalEntry) -> None:
        self._payload.entries.append(entry)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                self._payload.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(self._path)
