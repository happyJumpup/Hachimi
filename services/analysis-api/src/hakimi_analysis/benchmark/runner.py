import asyncio
import time
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from hakimi_analysis.benchmark.manifest import (
    ROUTE_DEFINITIONS,
    BenchmarkManifest,
)
from hakimi_analysis.benchmark.models import (
    BenchmarkRunResult,
    BenchmarkRunStatus,
    CleanupOutcome,
    MediaHandle,
    MediaLifecycleRecord,
    RouteExecution,
    RouteId,
    RunUnit,
)
from hakimi_analysis.benchmark.transport import BenchmarkTransportError


class PreflightStage(StrEnum):
    CONFIGURATION = "configuration"
    MINIMAL_CALL = "minimal_call"
    SYNTHETIC_TWO_SECONDS = "synthetic_two_seconds"
    REAL_EIGHT_SECONDS = "real_eight_seconds"
    FULL_SAMPLE = "full_sample"


class GateFailure(RuntimeError):
    def __init__(
        self,
        stage: PreflightStage,
        code: str,
        *,
        retryable: bool,
        status_code: int | None = None,
        provider: str | None = None,
        cleanup_failed: bool = False,
        diagnostic: str | None = None,
    ) -> None:
        super().__init__(f"{stage}:{code}")
        self.stage = stage
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.provider = provider
        self.cleanup_failed = cleanup_failed
        self.diagnostic = diagnostic


class BenchmarkProvider(Protocol):
    async def check_configuration(self) -> None: ...

    async def minimal_call(self, model_id: str) -> None: ...

    async def upload(self, model_id: str, path: Path, media_kind: str) -> MediaHandle: ...

    async def probe(self, handle: MediaHandle) -> None: ...

    async def cleanup(self, handle: MediaHandle) -> None: ...

    async def verify_cleanup(self, handle: MediaHandle) -> CleanupOutcome: ...


class RouteAdapter(Protocol):
    async def execute(
        self,
        handles: Mapping[str, MediaHandle],
        *,
        run_index: int,
    ) -> RouteExecution: ...


