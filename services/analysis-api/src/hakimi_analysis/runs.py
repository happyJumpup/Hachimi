import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

from hakimi_analysis.models import (
    AnalysisError,
    AnalysisEvent,
    AnalysisRunView,
    ErrorCode,
    RunStage,
    RunStatus,
)
from hakimi_analysis.observability import log_safe_fields
from hakimi_analysis.pipeline import AnalysisPipeline, PipelineFailure
from hakimi_analysis.sources import VideoSource

TERMINAL_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
LOGGER = logging.getLogger("hakimi_analysis.runs")


@dataclass(slots=True)
class RunRecord:
    view: AnalysisRunView
    events: list[AnalysisEvent] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task[None] | None = None
    terminal_at: datetime | None = None
    started_monotonic: float = field(default_factory=time.perf_counter)
    on_terminal: Callable[[], Awaitable[None]] | None = None
    terminal_notified: bool = False


class AnalysisRunManager:
    def __init__(
        self,
        pipeline: AnalysisPipeline,
        *,
        timeout_seconds: float = 180,
        ttl_seconds: float = 600,
    ) -> None:
        self._pipeline = pipeline
        self._timeout_seconds = timeout_seconds
        self._ttl = timedelta(seconds=ttl_seconds)
        self._records: dict[str, RunRecord] = {}

    async def create(
        self,
        source: VideoSource,
        trigger_seconds: float,
        *,
        on_terminal: Callable[[], Awaitable[None]] | None = None,
    ) -> AnalysisRunView:
        self._prune()
        if trigger_seconds > source.duration_seconds:
            raise ValueError("trigger_seconds must be inside the source video")
        run_id = str(uuid4())
        record = RunRecord(
            view=AnalysisRunView(
                id=run_id,
                source_id=source.id,
                trigger_seconds=trigger_seconds,
                status=RunStatus.QUEUED,
                stage=RunStage.QUEUED,
            ),
            on_terminal=on_terminal,
        )
        self._records[run_id] = record
        record.task = asyncio.create_task(self._execute(record, source))
        return record.view.model_copy(deep=True)

    def get(self, run_id: str) -> AnalysisRunView:
        self._prune()
        return self._record(run_id).view.model_copy(deep=True)

    async def cancel(self, run_id: str) -> AnalysisRunView:
        record = self._record(run_id)
        if record.view.status in TERMINAL_STATUSES:
            return record.view.model_copy(deep=True)
        if record.task is not None:
            record.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await record.task
        if record.view.status not in TERMINAL_STATUSES:
            record.view.status = RunStatus.CANCELLED
            record.view.stage = RunStage.CANCELLED
            record.terminal_at = datetime.now(UTC)
            record.view.updated_at = record.terminal_at
            await self._event(record, "run.cancelled", {"stage": RunStage.CANCELLED.value})
        await self._notify_terminal(record)
        return record.view.model_copy(deep=True)

    async def events(self, run_id: str) -> AsyncIterator[AnalysisEvent]:
        record = self._record(run_id)
        index = 0
        while True:
            while index < len(record.events):
                event = record.events[index]
                index += 1
                yield event
            if record.view.status in TERMINAL_STATUSES:
                return
            async with record.condition:
                try:
                    await asyncio.wait_for(record.condition.wait(), timeout=15)
                except TimeoutError:
                    yield AnalysisEvent(
                        sequence=index,
                        type="heartbeat",
                        run_id=run_id,
                    )

    async def _execute(self, record: RunRecord, source: VideoSource) -> None:
        record.view.status = RunStatus.RUNNING
        record.view.stage = RunStage.PREPARING_MEDIA
        await self._event(record, "run.started", {"stage": RunStage.PREPARING_MEDIA.value})

        async def emit(
            stage: RunStage,
            event_type: str,
            data: dict[str, object],
        ) -> None:
            record.view.stage = stage
            record.view.updated_at = datetime.now(UTC)
            await self._event(record, event_type, {"stage": stage.value, **data})

        try:
            async with asyncio.timeout(self._timeout_seconds):
                output = await self._pipeline.analyze(
                    source,
                    record.view.trigger_seconds,
                    emit,
                )
            record.view.status = RunStatus.COMPLETED
            record.view.stage = RunStage.COMPLETED
            record.view.candidates = output.candidates
            record.view.warnings = output.warnings
            record.view.empty_reason = output.empty_reason
            await self._event(
                record,
                "run.completed",
                {
                    "stage": RunStage.COMPLETED.value,
                    "candidate_count": len(output.candidates),
                },
            )
        except asyncio.CancelledError:
            record.view.status = RunStatus.CANCELLED
            record.view.stage = RunStage.CANCELLED
            await self._event(record, "run.cancelled", {"stage": RunStage.CANCELLED.value})
            raise
        except TimeoutError:
            await self._fail(
                record,
                ErrorCode.TIMEOUT,
                "动作分析超时，请重试",
                retryable=True,
            )
        except PipelineFailure as error:
            code = ErrorCode(error.code)
            await self._fail(record, code, str(error), retryable=error.retryable)
        except Exception:
            await self._fail(
                record,
                ErrorCode.PROVIDER_ERROR,
                "动作分析暂时不可用，请稍后重试",
                retryable=True,
            )
        finally:
            if record.view.status in TERMINAL_STATUSES:
                record.terminal_at = datetime.now(UTC)
                record.view.updated_at = record.terminal_at
                async with record.condition:
                    record.condition.notify_all()
                await self._notify_terminal(record)

    async def _fail(
        self,
        record: RunRecord,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool,
    ) -> None:
        record.view.status = RunStatus.FAILED
        record.view.stage = RunStage.FAILED
        record.view.error = AnalysisError(code=code, message=message, retryable=retryable)
        await self._event(
            record,
            "run.failed",
            {"stage": RunStage.FAILED.value, "error_code": code.value},
        )

    async def _event(
        self,
        record: RunRecord,
        event_type: str,
        data: dict[str, object],
    ) -> None:
        event = AnalysisEvent(
            sequence=len(record.events) + 1,
            type=event_type,
            run_id=record.view.id,
            data=data,
        )
        record.events.append(event)
        log_safe_fields(
            LOGGER,
            run_id=record.view.id,
            source_id=record.view.source_id,
            stage=record.view.stage.value,
            elapsed_ms=round((time.perf_counter() - record.started_monotonic) * 1000),
            model_version=data.get("model_version"),
            skill_version=data.get("skill_version"),
            provider_request_id=data.get("provider_request_id"),
            error_code=data.get("error_code"),
        )
        async with record.condition:
            record.condition.notify_all()

    def _record(self, run_id: str) -> RunRecord:
        try:
            return self._records[run_id]
        except KeyError as error:
            raise KeyError(f"unknown run_id: {run_id}") from error

    def _prune(self) -> None:
        cutoff = datetime.now(UTC) - self._ttl
        expired = [
            run_id
            for run_id, record in self._records.items()
            if record.terminal_at is not None and record.terminal_at < cutoff
        ]
        for run_id in expired:
            record = self._records.pop(run_id)
            if record.task is not None and not record.task.done():
                record.task.cancel()

    async def close(self) -> None:
        tasks = [record.task for record in self._records.values() if record.task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for record in self._records.values():
            await self._notify_terminal(record)
        self._records.clear()

    async def _notify_terminal(self, record: RunRecord) -> None:
        if record.terminal_notified:
            return
        record.terminal_notified = True
        if record.on_terminal is not None:
            await record.on_terminal()


def as_pipeline(value: object) -> AnalysisPipeline:
    return cast(AnalysisPipeline, value)
