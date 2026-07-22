from pathlib import Path
from runpy import run_path
from typing import Any

import httpx


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
