import asyncio
import json
import math
import re
import secrets
import shutil
import tempfile
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from fastapi import (
    APIRouter,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
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
from hakimi_analysis.media import probe_duration_sync
from hakimi_analysis.models import (
    AccessSessionView,
    AnalysisRunView,
    ApiErrorResponse,
    CapabilitiesView,
    CreateAnalysisRunRequest,
    NotReadyResponse,
    ReadyResponse,
    SourceSummary,
    UpgradeAccessSessionRequest,
)
from hakimi_analysis.pipeline import AnalysisPipeline
from hakimi_analysis.readiness import ReadinessProbe, StaticReadiness
from hakimi_analysis.runs import AnalysisRunManager, as_pipeline
from hakimi_analysis.sources import EmptySourceCatalog, SourceCatalog, VideoSource

LOCAL_SOURCE_ID_PATTERN = re.compile(
    r"^local:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
LOCAL_MEDIA_TYPES = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
}


async def stream_run_events(
    manager: AnalysisRunManager,
    run_id: str,
    is_disconnected: Callable[[], Awaitable[bool]],
    *,
    owner_session_id: str | None = None,
) -> AsyncGenerator[str, None]:
    async for event in manager.events(run_id, owner_session_id=owner_session_id):
        if await is_disconnected():
            return
        data = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
        yield f"event: {event.type}\ndata: {data}\n\n"


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
    local_upload_enabled: bool = True,
    local_analysis_max_seconds: float = 60,
    local_upload_max_bytes: int = 256 * 1024 * 1024,
    local_upload_temp_root: Path | None = None,
    local_duration_probe: Callable[[Path], float] | None = None,
) -> FastAPI:
    if (
        not math.isfinite(local_analysis_max_seconds)
        or local_analysis_max_seconds <= 0
        or local_analysis_max_seconds > 600
    ):
        raise ValueError("local analysis limit must be between 0 and 600 seconds")
    if local_upload_max_bytes < 1:
        raise ValueError("local upload byte limit must be positive")
    duration_probe = local_duration_probe or probe_duration_sync
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
    if cors_origins is None:
        resolved_cors_origins = [] if app_env == "production" else ["http://localhost:5173"]
    else:
        resolved_cors_origins = cors_origins
    if app_env == "production":
        _validate_production_cors_origins(resolved_cors_origins)
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

    @app.get("/api/v1/capabilities", response_model=CapabilitiesView)
    async def capabilities(response: Response) -> CapabilitiesView:
        response.headers["Cache-Control"] = "no-store"
        return CapabilitiesView(
            local_upload_enabled=local_upload_enabled,
            local_analysis_max_seconds=local_analysis_max_seconds,
            local_upload_max_bytes=local_upload_max_bytes,
        )

    @app.get(
        "/api/v1/ready",
        response_model=ReadyResponse,
        responses={
            503: {
                "model": NotReadyResponse,
                "description": "生产就绪检查未通过，返回脱敏错误码。",
            }
        },
    )
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
        responses={
            400: {
                "model": ApiErrorResponse,
                "description": "可信代理提供的客户端地址无效。",
            },
            403: {
                "model": ApiErrorResponse,
                "description": "请求未通过同源校验。",
            },
            404: {
                "model": ApiErrorResponse,
                "description": "受控视频源不存在。",
            },
            429: {
                "model": ApiErrorResponse,
                "description": "分析容量或调用频率已达到限制。",
                "headers": {
                    "Retry-After": {
                        "description": "再次尝试前需要等待的秒数。",
                        "schema": {"type": "string"},
                    }
                },
            },
            503: {
                "model": ApiErrorResponse,
                "description": "生产分析服务尚未就绪。",
            },
        },
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
        session = access_manager.resolve(request.cookies.get(ACCESS_COOKIE_NAME))
        active = await access_manager.active_run(session.id)
        if active is not None and active.source_id != source.id and active.run_id is not None:
            with suppress(KeyError):
                await manager.cancel(active.run_id, owner_session_id=session.id)
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
                owner_session_id=session.id,
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

    local_upload_routes = app if local_upload_enabled else APIRouter()

    @local_upload_routes.post(
        "/api/v1/analysis-runs/local",
        response_model=AnalysisRunView,
        status_code=status.HTTP_202_ACCEPTED,
        responses={
            400: {
                "model": ApiErrorResponse,
                "description": "可信代理提供的客户端地址无效。",
            },
            403: {
                "model": ApiErrorResponse,
                "description": "请求未通过同源校验。",
            },
            404: {"model": ApiErrorResponse, "description": "本地视频导入未启用。"},
            413: {"model": ApiErrorResponse, "description": "上传媒体超过大小限制。"},
            415: {"model": ApiErrorResponse, "description": "上传媒体类型不受支持。"},
            422: {"model": ApiErrorResponse, "description": "媒体或分析范围无效。"},
            429: {
                "model": ApiErrorResponse,
                "description": "分析容量或调用频率已达到限制。",
                "headers": {
                    "Retry-After": {
                        "description": "再次尝试前需要等待的秒数。",
                        "schema": {"type": "string"},
                    }
                },
            },
            503: {"model": ApiErrorResponse, "description": "生产分析服务尚未就绪。"},
        },
    )
    async def create_local_run(
        request: Request,
        response: Response,
        media: Annotated[UploadFile, File()],
        local_source_id: Annotated[str, Form()],
        range_start_seconds: Annotated[float | None, Form(ge=0)] = None,
        range_end_seconds: Annotated[float | None, Form(gt=0)] = None,
    ) -> AnalysisRunView:
        _require_same_origin(
            request,
            app_env=app_env,
            development_origins=resolved_cors_origins,
        )
        if not local_upload_enabled:
            raise HTTPException(status_code=404, detail="本地视频导入未启用")
        if app_env == "production":
            failure_code = await asyncio.to_thread(readiness_probe.check)
            if failure_code is not None:
                raise HTTPException(status_code=503, detail="动作分析服务尚未就绪")
        if LOCAL_SOURCE_ID_PATTERN.fullmatch(local_source_id) is None:
            raise HTTPException(status_code=422, detail="本地来源标识无效")
        if (range_start_seconds is None) != (range_end_seconds is None):
            await media.close()
            raise HTTPException(
                status_code=422,
                detail="分析范围必须同时包含开始和结束时间",
            )
        suffix = LOCAL_MEDIA_TYPES.get(media.content_type or "")
        if suffix is None:
            await media.close()
            raise HTTPException(status_code=415, detail="暂不支持这种视频格式")

        upload_directory = await asyncio.to_thread(_make_upload_directory, local_upload_temp_root)
        source_path = upload_directory / f"source{suffix}"
        try:
            await _write_upload(media, source_path, max_bytes=local_upload_max_bytes)
            try:
                duration_seconds = await asyncio.to_thread(duration_probe, source_path)
            except Exception as error:
                raise HTTPException(status_code=422, detail="无法读取视频时长") from error
            if not math.isfinite(duration_seconds) or duration_seconds <= 0:
                raise HTTPException(status_code=422, detail="无法读取视频时长")
            if duration_seconds > local_analysis_max_seconds:
                raise HTTPException(status_code=422, detail="视频时长超过当前分析上限")

            analysis_start = 0.0 if range_start_seconds is None else range_start_seconds
            analysis_end = duration_seconds if range_end_seconds is None else range_end_seconds
            if (
                not math.isfinite(analysis_start)
                or not math.isfinite(analysis_end)
                or analysis_start >= analysis_end
                or analysis_end > duration_seconds
            ):
                raise HTTPException(status_code=422, detail="分析范围超出视频时长")
            source = VideoSource(
                id=local_source_id,
                title="本地导入视频",
                path=source_path,
                duration_seconds=duration_seconds,
                analysis_start_seconds=analysis_start,
                analysis_end_seconds=analysis_end,
            )

            session = access_manager.resolve(request.cookies.get(ACCESS_COOKIE_NAME))
            active = await access_manager.active_run(session.id)
            if active is not None and active.source_id != source.id and active.run_id is not None:
                with suppress(KeyError):
                    await manager.cancel(active.run_id, owner_session_id=session.id)
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

            async def finalize_local_run() -> None:
                try:
                    await _remove_upload_directory(upload_directory)
                finally:
                    await lease.release()

            try:
                created = await manager.create(
                    source,
                    None,
                    owner_session_id=session.id,
                    on_terminal=finalize_local_run,
                )
            except Exception:
                await lease.release()
                raise
            await lease.bind(created.id)
            _set_access_cookie(response, access_manager, session)
            return created
        except Exception:
            await _remove_upload_directory(upload_directory)
            raise
        finally:
            await media.close()

    if not local_upload_enabled:

        @app.post("/api/v1/analysis-runs/local", include_in_schema=False)
        async def reject_disabled_local_upload() -> None:
            raise HTTPException(status_code=404, detail="本地视频导入未启用")

    @app.get(
        "/api/v1/analysis-runs/{run_id}",
        response_model=AnalysisRunView,
        responses={
            404: {
                "model": ApiErrorResponse,
                "description": "分析请求不存在、已过期或不属于当前会话。",
            }
        },
    )
    async def get_run(run_id: str, request: Request) -> AnalysisRunView:
        session = access_manager.resolve(request.cookies.get(ACCESS_COOKIE_NAME))
        try:
            return manager.get(run_id, owner_session_id=session.id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="分析请求不存在或已过期") from error

    @app.delete(
        "/api/v1/analysis-runs/{run_id}",
        response_model=AnalysisRunView,
        status_code=status.HTTP_202_ACCEPTED,
        responses={
            403: {
                "model": ApiErrorResponse,
                "description": "请求未通过同源校验。",
            },
            404: {
                "model": ApiErrorResponse,
                "description": "分析请求不存在、已过期或不属于当前会话。",
            },
        },
    )
    async def cancel_run(run_id: str, request: Request) -> AnalysisRunView:
        _require_same_origin(
            request,
            app_env=app_env,
            development_origins=resolved_cors_origins,
        )
        session = access_manager.resolve(request.cookies.get(ACCESS_COOKIE_NAME))
        try:
            return await manager.cancel(run_id, owner_session_id=session.id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="分析请求不存在或已过期") from error

    @app.get(
        "/api/v1/analysis-runs/{run_id}/events",
        response_class=StreamingResponse,
        responses={
            200: {
                "description": "分析阶段与终态事件流。",
                "content": {
                    "text/event-stream": {
                        "schema": {"type": "string"},
                    }
                },
            },
            404: {
                "model": ApiErrorResponse,
                "description": "分析请求不存在、已过期或不属于当前会话。",
            },
        },
    )
    async def run_events(run_id: str, request: Request) -> StreamingResponse:
        session = access_manager.resolve(request.cookies.get(ACCESS_COOKIE_NAME))
        try:
            manager.get(run_id, owner_session_id=session.id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="分析请求不存在或已过期") from error

        return StreamingResponse(
            stream_run_events(
                manager,
                run_id,
                request.is_disconnected,
                owner_session_id=session.id,
            ),
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


def _make_upload_directory(root: Path | None) -> Path:
    if root is not None:
        root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="hachimi-local-run-", dir=str(root) if root else None))


async def _write_upload(media: UploadFile, destination: Path, *, max_bytes: int) -> None:
    written = 0
    try:
        with destination.open("xb") as output:
            while chunk := await media.read(1024 * 1024):
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(status_code=413, detail="视频文件超过上传大小限制")
                output.write(chunk)
    except OSError as error:
        raise HTTPException(status_code=422, detail="无法接收视频文件") from error
    if written == 0:
        raise HTTPException(status_code=422, detail="视频文件为空")


async def _remove_upload_directory(directory: Path) -> None:
    await asyncio.to_thread(shutil.rmtree, directory, True)


def _validate_production_cors_origins(origins: list[str]) -> None:
    for origin in origins:
        parsed = urlparse(origin)
        try:
            _ = parsed.port
        except ValueError as error:
            raise ValueError("production CORS origin must be an exact HTTPS origin") from error
        if (
            origin != origin.strip()
            or parsed.scheme != "https"
            or not parsed.netloc
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or "*" in parsed.netloc
            or parsed.path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("production CORS origin must be an exact HTTPS origin")


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
