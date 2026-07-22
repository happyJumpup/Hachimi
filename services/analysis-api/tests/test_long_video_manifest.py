import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from hakimi_analysis.benchmark.long_contract import QWEN_VIDEO_PROJECTION_VERSION
from hakimi_analysis.benchmark.long_manifest import (
    EXPECTED_LONG_EXPERIMENT_MODELS,
    LongExperimentManifest,
    load_long_experiment_manifest,
)


def _gold_contract(*names: str) -> dict[str, object]:
    return {
        "version": "long-video-unique-actions-v1",
        "actions": [
            {"canonical_name": name, "accepted_aliases": [name]} for name in names
        ],
    }


def valid_manifest_payload(tmp_path: Path) -> dict[str, object]:
    ignored_root = tmp_path / ".benchmark-work" / "long-video"
    external_downloads = tmp_path.parent / f"{tmp_path.name}-downloads"
    return {
        "version": 1,
        "models": dict(EXPECTED_LONG_EXPERIMENT_MODELS),
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
                "source_id": "romanian-deadlift-7m",
                "source_path": str(external_downloads / "seven.mp4"),
                "sha256": "a" * 64,
                "duration_seconds": 424.31,
                "gold_path": str(ignored_root / "gold" / "seven.json"),
                "gold_version": "long-video-gold-v2",
                "gold_contract": _gold_contract("罗马尼亚硬拉"),
            },
            {
                "source_id": "beginner-full-workout-19m",
                "source_path": str(external_downloads / "nineteen.mp4"),
                "sha256": "b" * 64,
                "duration_seconds": 1157.46,
                "gold_path": str(ignored_root / "gold" / "nineteen.json"),
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
            "temporary_root": str(ignored_root / "tmp"),
            "raw_results_root": str(tmp_path / "benchmark-results" / "long-video" / "raw"),
            "summary_path": str(tmp_path / "benchmark-results" / "long-video" / "summary.json"),
        },
    }


def test_manifest_locks_two_full_sources_and_long_video_protocol(tmp_path: Path) -> None:
    manifest = LongExperimentManifest.model_validate(valid_manifest_payload(tmp_path))

    assert manifest.models.model_dump() == EXPECTED_LONG_EXPERIMENT_MODELS
    assert manifest.repetitions == 3
    assert manifest.qwen_video_projection_version == QWEN_VIDEO_PROJECTION_VERSION
    assert manifest.chunk.duration_seconds == 60
    assert manifest.chunk.overlap_seconds == 10
    assert [source.source_id for source in manifest.sources] == [
        "romanian-deadlift-7m",
        "beginner-full-workout-19m",
    ]


