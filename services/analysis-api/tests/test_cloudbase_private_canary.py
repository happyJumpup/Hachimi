from pathlib import Path
from runpy import run_path
from typing import Any

import httpx
import pytest


def test_private_canary_consumes_and_closes_a_terminal_sse_response() -> None:
    namespace: dict[str, Any] = run_path(
        str(Path(__file__).parents[3] / "deploy" / "cloudbase" / "private-canary.py")
    )
    credential_type = namespace["TemporaryCredential"]
    client_type = namespace["SignedGatewayClient"]
    gateway = client_type(
        environment_id="example-environment",
        service_name="trainpal-demo",
        credential=credential_type(
            secret_id="temporary-id",
            secret_key="temporary-key",
            token="temporary-token",
        ),
        timeout_seconds=10,
    )
    gateway.close()
    responses: list[httpx.Response] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"].startswith("TC3-HMAC-SHA256 ")
        response = httpx.Response(
            200,
            content=(
                b"event: run.started\n"
                b"data: {}\n\n"
                b"event: run.completed\n"
                b"data: {}\n\n"
            ),
        )
        responses.append(response)
        return response

    gateway._client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        status, event_types, terminal = gateway.stream_events(
            "/api/v1/analysis-runs/example/events"
        )
    finally:
        gateway.close()

    assert status == 200
    assert event_types == ["run.started", "run.completed"]
    assert terminal is True
    assert responses[0].is_closed


def test_private_canary_routes_every_request_to_the_zero_traffic_candidate(
    tmp_path: Path,
) -> None:
    namespace: dict[str, Any] = run_path(
        str(Path(__file__).parents[3] / "deploy" / "cloudbase" / "private-canary.py")
    )
    credential_type = namespace["TemporaryCredential"]
    client_type = namespace["SignedGatewayClient"]
    gateway = client_type(
        environment_id="example-environment",
        service_name="trainpal-demo",
        credential=credential_type(
            secret_id="temporary-id",
            secret_key="temporary-key",
            token="temporary-token",
        ),
        timeout_seconds=10,
        routing_header_name="X-TrainPal-Canary",
        routing_header_value="private-route-token",
    )
    gateway.close()
    observed_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-TrainPal-Canary"] == "private-route-token"
        observed_paths.append(request.url.path)
        if request.url.path.endswith("/events"):
            return httpx.Response(
                200,
                content=b"event: run.completed\ndata: {}\n\n",
            )
        return httpx.Response(200, json={})

    media = tmp_path / "canary.mp4"
    media.write_bytes(b"video")
    gateway._client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        gateway.request_json("GET", "/api/v1/capabilities")
        gateway.upload_video("/api/v1/analysis-runs/local", media)
        gateway.stream_events("/api/v1/analysis-runs/example/events")
    finally:
        gateway.close()

    assert observed_paths == [
        "/v1/cloudrun/trainpal-demo/api/v1/capabilities",
        "/v1/cloudrun/trainpal-demo/api/v1/analysis-runs/local",
        "/v1/cloudrun/trainpal-demo/api/v1/analysis-runs/example/events",
    ]


def test_private_canary_verifies_the_spa_shell_and_a_built_asset() -> None:
    namespace: dict[str, Any] = run_path(
        str(Path(__file__).parents[3] / "deploy" / "cloudbase" / "private-canary.py")
    )
    credential_type = namespace["TemporaryCredential"]
    client_type = namespace["SignedGatewayClient"]
    gateway = client_type(
        environment_id="example-environment",
        service_name="trainpal-demo",
        credential=credential_type(
            secret_id="temporary-id",
            secret_key="temporary-key",
            token="temporary-token",
        ),
        timeout_seconds=10,
        routing_header_name="X-TrainPal-Canary",
        routing_header_value="private-route-token",
    )
    gateway.close()
    observed_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-TrainPal-Canary"] == "private-route-token"
        observed_paths.append(request.url.path)
        if request.url.path.endswith("/assets/index-candidate.js"):
            return httpx.Response(
                200,
                headers={"content-type": "text/javascript"},
                content=b"globalThis.trainpal=true",
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=(
                '<!doctype html><div id="app"></div>'
                '<script type="module" src="/assets/index-candidate.js"></script>'
            ),
        )

    gateway._client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        receipt = namespace["_verify_spa_candidate"](gateway)
    finally:
        gateway.close()

    assert receipt == {"shell_http_status": 200, "asset_http_status": 200}
    assert observed_paths == [
        "/v1/cloudrun/trainpal-demo/",
        "/v1/cloudrun/trainpal-demo/assets/index-candidate.js",
    ]


