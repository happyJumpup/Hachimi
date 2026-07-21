import asyncio
import json
from collections import Counter
from pathlib import Path

import pytest

from hakimi_analysis.benchmark.aggregate import aggregate_results
from hakimi_analysis.benchmark.manifest import (
    EXPECTED_MODELS,
    BenchmarkManifest,
    build_run_plan,
)
from hakimi_analysis.benchmark.models import (
    BenchmarkCandidate,
    BenchmarkRunStatus,
    CleanupOutcome,
    CleanupPolicy,
    EventKind,
    EvidenceChannel,
    GoldStatus,
    MediaHandle,
    RouteExecution,
    RouteId,
)
from hakimi_analysis.benchmark.runner import (
    BenchmarkRunner,
    GateFailure,
    PreflightStage,
)


class FakeProvider:
    def __init__(self, *, fail_at: PreflightStage | None = None) -> None:
        self.fail_at = fail_at
        self.calls: list[tuple[str, str]] = []
        self.uploads: Counter[tuple[str, str]] = Counter()
        self.cleaned: list[str] = []

    async def check_configuration(self) -> None:
        self.calls.append(("check", "configuration"))
        self._fail(PreflightStage.CONFIGURATION)

    async def minimal_call(self, model_id: str) -> None:
        del model_id
        self.calls.append(("call", "minimal"))
        self._fail(PreflightStage.MINIMAL_CALL)

    async def upload(self, model_id: str, path: Path, media_kind: str) -> MediaHandle:
        self.calls.append(("upload", path.name))
        self.uploads[(model_id, str(path))] += 1
        return MediaHandle(
            provider="fake",
            model_id=model_id,
            media_kind=media_kind,
            handle_id=f"{model_id}:{path.name}",
            cleanup_policy=CleanupPolicy.DELETE_AND_VERIFY,
        )

    async def probe(self, handle: MediaHandle) -> None:
        self.calls.append(("probe", handle.media_kind))

    async def cleanup(self, handle: MediaHandle) -> None:
        self.cleaned.append(handle.handle_id)

    async def verify_cleanup(self, handle: MediaHandle) -> CleanupOutcome:
        return (
            CleanupOutcome.DELETED_VERIFIED
            if handle.handle_id in self.cleaned
            else CleanupOutcome.FAILED
        )

    def _fail(self, stage: PreflightStage) -> None:
        if self.fail_at == stage:
            raise GateFailure(stage, "configuration_error", retryable=False)


class FakeAdapter:
    async def execute(
        self,
        handles: object,
        *,
        run_index: int,
    ) -> RouteExecution:
        del handles, run_index
        return RouteExecution(
            candidates=[
                BenchmarkCandidate(
                    action_name="测试动作",
                    start_seconds=0,
                    end_seconds=1,
                    kind=EventKind.ACTION,
                    evidence_channels=[EvidenceChannel.VISUAL],
                )
            ]
        )


def manifest(tmp_path: Path) -> BenchmarkManifest:
    samples = []
    for index in range(3):
        paths = [
            tmp_path / f"{index}-{suffix}"
            for suffix in ("source.mp4", "av.mp4", "silent.mp4", "audio.wav")
        ]
        for path in paths:
            path.touch()
        samples.append(
            {
                "sample_id": f"sample-{index}",
                "source_path": paths[0],
                "window_start_seconds": 0,
                "duration_seconds": 60,
                "av_path": paths[1],
                "silent_video_path": paths[2],
                "audio_path": paths[3],
                "av_sha256": "a" * 64,
                "silent_video_sha256": "b" * 64,
                "audio_sha256": "c" * 64,
                "gold_path": tmp_path / f"gold-{index}.json",
                "gold_status": GoldStatus.REVIEWED,
            }
        )
    return BenchmarkManifest(
        version=1,
        seed=20260721,
        models=EXPECTED_MODELS,
        samples=samples,
    )


@pytest.mark.asyncio
async def test_preflight_stops_provider_after_first_non_retryable_gate(tmp_path: Path) -> None:
    provider = FakeProvider(fail_at=PreflightStage.MINIMAL_CALL)
    runner = BenchmarkRunner({"seed": provider}, timeout_seconds=1)

    with pytest.raises(GateFailure):
        await runner.preflight_provider("seed", EXPECTED_MODELS["seed_lite"], [])

    assert provider.calls == [("check", "configuration"), ("call", "minimal")]


