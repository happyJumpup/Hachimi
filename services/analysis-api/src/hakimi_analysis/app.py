import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from hakimi_analysis.models import AnalysisRunView, CreateAnalysisRunRequest, SourceSummary
from hakimi_analysis.pipeline import AnalysisPipeline
from hakimi_analysis.runs import TERMINAL_STATUSES, AnalysisRunManager, as_pipeline
from hakimi_analysis.sources import EmptySourceCatalog, SourceCatalog


def create_app(
    *,
    catalog: SourceCatalog | None = None,
    pipeline: AnalysisPipeline | object | None = None,
    timeout_seconds: float = 180,
    ttl_seconds: float = 600,
    cors_origins: list[str] | None = None,
    close_callbacks: list[Callable[[], Awaitable[None]]] | None = None,
) -> FastAPI:
    source_catalog = catalog or EmptySourceCatalog()
    resolved_pipeline: AnalysisPipeline
    if pipeline is None:
        from hakimi_analysis.bootstrap import UnconfiguredPipeline

        resolved_pipeline = UnconfiguredPipeline()
    else:
        resolved_pipeline = as_pipeline(pipeline)
    manager = AnalysisRunManager(
        resolved_pipeline,
        timeout_seconds=timeout_seconds,
        ttl_seconds=ttl_seconds,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await manager.close()
        for callback in close_callbacks or []:
            await callback()

    app = FastAPI(
        title="Hachimi Action Analysis API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.run_manager = manager
    app.state.source_catalog = source_catalog
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/sources", response_model=list[SourceSummary])
    async def list_sources() -> list[SourceSummary]:
        return source_catalog.list()

    @app.get("/api/v1/sources/{source_id}/media", response_class=FileResponse)
    async def source_media(source_id: str) -> FileResponse:
        try:
            source = source_catalog.get(source_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="受控视频源不存在") from error
        return FileResponse(source.path, media_type="video/mp4")

    @app.post(
        "/api/v1/analysis-runs",
        response_model=AnalysisRunView,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_run(payload: CreateAnalysisRunRequest) -> AnalysisRunView:
        try:
            source = source_catalog.get(payload.source_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="受控视频源不存在") from error
        try:
            return await manager.create(source, payload.trigger_seconds)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/v1/analysis-runs/{run_id}", response_model=AnalysisRunView)
    async def get_run(run_id: str) -> AnalysisRunView:
        try:
            return manager.get(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="分析请求不存在或已过期") from error

    @app.delete(
        "/api/v1/analysis-runs/{run_id}",
        response_model=AnalysisRunView,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def cancel_run(run_id: str) -> AnalysisRunView:
        try:
            return await manager.cancel(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="分析请求不存在或已过期") from error

    @app.get("/api/v1/analysis-runs/{run_id}/events")
    async def run_events(run_id: str, request: Request) -> StreamingResponse:
        try:
            manager.get(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="分析请求不存在或已过期") from error

        async def stream() -> AsyncIterator[str]:
            disconnected = False
            async for event in manager.events(run_id):
                if await request.is_disconnected():
                    disconnected = True
                    break
                data = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
                yield f"event: {event.type}\ndata: {data}\n\n"
            if disconnected:
                try:
                    view = manager.get(run_id)
                except KeyError:
                    return
                if view.status not in TERMINAL_STATUSES:
                    await manager.cancel(run_id)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app
