import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Protocol

from hakimi_analysis.benchmark.long_execution import (
    LongCheckpointStore,
    LongExecutionError,
    deduplicate_chunk_candidates,
    normalize_chunk_candidate,
    retry_long_operation,
)
from hakimi_analysis.benchmark.long_models import ArmId, LongExperimentSource, LongVideoChunk
from hakimi_analysis.benchmark.long_prompts import (
    VISUAL_ONLY_SUFFIX,
    long_fusion_prompt,
    long_video_prompt,
)
from hakimi_analysis.benchmark.long_runtime import LongRunKey, ProviderPermits
from hakimi_analysis.benchmark.long_scoring import LongMediaLifecycleRecord
from hakimi_analysis.benchmark.models import (
    BenchmarkCandidate,
    CandidateEnvelope,
    CleanupOutcome,
    MediaHandle,
    ProviderInference,
)
from hakimi_analysis.models import Transcript


class LongExecutionFailure(LongExecutionError):
    """Safe terminal metadata retained when an arm fails after media cleanup."""

    def __init__(
        self,
        cause: Exception,
        *,
        lifecycle_audit: tuple[LongMediaLifecycleRecord, ...],
        cleanup_failed: bool,
    ) -> None:
        code = getattr(cause, "code", "provider_error")
        super().__init__(code if isinstance(code, str) else "provider_error")
        self.lifecycle_audit = lifecycle_audit
        self.cleanup_failed = cleanup_failed


@dataclass(slots=True)
class LongExecutionAudit:
    """Mutable, safe terminal metadata that survives an enclosing timeout."""

    lifecycle: list[LongMediaLifecycleRecord] = field(default_factory=list)
    cleanup_failed: bool = False


@dataclass(frozen=True, slots=True)
class LongPreparedChunk:
    chunk: LongVideoChunk
    silent_video_path: Path
    contact_sheet_path: Path
    frame_times_seconds: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class LongPreparedSource:
    source: LongExperimentSource
    audio_path: Path
    chunks: tuple[LongPreparedChunk, ...]


class LongAsrProvider(Protocol):
    async def upload(self, model_id: str, path: Path, media_kind: str) -> MediaHandle: ...

    async def transcribe(self, handle: MediaHandle, *, request_id: str) -> Transcript: ...

    async def cleanup(self, handle: MediaHandle) -> None: ...

    async def verify_cleanup(self, handle: MediaHandle) -> CleanupOutcome: ...


class LongSeedProvider(Protocol):
    async def analyze_contact_sheet(
        self,
        image_path: Path,
        *,
        frame_times_seconds: tuple[float, ...],
        window_start_seconds: float,
        window_end_seconds: float,
        prompt: str,
    ) -> ProviderInference: ...

    async def complete_text(self, prompt: str) -> ProviderInference: ...


class LongQwenProvider(Protocol):
    async def upload(self, model_id: str, path: Path, media_kind: str) -> MediaHandle: ...

    async def analyze(self, handle: MediaHandle, prompt: str) -> ProviderInference: ...

    async def cleanup(self, handle: MediaHandle) -> None: ...

    async def verify_cleanup(self, handle: MediaHandle) -> CleanupOutcome: ...


@dataclass(frozen=True, slots=True)
class LongProviderBundle:
    asr: LongAsrProvider
    seed: LongSeedProvider
    qwen: LongQwenProvider


@dataclass(frozen=True, slots=True)
class LongArmResult:
    key: LongRunKey
    candidates: tuple[BenchmarkCandidate, ...]
    completed_coverage_seconds: float
    total_seconds: float
    asr_seconds: float
    visual_seconds: float
    fusion_seconds: float
    upload_seconds: float
    cleanup_seconds: float
    first_candidate_seconds: float | None
    queue_wait_seconds: dict[str, float]
    lifecycle: tuple[LongMediaLifecycleRecord, ...]
    cleanup_failed: bool
    resumed_from_checkpoint: bool = False
    token_usage_by_provider: dict[str, tuple[int, int]] = field(default_factory=dict)
    token_usage_known: bool = False
    weight_violation_count: int = 0