class BenchmarkRunner:
    def __init__(
        self,
        providers: Mapping[str, BenchmarkProvider],
        adapters: Mapping[RouteId, RouteAdapter] | None = None,
        *,
        timeout_seconds: float,
        progress: Callable[[str, PreflightStage, str, float | None], None] | None = None,
    ) -> None:
        self._providers = providers
        self._adapters = adapters or {
            route_id: _ProviderRouteAdapter(providers[definition.requirements[0].provider])
            for route_id, definition in ROUTE_DEFINITIONS.items()
            if definition.requirements[0].provider in providers
        }
        self._timeout_seconds = timeout_seconds
        self._progress = progress
        self.lifecycle_records: list[MediaLifecycleRecord] = []

    async def preflight_provider(
        self,
        provider_name: str,
        minimal_model_id: str,
        staged_assets: Sequence[tuple[PreflightStage, str, Path, str]],
    ) -> None:
        provider = self._providers[provider_name]
        started = time.perf_counter()
        self._emit_progress(provider_name, PreflightStage.CONFIGURATION, "started", None)
        try:
            async with asyncio.timeout(self._timeout_seconds):
                await provider.check_configuration()
        except BenchmarkTransportError as error:
            raise GateFailure(
                PreflightStage.CONFIGURATION,
                error.code,
                retryable=error.retryable,
                status_code=error.status_code,
                provider=provider_name,
            ) from error
        self._emit_progress(
            provider_name,
            PreflightStage.CONFIGURATION,
            "completed",
            time.perf_counter() - started,
        )
        started = time.perf_counter()
        self._emit_progress(provider_name, PreflightStage.MINIMAL_CALL, "started", None)
        try:
            async with asyncio.timeout(self._timeout_seconds):
                await provider.minimal_call(minimal_model_id)
        except BenchmarkTransportError as error:
            raise GateFailure(
                PreflightStage.MINIMAL_CALL,
                error.code,
                retryable=error.retryable,
                status_code=error.status_code,
                provider=provider_name,
            ) from error
        except TimeoutError as error:
            raise GateFailure(
                PreflightStage.MINIMAL_CALL,
                "timeout",
                retryable=True,
                provider=provider_name,
            ) from error
        self._emit_progress(
            provider_name,
            PreflightStage.MINIMAL_CALL,
            "completed",
            time.perf_counter() - started,
        )
        for stage, model_id, path, media_kind in staged_assets:
            handle: MediaHandle | None = None
            failure: BaseException | None = None
            started = time.perf_counter()
            self._emit_progress(provider_name, stage, "started", None)
            try:
                async with asyncio.timeout(self._timeout_seconds):
                    handle = await provider.upload(model_id, path, media_kind)
                    await provider.probe(handle)
            except GateFailure as error:
                failure = error
            except BenchmarkTransportError as error:
                failure = GateFailure(
                    stage,
                    error.code,
                    retryable=error.retryable,
                    status_code=error.status_code,
                    provider=provider_name,
                    diagnostic=(
                        getattr(error, "diagnostic", None)
                        if isinstance(getattr(error, "diagnostic", None), str)
                        else None
                    ),
                )
            except TimeoutError:
                failure = GateFailure(
                    stage,
                    "timeout",
                    retryable=True,
                    provider=provider_name,
                )
            except Exception as error:
                safe_code = getattr(error, "code", "provider_error")
                if not isinstance(safe_code, str):
                    safe_code = "provider_error"
                failure = GateFailure(
                    stage,
                    safe_code,
                    retryable=False,
                    provider=provider_name,
                    diagnostic=(
                        getattr(error, "diagnostic", None)
                        if isinstance(getattr(error, "diagnostic", None), str)
                        else None
                    ),
                )
                failure.__cause__ = error
            except BaseException as error:
                failure = error
            cleanup_failed = False
            cleanup_cancelled = False
            if handle is not None:
                outcome, cleanup_cancelled = await _cleanup_handle(provider, handle)
                cleanup_failed = outcome == CleanupOutcome.FAILED
            elapsed = time.perf_counter() - started
            self._emit_progress(provider_name, stage, "completed", elapsed)
            if failure is not None:
                if isinstance(failure, GateFailure):
                    failure.cleanup_failed = cleanup_failed
                elif cleanup_failed:
                    failure.add_note("cleanup_failed")
                raise failure
            if cleanup_cancelled:
                cancellation = asyncio.CancelledError()
                if cleanup_failed:
                    cancellation.add_note("cleanup_failed")
                raise cancellation
            if cleanup_failed:
                raise GateFailure(
                    stage,
                    "cleanup_error",
                    retryable=False,
                    provider=provider_name,
                    cleanup_failed=True,
                )

    def _emit_progress(
        self,
        provider: str,
        stage: PreflightStage,
        event: str,
        elapsed: float | None,
    ) -> None:
        if self._progress is not None:
            self._progress(provider, stage, event, elapsed)

    async def run(
        self,
        manifest: BenchmarkManifest,
        plan: Sequence[RunUnit],
    ) -> list[BenchmarkRunResult]:
        samples = {sample.sample_id: sample for sample in manifest.samples}
        handles: dict[tuple[str, str, str], MediaHandle] = {}
        lifecycle: dict[tuple[str, str, str], MediaLifecycleRecord] = {}
        results: list[BenchmarkRunResult] = []
        run_failure: BaseException | None = None
        try:
            for unit in plan:
                sample = samples[unit.sample_id]
                definition = ROUTE_DEFINITIONS[unit.route_id]
                route_handles: dict[str, MediaHandle] = {}
                upload_seconds = 0.0
                inference_started: float | None = None
                try:
                    for requirement in definition.requirements:
                        provider = self._providers[requirement.provider]
                        model_id = manifest.models[requirement.model_key]
                        path = Path(getattr(sample, requirement.media_field))
                        handle_key = (requirement.provider, model_id, str(path))
                        handle = handles.get(handle_key)
                        if handle is None:
                            upload_started = time.perf_counter()
                            async with asyncio.timeout(self._timeout_seconds):
                                handle = await provider.upload(
                                    model_id,
                                    path,
                                    requirement.media_kind,
                                )
                            elapsed_upload = time.perf_counter() - upload_started
                            upload_seconds += elapsed_upload
                            handles[handle_key] = handle
                            lifecycle[handle_key] = MediaLifecycleRecord(
                                provider=requirement.provider,
                                model_id=model_id,
                                media_kind=requirement.media_kind,
                                upload_seconds=elapsed_upload,
                            )
                        route_handles[requirement.role] = handle
                    inference_started = time.perf_counter()
                    async with asyncio.timeout(self._timeout_seconds):
                        execution = await self._adapters[unit.route_id].execute(
                            route_handles,
                            run_index=unit.run_index,
                        )
                except asyncio.CancelledError:
                    raise
                except TimeoutError:
                    results.append(
                        _failed_run_result(
                            unit,
                            status=BenchmarkRunStatus.TIMEOUT,
                            error_code="timeout",
                            elapsed=(
                                time.perf_counter() - inference_started
                                if inference_started is not None
                                else 0
                            ),
                            upload_seconds=upload_seconds,
                        )
                    )
                    continue
                except Exception as error:
                    error_code = getattr(error, "code", "provider_error")
                    if not isinstance(error_code, str):
                        error_code = "provider_error"
                    status = (
                        BenchmarkRunStatus.SCHEMA_ERROR
                        if "schema" in error_code
                        else BenchmarkRunStatus.PROVIDER_ERROR
                    )
                    results.append(
                        _failed_run_result(
                            unit,
                            status=status,
                            error_code=error_code,
                            elapsed=(
                                time.perf_counter() - inference_started
                                if inference_started is not None
                                else 0
                            ),
                            upload_seconds=upload_seconds,
                        )
                    )
                    continue
                results.append(
                    BenchmarkRunResult(
                        sample_id=unit.sample_id,
                        route_id=unit.route_id,
                        run_index=unit.run_index,
                        candidates=execution.candidates,
                        upload_seconds=upload_seconds,
                        inference_seconds=(
                            time.perf_counter() - inference_started
                            if inference_started is not None
                            else 0
                        ),
                        provider_request_id=execution.provider_request_id,
                        input_tokens=execution.input_tokens,
                        output_tokens=execution.output_tokens,
                        visual_control=execution.visual_control,
                        preprocessing_seconds=execution.preprocessing_seconds,
                        first_response_seconds=execution.first_response_seconds,
                        first_parseable_candidate_seconds=(
                            execution.first_parseable_candidate_seconds
                        ),
                        final_result_seconds=execution.final_result_seconds,
                        fusion_seconds=execution.fusion_seconds,
                    )
                )
        except BaseException as error:
            run_failure = error

        cleanup_failed = False
        cleanup_cancelled = False
        for cleanup_key, handle in reversed(handles.items()):
            provider_name = cleanup_key[0]
            provider = self._providers[provider_name]
            cleanup_started = time.perf_counter()
            outcome, was_cancelled = await _cleanup_handle(provider, handle)
            cleanup_cancelled = cleanup_cancelled or was_cancelled
            record = lifecycle[cleanup_key]
            record.cleanup_seconds = time.perf_counter() - cleanup_started
            record.cleanup_outcome = outcome
            if outcome == CleanupOutcome.FAILED:
                cleanup_failed = True
        self.lifecycle_records = list(lifecycle.values())
        if run_failure is not None:
            if cleanup_failed:
                if isinstance(run_failure, GateFailure):
                    run_failure.cleanup_failed = True
                else:
                    run_failure.add_note("cleanup_failed")
            raise run_failure
        if cleanup_cancelled:
            cancellation = asyncio.CancelledError()
            if cleanup_failed:
                cancellation.add_note("cleanup_failed")
            raise cancellation
        if cleanup_failed:
            raise GateFailure(
                PreflightStage.FULL_SAMPLE,
                "cleanup_error",
                retryable=False,
                cleanup_failed=True,
            )
        return results


