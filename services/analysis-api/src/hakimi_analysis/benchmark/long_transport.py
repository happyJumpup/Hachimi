"""Transport owned exclusively by the isolated long-video A/B experiment.

The product and frozen native-AV benchmark keep their existing transport.  This
module deliberately owns the long-experiment-specific multipart behavior:
explicit SOCKS5 hostname resolution, HTTP/1.1, and a disabled ``Expect``
handshake.  It does not retry requests itself.  The experiment scheduler owns
the two-attempt retry policy so it can account for backoff and cancellation.
"""

import asyncio
import ipaddress
import json
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_STATUS_MARKER = "\n__HAKIMI_LONG_HTTP_STATUS__:"
_FIRST_BYTE_MARKER = "\n__HAKIMI_LONG_FIRST_BYTE__:"
_TOTAL_MARKER = "\n__HAKIMI_LONG_TOTAL__:"
_RETRY_AFTER_MARKER = "\n__HAKIMI_LONG_RETRY_AFTER__:"


class LongBenchmarkTransportError(RuntimeError):
    """A sanitized transport failure consumable by ``retry_long_operation``."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class LongCurlResponse:
    status_code: int
    body: str
    first_byte_seconds: float | None = None
    total_seconds: float | None = None
    retry_after_seconds: float | None = None


def require_long_proxy_for_fake_ip(
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address],
    proxy_url: str | None,
) -> None:
    """Reject accidental direct routing when a provider resolves to Fake-IP."""
    if any(address in _FAKE_IP_NETWORK for address in addresses) and not proxy_url:
        raise LongBenchmarkTransportError("fake_ip_requires_proxy", retryable=False)
    _require_explicit_socks5h(proxy_url)


def resolve_long_addresses(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    return list(
        {
            ipaddress.ip_address(item[4][0])
            for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        }
    )


def build_long_curl_base(
    proxy_url: str,
    *,
    connect_timeout_seconds: int,
    timeout_seconds: int,
) -> list[str]:
    """Build a command which cannot silently inherit a no-proxy bypass."""
    _require_explicit_socks5h(proxy_url)
    if connect_timeout_seconds <= 0 or timeout_seconds <= 0:
        raise ValueError("long transport timeouts must be positive")
    return [
        "curl.exe",
        "--proxy",
        proxy_url,
        "--noproxy",
        "",
        "--connect-timeout",
        str(connect_timeout_seconds),
        "--max-time",
        str(timeout_seconds),
        "--silent",
        "--show-error",
    ]


class LongCurlTransport:
    """Small curl facade with the existing provider JSON/multipart seam."""

    def __init__(
        self,
        *,
        proxy_url: str,
        connect_timeout_seconds: int = 30,
        timeout_seconds: int = 180,
    ) -> None:
        self._base = build_long_curl_base(
            proxy_url,
            connect_timeout_seconds=connect_timeout_seconds,
            timeout_seconds=timeout_seconds,
        )

    async def json_request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
    ) -> LongCurlResponse:
        if query:
            url = f"{url}?{urlencode(query)}"
        command = [
            *self._base,
            "--request",
            method,
            "--write-out",
            _long_curl_write_out(),
            "--config",
            "-",
            url,
        ]
        config_lines = [
            _long_curl_config_line("header", f"{name}: {value}")
            for name, value in headers.items()
        ]
        if payload is not None:
            config_lines.append(
                _long_curl_config_line("data-binary", json.dumps(payload, ensure_ascii=False))
            )
        return await self._request(command, input_text="\n".join(config_lines) + "\n")

    async def multipart_request(
        self,
        url: str,
        *,
        headers: dict[str, str],
        fields: dict[str, str],
        file_path: Path,
        mime_type: str,
    ) -> LongCurlResponse:
        """Post an upload once; an outer policy decides whether to retry it.

        HTTP/1.1 and an empty ``Expect`` header are intentionally limited to
        this long-experiment multipart path.  JSON calls retain curl defaults.
        """
        if not file_path.is_file():
            raise LongBenchmarkTransportError("multipart_file_missing", retryable=False)
        command = [
            *self._base,
            "--request",
            "POST",
            "--http1.1",
            "--write-out",
            _long_curl_write_out(),
            "--config",
            "-",
            url,
        ]
        config_lines = [
            _long_curl_config_line("header", "Expect:"),
            *(
                _long_curl_config_line("header", f"{name}: {value}")
                for name, value in headers.items()
            ),
            *(
                _long_curl_config_line("form-string", f"{name}={value}")
                for name, value in fields.items()
            ),
            _long_curl_config_line("form", f"file=@{file_path};type={mime_type}"),
        ]
        return await self._request(command, input_text="\n".join(config_lines) + "\n")

    async def _request(self, command: list[str], *, input_text: str) -> LongCurlResponse:
        returncode, stdout = await _run_long_process(command, input_text=input_text)
        if returncode != 0:
            raise LongBenchmarkTransportError(
                _curl_exit_code(returncode),
                retryable=True,
            )
        return _parse_long_response(stdout)


async def _run_long_process(
    command: list[str],
    *,
    input_text: str | None,
) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE if input_text is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await process.communicate(
            input_text.encode("utf-8") if input_text is not None else None
        )
    except asyncio.CancelledError:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()
        raise
    return process.returncode or 0, stdout.decode("utf-8", errors="replace")


def _parse_long_response(output: str) -> LongCurlResponse:
    body, marker, metrics = output.rpartition(_STATUS_MARKER)
    if not marker:
        raise LongBenchmarkTransportError("missing_http_status", retryable=False)
    try:
        status_text, first_text = metrics.split(_FIRST_BYTE_MARKER, maxsplit=1)
        first_value, total_text = first_text.split(_TOTAL_MARKER, maxsplit=1)
        total_value, retry_marker, retry_after_text = total_text.partition(_RETRY_AFTER_MARKER)
        return LongCurlResponse(
            status_code=int(status_text.strip()),
            body=body,
            first_byte_seconds=float(first_value.strip()),
            total_seconds=float(total_value.strip()),
            retry_after_seconds=(
                _parse_long_retry_after(retry_after_text) if retry_marker else None
            ),
        )
    except ValueError as error:
        raise LongBenchmarkTransportError("invalid_http_status", retryable=False) from error


def require_long_success(response: LongCurlResponse) -> None:
    if 200 <= response.status_code < 300:
        return
    if response.status_code in {401, 403, 404}:
        raise LongBenchmarkTransportError(
            "configuration_error",
            retryable=False,
            status_code=response.status_code,
            retry_after_seconds=response.retry_after_seconds,
        )
    if response.status_code in {400, 409, 415, 422}:
        raise LongBenchmarkTransportError(
            "request_or_media_error",
            retryable=False,
            status_code=response.status_code,
            retry_after_seconds=response.retry_after_seconds,
        )
    raise LongBenchmarkTransportError(
        "provider_error",
        retryable=response.status_code in {408, 429} or response.status_code >= 500,
        status_code=response.status_code,
        retry_after_seconds=response.retry_after_seconds,
    )


def _require_explicit_socks5h(proxy_url: str | None) -> None:
    if proxy_url is None or not proxy_url.startswith("socks5h://"):
        raise LongBenchmarkTransportError("proxy_must_be_socks5h", retryable=False)


def _curl_exit_code(returncode: int) -> str:
    if 1 <= returncode <= 255:
        return f"network_error_curl_exit_{returncode}"
    return "network_error_curl_exit_unknown"


def _long_curl_write_out() -> str:
    return (
        f"{_STATUS_MARKER}%{{http_code}}"
        f"{_FIRST_BYTE_MARKER}%{{time_starttransfer}}"
        f"{_TOTAL_MARKER}%{{time_total}}"
        f"{_RETRY_AFTER_MARKER}%header{{retry-after}}"
    )


def _long_curl_config_line(option: str, value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'{option} = "{escaped}"'


def _parse_long_retry_after(value: str) -> float | None:
    candidate = value.strip()
    if not candidate:
        return None
    try:
        return max(0.0, float(candidate))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(candidate)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(0.0, (parsed - datetime.now(UTC)).total_seconds())
