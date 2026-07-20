import secrets
from pathlib import Path

import httpx
from fastapi import FastAPI

from hakimi_analysis.access import AccessManager
from hakimi_analysis.media import LocalMediaProcessor, probe_duration_sync
from hakimi_analysis.models import (
    AnalysisCandidate,
    CandidateParameters,
    EvidenceSpan,
    EvidenceType,
    RunStage,
    Segment,
)
from hakimi_analysis.orchestration import OrchestratedAnalysisPipeline, SkillRepository
from hakimi_analysis.pipeline import AnalysisPipeline, EmitCallback, PipelineFailure, PipelineOutput
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.providers.asr import VolcAsrClient
from hakimi_analysis.readiness import ProductionReadiness
from hakimi_analysis.settings import PROJECT_ROOT, Settings
from hakimi_analysis.sources import (
    EmptySourceCatalog,
    SourceCatalog,
    SourceManifestError,
    VideoSource,
)


class UnconfiguredPipeline:
    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float,
        emit: EmitCallback,
    ) -> PipelineOutput:
        raise PipelineFailure(
            "configuration_error",
            "动作分析服务尚未配置",
            retryable=False,
        )


class DeterministicTestPipeline:
    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float,
        emit: EmitCallback,
    ) -> PipelineOutput:
        await emit(RunStage.ANALYZING_EVIDENCE, "stage.changed", {})
        start = max(0, trigger_seconds - 4)
        end = min(source.duration_seconds, trigger_seconds + 6)
        await emit(RunStage.FUSING_CANDIDATES, "stage.changed", {})
        return PipelineOutput(
            candidates=[
                AnalysisCandidate(
                    id="candidate-1",
                    name="拖拽弯举",
                    source_id=source.id,
                    segment=Segment(start_seconds=start, end_seconds=end),
                    parameters=CandidateParameters(),
                    evidence=[
                        EvidenceSpan(
                            type=EvidenceType.SPEECH,
                            start_seconds=start,
                            end_seconds=end,
                        ),
                        EvidenceSpan(
                            type=EvidenceType.VISUAL,
                            start_seconds=start,
                            end_seconds=end,
                        ),
                    ],
                    needs_confirmation=False,
                )
            ]
        )


def build_catalog(settings: Settings) -> SourceCatalog:
    if (
        settings.source_manifest_path is not None
        and settings.source_media_root is not None
        and settings.public_media_base_url is not None
    ):
        try:
            return SourceCatalog.from_manifest(
                manifest_path=settings.source_manifest_path,
                media_root=settings.source_media_root,
                public_media_base_url=settings.public_media_base_url,
                duration_probe=probe_duration_sync,
            )
        except SourceManifestError:
            return EmptySourceCatalog()
    if settings.app_env == "production":
        # Production media is allowlisted only by the versioned manifest. A legacy
        # developer path must never become public when deployment configuration is
        # missing or incomplete.
        return EmptySourceCatalog()
    path = settings.hakimi_demo_video_path
    if path is None:
        return EmptySourceCatalog()
    resolved_path = path.expanduser().resolve()
    duration = probe_duration_sync(resolved_path)
    sources = [
        VideoSource(
            id="legacy-arm-workout",
            title="哈基米手臂训练｜本地来源视频",
            path=resolved_path,
            duration_seconds=duration,
        )
    ]
    if settings.app_env == "test":
        sources.append(
            VideoSource(
                id="legacy-arm-workout-alt",
                title="哈基米手臂训练 B｜合成测试来源",
                path=resolved_path,
                duration_seconds=duration,
            )
        )
    return SourceCatalog(sources)


def build_pipeline(
    settings: Settings,
    http_client: httpx.AsyncClient,
    *,
    temp_root: Path | None = None,
) -> AnalysisPipeline:
    if settings.analysis_provider == "test":
        return DeterministicTestPipeline()
    if settings.ark_api_key is None or settings.volc_asr_api_key is None:
        return UnconfiguredPipeline()

    media = LocalMediaProcessor(temp_root=temp_root or PROJECT_ROOT / "tmp" / "analysis-runs")
    asr = VolcAsrClient(
        api_key=settings.volc_asr_api_key.get_secret_value(),
        resource_id=settings.volc_asr_resource_id,
        url=settings.volc_asr_url,
    )
    ark = ArkResponsesClient(
        api_key=settings.ark_api_key.get_secret_value(),
        model_id=settings.ark_model_id,
        base_url=settings.ark_base_url,
        http_client=http_client,
    )
    try:
        skills = SkillRepository.load(PROJECT_ROOT / "skills")
    except (OSError, ValueError):
        return UnconfiguredPipeline()
    return OrchestratedAnalysisPipeline(media=media, asr=asr, ark=ark, skills=skills)


def build_default_app() -> FastAPI:
    from hakimi_analysis.app import create_app

    settings = Settings()
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.run_timeout_seconds, connect=15),
        follow_redirects=False,
    )
    catalog = build_catalog(settings)
    temp_root = PROJECT_ROOT / "tmp" / "analysis-runs"
    configured_cookie_secret = (
        settings.access_cookie_secret.get_secret_value()
        if settings.access_cookie_secret is not None
        else ""
    )
    cookie_secret = (
        configured_cookie_secret
        if len(configured_cookie_secret.encode("utf-8")) >= 32
        else secrets.token_urlsafe(32)
    )
    configured_judge_access_code = (
        settings.judge_access_code.get_secret_value()
        if settings.judge_access_code is not None
        else ""
    )
    judge_access_code = (
        configured_judge_access_code if configured_judge_access_code else secrets.token_urlsafe(16)
    )
    public_concurrency = settings.public_analysis_concurrency
    if settings.app_env == "test":
        public_concurrency = max(1, public_concurrency)
    access = AccessManager(
        cookie_secret=cookie_secret,
        judge_access_code=judge_access_code,
        judge_concurrency=settings.judge_analysis_concurrency,
        public_concurrency=public_concurrency,
        public_attempt_limit=100 if settings.app_env == "test" else 1,
    )
    readiness = ProductionReadiness(
        settings=settings,
        catalog=catalog,
        temp_root=temp_root,
        skills_root=PROJECT_ROOT / "skills",
    )
    return create_app(
        catalog=catalog,
        pipeline=build_pipeline(settings, http_client, temp_root=temp_root),
        timeout_seconds=settings.run_timeout_seconds,
        ttl_seconds=settings.run_ttl_seconds,
        cors_origins=settings.cors_origin_list,
        close_callbacks=[http_client.aclose],
        access=access,
        app_env=settings.app_env,
        readiness=readiness,
        web_static_root=settings.web_static_root,
        trusted_proxy_cidrs=settings.trusted_proxy_cidr_list,
    )
