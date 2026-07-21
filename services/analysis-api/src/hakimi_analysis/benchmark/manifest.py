import hashlib
import json
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import imageio_ffmpeg
from pydantic import Field, model_validator

from hakimi_analysis.benchmark.models import (
    BenchmarkSample,
    GoldStatus,
    RouteId,
    RunUnit,
    StrictModel,
)

EXPECTED_MODELS = {
    "seed_lite": "doubao-seed-2-0-lite-260428",
    "seed_mini": "doubao-seed-2-0-mini-260428",
    "qwen_omni": "qwen3.5-omni-flash-2026-03-15",
    "qwen_vl": "qwen3-vl-flash-2026-01-22",
    "asr_resource": "volc.seedasr.sauc.duration",
}


@dataclass(frozen=True, slots=True)
class MediaRequirement:
    role: str
    provider: Literal["seed", "qwen", "asr"]
    model_key: str
    media_field: Literal["av_path", "silent_video_path", "audio_path"]
    media_kind: Literal["video", "audio"]


@dataclass(frozen=True, slots=True)
class RouteDefinition:
    requirements: tuple[MediaRequirement, ...]


ROUTE_DEFINITIONS = {
    RouteId.SEED_AV: RouteDefinition(
        (MediaRequirement("av", "seed", "seed_lite", "av_path", "video"),)
    ),
    RouteId.SEED_MODULAR: RouteDefinition(
        (
            MediaRequirement(
                "visual", "seed", "seed_lite", "silent_video_path", "video"
            ),
            MediaRequirement("audio", "asr", "asr_resource", "audio_path", "audio"),
        )
    ),
    RouteId.QWEN_AV: RouteDefinition(
        (MediaRequirement("av", "qwen", "qwen_omni", "av_path", "video"),)
    ),
    RouteId.QWEN_MODULAR: RouteDefinition(
        (
            MediaRequirement(
                "visual", "qwen", "qwen_vl", "silent_video_path", "video"
            ),
            MediaRequirement("audio", "asr", "asr_resource", "audio_path", "audio"),
        )
    ),
    RouteId.ASR_TEXT: RouteDefinition(
        (MediaRequirement("audio", "asr", "asr_resource", "audio_path", "audio"),)
    ),
    RouteId.SEED_AUDIO: RouteDefinition(
        (MediaRequirement("audio", "seed", "seed_lite", "audio_path", "audio"),)
    ),
    RouteId.QWEN_AUDIO: RouteDefinition(
        (MediaRequirement("audio", "qwen", "qwen_omni", "audio_path", "audio"),)
    ),
}


class BenchmarkManifest(StrictModel):
    version: Literal[1]
    seed: Literal[20260721]
    models: dict[str, str]
    samples: list[BenchmarkSample] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_frozen_matrix(self) -> "BenchmarkManifest":
        if self.models != EXPECTED_MODELS:
            raise ValueError("benchmark model IDs must exactly match the frozen matrix")
        if len({sample.sample_id for sample in self.samples}) != len(self.samples):
            raise ValueError("sample IDs must be unique")
        return self


def load_manifest(path: Path, *, require_reviewed_gold: bool) -> BenchmarkManifest:
    manifest = BenchmarkManifest.model_validate_json(path.read_text(encoding="utf-8"))
    if require_reviewed_gold and any(
        sample.gold_status != GoldStatus.REVIEWED for sample in manifest.samples
    ):
        raise ValueError("all scoring annotations must be human-reviewed")
    return manifest


def build_run_plan(manifest: BenchmarkManifest) -> list[RunUnit]:
    schedule = [
        [
            RouteId.SEED_AV,
            RouteId.QWEN_AV,
            RouteId.QWEN_MODULAR,
            RouteId.SEED_MODULAR,
            RouteId.ASR_TEXT,
            RouteId.SEED_AUDIO,
            RouteId.QWEN_AUDIO,
        ],
        [
            RouteId.SEED_MODULAR,
            RouteId.QWEN_MODULAR,
            RouteId.SEED_AUDIO,
            RouteId.QWEN_AUDIO,
            RouteId.ASR_TEXT,
            RouteId.QWEN_AV,
            RouteId.SEED_AV,
        ],
        [
            RouteId.QWEN_AUDIO,
            RouteId.ASR_TEXT,
            RouteId.SEED_AUDIO,
            RouteId.SEED_AV,
            RouteId.QWEN_AV,
            RouteId.QWEN_MODULAR,
            RouteId.SEED_MODULAR,
        ],
        [
            RouteId.SEED_MODULAR,
            RouteId.QWEN_MODULAR,
            RouteId.ASR_TEXT,
            RouteId.SEED_AUDIO,
            RouteId.QWEN_AUDIO,
            RouteId.QWEN_AV,
            RouteId.SEED_AV,
        ],
        [
            RouteId.SEED_AUDIO,
            RouteId.QWEN_AUDIO,
            RouteId.ASR_TEXT,
            RouteId.SEED_AV,
            RouteId.QWEN_AV,
            RouteId.QWEN_MODULAR,
            RouteId.SEED_MODULAR,
        ],
        [
            RouteId.QWEN_AV,
            RouteId.SEED_AV,
            RouteId.SEED_MODULAR,
            RouteId.QWEN_MODULAR,
            RouteId.QWEN_AUDIO,
            RouteId.ASR_TEXT,
            RouteId.SEED_AUDIO,
        ],
        [
            RouteId.ASR_TEXT,
            RouteId.SEED_AUDIO,
            RouteId.QWEN_AUDIO,
            RouteId.SEED_AV,
            RouteId.QWEN_AV,
            RouteId.QWEN_MODULAR,
            RouteId.SEED_MODULAR,
        ],
        [
            RouteId.QWEN_AV,
            RouteId.SEED_AV,
            RouteId.SEED_MODULAR,
            RouteId.QWEN_MODULAR,
            RouteId.SEED_AUDIO,
            RouteId.QWEN_AUDIO,
            RouteId.ASR_TEXT,
        ],
        [
            RouteId.QWEN_MODULAR,
            RouteId.SEED_MODULAR,
            RouteId.QWEN_AUDIO,
            RouteId.ASR_TEXT,
            RouteId.SEED_AUDIO,
            RouteId.SEED_AV,
            RouteId.QWEN_AV,
        ],
    ]
    return [
        RunUnit(
            sample_id=manifest.samples[row_index // 3].sample_id,
            route_id=route_id,
            run_index=row_index % 3 + 1,
        )
        for row_index, routes in enumerate(schedule)
        for route_id in routes
    ]


def write_manifest(path: Path, manifest: BenchmarkManifest) -> None:
    path.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def verify_prepared_media(manifest: BenchmarkManifest) -> None:
    for sample in manifest.samples:
        media = (
            (sample.av_path, sample.av_sha256),
            (sample.silent_video_path, sample.silent_video_sha256),
            (sample.audio_path, sample.audio_sha256),
        )
        for path, expected_digest in media:
            if not path.is_file():
                raise ValueError("prepared benchmark media is missing")
            if _sha256(path) != expected_digest:
                raise ValueError("prepared benchmark media hash mismatch")
            actual_duration = _duration_seconds(path)
            if abs(actual_duration - sample.duration_seconds) > 1:
                raise ValueError("prepared benchmark media duration mismatch")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _duration_seconds(path: Path) -> float:
    if path.suffix.lower() == ".wav":
        with wave.open(str(path), "rb") as audio:
            return audio.getnframes() / audio.getframerate()
    _, duration = imageio_ffmpeg.count_frames_and_secs(str(path))
    return float(duration)
