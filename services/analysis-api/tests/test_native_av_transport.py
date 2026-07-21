import ipaddress
from pathlib import Path

import pytest

from hakimi_analysis.benchmark.transport import (
    BenchmarkTransportError,
    CurlTransport,
    build_curl_base,
    require_proxy_for_fake_ip,
)


def test_fake_ip_requires_explicit_socks5h_proxy() -> None:
    addresses = [ipaddress.ip_address("198.18.0.126")]

    with pytest.raises(BenchmarkTransportError, match="fake_ip_requires_proxy"):
        require_proxy_for_fake_ip(addresses, None)

    require_proxy_for_fake_ip(addresses, "socks5h://127.0.0.1:7897")


def test_curl_transport_never_relies_on_implicit_proxy() -> None:
    command = build_curl_base(
        "socks5h://127.0.0.1:7897",
        connect_timeout_seconds=30,
        timeout_seconds=180,
    )

    assert command[:3] == ["curl.exe", "--proxy", "socks5h://127.0.0.1:7897"]
    assert "--noproxy" not in command


@pytest.mark.asyncio
async def test_multipart_upload_is_not_replayed_after_ambiguous_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = 0

    async def ambiguous_failure(
        command: list[str],
        *,
        input_text: str | None,
    ) -> tuple[int, str]:
        nonlocal calls
        del command, input_text
        calls += 1
        return (
            0,
            '{}\n__HAKIMI_HTTP_STATUS__:500'
            '\n__HAKIMI_FIRST_BYTE__:0.1\n__HAKIMI_TOTAL__:0.2',
        )

    monkeypatch.setattr(
        "hakimi_analysis.benchmark.transport._run_process",
        ambiguous_failure,
    )
    media = tmp_path / "probe.mp4"
    media.write_bytes(b"video")
    transport = CurlTransport(
        proxy_url="socks5h://127.0.0.1:7897",
        retry_delays=(0, 0),
    )

    response = await transport.multipart_request(
        "https://provider.example/upload",
        headers={},
        fields={},
        file_path=media,
        mime_type="video/mp4",
    )

    assert response.status_code == 500
    assert calls == 1
