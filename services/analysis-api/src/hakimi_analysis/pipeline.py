from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from hakimi_analysis.models import AnalysisCandidate, AnalysisWarning, RunStage
from hakimi_analysis.sources import VideoSource

EmitCallback = Callable[[RunStage, str, dict[str, object]], Awaitable[None]]


@dataclass(slots=True)
class PipelineOutput:
    candidates: list[AnalysisCandidate] = field(default_factory=list)
    warnings: list[AnalysisWarning] = field(default_factory=list)
    empty_reason: Literal["no_evidence"] | None = None


class AnalysisPipeline(Protocol):
    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float,
        emit: EmitCallback,
    ) -> PipelineOutput: ...


class PipelineFailure(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
