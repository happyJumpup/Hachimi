import asyncio
from pathlib import Path

import httpx
import pytest

from hakimi_analysis.access import AccessManager
from hakimi_analysis.app import create_app
from hakimi_analysis.models import RunStage
from hakimi_analysis.pipeline import EmitCallback, PipelineOutput
from hakimi_analysis.sources import SourceCatalog, VideoSource


def access_manager(*, public_concurrency: int = 0) -> AccessManager:
    return AccessManager(
        cookie_secret="cookie-signing-secret-with-at-least-32-bytes",
        judge_access_code="judge-demo-code",
        judge_concurrency=2,
        public_concurrency=public_concurrency,
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


class ImmediatePipeline:
    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float,
        emit: EmitCallback,
    ) -> PipelineOutput:
        return PipelineOutput(candidates=[], empty_reason="no_evidence")


def two_source_catalog(tmp_path: Path) -> SourceCatalog:
    sources: list[VideoSource] = []
    for source_id in ("arm-01", "arm-02"):
        path = tmp_path / f"{source_id}.mp4"
        path.write_bytes(b"not-read-by-test-pipeline")
        sources.append(
            VideoSource(
                id=source_id,
                title=source_id,
                path=path,
                duration_seconds=60,
            )
        )
    return SourceCatalog(sources)


@pytest.mark.asyncio
async def test_access_session_uses_a_secure_signed_cookie_and_no_store() -> None:
    app = create_app(access=access_manager())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get("/api/v1/access/session")

    assert response.status_code == 200
    assert response.json() == {
        "tier": "public",
        "can_analyze": False,
        "retry_after_seconds": None,
    }
    assert response.headers["cache-control"] == "no-store"
    cookie = response.headers["set-cookie"]
    assert "hachimi_access=" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie
    assert "Max-Age=43200" in cookie
    assert "judge-demo-code" not in cookie


@pytest.mark.asyncio
async def test_valid_access_code_upgrades_the_existing_session_to_judge() -> None:
    app = create_app(access=access_manager())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        await client.get("/api/v1/access/session")
        response = await client.post(
            "/api/v1/access/session",
            json={"access_code": "judge-demo-code"},
            headers={"Origin": "https://test"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "tier": "judge",
        "can_analyze": True,
        "retry_after_seconds": None,
    }
    assert response.headers["cache-control"] == "no-store"
    assert "judge-demo-code" not in response.headers["set-cookie"]


@pytest.mark.asyncio
async def test_invalid_access_code_has_one_safe_failure_response() -> None:
    app = create_app(access=access_manager())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.post(
            "/api/v1/access/session",
            json={"access_code": "wrong"},
            headers={"Origin": "https://test"},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "体验码无效"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_access_code_attempts_are_rate_limited_by_client_ip() -> None:
    access = AccessManager(
        cookie_secret="cookie-signing-secret-with-at-least-32-bytes",
        judge_access_code="judge-demo-code",
        upgrade_attempt_limit=2,
        upgrade_attempt_window_seconds=600,
    )
    app = create_app(access=access)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        first = await client.post(
            "/api/v1/access/session",
            json={"access_code": "wrong-one"},
            headers={"Origin": "https://test"},
        )
        second = await client.post(
            "/api/v1/access/session",
            json={"access_code": "wrong-two"},
            headers={"Origin": "https://test"},
        )
        blocked = await client.post(
            "/api/v1/access/session",
            json={"access_code": "judge-demo-code"},
            headers={"Origin": "https://test"},
        )

    assert first.status_code == 401
    assert second.status_code == 401
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == "600"
    assert blocked.json() == {"detail": "体验码尝试过于频繁，请稍后重试"}


@pytest.mark.asyncio
async def test_access_and_analysis_payload_strings_have_bounded_lengths() -> None:
    app = create_app(access=access_manager())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        access_response = await client.post(
            "/api/v1/access/session",
            json={"access_code": "x" * 257},
            headers={"Origin": "https://test"},
        )
        analysis_response = await client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "x" * 129, "trigger_seconds": 10},
            headers={"Origin": "https://test"},
        )

    assert access_response.status_code == 422
    assert analysis_response.status_code == 422