class LongArmExecutor:
    """Executes one arm against a prepared full source without product API state."""

    def __init__(
        self,
        *,
        prepared_sources: Mapping[str, LongPreparedSource],
        providers: LongProviderBundle,
        asr_model_id: str,
        qwen_model_id: str,
        checkpoints: LongCheckpointStore | None = None,
        retry_delays: tuple[float, float] = (1.0, 2.0),
        task_instructions: str = long_video_prompt("long-video-ab-v1"),
        lifecycle_sink: Callable[[LongRunKey, LongMediaLifecycleRecord], None] | None = None,
    ) -> None:
        self._prepared_sources = dict(prepared_sources)
        self._providers = providers
        self._asr_model_id = asr_model_id
        self._qwen_model_id = qwen_model_id
        self._checkpoints = checkpoints
        self._retry_delays = retry_delays
        self._lifecycle_sink = lifecycle_sink
        if not task_instructions.strip():
            raise ValueError("long-video task instructions must not be empty")
        self._task_instructions = task_instructions

    async def execute(
        self,
        key: LongRunKey,
        permits: ProviderPermits,
        *,
        audit: LongExecutionAudit | None = None,
    ) -> LongArmResult:
        try:
            prepared = self._prepared_sources[key.source_id]
        except KeyError as error:
            raise LongExecutionError("prepared_source_missing") from error
        started = perf_counter()
        queue_wait_seconds: dict[str, float] = {"asr": 0.0, "seed": 0.0, "qwen": 0.0}
        lifecycle_sink = self._lifecycle_sink
        metrics = _MutableMetrics(
            audit=audit or LongExecutionAudit(),
            lifecycle_sink=(
                (lambda record: lifecycle_sink(key, record))
                if lifecycle_sink is not None
                else None
            ),
        )
        checkpointed_by_chunk = {
            item.chunk.index: self._read_checkpoint(key, item.chunk) for item in prepared.chunks
        }
        if any(value is not None for value in checkpointed_by_chunk.values()):
            metrics.resumed_from_checkpoint = True
        transcript_task: asyncio.Task[Transcript] | None = None
        if any(value is None for value in checkpointed_by_chunk.values()):
            transcript_task = asyncio.create_task(
                self._transcribe(prepared, key, permits, queue_wait_seconds, metrics)
            )
        # A source may have 23 chunks. Keep only one visual and one fusion
        # operation per arm eligible to queue at a time so the longer source
        # cannot pre-fill a shared provider queue ahead of the paired source.
        visual_slot = asyncio.Semaphore(1)
        fusion_slot = asyncio.Semaphore(1)
        first_candidate_seconds: float | None = None
        first_candidate_lock = asyncio.Lock()

        async def fuse_one(
            item: LongPreparedChunk,
            visual_candidates: list[BenchmarkCandidate],
            transcript: Transcript,
        ) -> list[BenchmarkCandidate]:
            nonlocal first_candidate_seconds
            chunk_transcript = _transcript_for_chunk(transcript, item.chunk)
            prompt = long_fusion_prompt(
                chunk_transcript.model_dump(mode="json"),
                CandidateEnvelope(actions=visual_candidates),
                source_window=(item.chunk.start_seconds, item.chunk.end_seconds),
                instructions=self._task_instructions,
            )
            fusion_started = perf_counter()
            async with fusion_slot, permits.acquire("seed") as waited:
                queue_wait_seconds["seed"] += waited
                inference = await retry_long_operation(
                    lambda: self._providers.seed.complete_text(prompt),
                    retry_delays=self._retry_delays,
                    on_retry=metrics.mark_billable_retry,
                )
            metrics.fusion_seconds += perf_counter() - fusion_started
            normalized = _normalize_inference_candidates(
                item.chunk,
                inference.envelope.actions,
                metrics,
            )
            metrics.record_inference("seed", inference)
            self._write_checkpoint(key, item.chunk, normalized)
            if normalized:
                async with first_candidate_lock:
                    if first_candidate_seconds is None:
                        first_candidate_seconds = perf_counter() - started
            return normalized

        async def run_chunk(item: LongPreparedChunk) -> list[BenchmarkCandidate]:
            checkpointed = checkpointed_by_chunk[item.chunk.index]
            if checkpointed is not None:
                return checkpointed
            async with visual_slot:
                visual_candidates = await self._visual_for_chunk(
                    prepared,
                    key,
                    item,
                    permits,
                    queue_wait_seconds,
                    metrics,
                )
            assert transcript_task is not None
            transcript = await asyncio.shield(transcript_task)
            return await fuse_one(item, visual_candidates, transcript)

        chunk_tasks = [asyncio.create_task(run_chunk(item)) for item in prepared.chunks]
        try:
            fused_by_chunk = await asyncio.gather(*chunk_tasks)
        except BaseException as error:
            if transcript_task is not None:
                transcript_task.cancel()
            for task in chunk_tasks:
                task.cancel()
            tasks_to_wait: list[asyncio.Task[object]] = [
                *chunk_tasks,
            ]
            if transcript_task is not None:
                tasks_to_wait.append(transcript_task)
            await asyncio.gather(*tasks_to_wait, return_exceptions=True)
            if isinstance(error, Exception):
                raise LongExecutionFailure(
                    error,
                    lifecycle_audit=tuple(metrics.audit.lifecycle),
                    cleanup_failed=metrics.audit.cleanup_failed,
                ) from error
            raise
        candidates = deduplicate_chunk_candidates(
            [candidate for chunk_candidates in fused_by_chunk for candidate in chunk_candidates]
        )
        return LongArmResult(
            key=key,
            candidates=tuple(candidates),
            completed_coverage_seconds=_covered_seconds(
                tuple(item.chunk for item in prepared.chunks)
            ),
            total_seconds=perf_counter() - started,
            asr_seconds=metrics.asr_seconds,
            visual_seconds=metrics.visual_seconds,
            fusion_seconds=metrics.fusion_seconds,
            upload_seconds=metrics.upload_seconds,
            cleanup_seconds=metrics.cleanup_seconds,
            first_candidate_seconds=first_candidate_seconds,
            queue_wait_seconds=queue_wait_seconds,
            lifecycle=tuple(metrics.audit.lifecycle),
            cleanup_failed=metrics.audit.cleanup_failed,
            resumed_from_checkpoint=metrics.resumed_from_checkpoint,
            token_usage_by_provider=dict(metrics.token_usage_by_provider),
            token_usage_known=(
                metrics.token_usage_known and metrics.token_usage_complete
            ),
            weight_violation_count=metrics.weight_violation_count,
        )

    async def _transcribe(
        self,
        prepared: LongPreparedSource,
        key: LongRunKey,
        permits: ProviderPermits,
        queue_wait_seconds: dict[str, float],
        metrics: "_MutableMetrics",
    ) -> Transcript:
        started = perf_counter()
        try:
            async with permits.acquire("asr") as waited:
                queue_wait_seconds["asr"] += waited
                handle = await self._providers.asr.upload(
                    self._asr_model_id,
                    prepared.audio_path,
                    "audio",
                )
                try:
                    return await retry_long_operation(
                        lambda: self._providers.asr.transcribe(
                            handle,
                            request_id=_asr_request_id(key),
                        ),
                        retry_delays=self._retry_delays,
                        on_retry=metrics.mark_billable_retry,
                    )
                finally:
                    await _finalize_provider_media(
                        self._providers.asr,
                        handle,
                        provider_name="asr",
                        model_id=self._asr_model_id,
                        media_kind="audio",
                        upload_seconds=0,
                        metrics=metrics,
                    )
        finally:
            metrics.asr_seconds += perf_counter() - started

    async def _visual_for_chunk(
        self,
        prepared: LongPreparedSource,
        key: LongRunKey,
        item: LongPreparedChunk,
        permits: ProviderPermits,
        queue_wait_seconds: dict[str, float],
        metrics: "_MutableMetrics",
    ) -> list[BenchmarkCandidate]:
        checkpointed = self._read_checkpoint(key, item.chunk)
        if checkpointed is not None:
            return checkpointed
        visual_started = perf_counter()
        if key.arm_id == ArmId.SEED_CONTACT_SHEET:
            async with permits.acquire("seed") as waited:
                queue_wait_seconds["seed"] += waited
                inference = await retry_long_operation(
                    lambda: self._providers.seed.analyze_contact_sheet(
                        item.contact_sheet_path,
                        frame_times_seconds=item.frame_times_seconds,
                        window_start_seconds=item.chunk.start_seconds,
                        window_end_seconds=item.chunk.end_seconds,
                        prompt=self._task_instructions + VISUAL_ONLY_SUFFIX,
                    ),
                    retry_delays=self._retry_delays,
                    on_retry=metrics.mark_billable_retry,
                )
        elif key.arm_id == ArmId.QWEN_VIDEO:
            inference = await self._qwen_visual_for_chunk(
                item,
                permits,
                queue_wait_seconds,
                metrics,
            )
        else:
            raise LongExecutionError("long_arm_unknown")
        if key.arm_id == ArmId.SEED_CONTACT_SHEET:
            metrics.record_inference("seed", inference)
        metrics.visual_seconds += perf_counter() - visual_started
        return _normalize_inference_candidates(
            item.chunk,
            inference.envelope.actions,
            metrics,
        )

    async def _qwen_visual_for_chunk(
        self,
        item: LongPreparedChunk,
        permits: ProviderPermits,
        queue_wait_seconds: dict[str, float],
        metrics: "_MutableMetrics",
    ) -> ProviderInference:
        async with permits.acquire("qwen") as waited:
            queue_wait_seconds["qwen"] += waited
            upload_started = perf_counter()
            # A multipart transport failure is ambiguous: OSS may have stored
            # the object even though the client never received a handle. Do not
            # replay it and risk an untracked object; the provider retries only
            # its safe policy request before this one upload attempt.
            try:
                handle = await self._providers.qwen.upload(
                    self._qwen_model_id,
                    item.silent_video_path,
                    "video",
                )
            except BaseException as error:
                upload_seconds = perf_counter() - upload_started
                metrics.upload_seconds += upload_seconds
                expires_at = _safe_expiry_from_error(error)
                if expires_at is not None:
                    metrics.record_lifecycle(
                        LongMediaLifecycleRecord(
                            provider="qwen",
                            model_id=self._qwen_model_id,
                            media_kind="video",
                            upload_seconds=upload_seconds,
                            cleanup_outcome=CleanupOutcome.EXPIRY_RECORDED,
                            expires_at=expires_at,
                        )
                    )
                raise
            upload_seconds = perf_counter() - upload_started
            metrics.upload_seconds += upload_seconds
            expires_at = _safe_expiry_value(handle.expires_at)
            try:
                if expires_at is None:
                    raise LongExecutionError("qwen_expiry_missing")
                metrics.persist_lifecycle(
                    LongMediaLifecycleRecord(
                        provider="qwen",
                        model_id=self._qwen_model_id,
                        media_kind="video",
                        upload_seconds=upload_seconds,
                        cleanup_outcome=CleanupOutcome.EXPIRY_RECORDED,
                        expires_at=expires_at,
                    )
                )
                metadata = {
                    "analysis_scope": "long_video_chunk",
                    "window": item.chunk.model_dump(
                        include={"start_seconds", "end_seconds"}, mode="json"
                    ),
                    "time_rule": "Return absolute source-video seconds within this window.",
                }
                inference = await retry_long_operation(
                    lambda: self._providers.qwen.analyze(
                        handle,
                        self._task_instructions
                        + VISUAL_ONLY_SUFFIX
                        + "\n"
                        + json.dumps(metadata, ensure_ascii=False),
                    ),
                    retry_delays=self._retry_delays,
                    on_retry=metrics.mark_billable_retry,
                )
                metrics.record_inference("qwen", inference)
                return inference
            finally:
                await _finalize_provider_media(
                    self._providers.qwen,
                    handle,
                    provider_name="qwen",
                    model_id=self._qwen_model_id,
                    media_kind="video",
                    upload_seconds=upload_seconds,
                    metrics=metrics,
                    expires_at=expires_at,
                    force_failure=expires_at is None,
                )

    def _read_checkpoint(
        self,
        key: LongRunKey,
        chunk: LongVideoChunk,
    ) -> list[BenchmarkCandidate] | None:
        if self._checkpoints is None:
            return None
        return self._checkpoints.read(key.source_id, key.arm_id.value, key.repetition, chunk)

    def _write_checkpoint(
        self,
        key: LongRunKey,
        chunk: LongVideoChunk,
        candidates: Sequence[BenchmarkCandidate],
    ) -> None:
        if self._checkpoints is not None:
            self._checkpoints.write(
                key.source_id,
                key.arm_id.value,
                key.repetition,
                chunk,
                candidates,
            )


