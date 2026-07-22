import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from hakimi_analysis.access import AccessManager
from hakimi_analysis.app import create_app
from hakimi_analysis.readiness import ProductionReadiness
from hakimi_analysis.settings import Settings
from hakimi_analysis.sources import EmptySourceCatalog, SourceCatalog, VideoSource


@pytest.mark.asyncio
async def test_production_ready_fails_closed_without_real_provider_configuration(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        analysis_provider="cloud",
        source_manifest_path=tmp_path / "missing-sources.json",
        source_media_root=tmp_path / "media",
        public_media_base_url="https://media.example.com/hachimi/",
        judge_access_code="judge-code",
        access_cookie_secret="cookie-signing-secret-with-at-least-32-bytes",
    )
    readiness = ProductionReadiness(
        settings=settings,
        catalog=EmptySourceCatalog(),
        temp_root=tmp_path / "analysis-runs",
        contracts_root=tmp_path / "provider-contracts",
    )
    app = create_app(app_env="production", readiness=readiness)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        health = await client.get("/api/v1/health")
        ready = await client.get("/api/v1/ready")

    assert health.status_code == 200
    assert ready.status_code == 503
    assert ready.json() == {
        "status": "not_ready",
        "code": "provider_configuration_invalid",
    }
    serialized = ready.text
    assert "missing-sources.json" not in serialized
    assert "cookie-signing-secret" not in serialized


@pytest.mark.asyncio
async def test_production_does_not_start_analysis_while_not_ready(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        analysis_provider="cloud",
        source_manifest_path=tmp_path / "missing-sources.json",
        source_media_root=tmp_path / "media",
        public_media_base_url="https://media.example.com/hachimi/",
        judge_access_code="judge-code",
        access_cookie_secret="cookie-signing-secret-with-at-least-32-bytes",
    )
    readiness = ProductionReadiness(
        settings=settings,
        catalog=EmptySourceCatalog(),
        temp_root=tmp_path / "analysis-runs",
        contracts_root=tmp_path / "provider-contracts",
    )
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"not-read")
    catalog = SourceCatalog(
        [VideoSource(id="arm-01", title="手臂训练", path=source_path, duration_seconds=60)]
    )
    access = AccessManager(
        cookie_secret="cookie-signing-secret-with-at-least-32-bytes",
        judge_access_code="judge-code",
        public_concurrency=1,
    )
    app = create_app(
        catalog=catalog,
        access=access,
        app_env="production",
        readiness=readiness,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "arm-01", "trigger_seconds": 10},
            headers={"Origin": "https://test"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "动作分析服务尚未就绪"}


