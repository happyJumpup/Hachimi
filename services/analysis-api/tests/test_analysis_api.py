import asyncio
import json
import logging
from pathlib import Path
from typing import cast

import httpx
import pytest

from hakimi_analysis.access import AccessManager
from hakimi_analysis.app import create_app, stream_run_events
from hakimi_analysis.models import (
    AnalysisCandidate,
    CandidateParameters,
    EvidenceSpan,
    EvidenceType,
    RunStage,
    Segment,
)
from hakimi_analysis.observability import ALLOWED_LOG_FIELDS
from hakimi_analysis.pipeline import EmitCallback, PipelineFailure, PipelineOutput
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


class TriggerCapturePipeline:
    def __init__(self) -> None:
        self.received_trigger: float | None | object = object()

    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float | None,
        emit: EmitCallback,
    ) -> PipelineOutput:
        del source, emit
        self.received_trigger = trigger_seconds
        return PipelineOutput(candidates=[], empty_reason="no_evidence")


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


class SecretFailurePipeline:
    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float,
        emit: EmitCallback,
    ) -> PipelineOutput:
        raise PipelineFailure(
            "provider_error",
            "provider-secret-response-must-not-be-logged",
            retryable=False,
        )


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


def make_test_access() -> AccessManager:
    return AccessManager(
        cookie_secret="test-cookie-secret-with-at-least-32-bytes",
        judge_access_code="test-judge-code",
        public_concurrency=1,
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
    app = create_app(
        catalog=source_catalog(tmp_path), pipeline=SuccessfulPipeline(), access=make_test_access()
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        sources = await client.get("/api/v1/sources")
        assert sources.status_code == 200
        assert sources.json() == [
            {
                "id": "legacy-arm-workout",
                "title": "本地联调视频",
                "media_url": "/api/v1/sources/legacy-arm-workout/media",
                "duration_seconds": 54.0,
                "origin_url": None,
            }
        ]

        created = await client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "legacy-arm-workout"},
        )
        assert created.status_code == 202
        assert created.json()["trigger_seconds"] is None
        run_id = created.json()["id"]

        completed = await wait_for_status(client, run_id, "completed")
        assert completed["candidates"][0]["name"] == "拖拽弯举"  # type: ignore[index]

        events = await client.get(f"/api/v1/analysis-runs/{run_id}/events")
        assert events.status_code == 200
        assert "event: run.completed" in events.text


@pytest.mark.asyncio
async def test_legacy_trigger_is_recorded_but_never_forwarded_to_analysis(
    tmp_path: Path,
) -> None:
    pipeline = TriggerCapturePipeline()
    app = create_app(catalog=source_catalog(tmp_path), pipeline=pipeline, access=make_test_access())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        created = await client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "legacy-arm-workout", "trigger_seconds": 999},
        )
        completed = await wait_for_status(client, created.json()["id"], "completed")

    assert created.status_code == 202
    assert completed["trigger_seconds"] == 999
    assert pipeline.received_trigger is None


