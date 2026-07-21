import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from hakimi_analysis.app import create_app
from hakimi_analysis.bootstrap import build_catalog
from hakimi_analysis.pipeline import PipelineOutput
from hakimi_analysis.settings import Settings
from hakimi_analysis.sources import SourceCatalog, SourceManifestError, VideoSource


class EmptyPipeline:
    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float,
        emit: object,
    ) -> PipelineOutput:
        return PipelineOutput(candidates=[], empty_reason="no_evidence")


def write_manifest(tmp_path: Path, *, media_path: str = "competition/arm-01.mp4") -> Path:
    media_root = tmp_path / "media"
    source_path = media_root / "competition" / "arm-01.mp4"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"team-owned-video")
    second_source_path = media_root / "competition" / "arm-02.mp4"
    second_source_path.write_bytes(b"team-owned-video-2")
    manifest_path = tmp_path / "sources.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "sources": [
                    {
                        "id": "arm-01",
                        "title": "手臂训练 01",
                        "media_path": media_path,
                        "duration_seconds": 54.4,
                        "sha256": hashlib.sha256(b"team-owned-video").hexdigest(),
                        "origin_url": "https://www.douyin.com/video/123",
                    },
                    {
                        "id": "arm-02",
                        "title": "手臂训练 02",
                        "media_path": "competition/arm-02.mp4",
                        "duration_seconds": 48,
                        "sha256": hashlib.sha256(b"team-owned-video-2").hexdigest(),
                        "origin_url": None,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest_path


@pytest.mark.asyncio
async def test_manifest_sources_expose_origin_and_redirect_to_controlled_cdn(
    tmp_path: Path,
) -> None:
    manifest_path = write_manifest(tmp_path)
    catalog = SourceCatalog.from_manifest(
        manifest_path=manifest_path,
        media_root=tmp_path / "media",
        public_media_base_url="https://media.example.com/hachimi/",
        duration_probe=lambda path: 48 if path.name == "arm-02.mp4" else 54.4,
    )
    app = create_app(catalog=catalog, pipeline=EmptyPipeline())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
        follow_redirects=False,
    ) as client:
        sources = await client.get("/api/v1/sources")
        media = await client.get("/api/v1/sources/arm-01/media")

    assert sources.json() == [
        {
            "id": "arm-01",
            "title": "手臂训练 01",
            "media_url": "/api/v1/sources/arm-01/media",
            "duration_seconds": 54.4,
            "origin_url": "https://www.douyin.com/video/123",
        },
        {
            "id": "arm-02",
            "title": "手臂训练 02",
            "media_url": "/api/v1/sources/arm-02/media",
            "duration_seconds": 48.0,
            "origin_url": None,
        },
    ]
    assert media.status_code == 307
    assert media.headers["location"] == "https://media.example.com/hachimi/competition/arm-01.mp4"


def test_manifest_rejects_media_longer_than_full_source_boundary(tmp_path: Path) -> None:
    manifest_path = write_manifest(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["sources"][0]["duration_seconds"] = 60
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SourceManifestError):
        SourceCatalog.from_manifest(
            manifest_path=manifest_path,
            media_root=tmp_path / "media",
            public_media_base_url="https://media.example.com/hachimi/",
            duration_probe=lambda path: 48 if path.name == "arm-02.mp4" else 60.1,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "manifest_configuration",
    [
        {},
        {
            "source_manifest_path": "missing-sources.json",
            "source_media_root": "media",
        },
        {
            "source_manifest_path": "missing-sources.json",
            "source_media_root": "media",
            "public_media_base_url": "https://media.example.com/hachimi/",
        },
    ],
)
async def test_production_never_exposes_legacy_media_when_manifest_is_unavailable(
    tmp_path: Path,
    manifest_configuration: dict[str, Any],
) -> None:
    legacy_path = tmp_path / "legacy-unlicensed.mp4"
    legacy_path.write_bytes(b"legacy-local-video")
    resolved_configuration = {
        key: tmp_path / value if key != "public_media_base_url" else value
        for key, value in manifest_configuration.items()
    }
    settings = Settings(
        _env_file=None,
        app_env="production",
        hakimi_demo_video_path=legacy_path,
        **resolved_configuration,
    )
    catalog = build_catalog(settings)
    app = create_app(catalog=catalog, pipeline=EmptyPipeline(), app_env="production")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
    ) as client:
        sources = await client.get("/api/v1/sources")
        media = await client.get("/api/v1/sources/legacy-arm-workout/media")

    assert catalog.manifest_backed is False
    assert sources.status_code == 200
    assert sources.json() == []
    assert media.status_code == 404


def test_manifest_rejects_media_paths_outside_the_controlled_root(tmp_path: Path) -> None:
    manifest_path = write_manifest(tmp_path, media_path="../private.mp4")

    with pytest.raises(SourceManifestError):
        SourceCatalog.from_manifest(
            manifest_path=manifest_path,
            media_root=tmp_path / "media",
            public_media_base_url="https://media.example.com/hachimi/",
            duration_probe=lambda path: 48 if path.name == "arm-02.mp4" else 54.4,
        )


def test_manifest_rejects_origin_url_userinfo_without_echoing_credentials(
    tmp_path: Path,
) -> None:
    manifest_path = write_manifest(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["sources"][0]["origin_url"] = "https://demo-user:demo-password@www.douyin.com/video/123"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SourceManifestError) as failure:
        SourceCatalog.from_manifest(
            manifest_path=manifest_path,
            media_root=tmp_path / "media",
            public_media_base_url="https://media.example.com/hachimi/",
            duration_probe=lambda path: 48 if path.name == "arm-02.mp4" else 54.4,
        )

    assert "demo-user" not in str(failure.value)
    assert "demo-password" not in str(failure.value)
