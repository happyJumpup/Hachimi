import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from hakimi_analysis.benchmark.aggregate import aggregate_results
from hakimi_analysis.benchmark.gold import write_blank_gold_templates
from hakimi_analysis.benchmark.manifest import (
    EXPECTED_MODELS,
    BenchmarkManifest,
    build_run_plan,
    load_manifest,
    verify_prepared_media,
)
from hakimi_analysis.benchmark.media import (
    ProbeMedia,
    ProbeMediaDirectory,
    create_probe_media,
    prepare_manifest_media,
)
from hakimi_analysis.benchmark.models import (
    BenchmarkRunResult,
    BenchmarkSample,
    MediaLifecycleRecord,
    RouteId,
)
from hakimi_analysis.benchmark.prompts import AUDIO_ONLY_SUFFIX
from hakimi_analysis.benchmark.real_providers import (
    AsrMediaProvider,
    AsrTextAdapter,
    ModularAdapter,
    NativeMediaAdapter,
    QwenMediaProvider,
    SeedMediaProvider,
)
from hakimi_analysis.benchmark.report import sanitized_report
from hakimi_analysis.benchmark.runner import (
    BenchmarkProvider,
    BenchmarkRunner,
    GateFailure,
    PreflightStage,
    RouteAdapter,
)
from hakimi_analysis.benchmark.transport import (
    CurlTransport,
    require_proxy_for_fake_ip,
    resolve_addresses,
)
from hakimi_analysis.providers.asr import VolcAsrClient

PROJECT_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_RECEIPT = PROJECT_ROOT / ".benchmark-work" / "preflight-receipt.json"

