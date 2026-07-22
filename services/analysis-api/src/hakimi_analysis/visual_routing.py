from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Protocol

from hakimi_analysis.models import Segment, VisualLocalizationResult
from hakimi_analysis.observability import log_safe_fields
from hakimi_analysis.providers.base import ProviderError, ProviderSchemaError

LOGGER = logging.getLogger("hakimi_analysis.visual_routing")


class PreparedVisualProvider(Protocol):
    provider_name: str
    adapter_version: str

    def prepare(self, video_path: Path) -> AbstractAsyncContextManager[str | Path]: ...

    async def locate(
        self,
        prepared_media: str | Path,
        *,
        window: Segment,
        instructions: str,
    ) -> VisualLocalizationResult: ...


class DirectVisualLocator(Protocol):
    async def locate_visual(
        self,
        *,
        video_path: Path,
        window: Segment,
        instructions: str,
    ) -> VisualLocalizationResult: ...


class DirectVisualProvider:
    provider_name = "ark"
    adapter_version = "ark-responses-v1"

    def __init__(self, locator: DirectVisualLocator) -> None:
        self._locator = locator

    def prepare(self, video_path: Path) -> AbstractAsyncContextManager[str | Path]:
        return _DirectMediaContext(video_path)

    async def locate(
        self,
        prepared_media: str | Path,
        *,
        window: Segment,
        instructions: str,
    ) -> VisualLocalizationResult:
        if not isinstance(prepared_media, Path):
            raise ProviderError(
                "media_error",
                "direct visual provider requires a local video chunk",
                retryable=False,
            )
        result = await self._locator.locate_visual(
            video_path=prepared_media,
            window=window,
            instructions=instructions,
        )
        return result


class _DirectMediaContext:
    def __init__(self, path: Path) -> None:
        self._path = path

    async def __aenter__(self) -> str | Path:
        return self._path

    async def __aexit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        return None


@dataclass(slots=True)
class VisualRouteState:
    fallback_active: bool = False
    visual_calls: int = 0


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        failure_window_seconds: float = 60,
        open_seconds: float = 60,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._failure_window_seconds = failure_window_seconds
        self._open_seconds = open_seconds
        self._clock = clock
        self._failures: list[float] = []
        self._opened_at: float | None = None
        self._permanently_open = False
        self._half_open_probe_in_flight = False

    def acquire(self) -> bool:
        if self._permanently_open:
            return False
        if self._opened_at is None:
            return True
        if self._clock() - self._opened_at < self._open_seconds:
            return False
        if self._half_open_probe_in_flight:
            return False
        self._half_open_probe_in_flight = True
        return True

    def record_success(self) -> None:
        self._failures.clear()
        self._opened_at = None
        self._half_open_probe_in_flight = False

    def record_failure(self, error: ProviderError) -> None:
        self._half_open_probe_in_flight = False
        if error.code == "configuration_error":
            self._permanently_open = True
            self._opened_at = self._clock()
            return
        now = self._clock()
        self._failures = [
            failed_at
            for failed_at in self._failures
            if now - failed_at <= self._failure_window_seconds
        ]
        self._failures.append(now)
        if len(self._failures) >= self._failure_threshold or self._opened_at is not None:
            self._opened_at = now


