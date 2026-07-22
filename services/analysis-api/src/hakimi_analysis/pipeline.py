import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from hakimi_analysis.models import (
    AnalysisCandidate,
    AnalysisWarning,
    CoverageGap,
    CoverageStatus,
    RunStage,
)
from hakimi_analysis.sources import VideoSource

EmitCallback = Callable[[RunStage, str, dict[str, object]], Awaitable[None]]


@dataclass(slots=True)
class PipelineOutput:
    candidates: list[AnalysisCandidate] = field(default_factory=list)
    warnings: list[AnalysisWarning] = field(default_factory=list)
    empty_reason: Literal["no_evidence", "insufficient_evidence"] | None = None
    coverage_status: CoverageStatus = CoverageStatus.COMPLETE
    coverage_gaps: list[CoverageGap] = field(default_factory=list)
    processed_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.processed_seconds is not None and (
            not math.isfinite(self.processed_seconds) or self.processed_seconds < 0
        ):
            raise ValueError("processed_seconds must be finite and not negative")
        if self.coverage_status == CoverageStatus.COMPLETE and self.coverage_gaps:
            raise ValueError("complete pipeline output cannot contain coverage gaps")
        if self.coverage_status in {CoverageStatus.PARTIAL, CoverageStatus.INSUFFICIENT} and (
            not self.coverage_gaps or self.processed_seconds is None
        ):
            raise ValueError("incomplete pipeline output requires gaps and processed_seconds")
        if self.coverage_status == CoverageStatus.INSUFFICIENT and (
            self.candidates or self.empty_reason != "insufficient_evidence"
        ):
            raise ValueError("insufficient pipeline output cannot contain candidates")
        for index in range(1, len(self.coverage_gaps)):
            previous = self.coverage_gaps[index - 1]
            current = self.coverage_gaps[index]
            if current.start_seconds < previous.end_seconds:
                raise ValueError("coverage gaps must be sorted and non-overlapping")


class AnalysisPipeline(Protocol):
    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float | None,
        emit: EmitCallback,
    ) -> PipelineOutput: ...


class PipelineFailure(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
