from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class RouteId(StrEnum):
    SEED_AV = "S-AV"
    SEED_MODULAR = "S-MOD"
    QWEN_AV = "Q-AV"
    QWEN_MODULAR = "Q-MOD"
    ASR_TEXT = "A-ASR"
    SEED_AUDIO = "A-SEED"
    QWEN_AUDIO = "A-QWEN"


class EventKind(StrEnum):
    ACTION = "action"
    REST = "rest"
    EXPLANATION = "explanation"
    TRANSITION = "transition"


class EvidenceChannel(StrEnum):
    AUDIO = "audio"
    VISUAL = "visual"


class GoldStatus(StrEnum):
    DRAFT = "draft"
    REVIEWED = "reviewed"


class BenchmarkRunStatus(StrEnum):
    COMPLETED = "completed"
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"
    SCHEMA_ERROR = "schema_error"


class CleanupPolicy(StrEnum):
    DELETE_AND_VERIFY = "delete_and_verify"
    EXPIRE_AUTOMATICALLY = "expire_automatically"
    LOCAL_RELEASE = "local_release"


class CleanupOutcome(StrEnum):
    DELETED_VERIFIED = "deleted_verified"
    EXPIRY_RECORDED = "retained_until_expiry_recorded"
    LOCAL_RELEASED = "local_released"
    FAILED = "failed"


class ParameterName(StrEnum):
    SETS = "sets"
    REPS = "reps"
    DURATION_SECONDS = "duration_seconds"
    REST_SECONDS = "rest_seconds"


class BenchmarkCandidate(StrictModel):
    action_name: str = Field(min_length=1)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    kind: EventKind
    sets: int | None = Field(default=None, ge=1)
    reps: int | None = Field(default=None, ge=1)
    duration_seconds: int | None = Field(default=None, ge=1)
    rest_seconds: int | None = Field(default=None, ge=0)
    weight_kg: float | None = Field(default=None, gt=0)
    evidence_channels: list[EvidenceChannel] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_segment(self) -> "BenchmarkCandidate":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("candidate end must be after start")
        return self


class GoldEvent(StrictModel):
    canonical_name: str = Field(min_length=1)
    accepted_aliases: list[str] = Field(min_length=1)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    kind: EventKind
    sets: int | None = Field(default=None, ge=1)
    reps: int | None = Field(default=None, ge=1)
    duration_seconds: int | None = Field(default=None, ge=1)
    rest_seconds: int | None = Field(default=None, ge=0)
    weight_kg: Literal[None] = None
    evidence_channels: list[EvidenceChannel] = Field(min_length=1)
    uncertain_parameters: list[ParameterName] = Field(default_factory=list)
    notes: str | None = None

    @model_validator(mode="after")
    def validate_event(self) -> "GoldEvent":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("gold event end must be after start")
        if any(not alias.strip() for alias in self.accepted_aliases):
            raise ValueError("gold aliases must not be blank")
        if len(set(self.uncertain_parameters)) != len(self.uncertain_parameters):
            raise ValueError("uncertain parameters must be unique")
        if any(getattr(self, field.value) is not None for field in self.uncertain_parameters):
            raise ValueError("uncertain parameters cannot have explicit values")
        return self


class GoldAnnotation(StrictModel):
    version: Literal[1]
    sample_id: str = Field(min_length=1)
    status: GoldStatus
    reviewed_by: list[str] = Field(default_factory=list)
    events: list[GoldEvent]

    @model_validator(mode="after")
    def validate_review(self) -> "GoldAnnotation":
        if self.status == GoldStatus.REVIEWED and not self.reviewed_by:
            raise ValueError("reviewed gold requires at least one reviewer")
        return self


class BenchmarkSample(StrictModel):
    sample_id: str = Field(min_length=1)
    source_path: Path
    window_start_seconds: float = Field(ge=0)
    duration_seconds: float = Field(gt=0, le=60)
    av_path: Path
    silent_video_path: Path
    audio_path: Path
    av_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    silent_video_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    audio_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    gold_path: Path
    gold_status: GoldStatus


class RunUnit(StrictModel):
    sample_id: str
    route_id: RouteId
    run_index: int = Field(ge=1, le=3)


class MediaHandle(StrictModel):
    provider: str
    model_id: str
    media_kind: str
    handle_id: str
    cleanup_policy: CleanupPolicy
    expires_at: str | None = None


