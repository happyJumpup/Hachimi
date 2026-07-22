from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Literal, TypedDict

import httpx
from pydantic import Field, model_validator

from hakimi_analysis.content_understanding import EvidenceReconciler
from hakimi_analysis.media import LocalMediaProcessor, probe_duration_sync
from hakimi_analysis.models import Segment, StrictModel, VisualLocalizationResult, VisualSegment
from hakimi_analysis.orchestration import build_visual_chunks, deduplicate_visual_segments
from hakimi_analysis.provider_contracts import PromptContractRegistry
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.providers.qwen import QwenVisualClient
from hakimi_analysis.settings import PROJECT_ROOT, Settings
from hakimi_analysis.temporary_cos import TencentCosTemporaryStore
from hakimi_analysis.visual_routing import DirectVisualProvider, PreparedVisualProvider

ROUTES = {
    "seed-mini": "doubao-seed-2-0-mini-260428",
    "seed-lite": "doubao-seed-2-0-lite-260428",
    "qwen-vl": "qwen3-vl-flash-2026-01-22",
}
REQUIRED_SAMPLE_IDS = {
    "video-54s",
    "video-68s",
    "video-100s",
    "video-108s",
    "video-208s",
    "video-300s",
    "silent-action",
    "no-action",
}
DEFAULT_ROOT = PROJECT_ROOT / "tmp" / "provider-conformance"


class GoldEvent(StrictModel):
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_range(self) -> GoldEvent:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("gold event end must follow its start")
        return self


class ConformanceSample(StrictModel):
    id: str
    path: Path
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    duration_seconds: float = Field(gt=0, le=300)
    gold: list[GoldEvent] = Field(default_factory=list)


class ConformanceManifest(StrictModel):
    schema_version: Literal[1]
    manifest_version: str
    prompt_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    repetitions: Literal[3]
    visual_chunk_seconds: Literal[60]
    visual_overlap_seconds: Literal[10]
    routes: dict[str, str]
    samples: list[ConformanceSample]

    @model_validator(mode="after")
    def validate_frozen_matrix(self) -> ConformanceManifest:
        if self.routes != ROUTES:
            raise ValueError("provider conformance routes do not match the frozen model IDs")
        sample_ids = {sample.id for sample in self.samples}
        if sample_ids != REQUIRED_SAMPLE_IDS or len(self.samples) != len(sample_ids):
            raise ValueError("provider conformance samples do not match the frozen matrix")
        return self


class RawRun(StrictModel):
    route: str
    sample_id: str
    repetition: int = Field(ge=1, le=3)
    completed: bool
    elapsed_seconds: float = Field(ge=0)
    segments: list[VisualSegment] = Field(default_factory=list)
    error_code: str | None = None


class RunScore(StrictModel):
    precision: float
    recall: float
    f1_tiou_03: float
    f1_tiou_05: float
    time_hit_rate: float
    false_positive_count: int


class RouteSummary(TypedDict):
    route: str
    model_id: str
    units: int
    complete: bool
    success_rate: float
    precision: float
    recall: float
    f1_tiou_05: float
    time_hit_rate: float
    median_seconds_per_video_minute: float | None
    schema_or_clock_violations: int
    cost_status: Literal["not-measured"]
    passes_quality_gate: bool


@dataclass(frozen=True, slots=True)
class _MatchCounts:
    matched: int
    predicted: int
    gold: int


def load_manifest(path: Path) -> ConformanceManifest:
    return ConformanceManifest.model_validate_json(path.read_text(encoding="utf-8"))


