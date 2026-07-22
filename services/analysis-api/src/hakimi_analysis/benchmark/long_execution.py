import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Iterable, Sequence
from pathlib import Path

from hakimi_analysis.benchmark.long_contract import MAX_LONG_PRIMARY_DEMO_SECONDS
from hakimi_analysis.benchmark.long_models import LongVideoChunk
from hakimi_analysis.benchmark.models import BenchmarkCandidate


class LongExecutionError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def normalize_chunk_candidate(
    chunk: LongVideoChunk,
    candidate: BenchmarkCandidate,
) -> BenchmarkCandidate:
    """Validate the frozen source-absolute clock contract for one chunk."""

    # The frozen visual and fusion prompts require source-video absolute
    # seconds.  Inferring a relative clock from its numeric range is unsafe:
    # e.g. 55-60 is a valid absolute event inside the 50-110 second chunk.
    absolute = (
        candidate.start_seconds >= chunk.start_seconds - 1e-6
        and candidate.end_seconds <= chunk.end_seconds + 1e-6
    )
    if absolute:
        return candidate.model_copy(update={"weight_kg": None})
    raise LongExecutionError("chunk_candidate_time_invalid")


def deduplicate_chunk_candidates(
    candidates: Sequence[BenchmarkCandidate],
) -> list[BenchmarkCandidate]:
    """Merge same-action intervals that physically overlap across chunk boundaries."""
    ordered = sorted(
        candidates,
        key=lambda candidate: (
            candidate.start_seconds,
            candidate.end_seconds,
            _normalized_name(candidate.action_name),
        ),
    )
    by_identity: dict[tuple[object, str], list[BenchmarkCandidate]] = {}
    for candidate in ordered:
        candidate = candidate.model_copy(update={"weight_kg": None})
        identity = (candidate.kind, _normalized_name(candidate.action_name))
        active = by_identity.setdefault(identity, [])
        index = 0
        while index < len(active):
            existing = active[index]
            if not _should_merge(existing, candidate):
                index += 1
                continue
            candidate = _merge_candidates(existing, candidate)
            active.pop(index)
        active.append(candidate)
    return sorted(
        (candidate for group in by_identity.values() for candidate in group),
        key=lambda candidate: (
            candidate.start_seconds,
            candidate.end_seconds,
            _normalized_name(candidate.action_name),
        ),
    )


def consolidate_unique_action_candidates(
    candidates: Sequence[BenchmarkCandidate],
) -> list[BenchmarkCandidate]:
    """Keep one primary demonstration for each distinct trainable action.

    The v2 long-video study evaluates product-level actions rather than every
    repeated demonstration. Exact normalized names are consolidated locally;
    semantic alias resolution remains the fusion model's responsibility.
    """

    deduplicated = deduplicate_chunk_candidates(candidates)
    action_groups: dict[str, list[BenchmarkCandidate]] = {}
    non_actions: list[BenchmarkCandidate] = []
    for candidate in deduplicated:
        if candidate.kind.value != "action":
            non_actions.append(candidate)
            continue
        if candidate.end_seconds - candidate.start_seconds > MAX_LONG_PRIMARY_DEMO_SECONDS:
            continue
        action_groups.setdefault(_normalized_name(candidate.action_name), []).append(candidate)

    consolidated = [_consolidate_action_group(group) for group in action_groups.values()]
    return sorted(
        [*consolidated, *non_actions],
        key=lambda candidate: (
            candidate.start_seconds,
            candidate.end_seconds,
            _normalized_name(candidate.action_name),
        ),
    )


async def retry_long_operation[OperationValue](
    operation: Callable[[], Awaitable[OperationValue]],
    *,
    retry_delays: tuple[float, float] = (1.0, 2.0),
    on_retry: Callable[[], None] | None = None,
) -> OperationValue:
    """Retry only explicitly retryable transport errors, at most two times."""
    for attempt in range(len(retry_delays) + 1):
        try:
            return await operation()
        except Exception as error:
            retryable = getattr(error, "retryable", False)
            if not isinstance(retryable, bool) or not retryable or attempt == len(retry_delays):
                raise
            retry_after_seconds = getattr(error, "retry_after_seconds", None)
            delay = retry_delays[attempt]
            if isinstance(retry_after_seconds, int | float):
                delay = max(delay, float(retry_after_seconds))
            if on_retry is not None:
                on_retry()
            await asyncio.sleep(delay)
    raise AssertionError("retry loop exhausted")


