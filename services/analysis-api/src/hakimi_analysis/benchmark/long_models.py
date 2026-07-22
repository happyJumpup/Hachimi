from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from hakimi_analysis.benchmark.long_contract import QWEN_VIDEO_PROJECTION_VERSION
from hakimi_analysis.benchmark.long_prompts import long_video_prompt, long_video_prompt_sha256
from hakimi_analysis.benchmark.models import StrictModel

EXPECTED_LONG_EXPERIMENT_MODELS = {
    "asr_resource": "volc.seedasr.sauc.duration",
    "seed_visual_model": "doubao-seed-2-0-mini-260428",
    "seed_fusion_model": "doubao-seed-2-0-mini-260428",
    "qwen_visual_model": "qwen3-vl-flash-2026-01-22",
}


class ArmId(StrEnum):
    SEED_CONTACT_SHEET = "seed_contact_sheet"
    QWEN_VIDEO = "qwen_video"


class LongExperimentModels(StrictModel):
    asr_resource: str = Field(min_length=1)
    seed_visual_model: str = Field(min_length=1)
    seed_fusion_model: str = Field(min_length=1)
    qwen_visual_model: str = Field(min_length=1)


class LongGoldActionContract(StrictModel):
    """Offline-only action identity contract for one reviewed source."""

    canonical_name: str = Field(min_length=1)
    accepted_aliases: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_aliases(self) -> "LongGoldActionContract":
        normalized = [value.strip().casefold() for value in self.accepted_aliases]
        if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("long-video action aliases must be non-blank and unique")
        return self


class LongGoldSourceContract(StrictModel):
    """Frozen expected unique actions; never passed to a model or executor."""

    version: Literal["long-video-unique-actions-v1"]
    actions: list[LongGoldActionContract] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_action_identities(self) -> "LongGoldSourceContract":
        canonical_names = [action.canonical_name.strip().casefold() for action in self.actions]
        if len(set(canonical_names)) != len(canonical_names):
            raise ValueError("long-video source action contracts must be unique")
        alias_owners: dict[str, str] = {}
        for action in self.actions:
            owner_name = action.canonical_name.strip().casefold()
            for alias in (action.canonical_name, *action.accepted_aliases):
                normalized_alias = alias.strip().casefold()
                owner = alias_owners.setdefault(normalized_alias, owner_name)
                if owner != owner_name:
                    raise ValueError("long-video aliases cannot cross canonical actions")
        return self


class LongVideoChunkPolicy(StrictModel):
    version: str = Field(min_length=1)
    duration_seconds: float = Field(gt=0)
    overlap_seconds: float = Field(ge=0)

    @property
    def stride_seconds(self) -> float:
        return self.duration_seconds - self.overlap_seconds

    @model_validator(mode="after")
    def validate_long_video_protocol(self) -> "LongVideoChunkPolicy":
        if self.duration_seconds != 60 or self.overlap_seconds != 10:
            raise ValueError("long experiment requires 60-second chunks with 10-second overlap")
        if self.stride_seconds <= 0:
            raise ValueError("long experiment chunk stride must be positive")
        return self


class LongExperimentRetryPolicy(StrictModel):
    version: str = Field(min_length=1)
    max_retries: int = Field(ge=0)
    retry_network_errors: bool
    retry_http_408: bool
    retry_http_429: bool
    retry_http_5xx: bool
    respect_retry_after: bool

    @model_validator(mode="after")
    def validate_long_video_protocol(self) -> "LongExperimentRetryPolicy":
        if self.max_retries != 2:
            raise ValueError("long experiment retries must be capped at two")
        if not all(
            (
                self.retry_network_errors,
                self.retry_http_408,
                self.retry_http_429,
                self.retry_http_5xx,
                self.respect_retry_after,
            )
        ):
            raise ValueError(
                "long experiment retry policy must retain all approved retry conditions"
            )
        return self


class LongExperimentOutputPaths(StrictModel):
    working_root: Path
    temporary_root: Path
    raw_results_root: Path
    summary_path: Path


class LongExperimentSource(StrictModel):
    source_id: str = Field(min_length=1)
    source_path: Path
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    duration_seconds: float = Field(gt=0, le=1800)
    representative_start_seconds: float = Field(default=0, ge=0)
    gold_path: Path
    gold_version: str = Field(min_length=1)
    gold_contract: LongGoldSourceContract

    @model_validator(mode="after")
    def validate_representative_window(self) -> "LongExperimentSource":
        if self.representative_start_seconds + 60 > self.duration_seconds + 1e-6:
            raise ValueError("representative 60-second window must fit inside the source")
        return self


class LongVideoChunk(StrictModel):
    source_id: str = Field(min_length=1)
    index: int = Field(ge=0)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    duration_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_time_range(self) -> "LongVideoChunk":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("chunk end must be after chunk start")
        if abs((self.end_seconds - self.start_seconds) - self.duration_seconds) > 1e-6:
            raise ValueError("chunk duration must equal its time range")
        return self


class LongExperimentManifest(StrictModel):
    version: Literal[1]
    models: LongExperimentModels
    prompt_version: str = Field(min_length=1)
    qwen_video_projection_version: str = Field(min_length=1)
    chunk: LongVideoChunkPolicy
    retry: LongExperimentRetryPolicy
    repetitions: int = Field(ge=1)
    sources: list[LongExperimentSource] = Field(min_length=2, max_length=2)
    output: LongExperimentOutputPaths

    @property
    def prompt_sha256(self) -> str:
        return long_video_prompt_sha256(self.prompt_version)

    @model_validator(mode="after")
    def validate_two_source_protocol(self) -> "LongExperimentManifest":
        if self.models.model_dump() != EXPECTED_LONG_EXPERIMENT_MODELS:
            raise ValueError("long experiment model IDs must exactly match the frozen protocol")
        if self.repetitions != 3:
            raise ValueError("long experiment requires exactly three repetitions")
        if self.prompt_version != "long-video-ab-v3":
            raise ValueError("long experiment requires long-video-ab-v3")
        if self.qwen_video_projection_version != QWEN_VIDEO_PROJECTION_VERSION:
            raise ValueError("long experiment requires the frozen Qwen video projection")
        long_video_prompt(self.prompt_version)
        if any(source.gold_version != "long-video-gold-v2" for source in self.sources):
            raise ValueError("long experiment requires long-video-gold-v2")
        if len({source.source_id for source in self.sources}) != len(self.sources):
            raise ValueError("long experiment source IDs must be unique")
        return self
