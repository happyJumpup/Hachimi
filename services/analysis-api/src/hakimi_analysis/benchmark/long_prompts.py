"""Frozen prompt contract used only by the 7/19-minute experiment."""

import hashlib
import json

from hakimi_analysis.benchmark.long_contract import MAX_LONG_PRIMARY_DEMO_SECONDS
from hakimi_analysis.benchmark.models import CandidateEnvelope
from hakimi_analysis.benchmark.prompts import TASK_INSTRUCTIONS

VISUAL_ONLY_SUFFIX = "\n本次输入没有音轨，只允许使用 visual 证据。"
UNIQUE_ACTION_TASK_INSTRUCTIONS = (
    "你是健身训练视频动作分析器。识别整段视频中不同的可训练动作。\n"
    "每个不同的可训练动作只返回一个候选。"
    "同一动作的讲解、纠错和重复示范属于同一个动作，不得重复输出。\n"
    "同一动作跨时间块出现时必须使用同一个规范中文名。\n"
    "为每个动作选择最清晰、完整的主演示片段作为 start_seconds 和 end_seconds；"
    "不要把整个讲解章节当作演示片段，主演示片段不得超过"
    f"{MAX_LONG_PRIMARY_DEMO_SECONDS:g}秒。\n"
    "kind 必须为 action。只报告输入证据中明确出现的动作、时间边界和训练参数；"
    "未出现的参数必须为 null，重量永远为 null。\n"
    "evidence_channels 只能包含 audio、visual。"
    "不要补全常见训练参数，不要输出解释文字。\n"
    "严格返回下面 JSON Schema 对应的单个 JSON 对象：\n"
    + json.dumps(CandidateEnvelope.model_json_schema(), ensure_ascii=False)
)

LONG_VIDEO_PROMPTS = {
    "long-video-ab-v1": TASK_INSTRUCTIONS,
    "long-video-ab-v2": UNIQUE_ACTION_TASK_INSTRUCTIONS,
}


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
        "visual_candidates": (visual.model_dump(mode="json") if visual is not None else None),
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