class BenchmarkRunResult(StrictModel):
    sample_id: str
    route_id: RouteId
    run_index: int
    status: BenchmarkRunStatus = BenchmarkRunStatus.COMPLETED
    error_code: str | None = None
    candidates: list[BenchmarkCandidate] = Field(default_factory=list)
    upload_seconds: float = Field(default=0, ge=0)
    inference_seconds: float = Field(ge=0)
    preprocessing_seconds: float | None = Field(default=None, ge=0)
    first_response_seconds: float | None = Field(default=None, ge=0)
    first_parseable_candidate_seconds: float | None = Field(default=None, ge=0)
    final_result_seconds: float | None = Field(default=None, ge=0)
    fusion_seconds: float | None = Field(default=None, ge=0)
    provider_request_id: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    visual_control: list[BenchmarkCandidate] | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> "BenchmarkRunResult":
        if self.status == BenchmarkRunStatus.COMPLETED and self.error_code is not None:
            raise ValueError("completed benchmark result cannot include an error")
        if self.status != BenchmarkRunStatus.COMPLETED:
            if self.error_code is None:
                raise ValueError("failed benchmark result requires an error code")
            if self.candidates or self.visual_control:
                raise ValueError("failed benchmark result cannot include candidates")
        return self


class RouteExecution(StrictModel):
    candidates: list[BenchmarkCandidate]
    visual_control: list[BenchmarkCandidate] | None = None
    provider_request_id: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    preprocessing_seconds: float | None = Field(default=None, ge=0)
    first_response_seconds: float | None = Field(default=None, ge=0)
    first_parseable_candidate_seconds: float | None = Field(default=None, ge=0)
    final_result_seconds: float | None = Field(default=None, ge=0)
    fusion_seconds: float | None = Field(default=None, ge=0)


class MediaLifecycleRecord(StrictModel):
    provider: str
    model_id: str
    media_kind: str
    upload_seconds: float = Field(ge=0)
    cleanup_seconds: float = Field(default=0, ge=0)
    cleanup_outcome: CleanupOutcome | None = None


class CandidateEnvelope(StrictModel):
    actions: list[BenchmarkCandidate]


class ProviderInference(StrictModel):
    envelope: CandidateEnvelope
    request_id: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    first_response_seconds: float | None = Field(default=None, ge=0)
    final_response_seconds: float | None = Field(default=None, ge=0)


class MatchCounts(StrictModel):
    true_positives: int = Field(ge=0)
    false_positives: int = Field(ge=0)
    false_negatives: int = Field(ge=0)
    precision: float = Field(ge=0, le=1)
    recall: float = Field(ge=0, le=1)
    f1: float = Field(ge=0, le=1)


class CandidateScore(StrictModel):
    tiou_03: MatchCounts
    tiou_05: MatchCounts
    mean_start_error_seconds: float | None = Field(default=None, ge=0)
    mean_end_error_seconds: float | None = Field(default=None, ge=0)
    tail_recall: float | None = Field(default=None, ge=0, le=1)
    duplicate_count: int = Field(ge=0)
    fragment_count: int = Field(ge=0)
    reported_parameter_count: int = Field(ge=0)
    supported_parameter_count: int = Field(ge=0)
    correct_parameter_count: int = Field(ge=0)
    parameter_accuracy: float = Field(ge=0, le=1)
    parameter_supported_count_by_name: dict[ParameterName, int]
    parameter_correct_count_by_name: dict[ParameterName, int]
    unsupported_parameter_count: int = Field(ge=0)
    unsupported_parameter_rate: float = Field(ge=0, le=1)
    weight_violation_count: int = Field(ge=0)


class RouteAggregate(StrictModel):
    route_id: RouteId
    scored_units: int = Field(ge=0)
    precision: float = Field(ge=0, le=1)
    recall: float = Field(ge=0, le=1)
    precision_tiou_03: float = Field(ge=0, le=1)
    recall_tiou_03: float = Field(ge=0, le=1)
    f1_tiou_03: float = Field(ge=0, le=1)
    f1_tiou_05: float = Field(ge=0, le=1)
    mean_start_error_seconds: float | None = Field(default=None, ge=0)
    mean_end_error_seconds: float | None = Field(default=None, ge=0)
    tail_recall: float | None = Field(default=None, ge=0, le=1)
    duplicate_count: int = Field(ge=0)
    fragment_count: int = Field(ge=0)
    parameter_accuracy: float = Field(ge=0, le=1)
    parameter_accuracy_by_name: dict[ParameterName, float | None]
    median_inference_seconds: float = Field(ge=0)
    inference_seconds_range: tuple[float, float]
    unsupported_parameter_rate: float = Field(ge=0, le=1)
    weight_violation_count: int = Field(ge=0)
    schema_violation_count: int = Field(ge=0)
    cleanup_violation_count: int = Field(ge=0)
    eligible: bool
    stability_f1: float | None = Field(default=None, ge=0, le=1)
    action_count_range: tuple[int, int] | None = None
    gold_f1_range: float | None = Field(default=None, ge=0, le=1)
    total_input_tokens: int | None = Field(default=None, ge=0)
    total_output_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_cny: float | None = Field(default=None, ge=0)