def validate_prepared_manifest(manifest: ConformanceManifest) -> dict[str, object]:
    contracts = PromptContractRegistry.load(
        PROJECT_ROOT / "services" / "analysis-api" / "provider-contracts",
        speech_model_id=ROUTES["seed-mini"],
        visual_model_ids=tuple(ROUTES.values()),
    )
    if contracts.visual.prompt_sha256 != manifest.prompt_contract_sha256:
        raise ValueError("manifest prompt contract hash is stale")
    for sample in manifest.samples:
        if _sha256(sample.path) != sample.sha256:
            raise ValueError(f"sample hash mismatch: {sample.id}")
        observed_duration = probe_duration_sync(sample.path)
        if abs(observed_duration - sample.duration_seconds) > 0.25:
            raise ValueError(f"sample duration mismatch: {sample.id}")
        windows = build_visual_chunks(
            sample.duration_seconds,
            chunk_seconds=manifest.visual_chunk_seconds,
            overlap_seconds=manifest.visual_overlap_seconds,
        )
        if windows[0].start_seconds != 0 or windows[-1].end_seconds != sample.duration_seconds:
            raise ValueError(f"sample visual coverage is incomplete: {sample.id}")
    return {
        "status": "prepared",
        "manifest_version": manifest.manifest_version,
        "sample_count": len(manifest.samples),
        "route_count": len(manifest.routes),
    }


def score_run(run: RawRun, sample: ConformanceSample) -> RunScore:
    counts_03 = _match(run.segments, sample.gold, threshold=0.3)
    counts_05 = _match(run.segments, sample.gold, threshold=0.5)
    precision = _precision(counts_05)
    recall = _recall(counts_05)
    return RunScore(
        precision=precision,
        recall=recall,
        f1_tiou_03=_f1(_precision(counts_03), _recall(counts_03)),
        f1_tiou_05=_f1(precision, recall),
        time_hit_rate=(
            (1.0 if not run.segments else 0.0)
            if not sample.gold
            else counts_03.matched / len(sample.gold)
        ),
        false_positive_count=max(0, len(run.segments) - counts_05.matched),
    )


def build_sanitized_report(
    manifest: ConformanceManifest,
    runs: Sequence[RawRun],
) -> dict[str, object]:
    sample_by_id = {sample.id: sample for sample in manifest.samples}
    summaries: list[RouteSummary] = []
    for route, model_id in ROUTES.items():
        route_runs = [run for run in runs if run.route == route]
        expected_units = len(manifest.samples) * manifest.repetitions
        scores = [score_run(run, sample_by_id[run.sample_id]) for run in route_runs]
        completed = [run for run in route_runs if run.completed]
        summary: RouteSummary = {
            "route": route,
            "model_id": model_id,
            "units": len(route_runs),
            "complete": len(completed) == expected_units,
            "success_rate": len(completed) / expected_units,
            "precision": _mean(score.precision for score in scores),
            "recall": _mean(score.recall for score in scores),
            "f1_tiou_05": _mean(score.f1_tiou_05 for score in scores),
            "time_hit_rate": _mean(score.time_hit_rate for score in scores),
            "median_seconds_per_video_minute": _median(
                run.elapsed_seconds
                / (sample_by_id[run.sample_id].duration_seconds / 60)
                for run in completed
            ),
            "schema_or_clock_violations": sum(
                run.error_code == "schema_error" for run in route_runs
            ),
            "cost_status": "not-measured",
            "passes_quality_gate": False,
        }
        summary["passes_quality_gate"] = bool(
            summary["complete"]
            and summary["precision"] >= 0.90
            and summary["recall"] >= 0.80
            and summary["f1_tiou_05"] >= 0.75
            and summary["schema_or_clock_violations"] == 0
        )
        summaries.append(summary)
    research_selection = _select_route(summaries)
    seed_selection = _select_route(
        [summary for summary in summaries if summary["route"].startswith("seed-")]
    )
    qwen_qualified = next(
        summary["passes_quality_gate"]
        for summary in summaries
        if summary["route"] == "qwen-vl"
    )
    return {
        "schema_version": 1,
        "manifest_version": manifest.manifest_version,
        "prompt_contract_sha256": manifest.prompt_contract_sha256,
        "routes": summaries,
        "selection": research_selection,
        "seed_primary_selection": seed_selection,
        "qwen_fallback_qualified": qwen_qualified,
        "version_c_decision": _version_c_decision(
            research_selection=research_selection,
            seed_selection=seed_selection,
            qwen_qualified=qwen_qualified,
        ),
    }


