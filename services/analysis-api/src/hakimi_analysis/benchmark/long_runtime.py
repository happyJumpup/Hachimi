import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import perf_counter
from typing import TypeVar

from hakimi_analysis.benchmark.long_models import ArmId


@dataclass(frozen=True, slots=True)
class LongRunKey:
    source_id: str
    arm_id: ArmId
    repetition: int


RunValue = TypeVar("RunValue")
RunExecutor = Callable[[LongRunKey, "ProviderPermits"], Awaitable[RunValue]]


class ProviderPermits:
    """FIFO provider permits shared by both source jobs in a study."""

    def __init__(self, capacities: dict[str, int]) -> None:
        if not capacities or any(capacity <= 0 for capacity in capacities.values()):
            raise ValueError("provider capacities must be positive")
        self._tokens: dict[str, asyncio.Queue[object]] = {}
        for provider, capacity in capacities.items():
            queue: asyncio.Queue[object] = asyncio.Queue(maxsize=capacity)
            for _ in range(capacity):
                queue.put_nowait(object())
            self._tokens[provider] = queue

    @asynccontextmanager
    async def acquire(self, provider: str) -> AsyncIterator[float]:
        try:
            queue = self._tokens[provider]
        except KeyError as error:
            raise ValueError(f"provider has no calibrated permit: {provider}") from error
        queued_at = perf_counter()
        token = await queue.get()
        queue_wait_seconds = perf_counter() - queued_at
        try:
            yield queue_wait_seconds
        finally:
            queue.put_nowait(token)


class LongStudyRunner:
    """Runs paired AB/BA waves without allowing a later wave to overlap."""

    def __init__(
        self,
        waves: Sequence[tuple[LongRunKey, LongRunKey]],
        permits: ProviderPermits,
    ) -> None:
        if not waves:
            raise ValueError("long study requires at least one synchronized wave")
        self._waves = tuple(waves)
        self._permits = permits

    async def run(self, executor: RunExecutor[RunValue]) -> list[RunValue]:
        results: list[RunValue] = []
        for wave in self._waves:
            results.extend(await self._run_wave(wave, executor))
        return results

    async def _run_wave(
        self,
        wave: tuple[LongRunKey, LongRunKey],
        executor: RunExecutor[RunValue],
    ) -> list[RunValue]:
        barrier = asyncio.Barrier(len(wave) + 1)

        async def start_together(key: LongRunKey) -> RunValue:
            await barrier.wait()
            return await executor(key, self._permits)

        tasks = [asyncio.create_task(start_together(key)) for key in wave]
        try:
            await barrier.wait()
            return list(await asyncio.gather(*tasks))
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise


def build_ab_ba_waves(
    *,
    seven_source_id: str,
    nineteen_source_id: str,
    repetitions: int,
) -> tuple[tuple[LongRunKey, LongRunKey], ...]:
    if not seven_source_id or not nineteen_source_id or seven_source_id == nineteen_source_id:
        raise ValueError("long study requires two distinct source IDs")
    if repetitions != 3:
        raise ValueError("long study requires exactly three repetitions")

    seed = ArmId.SEED_CONTACT_SHEET
    qwen = ArmId.QWEN_VIDEO
    waves: list[tuple[LongRunKey, LongRunKey]] = []
    for repetition in range(1, repetitions + 1):
        first_seven_arm, first_nineteen_arm = (seed, qwen) if repetition % 2 else (qwen, seed)
        waves.append(
            (
                LongRunKey(seven_source_id, first_seven_arm, repetition),
                LongRunKey(nineteen_source_id, first_nineteen_arm, repetition),
            )
        )
        waves.append(
            (
                LongRunKey(seven_source_id, first_nineteen_arm, repetition),
                LongRunKey(nineteen_source_id, first_seven_arm, repetition),
            )
        )
    return tuple(waves)