@pytest.mark.asyncio
async def test_production_rejects_cross_origin_session_changes() -> None:
    app = create_app(access=access_manager(), app_env="production")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.post(
            "/api/v1/access/session",
            json={"access_code": "judge-demo-code"},
            headers={"Origin": "https://attacker.example"},
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "请求来源无效"}


@pytest.mark.asyncio
async def test_judge_and_public_capacity_are_isolated_and_full_pool_returns_429(
    tmp_path: Path,
) -> None:
    access = AccessManager(
        cookie_secret="cookie-signing-secret-with-at-least-32-bytes",
        judge_access_code="judge-demo-code",
        judge_concurrency=1,
        public_concurrency=1,
    )
    app = create_app(
        catalog=two_source_catalog(tmp_path),
        pipeline=SlowPipeline(),
        access=access,
    )
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("203.0.113.1", 1001)),
            base_url="https://test",
        ) as public_one,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("203.0.113.2", 1002)),
            base_url="https://test",
        ) as public_two,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("203.0.113.3", 1003)),
            base_url="https://test",
        ) as judge,
    ):
        public_run = await public_one.post(
            "/api/v1/analysis-runs",
            json={"source_id": "arm-01", "trigger_seconds": 10},
        )
        await judge.get("/api/v1/access/session")
        await judge.post(
            "/api/v1/access/session",
            json={"access_code": "judge-demo-code"},
            headers={"Origin": "https://test"},
        )
        judge_run = await judge.post(
            "/api/v1/analysis-runs",
            json={"source_id": "arm-01", "trigger_seconds": 10},
        )
        rejected = await public_two.post(
            "/api/v1/analysis-runs",
            json={"source_id": "arm-02", "trigger_seconds": 10},
        )

        assert public_run.status_code == 202
        assert judge_run.status_code == 202
        assert rejected.status_code == 429
        assert rejected.headers["retry-after"] == "15"
        assert rejected.json() == {"detail": "真实动作分析暂时繁忙，请稍后重试"}

        await public_one.delete(f"/api/v1/analysis-runs/{public_run.json()['id']}")
        await judge.delete(f"/api/v1/analysis-runs/{judge_run.json()['id']}")


@pytest.mark.asyncio
async def test_public_session_is_limited_to_one_analysis_per_ten_minutes(tmp_path: Path) -> None:
    app = create_app(
        catalog=two_source_catalog(tmp_path),
        pipeline=ImmediatePipeline(),
        access=access_manager(public_concurrency=1),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        first = await client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "arm-01", "trigger_seconds": 10},
        )
        for _ in range(50):
            completed = await client.get(f"/api/v1/analysis-runs/{first.json()['id']}")
            if completed.json()["status"] == "completed":
                break
            await asyncio.sleep(0.01)
        second = await client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "arm-02", "trigger_seconds": 10},
        )

    assert first.status_code == 202
    assert second.status_code == 429
    assert second.headers["retry-after"] == "600"