def test_manifest_rejects_non_frozen_protocol_or_duplicate_sources(tmp_path: Path) -> None:
    payload = valid_manifest_payload(tmp_path)
    models = payload["models"]
    assert isinstance(models, dict)
    models["qwen_visual_model"] = "qwen3-vl-flash"

    with pytest.raises(ValidationError, match="model IDs"):
        LongExperimentManifest.model_validate(payload)

    payload = valid_manifest_payload(tmp_path)
    sources = payload["sources"]
    assert isinstance(sources, list)
    sources[1]["source_id"] = sources[0]["source_id"]

    with pytest.raises(ValidationError, match="unique"):
        LongExperimentManifest.model_validate(payload)

    payload = valid_manifest_payload(tmp_path)
    chunk = payload["chunk"]
    assert isinstance(chunk, dict)
    chunk["overlap_seconds"] = 5

    with pytest.raises(ValidationError, match="60-second chunks with 10-second overlap"):
        LongExperimentManifest.model_validate(payload)

    payload = valid_manifest_payload(tmp_path)
    payload["prompt_version"] = "unfrozen-prompt-v1"

    with pytest.raises(ValidationError, match="long-video-ab-v3"):
        LongExperimentManifest.model_validate(payload)

    payload = valid_manifest_payload(tmp_path)
    payload["qwen_video_projection_version"] = "unfrozen-qwen-input"

    with pytest.raises(ValidationError, match="frozen Qwen video projection"):
        LongExperimentManifest.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("prompt_version", "long-video-ab-v2", "long-video-ab-v3"),
        ("gold_version", "seven-v1", "long-video-gold-v2"),
    ],
)
def test_manifest_rejects_historical_prompt_or_gold_contract(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    payload = valid_manifest_payload(tmp_path)
    if field == "prompt_version":
        payload[field] = value
    else:
        sources = payload["sources"]
        assert isinstance(sources, list)
        sources[0][field] = value

    with pytest.raises(ValidationError, match=message):
        LongExperimentManifest.model_validate(payload)


def test_manifest_exposes_a_stable_frozen_prompt_digest(tmp_path: Path) -> None:
    manifest = LongExperimentManifest.model_validate(valid_manifest_payload(tmp_path))

    assert len(manifest.prompt_sha256) == 64
    assert manifest.prompt_sha256 == LongExperimentManifest.model_validate(
        valid_manifest_payload(tmp_path)
    ).prompt_sha256


def test_manifest_requires_each_representative_window_to_fit_its_source(tmp_path: Path) -> None:
    payload = valid_manifest_payload(tmp_path)
    sources = payload["sources"]
    assert isinstance(sources, list)
    sources[0]["representative_start_seconds"] = 400

    with pytest.raises(ValidationError, match="representative 60-second window"):
        LongExperimentManifest.model_validate(payload)


def test_manifest_rejects_sources_longer_than_the_thirty_minute_protocol(tmp_path: Path) -> None:
    payload = valid_manifest_payload(tmp_path)
    sources = payload["sources"]
    assert isinstance(sources, list)
    sources[0]["duration_seconds"] = 1800.1

    with pytest.raises(ValidationError, match="less than or equal to 1800"):
        LongExperimentManifest.model_validate(payload)


def test_load_manifest_requires_ignored_gold_and_output_paths_but_allows_external_source(
    tmp_path: Path,
) -> None:
    payload = valid_manifest_payload(tmp_path)
    manifest_path = tmp_path / "benchmark-results" / "long-video" / "manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = load_long_experiment_manifest(manifest_path, repository_root=tmp_path)

    assert manifest.sources[0].source_path == (
        tmp_path.parent / f"{tmp_path.name}-downloads" / "seven.mp4"
    )

    payload = valid_manifest_payload(tmp_path)
    sources = payload["sources"]
    assert isinstance(sources, list)
    sources[0]["source_path"] = str(tmp_path / "docs" / "seven.mp4")
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="source path must be external or in a Git-ignored"):
        load_long_experiment_manifest(manifest_path, repository_root=tmp_path)

    payload = valid_manifest_payload(tmp_path)
    sources = payload["sources"]
    assert isinstance(sources, list)
    sources[0]["gold_path"] = str(tmp_path / "docs" / "seven.json")
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="gold path must be in a Git-ignored working root"):
        load_long_experiment_manifest(manifest_path, repository_root=tmp_path)

    payload = valid_manifest_payload(tmp_path)
    output = payload["output"]
    assert isinstance(output, dict)
    output["summary_path"] = str(tmp_path / "docs" / "summary.json")
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="output path must be in a Git-ignored working root"):
        load_long_experiment_manifest(manifest_path, repository_root=tmp_path)


def test_manifest_accepts_matching_source_hash_literals(tmp_path: Path) -> None:
    payload = valid_manifest_payload(tmp_path)
    source = tmp_path.parent / f"{tmp_path.name}-downloads" / "seven.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source placeholder")
    sources = payload["sources"]
    assert isinstance(sources, list)
    sources[0]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()

    manifest = LongExperimentManifest.model_validate(payload)

    assert manifest.sources[0].sha256 == hashlib.sha256(b"source placeholder").hexdigest()
