import asyncio
import json
import logging
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi.routing import APIRoute

from hakimi_analysis.access import AccessManager
from hakimi_analysis.app import create_app
from hakimi_analysis.models import (
    AnalysisCandidate,
    CandidateParameters,
    EvidenceSpan,
    EvidenceType,
    Segment,
    SegmentRole,
)
from hakimi_analysis.observability import ALLOWED_LOG_FIELDS
from hakimi_analysis.pipeline import EmitCallback, PipelineFailure, PipelineOutput
from hakimi_analysis.sources import VideoSource

LOCAL_SOURCE_ID = "local:62f31c4b-bd1c-4b6a-8ad8-c6121b56135a"
SECOND_LOCAL_SOURCE_ID = "local:a55df85a-e80d-4c85-807f-cd4a9c26f7e5"


def make_test_access(*, public_attempt_limit: int = 1) -> AccessManager:
    return AccessManager(
        cookie_secret="test-cookie-secret-with-at-least-32-bytes",
        judge_access_code="test-judge-code",
        public_concurrency=1,
        public_attempt_limit=public_attempt_limit,
    )


async def wait_for_status(client: httpx.AsyncClient, run_id: str, status: str) -> dict[str, object]:
    for _ in range(100):
        response = await client.get(f"/api/v1/analysis-runs/{run_id}")
        payload = cast(dict[str, object], response.json())
        if payload["status"] == status:
            return payload
        await asyncio.sleep(0.01)
    raise AssertionError(f"run did not reach {status}")


class RelativeCandidatePipeline:
    def __init__(self) -> None:
        self.source: VideoSource | None = None

    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float | None,
        emit: EmitCallback,
    ) -> PipelineOutput:
        del trigger_seconds, emit
        self.source = source
        assert source.path.is_file()
        return PipelineOutput(
            candidates=[
                AnalysisCandidate(
                    id="candidate-local",
                    name="深蹲",
                    source_id=source.id,
                    segment=Segment(start_seconds=1, end_seconds=3),
                    parameters=CandidateParameters(),
                    evidence=[
                        EvidenceSpan(
                            type=EvidenceType.VISUAL,
                            start_seconds=1.25,
                            end_seconds=2.75,
                        )
                    ],
                    segment_role=SegmentRole.UNKNOWN,
                    needs_confirmation=False,
                )
            ]
        )


class BlockingCapturePipeline:
    def __init__(self) -> None:
        self.sources: list[VideoSource] = []
        self.cancelled_source_ids: set[str] = set()

    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float | None,
        emit: EmitCallback,
    ) -> PipelineOutput:
        del trigger_seconds, emit
        self.sources.append(source)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled_source_ids.add(source.id)
            raise
        return PipelineOutput()


class SecretFailureCapturePipeline:
    def __init__(self) -> None:
        self.source: VideoSource | None = None

    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float | None,
        emit: EmitCallback,
    ) -> PipelineOutput:
        del trigger_seconds, emit
        self.source = source
        raise PipelineFailure(
            "provider_error",
            f"private-filename.mp4 {source.path} secret transcript text",
            retryable=True,
        )


@pytest.mark.asyncio
async def test_capabilities_publish_the_configured_local_upload_limits(tmp_path: Path) -> None:
    app = create_app(
        access=make_test_access(),
        local_upload_enabled=True,
        local_analysis_max_seconds=45,
        local_upload_max_bytes=12_345,
        local_upload_temp_root=tmp_path,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get("/api/v1/capabilities")

    assert response.status_code == 200
    assert response.json() == {
        "local_upload_enabled": True,
        "local_analysis_max_seconds": 45.0,
        "local_upload_max_bytes": 12_345,
    }
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_disabled_local_upload_is_fail_closed_without_writing_media(tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads"
    app = create_app(
        pipeline=RelativeCandidatePipeline(),
        access=make_test_access(),
        local_upload_enabled=False,
        local_upload_temp_root=upload_root,
    )
    assert "/api/v1/analysis-runs/local" not in app.openapi()["paths"]
    disabled_route = next(
        route
        for route in app.routes
        if getattr(route, "path", None) == "/api/v1/analysis-runs/local"
        and "POST" in getattr(route, "methods", set())
    )
    assert isinstance(disabled_route, APIRoute)
    assert disabled_route.body_field is None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("source.mp4", b"video-bytes", "video/mp4")},
            data={"local_source_id": LOCAL_SOURCE_ID},
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "本地视频导入未启用"}
    assert not upload_root.exists()


