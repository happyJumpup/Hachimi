import hashlib
import json
from pathlib import Path

import pytest

from hakimi_analysis.provider_contracts import (
    PromptContractError,
    PromptContractRegistry,
    serialize_asr_hotword_context,
)
from hakimi_analysis.provider_profile import ProviderProfile
from hakimi_analysis.settings import Settings


def _write_contract(
    root: Path,
    name: str,
    *,
    prompt: str,
    input_media_type: str,
    time_coordinate: str,
    output_schema: str,
    compatible_model_families: list[str],
) -> None:
    contract_root = root / name
    contract_root.mkdir(parents=True)
    (contract_root / "PROMPT.md").write_text(prompt, encoding="utf-8")
    (contract_root / "contract.json").write_text(
        json.dumps(
            {
                "contract_version": "1.0.0",
                "input_media_type": input_media_type,
                "time_coordinate": time_coordinate,
                "output_schema": output_schema,
                "compatible_model_families": compatible_model_families,
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            }
        ),
        encoding="utf-8",
    )


def _valid_registry_root(tmp_path: Path) -> Path:
    root = tmp_path / "provider-contracts"
    _write_contract(
        root,
        "speech-evidence",
        prompt="Extract only timestamped, explicit exercise evidence.",
        input_media_type="timestamped_transcript",
        time_coordinate="analysis_range_relative",
        output_schema="SpeechUnderstandingResult.v1",
        compatible_model_families=["doubao-seed-2.0"],
    )
    _write_contract(
        root,
        "visual-evidence",
        prompt="Inspect the complete continuous silent MP4 chunk.",
        input_media_type="continuous_silent_mp4",
        time_coordinate="chunk_relative",
        output_schema="VisualLocalizationResult.v1",
        compatible_model_families=["doubao-seed-2.0", "qwen3-vl"],
    )
    context_root = root / "asr-fitness-context"
    context_root.mkdir(parents=True)
    hotwords = "罗马尼亚硬拉\n蝴蝶机反向飞鸟\n"
    (context_root / "HOTWORDS.txt").write_text(hotwords, encoding="utf-8")
    (context_root / "contract.json").write_text(
        json.dumps(
            {
                "contract_version": "asr-fitness-context-v1",
                "request_field": "request.context",
                "mode": "hotwords",
                "content_sha256": hashlib.sha256(hotwords.encode("utf-8")).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    return root


def test_prompt_contract_registry_loads_semantically_compatible_contracts(
    tmp_path: Path,
) -> None:
    registry = PromptContractRegistry.load(
        _valid_registry_root(tmp_path),
        speech_model_id="doubao-seed-2-0-mini-260428",
        visual_model_ids=(
            "doubao-seed-2-0-mini-260428",
            "qwen3-vl-flash-2026-01-22",
        ),
    )

    assert registry.speech.contract_version == "1.0.0"
    assert registry.visual.input_media_type == "continuous_silent_mp4"
    assert registry.visual.time_coordinate == "chunk_relative"
    assert "contact sheet" not in registry.visual.prompt.casefold()
    assert registry.asr_context.request_sha256 == hashlib.sha256(
        serialize_asr_hotword_context(registry.asr_context.hotwords).encode("utf-8")
    ).hexdigest()
    assert registry.asr_context.hotwords == ("罗马尼亚硬拉", "蝴蝶机反向飞鸟")


def test_prompt_contract_registry_rejects_prompt_hash_drift(tmp_path: Path) -> None:
    root = _valid_registry_root(tmp_path)
    (root / "visual-evidence" / "PROMPT.md").write_text(
        "Changed without updating the manifest.",
        encoding="utf-8",
    )

    with pytest.raises(PromptContractError, match="prompt hash"):
        PromptContractRegistry.load(
            root,
            speech_model_id="doubao-seed-2-0-mini-260428",
            visual_model_ids=("doubao-seed-2-0-mini-260428",),
        )


@pytest.mark.parametrize(
    ("prompt", "media_type", "clock"),
    [
        ("Inspect a row-major contact sheet.", "continuous_silent_mp4", "chunk_relative"),
        ("Inspect the complete continuous silent MP4 chunk.", "contact_sheet", "chunk_relative"),
        (
            "Inspect the complete continuous silent MP4 chunk.",
            "continuous_silent_mp4",
            "source_absolute",
        ),
    ],
)
def test_visual_contract_rejects_legacy_media_or_clock_semantics(
    tmp_path: Path,
    prompt: str,
    media_type: str,
    clock: str,
) -> None:
    root = _valid_registry_root(tmp_path)
    visual_root = root / "visual-evidence"
    (visual_root / "PROMPT.md").write_text(prompt, encoding="utf-8")
    manifest = json.loads((visual_root / "contract.json").read_text(encoding="utf-8"))
    manifest["input_media_type"] = media_type
    manifest["time_coordinate"] = clock
    manifest["prompt_sha256"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    (visual_root / "contract.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(PromptContractError):
        PromptContractRegistry.load(
            root,
            speech_model_id="doubao-seed-2-0-mini-260428",
            visual_model_ids=("doubao-seed-2-0-mini-260428",),
        )


def test_provider_profile_freezes_the_five_minute_production_budget() -> None:
    profile = ProviderProfile.from_settings(Settings(_env_file=None))

    assert profile.profile_version == "five-minute-production-v1"
    assert profile.max_source_seconds == 300
    assert profile.max_source_bytes == 256 * 1024 * 1024
    assert profile.visual_chunk_seconds == 60
    assert profile.visual_overlap_seconds == 10
    assert profile.max_visual_chunks == 6
    assert profile.visual_attempt_timeout_seconds == 20
    assert profile.speech_timeout_seconds == 45
    assert profile.evidence_deadline_seconds == 170
    assert profile.run_timeout_seconds == 180
    assert profile.cleanup_reserve_seconds == 10
    assert profile.max_attempts_per_visual_provider == 2
    assert profile.max_visual_calls == 12


def test_provider_profile_rejects_an_internally_impossible_budget() -> None:
    with pytest.raises(ValueError, match="cleanup reserve"):
        ProviderProfile.from_settings(
            Settings(_env_file=None, analysis_evidence_deadline_seconds=175)
        )

    with pytest.raises(ValueError, match="visual call budget"):
        ProviderProfile.from_settings(
            Settings(_env_file=None, analysis_max_visual_calls=5)
        )
