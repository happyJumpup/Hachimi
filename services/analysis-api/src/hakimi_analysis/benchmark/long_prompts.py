"""Frozen prompt contract used only by the 7/19-minute experiment."""

import hashlib
import json

from hakimi_analysis.benchmark.models import CandidateEnvelope
from hakimi_analysis.benchmark.prompts import TASK_INSTRUCTIONS

VISUAL_ONLY_SUFFIX = "\n本次输入没有音轨，只允许使用 visual 证据。"
LONG_VIDEO_PROMPTS = {"long-video-ab-v1": TASK_INSTRUCTIONS}


def long_video_prompt(version: str) -> str:
    try:
        return LONG_VIDEO_PROMPTS[version]
    except KeyError as error:
        raise ValueError("long-video prompt version is not frozen") from error


def long_video_prompt_sha256(version: str) -> str:
    return hashlib.sha256(long_video_prompt(version).encode()).hexdigest()


def long_fusion_prompt(
    transcript: dict[str, object],
    visual: CandidateEnvelope | None,
    *,
    source_window: tuple[float, float] | None = None,
    instructions: str,
) -> str:
    """Build a source-clock fusion request without changing native-AV prompts."""

    evidence: dict[str, object] = {
        "transcript": transcript,
        "visual_candidates": (
            visual.model_dump(mode="json") if visual is not None else None
        ),
    }
    time_instruction = ""
    if source_window is not None:
        window_start, window_end = source_window
        if window_start < 0 or window_end <= window_start:
            raise ValueError("fusion source window is invalid")
        evidence["time_contract"] = {
            "coordinate_system": "absolute_source_video_seconds",
            "allowed_window": {
                "start_seconds": window_start,
                "end_seconds": window_end,
            },
            "rule": (
                "Return only absolute source-video seconds inside this window; "
                "never reset time to zero for a chunk."
            ),
        }
        time_instruction = (
            "\nAll start_seconds/end_seconds must be absolute source-video seconds; "
            "never use a chunk-relative clock."
        )
    return (
        instructions
        + "\n融合以下 ASR 与纯视觉证据；冲突时保留 null 并忠实记录时间：\n"
        + json.dumps(evidence, ensure_ascii=False)
        + time_instruction
    )
