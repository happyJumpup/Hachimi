import hashlib
import json
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
    settings_overrides: dict[str, Any] | None = None,
) -> tuple[ProductionReadiness, Path]:
    media_root = tmp_path / "media"
    media_path = media_root / "arm-01.mp4"
    manifest_path = tmp_path / "sources.json"
    if controlled_sources:
        media_root.mkdir()
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
    skills_root = tmp_path / "skills"
    for skill_name in (
        "training-speech-understanding",
        "visual-action-localization",
        "candidate-fusion",
    ):
        skill_dir = skills_root / skill_name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("version: 1.0.0\n", encoding="utf-8")
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
            skills_root=skills_root,
            duration_probe=lambda _: 60,
            ffmpeg_receipt_probe=lambda *_: ffmpeg_audit_passes,
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
        ("analysis_visual_chunk_seconds", 50),
        ("analysis_visual_overlap_seconds", 5),
        ("run_timeout_seconds", 179),
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
