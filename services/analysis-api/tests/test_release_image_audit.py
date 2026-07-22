from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
from types import ModuleType

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def load_audit_module() -> ModuleType:
    script = PROJECT_ROOT / "deploy" / "audit-image-layers.py"
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


def test_release_audit_registers_every_versioned_trainpal_visual() -> None:
    audit = load_audit_module()
    source_files = sorted(
        [
            *(PROJECT_ROOT / "apps" / "web" / "public" / "trainpal" / "pets").rglob(
                "*.webp"
            ),
            *(PROJECT_ROOT / "apps" / "web" / "public" / "gymti" / "types").glob(
                "*.webp"
            ),
        ]
    )

    assert len(source_files) == 175
    for source_file in source_files:
        content_sha256 = hashlib.sha256(source_file.read_bytes()).hexdigest()
        image_path = PurePosixPath("workspace/apps/web/dist") / source_file.relative_to(
            PROJECT_ROOT / "apps" / "web" / "public"
        ).as_posix()
        assert audit._violation(image_path, content_sha256=content_sha256) is None


def test_registered_visual_manifest_is_complete_and_keeps_legacy_assets() -> None:
    manifest = json.loads(
        (PROJECT_ROOT / "deploy" / "registered-visual-assets.json").read_text(encoding="utf-8")
    )
    assets = manifest["assets"]
    versioned_sources = {
        str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
        for root in (
            PROJECT_ROOT / "apps" / "web" / "public" / "trainpal" / "pets",
            PROJECT_ROOT / "apps" / "web" / "public" / "gymti" / "types",
        )
        for path in root.rglob("*.webp")
    }

    assert manifest["version"] == 1
    assert len(versioned_sources) == 175
    assert {item["sourcePath"] for item in assets if not item.get("legacy", False)} == (
        versioned_sources
    )
    assert len(assets) == 180
    assert len({item["sha256"] for item in assets}) == 180


def test_web_builder_and_runtime_copy_the_shared_gymti_contract() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    contract_copy = (
        "COPY contracts/gymti-questionnaire.v1.json "
        "contracts/gymti-questionnaire.v1.json"
    )
    assert dockerfile.count(contract_copy) == 2


def test_release_image_verifiers_share_the_anonymous_gymti_smoke_contract() -> None:
    for verifier_name in ("verify-competition-image.sh", "verify-competition-image.ps1"):
        verifier = (PROJECT_ROOT / "deploy" / verifier_name).read_text(encoding="utf-8")
        assert "smoke-gymti-image.py" in verifier
        assert "GYMTI_LLM_ENABLED" in verifier
        assert "GYMTI_LLM_RETENTION_CONFIRMED" in verifier
        assert "/workspace/contracts/gymti-questionnaire.v1.json" in verifier
