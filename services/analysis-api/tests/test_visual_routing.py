from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from hakimi_analysis.models import Segment, VisualLocalizationResult, VisualSegment
from hakimi_analysis.providers.base import ProviderError, ProviderSchemaError
from hakimi_analysis.visual_routing import CircuitBreaker, SequentialVisualRouter


class FakePreparedProvider:
    adapter_version = "fake-v1"

    def __init__(self, provider_name: str, outcomes: list[object]) -> None:
        self.provider_name = provider_name
        self.outcomes = outcomes
        self.prepare_count = 0
        self.prepared_values: list[str | Path] = []
        self.locate_count = 0

    @asynccontextmanager
    async def prepare(self, video_path: Path) -> AsyncIterator[str | Path]:
        self.prepare_count += 1
        prepared = f"signed:{video_path.name}:{self.prepare_count}"
        yield prepared

    async def locate(
        self,
        prepared_media: str | Path,
        *,
        window: Segment,
        instructions: str,
    ) -> VisualLocalizationResult:
        del window, instructions
        self.locate_count += 1
        self.prepared_values.append(prepared_media)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, VisualLocalizationResult)
        return outcome


def visual_result(*, empty: bool = False) -> VisualLocalizationResult:
    return VisualLocalizationResult(
        segments=(
            []
            if empty
            else [
                VisualSegment(
                    action_name="罗马尼亚硬拉",
                    start_seconds=2,
                    end_seconds=8,
                    visual_cue="髋部后移",
                )
            ]
        )
    )


@pytest.mark.asyncio
async def test_schema_failure_switches_current_and_remaining_chunks_to_fallback(
    tmp_path: Path,
) -> None:
    primary = FakePreparedProvider(
        "ark",
        [ProviderSchemaError("invalid schema")],
    )
    fallback = FakePreparedProvider(
        "qwen",
        [
            ProviderError("provider_error", "retry", retryable=True),
            visual_result(),
            visual_result(),
        ],
    )
    router = SequentialVisualRouter(primary=primary, fallback=fallback)
    state = router.new_run_state()

    first = await router.locate_visual(
        video_path=tmp_path / "chunk-1.mp4",
        window=Segment(start_seconds=0, end_seconds=60),
        instructions="Return JSON.",
        state=state,
        deadline=float("inf"),
        chunk_index=1,
    )
    second = await router.locate_visual(
        video_path=tmp_path / "chunk-2.mp4",
        window=Segment(start_seconds=0, end_seconds=60),
        instructions="Return JSON.",
        state=state,
        deadline=float("inf"),
        chunk_index=2,
    )

    assert first.segments and second.segments
    assert state.fallback_active is True
    assert primary.locate_count == 1
    assert fallback.locate_count == 3
    assert fallback.prepare_count == 2
    assert fallback.prepared_values[0] == fallback.prepared_values[1]


@pytest.mark.asyncio
async def test_valid_empty_result_does_not_switch_provider(tmp_path: Path) -> None:
    primary = FakePreparedProvider("ark", [visual_result(empty=True), visual_result()])
    fallback = FakePreparedProvider("qwen", [visual_result()])
    router = SequentialVisualRouter(primary=primary, fallback=fallback)
    state = router.new_run_state()

    result = await router.locate_visual(
        video_path=tmp_path / "chunk.mp4",
        window=Segment(start_seconds=0, end_seconds=60),
        instructions="Return JSON.",
        state=state,
        deadline=float("inf"),
        chunk_index=1,
    )

    assert result.segments == []
    assert state.fallback_active is False
    assert fallback.locate_count == 0


@pytest.mark.asyncio
async def test_content_safety_and_media_errors_do_not_fallback(
    tmp_path: Path,
) -> None:
    for code in ("content_safety", "media_error", "budget_exhausted"):
        primary = FakePreparedProvider(
            "ark",
            [ProviderError(code, "stop", retryable=False)],
        )
        fallback = FakePreparedProvider("qwen", [visual_result()])
        router = SequentialVisualRouter(primary=primary, fallback=fallback)

        with pytest.raises(ProviderError) as raised:
            await router.locate_visual(
                video_path=tmp_path / f"{code}.mp4",
                window=Segment(start_seconds=0, end_seconds=60),
                instructions="Return JSON.",
                state=router.new_run_state(),
                deadline=float("inf"),
                chunk_index=1,
            )

        assert raised.value.code == code
        assert fallback.locate_count == 0


@pytest.mark.asyncio
async def test_visual_call_budget_counts_provider_attempts(tmp_path: Path) -> None:
    primary = FakePreparedProvider(
        "ark",
        [
            ProviderError("provider_error", "retry", retryable=True),
            ProviderError("provider_error", "retry", retryable=True),
        ],
    )
    fallback = FakePreparedProvider("qwen", [visual_result()])
    router = SequentialVisualRouter(
        primary=primary,
        fallback=fallback,
        max_visual_calls=2,
    )
    state = router.new_run_state()

    with pytest.raises(ProviderError, match="budget") as raised:
        await router.locate_visual(
            video_path=tmp_path / "chunk.mp4",
            window=Segment(start_seconds=0, end_seconds=60),
            instructions="Return JSON.",
            state=state,
            deadline=float("inf"),
            chunk_index=1,
        )

    assert raised.value.code == "budget_exhausted"
    assert state.visual_calls == 2
    assert fallback.locate_count == 0


def test_circuit_breaker_opens_after_three_transient_failures_and_half_opens() -> None:
    now = [0.0]
    breaker = CircuitBreaker(clock=lambda: now[0])
    transient = ProviderError("provider_error", "retry", retryable=True)

    for _ in range(3):
        assert breaker.acquire() is True
        breaker.record_failure(transient)

    assert breaker.acquire() is False
    now[0] = 61
    assert breaker.acquire() is True
    assert breaker.acquire() is False
    breaker.record_success()
    assert breaker.acquire() is True


def test_configuration_error_opens_circuit_until_restart() -> None:
    now = [0.0]
    breaker = CircuitBreaker(clock=lambda: now[0])
    breaker.record_failure(
        ProviderError("configuration_error", "bad model", retryable=False)
    )

    now[0] = 1_000
    assert breaker.acquire() is False
