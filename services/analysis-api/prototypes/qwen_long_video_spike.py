"""Throwaway Qwen long-video spike.

Question: can direct Qwen3-VL video input turn a complete seven-minute fitness
video into a structured, time-addressable action list quickly enough to replace
our fixed contact-sheet path?

The source video is uploaded only to DashScope's model-bound temporary OSS and
is not copied into the repository. The raw provider response is never persisted.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]
POLICY_URL = "https://dashscope.aliyuncs.com/api/v1/uploads"
GENERATION_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
)


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def upload_temporary_video(
    client: httpx.Client,
    *,
    api_key: str,
    model: str,
    video_path: Path,
) -> str:
    policy_response = client.get(
        POLICY_URL,
        params={"action": "getPolicy", "model": model},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    policy_response.raise_for_status()
    policy = policy_response.json()["data"]

    size_mb = video_path.stat().st_size / 1024 / 1024
    limit_mb = float(policy["max_file_size_mb"])
    if size_mb > limit_mb:
        raise RuntimeError(
            f"Video is {size_mb:.1f} MB but the model-bound upload limit is {limit_mb:.1f} MB"
        )

    object_key = f"{policy['upload_dir']}/{video_path.name}"
    fields = {
        "OSSAccessKeyId": policy["oss_access_key_id"],
        "policy": policy["policy"],
        "Signature": policy["signature"],
        "key": object_key,
        "x-oss-object-acl": policy["x_oss_object_acl"],
        "x-oss-forbid-overwrite": policy["x_oss_forbid_overwrite"],
        "success_action_status": "200",
    }
    with video_path.open("rb") as video_file:
        upload_response = client.post(
            policy["upload_host"],
            data=fields,
            files={"file": (video_path.name, video_file, "video/mp4")},
        )
    upload_response.raise_for_status()
    return f"oss://{object_key}"


def extract_text(response: dict[str, Any]) -> str:
    choices = response.get("output", {}).get("choices", [])
    if not choices:
        raise RuntimeError("Provider response did not contain a choice")
    content = choices[0].get("message", {}).get("content", [])
    for part in content:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            return part["text"]
    raise RuntimeError("Provider response did not contain text content")


def stream_generation(
    client: httpx.Client,
    *,
    api_key: str,
    payload: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    chunks: list[str] = []
    final_event: dict[str, Any] = {}
    with client.stream(
        "POST",
        GENERATION_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-DashScope-OssResourceResolve": "enable",
            "X-DashScope-SSE": "enable",
        },
        json=payload,
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            data = line.removeprefix("data:").strip()
            if data == "[DONE]":
                break
            event = json.loads(data)
            final_event = event
            choices = event.get("output", {}).get("choices", [])
            if not choices:
                continue
            for part in choices[0].get("message", {}).get("content", []):
                text = part.get("text") if isinstance(part, dict) else None
                if isinstance(text, str):
                    chunks.append(text)
    if not chunks:
        raise RuntimeError("Provider stream did not contain text content")
    return "".join(chunks), final_event


def main() -> int:
    load_dotenv(ROOT / ".env.local", override=False)
    api_key = required_env("DASHSCOPE_API_KEY")
    model = os.getenv("QWEN_VIDEO_MODEL", "qwen3-vl-flash").strip()
    video_path = Path(required_env("QWEN_SPIKE_VIDEO"))
    fps = float(os.getenv("QWEN_SPIKE_FPS", "0.5"))

    if not video_path.is_file():
        raise FileNotFoundError(f"Video not found: {video_path}")

    prompt = """
你是健身视频动作切分器。请从视频第 0 秒一直检查到视频结尾，不要只分析开头或摘要。
按时间顺序列出每一段可训练的动作；相同动作在不连续时段再次出现也要分别列出。
忽略纯讲解、转场、饮水和休息段。仅依据看得见的证据，不要猜重量。
时间必须是相对整条视频的绝对秒数，起止时间应覆盖该动作的实际示范或跟练区间。
请只输出一个 JSON 对象，不要 Markdown。格式：
{
  "coverage": {"start_seconds": 0, "end_seconds": number, "complete": boolean},
  "actions": [
    {
      "name_zh": string,
      "start_seconds": number,
      "end_seconds": number,
      "mode": "repetitions" | "duration" | "unknown",
      "sets": integer | null,
      "repetitions": integer | null,
      "duration_seconds": integer | null,
      "rest_seconds": integer | null,
      "needs_confirmation": boolean
    }
  ],
  "notes": string
}
""".strip()

    started_at = time.perf_counter()
    with httpx.Client(timeout=httpx.Timeout(900.0, connect=30.0)) as client:
        print("stage=upload started", flush=True)
        upload_started_at = time.perf_counter()
        temporary_url = upload_temporary_video(
            client,
            api_key=api_key,
            model=model,
            video_path=video_path,
        )
        upload_seconds = time.perf_counter() - upload_started_at
        print(f"stage=upload completed seconds={upload_seconds:.2f}", flush=True)

        print("stage=inference started", flush=True)
        inference_started_at = time.perf_counter()
        response_text, provider_response = stream_generation(
            client,
            api_key=api_key,
            payload={
                "model": model,
                "input": {
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "video": temporary_url,
                                    "fps": fps,
                                    "min_pixels": 4096,
                                    "max_pixels": 65536,
                                    "total_pixels": 67108864,
                                },
                                {"text": prompt},
                            ],
                        }
                    ]
                },
                "parameters": {
                    "result_format": "message",
                    "response_format": {"type": "json_object"},
                    "incremental_output": True,
                    "temperature": 0.1,
                    "max_tokens": 4096,
                },
            },
        )
        inference_seconds = time.perf_counter() - inference_started_at
        print(f"stage=inference completed seconds={inference_seconds:.2f}", flush=True)

    parsed = json.loads(response_text)
    actions = parsed.get("actions", [])
    safe_result = {
        "question": "Can direct Qwen3-VL video input replace the fixed contact-sheet path?",
        "model": model,
        "video_duration_label": "7min",
        "fps": fps,
        "upload_seconds": round(upload_seconds, 2),
        "inference_seconds": round(inference_seconds, 2),
        "total_seconds": round(time.perf_counter() - started_at, 2),
        "coverage": parsed.get("coverage"),
        "action_count": len(actions),
        "actions": actions,
        "notes": parsed.get("notes"),
        "usage": provider_response.get("usage"),
        "request_id": provider_response.get("request_id"),
    }
    print(json.dumps(safe_result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (httpx.HTTPError, KeyError, ValueError, RuntimeError) as error:
        print(f"Qwen spike failed: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1) from error