class LongCheckpointStore:
    """Stores only normalized candidate checkpoints under an ignored work root."""

    def __init__(self, root: Path, *, manifest_sha256: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256):
            raise ValueError("manifest hash must be a SHA-256 hex digest")
        self._root = root
        self._manifest_sha256 = manifest_sha256

    def read(
        self,
        source_id: str,
        arm_id: str,
        repetition: int,
        chunk: LongVideoChunk,
    ) -> list[BenchmarkCandidate] | None:
        path = self._path(source_id, arm_id, repetition, chunk)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("manifest_sha256") != self._manifest_sha256:
                return None
            if payload.get("source_id") != source_id or payload.get("chunk_index") != chunk.index:
                return None
            values = payload.get("candidates")
            if not isinstance(values, list):
                return None
            return [BenchmarkCandidate.model_validate(value) for value in values]
        except (OSError, TypeError, ValueError):
            return None

    def write(
        self,
        source_id: str,
        arm_id: str,
        repetition: int,
        chunk: LongVideoChunk,
        candidates: Sequence[BenchmarkCandidate],
    ) -> None:
        path = self._path(source_id, arm_id, repetition, chunk)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "manifest_sha256": self._manifest_sha256,
            "source_id": source_id,
            "arm_id": arm_id,
            "repetition": repetition,
            "chunk_index": chunk.index,
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    def _path(
        self,
        source_id: str,
        arm_id: str,
        repetition: int,
        chunk: LongVideoChunk,
    ) -> Path:
        for value in (source_id, arm_id):
            if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
                raise ValueError("checkpoint identifiers must be path-safe")
        if repetition < 1:
            raise ValueError("checkpoint repetition must be positive")
        return self._root / source_id / arm_id / str(repetition) / f"chunk-{chunk.index:03d}.json"


def _normalized_name(value: str) -> str:
    return re.sub(r"[^\w]", "", value.casefold(), flags=re.UNICODE)


def _should_merge(left: BenchmarkCandidate, right: BenchmarkCandidate) -> bool:
    if left.kind != right.kind or _normalized_name(left.action_name) != _normalized_name(
        right.action_name
    ):
        return False
    return max(left.start_seconds, right.start_seconds) <= min(
        left.end_seconds,
        right.end_seconds,
    )


def _merge_candidates(
    left: BenchmarkCandidate,
    right: BenchmarkCandidate,
) -> BenchmarkCandidate:
    channels = sorted(
        set(left.evidence_channels) | set(right.evidence_channels),
        key=lambda channel: channel.value,
    )
    return left.model_copy(
        update={
            "start_seconds": min(left.start_seconds, right.start_seconds),
            "end_seconds": max(left.end_seconds, right.end_seconds),
            "sets": _shared_value(left.sets, right.sets),
            "reps": _shared_value(left.reps, right.reps),
            "duration_seconds": _shared_value(left.duration_seconds, right.duration_seconds),
            "rest_seconds": _shared_value(left.rest_seconds, right.rest_seconds),
            "weight_kg": None,
            "evidence_channels": list(channels),
        }
    )


def _consolidate_action_group(
    candidates: Sequence[BenchmarkCandidate],
) -> BenchmarkCandidate:
    representative = max(
        candidates,
        key=_primary_demo_rank,
    )
    channels = sorted(
        {channel for candidate in candidates for channel in candidate.evidence_channels},
        key=lambda channel: channel.value,
    )
    return representative.model_copy(
        update={
            "sets": _non_conflicting_value(candidate.sets for candidate in candidates),
            "reps": _non_conflicting_value(candidate.reps for candidate in candidates),
            "duration_seconds": _non_conflicting_value(
                candidate.duration_seconds for candidate in candidates
            ),
            "rest_seconds": _non_conflicting_value(
                candidate.rest_seconds for candidate in candidates
            ),
            "weight_kg": None,
            "evidence_channels": channels,
        }
    )


def _primary_demo_rank(candidate: BenchmarkCandidate) -> tuple[int, int, float, float]:
    """Prefer the most complete evidence-rich segment inside the v2 duration bound."""

    segment_seconds = candidate.end_seconds - candidate.start_seconds
    parameter_count = sum(
        value is not None
        for value in (
            candidate.sets,
            candidate.reps,
            candidate.duration_seconds,
            candidate.rest_seconds,
        )
    )
    return (
        len(set(candidate.evidence_channels)),
        parameter_count,
        segment_seconds,
        -candidate.start_seconds,
    )


def _shared_value[T](left: T | None, right: T | None) -> T | None:
    return left if left == right else None


def _non_conflicting_value[T](values: Iterable[T | None]) -> T | None:
    non_null = {value for value in values if value is not None}
    return next(iter(non_null)) if len(non_null) == 1 else None
