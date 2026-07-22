"""CLI for the isolated 7/19-minute Seed-contact-sheet vs Qwen-VL study.

This module intentionally doesn't import or call the product analysis API.  Its
only persistent artifacts live below Git-ignored benchmark roots and contain no
media, ASR transcript, prompt, or raw provider response.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import socket
import sys
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, cast
from urllib.parse import urlparse

from dotenv import load_dotenv
from pydantic import Field, model_validator

from hakimi_analysis.benchmark.long_execution import (
    LongCheckpointStore,
    LongExecutionError,
    retry_long_operation,
)
from hakimi_analysis.benchmark.long_manifest import load_long_experiment_manifest
from hakimi_analysis.benchmark.long_media import (
    build_complete_source_chunks,
    verify_long_source_media,
)
from hakimi_analysis.benchmark.long_models import (
    ArmId,
    LongExperimentManifest,
    LongExperimentSource,
    LongVideoChunk,
)
from hakimi_analysis.benchmark.long_pipeline import (
    LongArmExecutor,
    LongArmResult,
    LongExecutionAudit,
    LongPreparedChunk,
    LongPreparedSource,
    LongPromptVersion,
    LongProviderBundle,
    LongQwenExpiryLifecycle,
)
from hakimi_analysis.benchmark.long_preparation import (
    LongMediaPreparer,
    create_long_probe_media,
)
from hakimi_analysis.benchmark.long_prompts import (
    VISUAL_ONLY_SUFFIX,
    long_fusion_prompt,
    long_video_prompt,
)
from hakimi_analysis.benchmark.long_real_providers import (
    LongAsrMediaProvider,
    LongProviderContractError,
    LongProxyVolcAsrClient,
    LongQwenVlProvider,
    LongSeedMediaProvider,
)
from hakimi_analysis.benchmark.long_report import render_long_video_report
from hakimi_analysis.benchmark.long_results import (
    LONG_EXPIRY_AUDIT_MARGIN_SECONDS,
    LongExperimentRawResult,
    LongExpiryAuditReceipt,
    LongExpiryAuditStatus,
    LongLifecycleJournal,
    LongLifecycleJournalEntry,
)
from hakimi_analysis.benchmark.long_runtime import (
    LongRunKey,
    LongStudyRunner,
    ProviderPermits,
    build_ab_ba_waves,
)
from hakimi_analysis.benchmark.long_scoring import (
    LongMediaLifecycleRecord,
    LongRunStatus,
    LongVideoRun,
    LongVideoSourceGold,
    choose_long_video_arm,
    score_long_video_runs,
    validate_gold_against_source_contract,
)
from hakimi_analysis.benchmark.long_transport import (
    LongBenchmarkTransportError,
    LongCurlTransport,
    require_long_proxy_for_fake_ip,
    resolve_long_addresses,
)
from hakimi_analysis.benchmark.media import ProbeMedia
from hakimi_analysis.benchmark.models import (
    CandidateEnvelope,
    CleanupOutcome,
    StrictModel,
)
from hakimi_analysis.media import LocalMediaProcessor

PROJECT_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env.benchmark.local"
_IGNORED_ROOTS = frozenset({".benchmark-work", "benchmark-results", "tmp"})
_ERROR_CODE = re.compile(r"^[a-z0-9_]{1,64}$")
_PREFLIGHT_MAX_AGE = timedelta(hours=6)
_CALIBRATION_MAX_AGE = timedelta(hours=6)
_DEFAULT_RUN_TIMEOUT_SECONDS = 600.0
_PREFLIGHT_COMPONENTS = ("asr", "qwen_visual", "seed_fusion", "seed_visual")
_PREFLIGHT_PROBE_STAGES = (
    "synthetic_2_seconds",
    "real_8_seconds",
    "representative_60_seconds",
)


@dataclass(frozen=True, slots=True)
class LongBenchmarkRuntime:
    seed: LongSeedMediaProvider
    qwen: LongQwenVlProvider
    asr: LongAsrMediaProvider

    @property
    def providers(self) -> LongProviderBundle:
        return LongProviderBundle(asr=self.asr, seed=self.seed, qwen=self.qwen)


@dataclass(frozen=True, slots=True)
class LongPricing:
    seed_input_cny_per_1k: float | None
    seed_output_cny_per_1k: float | None
    qwen_input_cny_per_1k: float | None
    qwen_output_cny_per_1k: float | None
    asr_cny_per_minute: float | None


class LongPreflightFailure(RuntimeError):
    def __init__(
        self,
        component: str,
        stage: str,
        code: str,
        *,
        expires_at: datetime | None = None,
    ) -> None:
        self.component = component
        self.stage = stage
        self.code = code
        self.expires_at = expires_at
        super().__init__(f"{component}:{stage}:{code}")


class LongPreflightCheck(StrictModel):
    """A sanitized, independently-recorded preflight check."""

    component: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    stage: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    source_id: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,128}$")
    status: Literal["passed", "failed", "skipped"]
    error_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,64}$")
    expires_at: datetime | None = None


class LongPreflightReceipt(StrictModel):
    version: int = 1
    status: str
    created_at: datetime
    manifest_sha256: str
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    models: dict[str, str]
    passed_components: list[str]
    failure: dict[str, str] | None = None
    checks: list[LongPreflightCheck] = Field(default_factory=list)


class LongCalibrationReceipt(StrictModel):
    version: int = 1
    status: str
    created_at: datetime
    manifest_sha256: str
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_concurrency_limit: int = Field(ge=1, le=3)
    attempted_limits: list[int]
    qwen_lifecycle_audit: list[LongMediaLifecycleRecord] = Field(default_factory=list)
    terminal_unsafe_qwen_lifecycle_audit: list[LongMediaLifecycleRecord] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def validate_attempted_limits(self) -> "LongCalibrationReceipt":
        terminal_attempt = min(self.provider_concurrency_limit + 1, 3)
        expected = list(range(1, terminal_attempt + 1))
        if self.attempted_limits != expected:
            raise ValueError(
                "long-video calibration must record every safe level and the first unsafe level"
            )
        if not self.qwen_lifecycle_audit or any(
            record.provider != "qwen"
            or record.cleanup_outcome != CleanupOutcome.EXPIRY_RECORDED
            or (expiry := _expiry_datetime_from_value(record.expires_at)) is None
            or expiry <= self.created_at
            for record in self.qwen_lifecycle_audit
        ):
            raise ValueError("long-video calibration requires Qwen expiry lifecycle audit")
        if self.provider_concurrency_limit == 3 and self.terminal_unsafe_qwen_lifecycle_audit:
            raise ValueError("maximum safe calibration cannot contain a terminal unsafe audit")
        if any(
            record.provider != "qwen"
            or record.cleanup_outcome not in {CleanupOutcome.EXPIRY_RECORDED, CleanupOutcome.FAILED}
            or (
                record.cleanup_outcome == CleanupOutcome.EXPIRY_RECORDED
                and (
                    (expiry := _expiry_datetime_from_value(record.expires_at)) is None
                    or expiry <= self.created_at
                )
            )
            or (
                record.cleanup_outcome == CleanupOutcome.FAILED
                and record.expires_at is not None
                and (
                    (failed_expiry := _expiry_datetime_from_value(record.expires_at)) is None
                    or failed_expiry <= self.created_at
                )
            )
            for record in self.terminal_unsafe_qwen_lifecycle_audit
        ):
            raise ValueError("long-video terminal unsafe Qwen lifecycle audit is invalid")
        return self


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Isolated long-video A/B experiment")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("prepare", "gold-template", "audit-expiry"):
        command = subparsers.add_parser(name)
        command.add_argument("--manifest", type=Path, required=True)

    for name in ("preflight", "calibrate", "run"):
        command = subparsers.add_parser(name)
        command.add_argument("--manifest", type=Path, required=True)
        command.add_argument("--real", action="store_true")

    run = subparsers.choices["run"]
    assert run is not None
    run.add_argument("--timeout-seconds", type=float, default=_DEFAULT_RUN_TIMEOUT_SECONDS)

    score = subparsers.add_parser("score")
    score.add_argument("--manifest", type=Path, required=True)
    score.add_argument("--results", type=Path)
    score.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    manifest = _load_manifest(args.manifest)
    if args.command in {"preflight", "calibrate", "run"}:
        if not args.real:
            raise RuntimeError("real long-video calls require the explicit --real flag")
        _load_env_file(args.env_file)

    if args.command == "prepare":
        verify_long_source_media(manifest)
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "scope": "long_video_manifest_validation",
                    "chunk_counts": {
                        source.source_id: len(build_complete_source_chunks(source, manifest.chunk))
                        for source in manifest.sources
                    },
                }
            )
        )
        return 0
    if args.command == "gold-template":
        _write_long_gold_templates(manifest)
        print(json.dumps({"status": "PASS", "scope": "long_video_gold_template"}))
        return 0
    if args.command == "audit-expiry":
        receipt = _audit_qwen_expiry(manifest, checked_at=datetime.now(UTC))
        _write_json(
            _expiry_audit_receipt_path(manifest),
            receipt.model_dump(mode="json"),
        )
        print(
            json.dumps(
                {
                    "status": receipt.status,
                    "scope": "long_video_qwen_expiry_audit",
                    "record_count": receipt.record_count,
                    "blocking_record_count": receipt.blocking_record_count,
                }
            ),
            file=sys.stdout if receipt.status == "provider_ttl_elapsed" else sys.stderr,
        )
        return 0 if receipt.status == "provider_ttl_elapsed" else 1
    if args.command == "preflight":
        verify_long_source_media(manifest)
        receipt_path = _preflight_receipt_path(manifest)
        try:
            runtime = _build_runtime()
            preflight_receipt = asyncio.run(
                _run_preflight(manifest, runtime, journal=_lifecycle_journal(manifest))
            )
        except LongPreflightFailure as error:
            preflight_receipt = _failed_preflight_receipt(manifest, error)
        except Exception as error:
            preflight_receipt = _failed_preflight_receipt(
                manifest,
                LongPreflightFailure("runtime", "configuration", _safe_error_code(error)),
            )
        _write_json(receipt_path, preflight_receipt.model_dump(mode="json"))
        print(
            json.dumps(
                {
                    "status": preflight_receipt.status,
                    "scope": "long_video_preflight",
                    "failure": preflight_receipt.failure,
                }
            ),
            file=sys.stderr if preflight_receipt.status != "PASS" else sys.stdout,
        )
        return 0 if preflight_receipt.status == "PASS" else 1
    if args.command == "calibrate":
        verify_long_source_media(manifest)
        _validate_preflight_receipt(manifest)
        runtime = _build_runtime()
        calibration_receipt = asyncio.run(
            _run_calibration(manifest, runtime, journal=_lifecycle_journal(manifest))
        )
        _write_json(
            _calibration_receipt_path(manifest),
            calibration_receipt.model_dump(mode="json"),
        )
        print(
            json.dumps(
                {
                    "status": calibration_receipt.status,
                    "scope": "long_video_concurrency_calibration",
                    "provider_concurrency_limit": calibration_receipt.provider_concurrency_limit,
                }
            )
        )
        return 0
    if args.command == "run":
        if args.timeout_seconds <= 0:
            raise RuntimeError("long-video run timeout must be positive")
        verify_long_source_media(manifest)
        # The 12 scored arm-runs are forbidden until both full-source gold
        # files are explicitly human-review locked. Preflight and calibration
        # remain capability gates and cannot produce a quality conclusion.
        reviewed_gold = _load_reviewed_gold(manifest)
        gold_sha256 = _reviewed_gold_sha256(reviewed_gold)
        _validate_preflight_receipt(manifest)
        concurrency = _load_calibrated_limit(manifest)
        runtime = _build_runtime()
        raw_result = asyncio.run(
            _run_experiment(
                manifest,
                runtime,
                provider_concurrency_limit=concurrency,
                timeout_seconds=args.timeout_seconds,
                pricing=_load_pricing(),
                gold_sha256=gold_sha256,
                journal=_lifecycle_journal(manifest),
            )
        )
        output = _raw_result_path(manifest)
        _write_json(output, raw_result.model_dump(mode="json"))
        print(
            json.dumps(
                {
                    "status": "PASS" if raw_result.experiment_status == "completed" else "FAIL",
                    "scope": "long_video_experiment",
                    "experiment_status": raw_result.experiment_status,
                    "completed_runs": sum(
                        run.status == LongRunStatus.COMPLETED for run in raw_result.runs
                    ),
                }
            ),
            file=sys.stderr if raw_result.experiment_status != "completed" else sys.stdout,
        )
        return 0 if raw_result.experiment_status == "completed" else 1
    if args.command == "score":
        raw_path = (
            _repo_path(args.results) if args.results is not None else _raw_result_path(manifest)
        )
        _assert_ignored_output(raw_path)
        output = _repo_path(args.output)
        _assert_ignored_output(output)
        raw = LongExperimentRawResult.model_validate_json(raw_path.read_text(encoding="utf-8"))
        if raw.manifest_sha256 != _file_sha256_from_manifest(manifest):
            raise RuntimeError("long-video results do not match the manifest")
        if raw.prompt_sha256 != manifest.prompt_sha256:
            raise RuntimeError("long-video results do not match the frozen prompt")
        reviewed_gold = _load_reviewed_gold(manifest)
        if raw.gold_sha256 != _reviewed_gold_sha256(reviewed_gold):
            raise RuntimeError("long-video reviewed gold changed after the experiment")
        aggregates = score_long_video_runs(reviewed_gold, raw.runs)
        report = render_long_video_report(
            aggregates,
            choose_long_video_arm(aggregates),
            protocol=_report_protocol(manifest, gold_sha256=raw.gold_sha256),
            expiry_audit_status=_load_expiry_audit_status(manifest),
            diagnostics={
                "proxy_mode": "explicit-socks5h",
                "preflight_status": "passed",
                "experiment_status": raw.experiment_status,
                "provider_concurrency_limit": raw.provider_concurrency_limit,
            },
        )
        _write_text(output, report)
        print(json.dumps({"status": "PASS", "scope": "long_video_sanitized_score"}))
        return 0
    raise AssertionError("unreachable command")


def _load_manifest(path: Path) -> LongExperimentManifest:
    manifest = load_long_experiment_manifest(_repo_path(path), repository_root=PROJECT_ROOT)
    resolved_sources = [
        source.model_copy(
            update={
                "source_path": _repo_path(source.source_path),
                "gold_path": _repo_path(source.gold_path),
            }
        )
        for source in manifest.sources
    ]
    output = manifest.output.model_copy(
        update={
            "working_root": _repo_path(manifest.output.working_root),
            "temporary_root": _repo_path(manifest.output.temporary_root),
            "raw_results_root": _repo_path(manifest.output.raw_results_root),
            "summary_path": _repo_path(manifest.output.summary_path),
        }
    )
    return manifest.model_copy(update={"sources": resolved_sources, "output": output})


def _repo_path(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def _assert_ignored_output(path: Path) -> None:
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(PROJECT_ROOT.resolve())
    except ValueError as error:
        raise RuntimeError("long-video output must remain in an ignored project root") from error
    if not relative.parts or relative.parts[0] not in _IGNORED_ROOTS:
        raise RuntimeError("long-video output must remain in an ignored project root")


def _load_env_file(path: Path) -> None:
    candidate = _repo_path(path)
    if not candidate.is_file():
        raise RuntimeError("long-video benchmark environment file is missing")
    load_dotenv(candidate, override=False)


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"missing long-video benchmark credential: {name}")
    return value


def _load_pricing() -> LongPricing:
    return LongPricing(
        seed_input_cny_per_1k=_optional_rate("LONG_BENCHMARK_SEED_INPUT_CNY_PER_1K"),
        seed_output_cny_per_1k=_optional_rate("LONG_BENCHMARK_SEED_OUTPUT_CNY_PER_1K"),
        qwen_input_cny_per_1k=_optional_rate("LONG_BENCHMARK_QWEN_INPUT_CNY_PER_1K"),
        qwen_output_cny_per_1k=_optional_rate("LONG_BENCHMARK_QWEN_OUTPUT_CNY_PER_1K"),
        asr_cny_per_minute=_optional_rate("LONG_BENCHMARK_ASR_CNY_PER_MINUTE"),
    )


def _optional_rate(name: str) -> float | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    try:
        rate = float(value)
    except ValueError as error:
        raise RuntimeError("long-video pricing configuration is invalid") from error
    if rate < 0:
        raise RuntimeError("long-video pricing configuration is invalid")
    return rate


def _build_runtime() -> LongBenchmarkRuntime:
    proxy_url = os.getenv("NATIVE_AV_PROXY_URL", "socks5h://127.0.0.1:7897").strip()
    ark_base_url = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3").strip()
    asr_url = os.getenv(
        "VOLC_ASR_URL", "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream"
    ).strip()
    _verify_explicit_proxy(proxy_url)
    for url in (ark_base_url, "https://dashscope.aliyuncs.com", asr_url):
        host = urlparse(url).hostname
        if host is None:
            raise RuntimeError("long-video provider URL has no host")
        require_long_proxy_for_fake_ip(resolve_long_addresses(host), proxy_url)

    transport = LongCurlTransport(
        proxy_url=proxy_url,
        timeout_seconds=180,
    )
    return LongBenchmarkRuntime(
        seed=LongSeedMediaProvider(
            api_key=_required_env("ARK_API_KEY"),
            base_url=ark_base_url,
            transport=transport,
        ),
        qwen=LongQwenVlProvider(
            api_key=_required_env("DASHSCOPE_API_KEY"),
            transport=transport,
        ),
        asr=LongAsrMediaProvider(
            LongProxyVolcAsrClient(
                api_key=_required_env("VOLC_ASR_API_KEY"),
                resource_id="volc.seedasr.sauc.duration",
                url=asr_url,
                pace_audio=False,
                retry_delays=(),
                proxy_url=proxy_url,
            )
        ),
    )


def _verify_explicit_proxy(proxy_url: str) -> None:
    parsed = urlparse(proxy_url)
    if parsed.scheme != "socks5h" or parsed.hostname is None or parsed.port is None:
        raise RuntimeError("long-video benchmark requires an explicit socks5h proxy")
    try:
        with socket.create_connection((parsed.hostname, parsed.port), timeout=2):
            return
    except OSError as error:
        raise RuntimeError("long-video benchmark proxy is unavailable") from error


async def _run_preflight(
    manifest: LongExperimentManifest,
    runtime: LongBenchmarkRuntime,
    *,
    journal: LongLifecycleJournal | None = None,
) -> LongPreflightReceipt:
    task_instructions = long_video_prompt(manifest.prompt_version)
    checks: list[LongPreflightCheck] = []
    availability: dict[str, bool] = {}
    seed_provider_unavailable = False
    components = (
        ("seed_visual", runtime.seed, manifest.models.seed_visual_model),
        ("seed_fusion", runtime.seed, manifest.models.seed_fusion_model),
        ("qwen_visual", runtime.qwen, manifest.models.qwen_visual_model),
        ("asr", runtime.asr, manifest.models.asr_resource),
    )
    for component, provider, model_id in components:
        if component.startswith("seed_") and seed_provider_unavailable:
            availability[component] = False
            _record_preflight_skip(
                checks,
                component,
                "configuration",
                error_code="seed_provider_unavailable",
            )
            _record_preflight_skip(
                checks,
                component,
                "minimal_call",
                error_code="seed_provider_unavailable",
            )
            continue
        configuration_ok, _ = await _capture_preflight_result(
            checks,
            component,
            "configuration",
            provider.check_configuration,
        )
        if not configuration_ok:
            availability[component] = False
            _record_preflight_skip(
                checks,
                component,
                "minimal_call",
                error_code="configuration_unavailable",
            )
            if component.startswith("seed_"):
                seed_provider_unavailable = True
                _disable_seed_peer(availability, checks, component)
            continue

        async def run_minimal_call(
            component: str = component,
            provider: LongSeedMediaProvider | LongQwenVlProvider | LongAsrMediaProvider = provider,
            model_id: str = model_id,
        ) -> None:
            await _preflight_minimal(component, provider, model_id)

        minimal_ok, _ = await _capture_preflight_result(
            checks,
            component,
            "minimal_call",
            run_minimal_call,
        )
        availability[component] = minimal_ok
        if component.startswith("seed_") and not minimal_ok:
            seed_provider_unavailable = True
            _disable_seed_peer(availability, checks, component)

    with tempfile.TemporaryDirectory(prefix="hachimi-long-preflight-") as temporary_directory:
        root = Path(temporary_directory)
        for source_index, source in enumerate(manifest.sources):
            source_root = root / f"source-{source_index}"
            probes = (
                ("synthetic_2_seconds", 2, True, 0.0),
                ("real_8_seconds", 8, False, source.representative_start_seconds),
                ("representative_60_seconds", 60, False, source.representative_start_seconds),
            )
            for stage, duration_seconds, synthetic, start_seconds in probes:

                async def create_probe(
                    source: LongExperimentSource = source,
                    duration_seconds: int = duration_seconds,
                    synthetic: bool = synthetic,
                    source_root: Path = source_root,
                    start_seconds: float = start_seconds,
                ) -> ProbeMedia:
                    return await create_long_probe_media(
                        source.source_path,
                        duration_seconds=duration_seconds,
                        synthetic=synthetic,
                        root=source_root,
                        start_seconds=start_seconds,
                        qwen_video_projection_version=manifest.qwen_video_projection_version,
                    )

                probe_ready, probe = await _capture_preflight_result(
                    checks,
                    "source_media",
                    stage,
                    create_probe,
                    source_id=source.source_id,
                )
                if not probe_ready or probe is None:
                    _record_unavailable_probe_components(
                        checks,
                        availability,
                        stage,
                        source.source_id,
                        error_code="source_probe_unavailable",
                    )
                    continue
                await _preflight_probe_stage(
                    runtime,
                    probe,
                    float(duration_seconds),
                    stage,
                    source_root,
                    task_instructions,
                    source_id=source.source_id,
                    availability=availability,
                    checks=checks,
                    journal=journal,
                )
    return _preflight_receipt_from_checks(manifest, checks)


def _disable_seed_peer(
    availability: dict[str, bool],
    checks: list[LongPreflightCheck],
    failed_component: str,
) -> None:
    peer = "seed_fusion" if failed_component == "seed_visual" else "seed_visual"
    if availability.get(peer, False):
        availability[peer] = False
        _record_preflight_skip(
            checks,
            peer,
            "provider_dependency",
            error_code="seed_provider_unavailable",
        )


async def _capture_preflight_result[PreflightResult](
    checks: list[LongPreflightCheck],
    component: str,
    stage: str,
    operation: Callable[[], Awaitable[PreflightResult]],
    *,
    source_id: str | None = None,
) -> tuple[bool, PreflightResult | None]:
    """Record an operation without letting one Provider short-circuit peers."""

    try:
        result = await operation()
    except asyncio.CancelledError:
        raise
    except Exception as error:
        _record_preflight_check(
            checks,
            component,
            stage,
            source_id=source_id,
            status="failed",
            error_code=_safe_error_code(error),
            expires_at=_expiry_datetime_from_value(getattr(error, "expires_at", None)),
        )
        return False, None
    _record_preflight_check(
        checks,
        component,
        stage,
        source_id=source_id,
        status="passed",
        expires_at=(result if isinstance(result, datetime) else None),
    )
    return True, result


def _record_preflight_check(
    checks: list[LongPreflightCheck],
    component: str,
    stage: str,
    *,
    source_id: str | None = None,
    status: Literal["passed", "failed", "skipped"],
    error_code: str | None = None,
    expires_at: datetime | None = None,
) -> None:
    checks.append(
        LongPreflightCheck(
            component=component,
            stage=stage,
            source_id=source_id,
            status=status,
            error_code=error_code,
            expires_at=expires_at,
        )
    )


def _record_preflight_skip(
    checks: list[LongPreflightCheck],
    component: str,
    stage: str,
    *,
    source_id: str | None = None,
    error_code: str,
) -> None:
    _record_preflight_check(
        checks,
        component,
        stage,
        source_id=source_id,
        status="skipped",
        error_code=error_code,
    )


def _record_unavailable_probe_components(
    checks: list[LongPreflightCheck],
    availability: dict[str, bool],
    stage: str,
    source_id: str,
    *,
    error_code: str,
) -> None:
    for component in _PREFLIGHT_COMPONENTS:
        _record_preflight_skip(
            checks,
            component,
            stage,
            source_id=source_id,
            error_code=(
                error_code if availability.get(component, False) else "component_unavailable"
            ),
        )


def _preflight_receipt_from_checks(
    manifest: LongExperimentManifest,
    checks: list[LongPreflightCheck],
) -> LongPreflightReceipt:
    failed_or_skipped = next((check for check in checks if check.status != "passed"), None)
    passed_components = [
        component
        for component in _PREFLIGHT_COMPONENTS
        if any(check.component == component for check in checks)
        and all(check.status == "passed" for check in checks if check.component == component)
    ]
    failure: dict[str, str] | None = None
    if failed_or_skipped is not None:
        failure = {
            "component": failed_or_skipped.component,
            "stage": failed_or_skipped.stage,
            "error_code": failed_or_skipped.error_code or "preflight_incomplete",
        }
        if failed_or_skipped.source_id is not None:
            failure["source_id"] = failed_or_skipped.source_id
    return LongPreflightReceipt(
        status="PASS" if failed_or_skipped is None else "FAIL",
        created_at=datetime.now(UTC),
        manifest_sha256=_file_sha256_from_manifest(manifest),
        prompt_sha256=manifest.prompt_sha256,
        models=manifest.models.model_dump(),
        passed_components=passed_components,
        failure=failure,
        checks=checks,
    )


async def _preflight_step(
    component: str,
    stage: str,
    operation: Callable[[], Awaitable[None]],
) -> None:
    try:
        await operation()
    except asyncio.CancelledError:
        raise
    except Exception as error:
        raise LongPreflightFailure(component, stage, _safe_error_code(error)) from error


async def _preflight_minimal(
    component: str,
    provider: LongSeedMediaProvider | LongQwenVlProvider | LongAsrMediaProvider,
    model_id: str,
) -> None:
    async def operation() -> None:
        await provider.minimal_call(model_id)

    await _preflight_step(
        component,
        "minimal_call",
        lambda: retry_long_operation(operation),
    )


async def _preflight_probe_stage(
    runtime: LongBenchmarkRuntime,
    probe: ProbeMedia,
    duration_seconds: float,
    stage: str,
    root: Path,
    task_instructions: str,
    *,
    source_id: str,
    availability: dict[str, bool],
    checks: list[LongPreflightCheck],
    journal: LongLifecycleJournal | None = None,
) -> None:
    processor = LocalMediaProcessor(
        temp_root=root,
        max_source_duration_seconds=60,
        command_timeout_seconds=180,
    )
    async with AsyncExitStack() as contexts:
        prepared: Any | None = None
        if availability.get("seed_visual", False) or availability.get("asr", False):
            prepared_ok, prepared = await _capture_preflight_result(
                checks,
                "source_media",
                stage,
                lambda: contexts.enter_async_context(
                    processor.prepare_source(probe.av_path, duration_seconds)
                ),
                source_id=source_id,
            )
            if not prepared_ok:
                _record_preflight_skip(
                    checks,
                    "seed_visual",
                    stage,
                    source_id=source_id,
                    error_code=(
                        "source_media_unavailable"
                        if availability.get("seed_visual", False)
                        else "component_unavailable"
                    ),
                )
                _record_preflight_skip(
                    checks,
                    "asr",
                    stage,
                    source_id=source_id,
                    error_code=(
                        "source_media_unavailable"
                        if availability.get("asr", False)
                        else "component_unavailable"
                    ),
                )
        else:
            _record_preflight_skip(
                checks,
                "seed_visual",
                stage,
                source_id=source_id,
                error_code="component_unavailable",
            )
            _record_preflight_skip(
                checks,
                "asr",
                stage,
                source_id=source_id,
                error_code="component_unavailable",
            )

        if prepared is not None:
            if availability.get("seed_visual", False):
                visual_ok, _ = await _capture_preflight_result(
                    checks,
                    "seed_visual",
                    stage,
                    lambda: _seed_visual_probe_from_prepared(
                        runtime.seed,
                        prepared,
                        duration_seconds,
                        task_instructions,
                    ),
                    source_id=source_id,
                )
                if not visual_ok:
                    availability["seed_visual"] = False
                    availability["seed_fusion"] = False
            if availability.get("asr", False):
                asr_ok, transcript = await _capture_preflight_result(
                    checks,
                    "asr",
                    stage,
                    lambda: _asr_probe(runtime.asr, prepared.audio_path, stage, source_id),
                    source_id=source_id,
                )
                if not asr_ok:
                    availability["asr"] = False
            else:
                transcript = None
        else:
            transcript = None

        if availability.get("seed_fusion", False) and transcript is not None:
            fusion_ok, _ = await _capture_preflight_result(
                checks,
                "seed_fusion",
                stage,
                lambda: _seed_fusion_probe(
                    runtime.seed,
                    transcript.model_dump(mode="json"),
                    task_instructions,
                ),
                source_id=source_id,
            )
            if not fusion_ok:
                availability["seed_fusion"] = False
                availability["seed_visual"] = False
        elif availability.get("seed_fusion", False):
            _record_preflight_skip(
                checks,
                "seed_fusion",
                stage,
                source_id=source_id,
                error_code="asr_evidence_unavailable",
            )
        else:
            _record_preflight_skip(
                checks,
                "seed_fusion",
                stage,
                source_id=source_id,
                error_code="component_unavailable",
            )

        if availability.get("qwen_visual", False):
            # ProbeMedia.silent_video_path is the frozen Qwen projection.
            # Do not route it through LocalMediaProcessor.prepare(), whose
            # general-purpose MPEG-4 window would silently bypass that profile.
            qwen_ok, _ = await _capture_preflight_result(
                checks,
                "qwen_visual",
                stage,
                lambda: _qwen_visual_probe(
                    runtime.qwen,
                    probe.silent_video_path,
                    task_instructions,
                    source_id=source_id,
                    stage=stage,
                    journal=journal,
                ),
                source_id=source_id,
            )
            if not qwen_ok:
                availability["qwen_visual"] = False
        else:
            _record_preflight_skip(
                checks,
                "qwen_visual",
                stage,
                source_id=source_id,
                error_code="component_unavailable",
            )


async def _seed_visual_probe_from_prepared(
    provider: LongSeedMediaProvider,
    prepared: Any,
    duration_seconds: float,
    task_instructions: str,
) -> None:
    if prepared.contact_sheet_ready is not None:
        await prepared.contact_sheet_ready
    if prepared.contact_sheet_path is None:
        raise LongProviderContractError("contact_sheet_missing")
    await _seed_visual_probe(
        provider,
        prepared.contact_sheet_path,
        prepared.contact_sheet_timestamps,
        duration_seconds,
        task_instructions,
    )


async def _seed_visual_probe(
    provider: LongSeedMediaProvider,
    contact_sheet_path: Path,
    timestamps: tuple[float, ...],
    duration_seconds: float,
    task_instructions: str,
) -> None:
    await retry_long_operation(
        lambda: provider.analyze_contact_sheet(
            contact_sheet_path,
            frame_times_seconds=timestamps,
            window_start_seconds=0,
            window_end_seconds=duration_seconds,
            prompt=task_instructions + VISUAL_ONLY_SUFFIX,
        )
    )


async def _asr_probe(
    provider: LongAsrMediaProvider,
    audio_path: Path,
    stage: str,
    source_id: str,
) -> Any:
    handle = await provider.upload("volc.seedasr.sauc.duration", audio_path, "audio")
    try:
        return await retry_long_operation(
            lambda: provider.transcribe(
                handle,
                request_id=_preflight_asr_request_id(stage, source_id),
            )
        )
    finally:
        await provider.cleanup(handle)
        if await provider.verify_cleanup(handle) == CleanupOutcome.FAILED:
            raise LongPreflightFailure("asr", stage, "cleanup_error")


def _preflight_asr_request_id(stage: str, source_id: str) -> str:
    digest = hashlib.sha256(f"{source_id}:{stage}".encode()).hexdigest()[:32]
    return f"long-preflight-{digest}"


async def _seed_fusion_probe(
    provider: LongSeedMediaProvider,
    transcript: dict[str, object],
    task_instructions: str,
) -> None:
    await retry_long_operation(
        lambda: provider.complete_text(
            long_fusion_prompt(
                transcript,
                CandidateEnvelope(actions=[]),
                instructions=task_instructions,
            )
        )
    )


async def _qwen_visual_probe(
    provider: LongQwenVlProvider,
    silent_video_path: Path,
    task_instructions: str,
    *,
    source_id: str,
    stage: str,
    journal: LongLifecycleJournal | None,
) -> datetime:
    upload_started = perf_counter()
    lifecycle = LongQwenExpiryLifecycle(
        model_id="qwen3-vl-flash-2026-01-22",
        persist=(
            None
            if journal is None
            else lambda record: _append_preflight_lifecycle(
                journal,
                source_id=source_id,
                stage=stage,
                record=record,
            )
        ),
    )

    try:
        # Multipart upload is intentionally one-shot: a transport-ambiguous
        # object would otherwise escape this run's lifecycle audit.
        handle = await provider.upload(
            "qwen3-vl-flash-2026-01-22",
            silent_video_path,
            "video",
            on_expiry=lifecycle.record_before_upload,
        )
    except BaseException as error:
        try:
            lifecycle.persist_upload_failure(error)
        except BaseException:
            if isinstance(error, asyncio.CancelledError):
                raise error from None
            raise
        if isinstance(error, LongBenchmarkTransportError):
            raise LongProviderContractError(f"qwen_upload_{error.code}") from error
        raise
    upload_seconds = perf_counter() - upload_started
    primary_error: BaseException | None = None
    expires_at: datetime | None = None
    try:
        expires_at = datetime.fromisoformat(lifecycle.bind_handle(handle.expires_at))
        await retry_long_operation(
            lambda: provider.analyze(handle, task_instructions + VISUAL_ONLY_SUFFIX)
        )
    except BaseException as error:
        primary_error = error
        raise
    finally:
        cleanup_started = perf_counter()
        cleanup_failed = False
        try:
            await provider.cleanup(handle)
        except BaseException:
            cleanup_failed = True
        try:
            cleanup_outcome = await provider.verify_cleanup(handle)
        except BaseException:
            cleanup_outcome = CleanupOutcome.FAILED
        lifecycle_failed = (
            cleanup_failed or cleanup_outcome != CleanupOutcome.EXPIRY_RECORDED or lifecycle.invalid
        )
        if lifecycle_failed:
            _append_preflight_lifecycle(
                cast(LongLifecycleJournal, journal),
                source_id=source_id,
                stage=stage,
                record=LongMediaLifecycleRecord(
                    provider="qwen",
                    model_id="qwen3-vl-flash-2026-01-22",
                    media_kind="video",
                    upload_seconds=upload_seconds,
                    cleanup_seconds=perf_counter() - cleanup_started,
                    cleanup_outcome=CleanupOutcome.FAILED,
                    expires_at=lifecycle.handle_expiry,
                ),
            )
        if lifecycle_failed and primary_error is None:
            raise LongPreflightFailure(
                "qwen_visual",
                "cleanup",
                "cleanup_error",
                expires_at=expires_at,
            )
    if expires_at is None:
        raise LongExecutionError("qwen_expiry_missing")
    return expires_at


def _failed_preflight_receipt(
    manifest: LongExperimentManifest,
    failure: LongPreflightFailure,
) -> LongPreflightReceipt:
    return LongPreflightReceipt(
        status="FAIL",
        created_at=datetime.now(UTC),
        manifest_sha256=_file_sha256_from_manifest(manifest),
        prompt_sha256=manifest.prompt_sha256,
        models=manifest.models.model_dump(),
        passed_components=[],
        failure={
            "component": failure.component,
            "stage": failure.stage,
            "error_code": failure.code,
        },
        checks=[
            LongPreflightCheck(
                component=failure.component,
                stage=failure.stage,
                status="failed",
                error_code=failure.code,
            )
        ],
    )


async def calibrate_provider_concurrency(
    maximum: int,
    trial: Callable[[int], Awaitable[bool]],
) -> tuple[int, list[int]]:
    if maximum < 1 or maximum > 3:
        raise ValueError("long-video provider concurrency must be between one and three")
    passed = 0
    attempted: list[int] = []
    for candidate in range(1, maximum + 1):
        attempted.append(candidate)
        if not await trial(candidate):
            break
        passed = candidate
    if passed == 0:
        raise RuntimeError("long-video concurrency calibration has no safe provider limit")
    return passed, attempted


async def _run_calibration(
    manifest: LongExperimentManifest,
    runtime: LongBenchmarkRuntime,
    *,
    journal: LongLifecycleJournal | None = None,
) -> LongCalibrationReceipt:
    if journal is None:
        raise LongExecutionError("qwen_lifecycle_sink_missing")
    qwen_lifecycle_by_limit: dict[int, list[LongMediaLifecycleRecord]] = {}
    active_capacity: int | None = None

    def lifecycle_sink(key: LongRunKey, record: LongMediaLifecycleRecord) -> None:
        if record.provider != "qwen":
            return
        if active_capacity is None:
            raise RuntimeError("long-video calibration lifecycle escaped its capacity trial")
        qwen_lifecycle_by_limit.setdefault(active_capacity, []).append(record)
        _append_run_lifecycle(
            journal,
            "calibration",
            key,
            record,
            stage=f"capacity_{active_capacity}",
        )

    async with _representative_sources(manifest) as prepared_sources:

        async def trial(capacity: int) -> bool:
            nonlocal active_capacity
            active_capacity = capacity
            executor = LongArmExecutor(
                prepared_sources=prepared_sources,
                providers=runtime.providers,
                asr_model_id=manifest.models.asr_resource,
                qwen_model_id=manifest.models.qwen_visual_model,
                retry_delays=(1.0, 2.0),
                prompt_version=cast(LongPromptVersion, manifest.prompt_version),
                lifecycle_sink=lifecycle_sink,
            )
            permits = ProviderPermits({"asr": capacity, "seed": capacity, "qwen": capacity})
            keys = [
                key
                for run_index in range(1, 4)
                for key in (
                    LongRunKey(manifest.sources[0].source_id, ArmId.SEED_CONTACT_SHEET, run_index),
                    LongRunKey(manifest.sources[1].source_id, ArmId.QWEN_VIDEO, run_index),
                )
            ]
            try:
                runs = await asyncio.gather(
                    *(
                        _safe_execute(
                            executor,
                            key,
                            permits,
                            source_duration_seconds=60,
                            preprocessing_seconds=0,
                            timeout_seconds=180,
                            pricing=None,
                        )
                        for key in keys
                    )
                )
                return all(
                    run.status == LongRunStatus.COMPLETED and run.full_coverage and run.cleanup_ok
                    for run in runs
                )
            finally:
                active_capacity = None

        limit, attempted = await calibrate_provider_concurrency(3, trial)
    safe_lifecycle, terminal_unsafe_lifecycle = _partition_calibration_lifecycle(
        qwen_lifecycle_by_limit,
        selected_limit=limit,
        attempted_limits=attempted,
    )
    return LongCalibrationReceipt(
        status="PASS",
        created_at=datetime.now(UTC),
        manifest_sha256=_file_sha256_from_manifest(manifest),
        prompt_sha256=manifest.prompt_sha256,
        provider_concurrency_limit=limit,
        attempted_limits=attempted,
        qwen_lifecycle_audit=safe_lifecycle,
        terminal_unsafe_qwen_lifecycle_audit=terminal_unsafe_lifecycle,
    )


def _partition_calibration_lifecycle(
    records_by_limit: Mapping[int, Sequence[LongMediaLifecycleRecord]],
    *,
    selected_limit: int,
    attempted_limits: Sequence[int],
) -> tuple[list[LongMediaLifecycleRecord], list[LongMediaLifecycleRecord]]:
    safe = [
        record
        for limit in attempted_limits
        if limit <= selected_limit
        for record in records_by_limit.get(limit, ())
    ]
    terminal_limit = selected_limit + 1
    terminal = (
        list(records_by_limit.get(terminal_limit, ())) if terminal_limit in attempted_limits else []
    )
    return safe, terminal


@asynccontextmanager
async def _representative_sources(
    manifest: LongExperimentManifest,
) -> AsyncIterator[dict[str, LongPreparedSource]]:
    with tempfile.TemporaryDirectory(prefix="hachimi-long-calibration-") as temporary_directory:
        root = Path(temporary_directory)
        first_probe, second_probe = await asyncio.gather(
            create_long_probe_media(
                manifest.sources[0].source_path,
                duration_seconds=60,
                synthetic=False,
                root=root / "source-0",
                start_seconds=manifest.sources[0].representative_start_seconds,
                qwen_video_projection_version=manifest.qwen_video_projection_version,
            ),
            create_long_probe_media(
                manifest.sources[1].source_path,
                duration_seconds=60,
                synthetic=False,
                root=root / "source-1",
                start_seconds=manifest.sources[1].representative_start_seconds,
                qwen_video_projection_version=manifest.qwen_video_projection_version,
            ),
        )
        processor = LocalMediaProcessor(
            temp_root=root,
            max_source_duration_seconds=60,
            command_timeout_seconds=180,
        )
        async with (
            processor.prepare_source(first_probe.av_path, 60) as first_media,
            processor.prepare_source(second_probe.av_path, 60) as second_media,
        ):
            for prepared in (first_media, second_media):
                if prepared.contact_sheet_ready is not None:
                    await prepared.contact_sheet_ready
                if prepared.contact_sheet_path is None:
                    raise RuntimeError("long-video representative contact sheet is missing")
            yield {
                manifest.sources[0].source_id: _prepared_representative_source(
                    manifest.sources[0], first_probe, first_media
                ),
                manifest.sources[1].source_id: _prepared_representative_source(
                    manifest.sources[1], second_probe, second_media
                ),
            }


def _prepared_representative_source(
    source: LongExperimentSource,
    probe: ProbeMedia,
    media: Any,
) -> LongPreparedSource:
    if media.contact_sheet_path is None:
        raise RuntimeError("long-video representative contact sheet is missing")
    chunk = LongVideoChunk(
        source_id=source.source_id,
        index=0,
        start_seconds=0,
        end_seconds=60,
        duration_seconds=60,
    )
    return LongPreparedSource(
        source_id=source.source_id,
        duration_seconds=60.0,
        audio_path=media.audio_path,
        chunks=(
            LongPreparedChunk(
                chunk=chunk,
                silent_video_path=probe.silent_video_path,
                contact_sheet_path=media.contact_sheet_path,
                frame_times_seconds=media.contact_sheet_timestamps,
            ),
        ),
    )


async def _run_experiment(
    manifest: LongExperimentManifest,
    runtime: LongBenchmarkRuntime,
    *,
    provider_concurrency_limit: int,
    timeout_seconds: float,
    pricing: LongPricing | None,
    gold_sha256: str,
    journal: LongLifecycleJournal | None = None,
) -> LongExperimentRawResult:
    if journal is None:
        raise LongExecutionError("qwen_lifecycle_sink_missing")
    prepared_sources: dict[str, LongPreparedSource] = {}
    preprocessing_seconds: dict[str, float] = {}
    preparer = LongMediaPreparer(
        temp_root=manifest.output.temporary_root,
        qwen_video_projection_version=manifest.qwen_video_projection_version,
    )
    first_started = perf_counter()
    async with preparer.prepare_source(manifest.sources[0], manifest.chunk) as first:
        prepared_sources[first.source_id] = first
        preprocessing_seconds[first.source_id] = perf_counter() - first_started
        second_started = perf_counter()
        async with preparer.prepare_source(manifest.sources[1], manifest.chunk) as second:
            prepared_sources[second.source_id] = second
            preprocessing_seconds[second.source_id] = perf_counter() - second_started
            executor = LongArmExecutor(
                prepared_sources=prepared_sources,
                providers=runtime.providers,
                asr_model_id=manifest.models.asr_resource,
                qwen_model_id=manifest.models.qwen_visual_model,
                checkpoints=LongCheckpointStore(
                    manifest.output.working_root / "checkpoints",
                    manifest_sha256=_file_sha256_from_manifest(manifest),
                ),
                prompt_version=cast(LongPromptVersion, manifest.prompt_version),
                lifecycle_sink=lambda key, record: _append_run_lifecycle(
                    journal,
                    "experiment",
                    key,
                    record,
                ),
            )
            sources = {source.source_id: source for source in manifest.sources}
            runner = LongStudyRunner(
                build_ab_ba_waves(
                    seven_source_id=manifest.sources[0].source_id,
                    nineteen_source_id=manifest.sources[1].source_id,
                    repetitions=manifest.repetitions,
                ),
                ProviderPermits(
                    {
                        "asr": provider_concurrency_limit,
                        "seed": provider_concurrency_limit,
                        "qwen": provider_concurrency_limit,
                    }
                ),
            )

            async def execute_safe(key: LongRunKey, permits: ProviderPermits) -> LongVideoRun:
                return await _safe_execute(
                    executor,
                    key,
                    permits,
                    source_duration_seconds=sources[key.source_id].duration_seconds,
                    preprocessing_seconds=preprocessing_seconds[key.source_id],
                    timeout_seconds=timeout_seconds,
                    pricing=pricing,
                )

            runs = await runner.run(execute_safe)
    experiment_status: Literal["completed", "incomplete"] = (
        "completed"
        if all(
            run.status == LongRunStatus.COMPLETED
            and run.cleanup_ok
            and not run.resumed_from_checkpoint
            for run in runs
        )
        else "incomplete"
    )
    return LongExperimentRawResult(
        version=1,
        manifest_sha256=_file_sha256_from_manifest(manifest),
        prompt_sha256=manifest.prompt_sha256,
        gold_sha256=gold_sha256,
        experiment_status=experiment_status,
        provider_concurrency_limit=provider_concurrency_limit,
        preprocessing_seconds_by_source=preprocessing_seconds,
        runs=runs,
    )


async def _safe_execute(
    executor: LongArmExecutor,
    key: LongRunKey,
    permits: ProviderPermits,
    *,
    source_duration_seconds: float,
    preprocessing_seconds: float,
    timeout_seconds: float,
    pricing: LongPricing | None,
) -> LongVideoRun:
    audit = LongExecutionAudit()
    try:
        async with asyncio.timeout(timeout_seconds):
            result = await executor.execute(key, permits, audit=audit)
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        return _failed_run(
            key,
            LongRunStatus.TIMEOUT,
            "timeout",
            preprocessing_seconds,
            lifecycle_audit=list(audit.lifecycle),
            # A timed-out arm never qualifies as cleanup-verified, even when
            # individual handles were released before the deadline.
            cleanup_ok=False,
        )
    except Exception as error:
        error_code = _safe_error_code(error)
        status = (
            LongRunStatus.SCHEMA_ERROR
            if "schema" in error_code or error_code.startswith("chunk_candidate_")
            else LongRunStatus.PROVIDER_ERROR
        )
        lifecycle_audit = tuple(audit.lifecycle) or getattr(error, "lifecycle_audit", ())
        cleanup_failed = audit.cleanup_failed or getattr(error, "cleanup_failed", True)
        return _failed_run(
            key,
            status,
            error_code,
            preprocessing_seconds,
            lifecycle_audit=(
                list(lifecycle_audit)
                if isinstance(lifecycle_audit, tuple)
                and all(isinstance(item, LongMediaLifecycleRecord) for item in lifecycle_audit)
                else []
            ),
            cleanup_ok=(
                not cleanup_failed
                and isinstance(cleanup_failed, bool)
                and all(item.cleanup_outcome != CleanupOutcome.FAILED for item in lifecycle_audit)
            ),
        )
    return _completed_run(result, source_duration_seconds, preprocessing_seconds, pricing)


def _completed_run(
    result: LongArmResult,
    source_duration_seconds: float,
    preprocessing_seconds: float,
    pricing: LongPricing | None = None,
) -> LongVideoRun:
    cleanup_ok = not result.cleanup_failed and all(
        record.cleanup_outcome != CleanupOutcome.FAILED for record in result.lifecycle
    )
    return LongVideoRun(
        source_id=result.key.source_id,
        arm=result.key.arm_id,
        run_index=result.key.repetition,
        status=LongRunStatus.COMPLETED,
        candidates=list(result.candidates),
        full_coverage=abs(result.completed_coverage_seconds - source_duration_seconds) <= 1e-6,
        cleanup_ok=cleanup_ok,
        resumed_from_checkpoint=result.resumed_from_checkpoint,
        model_weight_violation_count=result.weight_violation_count,
        lifecycle_audit=list(result.lifecycle),
        queue_seconds=sum(result.queue_wait_seconds.values()),
        upload_seconds=result.upload_seconds,
        preprocessing_seconds=preprocessing_seconds,
        asr_seconds=result.asr_seconds,
        visual_seconds=result.visual_seconds,
        fusion_seconds=result.fusion_seconds,
        cleanup_seconds=result.cleanup_seconds,
        input_tokens=(
            sum(input_tokens for input_tokens, _ in result.token_usage_by_provider.values())
            if result.token_usage_known
            else None
        ),
        output_tokens=(
            sum(output_tokens for _, output_tokens in result.token_usage_by_provider.values())
            if result.token_usage_known
            else None
        ),
        first_candidate_seconds=result.first_candidate_seconds,
        completed_seconds=result.total_seconds,
        cost_cny=_estimated_cost(result, source_duration_seconds, pricing),
    )


def _failed_run(
    key: LongRunKey,
    status: LongRunStatus,
    error_code: str,
    preprocessing_seconds: float,
    *,
    lifecycle_audit: list[LongMediaLifecycleRecord] | None = None,
    cleanup_ok: bool = False,
) -> LongVideoRun:
    return LongVideoRun(
        source_id=key.source_id,
        arm=key.arm_id,
        run_index=key.repetition,
        status=status,
        error_code=error_code,
        cleanup_ok=cleanup_ok,
        preprocessing_seconds=preprocessing_seconds,
        lifecycle_audit=lifecycle_audit or [],
    )


def _estimated_cost(
    result: LongArmResult,
    source_duration_seconds: float,
    pricing: LongPricing | None,
) -> float | None:
    if pricing is None or not result.token_usage_known or pricing.asr_cny_per_minute is None:
        return None
    total = pricing.asr_cny_per_minute * source_duration_seconds / 60
    for provider, (input_tokens, output_tokens) in result.token_usage_by_provider.items():
        if provider == "seed":
            input_rate = pricing.seed_input_cny_per_1k
            output_rate = pricing.seed_output_cny_per_1k
        elif provider == "qwen":
            input_rate = pricing.qwen_input_cny_per_1k
            output_rate = pricing.qwen_output_cny_per_1k
        else:
            return None
        if input_rate is None or output_rate is None:
            return None
        total += input_rate * input_tokens / 1000 + output_rate * output_tokens / 1000
    return round(total, 6)


def _safe_error_code(error: BaseException) -> str:
    value = getattr(error, "code", None)
    if isinstance(value, str) and _ERROR_CODE.fullmatch(value):
        return value
    if isinstance(error, LongProviderContractError):
        return "provider_contract_error"
    if isinstance(error, LongBenchmarkTransportError):
        return "provider_error"
    return "provider_error"


def _write_long_gold_templates(manifest: LongExperimentManifest) -> None:
    for source in manifest.sources:
        if source.gold_path.exists():
            continue
        _write_json(
            source.gold_path,
            {
                "source_id": source.source_id,
                "duration_seconds": source.duration_seconds,
                "gold_version": source.gold_version,
                "reviewed": False,
                "events": [],
            },
        )


def _load_reviewed_gold(manifest: LongExperimentManifest) -> list[LongVideoSourceGold]:
    gold: list[LongVideoSourceGold] = []
    for source in manifest.sources:
        try:
            payload = json.loads(source.gold_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError) as error:
            raise RuntimeError("long-video gold annotation is unavailable") from error
        if not isinstance(payload, dict):
            raise RuntimeError("long-video gold annotation is invalid")
        try:
            annotation = LongVideoSourceGold.model_validate(payload)
        except Exception as error:
            raise RuntimeError("long-video gold annotation is not review-locked") from error
        try:
            validate_gold_against_source_contract(source, annotation)
        except ValueError as error:
            raise RuntimeError("long-video gold annotation does not match the manifest") from error
        gold.append(annotation)
    return gold


def _reviewed_gold_sha256(gold: list[LongVideoSourceGold]) -> str:
    canonical = [
        item.model_dump(mode="json") for item in sorted(gold, key=lambda item: item.source_id)
    ]
    payload = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _expiry_datetime_from_value(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _lifecycle_journal_path(manifest: LongExperimentManifest) -> Path:
    manifest_sha256 = _file_sha256_from_manifest(manifest)
    path = manifest.output.working_root / f"long-video-lifecycle-{manifest_sha256[:12]}.json"
    _assert_ignored_output(path)
    return path


def _lifecycle_journal(manifest: LongExperimentManifest) -> LongLifecycleJournal:
    manifest_sha256 = _file_sha256_from_manifest(manifest)
    path = _lifecycle_journal_path(manifest)
    return LongLifecycleJournal(path, manifest_sha256=manifest_sha256)


def _audit_qwen_expiry(
    manifest: LongExperimentManifest,
    *,
    checked_at: datetime,
) -> LongExpiryAuditReceipt:
    if checked_at.tzinfo is None:
        raise RuntimeError("long-video expiry audit time must include a timezone")
    checked_at = checked_at.astimezone(UTC)
    manifest_sha256 = _file_sha256_from_manifest(manifest)
    journal_path = _lifecycle_journal_path(manifest)
    journal = LongLifecycleJournal(
        journal_path,
        manifest_sha256=manifest_sha256,
    )
    payload = journal.snapshot()
    source_ids = {source.source_id for source in manifest.sources}
    blocking_record_count = 0
    expiries: list[datetime] = []
    for entry in payload.entries:
        record = entry.lifecycle
        expires_at = _expiry_datetime_from_value(record.expires_at)
        recorded_at = entry.recorded_at
        recorded_at_is_valid = recorded_at.tzinfo is not None
        if expires_at is not None:
            expiries.append(expires_at)
        if (
            entry.arm != ArmId.QWEN_VIDEO
            or entry.source_id not in source_ids
            or record.provider != "qwen"
            or record.model_id != manifest.models.qwen_visual_model
            or record.media_kind != "video"
            or record.cleanup_outcome != CleanupOutcome.EXPIRY_RECORDED
            or expires_at is None
            or not recorded_at_is_valid
            or (
                recorded_at_is_valid
                and expires_at is not None
                and expires_at <= recorded_at.astimezone(UTC)
            )
        ):
            blocking_record_count += 1
    max_expires_at = max(expiries, default=None)
    if blocking_record_count:
        status: LongExpiryAuditStatus = "lifecycle_failed"
    elif not payload.entries or max_expires_at is None:
        status = "pending"
    elif checked_at >= max_expires_at + timedelta(seconds=LONG_EXPIRY_AUDIT_MARGIN_SECONDS):
        status = "provider_ttl_elapsed"
    else:
        status = "pending"
    return LongExpiryAuditReceipt(
        manifest_sha256=manifest_sha256,
        journal_sha256=journal.snapshot_sha256,
        checked_at=checked_at,
        status=status,
        record_count=len(payload.entries),
        blocking_record_count=blocking_record_count,
        max_expires_at=max_expires_at,
    )


def _load_expiry_audit_status(manifest: LongExperimentManifest) -> LongExpiryAuditStatus:
    receipt_path = _expiry_audit_receipt_path(manifest)
    if not receipt_path.is_file():
        return "pending"
    try:
        receipt = LongExpiryAuditReceipt.model_validate_json(
            receipt_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return "lifecycle_failed"
    manifest_sha256 = _file_sha256_from_manifest(manifest)
    if receipt.manifest_sha256 != manifest_sha256:
        return "lifecycle_failed"
    journal_path = _lifecycle_journal_path(manifest)
    if receipt.journal_sha256 is None:
        return "pending"
    if not journal_path.is_file():
        return "pending"
    try:
        journal = LongLifecycleJournal(journal_path, manifest_sha256=manifest_sha256)
    except RuntimeError:
        return "lifecycle_failed"
    if journal.snapshot_sha256 != receipt.journal_sha256:
        return "pending"
    return receipt.status


def _append_preflight_lifecycle(
    journal: LongLifecycleJournal,
    *,
    source_id: str,
    stage: str,
    record: LongMediaLifecycleRecord,
) -> None:
    journal.append(
        LongLifecycleJournalEntry(
            phase="preflight",
            source_id=source_id,
            arm=ArmId.QWEN_VIDEO,
            stage=stage,
            lifecycle=record,
        )
    )


def _append_run_lifecycle(
    journal: LongLifecycleJournal,
    phase: Literal["calibration", "experiment"],
    key: LongRunKey,
    record: LongMediaLifecycleRecord,
    *,
    stage: str | None = None,
) -> None:
    if record.provider != "qwen":
        return
    journal.append(
        LongLifecycleJournalEntry(
            phase=phase,
            source_id=key.source_id,
            arm=key.arm_id,
            run_index=key.repetition,
            stage=stage,
            lifecycle=record,
        )
    )


def _preflight_receipt_path(manifest: LongExperimentManifest) -> Path:
    return manifest.output.working_root / "long-video-preflight.json"


def _calibration_receipt_path(manifest: LongExperimentManifest) -> Path:
    return manifest.output.working_root / "long-video-calibration.json"


def _raw_result_path(manifest: LongExperimentManifest) -> Path:
    return manifest.output.raw_results_root / f"{_file_sha256_from_manifest(manifest)}.json"


def _validate_preflight_receipt(manifest: LongExperimentManifest) -> None:
    try:
        receipt = LongPreflightReceipt.model_validate_json(
            _preflight_receipt_path(manifest).read_text(encoding="utf-8")
        )
    except Exception as error:
        raise RuntimeError("long-video preflight receipt is missing or invalid") from error
    if receipt.status != "PASS":
        raise RuntimeError("long-video preflight did not pass")
    if receipt.manifest_sha256 != _file_sha256_from_manifest(manifest):
        raise RuntimeError("long-video preflight receipt does not match the manifest")
    if receipt.prompt_sha256 != manifest.prompt_sha256:
        raise RuntimeError("long-video preflight prompt binding mismatch")
    if receipt.models != manifest.models.model_dump():
        raise RuntimeError("long-video preflight model matrix mismatch")
    if datetime.now(UTC) - receipt.created_at > _PREFLIGHT_MAX_AGE:
        raise RuntimeError("long-video preflight receipt has expired")
    if any(check.status != "passed" for check in receipt.checks):
        raise RuntimeError("long-video preflight receipt is incomplete")

    def passed(
        component: str,
        stage: str,
        source_id: str | None = None,
        *,
        require_future_expiry: bool = False,
    ) -> bool:
        return any(
            check.component == component
            and check.stage == stage
            and check.source_id == source_id
            and check.status == "passed"
            and (
                not require_future_expiry
                or (check.expires_at is not None and check.expires_at > datetime.now(UTC))
            )
            for check in receipt.checks
        )

    for component in _PREFLIGHT_COMPONENTS:
        for stage in ("configuration", "minimal_call"):
            if not passed(component, stage):
                raise RuntimeError("long-video preflight receipt is incomplete")
    for source in manifest.sources:
        for stage in _PREFLIGHT_PROBE_STAGES:
            if not passed("source_media", stage, source.source_id):
                raise RuntimeError("long-video preflight receipt is incomplete")
            for component in _PREFLIGHT_COMPONENTS:
                if not passed(
                    component,
                    stage,
                    source.source_id,
                    require_future_expiry=component == "qwen_visual",
                ):
                    raise RuntimeError("long-video preflight receipt is incomplete")


def _load_calibrated_limit(manifest: LongExperimentManifest) -> int:
    try:
        receipt = LongCalibrationReceipt.model_validate_json(
            _calibration_receipt_path(manifest).read_text(encoding="utf-8")
        )
    except Exception as error:
        raise RuntimeError("long-video calibration receipt is missing or invalid") from error
    if receipt.status != "PASS":
        raise RuntimeError("long-video concurrency calibration did not pass")
    if receipt.manifest_sha256 != _file_sha256_from_manifest(manifest):
        raise RuntimeError("long-video calibration receipt does not match the manifest")
    if receipt.prompt_sha256 != manifest.prompt_sha256:
        raise RuntimeError("long-video calibration prompt binding mismatch")
    if datetime.now(UTC) - receipt.created_at > _CALIBRATION_MAX_AGE:
        raise RuntimeError("long-video calibration receipt has expired")
    return receipt.provider_concurrency_limit


def _file_sha256_from_manifest(manifest: LongExperimentManifest) -> str:
    # The manifest contains no opaque bytes; this is stable across path resolution.
    serialized = json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _expiry_audit_receipt_path(manifest: LongExperimentManifest) -> Path:
    manifest_sha256 = _file_sha256_from_manifest(manifest)
    path = manifest.output.working_root / f"long-video-expiry-audit-{manifest_sha256[:12]}.json"
    _assert_ignored_output(path)
    return path


def _report_protocol(
    manifest: LongExperimentManifest,
    *,
    gold_sha256: str,
) -> dict[str, object]:
    return {
        "chunk_seconds": manifest.chunk.duration_seconds,
        "overlap_seconds": manifest.chunk.overlap_seconds,
        "manifest_sha256": _file_sha256_from_manifest(manifest),
        "repetitions_per_source_arm": manifest.repetitions,
        "retry_limit": manifest.retry.max_retries,
        "prompt_version": manifest.prompt_version,
        "prompt_sha256": manifest.prompt_sha256,
        "gold_version": "+".join(source.gold_version for source in manifest.sources),
        "gold_sha256": gold_sha256,
        "asr_resource": manifest.models.asr_resource,
        "seed_visual_model": manifest.models.seed_visual_model,
        "seed_fusion_model": manifest.models.seed_fusion_model,
        "qwen_visual_model": manifest.models.qwen_visual_model,
        "scheduler_version": "long-abba-v1",
        "chunk_policy_version": manifest.chunk.version,
        "retry_policy_version": manifest.retry.version,
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    _assert_ignored_output(path)
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _write_text(path: Path, content: str) -> None:
    _assert_ignored_output(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(
            json.dumps({"status": "FAIL", "error_code": _safe_error_code(error)}),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