@pytest.mark.asyncio
async def test_matrix_reuses_one_media_handle_per_model_and_asset(tmp_path: Path) -> None:
    fake = FakeProvider()
    benchmark_manifest = manifest(tmp_path)
    runner = BenchmarkRunner(
        {"seed": fake, "qwen": fake, "asr": fake},
        {route_id: FakeAdapter() for route_id in RouteId},
        timeout_seconds=1,
    )

    results = await runner.run(benchmark_manifest, build_run_plan(benchmark_manifest))

    assert len(results) == 63
    assert all(count == 1 for count in fake.uploads.values())
    assert len(fake.cleaned) == len(fake.uploads)
    assert len(runner.lifecycle_records) == len(fake.uploads)
    assert all(
        record.cleanup_outcome == CleanupOutcome.DELETED_VERIFIED
        for record in runner.lifecycle_records
    )
    for sample in benchmark_manifest.samples:
        sample.gold_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "sample_id": sample.sample_id,
                    "status": "reviewed",
                    "reviewed_by": ["test-reviewer"],
                    "events": [
                        {
                            "canonical_name": "测试动作",
                            "accepted_aliases": ["测试动作"],
                            "start_seconds": 0,
                            "end_seconds": 1,
                            "kind": "action",
                            "evidence_channels": ["visual"],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    aggregates = aggregate_results(benchmark_manifest, results)
    assert len(aggregates) == 7
    assert all(route.scored_units == 9 for route in aggregates)


@pytest.mark.asyncio
async def test_timeout_cancels_and_still_cleans_uploaded_handles(tmp_path: Path) -> None:
    class SlowProvider(FakeProvider):
        async def probe(self, handle: MediaHandle) -> None:
            del handle
            await asyncio.sleep(1)

    class SlowAdapter(FakeAdapter):
        async def execute(self, *args: object, **kwargs: object) -> RouteExecution:
            await asyncio.sleep(1)
            return RouteExecution(candidates=[])

    fake = SlowProvider()
    benchmark_manifest = manifest(tmp_path)
    runner = BenchmarkRunner(
        {"seed": fake, "qwen": fake, "asr": fake},
        {route_id: SlowAdapter() for route_id in RouteId},
        timeout_seconds=0.01,
    )

    results = await runner.run(benchmark_manifest, build_run_plan(benchmark_manifest)[:1])

    assert results[0].status == BenchmarkRunStatus.TIMEOUT
    assert fake.cleaned


@pytest.mark.asyncio
async def test_preflight_timeout_still_cleans_the_uploaded_handle(tmp_path: Path) -> None:
    class SlowProbeProvider(FakeProvider):
        async def probe(self, handle: MediaHandle) -> None:
            del handle
            await asyncio.sleep(1)

    provider = SlowProbeProvider()
    media = tmp_path / "probe.mp4"
    media.touch()
    runner = BenchmarkRunner({"seed": provider}, timeout_seconds=0.01)

    with pytest.raises(GateFailure) as failure:
        await runner.preflight_provider(
            "seed",
            EXPECTED_MODELS["seed_lite"],
            [
                (
                    PreflightStage.SYNTHETIC_TWO_SECONDS,
                    EXPECTED_MODELS["seed_lite"],
                    media,
                    "video",
                )
            ],
        )

    assert failure.value.code == "timeout"
    assert failure.value.cleanup_failed is False
    assert provider.cleaned


@pytest.mark.asyncio
async def test_external_preflight_cancellation_waits_for_cleanup(tmp_path: Path) -> None:
    probe_started = asyncio.Event()

    class CancellableProvider(FakeProvider):
        async def probe(self, handle: MediaHandle) -> None:
            del handle
            probe_started.set()
            await asyncio.Event().wait()

    provider = CancellableProvider()
    media = tmp_path / "probe.mp4"
    media.touch()
    runner = BenchmarkRunner({"seed": provider}, timeout_seconds=10)
    task = asyncio.create_task(
        runner.preflight_provider(
            "seed",
            EXPECTED_MODELS["seed_lite"],
            [
                (
                    PreflightStage.SYNTHETIC_TWO_SECONDS,
                    EXPECTED_MODELS["seed_lite"],
                    media,
                    "video",
                )
            ],
        )
    )
    await probe_started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert provider.cleaned


@pytest.mark.asyncio
async def test_matrix_cancellation_during_cleanup_finishes_every_handle(
    tmp_path: Path,
) -> None:
    cleanup_started = asyncio.Event()

    class SlowCleanupProvider(FakeProvider):
        async def cleanup(self, handle: MediaHandle) -> None:
            cleanup_started.set()
            await asyncio.sleep(0.05)
            await super().cleanup(handle)

    provider = SlowCleanupProvider()
    benchmark_manifest = manifest(tmp_path)
    runner = BenchmarkRunner(
        {"seed": provider, "qwen": provider, "asr": provider},
        {route_id: FakeAdapter() for route_id in RouteId},
        timeout_seconds=1,
    )
    task = asyncio.create_task(
        runner.run(
            benchmark_manifest,
            build_run_plan(benchmark_manifest)[:2],
        )
    )
    await cleanup_started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(provider.cleaned) == len(provider.uploads)
