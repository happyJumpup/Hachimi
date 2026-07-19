
import httpx
from fastapi import FastAPI

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
from hakimi_analysis.settings import PROJECT_ROOT, Settings
from hakimi_analysis.sources import EmptySourceCatalog, SourceCatalog, VideoSource


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
    path = settings.hakimi_demo_video_path
    if path is None:
        return EmptySourceCatalog()
    resolved_path = path.expanduser().resolve()
    duration = probe_duration_sync(resolved_path)
    return SourceCatalog(
        [
            VideoSource(
                id="legacy-arm-workout",
                title="哈基米手臂训练｜本地受控视频",
                path=resolved_path,
                duration_seconds=duration,
            )
        ]
    )


def build_pipeline(settings: Settings, http_client: httpx.AsyncClient) -> AnalysisPipeline:
    if settings.analysis_provider == "test":
        return DeterministicTestPipeline()
    if settings.ark_api_key is None or settings.volc_asr_api_key is None:
        return UnconfiguredPipeline()

    media = LocalMediaProcessor(temp_root=PROJECT_ROOT / "tmp" / "analysis-runs")
    asr = VolcAsrClient(
        api_key=settings.volc_asr_api_key.get_secret_value(),
        resource_id=settings.volc_asr_resource_id,
        url=settings.volc_asr_url,
        http_client=http_client,
    )
    ark = ArkResponsesClient(
        api_key=settings.ark_api_key.get_secret_value(),
        model_id=settings.ark_model_id,
        base_url=settings.ark_base_url,
        http_client=http_client,
    )
    return OrchestratedAnalysisPipeline(
        media=media,
        asr=asr,
        ark=ark,
        skills=SkillRepository.load(PROJECT_ROOT / "skills"),
    )


def build_default_app() -> FastAPI:
    from hakimi_analysis.app import create_app

    settings = Settings()
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.run_timeout_seconds, connect=15),
        follow_redirects=False,
    )
    return create_app(
        catalog=build_catalog(settings),
        pipeline=build_pipeline(settings, http_client),
        timeout_seconds=settings.run_timeout_seconds,
        ttl_seconds=settings.run_ttl_seconds,
        cors_origins=settings.cors_origin_list,
        close_callbacks=[http_client.aclose],
    )
