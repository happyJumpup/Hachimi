import asyncio
import json
from pathlib import Path

import pytest

from hakimi_analysis.benchmark.manifest import EXPECTED_MODELS
from hakimi_analysis.benchmark.models import CleanupOutcome, CleanupPolicy, MediaHandle
from hakimi_analysis.benchmark.real_providers import (
    ProviderContractError,
    QwenMediaProvider,
    SeedMediaProvider,
    _validate_envelope,
)
from hakimi_analysis.benchmark.transport import CurlResponse, CurlTransport


class FakeTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []

    async def json_request(
        self,
        method: str,
        url: str,
        **kwargs: object,
    ) -> CurlResponse:
        del kwargs
        self.requests.append((method, url))
        if method == "GET" and url.endswith("/files/file-1"):
            return CurlResponse(404, "{}")
        if "uploads" in url:
            return CurlResponse(
                200,
                json.dumps(
                    {
                        "data": {
                            "max_file_size_mb": 100,
                            "upload_dir": "temporary/dir",
                            "oss_access_key_id": "temporary-access",
                            "signature": "temporary-signature",
                            "policy": "temporary-policy",
                            "x_oss_object_acl": "private",
                            "x_oss_forbid_overwrite": "true",
                            "upload_host": "https://oss.example/upload",
                        }
                    }
                ),
            )
        return CurlResponse(200, '{"deleted": true}')

    async def multipart_request(self, *args: object, **kwargs: object) -> CurlResponse:
        del args, kwargs
        return CurlResponse(200, "{}")


@pytest.mark.asyncio
async def test_ark_cleanup_requires_delete_then_missing_verification() -> None:
    transport = FakeTransport()
    provider = SeedMediaProvider(
        api_key="test-key",
        base_url="https://ark.example/api/v3",
        transport=transport,  # type: ignore[arg-type]
    )
    handle = MediaHandle(
        provider="seed",
        model_id=EXPECTED_MODELS["seed_lite"],
        media_kind="video",
        handle_id="file-1",
        cleanup_policy=CleanupPolicy.DELETE_AND_VERIFY,
    )

    await provider.cleanup(handle)

    assert await provider.verify_cleanup(handle) == CleanupOutcome.DELETED_VERIFIED
    assert transport.requests == [
        ("DELETE", "https://ark.example/api/v3/files/file-1"),
        ("GET", "https://ark.example/api/v3/files/file-1"),
    ]


@pytest.mark.asyncio
async def test_qwen_handle_is_model_bound_and_records_48_hour_expiry(tmp_path: Path) -> None:
    media = tmp_path / "probe.mp4"
    media.write_bytes(b"video")
    provider = QwenMediaProvider(
        api_key="test-key",
        compatible_base_url="https://dashscope.example/compatible-mode/v1",
        transport=FakeTransport(),  # type: ignore[arg-type]
    )

    handle = await provider.upload(
        EXPECTED_MODELS["qwen_omni"],
        media,
        "video",
    )

    assert handle.model_id == EXPECTED_MODELS["qwen_omni"]
    assert handle.handle_id.startswith("oss://")
    assert handle.expires_at is not None
    await provider.cleanup(handle)
    assert await provider.verify_cleanup(handle) == CleanupOutcome.EXPIRY_RECORDED


def test_candidate_validation_does_not_repair_markdown_or_trailing_text() -> None:
    with pytest.raises(ProviderContractError, match="candidate_schema_error"):
        _validate_envelope('```json\n{"actions": []}\n```')
    with pytest.raises(ProviderContractError, match="candidate_schema_error"):
        _validate_envelope('{"actions": []} trailing')


@pytest.mark.asyncio
async def test_seed_upload_cancellation_deletes_file_id_before_propagating(
    tmp_path: Path,
) -> None:
    class WaitingTransport(FakeTransport):
        def __init__(self) -> None:
            super().__init__()
            self.deleted = False

        async def multipart_request(
            self, *args: object, **kwargs: object
        ) -> CurlResponse:
            del args, kwargs
            return CurlResponse(200, '{"id":"file-1","status":"processing"}')

        async def json_request(
            self,
            method: str,
            url: str,
            **kwargs: object,
        ) -> CurlResponse:
            del kwargs
            self.requests.append((method, url))
            if method == "DELETE":
                self.deleted = True
                return CurlResponse(200, '{"deleted":true}')
            if self.deleted:
                return CurlResponse(404, "{}")
            return CurlResponse(200, '{"status":"processing"}')

    media = tmp_path / "probe.mp4"
    media.write_bytes(b"video")
    transport = WaitingTransport()
    provider = SeedMediaProvider(
        api_key="test-key",
        base_url="https://ark.example/api/v3",
        transport=transport,  # type: ignore[arg-type]
        poll_interval_seconds=10,
    )
    task = asyncio.create_task(
        provider.upload(EXPECTED_MODELS["seed_lite"], media, "video")
    )
    await asyncio.sleep(0.01)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert transport.deleted


@pytest.mark.asyncio
async def test_seed_cleanup_failure_does_not_replace_original_cancellation(
    tmp_path: Path,
) -> None:
    class FailingCleanupTransport(FakeTransport):
        async def multipart_request(
            self, *args: object, **kwargs: object
        ) -> CurlResponse:
            del args, kwargs
            return CurlResponse(200, '{"id":"file-1","status":"processing"}')

        async def json_request(
            self,
            method: str,
            url: str,
            **kwargs: object,
        ) -> CurlResponse:
            del url, kwargs
            if method == "DELETE":
                return CurlResponse(500, "{}")
            return CurlResponse(200, '{"status":"processing"}')

    media = tmp_path / "probe.mp4"
    media.write_bytes(b"video")
    provider = SeedMediaProvider(
        api_key="test-key",
        base_url="https://ark.example/api/v3",
        transport=FailingCleanupTransport(),  # type: ignore[arg-type]
        poll_interval_seconds=10,
    )
    task = asyncio.create_task(
        provider.upload(EXPECTED_MODELS["seed_lite"], media, "video")
    )
    await asyncio.sleep(0.01)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_curl_secrets_are_sent_via_stdin_not_process_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_run(
        command: list[str],
        *,
        input_text: str | None,
    ) -> tuple[int, str]:
        captured["command"] = command
        captured["input"] = input_text
        return (
            0,
            '{}\n__HAKIMI_HTTP_STATUS__:200'
            '\n__HAKIMI_FIRST_BYTE__:0.1\n__HAKIMI_TOTAL__:0.2',
        )

    monkeypatch.setattr(
        "hakimi_analysis.benchmark.transport._run_process",
        fake_run,
    )
    transport = CurlTransport(
        proxy_url="socks5h://127.0.0.1:7897",
        retry_delays=(),
    )

    await transport.json_request(
        "POST",
        "https://provider.example/test",
        headers={"Authorization": "Bearer secret-value"},
        payload={"private": "payload"},
    )

    assert "secret-value" not in " ".join(captured["command"])  # type: ignore[arg-type]
    assert "secret-value" in str(captured["input"])