@pytest.mark.asyncio
async def test_local_range_upload_returns_absolute_candidate_times_and_cleans_media(
    tmp_path: Path,
) -> None:
    pipeline = RelativeCandidatePipeline()
    upload_root = tmp_path / "uploads"
    app = create_app(
        pipeline=pipeline,
        access=make_test_access(),
        local_analysis_max_seconds=60,
        local_upload_max_bytes=1024,
        local_upload_temp_root=upload_root,
        local_duration_probe=lambda _: 30.0,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        created = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("private-filename.mp4", b"video-bytes", "video/mp4")},
            data={
                "local_source_id": LOCAL_SOURCE_ID,
                "range_start_seconds": "10",
                "range_end_seconds": "20",
            },
        )
        assert created.status_code == 202
        completed = await wait_for_status(client, created.json()["id"], "completed")

    assert completed["source_duration_seconds"] == 30.0
    assert completed["processed_seconds"] == 10.0
    assert completed["coverage_status"] == "complete"
    assert completed["coverage_gaps"] == []
    candidate = completed["candidates"][0]  # type: ignore[index]
    assert candidate["source_id"] == LOCAL_SOURCE_ID
    assert candidate["segment"] == {"start_seconds": 11.0, "end_seconds": 13.0}
    assert candidate["evidence"] == [
        {"type": "visual", "start_seconds": 11.25, "end_seconds": 12.75}
    ]
    assert pipeline.source is not None
    assert pipeline.source.analysis_start_seconds == 10.0
    assert pipeline.source.analysis_end_seconds == 20.0
    assert pipeline.source.analysis_duration_seconds == 10.0
    for _ in range(100):
        if not pipeline.source.path.exists():
            break
        await asyncio.sleep(0.01)
    assert not pipeline.source.path.exists()
    assert not upload_root.exists() or list(upload_root.iterdir()) == []


@pytest.mark.asyncio
async def test_local_upload_rejects_media_outside_the_video_allowlist(tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads"
    app = create_app(
        pipeline=RelativeCandidatePipeline(),
        access=make_test_access(),
        local_upload_temp_root=upload_root,
        local_duration_probe=lambda _: 10.0,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("payload.bin", b"not-video", "application/octet-stream")},
            data={"local_source_id": LOCAL_SOURCE_ID},
        )

    assert response.status_code == 415
    assert response.json() == {"detail": "暂不支持这种视频格式"}
    assert not upload_root.exists()


@pytest.mark.asyncio
async def test_local_upload_enforces_the_streamed_byte_limit_and_cleans_partial_file(
    tmp_path: Path,
) -> None:
    upload_root = tmp_path / "uploads"
    probe_called = False

    def duration_probe(_: Path) -> float:
        nonlocal probe_called
        probe_called = True
        return 10.0

    app = create_app(
        pipeline=RelativeCandidatePipeline(),
        access=make_test_access(),
        local_upload_max_bytes=4,
        local_upload_temp_root=upload_root,
        local_duration_probe=duration_probe,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("too-large.mp4", b"12345", "video/mp4")},
            data={"local_source_id": LOCAL_SOURCE_ID},
        )

    assert response.status_code == 413
    assert response.json() == {"detail": "视频文件超过上传大小限制"}
    assert probe_called is False
    assert list(upload_root.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("form_overrides", "expected_detail"),
    [
        ({"local_source_id": "local:not-a-uuid"}, "本地来源标识无效"),
        ({"range_start_seconds": "5"}, "分析范围必须同时包含开始和结束时间"),
        ({"range_end_seconds": "15"}, "分析范围必须同时包含开始和结束时间"),
        ({"range_start_seconds": "10", "range_end_seconds": "10"}, "分析范围超出视频时长"),
        ({"range_start_seconds": "0", "range_end_seconds": "20.1"}, "分析范围超出视频时长"),
    ],
)
async def test_local_upload_rejects_invalid_ids_and_out_of_bounds_ranges(
    tmp_path: Path,
    form_overrides: dict[str, str],
    expected_detail: str,
) -> None:
    upload_root = tmp_path / "uploads"
    app = create_app(
        pipeline=RelativeCandidatePipeline(),
        access=make_test_access(),
        local_upload_temp_root=upload_root,
        local_duration_probe=lambda _: 20.0,
    )
    form = {"local_source_id": LOCAL_SOURCE_ID, **form_overrides}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("source.mp4", b"video-bytes", "video/mp4")},
            data=form,
        )

    assert response.status_code == 422
    assert response.json() == {"detail": expected_detail}
    assert not upload_root.exists() or list(upload_root.iterdir()) == []