@pytest.mark.asyncio
async def test_unknown_source_is_rejected_without_starting_a_run(tmp_path: Path) -> None:
    app = create_app(
        catalog=source_catalog(tmp_path), pipeline=SuccessfulPipeline(), access=make_test_access()
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
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
    app = create_app(catalog=catalog, pipeline=SuccessfulPipeline(), access=make_test_access())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
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
    app = create_app(
        catalog=source_catalog(tmp_path), pipeline=SlowPipeline(), access=make_test_access()
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
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


@pytest.mark.asyncio
async def test_run_timeout_is_an_explicit_failure(tmp_path: Path) -> None:
    app = create_app(
        catalog=source_catalog(tmp_path),
        pipeline=SlowPipeline(),
        timeout_seconds=0.01,
        access=make_test_access(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        created = await client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "legacy-arm-workout", "trigger_seconds": 45},
        )
        failed = await wait_for_status(client, created.json()["id"], "failed")

    assert failed["stage"] == "failed"
    assert failed["error"] == {
        "code": "timeout",
        "message": "动作分析超时，请重试",
        "retryable": True,
    }


@pytest.mark.asyncio
async def test_closing_sse_stream_cancels_an_in_flight_run(tmp_path: Path) -> None:
    app = create_app(
        catalog=source_catalog(tmp_path), pipeline=SlowPipeline(), access=make_test_access()
    )
    manager = app.state.run_manager
    source = app.state.source_catalog.get("legacy-arm-workout")
    created = await manager.create(source, 45)

    async def still_connected() -> bool:
        return False

    stream = stream_run_events(manager, created.id, still_connected)
    first_event = await anext(stream)
    assert "event: run.started" in first_event
    await stream.aclose()

    assert manager.get(created.id).status == "cancelled"
    await manager.close()


@pytest.mark.asyncio
async def test_runtime_logs_keep_only_allowlisted_diagnostic_fields(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app(
        catalog=source_catalog(tmp_path),
        pipeline=SecretFailurePipeline(),
        access=make_test_access(),
    )
    with caplog.at_level(logging.INFO, logger="hakimi_analysis.runs"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            created = await client.post(
                "/api/v1/analysis-runs",
                json={"source_id": "legacy-arm-workout", "trigger_seconds": 45},
            )
            await wait_for_status(client, created.json()["id"], "failed")

    messages = [
        record.getMessage() for record in caplog.records if record.name == "hakimi_analysis.runs"
    ]
    payloads = [json.loads(message) for message in messages]
    assert payloads
    assert all(set(payload) <= ALLOWED_LOG_FIELDS for payload in payloads)
    assert any(payload.get("error_code") == "provider_error" for payload in payloads)
    assert all("provider-secret-response-must-not-be-logged" not in message for message in messages)


@pytest.mark.asyncio
async def test_provider_failure_returns_only_a_safe_public_message(tmp_path: Path) -> None:
    app = create_app(
        catalog=source_catalog(tmp_path),
        pipeline=SecretFailurePipeline(),
        access=make_test_access(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        created = await client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "legacy-arm-workout", "trigger_seconds": 45},
        )
        failed = await wait_for_status(client, created.json()["id"], "failed")

    assert failed["error"] == {
        "code": "provider_error",
        "message": "动作分析暂时不可用，请稍后重试",
        "retryable": False,
    }
    assert "provider-secret-response-must-not-be-logged" not in str(failed)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path_suffix"),
    [
        ("GET", ""),
        ("DELETE", ""),
        ("GET", "/events"),
    ],
)
async def test_terminal_run_is_unavailable_from_every_http_path_after_ttl(
    tmp_path: Path,
    method: str,
    path_suffix: str,
) -> None:
    app = create_app(
        catalog=source_catalog(tmp_path),
        pipeline=SuccessfulPipeline(),
        access=make_test_access(),
        ttl_seconds=1,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        created = await client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "legacy-arm-workout", "trigger_seconds": 45},
        )
        run_id = created.json()["id"]
        await wait_for_status(client, run_id, "completed")
        await asyncio.sleep(1.05)

        response = await client.request(
            method,
            f"/api/v1/analysis-runs/{run_id}{path_suffix}",
        )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_manager_event_stream_prunes_a_terminal_run_after_ttl(tmp_path: Path) -> None:
    app = create_app(
        catalog=source_catalog(tmp_path),
        pipeline=SuccessfulPipeline(),
        access=make_test_access(),
        ttl_seconds=1,
    )
    manager = app.state.run_manager
    source = app.state.source_catalog.get("legacy-arm-workout")
    created = await manager.create(source, 45)
    for _ in range(100):
        if manager.get(created.id).status == "completed":
            break
        await asyncio.sleep(0.001)
    else:
        raise AssertionError("run did not complete")
    await asyncio.sleep(1.05)

    events = manager.events(created.id)
    with pytest.raises(KeyError):
        await anext(events)
    await events.aclose()
    await manager.close()
