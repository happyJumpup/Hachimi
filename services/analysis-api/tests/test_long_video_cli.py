import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import hakimi_analysis.benchmark.long_cli as long_cli
from hakimi_analysis.benchmark.long_cli import (
    LongBenchmarkRuntime,
    LongPreflightCheck,
    _completed_run,
    _load_reviewed_gold,
    _preflight_minimal,
    _preflight_probe_stage,
    _reviewed_gold_sha256,
    _run_preflight,
    _safe_execute,
    _write_long_gold_templates,
    calibrate_provider_concurrency,
)
from hakimi_analysis.benchmark.long_models import (
    EXPECTED_LONG_EXPERIMENT_MODELS,
    ArmId,
    LongExperimentManifest,
)
from hakimi_analysis.benchmark.long_pipeline import LongArmResult, LongExecutionAudit
from hakimi_analysis.benchmark.long_real_providers import LongProviderContractError
from hakimi_analysis.benchmark.long_results import (
    LongLifecycleJournal,
    LongLifecycleJournalPayload,
)
from hakimi_analysis.benchmark.long_runtime import LongRunKey
from hakimi_analysis.benchmark.long_scoring import LongMediaLifecycleRecord, LongRunStatus
from hakimi_analysis.benchmark.media import ProbeMedia
from hakimi_analysis.benchmark.models import CleanupOutcome


def _manifest(tmp_path: Path) -> LongExperimentManifest:
    ignored_root = tmp_path / ".benchmark-work" / "long-video"
    return LongExperimentManifest.model_validate(
        {
            "version": 1,
            "models": EXPECTED_LONG_EXPERIMENT_MODELS,
            "prompt_version": "long-video-ab-v1",
            "chunk": {
                "version": "long-video-chunks-v1",
                "duration_seconds": 60,
                "overlap_seconds": 10,
            },
            "retry": {
                "version": "long-video-retry-v1",
                "max_retries": 2,
                "retry_network_errors": True,
                "retry_http_408": True,
                "retry_http_429": True,
                "retry_http_5xx": True,
                "respect_retry_after": True,
            },
            "repetitions": 3,
            "sources": [
                {
                    "source_id": "seven-minute-rdl",
                    "source_path": str(tmp_path / "seven.mp4"),
                    "sha256": "a" * 64,
                    "duration_seconds": 424.31,
                    "gold_path": str(ignored_root / "seven-gold.json"),
                    "gold_version": "seven-v1",
                },
                {
                    "source_id": "nineteen-minute-workout",
                    "source_path": str(tmp_path / "nineteen.mp4"),
                    "sha256": "b" * 64,
                    "duration_seconds": 1157.46,
                    "gold_path": str(ignored_root / "nineteen-gold.json"),
                    "gold_version": "nineteen-v1",
                },
            ],
            "output": {
                "working_root": str(ignored_root),
                "temporary_root": str(ignored_root / "temporary"),
                "raw_results_root": str(tmp_path / "benchmark-results" / "long-video"),
                "summary_path": str(tmp_path / "benchmark-results" / "long-video" / "summary.json"),
            },
        }
    )


@pytest.mark.asyncio
async def test_concurrency_calibration_stops_at_the_first_unsafe_level() -> None:
    attempted: list[int] = []

    async def trial(limit: int) -> bool:
        attempted.append(limit)
        return limit < 3

    limit, recorded = await calibrate_provider_concurrency(3, trial)

    assert limit == 2
    assert recorded == [1, 2, 3]
    assert attempted == [1, 2, 3]


