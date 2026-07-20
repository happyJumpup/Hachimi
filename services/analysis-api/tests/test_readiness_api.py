import hashlib
import json
from pathlib import Path

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
        skills_root=tmp_path / "skills",
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
        skills_root=tmp_path / "skills",
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
        skills_root=tmp_path / "skills",
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


def configured_readiness(tmp_path: Path) -> tuple[ProductionReadiness, Path]:
    media_root = tmp_path / "media"
    media_root.mkdir()
    media_path = media_root / "arm-01.mp4"
    media_path.write_bytes(b"team-owned-video")
    manifest_path = tmp_path / "sources.json"
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
    skills_root = tmp_path / "skills"
    for skill_name in (
        "training-speech-understanding",
        "visual-action-localization",
        "candidate-fusion",
    ):
        skill_dir = skills_root / skill_name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("version: 1.0.0\n", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        app_env="production",
        analysis_provider="cloud",
        ark_api_key="ark-key",
        volc_asr_api_key="asr-key",
        source_manifest_path=manifest_path,
        source_media_root=media_root,
        public_media_base_url="https://media.example.com/hachimi/",
        judge_access_code="judge-code",
        access_cookie_secret="cookie-signing-secret-with-at-least-32-bytes",
    )
    catalog = SourceCatalog.from_manifest(
        manifest_path=manifest_path,
        media_root=media_root,
        public_media_base_url="https://media.example.com/hachimi/",
        duration_probe=lambda _: 60,
    )
    return (
        ProductionReadiness(
            settings=settings,
            catalog=catalog,
            temp_root=tmp_path / "analysis-runs",
            skills_root=skills_root,
            duration_probe=lambda _: 60,
        ),
        media_path,
    )


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
