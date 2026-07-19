import asyncio
import base64
from pathlib import Path
from typing import Any

import httpx

from hakimi_analysis.models import Transcript, TranscriptUtterance, TranscriptWord
from hakimi_analysis.providers.base import ProviderError, request_with_retry


class VolcAsrClient:
    def __init__(
        self,
        *,
        api_key: str,
        resource_id: str,
        url: str,
        http_client: httpx.AsyncClient,
        retry_delays: tuple[float, ...] = (1.0, 2.0),
    ) -> None:
        self._api_key = api_key
        self._resource_id = resource_id
        self._url = url
        self._http_client = http_client
        self._retry_delays = retry_delays

    async def recognize(
        self,
        *,
        audio_path: Path,
        window_start_seconds: float,
        request_id: str,
    ) -> Transcript:
        audio_bytes = await asyncio.to_thread(audio_path.read_bytes)
        payload = {
            "user": {"uid": f"hachimi-{request_id}"},
            "audio": {"data": base64.b64encode(audio_bytes).decode("ascii")},
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
            },
        }
        response = await request_with_retry(
            self._http_client,
            "POST",
            self._url,
            retry_delays=self._retry_delays,
            headers={
                "X-Api-Key": self._api_key,
                "X-Api-Resource-Id": self._resource_id,
                "X-Api-Request-Id": request_id,
                "X-Api-Sequence": "-1",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        status_code = response.headers.get("X-Api-Status-Code")
        provider_request_id = response.headers.get("X-Tt-Logid")
        if status_code == "20000003":
            return Transcript(text="", utterances=[], provider_request_id=provider_request_id)
        if status_code != "20000000":
            retryable = status_code is None or status_code.startswith("55")
            code = "configuration_error" if response.status_code in {401, 403} else "provider_error"
            raise ProviderError(
                code,
                "语音识别服务暂时不可用",
                retryable=retryable,
                request_id=provider_request_id,
            )

        try:
            result: dict[str, Any] = response.json().get("result", {})
            utterances = [
                TranscriptUtterance(
                    text=str(item.get("text", "")),
                    start_seconds=window_start_seconds + float(item["start_time"]) / 1000,
                    end_seconds=window_start_seconds + float(item["end_time"]) / 1000,
                    words=[
                        TranscriptWord(
                            text=str(word.get("text", "")),
                            start_seconds=(
                                window_start_seconds + float(word["start_time"]) / 1000
                            ),
                            end_seconds=window_start_seconds + float(word["end_time"]) / 1000,
                        )
                        for word in item.get("words", [])
                    ],
                )
                for item in result.get("utterances", [])
            ]
            return Transcript(
                text=str(result.get("text", "")),
                utterances=utterances,
                provider_request_id=provider_request_id,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ProviderError(
                "schema_error",
                "语音识别返回格式无效",
                retryable=False,
                request_id=provider_request_id,
            ) from error