async def _finalize_provider_media(
    provider: LongAsrProvider | LongQwenProvider,
    handle: MediaHandle,
    *,
    provider_name: str,
    model_id: str,
    media_kind: str,
    upload_seconds: float,
    metrics: "_MutableMetrics",
    expires_at: str | None = None,
    force_failure: bool = False,
) -> None:
    """Attempt cleanup and verification even while the enclosing arm is cancelled."""

    cleanup_started = perf_counter()
    outcome = CleanupOutcome.FAILED

    async def cleanup_and_verify() -> CleanupOutcome:
        cleanup_call_failed = False
        try:
            await provider.cleanup(handle)
        except BaseException:
            cleanup_call_failed = True
            metrics.audit.cleanup_failed = True
        try:
            outcome = await provider.verify_cleanup(handle)
        except BaseException:
            metrics.audit.cleanup_failed = True
            return CleanupOutcome.FAILED
        return CleanupOutcome.FAILED if cleanup_call_failed else outcome

    try:
        outcome = await _await_cleanup_completion(cleanup_and_verify())
    finally:
        if force_failure:
            outcome = CleanupOutcome.FAILED
        cleanup_seconds = perf_counter() - cleanup_started
        metrics.cleanup_seconds += cleanup_seconds
        metrics.record_lifecycle(
            LongMediaLifecycleRecord(
                provider=provider_name,
                model_id=model_id,
                media_kind=media_kind,
                upload_seconds=upload_seconds,
                cleanup_seconds=cleanup_seconds,
                cleanup_outcome=outcome,
                expires_at=expires_at,
            ),
            persist=provider_name != "qwen" or outcome == CleanupOutcome.FAILED,
        )
        metrics.audit.cleanup_failed = (
            metrics.audit.cleanup_failed or outcome == CleanupOutcome.FAILED
        )


