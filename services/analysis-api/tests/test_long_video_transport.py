import ipaddress
from pathlib import Path

import pytest

from hakimi_analysis.benchmark.long_transport import (
    LongBenchmarkTransportError,
    LongCurlTransport,
    _parse_long_response,
    build_long_curl_base,
    require_long_proxy_for_fake_ip,
    require_long_success,
)


def test_long_transport_forces_explicit_socks5h_without_no_proxy_bypass() -> None:
    command = build_long_curl_base(
        "socks5h://127.0.0.1:7897",
        connect_timeout_seconds=30,
        timeout_seconds=180,
    )

    assert command[:5] == [
        "curl.exe",
        "--proxy",
        "socks5h://127.0.0.1:7897",
        "--noproxy",
        "",
    ]

    with pytest.raises(LongBenchmarkTransportError, match="proxy_must_be_socks5h"):
        build_long_curl_base(
            "http://127.0.0.1:7897",
            connect_timeout_seconds=30,
            timeout_seconds=180,
        )

    with pytest.raises(LongBenchmarkTransportError, match="fake_ip_requires_proxy"):
        require_long_proxy_for_fake_ip([ipaddress.ip_address("198.18.0.126")], None)


@pytest.mark.asyncio
async def test_long_multipart_has_http1_and_empty_expect_but_json_does_not(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: list[tuple[list[str], str | None]] = []

    async def completed_request(
        command: list[str],
        *,
        input_text: str | None,
    ) -> tuple[int, str]:
        captured.append((command, input_text))
        return (
            0,
            "{}\n__HAKIMI_LONG_HTTP_STATUS__:200"
            "\n__HAKIMI_LONG_FIRST_BYTE__:0.1"
            "\n__HAKIMI_LONG_TOTAL__:0.2",
        )

    monkeypatch.setattr(
        "hakimi_analysis.benchmark.long_transport._run_long_process",
        completed_request,
    )
    media = tmp_path / "probe.mp4"
    media.write_bytes(b"video")
    transport = LongCurlTransport(proxy_url="socks5h://127.0.0.1:7897")

    await transport.json_request("POST", "https://provider.example/json", headers={})
    await transport.multipart_request(
        "https://provider.example/upload",
        headers={},
        fields={},
        file_path=media,
        mime_type="video/mp4",
    )

    json_command, json_input = captured[0]
    multipart_command, multipart_input = captured[1]
    assert "--http1.1" not in json_command
    assert 'header = "Expect:"' not in str(json_input)
    assert "--http1.1" in multipart_command
    assert 'header = "Expect:"' in str(multipart_input)


def test_long_retry_after_propagates_to_the_outer_retry_policy() -> None:
    response = _parse_long_response(
        "{}\n__HAKIMI_LONG_HTTP_STATUS__:429"
        "\n__HAKIMI_LONG_FIRST_BYTE__:0.1"
        "\n__HAKIMI_LONG_TOTAL__:0.2"
        "\n__HAKIMI_LONG_RETRY_AFTER__:7"
    )

    with pytest.raises(LongBenchmarkTransportError) as captured:
        require_long_success(response)

    assert captured.value.code == "provider_error"
    assert captured.value.retryable is True
    assert captured.value.retry_after_seconds == 7


@pytest.mark.asyncio
async def test_long_network_error_keeps_only_safe_curl_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failed_request(
        command: list[str],
        *,
        input_text: str | None,
    ) -> tuple[int, str]:
        del command, input_text
        return 56, "untrusted provider response"

    monkeypatch.setattr(
        "hakimi_analysis.benchmark.long_transport._run_long_process",
        failed_request,
    )
    transport = LongCurlTransport(proxy_url="socks5h://127.0.0.1:7897")

    with pytest.raises(LongBenchmarkTransportError) as captured:
        await transport.json_request("POST", "https://provider.example/endpoint", headers={})

    assert captured.value.code == "network_error_curl_exit_56"
    assert "untrusted" not in captured.value.code