@pytest.mark.asyncio
async def test_trusted_proxy_uses_one_forwarded_client_ip_for_rate_limits(
    tmp_path: Path,
) -> None:
    app = create_app(
        catalog=two_source_catalog(tmp_path),
        pipeline=ImmediatePipeline(),
        access=access_manager(public_concurrency=2),
        trusted_proxy_cidrs=["172.30.248.2/32"],
    )
    transport_one = httpx.ASGITransport(app=app, client=("172.30.248.2", 1001))
    transport_two = httpx.ASGITransport(app=app, client=("172.30.248.2", 1002))
    async with (
        httpx.AsyncClient(transport=transport_one, base_url="https://test") as first_client,
        httpx.AsyncClient(transport=transport_two, base_url="https://test") as second_client,
    ):
        first = await first_client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "arm-01", "trigger_seconds": 10},
            headers={"X-Forwarded-For": "203.0.113.1", "X-Real-IP": "203.0.113.1"},
        )
        for _ in range(50):
            completed = await first_client.get(f"/api/v1/analysis-runs/{first.json()['id']}")
            if completed.json()["status"] == "completed":
                break
            await asyncio.sleep(0.01)
        second = await second_client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "arm-02", "trigger_seconds": 10},
            headers={"X-Forwarded-For": "203.0.113.2", "X-Real-IP": "203.0.113.2"},
        )

    assert first.status_code == 202
    assert second.status_code == 202


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "forwarded_headers",
    [
        {},
        {"X-Forwarded-For": "203.0.113.1, 203.0.113.2"},
        {"X-Forwarded-For": "not-an-ip"},
        {"X-Forwarded-For": "203.0.113.1", "X-Real-IP": "203.0.113.2"},
    ],
)
async def test_trusted_proxy_rejects_missing_ambiguous_or_invalid_client_ip(
    tmp_path: Path,
    forwarded_headers: dict[str, str],
) -> None:
    app = create_app(
        catalog=two_source_catalog(tmp_path),
        pipeline=ImmediatePipeline(),
        access=access_manager(public_concurrency=1),
        trusted_proxy_cidrs=["172.30.248.2/32"],
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("172.30.248.2", 1001)),
        base_url="https://test",
    ) as client:
        response = await client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "arm-01", "trigger_seconds": 10},
            headers=forwarded_headers,
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "客户端地址无效"}


@pytest.mark.asyncio
async def test_untrusted_direct_client_cannot_bypass_ip_limit_with_forwarded_headers(
    tmp_path: Path,
) -> None:
    app = create_app(
        catalog=two_source_catalog(tmp_path),
        pipeline=ImmediatePipeline(),
        access=access_manager(public_concurrency=2),
        trusted_proxy_cidrs=["172.30.248.2/32"],
    )
    first_transport = httpx.ASGITransport(app=app, client=("198.51.100.10", 1001))
    second_transport = httpx.ASGITransport(app=app, client=("198.51.100.10", 1002))
    async with (
        httpx.AsyncClient(transport=first_transport, base_url="https://test") as first_client,
        httpx.AsyncClient(transport=second_transport, base_url="https://test") as second_client,
    ):
        first = await first_client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "arm-01", "trigger_seconds": 10},
            headers={"X-Forwarded-For": "203.0.113.1"},
        )
        for _ in range(50):
            completed = await first_client.get(f"/api/v1/analysis-runs/{first.json()['id']}")
            if completed.json()["status"] == "completed":
                break
            await asyncio.sleep(0.01)
        second = await second_client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "arm-02", "trigger_seconds": 10},
            headers={"X-Forwarded-For": "203.0.113.2"},
        )

    assert first.status_code == 202
    assert second.status_code == 429
    assert second.headers["retry-after"] == "600"


@pytest.mark.asyncio
async def test_starting_analysis_for_another_source_cancels_the_session_old_run(
    tmp_path: Path,
) -> None:
    app = create_app(
        catalog=two_source_catalog(tmp_path),
        pipeline=SlowPipeline(),
        access=access_manager(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        await client.get("/api/v1/access/session")
        await client.post(
            "/api/v1/access/session",
            json={"access_code": "judge-demo-code"},
            headers={"Origin": "https://test"},
        )
        first = await client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "arm-01", "trigger_seconds": 10},
        )
        second = await client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "arm-02", "trigger_seconds": 10},
        )
        old_view = await client.get(f"/api/v1/analysis-runs/{first.json()['id']}")

        assert first.status_code == 202
        assert second.status_code == 202
        assert old_view.json()["status"] == "cancelled"

        await client.delete(f"/api/v1/analysis-runs/{second.json()['id']}")
