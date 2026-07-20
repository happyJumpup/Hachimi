from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApiErrorResponse(StrictModel):
    detail: str


class ReadyResponse(StrictModel):
    status: Literal["ready"]


class NotReadyResponse(StrictModel):
    status: Literal["not_ready"]
    code: str = Field(min_length=1)


class ActionMode(StrEnum):
    REPS = "reps"
    DURATION = "duration"


class EvidenceType(StrEnum):
    SPEECH = "speech"
    VISUAL = "visual"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AccessTier(StrEnum):
    PUBLIC = "public"
    JUDGE = "judge"


class RunStage(StrEnum):
    QUEUED = "queued"
    PREPARING_MEDIA = "preparing_media"
    ANALYZING_EVIDENCE = "analyzing_evidence"
    EXPANDING_WINDOW = "expanding_window"
    FUSING_CANDIDATES = "fusing_candidates"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ErrorCode(StrEnum):
    CONFIGURATION_ERROR = "configuration_error"
    PROVIDER_ERROR = "provider_error"
    SCHEMA_ERROR = "schema_error"
    MEDIA_ERROR = "media_error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class SourceSummary(StrictModel):
    id: str
    title: str
    media_url: str
    duration_seconds: float
    origin_url: str | None = None


class Segment(StrictModel):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_order(self) -> "Segment":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("segment end must be after start")
        return self


class EvidenceSpan(Segment):
    type: EvidenceType


class CandidateParameters(StrictModel):
    mode: ActionMode | None = None
    sets: int | None = Field(default=None, ge=1)
    reps: int | None = Field(default=None, ge=1)
    duration_seconds: int | None = Field(default=None, ge=1)
    rest_seconds: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_mode(self) -> "CandidateParameters":
        if self.mode == ActionMode.REPS and self.duration_seconds is not None:
            raise ValueError("reps mode cannot include duration_seconds")
        if self.mode == ActionMode.DURATION and self.reps is not None:
            raise ValueError("duration mode cannot include reps")
        return self


class AnalysisCandidate(StrictModel):
    id: str
    name: str = Field(min_length=1)
    source_id: str
    segment: Segment | None
    parameters: CandidateParameters
    evidence: list[EvidenceSpan]
    needs_confirmation: bool


class SpeechSignal(StrictModel):
    action_name: str = Field(min_length=1)
    sets: int | None = Field(default=None, ge=1)
    reps: int | None = Field(default=None, ge=1)
    duration_seconds: int | None = Field(default=None, ge=1)
    rest_seconds: int | None = Field(default=None, ge=0)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    evidence_text: str = Field(min_length=1)


class SpeechUnderstandingResult(StrictModel):
    signals: list[SpeechSignal]


class VisualSegment(StrictModel):
    action_name: str | None = None
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    visual_cue: str = Field(min_length=1)


class VisualLocalizationResult(StrictModel):
    segments: list[VisualSegment]


class TranscriptWord(StrictModel):
    text: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)


class TranscriptUtterance(StrictModel):
    text: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    words: list[TranscriptWord] = Field(default_factory=list)


class Transcript(StrictModel):
    text: str
    utterances: list[TranscriptUtterance]
    provider_request_id: str | None = None


class CreateAnalysisRunRequest(StrictModel):
    source_id: str = Field(min_length=1, max_length=128)
    trigger_seconds: float = Field(ge=0)


class UpgradeAccessSessionRequest(StrictModel):
    access_code: str = Field(min_length=1, max_length=256)


class AccessSessionView(StrictModel):
    tier: AccessTier
    can_analyze: bool
    retry_after_seconds: int | None = Field(default=None, ge=1)


class AnalysisWarning(StrictModel):
    code: Literal["speech_unavailable", "visual_unavailable"]
    message: str


class AnalysisError(StrictModel):
    code: ErrorCode
    message: str
    retryable: bool


class AnalysisRunView(StrictModel):
    id: str
    source_id: str
    trigger_seconds: float
    status: RunStatus
    stage: RunStage
    candidates: list[AnalysisCandidate] = Field(default_factory=list)
    warnings: list[AnalysisWarning] = Field(default_factory=list)
    empty_reason: Literal["no_evidence"] | None = None
    error: AnalysisError | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AnalysisEvent(StrictModel):
    sequence: int
    type: str
    run_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    data: dict[str, Any] = Field(default_factory=dict)