@pytest.mark.asyncio
async def test_local_upload_rejects_unreadable_and_over_limit_durations(tmp_path: Path) -> None:
    for duration_probe, expected_detail in (
        (lambda _: (_ for _ in ()).throw(RuntimeError("decoder detail")), "无法读取视频时长"),
        (lambda _: float("nan"), "无法读取视频时长"),
        (lambda _: float("inf"), "无法读取视频时长"),
        (lambda _: 60.1, "视频时长超过当前分析上限"),
    ):
        upload_root = tmp_path / expected_detail
        app = create_app(
            pipeline=RelativeCandidatePipeline(),
            access=make_test_access(),
            local_analysis_max_seconds=60,
            local_upload_temp_root=upload_root,
            local_duration_probe=duration_probe,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            response = await client.post(
                "/api/v1/analysis-runs/local",
                files={"media": ("source.mp4", b"video-bytes", "video/mp4")},
                data={"local_source_id": LOCAL_SOURCE_ID},
            )

        assert response.status_code == 422
        assert response.json() == {"detail": expected_detail}
        assert list(upload_root.iterdir()) == []


def test_direct_app_configuration_rejects_non_finite_local_analysis_limit() -> None:
    with pytest.raises(ValueError):
        create_app(local_analysis_max_seconds=float("nan"))


@pytest.mark.asyncio
async def test_explicit_delete_cancels_local_run_and_cleans_the_uploaded_media(
    tmp_path: Path,
) -> None:
    pipeline = BlockingCapturePipeline()
    upload_root = tmp_path / "uploads"
    app = create_app(
        pipeline=pipeline,
        access=make_test_access(),
        local_upload_temp_root=upload_root,
        local_duration_probe=lambda _: 20.0,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        created = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("private-filename.mp4", b"video-bytes", "video/mp4")},
            data={"local_source_id": LOCAL_SOURCE_ID},
        )
        for _ in range(100):
            if pipeline.sources:
                break
            await asyncio.sleep(0.01)
        assert pipeline.sources
        source_path = pipeline.sources[0].path
        assert source_path.is_file()

        cancelled = await client.delete(f"/api/v1/analysis-runs/{created.json()['id']}")

    assert cancelled.status_code == 202
    assert cancelled.json()["status"] == "cancelled"
    assert LOCAL_SOURCE_ID in pipeline.cancelled_source_ids
    assert not source_path.exists()
    assert list(upload_root.iterdir()) == []


@pytest.mark.asyncio
async def test_timeout_cleans_local_media_and_keeps_a_retryable_safe_error(tmp_path: Path) -> None:
    pipeline = BlockingCapturePipeline()
    upload_root = tmp_path / "uploads"
    app = create_app(
        pipeline=pipeline,
        access=make_test_access(),
        timeout_seconds=0.01,
        local_upload_temp_root=upload_root,
        local_duration_probe=lambda _: 20.0,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        created = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("private-filename.mp4", b"video-bytes", "video/mp4")},
            data={"local_source_id": LOCAL_SOURCE_ID},
        )
        failed = await wait_for_status(client, created.json()["id"], "failed")

    assert failed["error"] == {
        "code": "timeout",
        "message": "动作分析超时，请重试",
        "retryable": True,
    }
    assert pipeline.sources
    assert not pipeline.sources[0].path.exists()
    assert list(upload_root.iterdir()) == []