async def run_real_matrix(
    manifest: ConformanceManifest,
    *,
    output_root: Path,
    preflight_only: bool,
) -> list[RawRun]:
    settings = Settings()
    contracts = PromptContractRegistry.load(
        PROJECT_ROOT / "services" / "analysis-api" / "provider-contracts",
        speech_model_id=ROUTES["seed-mini"],
        visual_model_ids=tuple(ROUTES.values()),
    )
    output_root.mkdir(parents=True, exist_ok=True)
    raw_root = output_root / "raw"
    raw_root.mkdir(parents=True, exist_ok=True)
    if preflight_only:
        representative = next((sample for sample in manifest.samples if sample.gold), None)
        if representative is None:
            raise ValueError("provider preflight requires a positive representative sample")
        samples = [representative]
    else:
        samples = manifest.samples
    repetitions = 1 if preflight_only else manifest.repetitions
    results: list[RawRun] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=15)) as http_client:
        for route, model_id in ROUTES.items():
            try:
                provider = _real_provider(route, model_id, settings, http_client)
            except Exception as error:
                repetitions_for_failure = 1 if preflight_only else repetitions
                for sample in samples:
                    for repetition in range(1, repetitions_for_failure + 1):
                        run = RawRun(
                            route=route,
                            sample_id=sample.id,
                            repetition=repetition,
                            completed=False,
                            elapsed_seconds=0,
                            error_code=_error_code(error),
                        )
                        if not preflight_only:
                            _write_raw_run(raw_root, run)
                        results.append(run)
                continue
            for sample in samples:
                durations = (2.0, 8.0, min(60.0, sample.duration_seconds)) if preflight_only else ()
                if preflight_only:
                    started_at = monotonic()
                    try:
                        for duration in durations:
                            await _run_visual_sample(
                                provider,
                                sample,
                                contracts.visual.prompt,
                                duration_override=duration,
                            )
                    except Exception as error:
                        results.append(
                            RawRun(
                                route=route,
                                sample_id=sample.id,
                                repetition=1,
                                completed=False,
                                elapsed_seconds=monotonic() - started_at,
                                error_code=_error_code(error),
                            )
                        )
                    else:
                        results.append(
                            RawRun(
                                route=route,
                                sample_id=sample.id,
                                repetition=1,
                                completed=True,
                                elapsed_seconds=monotonic() - started_at,
                            )
                        )
                    continue
                for repetition in range(1, repetitions + 1):
                    started_at = monotonic()
                    try:
                        segments = await _run_visual_sample(
                            provider,
                            sample,
                            contracts.visual.prompt,
                        )
                        run = RawRun(
                            route=route,
                            sample_id=sample.id,
                            repetition=repetition,
                            completed=True,
                            elapsed_seconds=monotonic() - started_at,
                            segments=segments,
                        )
                    except Exception as error:
                        run = RawRun(
                            route=route,
                            sample_id=sample.id,
                            repetition=repetition,
                            completed=False,
                            elapsed_seconds=monotonic() - started_at,
                            error_code=_error_code(error),
                        )
                    _write_raw_run(raw_root, run)
                    results.append(run)
    return results


def _error_code(error: Exception) -> str:
    return str(getattr(error, "code", "configuration_error"))


def _write_raw_run(raw_root: Path, run: RawRun) -> None:
    (raw_root / f"{run.route}-{run.sample_id}-{run.repetition}.json").write_text(
        run.model_dump_json(indent=2),
        encoding="utf-8",
    )