@pytest.mark.asyncio
async def test_access_session_does_not_claim_analysis_is_available_while_not_ready(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        analysis_provider="cloud",
        judge_access_code="judge-code",
        access_cookie_secret="cookie-signing-secret-with-at-least-32-bytes",
    )
    readiness = ProductionReadiness(
        settings=settings,
        catalog=EmptySourceCatalog(),
        temp_root=tmp_path / "analysis-runs",
        contracts_root=tmp_path / "provider-contracts",
    )
    access = AccessManager(
        cookie_secret="cookie-signing-secret-with-at-least-32-bytes",
        judge_access_code="judge-code",
    )
    app = create_app(
        access=access,
        app_env="production",
        readiness=readiness,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        await client.get("/api/v1/access/session")
        upgraded = await client.post(
            "/api/v1/access/session",
            json={"access_code": "judge-code"},
            headers={"Origin": "https://test"},
        )

    assert upgraded.status_code == 200
    assert upgraded.json() == {
        "tier": "judge",
        "can_analyze": False,
        "retry_after_seconds": None,
    }


def configured_readiness(
    tmp_path: Path,
    *,
    controlled_sources: bool = True,
    include_web_root: bool = True,
    trusted_proxy_cidrs: str = "",
    judge_access_code: str = "judge-access-code-at-least-16-bytes",
    include_ffmpeg: bool = True,
    include_ffmpeg_receipt: bool = True,
    ffmpeg_audit_passes: bool = True,
    fallback_probe: Callable[[], bool] | None = None,
    settings_overrides: dict[str, Any] | None = None,
) -> tuple[ProductionReadiness, Path]:
    media_root = tmp_path / "media"
    media_path = media_root / "arm-01.mp4"
    manifest_path = tmp_path / "sources.json"
    if controlled_sources:
        media_root.mkdir(parents=True)
        media_path.write_bytes(b"team-owned-video")
        manifest_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "sources": [
                        {
                            "id": "arm-01",
                            "title": "手臂训练 01",
                            "media_path": "arm-01.mp4",
                            "duration_seconds": 60,
                            "sha256": hashlib.sha256(b"team-owned-video").hexdigest(),
                            "origin_url": None,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
    contracts_root = tmp_path / "provider-contracts"
    prompt_contracts = (
        (
            "speech-evidence",
            "Extract explicit timestamped exercise evidence.",
            "timestamped_transcript",
            "analysis_range_relative",
            "SpeechUnderstandingResult.v1",
            ["doubao-seed-2.0"],
        ),
        (
            "visual-evidence",
            "Inspect the complete continuous silent MP4 chunk.",
            "continuous_silent_mp4",
            "chunk_relative",
            "VisualLocalizationResult.v1",
            ["doubao-seed-2.0", "qwen3-vl"],
        ),
    )
    for name, prompt, input_media, clock, schema, families in prompt_contracts:
        contract_dir = contracts_root / name
        contract_dir.mkdir(parents=True)
        (contract_dir / "PROMPT.md").write_text(prompt, encoding="utf-8")
        (contract_dir / "contract.json").write_text(
            json.dumps(
                {
                    "contract_version": "1.0.0",
                    "input_media_type": input_media,
                    "time_coordinate": clock,
                    "output_schema": schema,
                    "compatible_model_families": families,
                    "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                }
            ),
            encoding="utf-8",
        )
    asr_context_root = contracts_root / "asr-fitness-context"
    asr_context_root.mkdir(parents=True)
    hotwords = "罗马尼亚硬拉\n蝴蝶机反向飞鸟\n"
    (asr_context_root / "HOTWORDS.txt").write_text(hotwords, encoding="utf-8")
    (asr_context_root / "contract.json").write_text(
        json.dumps(
            {
                "contract_version": "asr-fitness-context-v1",
                "request_field": "request.context",
                "mode": "hotwords",
                "content_sha256": hashlib.sha256(hotwords.encode("utf-8")).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    web_static_root = tmp_path / "web-dist"
    if include_web_root:
        web_static_root.mkdir()
        (web_static_root / "index.html").write_text("<main>ready</main>", encoding="utf-8")
    ffmpeg_root = tmp_path / "opt" / "trainpal" / "ffmpeg"
    ffmpeg_path = ffmpeg_root / "bin" / "ffmpeg"
    ffmpeg_receipt_path = ffmpeg_root / "receipt.json"
    if include_ffmpeg:
        ffmpeg_path.parent.mkdir(parents=True)
        ffmpeg_path.write_bytes(b"server-managed-ffmpeg")
        if include_ffmpeg_receipt:
            ffmpeg_receipt_path.write_text("{}", encoding="utf-8")
    ffmpeg_sha256 = hashlib.sha256(b"server-managed-ffmpeg").hexdigest()
    settings_kwargs: dict[str, Any] = {
        "_env_file": None,
        "app_env": "production",
        "analysis_provider": "cloud",
        "ark_api_key": "ark-key",
        "volc_asr_api_key": "asr-key",
        "source_manifest_path": manifest_path if controlled_sources else None,
        "source_media_root": media_root if controlled_sources else None,
        "public_media_base_url": (
            "https://media.example.com/hachimi/" if controlled_sources else None
        ),
        "judge_access_code": judge_access_code,
        "access_cookie_secret": "cookie-signing-secret-with-at-least-32-bytes",
        "judge_analysis_concurrency": 3,
        "public_analysis_concurrency": 0,
        "web_static_root": web_static_root,
        "trusted_proxy_cidrs": trusted_proxy_cidrs,
        "imageio_ffmpeg_exe": ffmpeg_path if include_ffmpeg else None,
        "ffmpeg_build_receipt_path": (
            ffmpeg_receipt_path if include_ffmpeg and include_ffmpeg_receipt else None
        ),
        "ffmpeg_expected_sha256": ffmpeg_sha256 if include_ffmpeg else None,
        "ffmpeg_expected_configuration_sha256": "c" * 64 if include_ffmpeg else None,
    }
    settings_kwargs.update(settings_overrides or {})
    settings = Settings(**settings_kwargs)
    catalog = (
        SourceCatalog.from_manifest(
            manifest_path=manifest_path,
            media_root=media_root,
            public_media_base_url="https://media.example.com/hachimi/",
            duration_probe=lambda _: 60,
        )
        if controlled_sources
        else EmptySourceCatalog()
    )
    return (
        ProductionReadiness(
            settings=settings,
            catalog=catalog,
            temp_root=tmp_path / "analysis-runs",
            contracts_root=contracts_root,
            duration_probe=lambda _: 60,
            ffmpeg_receipt_probe=lambda *_: ffmpeg_audit_passes,
            fallback_probe=fallback_probe,
        ),
        media_path,
    )


@pytest.mark.asyncio
async def test_production_ready_requires_a_high_entropy_judge_code(tmp_path: Path) -> None:
    readiness, _ = configured_readiness(tmp_path, judge_access_code="short")
    app = create_app(app_env="production", readiness=readiness)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "code": "access_configuration_invalid",
    }


@pytest.mark.asyncio
async def test_production_ready_requires_a_server_managed_ffmpeg(tmp_path: Path) -> None:
    readiness, _ = configured_readiness(tmp_path, include_ffmpeg=False)
    app = create_app(app_env="production", readiness=readiness)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "code": "media_processor_unavailable",
    }


@pytest.mark.asyncio
async def test_production_ready_rejects_ffmpeg_that_fails_runtime_audit(tmp_path: Path) -> None:
    readiness, _ = configured_readiness(tmp_path, ffmpeg_audit_passes=False)
    app = create_app(app_env="production", readiness=readiness)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "code": "media_processor_unavailable",
    }


@pytest.mark.asyncio
async def test_production_ready_rejects_legacy_hashes_without_signed_build_receipt(
    tmp_path: Path,
) -> None:
    readiness, _ = configured_readiness(tmp_path, include_ffmpeg_receipt=False)
    app = create_app(app_env="production", readiness=readiness)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "code": "media_processor_unavailable",
    }


@pytest.mark.parametrize(
    ("setting_name", "unsafe_value"),
    [
        ("local_upload_enabled", False),
        ("local_analysis_max_seconds", 299),
        ("local_upload_max_bytes", 128 * 1024 * 1024),
        ("analysis_chunk_timeout_seconds", 19),
        ("analysis_speech_timeout_seconds", 44),
        ("analysis_evidence_deadline_seconds", 169),
        ("analysis_cleanup_reserve_seconds", 9),
        ("analysis_visual_chunk_seconds", 50),
        ("analysis_visual_overlap_seconds", 5),
        ("analysis_max_visual_chunks", 5),
        ("analysis_max_attempts_per_visual_provider", 1),
        ("analysis_max_visual_calls", 11),
        ("run_timeout_seconds", 181),
        ("judge_analysis_concurrency", 2),
        ("public_analysis_concurrency", 1),
        ("trusted_proxy_cidrs", "10.0.0.0/8"),
    ],
)
def test_production_ready_rejects_competition_profile_drift(
    tmp_path: Path,
    setting_name: str,
    unsafe_value: object,
) -> None:
    readiness, _ = configured_readiness(
        tmp_path,
        settings_overrides={setting_name: unsafe_value},
    )

    assert readiness.check() == "competition_configuration_invalid"


def test_production_ready_rejects_prompt_contract_hash_drift(tmp_path: Path) -> None:
    readiness, _ = configured_readiness(tmp_path)
    (tmp_path / "provider-contracts" / "visual-evidence" / "PROMPT.md").write_text(
        "Inspect a legacy row-major contact sheet.",
        encoding="utf-8",
    )

    assert readiness.check() == "prompt_contract_invalid"


def test_production_ready_verifies_enabled_qwen_cos_fallback(tmp_path: Path) -> None:
    fallback_settings = {
        "visual_fallback_enabled": True,
        "qwen_api_key": "qwen-key",
        "cos_secret_id": "cos-id",
        "cos_secret_key": "cos-key",
        "cos_region": "ap-guangzhou",
        "cos_bucket": "private-bucket-123",
    }
    not_ready, _ = configured_readiness(
        tmp_path / "not-ready",
        settings_overrides=fallback_settings,
        fallback_probe=lambda: False,
    )
    ready, _ = configured_readiness(
        tmp_path / "ready",
        settings_overrides=fallback_settings,
        fallback_probe=lambda: True,
    )

    assert not_ready.check() == "visual_fallback_configuration_invalid"
    assert ready.check() is None


def test_production_ready_publishes_300_seconds_only_after_canary_receipt(
    tmp_path: Path,
) -> None:
    commit_sha = "a" * 40
    primary_model = "doubao-seed-2-0-mini-260428"
    fallback_model = "qwen3-vl-flash-2026-01-22"
    visual_prompt_sha256 = hashlib.sha256(
        b"Inspect the complete continuous silent MP4 chunk."
    ).hexdigest()

    def route(name: str, model_id: str) -> dict[str, object]:
        return {
            "route": name,
            "model_id": model_id,
            "units": 24,
            "complete": True,
            "success_rate": 1.0,
            "precision": 0.95,
            "recall": 0.9,
            "f1_tiou_05": 0.9,
            "time_hit_rate": 0.9,
            "median_seconds_per_video_minute": 10.0,
            "schema_or_clock_violations": 0,
            "cost_status": "not-measured",
            "passes_quality_gate": True,
        }

    conformance_report = json.dumps(
        {
            "schema_version": 1,
            "manifest_version": "provider-conformance-v1",
            "prompt_contract_sha256": visual_prompt_sha256,
            "routes": [
                route("seed-mini", primary_model),
                route("seed-lite", "doubao-seed-2-0-lite-260428"),
                route("qwen-vl", fallback_model),
            ],
            "selection": "seed-mini",
            "seed_primary_selection": "seed-mini",
            "qwen_fallback_qualified": True,
            "version_c_decision": "eligible-for-version-c-canary",
        },
        separators=(",", ":"),
    )
    conformance_sha256 = hashlib.sha256(conformance_report.encode()).hexdigest()
    receipt = tmp_path / "provider-canary.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profile_version": "five-minute-production-v1",
                "commit_sha": commit_sha,
                "completed_at": "2026-07-22T01:00:00Z",
                "private_smoke_passed": True,
                "qwen_preflight_passed": True,
                "full_pipeline_quality_passed": True,
                "parameter_provenance_passed": True,
                "five_minute_end_coverage_passed": True,
                "three_way_concurrency_passed": True,
                "cancellation_cleanup_passed": True,
                "circuit_breaker_passed": True,
                "cos_cleanup_passed": True,
                "rollback_sequence": "B-C-B-C",
                "published_max_seconds": 300,
                "primary_model_id": primary_model,
                "fallback_model_id": fallback_model,
                "provider_conformance_report_sha256": conformance_sha256,
            }
        ),
        encoding="utf-8",
    )
    fallback_settings = {
        "visual_fallback_enabled": True,
        "qwen_api_key": "qwen-key",
        "cos_secret_id": "cos-id",
        "cos_secret_key": "cos-key",
        "cos_region": "ap-guangzhou",
        "cos_bucket": "private-bucket-123",
        "published_analysis_max_seconds": 300,
        "deployment_commit_sha": commit_sha,
        "provider_conformance_report_json": conformance_report,
    }
    blocked, _ = configured_readiness(
        tmp_path / "blocked",
        settings_overrides=fallback_settings,
        fallback_probe=lambda: True,
    )
    released, _ = configured_readiness(
        tmp_path / "released",
        settings_overrides={
            **fallback_settings,
            "provider_canary_receipt_path": receipt,
        },
        fallback_probe=lambda: True,
    )

    assert blocked.check() == "competition_configuration_invalid"
    assert released.check() is None


