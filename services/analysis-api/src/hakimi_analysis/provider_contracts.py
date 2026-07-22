from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class PromptContractError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PromptContract:
    name: str
    contract_version: str
    input_media_type: str
    time_coordinate: str
    output_schema: str
    compatible_model_families: tuple[str, ...]
    prompt_sha256: str
    prompt: str


@dataclass(frozen=True, slots=True)
class AsrContextContract:
    contract_version: str
    request_field: str
    mode: str
    content_sha256: str
    request_sha256: str
    hotwords: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PromptContractRegistry:
    speech: PromptContract
    visual: PromptContract
    asr_context: AsrContextContract

    @classmethod
    def load(
        cls,
        root: Path,
        *,
        speech_model_id: str,
        visual_model_ids: tuple[str, ...],
    ) -> PromptContractRegistry:
        speech = _load_contract(root, "speech-evidence")
        visual = _load_contract(root, "visual-evidence")
        asr_context = _load_asr_context(root)
        _validate_speech(speech, speech_model_id)
        _validate_visual(visual, visual_model_ids)
        return cls(speech=speech, visual=visual, asr_context=asr_context)


_MANIFEST_KEYS = {
    "contract_version",
    "input_media_type",
    "time_coordinate",
    "output_schema",
    "compatible_model_families",
    "prompt_sha256",
}

_ASR_CONTEXT_KEYS = {
    "contract_version",
    "request_field",
    "mode",
    "content_sha256",
}


def _load_asr_context(root: Path) -> AsrContextContract:
    contract_root = root / "asr-fitness-context"
    content_path = contract_root / "HOTWORDS.txt"
    manifest_path = contract_root / "contract.json"
    try:
        content = content_path.read_text(encoding="utf-8")
        raw: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PromptContractError("ASR context contract cannot be loaded") from error
    if (
        not isinstance(raw, dict)
        or set(raw) != _ASR_CONTEXT_KEYS
        or not all(isinstance(raw.get(key), str) and raw[key].strip() for key in raw)
    ):
        raise PromptContractError("ASR context contract manifest is invalid")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    hotwords = tuple(line.strip() for line in content.splitlines() if line.strip())
    if (
        raw["request_field"] != "request.context"
        or raw["mode"] != "hotwords"
        or raw["content_sha256"].casefold() != digest
        or not hotwords
        or len(hotwords) > 100
        or len(set(hotwords)) != len(hotwords)
    ):
        raise PromptContractError("ASR context contract semantics are incompatible")
    return AsrContextContract(
        contract_version=raw["contract_version"],
        request_field=raw["request_field"],
        mode=raw["mode"],
        content_sha256=digest,
        request_sha256=hashlib.sha256(
            serialize_asr_hotword_context(hotwords).encode("utf-8")
        ).hexdigest(),
        hotwords=hotwords,
    )


def serialize_asr_hotword_context(hotwords: tuple[str, ...]) -> str:
    return json.dumps(
        {"hotwords": [{"word": word} for word in hotwords]},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _load_contract(root: Path, name: str) -> PromptContract:
    contract_root = root / name
    prompt_path = contract_root / "PROMPT.md"
    manifest_path = contract_root / "contract.json"
    try:
        prompt = prompt_path.read_text(encoding="utf-8")
        raw: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PromptContractError(f"{name} prompt contract cannot be loaded") from error
    if not isinstance(raw, dict) or set(raw) != _MANIFEST_KEYS:
        raise PromptContractError(f"{name} prompt contract manifest is invalid")
    scalar_keys = _MANIFEST_KEYS - {"compatible_model_families"}
    if not all(isinstance(raw.get(key), str) and raw[key].strip() for key in scalar_keys):
        raise PromptContractError(f"{name} prompt contract manifest is invalid")
    families = raw.get("compatible_model_families")
    if not isinstance(families, list) or not families or not all(
        isinstance(item, str) and item.strip() for item in families
    ):
        raise PromptContractError(f"{name} model families are invalid")
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    if raw["prompt_sha256"].casefold() != digest:
        raise PromptContractError(f"{name} prompt hash does not match the manifest")
    return PromptContract(
        name=name,
        contract_version=raw["contract_version"],
        input_media_type=raw["input_media_type"],
        time_coordinate=raw["time_coordinate"],
        output_schema=raw["output_schema"],
        compatible_model_families=tuple(families),
        prompt_sha256=digest,
        prompt=prompt,
    )


def _validate_speech(contract: PromptContract, model_id: str) -> None:
    if (
        contract.input_media_type != "timestamped_transcript"
        or contract.time_coordinate != "analysis_range_relative"
        or contract.output_schema != "SpeechUnderstandingResult.v1"
    ):
        raise PromptContractError("speech prompt contract semantics are incompatible")
    _validate_models(contract, (model_id,))


def _validate_visual(contract: PromptContract, model_ids: tuple[str, ...]) -> None:
    normalized_prompt = " ".join(contract.prompt.casefold().replace("-", " ").split())
    if (
        contract.input_media_type != "continuous_silent_mp4"
        or contract.time_coordinate != "chunk_relative"
        or contract.output_schema != "VisualLocalizationResult.v1"
        or "contact sheet" in normalized_prompt
        or "row major" in normalized_prompt
        or "拼图" in contract.prompt
        or "相邻图片" in contract.prompt
    ):
        raise PromptContractError("visual prompt contract semantics are incompatible")
    if not model_ids:
        raise PromptContractError("visual prompt contract has no configured model")
    _validate_models(contract, model_ids)


def _validate_models(contract: PromptContract, model_ids: tuple[str, ...]) -> None:
    compatible = set(contract.compatible_model_families)
    for model_id in model_ids:
        family = _model_family(model_id)
        if family not in compatible:
            raise PromptContractError(
                f"{contract.name} prompt contract is incompatible with configured model family"
            )


def _model_family(model_id: str) -> str:
    normalized = model_id.strip().casefold()
    if normalized.startswith("doubao-seed-2-0-"):
        return "doubao-seed-2.0"
    if normalized.startswith("qwen3-vl-"):
        return "qwen3-vl"
    raise PromptContractError("configured model family is not recognized")


__all__ = [
    "AsrContextContract",
    "PromptContract",
    "PromptContractError",
    "PromptContractRegistry",
    "serialize_asr_hotword_context",
]