async def _await_cleanup_completion(operation: Awaitable[CleanupOutcome]) -> CleanupOutcome:
    """Finish a cleanup coroutine before restoring any pending cancellation."""

    async def run_cleanup() -> CleanupOutcome:
        return await operation

    task: asyncio.Task[CleanupOutcome] = asyncio.create_task(run_cleanup())
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    outcome = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return outcome


@dataclass(slots=True)
class _MutableMetrics:
    audit: LongExecutionAudit = field(default_factory=LongExecutionAudit)
    lifecycle_sink: Callable[[LongMediaLifecycleRecord], None] | None = None
    asr_seconds: float = 0
    visual_seconds: float = 0
    fusion_seconds: float = 0
    upload_seconds: float = 0
    cleanup_seconds: float = 0
    resumed_from_checkpoint: bool = False
    token_usage_by_provider: dict[str, tuple[int, int]] = field(default_factory=dict)
    token_usage_known: bool = False
    token_usage_complete: bool = True
    weight_violation_count: int = 0

    @property
    def lifecycle(self) -> list[LongMediaLifecycleRecord]:
        return self.audit.lifecycle

    @property
    def cleanup_failed(self) -> bool:
        return self.audit.cleanup_failed

    @cleanup_failed.setter
    def cleanup_failed(self, value: bool) -> None:
        self.audit.cleanup_failed = value

    def record_inference(self, provider: str, inference: ProviderInference) -> None:
        if inference.input_tokens is None or inference.output_tokens is None:
            self.token_usage_complete = False
            return
        input_tokens, output_tokens = self.token_usage_by_provider.get(provider, (0, 0))
        self.token_usage_by_provider[provider] = (
            input_tokens + inference.input_tokens,
            output_tokens + inference.output_tokens,
        )
        self.token_usage_known = True

    def persist_lifecycle(self, record: LongMediaLifecycleRecord) -> None:
        if self.lifecycle_sink is not None:
            self.lifecycle_sink(record)

    def record_lifecycle(
        self,
        record: LongMediaLifecycleRecord,
        *,
        persist: bool = True,
    ) -> None:
        self.audit.lifecycle.append(record)
        if persist:
            self.persist_lifecycle(record)

    def mark_billable_retry(self) -> None:
        # A timed-out or 5xx attempt may be charged without returning usage.
        # Keep the quality result, but prevent understated cost tie-breaking.
        self.token_usage_complete = False


