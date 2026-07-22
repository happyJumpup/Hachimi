import asyncio
import json
from pathlib import Path

import pytest

from hakimi_analysis.benchmark.long_models import EXPECTED_LONG_EXPERIMENT_MODELS
from hakimi_analysis.benchmark.long_real_providers import (
    LongAsrMediaProvider,
    LongProviderContractError,
    LongProxyVolcAsrClient,
    LongQwenUploadAmbiguousError,
    LongQwenVlProvider,
    LongSeedMediaProvider,
    _parse_qwen_native_sse,
    long_asr_attempt_request_id,
)
from hakimi_analysis.benchmark.long_transport import LongBenchmarkTransportError
from hakimi_analysis.benchmark.models import CleanupPolicy, MediaHandle
from hakimi_analysis.benchmark.transport import CurlResponse
from hakimi_analysis.models import Transcript


class _FakeTransport:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []
        self.multipart_fields: list[dict[str, str]] = []

    async def json_request(
        self,
        method: str,
        url: str,
        **kwargs: object,
    ) -> CurlResponse:
        payload = kwargs.get("payload")
        if isinstance(payload, dict):
            self.payloads.append(payload)
        if method == "GET" and url.endswith("/uploads"):
            return CurlResponse(
                200,
                json.dumps(
                    {
                        "data": {
                            "max_file_size_mb": 100,
                            "upload_dir": "temporary/long-study",
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
        if "dashscope" in url:
            return CurlResponse(
                200,
                'data: {"request_id":"qwen-request","output":{"choices":['
                '{"message":{"content":[{"text":"{\\"actions\\": []}"}]}}]}}\n'
                "data: [DONE]\n",
            )
        return CurlResponse(
            200,
            json.dumps({"id": "seed-request", "output_text": '{"actions": []}'}),
        )

    async def multipart_request(
        self,
        _url: str,
        **kwargs: object,
    ) -> CurlResponse:
        fields = kwargs["fields"]
        assert isinstance(fields, dict)
        self.multipart_fields.append(fields)
        return CurlResponse(200, "{}")


class _AmbiguousMultipartTransport(_FakeTransport):
    """Simulates a transfer that may have created an unobservable OSS object."""

    async def multipart_request(
        self,
        _url: str,
        **_kwargs: object,
    ) -> CurlResponse:
        self.multipart_fields.append({"attempt": str(len(self.multipart_fields) + 1)})
        raise LongBenchmarkTransportError("network_error", retryable=True)


class _CancelledMultipartTransport(_FakeTransport):
    async def multipart_request(
        self,
        _url: str,
        **_kwargs: object,
    ) -> CurlResponse:
        self.multipart_fields.append({"attempt": str(len(self.multipart_fields) + 1)})
        raise asyncio.CancelledError


@pytest.mark.asyncio
async def test_long_seed_contact_sheet_uses_in_memory_image_and_absolute_window(
    tmp_path: Path,
) -> None:
    image = tmp_path / "contact-sheet.jpg"
    image.write_bytes(b"jpeg-content")
    transport = _FakeTransport()
    provider = LongSeedMediaProvider(
        api_key="test-key",
        base_url="https://ark.example/api/v3",
        transport=transport,  # type: ignore[arg-type]
    )

    result = await provider.analyze_contact_sheet(
        image,
        frame_times_seconds=(120.0, 123.5),
        window_start_seconds=120.0,
        window_end_seconds=180.0,
        prompt="return fitness actions",
    )

    assert result.envelope.actions == []
    assert transport.multipart_fields == []
    content = transport.payloads[0]["input"]
    assert isinstance(content, list)
    parts = content[0]["content"]
    assert isinstance(parts, list)
    assert str(parts[0]["image_url"]).startswith("data:image/jpeg;base64,")
    metadata = json.loads(parts[1]["text"])
    assert metadata["window"] == {"start_seconds": 120.0, "end_seconds": 180.0}
    assert metadata["contact_sheet"]["frame_times_seconds"] == [120.0, 123.5]


@pytest.mark.asyncio
async def test_long_qwen_uses_unique_object_keys_and_local_json_schema_only(
    tmp_path: Path,
) -> None:
    video = tmp_path / "silent.mp4"
    video.write_bytes(b"video")
    transport = _FakeTransport()
    provider = LongQwenVlProvider(
        api_key="test-key",
        transport=transport,  # type: ignore[arg-type]
    )
    model_id = EXPECTED_LONG_EXPERIMENT_MODELS["qwen_visual_model"]

    first, second = await asyncio.gather(
        provider.upload(model_id, video, "video"),
        provider.upload(model_id, video, "video"),
    )
    result = await provider.analyze(first, "Return action events.")

    assert first.handle_id != second.handle_id
    assert all(fields["key"].endswith(".mp4") for fields in transport.multipart_fields)
    assert result.envelope.actions == []
    request = transport.payloads[-1]
    parameters = request["parameters"]
    assert isinstance(parameters, dict)
    assert "response_format" not in parameters
    input_value = request["input"]
    assert isinstance(input_value, dict)
    messages = input_value["messages"]
    assert isinstance(messages, list)
    text = messages[0]["content"][1]["text"]
    assert "Do not use Markdown code fences" in text


@pytest.mark.asyncio
async def test_long_qwen_does_not_replay_an_ambiguous_multipart_transfer(
    tmp_path: Path,
) -> None:
    video = tmp_path / "silent.mp4"
    video.write_bytes(b"video")
    transport = _AmbiguousMultipartTransport()
    provider = LongQwenVlProvider(
        api_key="test-key",
        transport=transport,  # type: ignore[arg-type]
    )

    with pytest.raises(LongQwenUploadAmbiguousError, match="qwen_upload_network_error") as caught:
        await provider.upload(
            EXPECTED_LONG_EXPERIMENT_MODELS["qwen_visual_model"],
            video,
            "video",
        )

    assert len(transport.multipart_fields) == 1
    assert caught.value.expires_at.endswith("+00:00")


@pytest.mark.asyncio
async def test_long_qwen_records_expiry_before_starting_multipart(
    tmp_path: Path,
) -> None:
    video = tmp_path / "silent.mp4"
    video.write_bytes(b"video")
    transport = _CancelledMultipartTransport()
    provider = LongQwenVlProvider(
        api_key="test-key",
        transport=transport,  # type: ignore[arg-type]
    )
    recorded_expiries: list[str] = []

    with pytest.raises(asyncio.CancelledError):
        await provider.upload(
            EXPECTED_LONG_EXPERIMENT_MODELS["qwen_visual_model"],
            video,
            "video",
            on_expiry=recorded_expiries.append,
        )

    assert len(recorded_expiries) == 1
    assert recorded_expiries[0].endswith("+00:00")


@pytest.mark.asyncio
async def test_long_qwen_aborts_before_multipart_when_expiry_journal_fails(
    tmp_path: Path,
) -> None:
    video = tmp_path / "silent.mp4"
    video.write_bytes(b"video")
    transport = _FakeTransport()
    provider = LongQwenVlProvider(
        api_key="test-key",
        transport=transport,  # type: ignore[arg-type]
    )

    def fail_journal(_expires_at: str) -> None:
        raise RuntimeError("journal unavailable")

    with pytest.raises(RuntimeError, match="journal unavailable"):
        await provider.upload(
            EXPECTED_LONG_EXPERIMENT_MODELS["qwen_visual_model"],
            video,
            "video",
            on_expiry=fail_journal,
        )

    assert transport.multipart_fields == []


@pytest.mark.asyncio
async def test_long_qwen_attaches_expiry_metadata_when_multipart_is_cancelled(
    tmp_path: Path,
) -> None:
    video = tmp_path / "silent.mp4"
    video.write_bytes(b"video")
    transport = _CancelledMultipartTransport()
    provider = LongQwenVlProvider(
        api_key="test-key",
        transport=transport,  # type: ignore[arg-type]
    )

    with pytest.raises(asyncio.CancelledError) as caught:
        await provider.upload(
            EXPECTED_LONG_EXPERIMENT_MODELS["qwen_visual_model"],
            video,
            "video",
        )

    assert len(transport.multipart_fields) == 1
    assert str(getattr(caught.value, "expires_at", "")).endswith("+00:00")


@pytest.mark.asyncio
async def test_long_asr_adapter_preserves_the_caller_deterministic_request_id(
    tmp_path: Path,
) -> None:
    class FakeAsrClient:
        def __init__(self) -> None:
            self.request_ids: list[str] = []

        async def check_configuration(self) -> None:
            return None

        async def recognize(
            self,
            *,
            audio_path: Path,
            window_start_seconds: float,
            request_id: str,
        ) -> Transcript:
            assert audio_path.is_file()
            assert window_start_seconds == 0
            self.request_ids.append(request_id)
            return Transcript(text="", utterances=[], provider_request_id="asr-request")

    audio = tmp_path / "full.wav"
    audio.write_bytes(b"pcm")
    client = FakeAsrClient()
    provider = LongAsrMediaProvider(client)
    handle = await provider.upload(
        EXPECTED_LONG_EXPERIMENT_MODELS["asr_resource"],
        audio,
        "audio",
    )

    transcript = await provider.transcribe(handle, request_id="long-fixed-run-id")

    assert transcript.provider_request_id == "asr-request"
    assert client.request_ids == ["long-fixed-run-id"]
    assert await provider.verify_cleanup(handle)
    assert handle.cleanup_policy == CleanupPolicy.LOCAL_RELEASE


def test_long_asr_proxy_and_retry_connect_ids_are_explicit_and_deterministic() -> None:
    client = LongProxyVolcAsrClient(
        api_key="test-key",
        resource_id=EXPECTED_LONG_EXPERIMENT_MODELS["asr_resource"],
        url="wss://example.invalid/asr",
        proxy_url="socks5h://127.0.0.1:7897",
    )

    assert client._long_proxy_url == "socks5h://127.0.0.1:7897"
    assert long_asr_attempt_request_id("long-fixed-run-id", 0) == "long-fixed-run-id"
    assert long_asr_attempt_request_id("long-fixed-run-id", 1) == long_asr_attempt_request_id(
        "long-fixed-run-id", 1
    )
    assert long_asr_attempt_request_id("long-fixed-run-id", 1) != "long-fixed-run-id"
    assert len(long_asr_attempt_request_id("x" * 56, 1)) <= 64
    with pytest.raises(ValueError, match="explicit socks5h"):
        LongProxyVolcAsrClient(
            api_key="test-key",
            resource_id=EXPECTED_LONG_EXPERIMENT_MODELS["asr_resource"],
            url="wss://example.invalid/asr",
            proxy_url="http://127.0.0.1:7897",
        )
    with pytest.raises(LongProviderContractError, match="long_asr_request_id_invalid"):
        asyncio.run(
            LongAsrMediaProvider(_NoopAsrClient()).transcribe(
                MediaHandle(
                    provider="asr",
                    model_id=EXPECTED_LONG_EXPERIMENT_MODELS["asr_resource"],
                    media_kind="audio",
                    handle_id="C:/safe.wav",
                    cleanup_policy=CleanupPolicy.LOCAL_RELEASE,
                ),
                request_id="has a space",
            )
        )


class _NoopAsrClient:
    async def check_configuration(self) -> None:
        return None

    async def recognize(
        self,
        *,
        audio_path: Path,
        window_start_seconds: float,
        request_id: str,
    ) -> Transcript:
        del audio_path, window_start_seconds, request_id
        return Transcript(text="", utterances=[])


def test_long_qwen_sse_accepts_delta_or_cumulative_message_shapes_once() -> None:
    delta_text, _, _, _ = _parse_qwen_native_sse(
        'data: {"output":{"choices":[{"message":{"content":[{"text":"{\\"actions\\":"}]}}]}}\n'
        'data: {"output":{"choices":[{"message":{"content":[{"text":" []}"}]}}]}}\n'
        "data: [DONE]\n"
    )
    cumulative_text, _, _, _ = _parse_qwen_native_sse(
        'data: {"output":{"choices":[{"message":{"content":[{"text":"{\\"actions\\":"}]}}]}}\n'
        'data: {"output":{"choices":[{"message":{"content":[{"text":"{\\"actions\\": []}"}]}}]}}\n'
        "data: [DONE]\n"
    )

    assert delta_text == '{"actions": []}'
    assert cumulative_text == '{"actions": []}'


def test_long_qwen_sse_text_missing_keeps_only_a_structural_diagnostic() -> None:
    with pytest.raises(LongProviderContractError, match="qwen_sse_text_missing") as caught:
        _parse_qwen_native_sse(
            'data: {"output":{"choices":[{"message":{"content":[]}}]}}\n'
            "data: [DONE]\n"
        )

    assert caught.value.diagnostic == (
        "choices=1;content_lists=1;content_strings=0;done=1;error_events=0;events=1;"
        "messages=1;outputs=1;reasoning_parts=0;text_parts=0"
    )


def test_long_qwen_sse_provider_event_keeps_only_a_normalized_code() -> None:
    with pytest.raises(LongProviderContractError, match="qwen_sse_provider_event") as caught:
        _parse_qwen_native_sse('data: {"code":"Data-Inspection Failed!"}\n')

    assert caught.value.diagnostic == "data_inspection_failed"
