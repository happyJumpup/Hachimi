import asyncio
import json
import secrets
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse

from hakimi_analysis.access import (
    ACCESS_COOKIE_NAME,
    ACCESS_SESSION_SECONDS,
    AccessCodeRateLimited,
    AccessManager,
    AccessSession,
    AdmissionDenied,
)
from hakimi_analysis.models import (
    AccessSessionView,
    AnalysisRunView,
    CreateAnalysisRunRequest,
    SourceSummary,
    UpgradeAccessSessionRequest,
)
from hakimi_analysis.pipeline import AnalysisPipeline
from hakimi_analysis.readiness import ReadinessProbe, StaticReadiness
from hakimi_analysis.runs import TERMINAL_STATUSES, AnalysisRunManager, as_pipeline
from hakimi_analysis.sources import EmptySourceCatalog, SourceCatalog


async def stream_run_events(
    manager: AnalysisRunManager,
    run_id: str,
    is_disconnected: Callable[[], Awaitable[bool]],
) -> AsyncGenerator[str, None]:
    try:
        async for event in manager.events(run_id):
            if await is_disconnected():
                return
            data = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
            yield f"event: {event.type}\ndata: {data}\n\n"
    finally:
        view: AnalysisRunView | None
        try:
            view = manager.get(run_id)
        except KeyError:
            view = None
        if view is not None and view.status not in TERMINAL_STATUSES:
            with suppress(KeyError):
                await asyncio.shield(manager.cancel(run_id))