def test_private_canary_cli_accepts_a_complete_routing_header_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace: dict[str, Any] = run_path(
        str(Path(__file__).parents[3] / "deploy" / "cloudbase" / "private-canary.py")
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "private-canary.py",
            "--environment-id",
            "example-environment",
            "--media",
            "canary.mp4",
            "--output",
            "receipt.json",
            "--routing-header-name",
            "X-TrainPal-Canary",
            "--routing-header-value-stdin",
            "--expected-commit-sha",
            "a" * 40,
        ],
    )

    args = namespace["parse_args"]()

    assert args.routing_header_name == "X-TrainPal-Canary"
    assert args.routing_header_value_stdin is True
    assert args.expected_commit_sha == "a" * 40


def test_private_canary_cli_requires_candidate_identity_and_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace: dict[str, Any] = run_path(
        str(Path(__file__).parents[3] / "deploy" / "cloudbase" / "private-canary.py")
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "private-canary.py",
            "--environment-id",
            "example-environment",
            "--media",
            "canary.mp4",
            "--output",
            "receipt.json",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        namespace["parse_args"]()

    assert exc_info.value.code == 2


def test_private_canary_rejects_a_response_from_the_wrong_release() -> None:
    namespace: dict[str, Any] = run_path(
        str(Path(__file__).parents[3] / "deploy" / "cloudbase" / "private-canary.py")
    )

    with pytest.raises(namespace["CanaryError"], match="release identity"):
        namespace["_verify_release_identity"](
            {"status": "ok", "release_sha": "b" * 40},
            "a" * 40,
        )


def test_private_canary_accepts_the_exact_release_identity() -> None:
    namespace: dict[str, Any] = run_path(
        str(Path(__file__).parents[3] / "deploy" / "cloudbase" / "private-canary.py")
    )

    assert namespace["_verify_release_identity"](
        {"status": "ok", "release_sha": "a" * 40},
        "a" * 40,
    ) == {"commit_sha": "a" * 40}


def test_private_canary_requires_the_full_production_readiness_contract() -> None:
    namespace: dict[str, Any] = run_path(
        str(Path(__file__).parents[3] / "deploy" / "cloudbase" / "private-canary.py")
    )

    assert namespace["_verify_readiness"]({"status": "ready"}) == {
        "status": "ready"
    }
    with pytest.raises(namespace["CanaryError"], match="readiness"):
        namespace["_verify_readiness"](
            {"status": "not_ready", "code": "ffmpeg_audit_failed"}
        )


def test_private_canary_rejects_an_incomplete_routing_header_pair() -> None:
    namespace: dict[str, Any] = run_path(
        str(Path(__file__).parents[3] / "deploy" / "cloudbase" / "private-canary.py")
    )
    credential_type = namespace["TemporaryCredential"]
    client_type = namespace["SignedGatewayClient"]

    with pytest.raises(namespace["CanaryError"], match="routing header pair"):
        client_type(
            environment_id="example-environment",
            service_name="trainpal-demo",
            credential=credential_type(
                secret_id="temporary-id",
                secret_key="temporary-key",
                token="temporary-token",
            ),
            timeout_seconds=10,
            routing_header_name="X-TrainPal-Canary",
        )


def test_private_canary_verifies_anonymous_gymti_local_fallback() -> None:
    namespace: dict[str, Any] = run_path(
        str(Path(__file__).parents[3] / "deploy" / "cloudbase" / "private-canary.py")
    )
    credential_type = namespace["TemporaryCredential"]
    client_type = namespace["SignedGatewayClient"]
    gateway = client_type(
        environment_id="example-environment",
        service_name="trainpal-demo",
        credential=credential_type(
            secret_id="temporary-id",
            secret_key="temporary-key",
            token="temporary-token",
        ),
        timeout_seconds=10,
    )
    gateway.close()

    def handler(request: httpx.Request) -> httpx.Response:
        payload = request.read().decode("utf-8")
        assert '"candidate_question_ids":["q01_energy_after_work"]' in payload
        return httpx.Response(
            200,
            json={
                "question_id": "q01_energy_after_work",
                "source": "local_fallback",
                "model": None,
                "version": "gymti-questionnaire.v1",
            },
        )

    gateway._client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        receipt = namespace["_verify_anonymous_gymti_fallback"](gateway)
    finally:
        gateway.close()

    assert receipt == {
        "next_question_source": "local_fallback",
        "model": None,
        "contract_version": "gymti-questionnaire.v1",
    }