class SequentialVisualRouter:
    def __init__(
        self,
        *,
        primary: PreparedVisualProvider,
        fallback: PreparedVisualProvider | None = None,
        attempt_timeout_seconds: float = 20,
        max_attempts_per_provider: int = 2,
        max_visual_calls: int = 12,
        primary_breaker: CircuitBreaker | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if attempt_timeout_seconds <= 0:
            raise ValueError("visual attempt timeout must be positive")
        if max_attempts_per_provider <= 0 or max_visual_calls <= 0:
            raise ValueError("visual attempt limits must be positive")
        self._primary = primary
        self._fallback = fallback
        self._attempt_timeout_seconds = attempt_timeout_seconds
        self._max_attempts_per_provider = max_attempts_per_provider
        self._max_visual_calls = max_visual_calls
        self._primary_breaker = primary_breaker or CircuitBreaker(clock=clock)
        self._clock = clock

    def new_run_state(self) -> VisualRouteState:
        return VisualRouteState()

    async def locate_visual(
        self,
        *,
        video_path: Path,
        window: Segment,
        instructions: str,
        state: VisualRouteState,
        deadline: float,
        chunk_index: int,
    ) -> VisualLocalizationResult:
        if state.fallback_active:
            return await self._call_provider(
                self._require_fallback(),
                video_path=video_path,
                window=window,
                instructions=instructions,
                state=state,
                deadline=deadline,
                chunk_index=chunk_index,
            )

        if not self._primary_breaker.acquire():
            state.fallback_active = True
            return await self._call_provider(
                self._require_fallback(),
                video_path=video_path,
                window=window,
                instructions=instructions,
                state=state,
                deadline=deadline,
                chunk_index=chunk_index,
                fallback_reason="circuit_open",
            )

        try:
            result = await self._call_provider(
                self._primary,
                video_path=video_path,
                window=window,
                instructions=instructions,
                state=state,
                deadline=deadline,
                chunk_index=chunk_index,
            )
        except ProviderError as error:
            if not _may_fallback(error):
                raise
            self._primary_breaker.record_failure(error)
            if self._fallback is None:
                raise
            state.fallback_active = True
            return await self._call_provider(
                self._fallback,
                video_path=video_path,
                window=window,
                instructions=instructions,
                state=state,
                deadline=deadline,
                chunk_index=chunk_index,
                fallback_reason=error.code,
            )
        self._primary_breaker.record_success()
        return result

    async def _call_provider(
        self,
        provider: PreparedVisualProvider,
        *,
        video_path: Path,
        window: Segment,
        instructions: str,
        state: VisualRouteState,
        deadline: float,
        chunk_index: int,
        fallback_reason: str | None = None,
    ) -> VisualLocalizationResult:
        async with provider.prepare(video_path) as prepared_media:
            last_error: ProviderError | None = None
            for attempt in range(1, self._max_attempts_per_provider + 1):
                if state.visual_calls >= self._max_visual_calls:
                    raise ProviderError(
                        "budget_exhausted",
                        "visual provider call budget exhausted",
                        retryable=False,
                    )
                remaining_seconds = deadline - self._clock()
                if remaining_seconds <= 0:
                    raise ProviderError(
                        "budget_exhausted",
                        "visual evidence deadline exhausted",
                        retryable=False,
                    )
                state.visual_calls += 1
                started_at = self._clock()
                try:
                    async with asyncio.timeout(
                        min(self._attempt_timeout_seconds, remaining_seconds)
                    ):
                        result = await provider.locate(
                            prepared_media,
                            window=window,
                            instructions=instructions,
                        )
                except TimeoutError:
                    last_error = ProviderError(
                        "timeout",
                        "visual provider attempt timed out",
                        retryable=True,
                    )
                except ProviderError as error:
                    last_error = error
                else:
                    self._log_attempt(
                        provider=provider,
                        chunk_index=chunk_index,
                        attempt=attempt,
                        elapsed_ms=round((self._clock() - started_at) * 1000),
                        fallback_reason=fallback_reason,
                    )
                    return result
                self._log_attempt(
                    provider=provider,
                    chunk_index=chunk_index,
                    attempt=attempt,
                    elapsed_ms=round((self._clock() - started_at) * 1000),
                    fallback_reason=fallback_reason,
                    error_code=last_error.code,
                )
                if not last_error.retryable or isinstance(last_error, ProviderSchemaError):
                    raise last_error
                retry_after = last_error.retry_after_seconds or 0
                if retry_after > 0 and attempt < self._max_attempts_per_provider:
                    if retry_after >= deadline - self._clock():
                        raise ProviderError(
                            "budget_exhausted",
                            "Retry-After exceeds the visual evidence deadline",
                            retryable=False,
                        )
                    await asyncio.sleep(retry_after)
            if last_error is not None:
                raise last_error
        raise AssertionError("visual provider attempts were not executed")

    def _require_fallback(self) -> PreparedVisualProvider:
        if self._fallback is None:
            raise ProviderError(
                "provider_error",
                "primary visual provider circuit is open and no fallback is configured",
                retryable=True,
            )
        return self._fallback

    @staticmethod
    def _log_attempt(
        *,
        provider: PreparedVisualProvider,
        chunk_index: int,
        attempt: int,
        elapsed_ms: int,
        fallback_reason: str | None,
        error_code: str | None = None,
    ) -> None:
        log_safe_fields(
            LOGGER,
            provider=provider.provider_name,
            adapter_version=provider.adapter_version,
            chunk_index=chunk_index,
            attempt=attempt,
            inference_ms=elapsed_ms,
            fallback_reason=fallback_reason,
            error_code=error_code,
        )


def _may_fallback(error: ProviderError) -> bool:
    return error.code in {"configuration_error", "schema_error", "timeout"} or (
        error.code == "provider_error" and error.retryable
    )


__all__ = [
    "CircuitBreaker",
    "DirectVisualProvider",
    "PreparedVisualProvider",
    "SequentialVisualRouter",
    "VisualRouteState",
]