def _real_provider(
    route: str,
    model_id: str,
    settings: Settings,
    http_client: httpx.AsyncClient,
) -> PreparedVisualProvider:
    if route.startswith("seed-"):
        if settings.ark_api_key is None:
            raise RuntimeError("ARK_API_KEY is required")
        ark = ArkResponsesClient(
            api_key=settings.ark_api_key.get_secret_value(),
            model_id=model_id,
            visual_model_id=model_id,
            base_url=settings.ark_base_url,
            http_client=http_client,
            retry_delays=(),
        )
        return DirectVisualProvider(ark)
    if (
        settings.qwen_api_key is None
        or settings.cos_secret_id is None
        or settings.cos_secret_key is None
    ):
        raise RuntimeError("Qwen and private COS credentials are required")
    store = TencentCosTemporaryStore(
        secret_id=settings.cos_secret_id.get_secret_value(),
        secret_key=settings.cos_secret_key.get_secret_value(),
        region=settings.cos_region,
        bucket=settings.cos_bucket,
        object_prefix=settings.cos_object_prefix,
        signed_url_ttl_seconds=settings.cos_signed_url_ttl_seconds,
        lifecycle_days=settings.cos_lifecycle_days,
    )
    if not store.check_security_configuration():
        raise RuntimeError("private COS security configuration is invalid")
    return QwenVisualClient(
        api_key=settings.qwen_api_key.get_secret_value(),
        model_id=model_id,
        base_url=settings.qwen_base_url,
        http_client=http_client,
        object_store=store,
    )


async def _run_visual_sample(
    provider: PreparedVisualProvider,
    sample: ConformanceSample,
    prompt: str,
    *,
    duration_override: float | None = None,
) -> list[VisualSegment]:
    duration = duration_override or sample.duration_seconds
    windows = build_visual_chunks(duration, chunk_seconds=60, overlap_seconds=10)
    segments: list[VisualSegment] = []
    with tempfile.TemporaryDirectory(prefix="trainpal-conformance-") as directory_name:
        directory = Path(directory_name)
        media = LocalMediaProcessor(max_source_duration_seconds=300)
        for window in windows:
            chunk = await media.prepare_visual_chunk(sample.path, window, directory)
            async with provider.prepare(chunk.video_path) as prepared:
                async with asyncio.timeout(20):
                    result = await provider.locate(
                        prepared,
                        window=Segment(start_seconds=0, end_seconds=window.duration_seconds),
                        instructions=prompt,
                    )
            segments.extend(_offset(result, window.start_seconds))
    deduplicated = deduplicate_visual_segments(segments)
    reconciled = EvidenceReconciler().reconcile_candidates(
        sample.id,
        [],
        deduplicated,
    ).candidates
    return [
        VisualSegment(
            action_name=candidate.name,
            start_seconds=candidate.segment.start_seconds,
            end_seconds=candidate.segment.end_seconds,
            visual_cue="production-reconciler-projection",
        )
        for candidate in reconciled
    ]


def _offset(result: VisualLocalizationResult, offset: float) -> list[VisualSegment]:
    return [
        segment.model_copy(
            update={
                "start_seconds": segment.start_seconds + offset,
                "end_seconds": segment.end_seconds + offset,
            }
        )
        for segment in result.segments
    ]


def _match(
    predicted: list[VisualSegment],
    gold: list[GoldEvent],
    *,
    threshold: float,
) -> _MatchCounts:
    available = set(range(len(predicted)))
    matched = 0
    for event in gold:
        candidates = [
            (index, _event_tiou(predicted[index], event))
            for index in available
            if _matches_event_name(predicted[index].action_name, event)
        ]
        if not candidates:
            continue
        index, overlap = max(candidates, key=lambda item: item[1])
        if overlap >= threshold:
            matched += 1
            available.remove(index)
    return _MatchCounts(matched=matched, predicted=len(predicted), gold=len(gold))


def _matches_event_name(name: str | None, event: GoldEvent) -> bool:
    normalized = _normalize(name)
    return normalized in {_normalize(event.name), *(_normalize(alias) for alias in event.aliases)}


def _normalize(value: str | None) -> str:
    return "".join(character for character in (value or "").casefold() if character.isalnum())


def _event_tiou(segment: VisualSegment, event: GoldEvent) -> float:
    intersection = max(
        0.0,
        min(segment.end_seconds, event.end_seconds)
        - max(segment.start_seconds, event.start_seconds),
    )
    union = max(segment.end_seconds, event.end_seconds) - min(
        segment.start_seconds,
        event.start_seconds,
    )
    return intersection / union if union else 0.0


