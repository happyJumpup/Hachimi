from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from hakimi_analysis.models import Segment, VisualLocalizationResult
from hakimi_analysis.providers.base import (
    ProviderError,
    ProviderSchemaError,
    parse_retry_after_seconds,
)
from hakimi_analysis.temporary_cos import TencentCosTemporaryStore


class QwenVisualClient:
    provider_name = "qwen"
    adapter_version = "qwen-chat-completions-v1"

    def __init__(
        self,
        *,
        api_key: str,
        model_id: str,
        base_url: str,
        http_client: httpx.AsyncClient,
        object_store: TencentCosTemporaryStore,
    ) -> None:
        self._api_key = api_key
        self._model_id = model_id
        self._base_url = base_url.rstrip("/")
        self._http_client = http_client
        self._object_store = object_store

    def prepare(self, video_path: Path) -> AbstractAsyncContextManager[str | Path]:
        return self._object_store.signed_read_url(video_path)

    async def locate(
        self,
        prepared_media: str | Path,
        *,
        window: Segment,
        instructions: str,
    ) -> VisualLocalizationResult:
        if not isinstance(prepared_media, str) or not prepared_media.startswith("https://"):
            raise ProviderError(
                "media_error",
                "Qwen visual provider requires a private signed URL",
                retryable=False,
            )
        duration_seconds = window.end_seconds - window.start_seconds
        prompt = (
            f"{instructions}\n"
            "Return JSON only. The input is a continuous silent MP4 chunk. "
            f"All times must be in 0..{duration_seconds:.3f} seconds relative to this chunk."
        )
        try:
            response = await self._http_client.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model_id,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "video_url",
                                    "video_url": {"url": prepared_media},
                                    "fps": 1,
                                },
                                {"type": "text", "text": prompt},
                            ],
                        }
                    ],
                    "response_format": {"type": "json_object"},
                    "enable_thinking": False,
                },
            )
        except httpx.RequestError as error:
            raise ProviderError(
                "provider_error",
                "Qwen visual request failed",
                retryable=True,
            ) from error
        _raise_qwen_status(response)
        request_id = response.headers.get("x-request-id")
        try:
            payload = response.json()
            request_id = request_id or str(payload.get("id") or "") or None
            content = payload["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("message content is not text")
            result = VisualLocalizationResult.model_validate_json(content)
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as error:
            raise ProviderSchemaError(
                "Qwen visual response failed local schema validation",
                request_id=request_id,
            ) from error
        _validate_chunk_clock(result, window, request_id=request_id)
        return result


def _raise_qwen_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    request_id = response.headers.get("x-request-id")
    error_code = ""
    try:
        payload: Any = response.json()
        if isinstance(payload, dict):
            error_payload = payload.get("error", {})
            if isinstance(error_payload, dict):
                error_code = str(error_payload.get("code", ""))
    except ValueError:
        pass
    normalized_code = error_code.casefold()
    if "inspection" in normalized_code or "content" in normalized_code:
        raise ProviderError(
            "content_safety",
            "Qwen rejected the visual content",
            retryable=False,
            request_id=request_id,
        )
    if response.status_code in {401, 403, 404}:
        raise ProviderError(
            "configuration_error",
            "Qwen model or entitlement is unavailable",
            retryable=False,
            request_id=request_id,
        )
    raise ProviderError(
        "provider_error",
        "Qwen visual service is unavailable",
        retryable=response.status_code in {408, 429} or response.status_code >= 500,
        request_id=request_id,
        retry_after_seconds=parse_retry_after_seconds(response),
    )


def _validate_chunk_clock(
    result: VisualLocalizationResult,
    window: Segment,
    *,
    request_id: str | None,
) -> None:
    for segment in result.segments:
        if (
            segment.start_seconds < window.start_seconds
            or segment.end_seconds > window.end_seconds
        ):
            raise ProviderSchemaError(
                "Qwen visual timestamps exceed the chunk-relative clock",
                request_id=request_id,
            )


__all__ = ["QwenVisualClient"]