@pytest.mark.asyncio
async def test_failure_cleans_local_media_and_never_logs_names_paths_or_transcripts(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    pipeline = SecretFailureCapturePipeline()
    upload_root = tmp_path / "uploads"
    app = create_app(
        pipeline=pipeline,
        access=make_test_access(),
        local_upload_temp_root=upload_root,
        local_duration_probe=lambda _: 20.0,
    )
    with caplog.at_level(logging.INFO, logger="hakimi_analysis.runs"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            created = await client.post(
                "/api/v1/analysis-runs/local",
                files={"media": ("private-filename.mp4", b"video-bytes", "video/mp4")},
                data={"local_source_id": LOCAL_SOURCE_ID},
            )
            failed = await wait_for_status(client, created.json()["id"], "failed")

    assert failed["error"] == {
        "code": "provider_error",
        "message": "动作分析暂时不可用，请稍后重试",
        "retryable": True,
    }
    assert pipeline.source is not None
    sensitive_values = (
        "private-filename.mp4",
        str(pipeline.source.path),
        "secret transcript text",
    )
    messages = [record.getMessage() for record in caplog.records]
    assert messages
    assert all(not any(value in message for value in sensitive_values) for message in messages)
    payloads = [json.loads(message) for message in messages]
    assert all(set(payload) <= ALLOWED_LOG_FIELDS for payload in payloads)
    assert not pipeline.source.path.exists()
    assert list(upload_root.iterdir()) == []


@pytest.mark.asyncio
async def test_replacing_the_local_source_cancels_only_the_previous_run(tmp_path: Path) -> None:
    pipeline = BlockingCapturePipeline()
    upload_root = tmp_path / "uploads"
    app = create_app(
        pipeline=pipeline,
        access=make_test_access(public_attempt_limit=10),
        local_upload_temp_root=upload_root,
        local_duration_probe=lambda _: 20.0,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        first = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("first.mp4", b"first-video", "video/mp4")},
            data={"local_source_id": LOCAL_SOURCE_ID},
        )
        for _ in range(100):
            if len(pipeline.sources) == 1:
                break
            await asyncio.sleep(0.01)
        first_path = pipeline.sources[0].path

        second = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("second.mp4", b"second-video", "video/mp4")},
            data={"local_source_id": SECOND_LOCAL_SOURCE_ID},
        )
        for _ in range(100):
            if len(pipeline.sources) == 2:
                break
            await asyncio.sleep(0.01)
        old_view = await client.get(f"/api/v1/analysis-runs/{first.json()['id']}")

        assert first.status_code == 202
        assert second.status_code == 202
        assert old_view.json()["status"] == "cancelled"
        assert LOCAL_SOURCE_ID in pipeline.cancelled_source_ids
        assert not first_path.exists()
        assert pipeline.sources[1].path.is_file()

        await client.delete(f"/api/v1/analysis-runs/{second.json()['id']}")

    assert not pipeline.sources[1].path.exists()
    assert list(upload_root.iterdir()) == []


@pytest.mark.asyncio
async def test_second_request_for_the_same_source_does_not_cancel_the_active_run(
    tmp_path: Path,
) -> None:
    pipeline = BlockingCapturePipeline()
    upload_root = tmp_path / "uploads"
    app = create_app(
        pipeline=pipeline,
        access=make_test_access(public_attempt_limit=10),
        local_upload_temp_root=upload_root,
        local_duration_probe=lambda _: 20.0,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        first = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("first.mp4", b"first-video", "video/mp4")},
            data={"local_source_id": LOCAL_SOURCE_ID},
        )
        for _ in range(100):
            if pipeline.sources:
                break
            await asyncio.sleep(0.01)
        first_path = pipeline.sources[0].path

        rejected = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("retry.mp4", b"retry-video", "video/mp4")},
            data={"local_source_id": LOCAL_SOURCE_ID},
        )
        first_view = await client.get(f"/api/v1/analysis-runs/{first.json()['id']}")

        assert rejected.status_code == 429
        assert first_view.json()["status"] == "running"
        assert pipeline.cancelled_source_ids == set()
        assert first_path.is_file()
        assert len(list(upload_root.iterdir())) == 1

        await client.delete(f"/api/v1/analysis-runs/{first.json()['id']}")

    assert not first_path.exists()
    assert list(upload_root.iterdir()) == []