class _ProviderRouteAdapter:
    def __init__(self, provider: BenchmarkProvider) -> None:
        self._provider = provider

    async def execute(
        self,
        handles: Mapping[str, MediaHandle],
        *,
        run_index: int,
    ) -> RouteExecution:
        del run_index
        handle = next(iter(handles.values()))
        await self._provider.probe(handle)
        return RouteExecution(candidates=[])


def _failed_run_result(
    unit: RunUnit,
    *,
    status: BenchmarkRunStatus,
    error_code: str,
    elapsed: float,
    upload_seconds: float,
) -> BenchmarkRunResult:
    return BenchmarkRunResult(
        sample_id=unit.sample_id,
        route_id=unit.route_id,
        run_index=unit.run_index,
        status=status,
        error_code=error_code,
        upload_seconds=upload_seconds,
        inference_seconds=elapsed,
    )


async def _cleanup_handle(
    provider: BenchmarkProvider,
    handle: MediaHandle,
) -> tuple[CleanupOutcome, bool]:
    async def cleanup_and_verify() -> CleanupOutcome:
        try:
            async with asyncio.timeout(30):
                await provider.cleanup(handle)
                return await provider.verify_cleanup(handle)
        except (Exception, TimeoutError):
            return CleanupOutcome.FAILED

    task = asyncio.create_task(cleanup_and_verify())
    try:
        return await asyncio.shield(task), False
    except asyncio.CancelledError:
        outcome = await asyncio.shield(task)
        return outcome, True
