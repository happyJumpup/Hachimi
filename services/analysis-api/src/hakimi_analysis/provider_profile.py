from __future__ import annotations

import math
from dataclasses import dataclass

from hakimi_analysis.settings import Settings


@dataclass(frozen=True, slots=True)
class ProviderProfile:
    profile_version: str
    max_source_seconds: float
    max_source_bytes: int
    visual_chunk_seconds: float
    visual_overlap_seconds: float
    max_visual_chunks: int
    visual_attempt_timeout_seconds: float
    speech_timeout_seconds: float
    evidence_deadline_seconds: float
    run_timeout_seconds: float
    cleanup_reserve_seconds: float
    max_attempts_per_visual_provider: int
    max_visual_calls: int
    judge_concurrency: int
    public_concurrency: int

    @classmethod
    def from_settings(cls, settings: Settings) -> ProviderProfile:
        profile = cls(
            profile_version="five-minute-production-v1",
            max_source_seconds=settings.local_analysis_max_seconds,
            max_source_bytes=settings.local_upload_max_bytes,
            visual_chunk_seconds=settings.analysis_visual_chunk_seconds,
            visual_overlap_seconds=settings.analysis_visual_overlap_seconds,
            max_visual_chunks=settings.analysis_max_visual_chunks,
            visual_attempt_timeout_seconds=settings.analysis_chunk_timeout_seconds,
            speech_timeout_seconds=settings.analysis_speech_timeout_seconds,
            evidence_deadline_seconds=settings.analysis_evidence_deadline_seconds,
            run_timeout_seconds=settings.run_timeout_seconds,
            cleanup_reserve_seconds=settings.analysis_cleanup_reserve_seconds,
            max_attempts_per_visual_provider=settings.analysis_max_attempts_per_visual_provider,
            max_visual_calls=settings.analysis_max_visual_calls,
            judge_concurrency=settings.judge_analysis_concurrency,
            public_concurrency=settings.public_analysis_concurrency,
        )
        profile.validate()
        return profile

    def validate(self) -> None:
        stride = self.visual_chunk_seconds - self.visual_overlap_seconds
        required_chunks = 1 + max(
            0,
            math.ceil((self.max_source_seconds - self.visual_chunk_seconds) / stride),
        )
        if required_chunks > self.max_visual_chunks:
            raise ValueError("visual chunk budget cannot cover the maximum source duration")
        if self.max_visual_calls < required_chunks:
            raise ValueError("visual call budget cannot cover every source chunk")
        if self.max_visual_calls > (
            self.max_visual_chunks * self.max_attempts_per_visual_provider
        ):
            raise ValueError("visual call budget exceeds the declared per-provider attempts")
        if self.evidence_deadline_seconds + self.cleanup_reserve_seconds > self.run_timeout_seconds:
            raise ValueError("evidence deadline does not leave the cleanup reserve")
        if self.speech_timeout_seconds > self.evidence_deadline_seconds:
            raise ValueError("speech budget exceeds the evidence deadline")


__all__ = ["ProviderProfile"]
