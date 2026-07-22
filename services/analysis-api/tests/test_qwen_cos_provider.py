import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from hakimi_analysis.models import Segment
from hakimi_analysis.providers.base import ProviderError, ProviderSchemaError
from hakimi_analysis.providers.qwen import QwenVisualClient
from hakimi_analysis.temporary_cos import BackupSafetyGate, TencentCosTemporaryStore


class FakeCosClient:
    def __init__(self, *, delete_fails: bool = False) -> None:
        self.delete_fails = delete_fails
        self.uploaded: list[dict[str, Any]] = []
        self.deleted: list[str] = []

    def put_object(self, **kwargs: Any) -> None:
        self.uploaded.append(kwargs)

    def get_presigned_url(self, **kwargs: Any) -> str:
        return f"https://private.example/{kwargs['Key']}?signature=redacted"

    def delete_object(self, **kwargs: Any) -> None:
        if self.delete_fails:
            raise RuntimeError("delete failed")
        self.deleted.append(str(kwargs["Key"]))

    def get_bucket_acl(self, **kwargs: Any) -> dict[str, object]:
        del kwargs
        return {
            "AccessControlList": {
                "Grant": [{"Grantee": {"ID": "owner"}, "Permission": "FULL_CONTROL"}]
            }
        }

    def get_bucket_encryption(self, **kwargs: Any) -> dict[str, object]:
        del kwargs
        return {
            "Rule": {
                "ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}
            }
        }

    def get_bucket_lifecycle(self, **kwargs: Any) -> dict[str, object]:
        del kwargs
        return {
            "Rule": {
                "Status": "Enabled",
                "Filter": {"Prefix": "trainpal/visual-fallback"},
                "Expiration": {"Days": 1},
            }
        }


class PublicCosClient(FakeCosClient):
    def get_bucket_acl(self, **kwargs: Any) -> dict[str, object]:
        del kwargs
        return {
            "AccessControlList": {
                "Grant": [
                    {
                        "Grantee": {
                            "URI": "http://cam.qcloud.com/groups/global/AllUsers"
                        },
                        "Permission": "READ",
                    }
                ]
            }
        }


class SlowUploadCosClient(FakeCosClient):
    def __init__(self) -> None:
        super().__init__()
        self.upload_started = threading.Event()

    def put_object(self, **kwargs: Any) -> None:
        super().put_object(**kwargs)
        self.upload_started.set()
        time.sleep(0.05)


def make_store(
    client: FakeCosClient,
    gate: BackupSafetyGate | None = None,
) -> TencentCosTemporaryStore:
    return TencentCosTemporaryStore(
        secret_id="secret-id",
        secret_key="secret-key",
        region="ap-guangzhou",
        bucket="private-bucket-123",
        object_prefix="trainpal/visual-fallback",
        safety_gate=gate,
        client=client,
    )


@pytest.mark.asyncio
async def test_qwen_uses_video_url_json_mode_and_one_local_validation(
    tmp_path: Path,
) -> None:
    chunk = tmp_path / "user-file-name.mp4"
    chunk.write_bytes(b"silent-video")
    cos = FakeCosClient()
    observed: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        observed.append(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"x-request-id": "qwen-request-1"},
            json={
                "id": "chat-1",
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"segments":[{"action_name":"罗马尼亚硬拉",'
                                '"start_seconds":2,"end_seconds":8,'
                                '"visual_cue":"髋部后移",'
                                '"segment_role":"teaching_demo"}]}'
                            )
                        }
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = QwenVisualClient(
            api_key="qwen-key",
            model_id="qwen3-vl-flash-2026-01-22",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            http_client=http_client,
            object_store=make_store(cos),
        )
        async with client.prepare(chunk) as prepared:
            result = await client.locate(
                prepared,
                window=Segment(start_seconds=0, end_seconds=60),
                instructions="Return the VisualLocalizationResult JSON.",
            )

    assert result.segments[0].action_name == "罗马尼亚硬拉"
    payload = observed[0]
    assert payload["model"] == "qwen3-vl-flash-2026-01-22"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["enable_thinking"] is False
    video_input = payload["messages"][0]["content"][0]
    assert video_input["type"] == "video_url"
    assert video_input["fps"] == 1
    assert "user-file-name" not in video_input["video_url"]["url"]
    assert len(cos.uploaded) == 1
    assert cos.uploaded[0]["ServerSideEncryption"] == "AES256"
    assert cos.deleted == [cos.uploaded[0]["Key"]]


@pytest.mark.asyncio
async def test_qwen_schema_failure_is_not_repaired_by_another_model(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk.mp4"
    chunk.write_bytes(b"silent-video")
    requests = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "not-json"}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = QwenVisualClient(
            api_key="qwen-key",
            model_id="qwen3-vl-flash-2026-01-22",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            http_client=http_client,
            object_store=make_store(FakeCosClient()),
        )
        async with client.prepare(chunk) as prepared:
            with pytest.raises(ProviderSchemaError):
                await client.locate(
                    prepared,
                    window=Segment(start_seconds=0, end_seconds=60),
                    instructions="Return JSON.",
                )

    assert requests == 1


@pytest.mark.asyncio
async def test_cos_cleanup_failure_blocks_future_fallbacks(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk.mp4"
    chunk.write_bytes(b"silent-video")
    gate = BackupSafetyGate()
    store = make_store(FakeCosClient(delete_fails=True), gate)

    with pytest.raises(ProviderError) as raised:
        async with store.signed_read_url(chunk):
            pass

    assert raised.value.code == "security_error"
    assert gate.available is False
    with pytest.raises(ProviderError, match="disabled"):
        async with store.signed_read_url(chunk):
            pass


@pytest.mark.asyncio
async def test_cos_cleanup_preserves_the_primary_provider_error(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk.mp4"
    chunk.write_bytes(b"silent-video")
    gate = BackupSafetyGate()
    store = make_store(FakeCosClient(delete_fails=True), gate)

    with pytest.raises(ProviderSchemaError, match="primary schema error"):
        async with store.signed_read_url(chunk):
            raise ProviderSchemaError("primary schema error")

    assert gate.available is False


@pytest.mark.asyncio
async def test_cos_cancellation_during_upload_still_deletes_the_random_object(
    tmp_path: Path,
) -> None:
    chunk = tmp_path / "chunk.mp4"
    chunk.write_bytes(b"silent-video")
    client = SlowUploadCosClient()
    gate = BackupSafetyGate()
    store = make_store(client, gate)

    async def consume_url() -> None:
        async with store.signed_read_url(chunk):
            raise AssertionError("cancelled upload must not yield a signed URL")

    task = asyncio.create_task(consume_url())
    assert await asyncio.to_thread(client.upload_started.wait, 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(client.uploaded) == 1
    assert client.deleted == [client.uploaded[0]["Key"]]
    assert gate.available is True


def test_cos_readiness_requires_private_acl_encryption_and_lifecycle() -> None:
    assert make_store(FakeCosClient()).check_security_configuration() is True

    assert make_store(PublicCosClient()).check_security_configuration() is False
