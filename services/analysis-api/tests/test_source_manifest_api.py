import hashlib
import json
from pathlib import Path

import httpx
import pytest

from hakimi_analysis.app import create_app
from hakimi_analysis.pipeline import PipelineOutput
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
                        "duration_seconds": 96.4,
                        "sha256": hashlib.sha256(b"team-owned-video").hexdigest(),
                        "origin_url": "https://www.douyin.com/video/123",
                    },
                    {
                        "id": "arm-02",
                        "title": "手臂训练 02",
                        "media_path": "competition/arm-02.mp4",
                        "duration_seconds": 75,
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
        duration_probe=lambda path: 75 if path.name == "arm-02.mp4" else 96.4,
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
            "duration_seconds": 96.4,
            "origin_url": "https://www.douyin.com/video/123",
        },
        {
            "id": "arm-02",
            "title": "手臂训练 02",
            "media_url": "/api/v1/sources/arm-02/media",
            "duration_seconds": 75.0,
            "origin_url": None,
        },
    ]
    assert media.status_code == 307
    assert media.headers["location"] == "https://media.example.com/hachimi/competition/arm-01.mp4"


def test_manifest_rejects_media_paths_outside_the_controlled_root(tmp_path: Path) -> None:
    manifest_path = write_manifest(tmp_path, media_path="../private.mp4")

    with pytest.raises(SourceManifestError):
        SourceCatalog.from_manifest(
            manifest_path=manifest_path,
            media_root=tmp_path / "media",
            public_media_base_url="https://media.example.com/hachimi/",
            duration_probe=lambda path: 75 if path.name == "arm-02.mp4" else 96.4,
        )
