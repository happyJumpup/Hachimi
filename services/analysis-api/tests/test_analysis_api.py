import asyncio
from pathlib import Path
from typing import cast

import httpx
import pytest

from hakimi_analysis.app import create_app
from hakimi_analysis.models import (
    AnalysisCandidate,
    CandidateParameters,
    EvidenceSpan,
    EvidenceType,
    RunStage,
    Segment,
)
from hakimi_analysis.pipeline import EmitCallback, PipelineOutput
from hakimi_analysis.sources import SourceCatalog, VideoSource


class SuccessfulPipeline:
    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float,
        emit: EmitCallback,
    ) -> PipelineOutput:
        await emit(RunStage.ANALYZING_EVIDENCE, "stage.changed", {})
        await emit(RunStage.FUSING_CANDIDATES, "stage.changed", {})
        return PipelineOutput(
            candidates=[
                AnalysisCandidate(
                    id="candidate-1",
                    name="拖拽弯举",
                    source_id=source.id,
                    segment=Segment(start_seconds=41, end_seconds=51),
                    parameters=CandidateParameters(),
                    evidence=[
                        EvidenceSpan(
                            type=EvidenceType.VISUAL,
                            start_seconds=41,
                            end_seconds=51,
                        )
                    ],
                    needs_confirmation=True,
                )
            ]
        )


class SlowPipeline:
    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float,
        emit: EmitCallback,
    ) -> PipelineOutput:
        await emit(RunStage.ANALYZING_EVIDENCE, "stage.changed", {})
        await asyncio.sleep(30)
        return PipelineOutput()


def source_catalog(tmp_path: Path) -> SourceCatalog:
    path = tmp_path / "source.mp4"
    path.write_bytes(b"not-used-by-the-test-pipeline")
    return SourceCatalog(
        [
            VideoSource(
                id="legacy-arm-workout",
                title="本地联调视频",
                path=path,
                duration_seconds=54,
            )
        ]
    )


async def wait_for_status(client: httpx.AsyncClient, run_id: str, status: str) -> dict[str, object]:
    for _ in range(100):
        response = await client.get(f"/api/v1/analysis-runs/{run_id}")
        payload = cast(dict[str, object], response.json())
        if payload["status"] == status:
            return payload
        await asyncio.sleep(0.01)
    raise AssertionError(f"run did not reach {status}")


@pytest.mark.asyncio
async def test_sources_and_completed_run_are_observable_through_http(tmp_path: Path) -> None:
    app = create_app(catalog=source_catalog(tmp_path), pipeline=SuccessfulPipeline())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        sources = await client.get("/api/v1/sources")
        assert sources.status_code == 200
        assert sources.json() == [
            {
                "id": "legacy-arm-workout",
                "title": "本地联调视频",
                "media_url": "/api/v1/sources/legacy-arm-workout/media",
                "duration_seconds": 54.0,
            }
        ]

        created = await client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "legacy-arm-workout", "trigger_seconds": 45},
        )
        assert created.status_code == 202
        run_id = created.json()["id"]

        completed = await wait_for_status(client, run_id, "completed")
        assert completed["candidates"][0]["name"] == "拖拽弯举"  # type: ignore[index]

        events = await client.get(f"/api/v1/analysis-runs/{run_id}/events")
        assert events.status_code == 200
        assert "event: run.completed" in events.text


@pytest.mark.asyncio
async def test_unknown_source_is_rejected_without_starting_a_run(tmp_path: Path) -> None:
    app = create_app(catalog=source_catalog(tmp_path), pipeline=SuccessfulPipeline())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "../../private.mp4", "trigger_seconds": 10},
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_controlled_media_supports_http_range(tmp_path: Path) -> None:
    catalog = source_catalog(tmp_path)
    source_bytes = catalog.get("legacy-arm-workout").path.read_bytes()
    app = create_app(catalog=catalog, pipeline=SuccessfulPipeline())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/sources/legacy-arm-workout/media",
            headers={"Range": "bytes=0-3"},
        )

    assert response.status_code == 206
    assert response.content == source_bytes[:4]
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-range"] == f"bytes 0-3/{len(source_bytes)}"


@pytest.mark.asyncio
async def test_delete_cancels_an_in_flight_run(tmp_path: Path) -> None:
    app = create_app(catalog=source_catalog(tmp_path), pipeline=SlowPipeline())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "legacy-arm-workout", "trigger_seconds": 45},
        )
        run_id = created.json()["id"]
        cancelled = await client.delete(f"/api/v1/analysis-runs/{run_id}")
        assert cancelled.status_code == 202
        view = await wait_for_status(client, run_id, "cancelled")
        assert view["stage"] == "cancelled"
