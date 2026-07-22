import secrets
from pathlib import Path

import httpx
from fastapi import FastAPI

from hakimi_analysis.access import AccessManager
from hakimi_analysis.content_understanding import (
    ContentUnderstandingPipeline,
    ContentUnderstandingProvider,
)
from hakimi_analysis.media import LocalMediaProcessor, probe_duration_sync
from hakimi_analysis.models import (
    AnalysisCandidate,
    CandidateParameters,
    EvidenceSpan,
    EvidenceType,
    RunStage,
    Segment,
)
from hakimi_analysis.pipeline import AnalysisPipeline, EmitCallback, PipelineFailure, PipelineOutput
from hakimi_analysis.provider_contracts import PromptContractError, PromptContractRegistry
from hakimi_analysis.provider_profile import ProviderProfile
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.providers.asr import VolcAsrClient
from hakimi_analysis.providers.qwen import QwenVisualClient
from hakimi_analysis.readiness import ProductionReadiness
from hakimi_analysis.settings import PROJECT_ROOT, Settings
from hakimi_analysis.sources import (
    MAX_ANALYZABLE_SOURCE_DURATION_SECONDS,
    EmptySourceCatalog,
    SourceCatalog,
    SourceManifestError,
    VideoSource,
)
from hakimi_analysis.temporary_cos import TencentCosTemporaryStore
from hakimi_analysis.visual_routing import DirectVisualProvider, SequentialVisualRouter


class UnconfiguredPipeline:
    async def analyze(
        self,
        source: VideoSource,
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
        emit: EmitCallback,
    ) -> PipelineOutput:
        await emit(RunStage.ANALYZING_EVIDENCE, "stage.changed", {})
        start = source.analysis_start_seconds
        end = start + min(source.analysis_duration_seconds, 10)
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
                    needs_confirmation=True,
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
    if duration > MAX_ANALYZABLE_SOURCE_DURATION_SECONDS:
        return EmptySourceCatalog()
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
    fallback_store: TencentCosTemporaryStore | None = None,
    profile: ProviderProfile | None = None,
) -> AnalysisPipeline:
    if settings.analysis_provider == "test":
        return DeterministicTestPipeline()
    if settings.ark_api_key is None or settings.volc_asr_api_key is None:
        return UnconfiguredPipeline()

    try:
        resolved_profile = profile or ProviderProfile.from_settings(settings)
        visual_model_ids: tuple[str, ...] = (settings.ark_visual_model_id,)
        if settings.visual_fallback_enabled:
            visual_model_ids += (settings.qwen_visual_model_id,)
        contracts = PromptContractRegistry.load(
            PROJECT_ROOT / "services" / "analysis-api" / "provider-contracts",
            speech_model_id=settings.ark_model_id,
            visual_model_ids=visual_model_ids,
        )
    except (OSError, PromptContractError, ValueError):
        return UnconfiguredPipeline()

    media = LocalMediaProcessor(
        temp_root=temp_root or PROJECT_ROOT / "tmp" / "analysis-runs",
        max_source_duration_seconds=resolved_profile.max_source_seconds,
    )
    asr = VolcAsrClient(
        api_key=settings.volc_asr_api_key.get_secret_value(),
        resource_id=settings.volc_asr_resource_id,
        url=settings.volc_asr_url,
        pace_audio=False,
        hotwords=contracts.asr_context.hotwords,
    )
    speech_interpreter = ArkResponsesClient(
        api_key=settings.ark_api_key.get_secret_value(),
        model_id=settings.ark_model_id,
        visual_model_id=settings.ark_visual_model_id,
        base_url=settings.ark_base_url,
        http_client=http_client,
    )
    visual_locator = ArkResponsesClient(
        api_key=settings.ark_api_key.get_secret_value(),
        model_id=settings.ark_model_id,
        visual_model_id=settings.ark_visual_model_id,
        base_url=settings.ark_base_url,
        http_client=http_client,
        # The visual router owns the exact per-chunk attempt and call budget.
        retry_delays=(),
    )
    visual_router: SequentialVisualRouter | None = None
    if settings.visual_fallback_enabled:
        resolved_store = fallback_store or _build_fallback_store(settings)
        if resolved_store is None or settings.qwen_api_key is None:
            return UnconfiguredPipeline()
        qwen = QwenVisualClient(
            api_key=settings.qwen_api_key.get_secret_value(),
            model_id=settings.qwen_visual_model_id,
            base_url=settings.qwen_base_url,
            http_client=http_client,
            object_store=resolved_store,
        )
        visual_router = SequentialVisualRouter(
            primary=DirectVisualProvider(visual_locator),
            fallback=qwen,
            attempt_timeout_seconds=resolved_profile.visual_attempt_timeout_seconds,
            max_attempts_per_provider=resolved_profile.max_attempts_per_visual_provider,
            max_visual_calls=resolved_profile.max_visual_calls,
        )
    provider = ContentUnderstandingProvider(
        media=media,
        transcriber=asr,
        interpreter=speech_interpreter,
        visual_locator=visual_locator,
        visual_router=visual_router,
        contracts=contracts,
        speech_timeout_seconds=resolved_profile.speech_timeout_seconds,
        visual_chunk_timeout_seconds=resolved_profile.visual_attempt_timeout_seconds,
        visual_chunk_seconds=resolved_profile.visual_chunk_seconds,
        visual_overlap_seconds=resolved_profile.visual_overlap_seconds,
        evidence_deadline_seconds=resolved_profile.evidence_deadline_seconds,
        run_timeout_seconds=resolved_profile.run_timeout_seconds,
        cleanup_reserve_seconds=resolved_profile.cleanup_reserve_seconds,
        max_attempts_per_visual_provider=resolved_profile.max_attempts_per_visual_provider,
        max_visual_calls=resolved_profile.max_visual_calls,
    )
    return ContentUnderstandingPipeline(provider)