def _complete_preflight_checks(manifest: LongExperimentManifest) -> list[LongPreflightCheck]:
    checks = [
        LongPreflightCheck(component=component, stage=stage, status="passed")
        for component in long_cli._PREFLIGHT_COMPONENTS
        for stage in ("configuration", "minimal_call")
    ]
    for source in manifest.sources:
        for stage in long_cli._PREFLIGHT_PROBE_STAGES:
            checks.append(
                LongPreflightCheck(
                    component="source_media",
                    stage=stage,
                    source_id=source.source_id,
                    status="passed",
                )
            )
            checks.extend(
                LongPreflightCheck(
                    component=component,
                    stage=stage,
                    source_id=source.source_id,
                    status="passed",
                    expires_at=(
                        datetime.now(UTC) + timedelta(hours=48)
                        if component == "qwen_visual"
                        else None
                    ),
                )
                for component in long_cli._PREFLIGHT_COMPONENTS
            )
    return checks


def _qwen_expiry_audit() -> list[LongMediaLifecycleRecord]:
    return [
        LongMediaLifecycleRecord(
            provider="qwen",
            model_id="qwen3-vl-flash-2026-01-22",
            media_kind="video",
            upload_seconds=1,
            cleanup_outcome=CleanupOutcome.EXPIRY_RECORDED,
            expires_at=(datetime.now(UTC) + timedelta(hours=48)).isoformat(),
        )
    ]


def test_preflight_receipt_requires_every_provider_and_media_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(long_cli, "PROJECT_ROOT", tmp_path)
    manifest = _manifest(tmp_path)

    def write_receipt(checks: list[LongPreflightCheck]) -> None:
        receipt = long_cli.LongPreflightReceipt(
            status="PASS",
            created_at=datetime.now(UTC),
            manifest_sha256=long_cli._file_sha256_from_manifest(manifest),
            prompt_sha256=manifest.prompt_sha256,
            models=manifest.models.model_dump(),
            passed_components=list(long_cli._PREFLIGHT_COMPONENTS),
            checks=checks,
        )
        long_cli._write_json(
            long_cli._preflight_receipt_path(manifest),
            receipt.model_dump(mode="json"),
        )

    write_receipt([])
    with pytest.raises(RuntimeError, match="receipt is incomplete"):
        long_cli._validate_preflight_receipt(manifest)

    complete_checks = _complete_preflight_checks(manifest)
    missing_expiry_checks = [
        check.model_copy(update={"expires_at": None})
        if check.component == "qwen_visual"
        else check
        for check in complete_checks
    ]
    write_receipt(missing_expiry_checks)
    with pytest.raises(RuntimeError, match="receipt is incomplete"):
        long_cli._validate_preflight_receipt(manifest)

    write_receipt(complete_checks)
    long_cli._validate_preflight_receipt(manifest)


def test_calibration_receipt_requires_a_contiguous_safe_limit_and_freshness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(long_cli, "PROJECT_ROOT", tmp_path)
    manifest = _manifest(tmp_path)
    with pytest.raises(ValueError, match="record every safe level"):
        long_cli.LongCalibrationReceipt(
            status="PASS",
            created_at=datetime.now(UTC),
            manifest_sha256=long_cli._file_sha256_from_manifest(manifest),
            prompt_sha256=manifest.prompt_sha256,
            provider_concurrency_limit=2,
            attempted_limits=[1],
        )
    with pytest.raises(ValueError, match="Qwen expiry lifecycle audit"):
        long_cli.LongCalibrationReceipt(
            status="PASS",
            created_at=datetime.now(UTC),
            manifest_sha256=long_cli._file_sha256_from_manifest(manifest),
            prompt_sha256=manifest.prompt_sha256,
            provider_concurrency_limit=2,
            attempted_limits=[1, 2, 3],
        )

    stale = long_cli.LongCalibrationReceipt(
        status="PASS",
        created_at=datetime.now(UTC) - timedelta(hours=7),
        manifest_sha256=long_cli._file_sha256_from_manifest(manifest),
        prompt_sha256=manifest.prompt_sha256,
        provider_concurrency_limit=2,
        attempted_limits=[1, 2, 3],
        qwen_lifecycle_audit=_qwen_expiry_audit(),
    )
    long_cli._write_json(
        long_cli._calibration_receipt_path(manifest),
        stale.model_dump(mode="json"),
    )

    with pytest.raises(RuntimeError, match="calibration receipt has expired"):
        long_cli._load_calibrated_limit(manifest)


