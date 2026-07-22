import asyncio
from collections.abc import Sequence
from typing import Any

import httpx


class ProviderError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        request_id: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.request_id = request_id
        self.retry_after_seconds = retry_after_seconds


class ProviderSchemaError(ProviderError):
    def __init__(self, message: str, *, request_id: str | None = None) -> None:
        super().__init__(
            "schema_error",
            message,
            retryable=False,
            request_id=request_id,
        )


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    retry_delays: Sequence[float] = (1.0, 2.0),
    **kwargs: Any,
) -> httpx.Response:
    attempts = len(retry_delays) + 1
    for attempt in range(attempts):
        response: httpx.Response | None = None
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.RequestError as error:
            if attempt == attempts - 1:
                raise ProviderError(
                    "provider_error",
                    "云服务网络请求失败",
                    retryable=True,
                ) from error
        else:
            if response.status_code not in {408, 429} and response.status_code < 500:
                return response
            if attempt == attempts - 1:
                return response
        configured_delay = retry_delays[attempt]
        retry_after = parse_retry_after_seconds(response) if response is not None else None
        await asyncio.sleep(max(configured_delay, retry_after or 0))
    raise AssertionError("retry loop exhausted")


def raise_for_provider_status(response: httpx.Response, provider: str) -> None:
    if response.is_success:
        return
    request_id = (
        response.headers.get("x-request-id")
        or response.headers.get("x-tt-logid")
        or response.headers.get("x-api-request-id")
    )
    if response.status_code in {401, 403}:
        raise ProviderError(
            "configuration_error",
            f"{provider} 鉴权或服务权限不可用",
            retryable=False,
            request_id=request_id,
        )
    raise ProviderError(
        "provider_error",
        f"{provider} 服务暂时不可用",
        retryable=response.status_code in {408, 429} or response.status_code >= 500,
        request_id=request_id,
        retry_after_seconds=parse_retry_after_seconds(response),
    )


def parse_retry_after_seconds(response: httpx.Response) -> float | None:
    raw_value = response.headers.get("retry-after", "").strip()
    if not raw_value:
        return None
    try:
        value = float(raw_value)
    except ValueError:
        return None
    return value if 0 <= value <= 60 else None
