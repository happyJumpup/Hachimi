import asyncio
import json
from pathlib import Path
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from hakimi_analysis.models import (
    Segment,
    SpeechUnderstandingResult,
    VisualLocalizationResult,
)
from hakimi_analysis.providers.base import (
    ProviderError,
    ProviderSchemaError,
    raise_for_provider_status,
    request_with_retry,
)

StructuredResult = TypeVar("StructuredResult", bound=BaseModel)


class ArkResponsesClient:
    def __init__(
        self,
        *,
        api_key: str,
        model_id: str,
        base_url: str,
        http_client: httpx.AsyncClient,
        retry_delays: tuple[float, ...] = (1.0, 2.0),
        file_poll_interval_seconds: float = 1.0,
        file_poll_limit: int = 90,
    ) -> None:
        self._api_key = api_key
        self._model_id = model_id
        self._base_url = base_url.rstrip("/")
        self._http_client = http_client
        self._retry_delays = retry_delays
        self._file_poll_interval_seconds = file_poll_interval_seconds
        self._file_poll_limit = file_poll_limit

    async def understand_speech(
        self,
        *,
        transcript: dict[str, Any],
        trigger_seconds: float,
        window: Segment,
        instructions: str,
    ) -> SpeechUnderstandingResult:
        payload = {
            "trigger_seconds": trigger_seconds,
            "window": window.model_dump(mode="json"),
            "transcript": transcript,
        }
        result = await self._structured_response(
            instructions=instructions,
            content=[
                {
                    "type": "input_text",
                    "text": json.dumps(payload, ensure_ascii=False),
                }
            ],
            result_type=SpeechUnderstandingResult,
            schema_name="training_speech_understanding",
        )
        for signal in result.signals:
            if (
                signal.start_seconds < window.start_seconds - 0.5
                or signal.end_seconds > window.end_seconds + 0.5
            ):
                raise ProviderSchemaError("语音理解时间超出分析窗口")
        return result

    async def locate_visual(
        self,
        *,
        video_path: Path,
        window: Segment,
        trigger_seconds: float,
        instructions: str,
    ) -> VisualLocalizationResult:
        file_id = await self._upload_file(video_path)
        try:
            metadata = {
                "trigger_seconds": trigger_seconds,
                "window": window.model_dump(mode="json"),
                "time_rule": "Return absolute source-video seconds within this window.",
            }
            result = await self._structured_response(
                instructions=instructions,
                content=[
                    {"type": "input_video", "file_id": file_id},
                    {
                        "type": "input_text",
                        "text": json.dumps(metadata, ensure_ascii=False),
                    },
                ],
                result_type=VisualLocalizationResult,
                schema_name="visual_action_localization",
            )
            for segment in result.segments:
                if (
                    segment.start_seconds < window.start_seconds - 0.5
                    or segment.end_seconds > window.end_seconds + 0.5
                ):
                    raise ProviderSchemaError("视觉定位时间超出分析窗口")
            return result
        finally:
            await self._delete_file(file_id)

    async def _upload_file(self, path: Path) -> str:
        file_bytes = await asyncio.to_thread(path.read_bytes)
        response = await request_with_retry(
            self._http_client,
            "POST",
            f"{self._base_url}/files",
            retry_delays=self._retry_delays,
            headers=self._auth_headers(),
            data={
                "purpose": "user_data",
                "preprocess_configs[video][fps]": "1",
            },
            files={"file": (path.name, file_bytes, "video/mp4")},
        )
        raise_for_provider_status(response, "方舟文件")
        try:
            payload = response.json()
            file_id = str(payload["id"])
            status = str(payload.get("status", ""))
        except (KeyError, TypeError, ValueError) as error:
            raise ProviderSchemaError("方舟文件上传返回格式无效") from error
        if status not in {"processed", "succeeded", "completed", "active"}:
            await self._wait_for_file(file_id)
        return file_id

    async def _wait_for_file(self, file_id: str) -> None:
        for _ in range(self._file_poll_limit):
            response = await request_with_retry(
                self._http_client,
                "GET",
                f"{self._base_url}/files/{file_id}",
                retry_delays=self._retry_delays,
                headers=self._auth_headers(),
            )
            raise_for_provider_status(response, "方舟文件")
            try:
                status = str(response.json().get("status", ""))
            except (TypeError, ValueError) as error:
                raise ProviderSchemaError("方舟文件状态返回格式无效") from error
            if status in {"processed", "succeeded", "completed", "active"}:
                return
            if status in {"failed", "error", "cancelled"}:
                raise ProviderError(
                    "provider_error",
                    "方舟视频预处理失败",
                    retryable=False,
                )
            await asyncio.sleep(self._file_poll_interval_seconds)
        raise ProviderError(
            "provider_error",
            "方舟视频预处理超时",
            retryable=True,
        )

    async def _delete_file(self, file_id: str) -> None:
        response = await request_with_retry(
            self._http_client,
            "DELETE",
            f"{self._base_url}/files/{file_id}",
            retry_delays=self._retry_delays,
            headers=self._auth_headers(),
        )
        raise_for_provider_status(response, "方舟文件清理")

    async def _structured_response(
        self,
        *,
        instructions: str,
        content: list[dict[str, str]],
        result_type: type[StructuredResult],
        schema_name: str,
    ) -> StructuredResult:
        response = await request_with_retry(
            self._http_client,
            "POST",
            f"{self._base_url}/responses",
            retry_delays=self._retry_delays,
            headers={**self._auth_headers(), "Content-Type": "application/json"},
            json={
                "model": self._model_id,
                "store": False,
                "instructions": instructions,
                "input": [{"role": "user", "content": content}],
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "strict": True,
                        "schema": result_type.model_json_schema(),
                    }
                },
            },
        )
        raise_for_provider_status(response, "方舟推理")
        request_id: str | None = None
        try:
            payload = response.json()
            request_id = str(payload.get("id")) if payload.get("id") else None
            text = self._output_text(payload)
            return result_type.model_validate_json(text)
        except (ValueError, TypeError, ValidationError) as error:
            raise ProviderSchemaError("方舟结构化返回格式无效", request_id=request_id) from error

    @staticmethod
    def _output_text(payload: dict[str, Any]) -> str:
        top_level = payload.get("output_text")
        if isinstance(top_level, str):
            return top_level
        for output in payload.get("output", []):
            if not isinstance(output, dict):
                continue
            for content in output.get("content", []):
                if isinstance(content, dict) and content.get("type") == "output_text":
                    text = content.get("text")
                    if isinstance(text, str):
                        return text
        raise ValueError("response contains no output_text")

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}
