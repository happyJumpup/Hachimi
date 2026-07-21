import json
from itertools import pairwise
from pathlib import Path

import pytest
from pydantic import ValidationError

from hakimi_analysis.benchmark.manifest import (
    EXPECTED_MODELS,
    ROUTE_DEFINITIONS,
    BenchmarkManifest,
    build_run_plan,
    load_manifest,
)
from hakimi_analysis.benchmark.models import GoldAnnotation, RouteId


def valid_manifest_payload(tmp_path: Path) -> dict[str, object]:
    samples = []
    for index, duration in enumerate((54.87, 60.0, 60.0), start=1):
        source = tmp_path / f"source-{index}.mp4"
        av = tmp_path / f"sample-{index}-av.mp4"
        silent = tmp_path / f"sample-{index}-silent.mp4"
        audio = tmp_path / f"sample-{index}-audio.wav"
        for path in (source, av, silent, audio):
            path.touch()
        samples.append(
            {
                "sample_id": f"sample-{index}",
                "source_path": str(source),
                "window_start_seconds": 0,
                "duration_seconds": duration,
                "av_path": str(av),
                "silent_video_path": str(silent),
                "audio_path": str(audio),
                "av_sha256": "a" * 64,
                "silent_video_sha256": "b" * 64,
                "audio_sha256": "c" * 64,
                "gold_path": str(tmp_path / f"sample-{index}-gold.json"),
                "gold_status": "reviewed",
            }
        )
    return {
        "version": 1,
        "seed": 20260721,
        "models": dict(EXPECTED_MODELS),
        "samples": samples,
    }


def test_manifest_locks_seven_routes_and_exact_model_ids(tmp_path: Path) -> None:
    manifest = BenchmarkManifest.model_validate(valid_manifest_payload(tmp_path))

    assert set(ROUTE_DEFINITIONS) == set(RouteId)
    assert manifest.models == EXPECTED_MODELS
    assert len(build_run_plan(manifest)) == 63


def test_manifest_rejects_rolling_alias_or_unreviewed_gold(tmp_path: Path) -> None:
    payload = valid_manifest_payload(tmp_path)
    models = payload["models"]
    assert isinstance(models, dict)
    models["qwen_omni"] = "qwen3.5-omni-flash"

    with pytest.raises(ValidationError):
        BenchmarkManifest.model_validate(payload)

    payload = valid_manifest_payload(tmp_path)
    samples = payload["samples"]
    assert isinstance(samples, list)
    samples[0]["gold_status"] = "draft"
    manifest_path = tmp_path / "benchmark.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="human-reviewed"):
        load_manifest(manifest_path, require_reviewed_gold=True)


def test_run_plan_is_deterministic_interleaved_and_single_concurrency(tmp_path: Path) -> None:
    manifest = BenchmarkManifest.model_validate(valid_manifest_payload(tmp_path))

    first = build_run_plan(manifest)
    second = build_run_plan(manifest)

    assert first == second
    assert {unit.run_index for unit in first} == {1, 2, 3}
    assert len({(unit.sample_id, unit.route_id, unit.run_index) for unit in first}) == 63

    rows = [first[index : index + 7] for index in range(0, 63, 7)]
    assert all(
        left.route_id != right.route_id
        for row in rows
        for left, right in pairwise(row)
    )
    assert all(
        abs(
            next(i for i, unit in enumerate(row) if unit.route_id == RouteId.SEED_AV)
            - next(i for i, unit in enumerate(row) if unit.route_id == RouteId.QWEN_AV)
        )
        == 1
        for row in rows
    )
    assert all(
        abs(
            next(
                i for i, unit in enumerate(row) if unit.route_id == RouteId.SEED_MODULAR
            )
            - next(
                i for i, unit in enumerate(row) if unit.route_id == RouteId.QWEN_MODULAR
            )
        )
        == 1
        for row in rows
    )


def test_reviewed_gold_requires_a_named_human_reviewer() -> None:
    with pytest.raises(ValidationError, match="at least one reviewer"):
        GoldAnnotation.model_validate(
            {
                "version": 1,
                "sample_id": "sample-1",
                "status": "reviewed",
                "reviewed_by": [],
                "events": [],
            }
        )