@pytest.mark.asyncio
async def test_preflight_minimal_uses_the_bounded_operation_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Provider:
        async def minimal_call(self, model_id: str) -> None:
            calls.append(model_id)

    async def retry_once(operation: object) -> object:
        calls.append("retry")
        return await operation()  # type: ignore[operator]

    monkeypatch.setattr(long_cli, "retry_long_operation", retry_once)

    await _preflight_minimal("qwen_visual", Provider(), "qwen3-vl-flash-2026-01-22")  # type: ignore[arg-type]

    assert calls == ["retry", "qwen3-vl-flash-2026-01-22"]


@pytest.mark.asyncio
async def test_preflight_qwen_cancellation_is_journaled_before_it_propagates(
    tmp_path: Path,
) -> None:
    class ExpiringCancelledError(asyncio.CancelledError):
        expires_at = (datetime.now(UTC) + timedelta(hours=48)).isoformat()

    class Provider:
        async def upload(self, _model_id: str, _path: Path, _media_kind: str) -> object:
            raise ExpiringCancelledError

    video = tmp_path / "probe.mp4"
    video.write_bytes(b"video")
    journal_path = tmp_path / "lifecycle.json"
    journal = LongLifecycleJournal(journal_path, manifest_sha256="a" * 64)

    with pytest.raises(asyncio.CancelledError):
        await long_cli._qwen_visual_probe(
            cast(Any, Provider()),
            video,
            "Return action events.",
            source_id="seven-minute-rdl",
            stage="real_8_seconds",
            journal=journal,
        )

    payload = LongLifecycleJournalPayload.model_validate_json(
        journal_path.read_text(encoding="utf-8")
    )
    assert len(payload.entries) == 1
    assert payload.entries[0].lifecycle.cleanup_outcome == CleanupOutcome.EXPIRY_RECORDED


@pytest.mark.asyncio
async def test_preflight_qwen_invalid_expiry_cleans_and_journals_failure(
    tmp_path: Path,
) -> None:
    class Provider:
        def __init__(self) -> None:
            self.cleaned = False

        async def upload(self, model_id: str, path: Path, media_kind: str) -> object:
            return SimpleNamespace(
                provider="qwen",
                model_id=model_id,
                media_kind=media_kind,
                handle_id=f"oss://{path.name}",
                expires_at=None,
            )

        async def analyze(self, _handle: object, _prompt: str) -> object:
            raise AssertionError("invalid expiry must stop before inference")

        async def cleanup(self, _handle: object) -> None:
            self.cleaned = True

        async def verify_cleanup(self, _handle: object) -> CleanupOutcome:
            return CleanupOutcome.EXPIRY_RECORDED

    video = tmp_path / "probe.mp4"
    video.write_bytes(b"video")
    journal_path = tmp_path / "lifecycle.json"
    journal = LongLifecycleJournal(journal_path, manifest_sha256="a" * 64)
    provider = Provider()

    with pytest.raises(LongProviderContractError, match="qwen_expiry_missing"):
        await long_cli._qwen_visual_probe(
            cast(Any, provider),
            video,
            "Return action events.",
            source_id="seven-minute-rdl",
            stage="real_8_seconds",
            journal=journal,
        )

    payload = LongLifecycleJournalPayload.model_validate_json(
        journal_path.read_text(encoding="utf-8")
    )
    assert provider.cleaned is True
    assert len(payload.entries) == 1
    assert payload.entries[0].lifecycle.cleanup_outcome == CleanupOutcome.FAILED
    assert payload.entries[0].lifecycle.expires_at is None


