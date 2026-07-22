import asyncio
import json
import sys
from collections.abc import AsyncIterator, Callable
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
    LongPricing,
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
from hakimi_analysis.benchmark.long_contract import QWEN_VIDEO_PROJECTION_VERSION
from hakimi_analysis.benchmark.long_execution import LongExecutionError
from hakimi_analysis.benchmark.long_models import (
    EXPECTED_LONG_EXPERIMENT_MODELS,
    ArmId,
    LongExperimentManifest,
)
from hakimi_analysis.benchmark.long_pipeline import LongArmResult, LongExecutionAudit
from hakimi_analysis.benchmark.long_results import (
    LongExpiryAuditReceipt,
    LongLifecycleJournal,
    LongLifecycleJournalEntry,
    LongLifecycleJournalPayload,
)
from hakimi_analysis.benchmark.long_runtime import LongRunKey
from hakimi_analysis.benchmark.long_scoring import LongMediaLifecycleRecord, LongRunStatus
from hakimi_analysis.benchmark.media import ProbeMedia
from hakimi_analysis.benchmark.models import CleanupOutcome


def _gold_contract(*names: str) -> dict[str, object]:
    return {
        "version": "long-video-unique-actions-v1",
        "actions": [{"canonical_name": name, "accepted_aliases": [name]} for name in names],
    }