def _normalize_inference_candidates(
    chunk: LongVideoChunk,
    candidates: Sequence[BenchmarkCandidate],
    metrics: _MutableMetrics,
) -> list[BenchmarkCandidate]:
    """Count forbidden model weights before retaining a safe candidate copy."""

    metrics.weight_violation_count += sum(
        candidate.weight_kg is not None for candidate in candidates
    )
    return [normalize_chunk_candidate(chunk, candidate) for candidate in candidates]


def _safe_expiry_from_error(error: BaseException) -> str | None:
    return _safe_expiry_value(getattr(error, "expires_at", None))


def _safe_expiry_value(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    normalized = parsed.astimezone(UTC)
    if normalized <= datetime.now(UTC):
        return None
    return normalized.isoformat()


def _asr_request_id(key: LongRunKey) -> str:
    """Produce a short, opaque, deterministic ID for one ASR socket session."""
    identity = f"{key.source_id}\0{key.arm_id.value}\0{key.repetition}".encode()
    return f"long-{hashlib.sha256(identity).hexdigest()[:32]}"


def _transcript_for_chunk(transcript: Transcript, chunk: LongVideoChunk) -> Transcript:
    utterances = [
        utterance
        for utterance in transcript.utterances
        if utterance.end_seconds >= chunk.start_seconds
        and utterance.start_seconds <= chunk.end_seconds
    ]
    return Transcript(
        text=" ".join(utterance.text for utterance in utterances),
        utterances=utterances,
        provider_request_id=transcript.provider_request_id,
    )


def _covered_seconds(chunks: tuple[LongVideoChunk, ...]) -> float:
    """Return union coverage; fixed overlaps must not inflate completion."""
    if not chunks:
        return 0
    ordered = sorted(chunks, key=lambda item: (item.start_seconds, item.end_seconds))
    covered = 0.0
    current_start = ordered[0].start_seconds
    current_end = ordered[0].end_seconds
    for chunk in ordered[1:]:
        if chunk.start_seconds <= current_end:
            current_end = max(current_end, chunk.end_seconds)
            continue
        covered += current_end - current_start
        current_start = chunk.start_seconds
        current_end = chunk.end_seconds
    return covered + current_end - current_start
