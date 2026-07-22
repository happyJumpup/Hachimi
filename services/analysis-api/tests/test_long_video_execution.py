from pathlib import Path

import pytest

from hakimi_analysis.benchmark.long_execution import (
    LongCheckpointStore,
    LongExecutionError,
    deduplicate_chunk_candidates,
    normalize_chunk_candidate,
    retry_long_operation,
)
from hakimi_analysis.benchmark.long_models import LongVideoChunk
from hakimi_analysis.benchmark.models import BenchmarkCandidate, EventKind, EvidenceChannel
from hakimi_analysis.benchmark.transport import BenchmarkTransportError


def candidate(start: float, end: float, *, reps: int | None = None) -> BenchmarkCandidate:
    return BenchmarkCandidate(
        action_name="Romanian Deadlift",
        start_seconds=start,
        end_seconds=end,
        kind=EventKind.ACTION,
        reps=reps,
        evidence_channels=[EvidenceChannel.VISUAL],
    )


def test_normalize_chunk_candidate_requires_the_frozen_absolute_source_clock() -> None:
    chunk = LongVideoChunk(
        source_id="seven",
        index=2,
        start_seconds=100,
        end_seconds=160,
        duration_seconds=60,
    )

    absolute = normalize_chunk_candidate(chunk, candidate(105, 120))

    assert (absolute.start_seconds, absolute.end_seconds) == (105, 120)
    with pytest.raises(LongExecutionError, match="chunk_candidate_time_invalid"):
        normalize_chunk_candidate(chunk, candidate(5, 20))


def test_normalize_chunk_candidate_rejects_times_outside_chunk() -> None:
    chunk = LongVideoChunk(
        source_id="seven",
        index=0,
        start_seconds=0,
        end_seconds=60,
        duration_seconds=60,
    )

    with pytest.raises(LongExecutionError, match="chunk_candidate_time_invalid"):
        normalize_chunk_candidate(chunk, candidate(50, 70))


def test_normalize_chunk_candidate_accepts_absolute_events_inside_a_low_source_range() -> None:
    chunk = LongVideoChunk(
        source_id="seven",
        index=1,
        start_seconds=50,
        end_seconds=110,
        duration_seconds=60,
    )

    normalized = normalize_chunk_candidate(chunk, candidate(55, 58))

    assert (normalized.start_seconds, normalized.end_seconds) == (55, 58)


def test_deduplicate_chunk_candidates_merges_overlap_without_inventing_parameters() -> None:
    merged = deduplicate_chunk_candidates(
        [
            candidate(100, 120, reps=10),
            candidate(110, 130, reps=12),
        ]
    )

    assert len(merged) == 1
    assert (merged[0].start_seconds, merged[0].end_seconds) == (100, 130)
    assert merged[0].reps is None
    assert merged[0].weight_kg is None


def test_deduplicate_chunk_candidates_merges_low_iou_overlap_across_chunk_boundary() -> None:
    merged = deduplicate_chunk_candidates([candidate(40, 60), candidate(50, 80)])

    assert len(merged) == 1
    assert (merged[0].start_seconds, merged[0].end_seconds) == (40, 80)


def test_deduplicate_chunk_candidates_merges_interleaved_same_action_events() -> None:
    bench_press = candidate(45, 55).model_copy(update={"action_name": "Bench Press"})
    merged = deduplicate_chunk_candidates(
        [candidate(40, 60), bench_press, candidate(50, 80)]
    )

    assert [(item.action_name, item.start_seconds, item.end_seconds) for item in merged] == [
        ("Romanian Deadlift", 40, 80),
        ("Bench Press", 45, 55),
    ]


@pytest.mark.asyncio
async def test_retry_long_operation_retries_only_retryable_transport_errors() -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise BenchmarkTransportError("provider_error", retryable=True, status_code=503)
        return "ok"

    assert await retry_long_operation(operation, retry_delays=(0, 0)) == "ok"
    assert attempts == 3


@pytest.mark.asyncio
async def test_retry_long_operation_does_not_retry_schema_or_contract_errors() -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        raise LongExecutionError("candidate_schema_error")

    with pytest.raises(LongExecutionError, match="candidate_schema_error"):
        await retry_long_operation(operation, retry_delays=(0, 0))
    assert attempts == 1


@pytest.mark.asyncio
async def test_retry_long_operation_marks_potentially_billable_retries() -> None:
    attempts = 0
    retries = 0

    class RetryableError(RuntimeError):
        retryable = True

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RetryableError("provider timeout")
        return "ok"

    def on_retry() -> None:
        nonlocal retries
        retries += 1

    assert (
        await retry_long_operation(
            operation,
            retry_delays=(0, 0),
            on_retry=on_retry,
        )
        == "ok"
    )
    assert retries == 1


def test_checkpoint_store_uses_source_arm_repetition_and_chunk_paths(tmp_path: Path) -> None:
    store = LongCheckpointStore(tmp_path, manifest_sha256="a" * 64)
    chunk = LongVideoChunk(
        source_id="seven",
        index=3,
        start_seconds=150,
        end_seconds=210,
        duration_seconds=60,
    )
    values = [candidate(151, 160)]

    store.write("seven", "seed_contact_sheet", 2, chunk, values)

    assert store.read("seven", "seed_contact_sheet", 2, chunk) == values
    assert (tmp_path / "seven" / "seed_contact_sheet" / "2" / "chunk-003.json").is_file()