ROUTE_COMPONENTS = {
    RouteId.SEED_AV: {"seed_av"},
    RouteId.SEED_MODULAR: {"seed_visual", "asr", "seed_text"},
    RouteId.QWEN_AV: {"qwen_av"},
    RouteId.QWEN_MODULAR: {"qwen_visual", "asr", "seed_text"},
    RouteId.ASR_TEXT: {"asr", "seed_text"},
    RouteId.SEED_AUDIO: {"seed_audio"},
    RouteId.QWEN_AUDIO: {"qwen_audio"},
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed/Qwen native AV benchmark")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=PROJECT_ROOT / ".env.benchmark.local",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--manifest", type=Path, required=True)

    gold = subparsers.add_parser("gold-templates")
    gold.add_argument("--manifest", type=Path, required=True)

    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--manifest", type=Path, required=True)
    preflight.add_argument("--real", action="store_true")
    preflight.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    preflight.add_argument(
        "--provider",
        action="append",
        choices=("seed", "qwen", "asr"),
        dest="providers",
    )

    run = subparsers.add_parser("run")
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--real", action="store_true")
    run.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    run.add_argument("--route", action="append", choices=tuple(RouteId), dest="routes")

    score = subparsers.add_parser("score")
    score.add_argument("--manifest", type=Path, required=True)
    score.add_argument("--results", type=Path, required=True)
    score.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command in {"preflight", "run"} and not args.real:
        raise RuntimeError("real benchmark calls require the explicit --real flag")
    if args.command in {"preflight", "run"}:
        if not args.env_file.is_file():
            raise RuntimeError("benchmark environment file is missing")
        load_dotenv(args.env_file, override=False)

    if args.command == "prepare":
        manifest = load_manifest(args.manifest, require_reviewed_gold=False)
        asyncio.run(prepare_manifest_media(manifest, manifest_path=args.manifest))
        return 0
    if args.command == "gold-templates":
        write_blank_gold_templates(
            load_manifest(args.manifest, require_reviewed_gold=False)
        )
        return 0
    if args.command == "preflight":
        manifest = load_manifest(args.manifest, require_reviewed_gold=False)
        verify_prepared_media(manifest)
        failures, passed_components = asyncio.run(
            _preflight(manifest, selected_providers=args.providers)
        )
        _write_preflight_receipt(
            args.receipt,
            manifest_path=args.manifest,
            passed_components=passed_components,
            failures=failures,
        )
        status = "FAIL" if failures else "PASS"
        print(
            json.dumps(
                {
                    "status": status,
                    "scope": "local_preflight",
                    "failures": failures,
                }
            ),
            file=sys.stderr if failures else sys.stdout,
        )
        return 1 if failures else 0
    if args.command == "run":
        manifest = load_manifest(args.manifest, require_reviewed_gold=False)
        verify_prepared_media(manifest)
        _validate_preflight_receipt(
            args.receipt,
            manifest_path=args.manifest,
            selected_routes=args.routes,
        )
        results, lifecycle = asyncio.run(_run(manifest, selected_routes=args.routes))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "version": 1,
                    "results": [result.model_dump(mode="json") for result in results],
                    "media_lifecycle": [
                        record.model_dump(mode="json") for record in lifecycle
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(json.dumps({"status": "PASS", "scored_units": len(results)}))
        return 0
    if args.command == "score":
        manifest = load_manifest(args.manifest, require_reviewed_gold=True)
        result_payload = json.loads(args.results.read_text(encoding="utf-8"))
        raw_results = (
            result_payload["results"]
            if isinstance(result_payload, dict)
            else result_payload
        )
        results = [
            BenchmarkRunResult.model_validate(item)
            for item in raw_results
        ]
        report = sanitized_report(aggregate_results(manifest, results), diagnostics={})
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return 0
    raise AssertionError("unreachable command")


async def _preflight(
    manifest: BenchmarkManifest,
    *,
    selected_providers: list[str] | None,
) -> tuple[list[dict[str, object]], list[str]]:
    providers, adapters = _runtime()
    runner = BenchmarkRunner(
        providers,
        adapters,
        timeout_seconds=180,
        progress=_print_progress,
    )
    first = manifest.samples[0]
    probe_directory = ProbeMediaDirectory()
    try:
        synthetic = await create_probe_media(
            first.av_path,
            duration_seconds=2,
            synthetic=True,
            root=probe_directory.path,
        )
        real = await create_probe_media(
            first.av_path,
            duration_seconds=8,
            synthetic=False,
            root=probe_directory.path,
        )
        staged = _staged_assets(first, synthetic, real)
        failures: list[dict[str, object]] = []
        passed_components: list[str] = []
        for component, (provider_name, minimal_model_id, assets) in staged.items():
            if selected_providers and provider_name not in selected_providers:
                continue
            try:
                await runner.preflight_provider(
                    provider_name,
                    minimal_model_id,
                    assets,
                )
            except GateFailure as error:
                failure = _safe_failure(error)
                failure["component"] = component
                failures.append(failure)
                print(json.dumps(failure), file=sys.stderr, flush=True)
            else:
                passed_components.append(component)
        return failures, passed_components
    finally:
        probe_directory.cleanup()


def _staged_assets(
    first: BenchmarkSample,
    synthetic: ProbeMedia,
    real: ProbeMedia,
) -> dict[
    str,
    tuple[str, str, list[tuple[PreflightStage, str, Path, str]]],
]:
    return {
        "seed_av": (
            "seed",
            EXPECTED_MODELS["seed_lite"],
            _media_stages(
                EXPECTED_MODELS["seed_lite"],
                synthetic.av_path,
                real.av_path,
                first.av_path,
                "video",
            ),
        ),
        "seed_visual": (
            "seed",
            EXPECTED_MODELS["seed_lite"],
            _media_stages(
                EXPECTED_MODELS["seed_lite"],
                synthetic.silent_video_path,
                real.silent_video_path,
                first.silent_video_path,
                "video",
            ),
        ),
        "seed_audio": (
            "seed",
            EXPECTED_MODELS["seed_lite"],
            _media_stages(
                EXPECTED_MODELS["seed_lite"],
                synthetic.audio_path,
                real.audio_path,
                first.audio_path,
                "audio",
            ),
        ),
        "seed_text": ("seed", EXPECTED_MODELS["seed_mini"], []),
        "qwen_av": (
            "qwen",
            EXPECTED_MODELS["qwen_omni"],
            _media_stages(
                EXPECTED_MODELS["qwen_omni"],
                synthetic.av_path,
                real.av_path,
                first.av_path,
                "video",
            ),
        ),
        "qwen_visual": (
            "qwen",
            EXPECTED_MODELS["qwen_vl"],
            _media_stages(
                EXPECTED_MODELS["qwen_vl"],
                synthetic.silent_video_path,
                real.silent_video_path,
                first.silent_video_path,
                "video",
            ),
        ),
        "qwen_audio": (
            "qwen",
            EXPECTED_MODELS["qwen_omni"],
            _media_stages(
                EXPECTED_MODELS["qwen_omni"],
                synthetic.audio_path,
                real.audio_path,
                first.audio_path,
                "audio",
            ),
        ),
        "asr": (
            "asr",
            EXPECTED_MODELS["asr_resource"],
            _media_stages(
                EXPECTED_MODELS["asr_resource"],
                synthetic.audio_path,
                real.audio_path,
                first.audio_path,
                "audio",
            ),
        ),
    }


def _media_stages(
    model_id: str,
    synthetic: Path,
    real: Path,
    full: Path,
    media_kind: str,
) -> list[tuple[PreflightStage, str, Path, str]]:
    return [
        (PreflightStage.SYNTHETIC_TWO_SECONDS, model_id, synthetic, media_kind),
        (PreflightStage.REAL_EIGHT_SECONDS, model_id, real, media_kind),
        (PreflightStage.FULL_SAMPLE, model_id, full, media_kind),
    ]


async def _run(
    manifest: BenchmarkManifest,
    *,
    selected_routes: list[str] | None,
) -> tuple[list[BenchmarkRunResult], list[MediaLifecycleRecord]]:
    providers, adapters = _runtime()
    runner = BenchmarkRunner(providers, adapters, timeout_seconds=180)
    selected = {RouteId(route) for route in selected_routes} if selected_routes else None
    plan = [
        unit
        for unit in build_run_plan(manifest)
        if selected is None or unit.route_id in selected
    ]
    results = await runner.run(manifest, plan)
    return results, runner.lifecycle_records


def _write_preflight_receipt(
    path: Path,
    *,
    manifest_path: Path,
    passed_components: list[str],
    failures: list[dict[str, object]],
) -> None:
    payload = {
        "version": 1,
        "status": "PASS" if not failures else "FAIL",
        "created_at": datetime.now(UTC).isoformat(),
        "manifest_sha256": _file_sha256(manifest_path),
        "models": EXPECTED_MODELS,
        "passed_components": sorted(passed_components),
        "failed_components": sorted(
            str(failure["component"])
            for failure in failures
            if failure.get("component") is not None
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _validate_preflight_receipt(
    path: Path,
    *,
    manifest_path: Path,
    selected_routes: list[str] | None,
) -> None:
    if not path.is_file():
        raise RuntimeError("preflight receipt is missing")
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
        created_at = datetime.fromisoformat(str(receipt["created_at"]))
        passed = {str(item) for item in receipt["passed_components"]}
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("preflight receipt is invalid") from error
    if receipt.get("manifest_sha256") != _file_sha256(manifest_path):
        raise RuntimeError("preflight receipt does not match manifest")
    if receipt.get("models") != EXPECTED_MODELS:
        raise RuntimeError("preflight receipt model matrix mismatch")
    if datetime.now(UTC) - created_at > timedelta(hours=6):
        raise RuntimeError("preflight receipt has expired")
    routes = (
        {RouteId(route) for route in selected_routes}
        if selected_routes
        else set(RouteId)
    )
    required = set().union(*(ROUTE_COMPONENTS[route] for route in routes))
    if not required.issubset(passed):
        raise RuntimeError("preflight receipt does not cover selected routes")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime() -> tuple[dict[str, BenchmarkProvider], dict[RouteId, RouteAdapter]]:
    proxy_url = os.getenv(
        "NATIVE_AV_PROXY_URL", "socks5h://127.0.0.1:7897"
    ).strip()
    ark_base_url = os.getenv(
        "ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"
    ).strip()
    qwen_base_url = os.getenv(
        "QWEN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ).strip()
    for url in (ark_base_url, qwen_base_url, "https://dashscope.aliyuncs.com"):
        host = urlparse(url).hostname
        if host is None:
            raise RuntimeError("provider base URL has no host")
        require_proxy_for_fake_ip(resolve_addresses(host), proxy_url)

    transport = CurlTransport(proxy_url=proxy_url, timeout_seconds=180)
    seed = SeedMediaProvider(
        api_key=_required_env("ARK_API_KEY"),
        base_url=ark_base_url,
        transport=transport,
    )
    qwen = QwenMediaProvider(
        api_key=_required_env("DASHSCOPE_API_KEY"),
        compatible_base_url=qwen_base_url,
        transport=transport,
    )
    asr = AsrMediaProvider(
        VolcAsrClient(
            api_key=_required_env("VOLC_ASR_API_KEY"),
            resource_id=EXPECTED_MODELS["asr_resource"],
            url=os.getenv(
                "VOLC_ASR_URL",
                "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream",
            ),
            pace_audio=False,
        )
    )
    providers: dict[str, BenchmarkProvider] = {
        "seed": seed,
        "qwen": qwen,
        "asr": asr,
    }
    adapters: dict[RouteId, RouteAdapter] = {
        RouteId.SEED_AV: NativeMediaAdapter(seed, handle_role="av"),
        RouteId.SEED_MODULAR: ModularAdapter(seed, asr, seed),
        RouteId.QWEN_AV: NativeMediaAdapter(qwen, handle_role="av"),
        RouteId.QWEN_MODULAR: ModularAdapter(qwen, asr, seed),
        RouteId.ASR_TEXT: AsrTextAdapter(asr, seed),
        RouteId.SEED_AUDIO: NativeMediaAdapter(
            seed, handle_role="audio", prompt_suffix=AUDIO_ONLY_SUFFIX
        ),
        RouteId.QWEN_AUDIO: NativeMediaAdapter(
            qwen, handle_role="audio", prompt_suffix=AUDIO_ONLY_SUFFIX
        ),
    }
    return providers, adapters


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"missing benchmark credential: {name}")
    return value


def _print_progress(
    provider: str,
    stage: PreflightStage,
    event: str,
    elapsed: float | None,
) -> None:
    payload: dict[str, object] = {
        "provider": provider,
        "stage": stage,
        "event": event,
    }
    if elapsed is not None:
        payload["elapsed_seconds"] = round(elapsed, 2)
    print(json.dumps(payload), flush=True)


def _safe_failure(error: GateFailure) -> dict[str, object]:
    return {
        "provider": error.provider,
        "stage": error.stage,
        "error_code": error.code,
        "retryable": error.retryable,
        "provider_status": error.status_code,
        "cleanup_failed": error.cleanup_failed,
        "diagnostic": error.diagnostic,
    }


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GateFailure as error:
        print(json.dumps({"status": "FAIL", **_safe_failure(error)}), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "error_code": type(error).__name__.lower(),
                }
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
