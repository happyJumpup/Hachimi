from __future__ import annotations

import importlib.util
from pathlib import Path, PurePosixPath
from types import ModuleType

import pytest


def load_audit_module() -> ModuleType:
    script = Path(__file__).resolve().parents[3] / "deploy" / "audit-image-layers.py"
    spec = importlib.util.spec_from_file_location("competition_image_audit", script)
    if spec is None or spec.loader is None:
        raise RuntimeError("competition image audit module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("workspace/services/analysis-api/src/frame-001.jpg", "raw frame"),
        ("workspace/skills/transcript.vtt", "subtitle or transcript"),
        ("workspace/apps/web/dist/assets/unregistered.webp", "unregistered visual"),
        ("workspace/services/analysis-api/src/transcript.json", "transcript artifact"),
    ],
)
def test_release_audit_rejects_frames_and_transcripts(path: str, reason: str) -> None:
    audit = load_audit_module()
    assert reason in audit._violation(PurePosixPath(path))


def test_release_audit_allows_only_registered_pet_visuals() -> None:
    audit = load_audit_module()
    registered_pet_hash = "5518f49229cc0331bfdf9c5351e6a7806bf97cac6c882b2d5dc40e620f85561c"
    assert audit._violation(
        PurePosixPath("workspace/apps/web/dist/assets/idle-content-hash.webp"),
        content_sha256=registered_pet_hash,
    ) is None


def test_release_audit_allows_only_the_registered_ffmpeg_runtime_path() -> None:
    audit = load_audit_module()

    assert audit._violation(PurePosixPath("opt/trainpal/ffmpeg"), is_file=False) is None
    assert audit._violation(PurePosixPath("opt/trainpal/ffmpeg/bin/ffmpeg")) is None
    assert "bundled FFmpeg" in audit._violation(
        PurePosixPath("usr/local/bin/ffmpeg")
    )


@pytest.mark.parametrize(
    "name",
    [
        "ACCESS_COOKIE_SECRET",
        "ARK_API_KEY",
        "COS_SECRET_ID",
        "COS_SECRET_KEY",
        "JUDGE_ACCESS_CODE",
        "QWEN_API_KEY",
        "VOLC_ASR_API_KEY",
    ],
)
def test_release_audit_covers_every_production_secret_environment_name(
    name: str,
) -> None:
    audit = load_audit_module()
    assert name in audit.SECRET_ENV_NAMES
