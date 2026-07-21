import asyncio
import ipaddress
import json
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_STATUS_MARKER = "\n__HAKIMI_HTTP_STATUS__:"
_FIRST_BYTE_MARKER = "\n__HAKIMI_FIRST_BYTE__:"
_TOTAL_MARKER = "\n__HAKIMI_TOTAL__:"


class BenchmarkTransportError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class CurlResponse:
    status_code: int
    body: str
    first_byte_seconds: float | None = None
    total_seconds: float | None = None


def require_proxy_for_fake_ip(
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address],
    proxy_url: str | None,
) -> None:
    if any(address in _FAKE_IP_NETWORK for address in addresses) and not proxy_url:
        raise BenchmarkTransportError("fake_ip_requires_proxy", retryable=False)
    if proxy_url is not None and not proxy_url.startswith("socks5h://"):
        raise BenchmarkTransportError("proxy_must_be_socks5h", retryable=False)


def resolve_addresses(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    return list(
        {
            ipaddress.ip_address(item[4][0])
            for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        }
    )


def build_curl_base(
    proxy_url: str,
    *,
    connect_timeout_seconds: int,
    timeout_seconds: int,
) -> list[str]:
    if not proxy_url.startswith("socks5h://"):
        raise BenchmarkTransportError("proxy_must_be_socks5h", retryable=False)
    return [
        "curl.exe",
        "--proxy",
        proxy_url,
        "--connect-timeout",
        str(connect_timeout_seconds),
        "--max-time",
        str(timeout_seconds),
        "--silent",
        "--show-error",
    ]


class CurlTransport:
    def __init__(
        self,
        *,
        proxy_url: str,
        connect_timeout_seconds: int = 30,
        timeout_seconds: int = 180,
        retry_delays: tuple[float, ...] = (1.0, 2.0),
    ) -> None:
        self._base = build_curl_base(
            proxy_url,
            connect_timeout_seconds=connect_timeout_seconds,
            timeout_seconds=timeout_seconds,
        )
        self._retry_delays = retry_delays

    async def json_request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
    ) -> CurlResponse:
        if query:
            url = f"{url}?{urlencode(query)}"
        command = [
            *self._base,
            "--request",
            method,
            "--write-out",
            _curl_write_out(),
            "--config",
            "-",
        ]
        config_lines = [
            _curl_config_line("header", f"{name}: {value}")
            for name, value in headers.items()
        ]
        if payload is not None:
            config_lines.append(
                _curl_config_line("data-binary", json.dumps(payload, ensure_ascii=False))
            )
        command.append(url)
        return await self._run_with_retry(
            command,
            input_text="\n".join(config_lines) + "\n",
            allow_retry=True,
        )

    async def multipart_request(
        self,
        url: str,
        *,
        headers: dict[str, str],
        fields: dict[str, str],
        file_path: Path,
        mime_type: str,
    ) -> CurlResponse:
        command = [
            *self._base,
            "--request",
            "POST",
            "--write-out",
            _curl_write_out(),
            "--config",
            "-",
        ]
        config_lines = [
            *(
                _curl_config_line("header", f"{name}: {value}")
                for name, value in headers.items()
            ),
            *(
                _curl_config_line("form-string", f"{name}={value}")
                for name, value in fields.items()
            ),
            _curl_config_line("form", f"file=@{file_path};type={mime_type}"),
        ]
        command.append(url)
        return await self._run_with_retry(
            command,
            input_text="\n".join(config_lines) + "\n",
            allow_retry=False,
        )

    async def _run_with_retry(
        self,
        command: list[str],
        *,
        input_text: str | None,
        allow_retry: bool,
    ) -> CurlResponse:
        attempts = len(self._retry_delays) + 1 if allow_retry else 1
        for attempt in range(attempts):
            returncode, stdout = await _run_process(command, input_text=input_text)
            if returncode == 0:
                response = _parse_response(stdout)
                if response.status_code not in {408, 429} and response.status_code < 500:
                    return response
                if attempt == attempts - 1:
                    return response
            elif attempt == attempts - 1:
                raise BenchmarkTransportError("network_error", retryable=True)
            await asyncio.sleep(self._retry_delays[attempt])
        raise AssertionError("retry loop exhausted")


async def _run_process(
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


def _parse_response(output: str) -> CurlResponse:
    body, marker, metrics = output.rpartition(_STATUS_MARKER)
    if not marker:
        raise BenchmarkTransportError("missing_http_status", retryable=False)
    try:
        status_text, first_text = metrics.split(_FIRST_BYTE_MARKER, maxsplit=1)
        first_value, total_text = first_text.split(_TOTAL_MARKER, maxsplit=1)
        return CurlResponse(
            status_code=int(status_text.strip()),
            body=body,
            first_byte_seconds=float(first_value.strip()),
            total_seconds=float(total_text.strip()),
        )
    except ValueError as error:
        raise BenchmarkTransportError("invalid_http_status", retryable=False) from error


def _curl_write_out() -> str:
    return (
        f"{_STATUS_MARKER}%{{http_code}}"
        f"{_FIRST_BYTE_MARKER}%{{time_starttransfer}}"
        f"{_TOTAL_MARKER}%{{time_total}}"
    )


def _curl_config_line(option: str, value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'{option} = "{escaped}"'


def require_success(response: CurlResponse) -> None:
    if 200 <= response.status_code < 300:
        return
    if response.status_code in {401, 403, 404}:
        raise BenchmarkTransportError(
            "configuration_error",
            retryable=False,
            status_code=response.status_code,
        )
    if response.status_code in {400, 409, 415, 422}:
        raise BenchmarkTransportError(
            "request_or_media_error",
            retryable=False,
            status_code=response.status_code,
        )
    raise BenchmarkTransportError(
        "provider_error",
        retryable=response.status_code in {408, 429} or response.status_code >= 500,
        status_code=response.status_code,
    )