def _manifest(tmp_path: Path) -> LongExperimentManifest:
    ignored_root = tmp_path / ".benchmark-work" / "long-video"
    return LongExperimentManifest.model_validate(
        {
            "version": 1,
            "models": EXPECTED_LONG_EXPERIMENT_MODELS,
            "prompt_version": "long-video-ab-v3",
            "qwen_video_projection_version": QWEN_VIDEO_PROJECTION_VERSION,
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
                    "gold_version": "long-video-gold-v2",
                    "gold_contract": _gold_contract("罗马尼亚硬拉"),
                },
                {
                    "source_id": "nineteen-minute-workout",
                    "source_path": str(tmp_path / "nineteen.mp4"),
                    "sha256": "b" * 64,
                    "duration_seconds": 1157.46,
                    "gold_path": str(ignored_root / "nineteen-gold.json"),
                    "gold_version": "long-video-gold-v2",
                    "gold_contract": _gold_contract(
                        "弹力带肩活动",
                        "推撑类激活",
                        "低位绳索夹胸",
                        "平板杠铃卧推",
                        "上斜器械卧推",
                        "坐姿杠铃实力推",
                        "颈后绳索臂屈伸",
                    ),
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


def test_runtime_config_keeps_bounded_asr_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class RecordingAsrClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(long_cli, "_verify_explicit_proxy", lambda _proxy: None)
    monkeypatch.setattr(long_cli, "resolve_long_addresses", lambda _host: [])
    monkeypatch.setattr(
        long_cli,
        "require_long_proxy_for_fake_ip",
        lambda _addresses, _proxy: None,
    )
    monkeypatch.setattr(long_cli, "_required_env", lambda _name: "test-key")
    monkeypatch.setattr(long_cli, "LongSeedMediaProvider", lambda **_kwargs: object())
    monkeypatch.setattr(long_cli, "LongQwenVlProvider", lambda **_kwargs: object())
    monkeypatch.setattr(long_cli, "LongProxyVolcAsrClient", RecordingAsrClient)
    monkeypatch.setattr(long_cli, "LongAsrMediaProvider", lambda client: client)

    long_cli._build_runtime()

    assert captured["pace_audio"] is False
    assert captured["retry_delays"] == (1.0, 2.0)


def test_representative_source_maps_local_contact_sheet_times_to_source_clock(
    tmp_path: Path,
) -> None:
    source = _manifest(tmp_path).sources[0].model_copy(
        update={"representative_start_seconds": 300.0}
    )
    prepared = long_cli._prepared_representative_source(
        source,
        ProbeMedia(tmp_path / "av.mp4", tmp_path / "silent.mp4", tmp_path / "audio.wav"),
        SimpleNamespace(
            audio_path=tmp_path / "audio.wav",
            contact_sheet_path=tmp_path / "sheet.jpg",
            contact_sheet_timestamps=(0.0, 15.0, 30.0, 45.0),
        ),
    )

    chunk = prepared.chunks[0]
    assert (chunk.chunk.start_seconds, chunk.chunk.end_seconds) == (300.0, 360.0)
    assert chunk.frame_times_seconds == (300.0, 315.0, 330.0, 345.0)


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


def _append_qwen_lifecycle(
    manifest: LongExperimentManifest,
    *,
    expires_at: datetime,
    outcome: CleanupOutcome = CleanupOutcome.EXPIRY_RECORDED,
) -> None:
    long_cli._lifecycle_journal(manifest).append(
        LongLifecycleJournalEntry(
            phase="experiment",
            source_id=manifest.sources[0].source_id,
            arm=ArmId.QWEN_VIDEO,
            run_index=1,
            recorded_at=expires_at - timedelta(hours=48),
            lifecycle=LongMediaLifecycleRecord(
                provider="qwen",
                model_id=manifest.models.qwen_visual_model,
                media_kind="video",
                upload_seconds=1,
                cleanup_outcome=outcome,
                expires_at=expires_at.isoformat(),
            ),
        )
    )


def test_parser_exposes_a_local_expiry_audit_without_a_real_provider_flag() -> None:
    args = long_cli.build_parser().parse_args(
        ["audit-expiry", "--manifest", ".benchmark-work/long-video/manifest.json"]
    )

    assert args.command == "audit-expiry"
    assert not hasattr(args, "real")


def test_audit_expiry_command_writes_an_ignored_receipt_without_building_providers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(long_cli, "PROJECT_ROOT", tmp_path)
    manifest = _manifest(tmp_path)
    monkeypatch.setattr(long_cli, "_load_manifest", lambda _path: manifest)
    monkeypatch.setattr(
        long_cli,
        "_build_runtime",
        lambda: pytest.fail("the local expiry audit must not build cloud providers"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["long-video", "audit-expiry", "--manifest", "manifest.json"],
    )

    assert long_cli.main() == 1
    receipt_path = long_cli._expiry_audit_receipt_path(manifest)
    receipt = LongExpiryAuditReceipt.model_validate_json(receipt_path.read_text(encoding="utf-8"))
    assert receipt.status == "pending"
    assert receipt.journal_sha256 is None


def test_expiry_audit_is_pending_when_the_journal_is_missing_or_margin_has_not_elapsed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(long_cli, "PROJECT_ROOT", tmp_path)
    manifest = _manifest(tmp_path)
    checked_at = datetime(2026, 7, 25, 12, tzinfo=UTC)

    missing = long_cli._audit_qwen_expiry(manifest, checked_at=checked_at)
    assert missing.status == "pending"
    assert missing.journal_sha256 is None

    _append_qwen_lifecycle(
        manifest,
        expires_at=checked_at - timedelta(minutes=4),
    )
    not_elapsed = long_cli._audit_qwen_expiry(manifest, checked_at=checked_at)
    assert not_elapsed.status == "pending"
    assert not_elapsed.journal_sha256 is not None
    assert not_elapsed.max_expires_at == checked_at - timedelta(minutes=4)


def test_expiry_audit_records_provider_ttl_elapsed_only_after_the_margin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(long_cli, "PROJECT_ROOT", tmp_path)
    manifest = _manifest(tmp_path)
    checked_at = datetime(2026, 7, 25, 12, tzinfo=UTC)
    _append_qwen_lifecycle(
        manifest,
        expires_at=checked_at - timedelta(minutes=6),
    )

    receipt = long_cli._audit_qwen_expiry(manifest, checked_at=checked_at)

    assert receipt.status == "provider_ttl_elapsed"
    assert receipt.margin_seconds == 300
    assert receipt.record_count == 1
    assert receipt.blocking_record_count == 0


def test_score_gate_consumes_only_a_receipt_bound_to_the_current_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(long_cli, "PROJECT_ROOT", tmp_path)
    manifest = _manifest(tmp_path)
    checked_at = datetime(2026, 7, 25, 12, tzinfo=UTC)
    _append_qwen_lifecycle(
        manifest,
        expires_at=checked_at - timedelta(minutes=6),
    )
    receipt = long_cli._audit_qwen_expiry(manifest, checked_at=checked_at)
    long_cli._write_json(
        long_cli._expiry_audit_receipt_path(manifest),
        receipt.model_dump(mode="json"),
    )

    assert long_cli._load_expiry_audit_status(manifest) == "provider_ttl_elapsed"

    _append_qwen_lifecycle(
        manifest,
        expires_at=checked_at + timedelta(hours=48),
    )
    assert long_cli._load_expiry_audit_status(manifest) == "pending"


def test_expiry_audit_keeps_any_failed_lifecycle_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(long_cli, "PROJECT_ROOT", tmp_path)
    manifest = _manifest(tmp_path)
    checked_at = datetime(2026, 7, 25, 12, tzinfo=UTC)
    _append_qwen_lifecycle(
        manifest,
        expires_at=checked_at - timedelta(hours=1),
        outcome=CleanupOutcome.FAILED,
    )

    receipt = long_cli._audit_qwen_expiry(manifest, checked_at=checked_at)

    assert receipt.status == "lifecycle_failed"
    assert receipt.blocking_record_count == 1
    long_cli._write_json(
        long_cli._expiry_audit_receipt_path(manifest),
        receipt.model_dump(mode="json"),
    )
    assert long_cli._load_expiry_audit_status(manifest) == "lifecycle_failed"


def test_expiry_audit_rejects_a_journal_bound_to_another_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(long_cli, "PROJECT_ROOT", tmp_path)
    manifest = _manifest(tmp_path)
    path = long_cli._lifecycle_journal_path(manifest)
    path.parent.mkdir(parents=True)
    path.write_text(
        LongLifecycleJournalPayload(manifest_sha256="f" * 64).model_dump_json(),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="journal manifest mismatch"):
        long_cli._audit_qwen_expiry(
            manifest,
            checked_at=datetime(2026, 7, 25, 12, tzinfo=UTC),
        )


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
        check.model_copy(update={"expires_at": None}) if check.component == "qwen_visual" else check
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

    terminal_failure = LongMediaLifecycleRecord(
        provider="qwen",
        model_id="qwen3-vl-flash-2026-01-22",
        media_kind="video",
        upload_seconds=1,
        cleanup_outcome=CleanupOutcome.FAILED,
        expires_at=(datetime.now(UTC) + timedelta(hours=48)).isoformat(),
    )
    fallback = long_cli.LongCalibrationReceipt(
        status="PASS",
        created_at=datetime.now(UTC),
        manifest_sha256=long_cli._file_sha256_from_manifest(manifest),
        prompt_sha256=manifest.prompt_sha256,
        provider_concurrency_limit=1,
        attempted_limits=[1, 2],
        qwen_lifecycle_audit=_qwen_expiry_audit(),
        terminal_unsafe_qwen_lifecycle_audit=[terminal_failure],
    )
    assert fallback.provider_concurrency_limit == 1
    assert fallback.terminal_unsafe_qwen_lifecycle_audit == [terminal_failure]
    safe_records, terminal_records = long_cli._partition_calibration_lifecycle(
        {1: _qwen_expiry_audit(), 2: [*_qwen_expiry_audit(), terminal_failure]},
        selected_limit=1,
        attempted_limits=[1, 2],
    )
    assert all(record.cleanup_outcome == CleanupOutcome.EXPIRY_RECORDED for record in safe_records)
    assert terminal_records[-1] == terminal_failure

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


def test_failed_calibration_receipt_preserves_only_safe_trial_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(long_cli, "PROJECT_ROOT", tmp_path)
    manifest = _manifest(tmp_path)
    receipt = long_cli.LongCalibrationReceipt(
        status="FAIL",
        created_at=datetime.now(UTC),
        manifest_sha256=long_cli._file_sha256_from_manifest(manifest),
        prompt_sha256=manifest.prompt_sha256,
        provider_concurrency_limit=None,
        attempted_limits=[1],
        failure={"stage": "capacity_1", "error_code": "provider_error"},
        trial_summaries=[
            long_cli.LongCalibrationTrialSummary(
                provider_concurrency_limit=1,
                passed=False,
                runs=[
                    long_cli.LongCalibrationRunSummary(
                        source_id="seven-minute-rdl",
                        arm=ArmId.SEED_CONTACT_SHEET,
                        run_index=1,
                        status=LongRunStatus.PROVIDER_ERROR,
                        error_code="provider_error",
                        full_coverage=False,
                        cleanup_ok=False,
                    )
                ],
            )
        ],
    )

    payload = receipt.model_dump_json()
    assert '"provider_concurrency_limit":null' in payload
    assert '"error_code":"provider_error"' in payload
    assert "private provider detail" not in payload
    with pytest.raises(ValueError, match="cannot select a provider limit"):
        long_cli.LongCalibrationReceipt(
            status="FAIL",
            created_at=datetime.now(UTC),
            manifest_sha256=long_cli._file_sha256_from_manifest(manifest),
            prompt_sha256=manifest.prompt_sha256,
            provider_concurrency_limit=1,
            attempted_limits=[1],
            failure={"stage": "capacity_1", "error_code": "provider_error"},
            trial_summaries=[],
        )


def test_calibrate_command_writes_a_safe_failed_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(long_cli, "PROJECT_ROOT", tmp_path)
    manifest = _manifest(tmp_path)
    failed_receipt = long_cli.LongCalibrationReceipt(
        status="FAIL",
        created_at=datetime.now(UTC),
        manifest_sha256=long_cli._file_sha256_from_manifest(manifest),
        prompt_sha256=manifest.prompt_sha256,
        provider_concurrency_limit=None,
        attempted_limits=[1],
        failure={"stage": "capacity_1", "error_code": "provider_error"},
        trial_summaries=[
            long_cli.LongCalibrationTrialSummary(
                provider_concurrency_limit=1,
                passed=False,
                runs=[
                    long_cli.LongCalibrationRunSummary(
                        source_id="seven-minute-rdl",
                        arm=ArmId.SEED_CONTACT_SHEET,
                        run_index=1,
                        status=LongRunStatus.PROVIDER_ERROR,
                        error_code="provider_error",
                        full_coverage=False,
                        cleanup_ok=False,
                    )
                ],
            )
        ],
    )

    async def failed_calibration(*_args: object, **_kwargs: object) -> object:
        return failed_receipt

    monkeypatch.setattr(long_cli, "_load_manifest", lambda _path: manifest)
    monkeypatch.setattr(long_cli, "_load_env_file", lambda _path: None)
    monkeypatch.setattr(long_cli, "verify_long_source_media", lambda _manifest: None)
    monkeypatch.setattr(long_cli, "_validate_preflight_receipt", lambda _manifest: None)
    monkeypatch.setattr(long_cli, "_build_runtime", lambda: object())
    monkeypatch.setattr(long_cli, "_run_calibration", failed_calibration)
    monkeypatch.setattr(
        sys,
        "argv",
        ["long-video", "calibrate", "--manifest", "manifest.json", "--real"],
    )

    assert long_cli.main() == 1
    receipt = long_cli.LongCalibrationReceipt.model_validate_json(
        long_cli._calibration_receipt_path(manifest).read_text(encoding="utf-8")
    )
    assert receipt.status == "FAIL"
    assert receipt.failure == {"stage": "capacity_1", "error_code": "provider_error"}


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
        async def upload(
            self,
            _model_id: str,
            _path: Path,
            _media_kind: str,
            *,
            on_expiry: Callable[[str], None] | None = None,
        ) -> object:
            if on_expiry is not None:
                on_expiry(ExpiringCancelledError.expires_at)
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
    assert [entry.lifecycle.cleanup_outcome for entry in payload.entries] == [
        CleanupOutcome.EXPIRY_RECORDED,
        CleanupOutcome.FAILED,
    ]


@pytest.mark.asyncio
async def test_preflight_qwen_fails_closed_before_upload_without_journal(
    tmp_path: Path,
) -> None:
    class Provider:
        upload_started = False

        async def upload(self, *_args: object, **_kwargs: object) -> object:
            self.upload_started = True
            raise AssertionError("upload must not start without a lifecycle journal")

    provider = Provider()
    video = tmp_path / "probe.mp4"
    video.write_bytes(b"video")

    with pytest.raises(LongExecutionError, match="qwen_lifecycle_sink_missing"):
        await long_cli._qwen_visual_probe(
            cast(Any, provider),
            video,
            "Return action events.",
            source_id="seven-minute-rdl",
            stage="real_8_seconds",
            journal=None,
        )

    assert provider.upload_started is False


@pytest.mark.asyncio
async def test_calibration_fails_before_media_preparation_without_journal(
    tmp_path: Path,
) -> None:
    with pytest.raises(LongExecutionError, match="qwen_lifecycle_sink_missing"):
        await long_cli._run_calibration(
            _manifest(tmp_path),
            cast(Any, object()),
            journal=None,
        )


@pytest.mark.asyncio
async def test_experiment_fails_before_media_preparation_without_journal(
    tmp_path: Path,
) -> None:
    with pytest.raises(LongExecutionError, match="qwen_lifecycle_sink_missing"):
        await long_cli._run_experiment(
            _manifest(tmp_path),
            cast(Any, object()),
            provider_concurrency_limit=1,
            timeout_seconds=180,
            pricing=None,
            gold_sha256="a" * 64,
            journal=None,
        )


@pytest.mark.asyncio
async def test_preflight_qwen_invalid_expiry_cleans_and_journals_failure(
    tmp_path: Path,
) -> None:
    class Provider:
        def __init__(self) -> None:
            self.cleaned = False

        async def upload(
            self,
            model_id: str,
            path: Path,
            media_kind: str,
            *,
            on_expiry: Callable[[str], None] | None = None,
        ) -> object:
            del on_expiry
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

    with pytest.raises(LongExecutionError, match="qwen_expiry_missing"):
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
async def test_preflight_qwen_expiry_mismatch_is_journaled_as_failed(
    tmp_path: Path,
) -> None:
    recorded_expiry = (datetime.now(UTC) + timedelta(hours=47)).isoformat()
    returned_expiry = (datetime.now(UTC) + timedelta(hours=48)).isoformat()

    class Provider:
        async def upload(
            self,
            model_id: str,
            _path: Path,
            media_kind: str,
            *,
            on_expiry: Callable[[str], None] | None = None,
        ) -> object:
            if on_expiry is not None:
                on_expiry(recorded_expiry)
            return SimpleNamespace(
                provider="qwen",
                model_id=model_id,
                media_kind=media_kind,
                expires_at=returned_expiry,
            )

        async def analyze(self, _handle: object, _prompt: str) -> None:
            raise AssertionError("mismatched handle must not be analyzed")

        async def cleanup(self, _handle: object) -> None:
            return None

        async def verify_cleanup(self, _handle: object) -> CleanupOutcome:
            return CleanupOutcome.EXPIRY_RECORDED

    video = tmp_path / "probe.mp4"
    video.write_bytes(b"video")
    journal_path = tmp_path / "lifecycle.json"
    journal = LongLifecycleJournal(journal_path, manifest_sha256="a" * 64)

    with pytest.raises(LongExecutionError, match="qwen_expiry_mismatch"):
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
    assert [entry.lifecycle.cleanup_outcome for entry in payload.entries] == [
        CleanupOutcome.EXPIRY_RECORDED,
        CleanupOutcome.FAILED,
    ]


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

    async def succeeding_qwen(
        _provider: object,
        video_path: Path,
        *_args: object,
        **_kwargs: object,
    ) -> None:
        assert video_path == Path("qwen-projection.mp4")
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
        ProbeMedia(Path("probe.mp4"), Path("qwen-projection.mp4"), Path("audio.wav")),
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

    async def succeeding_qwen(
        _provider: object,
        video_path: Path,
        *_args: object,
        **_kwargs: object,
    ) -> None:
        assert video_path == Path("qwen-projection.mp4")
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
        ProbeMedia(Path("probe.mp4"), Path("qwen-projection.mp4"), Path("audio.wav")),
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
        token_usage_by_provider={"seed": (1000, 500)},
        # Retried billable calls deliberately leave this false because a
        # failed attempt may have charged tokens without returning usage.
        token_usage_known=False,
    )

    run = _completed_run(
        result,
        424.31,
        preprocessing_seconds=8,
        pricing=LongPricing(
            seed_input_cny_per_1k=1,
            seed_output_cny_per_1k=1,
            qwen_input_cny_per_1k=1,
            qwen_output_cny_per_1k=1,
            asr_cny_per_minute=1,
        ),
    )

    assert run.full_coverage is True
    assert run.cleanup_ok is True
    assert run.queue_seconds == 3
    assert run.preprocessing_seconds == 8
    assert run.completed_seconds == 22
    assert run.input_tokens is None
    assert run.output_tokens is None
    assert run.cost_cny is None
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
        events = [
            {
                "canonical_name": action.canonical_name,
                "accepted_aliases": action.accepted_aliases,
                "start_seconds": index * 10,
                "end_seconds": index * 10 + 5,
                "kind": "action",
                "evidence_channels": ["visual"],
            }
            for index, action in enumerate(source.gold_contract.actions)
        ]
        source.gold_path.write_text(
            json.dumps(
                {
                    "source_id": source.source_id,
                    "duration_seconds": source.duration_seconds,
                    "gold_version": source.gold_version,
                    "reviewed": True,
                    "events": events,
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
    payload["events"][0]["start_seconds"] = 1
    manifest.sources[0].gold_path.write_text(json.dumps(payload), encoding="utf-8")

    assert _reviewed_gold_sha256(_load_reviewed_gold(manifest)) != first_digest
