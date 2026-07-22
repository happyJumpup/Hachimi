import asyncio

import pytest

from hakimi_analysis.benchmark.long_models import ArmId
from hakimi_analysis.benchmark.long_runtime import (
    LongRunKey,
    LongStudyRunner,
    ProviderPermits,
    build_ab_ba_waves,
)


def test_three_repetitions_use_balanced_ab_ba_source_waves() -> None:
    waves = build_ab_ba_waves(
        seven_source_id="seven",
        nineteen_source_id="nineteen",
        repetitions=3,
    )

    assert [[(key.source_id, key.arm_id) for key in wave] for wave in waves] == [
        [("seven", "seed_contact_sheet"), ("nineteen", "qwen_video")],
        [("seven", "qwen_video"), ("nineteen", "seed_contact_sheet")],
        [("seven", "qwen_video"), ("nineteen", "seed_contact_sheet")],
        [("seven", "seed_contact_sheet"), ("nineteen", "qwen_video")],
        [("seven", "seed_contact_sheet"), ("nineteen", "qwen_video")],
        [("seven", "qwen_video"), ("nineteen", "seed_contact_sheet")],
    ]


@pytest.mark.asyncio
async def test_study_waits_for_a_synchronized_wave_before_next_wave() -> None:
    waves = [
        (
            LongRunKey("seven", ArmId.SEED_CONTACT_SHEET, 1),
            LongRunKey("nineteen", ArmId.QWEN_VIDEO, 1),
        ),
        (
            LongRunKey("seven", ArmId.QWEN_VIDEO, 1),
            LongRunKey("nineteen", ArmId.SEED_CONTACT_SHEET, 1),
        ),
    ]
    events: list[tuple[str, str, str]] = []

    async def execute(key: LongRunKey, _permits: ProviderPermits) -> str:
        events.append(("start", key.source_id, str(key.arm_id)))
        await asyncio.sleep(0)
        events.append(("end", key.source_id, str(key.arm_id)))
        return str(key.arm_id)

    results = await LongStudyRunner(waves, ProviderPermits({"seed": 1, "qwen": 1})).run(
        execute
    )

    assert results == ["seed_contact_sheet", "qwen_video", "qwen_video", "seed_contact_sheet"]
    first_second_wave_start = events.index(("start", "seven", "qwen_video"))
    assert events.index(("end", "seven", "seed_contact_sheet")) < first_second_wave_start
    assert events.index(("end", "nineteen", "qwen_video")) < first_second_wave_start


@pytest.mark.asyncio
async def test_provider_permits_limit_work_and_report_queue_wait() -> None:
    permits = ProviderPermits({"qwen": 1})
    active = 0
    maximum_active = 0
    waits: list[float] = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def use_permit() -> None:
        nonlocal active, maximum_active
        async with permits.acquire("qwen") as queue_wait:
            waits.append(queue_wait)
            active += 1
            maximum_active = max(maximum_active, active)
            entered.set()
            await release.wait()
            active -= 1

    first = asyncio.create_task(use_permit())
    await entered.wait()
    second = asyncio.create_task(use_permit())
    await asyncio.sleep(0)
    assert maximum_active == 1
    release.set()
    await asyncio.gather(first, second)

    assert maximum_active == 1
    assert len(waits) == 2
    assert waits[0] >= 0
    assert waits[1] >= 0


def test_provider_permits_reject_invalid_capacities() -> None:
    with pytest.raises(ValueError, match="positive"):
        ProviderPermits({"seed": 0})