def build_default_app() -> FastAPI:
    from hakimi_analysis.app import create_app

    settings = Settings()
    profile = ProviderProfile.from_settings(settings)
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.run_timeout_seconds, connect=15),
        follow_redirects=False,
    )
    catalog = build_catalog(settings)
    temp_root = PROJECT_ROOT / "tmp" / "analysis-runs"
    fallback_store = (
        _build_fallback_store(settings) if settings.visual_fallback_enabled else None
    )
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
        contracts_root=PROJECT_ROOT / "services" / "analysis-api" / "provider-contracts",
        fallback_probe=(
            None
            if fallback_store is None
            else lambda: (
                fallback_store.safety_gate.available
                and fallback_store.check_security_configuration()
            )
        ),
    )
    return create_app(
        catalog=catalog,
        pipeline=build_pipeline(
            settings,
            http_client,
            temp_root=temp_root,
            fallback_store=fallback_store,
            profile=profile,
        ),
        timeout_seconds=profile.run_timeout_seconds,
        ttl_seconds=settings.run_ttl_seconds,
        cors_origins=settings.cors_origin_list,
        close_callbacks=[http_client.aclose],
        access=access,
        app_env=settings.app_env,
        readiness=readiness,
        web_static_root=settings.web_static_root,
        trusted_proxy_cidrs=settings.trusted_proxy_cidr_list,
        local_upload_enabled=settings.local_upload_enabled,
        local_analysis_max_seconds=settings.published_analysis_max_seconds,
        accepted_local_analysis_max_seconds=profile.max_source_seconds,
        local_upload_max_bytes=profile.max_source_bytes,
        local_upload_temp_root=temp_root,
    )


def _build_fallback_store(settings: Settings) -> TencentCosTemporaryStore | None:
    if (
        settings.qwen_api_key is None
        or settings.cos_secret_id is None
        or settings.cos_secret_key is None
    ):
        return None
    return TencentCosTemporaryStore(
        secret_id=settings.cos_secret_id.get_secret_value(),
        secret_key=settings.cos_secret_key.get_secret_value(),
        region=settings.cos_region,
        bucket=settings.cos_bucket,
        object_prefix=settings.cos_object_prefix,
        signed_url_ttl_seconds=settings.cos_signed_url_ttl_seconds,
        lifecycle_days=settings.cos_lifecycle_days,
    )