def _precision(counts: _MatchCounts) -> float:
    if counts.predicted == 0:
        return 1.0 if counts.gold == 0 else 0.0
    return counts.matched / counts.predicted


def _recall(counts: _MatchCounts) -> float:
    if counts.gold == 0:
        return 1.0 if counts.predicted == 0 else 0.0
    return counts.matched / counts.gold


def _f1(precision: float, recall: float) -> float:
    return 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def _median(values: Iterable[float]) -> float | None:
    items = sorted(values)
    if not items:
        return None
    middle = len(items) // 2
    return float(
        items[middle]
        if len(items) % 2
        else (items[middle - 1] + items[middle]) / 2
    )


def _select_route(summaries: list[RouteSummary]) -> str:
    qualified = [summary for summary in summaries if summary["passes_quality_gate"] is True]
    if not qualified:
        return "no-qualified-route"
    best_quality = max(summary["f1_tiou_05"] for summary in qualified)
    worst_quality = min(summary["f1_tiou_05"] for summary in qualified)
    if best_quality - worst_quality > 0.03:
        return str(max(qualified, key=lambda summary: summary["f1_tiou_05"])["route"])

    best_success = max(summary["success_rate"] for summary in qualified)
    success_leaders = [
        summary for summary in qualified if summary["success_rate"] == best_success
    ]
    if len(success_leaders) == 1:
        return str(success_leaders[0]["route"])

    observed_latency = [
        summary
        for summary in success_leaders
        if summary["median_seconds_per_video_minute"] is not None
    ]
    if observed_latency:
        best_latency = min(
            latency
            for summary in observed_latency
            if (latency := summary["median_seconds_per_video_minute"]) is not None
        )
        latency_leaders = [
            summary
            for summary in observed_latency
            if summary["median_seconds_per_video_minute"] == best_latency
        ]
        if len(latency_leaders) == 1:
            return str(latency_leaders[0]["route"])

    return "qualified-routes-require-cost-review"


def _version_c_decision(
    *,
    research_selection: str,
    seed_selection: str,
    qwen_qualified: bool,
) -> str:
    if research_selection == "qualified-routes-require-cost-review":
        return "blocked-requires-cost-review"
    if research_selection == "qwen-vl":
        return "blocked-qwen-primary-conflicts-with-failed-chunk-only-cos"
    if not research_selection.startswith("seed-") or not seed_selection.startswith("seed-"):
        return "blocked-no-qualified-seed-primary"
    if not qwen_qualified:
        return "blocked-no-qualified-cross-vendor-fallback"
    return "eligible-for-version-c-canary"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_raw_runs(raw_root: Path) -> list[RawRun]:
    return [
        RawRun.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(raw_root.glob("*.json"))
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Production-isomorphic visual provider gate")
    parser.add_argument("command", choices=("prepare", "preflight", "run", "score"))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_ROOT / "manifest.json")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--real", action="store_true")
    args = parser.parse_args(argv)
    manifest = load_manifest(args.manifest)
    if args.command == "prepare":
        print(json.dumps(validate_prepared_manifest(manifest), separators=(",", ":")))
        return 0
    if args.command in {"preflight", "run"}:
        if not args.real:
            raise SystemExit("real provider calls require --real")
        runs = asyncio.run(
            run_real_matrix(
                manifest,
                output_root=args.output_root,
                preflight_only=args.command == "preflight",
            )
        )
        preflight_passed = all(run.completed for run in runs)
        print(
            json.dumps(
                {
                    "status": (
                        "passed"
                        if args.command == "preflight" and preflight_passed
                        else "blocked"
                        if args.command == "preflight"
                        else "completed"
                    ),
                    "command": args.command,
                    "unit_count": len(runs),
                },
                separators=(",", ":"),
            )
        )
        return 0 if args.command != "preflight" or preflight_passed else 2
    report = build_sanitized_report(manifest, _load_raw_runs(args.output_root / "raw"))
    report_path = args.output_root / "report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
