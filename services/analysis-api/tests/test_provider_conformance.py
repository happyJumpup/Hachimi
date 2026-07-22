from pathlib import Path

import pytest
from pydantic import ValidationError

from hakimi_analysis.models import VisualSegment
from hakimi_analysis.provider_conformance import (
    REQUIRED_SAMPLE_IDS,
    ROUTES,
    ConformanceManifest,
    ConformanceSample,
    GoldEvent,
    RawRun,
    build_sanitized_report,
    score_run,
)


def make_manifest(tmp_path: Path) -> ConformanceManifest:
    samples = []
    for index, sample_id in enumerate(sorted(REQUIRED_SAMPLE_IDS)):
        negative = sample_id == "no-action"
        samples.append(
            ConformanceSample(
                id=sample_id,
                path=tmp_path / f"sample-{index}.mp4",
                sha256="a" * 64,
                duration_seconds=60,
                gold=(
                    []
                    if negative
                    else [
                        GoldEvent(
                            name="罗马尼亚硬拉",
                            aliases=["RDL"],
                            start_seconds=10,
                            end_seconds=20,
                        )
                    ]
                ),
            )
        )
    return ConformanceManifest(
        schema_version=1,
        manifest_version="provider-conformance-v1",
        prompt_contract_sha256="b" * 64,
        repetitions=3,
        visual_chunk_seconds=60,
        visual_overlap_seconds=10,
        routes=ROUTES,
        samples=samples,
    )


def test_manifest_locks_exact_models_samples_and_three_repetitions(tmp_path: Path) -> None:
    manifest = make_manifest(tmp_path)
    payload = manifest.model_dump()
    payload["routes"] = {**ROUTES, "qwen-vl": "qwen3-vl-flash"}

    with pytest.raises(ValidationError, match="frozen model IDs"):
        ConformanceManifest.model_validate(payload)

    payload = manifest.model_dump()
    payload["samples"] = payload["samples"][:-1]
    with pytest.raises(ValidationError, match="frozen matrix"):
        ConformanceManifest.model_validate(payload)


def test_score_requires_semantic_name_and_temporal_overlap(tmp_path: Path) -> None:
    sample = next(sample for sample in make_manifest(tmp_path).samples if sample.gold)
    correct = RawRun(
        route="seed-mini",
        sample_id=sample.id,
        repetition=1,
        completed=True,
        elapsed_seconds=5,
        segments=[
            VisualSegment(
                action_name="RDL",
                start_seconds=11,
                end_seconds=19,
                visual_cue="髋部后移",
            )
        ],
    )
    wrong_name = correct.model_copy(
        update={
            "segments": [
                VisualSegment(
                    action_name="深蹲",
                    start_seconds=11,
                    end_seconds=19,
                    visual_cue="下蹲",
                )
            ]
        }
    )

    assert score_run(correct, sample).f1_tiou_05 == 1
    assert score_run(wrong_name, sample).f1_tiou_05 == 0


def test_sanitized_report_contains_metrics_but_no_local_paths(tmp_path: Path) -> None:
    manifest = make_manifest(tmp_path)
    runs: list[RawRun] = []
    for route in ROUTES:
        for sample in manifest.samples:
            for repetition in range(1, 4):
                runs.append(
                    RawRun(
                        route=route,
                        sample_id=sample.id,
                        repetition=repetition,
                        completed=True,
                        elapsed_seconds=5,
                        segments=(
                            []
                            if not sample.gold
                            else [
                                VisualSegment(
                                    action_name="罗马尼亚硬拉",
                                    start_seconds=10,
                                    end_seconds=20,
                                    visual_cue="髋部后移",
                                )
                            ]
                        ),
                    )
                )

    report = build_sanitized_report(manifest, runs)

    assert report["selection"] == "qualified-routes-require-cost-review"
    assert report["seed_primary_selection"] == "qualified-routes-require-cost-review"
    assert report["qwen_fallback_qualified"] is True
    assert report["version_c_decision"] == "blocked-requires-cost-review"
    routes = report["routes"]
    assert isinstance(routes, list)
    assert all(route["passes_quality_gate"] for route in routes)
    assert all(route["cost_status"] == "not-measured" for route in routes)
    assert str(tmp_path) not in str(report)