@pytest.mark.asyncio
async def test_production_ready_requires_built_web_entrypoint(tmp_path: Path) -> None:
    readiness, _ = configured_readiness(tmp_path, include_web_root=False)
    app = create_app(app_env="production", readiness=readiness)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "code": "web_static_unavailable"}


@pytest.mark.asyncio
async def test_production_ready_uses_direct_peer_when_no_trusted_proxy_is_configured(
    tmp_path: Path,
) -> None:
    readiness, _ = configured_readiness(tmp_path, trusted_proxy_cidrs="")
    app = create_app(app_env="production", readiness=readiness)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


@pytest.mark.asyncio
async def test_production_ready_accepts_local_upload_without_controlled_catalog(
    tmp_path: Path,
) -> None:
    readiness, _ = configured_readiness(
        tmp_path,
        controlled_sources=False,
        trusted_proxy_cidrs="",
    )
    app = create_app(app_env="production", readiness=readiness)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


@pytest.mark.asyncio
async def test_production_ready_checks_manifest_media_skills_access_and_temp_storage(
    tmp_path: Path,
) -> None:
    readiness, _ = configured_readiness(tmp_path)
    app = create_app(app_env="production", readiness=readiness)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert list((tmp_path / "analysis-runs").iterdir()) == []


@pytest.mark.asyncio
async def test_production_ready_detects_media_changed_after_startup(tmp_path: Path) -> None:
    readiness, media_path = configured_readiness(tmp_path)
    media_path.write_bytes(b"tampered-video")
    app = create_app(app_env="production", readiness=readiness)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "code": "media_cache_invalid"}
