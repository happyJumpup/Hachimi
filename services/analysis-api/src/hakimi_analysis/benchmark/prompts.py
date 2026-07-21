import json

from hakimi_analysis.benchmark.models import CandidateEnvelope

TASK_INSTRUCTIONS = """你是健身训练视频动作分析器。按时间顺序识别每一次可训练动作事件。
只报告输入证据中明确出现的动作、时间边界和训练参数；未出现的参数必须为 null，重量永远为 null。
kind 只能是 action、rest、explanation、transition。evidence_channels 只能包含 audio、visual。
不要合并时间上分离的同名动作，不要补全常见训练参数，不要输出解释文字。
严格返回下面 JSON Schema 对应的单个 JSON 对象：
""" + json.dumps(CandidateEnvelope.model_json_schema(), ensure_ascii=False)

VISUAL_ONLY_SUFFIX = "\n本次输入没有音轨，只允许使用 visual 证据。"
AUDIO_ONLY_SUFFIX = "\n本次输入只有音频，只允许使用 audio 证据。"


def fusion_prompt(transcript: dict[str, object], visual: CandidateEnvelope | None) -> str:
    evidence = {
        "transcript": transcript,
        "visual_candidates": (
            visual.model_dump(mode="json") if visual is not None else None
        ),
    }
    return (
        TASK_INSTRUCTIONS
        + "\n融合以下 ASR 与纯视觉证据；冲突时保留 null 并忠实记录时间：\n"
        + json.dumps(evidence, ensure_ascii=False)
    )