def create_app(
    *,
    catalog: SourceCatalog | None = None,
    pipeline: AnalysisPipeline | object | None = None,
    timeout_seconds: float = 180,
    ttl_seconds: float = 600,
    cors_origins: list[str] | None = None,
    close_callbacks: list[Callable[[], Awaitable[None]]] | None = None,
    access: AccessManager | None = None,
    app_env: str = "test",
    readiness: ReadinessProbe | None = None,
    web_static_root: Path | None = None,
    trusted_proxy_cidrs: list[str] | None = None,
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
    access_manager = access or AccessManager(
        cookie_secret=secrets.token_urlsafe(32),
        judge_access_code=secrets.token_urlsafe(16),
    )
    readiness_probe = readiness or StaticReadiness(
        "provider_configuration_invalid" if app_env == "production" else None
    )
    resolved_cors_origins = cors_origins or ["http://localhost:5173"]
    trusted_proxy_networks = tuple(
        ip_network(value, strict=False) for value in trusted_proxy_cidrs or []
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
    app.state.access_manager = access_manager
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/ready")
    async def ready() -> JSONResponse:
        failure_code = await asyncio.to_thread(readiness_probe.check)
        if failure_code is not None:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "code": failure_code},
            )
        return JSONResponse(status_code=200, content={"status": "ready"})

    @app.get("/api/v1/access/session", response_model=AccessSessionView)
    async def get_access_session(request: Request, response: Response) -> AccessSessionView:
        session = access_manager.resolve(request.cookies.get(ACCESS_COOKIE_NAME))
        _set_access_cookie(response, access_manager, session)
        response.headers["Cache-Control"] = "no-store"
        return await access_view(session)

    @app.post("/api/v1/access/session", response_model=AccessSessionView)
    async def upgrade_access_session(
        payload: UpgradeAccessSessionRequest,
        request: Request,
        response: Response,
    ) -> AccessSessionView:
        _require_same_origin(
            request,
            app_env=app_env,
            development_origins=resolved_cors_origins,
        )
        session = access_manager.resolve(request.cookies.get(ACCESS_COOKIE_NAME))
        try:
            upgraded = await access_manager.upgrade(
                session,
                payload.access_code,
                client_ip=_client_ip(request, trusted_proxy_networks),
            )
        except AccessCodeRateLimited as error:
            raise HTTPException(
                status_code=429,
                detail="体验码尝试过于频繁，请稍后重试",
                headers={
                    "Cache-Control": "no-store",
                    "Retry-After": str(error.retry_after_seconds),
                },
            ) from error
        if upgraded is None:
            raise HTTPException(
                status_code=401,
                detail="体验码无效",
                headers={"Cache-Control": "no-store"},
            )
        _set_access_cookie(response, access_manager, upgraded)
        response.headers["Cache-Control"] = "no-store"
        return await access_view(upgraded)

    async def access_view(session: AccessSession) -> AccessSessionView:
        view = access_manager.view(session)
        if app_env != "production":
            return view
        failure_code = await asyncio.to_thread(readiness_probe.check)
        if failure_code is None:
            return view
        return view.model_copy(update={"can_analyze": False, "retry_after_seconds": None})

    @app.get("/api/v1/sources", response_model=list[SourceSummary])
    async def list_sources() -> list[SourceSummary]:
        return source_catalog.list()

    @app.get("/api/v1/sources/{source_id}/media")
    async def source_media(source_id: str) -> Response:
        try:
            source = source_catalog.get(source_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="受控视频源不存在") from error
        if source.public_media_url is not None:
            return RedirectResponse(source.public_media_url, status_code=307)
        return FileResponse(source.path, media_type="video/mp4")

    @app.post(
        "/api/v1/analysis-runs",
        response_model=AnalysisRunView,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_run(
        payload: CreateAnalysisRunRequest,
        request: Request,
        response: Response,
    ) -> AnalysisRunView:
        _require_same_origin(
            request,
            app_env=app_env,
            development_origins=resolved_cors_origins,
        )
        if app_env == "production":
            failure_code = await asyncio.to_thread(readiness_probe.check)
            if failure_code is not None:
                raise HTTPException(status_code=503, detail="动作分析服务尚未就绪")
        try:
            source = source_catalog.get(payload.source_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="受控视频源不存在") from error
        if payload.trigger_seconds > source.duration_seconds:
            raise HTTPException(
                status_code=422,
                detail="trigger_seconds must be inside the source video",
            )
        session = access_manager.resolve(request.cookies.get(ACCESS_COOKIE_NAME))
        active = await access_manager.active_run(session.id)
        if active is not None and active.source_id != source.id and active.run_id is not None:
            with suppress(KeyError):
                await manager.cancel(active.run_id)
        try:
            lease = await access_manager.reserve(
                session,
                source_id=source.id,
                client_ip=_client_ip(request, trusted_proxy_networks),
            )
        except AdmissionDenied as error:
            raise HTTPException(
                status_code=429,
                detail="真实动作分析暂时繁忙，请稍后重试",
                headers={"Retry-After": str(error.retry_after_seconds)},
            ) from error
        try:
            created = await manager.create(
                source,
                payload.trigger_seconds,
                on_terminal=lease.release,
            )
        except ValueError as error:
            await lease.release()
            raise HTTPException(status_code=422, detail=str(error)) from error
        except Exception:
            await lease.release()
            raise
        await lease.bind(created.id)
        _set_access_cookie(response, access_manager, session)
        return created

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
    async def cancel_run(run_id: str, request: Request) -> AnalysisRunView:
        _require_same_origin(
            request,
            app_env=app_env,
            development_origins=resolved_cors_origins,
        )
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

        return StreamingResponse(
            stream_run_events(manager, run_id, request.is_disconnected),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    resolved_web_root = _usable_web_root(web_static_root)
    if resolved_web_root is not None:
        index_path = resolved_web_root / "index.html"

        @app.get("/{spa_path:path}", include_in_schema=False)
        async def web_spa(spa_path: str) -> FileResponse:
            if spa_path == "api" or spa_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="页面不存在")
            candidate = (resolved_web_root / spa_path).resolve()
            try:
                candidate.relative_to(resolved_web_root)
            except ValueError as error:
                raise HTTPException(status_code=404, detail="页面不存在") from error
            if candidate.is_file():
                return FileResponse(candidate)
            if spa_path == "assets" or spa_path.startswith("assets/"):
                raise HTTPException(status_code=404, detail="页面不存在")
            return FileResponse(index_path)

    return app


def _usable_web_root(web_static_root: Path | None) -> Path | None:
    if web_static_root is None:
        return None
    resolved = web_static_root.expanduser().resolve()
    if not resolved.is_dir() or not (resolved / "index.html").is_file():
        return None
    return resolved


def _set_access_cookie(
    response: Response,
    access_manager: AccessManager,
    session: AccessSession,
) -> None:
    response.set_cookie(
        key=ACCESS_COOKIE_NAME,
        value=access_manager.encode(session),
        max_age=ACCESS_SESSION_SECONDS,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )


def _require_same_origin(
    request: Request,
    *,
    app_env: str,
    development_origins: list[str],
) -> None:
    origin = request.headers.get("origin")
    if origin is None:
        if app_env == "production":
            raise HTTPException(status_code=403, detail="请求来源无效")
        return
    expected_host = request.headers.get("host", "").lower()
    parsed = urlparse(origin)
    is_same_host = parsed.scheme in {"http", "https"} and parsed.netloc.lower() == expected_host
    is_allowed_development_origin = app_env != "production" and origin in development_origins
    if not is_same_host and not is_allowed_development_origin:
        raise HTTPException(status_code=403, detail="请求来源无效")


def _client_ip(
    request: Request,
    trusted_proxy_networks: tuple[IPv4Network | IPv6Network, ...],
) -> str:
    peer_value = request.client.host if request.client is not None else ""
    try:
        peer_ip = ip_address(peer_value)
    except ValueError:
        return "unknown"
    if not any(peer_ip in network for network in trusted_proxy_networks):
        return str(peer_ip)

    forwarded_for = request.headers.get("x-forwarded-for")
    real_ip = request.headers.get("x-real-ip")
    forwarded_values = [value.strip() for value in (forwarded_for, real_ip) if value]
    if not forwarded_values or any("," in value for value in forwarded_values):
        raise HTTPException(status_code=400, detail="客户端地址无效")
    if len(set(forwarded_values)) != 1:
        raise HTTPException(status_code=400, detail="客户端地址无效")
    try:
        return str(ip_address(forwarded_values[0]))
    except ValueError as error:
        raise HTTPException(status_code=400, detail="客户端地址无效") from error