@pytest.mark.asyncio
async def test_preflight_continues_after_one_provider_configuration_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class SeedProvider:
        async def check_configuration(self) -> None:
            calls.append("seed:configuration")

        async def minimal_call(self, model_id: str) -> None:
            calls.append(f"seed:{model_id}")

    class QwenProvider:
        async def check_configuration(self) -> None:
            calls.append("qwen:configuration")
            raise RuntimeError("do not write provider detail into the receipt")

        async def minimal_call(self, model_id: str) -> None:
            calls.append(f"qwen:{model_id}")

    class AsrProvider:
        async def check_configuration(self) -> None:
            calls.append("asr:configuration")

        async def minimal_call(self, model_id: str) -> None:
            calls.append(f"asr:{model_id}")

    async def unavailable_probe(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("media is intentionally unavailable in this unit test")

    monkeypatch.setattr(long_cli, "create_long_probe_media", unavailable_probe)
    runtime = LongBenchmarkRuntime(
        seed=SeedProvider(),  # type: ignore[arg-type]
        qwen=QwenProvider(),  # type: ignore[arg-type]
        asr=AsrProvider(),  # type: ignore[arg-type]
    )

    receipt = await _run_preflight(_manifest(tmp_path), runtime)

    assert "asr:configuration" in calls
    assert "asr:volc.seedasr.sauc.duration" in calls
    assert "qwen:qwen3-vl-flash-2026-01-22" not in calls
    assert receipt.status == "FAIL"
    qwen_failure = next(
        check
        for check in receipt.checks
        if check.component == "qwen_visual" and check.stage == "configuration"
    )
    assert qwen_failure.status == "failed"
    assert qwen_failure.error_code == "provider_error"
    assert "provider detail" not in receipt.model_dump_json()


@pytest.mark.asyncio
async def test_preflight_probe_failure_does_not_block_independent_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeProcessor:
        def __init__(self, **_kwargs: object) -> None:
            pass

        @asynccontextmanager
        async def prepare_source(self, *_args: object) -> AsyncIterator[Any]:
            yield SimpleNamespace(
                audio_path=Path("audio.wav"),
                contact_sheet_path=Path("contact-sheet.jpg"),
                contact_sheet_timestamps=(0.0,),
                contact_sheet_ready=None,
            )

        @asynccontextmanager
        async def prepare(self, *_args: object) -> AsyncIterator[Any]:
            yield SimpleNamespace(video_path=Path("silent.mp4"))

    async def failing_seed(*_args: object, **_kwargs: object) -> None:
        calls.append("seed_visual")
        raise RuntimeError("raw Seed failure detail")

    async def succeeding_asr(*_args: object, **_kwargs: object) -> object:
        calls.append("asr")
        return SimpleNamespace(model_dump=lambda **_kwargs: {"words": []})

    async def succeeding_qwen(*_args: object, **_kwargs: object) -> None:
        calls.append("qwen_visual")

    monkeypatch.setattr(long_cli, "LocalMediaProcessor", FakeProcessor)
    monkeypatch.setattr(long_cli, "_seed_visual_probe", failing_seed)
    monkeypatch.setattr(long_cli, "_asr_probe", succeeding_asr)
    monkeypatch.setattr(long_cli, "_qwen_visual_probe", succeeding_qwen)
    checks: list[LongPreflightCheck] = []
    availability = {
        "seed_visual": True,
        "seed_fusion": True,
        "qwen_visual": True,
        "asr": True,
    }

    await _preflight_probe_stage(
        LongBenchmarkRuntime(
            seed=cast(Any, object()),
            qwen=cast(Any, object()),
            asr=cast(Any, object()),
        ),
        ProbeMedia(Path("probe.mp4"), Path("silent.mp4"), Path("audio.wav")),
        8,
        "real_8_seconds",
        Path("preflight"),
        "instructions",
        source_id="source-a",
        availability=availability,
        checks=checks,
    )

    assert calls == ["seed_visual", "asr", "qwen_visual"]
    assert availability["seed_visual"] is False
    assert availability["asr"] is True
    assert availability["seed_fusion"] is False
    assert availability["qwen_visual"] is True
    seed_failure = next(check for check in checks if check.component == "seed_visual")
    assert seed_failure.source_id == "source-a"
    assert seed_failure.error_code == "provider_error"
    assert any(
        check.component == "seed_fusion"
        and check.status == "skipped"
        and check.error_code == "component_unavailable"
        for check in checks
    )


@pytest.mark.asyncio
async def test_preflight_probe_media_failure_does_not_block_qwen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeProcessor:
        def __init__(self, **_kwargs: object) -> None:
            pass

        @asynccontextmanager
        async def prepare_source(self, *_args: object) -> AsyncIterator[object]:
            raise RuntimeError("raw source preparation detail")
            yield  # pragma: no cover

        @asynccontextmanager
        async def prepare(self, *_args: object) -> AsyncIterator[object]:
            yield SimpleNamespace(video_path=Path("silent.mp4"))

    async def succeeding_qwen(*_args: object, **_kwargs: object) -> None:
        calls.append("qwen_visual")

    monkeypatch.setattr(long_cli, "LocalMediaProcessor", FakeProcessor)
    monkeypatch.setattr(long_cli, "_qwen_visual_probe", succeeding_qwen)
    checks: list[LongPreflightCheck] = []
    availability = {
        "seed_visual": True,
        "seed_fusion": True,
        "qwen_visual": True,
        "asr": True,
    }

    await _preflight_probe_stage(
        LongBenchmarkRuntime(
            seed=cast(Any, object()),
            qwen=cast(Any, object()),
            asr=cast(Any, object()),
        ),
        ProbeMedia(Path("probe.mp4"), Path("silent.mp4"), Path("audio.wav")),
        8,
        "real_8_seconds",
        Path("preflight"),
        "instructions",
        source_id="source-a",
        availability=availability,
        checks=checks,
    )

    assert calls == ["qwen_visual"]
    media_failure = next(check for check in checks if check.component == "source_media")
    assert media_failure.source_id == "source-a"
    assert media_failure.error_code == "provider_error"


@pytest.mark.asyncio
async def test_preflight_probe_propagates_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProcessor:
        def __init__(self, **_kwargs: object) -> None:
            pass

        @asynccontextmanager
        async def prepare_source(self, *_args: object) -> AsyncIterator[object]:
            yield SimpleNamespace(
                audio_path=Path("audio.wav"),
                contact_sheet_path=Path("contact-sheet.jpg"),
                contact_sheet_timestamps=(0.0,),
                contact_sheet_ready=None,
            )

        @asynccontextmanager
        async def prepare(self, *_args: object) -> AsyncIterator[object]:
            yield SimpleNamespace(video_path=Path("silent.mp4"))

    async def cancelled_seed(*_args: object, **_kwargs: object) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(long_cli, "LocalMediaProcessor", FakeProcessor)
    monkeypatch.setattr(long_cli, "_seed_visual_probe", cancelled_seed)

    with pytest.raises(asyncio.CancelledError):
        await _preflight_probe_stage(
            LongBenchmarkRuntime(
                seed=cast(Any, object()),
                qwen=cast(Any, object()),
                asr=cast(Any, object()),
            ),
            ProbeMedia(Path("probe.mp4"), Path("silent.mp4"), Path("audio.wav")),
            8,
            "real_8_seconds",
            Path("preflight"),
            "instructions",
            source_id="source-a",
            availability={
                "seed_visual": True,
                "seed_fusion": True,
                "qwen_visual": True,
                "asr": True,
            },
            checks=[],
        )


def test_completed_run_keeps_union_coverage_and_only_sanitized_timing_fields() -> None:
    result = LongArmResult(
        key=LongRunKey("seven-minute-rdl", ArmId.SEED_CONTACT_SHEET, 1),
        candidates=(),
        completed_coverage_seconds=424.31,
        total_seconds=22,
        asr_seconds=6,
        visual_seconds=10,
        fusion_seconds=4,
        upload_seconds=0,
        cleanup_seconds=1,
        first_candidate_seconds=15,
        queue_wait_seconds={"asr": 1, "seed": 2, "qwen": 0},
        lifecycle=(
            LongMediaLifecycleRecord(
                provider="asr",
                model_id="asr",
                media_kind="audio",
                upload_seconds=0,
                cleanup_outcome=CleanupOutcome.LOCAL_RELEASED,
            ),
            LongMediaLifecycleRecord(
                provider="qwen",
                model_id="qwen3-vl-flash-2026-01-22",
                media_kind="video",
                upload_seconds=1,
                cleanup_outcome=CleanupOutcome.EXPIRY_RECORDED,
                expires_at="2099-01-01T00:00:00+00:00",
            ),
        ),
        cleanup_failed=False,
    )

    run = _completed_run(result, 424.31, preprocessing_seconds=8)

    assert run.full_coverage is True
    assert run.cleanup_ok is True
    assert run.queue_seconds == 3
    assert run.preprocessing_seconds == 8
    assert run.completed_seconds == 22
    assert run.lifecycle_audit[1].expires_at == "2099-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_timeout_persists_safe_lifecycle_audit_but_never_marks_cleanup_ok() -> None:
    class TimeoutExecutor:
        async def execute(
            self,
            _key: LongRunKey,
            _permits: object,
            *,
            audit: LongExecutionAudit,
        ) -> LongArmResult:
            audit.lifecycle.append(
                LongMediaLifecycleRecord(
                    provider="qwen",
                    model_id="qwen3-vl-flash-2026-01-22",
                    media_kind="video",
                    upload_seconds=1,
                    cleanup_outcome=CleanupOutcome.EXPIRY_RECORDED,
                    expires_at="2099-01-01T00:00:00+00:00",
                )
            )
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    run = await _safe_execute(
        TimeoutExecutor(),  # type: ignore[arg-type]
        LongRunKey("seven-minute-rdl", ArmId.QWEN_VIDEO, 1),
        object(),  # type: ignore[arg-type]
        source_duration_seconds=424.31,
        preprocessing_seconds=0,
        timeout_seconds=0.01,
        pricing=None,
    )

    assert run.status == LongRunStatus.TIMEOUT
    assert run.cleanup_ok is False
    assert run.lifecycle_audit[0].expires_at == "2099-01-01T00:00:00+00:00"


def test_gold_templates_require_an_explicit_human_review_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(long_cli, "PROJECT_ROOT", tmp_path)
    manifest = _manifest(tmp_path)
    _write_long_gold_templates(manifest)

    draft = json.loads(manifest.sources[0].gold_path.read_text(encoding="utf-8"))
    assert draft["reviewed"] is False
    with pytest.raises(RuntimeError, match="not review-locked"):
        _load_reviewed_gold(manifest)

    for source in manifest.sources:
        source.gold_path.write_text(
            json.dumps(
                {
                    "source_id": source.source_id,
                    "duration_seconds": source.duration_seconds,
                    "gold_version": source.gold_version,
                    "reviewed": True,
                    "events": [],
                }
            ),
            encoding="utf-8",
        )

    assert [item.source_id for item in _load_reviewed_gold(manifest)] == [
        "seven-minute-rdl",
        "nineteen-minute-workout",
    ]

    first_digest = _reviewed_gold_sha256(_load_reviewed_gold(manifest))
    payload = json.loads(manifest.sources[0].gold_path.read_text(encoding="utf-8"))
    payload["events"] = [
        {
            "canonical_name": "Romanian deadlift",
            "accepted_aliases": ["RDL"],
            "start_seconds": 10,
            "end_seconds": 20,
            "kind": "action",
            "evidence_channels": ["visual"],
        }
    ]
    manifest.sources[0].gold_path.write_text(json.dumps(payload), encoding="utf-8")

    assert _reviewed_gold_sha256(_load_reviewed_gold(manifest)) != first_digest
