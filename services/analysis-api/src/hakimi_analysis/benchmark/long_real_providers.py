"""Provider adapters used only by the isolated long-video A/B experiment.

The production API and the frozen 60-second native-AV benchmark deliberately
keep their provider contracts unchanged.  Long-video experiments need a
contact-sheet request shape, collision-safe temporary Qwen object names, and
an explicitly proxied ASR socket, so those differences live here rather than
leaking into the shared adapters.
"""

import asyncio
import base64
import hashlib
import json
import mimetypes
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from pydantic import ValidationError
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from hakimi_analysis.benchmark.long_execution import retry_long_operation
from hakimi_analysis.benchmark.long_models import EXPECTED_LONG_EXPERIMENT_MODELS
from hakimi_analysis.benchmark.long_transport import (
    LongBenchmarkTransportError,
    LongCurlResponse,
    LongCurlTransport,
    require_long_success,
)
from hakimi_analysis.benchmark.models import (
    CandidateEnvelope,
    CleanupOutcome,
    CleanupPolicy,
    MediaHandle,
    ProviderInference,
)
from hakimi_analysis.benchmark.runner import GateFailure, PreflightStage
from hakimi_analysis.models import Transcript
from hakimi_analysis.providers.asr import (
    VolcAsrClient,
    _config_frame,
    _handshake_error,
    _read_pcm_chunks,
    _receive_final_result,
    _receive_frame,
    _to_transcript,
)
from hakimi_analysis.providers.base import ProviderError

_QWEN_POLICY_URL = "https://dashscope.aliyuncs.com/api/v1/uploads"
_QWEN_NATIVE_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
)
_RAW_JSON_SUFFIX = (
    "\nReturn exactly one raw JSON object matching the requested schema and nothing "
    "else. Do not use Markdown code fences."
)
_CONNECT_ID = re.compile(r"^[A-Za-z0-9_-]{1,56}$")


class LongProviderContractError(RuntimeError):
    """A non-retryable long-experiment provider contract failure."""

    def __init__(self, code: str, *, diagnostic: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.diagnostic = diagnostic
        self.retryable = False


class LongQwenUploadAmbiguousError(LongProviderContractError):
    """One-shot multipart failed after an OSS object may have been stored."""

    def __init__(self, code: str, *, expires_at: str) -> None:
        super().__init__(code)
        self.expires_at = expires_at


class LongSeedMediaProvider:
    """Seed Mini contact-sheet visual and text-fusion adapter.

    Contact sheets are encoded in-memory as a data URI.  They are never first
    uploaded as an Ark File, which keeps this arm free of provider-side media
    cleanup obligations.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        transport: LongCurlTransport,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._transport = transport

    async def check_configuration(self) -> None:
        if not self._api_key.strip():
            raise GateFailure(
                PreflightStage.CONFIGURATION,
                "missing_ark_api_key",
                retryable=False,
            )

    async def minimal_call(self, model_id: str) -> None:
        self._require_model(model_id)
        payload, _ = await self._response(
            instructions="Return an empty action envelope.",
            content=[{"type": "input_text", "text": 'Return {"actions": []}'}],
        )
        _parse_ark_envelope(payload)

    async def analyze_contact_sheet(
        self,
        image_path: Path,
        *,
        frame_times_seconds: tuple[float, ...],
        window_start_seconds: float,
        window_end_seconds: float,
        prompt: str,
    ) -> ProviderInference:
        if not image_path.is_file() or image_path.stat().st_size <= 0:
            raise LongProviderContractError("contact_sheet_missing")
        if window_start_seconds < 0 or window_end_seconds <= window_start_seconds:
            raise LongProviderContractError("contact_sheet_window_invalid")
        if not frame_times_seconds or any(
            frame_time < window_start_seconds or frame_time > window_end_seconds
            for frame_time in frame_times_seconds
        ):
            raise LongProviderContractError("contact_sheet_times_invalid")

        image_bytes = await asyncio.to_thread(image_path.read_bytes)
        image_url = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii")
        metadata = {
            "analysis_scope": "long_video_chunk",
            "window": {
                "start_seconds": window_start_seconds,
                "end_seconds": window_end_seconds,
            },
            "contact_sheet": {
                "columns": 4,
                "frame_times_seconds": list(frame_times_seconds),
                "order": "row_major",
            },
            "time_rule": "Return absolute source-video seconds within this window.",
        }
        payload, response = await self._response(
            instructions=prompt,
            content=[
                {"type": "input_image", "image_url": image_url},
                {"type": "input_text", "text": json.dumps(metadata)},
            ],
        )
        envelope, request_id, input_tokens, output_tokens = _parse_ark_envelope(payload)
        return ProviderInference(
            envelope=envelope,
            request_id=request_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            first_response_seconds=response.first_byte_seconds,
            final_response_seconds=response.total_seconds,
        )

    async def complete_text(self, prompt: str) -> ProviderInference:
        payload, response = await self._response(
            instructions=prompt,
            content=[{"type": "input_text", "text": prompt}],
        )
        envelope, request_id, input_tokens, output_tokens = _parse_ark_envelope(payload)
        return ProviderInference(
            envelope=envelope,
            request_id=request_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            first_response_seconds=response.first_byte_seconds,
            final_response_seconds=response.total_seconds,
        )

    async def _response(
        self,
        *,
        instructions: str,
        content: list[dict[str, str]],
    ) -> tuple[dict[str, Any], LongCurlResponse]:
        response = await self._transport.json_request(
            "POST",
            f"{self._base_url}/responses",
            headers={**self._auth_headers(), "Content-Type": "application/json"},
            payload={
                "model": EXPECTED_LONG_EXPERIMENT_MODELS["seed_visual_model"],
                "store": False,
                "thinking": {"type": "disabled"},
                "instructions": instructions,
                "input": [{"role": "user", "content": content}],
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "fitness_action_events",
                        "strict": True,
                        "schema": CandidateEnvelope.model_json_schema(),
                    }
                },
            },
        )
        require_long_success(response)
        return _json_object(response), response

    def _require_model(self, model_id: str) -> None:
        if model_id not in {
            EXPECTED_LONG_EXPERIMENT_MODELS["seed_visual_model"],
            EXPECTED_LONG_EXPERIMENT_MODELS["seed_fusion_model"],
        }:
            raise LongProviderContractError("long_seed_model_not_frozen")

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}


class LongQwenVlProvider:
    """Qwen3-VL adapter for silent chunk videos in the long-video experiment."""

    def __init__(
        self,
        *,
        api_key: str,
        transport: LongCurlTransport,
        compatible_base_url: str | None = None,
    ) -> None:
        # Kept only to make the isolated replacement mechanically compatible
        # with the old benchmark runtime constructor.  Long Qwen calls are all
        # native DashScope VL calls and must not use compatible mode.
        del compatible_base_url
        self._api_key = api_key
        self._transport = transport

    async def check_configuration(self) -> None:
        if not self._api_key.strip():
            raise GateFailure(
                PreflightStage.CONFIGURATION,
                "missing_dashscope_api_key",
                retryable=False,
            )

    async def minimal_call(self, model_id: str) -> None:
        self._require_model(model_id)
        response = await self._transport.json_request(
            "POST",
            _QWEN_NATIVE_URL,
            headers={
                **self._auth_headers(),
                "Content-Type": "application/json",
                "X-DashScope-SSE": "enable",
            },
            payload={
                "model": model_id,
                "input": {
                    "messages": [
                        {
                            "role": "user",
                            "content": [{"text": 'Return {"actions": []}' + _RAW_JSON_SUFFIX}],
                        }
                    ]
                },
                "parameters": {
                    "result_format": "message",
                    "incremental_output": True,
                    "temperature": 0,
                    "max_tokens": 256,
                },
            },
        )
        require_long_success(response)
        text, _, _, _ = _parse_qwen_native_sse(response.body)
        _validate_envelope(text)

    async def upload(self, model_id: str, path: Path, media_kind: str) -> MediaHandle:
        self._require_model(model_id)
        if media_kind != "video":
            raise LongProviderContractError("long_qwen_media_kind_invalid")
        if not path.is_file() or path.stat().st_size <= 0:
            raise LongProviderContractError("long_qwen_media_missing")

        async def request_upload_policy() -> LongCurlResponse:
            response = await self._transport.json_request(
                "GET",
                _QWEN_POLICY_URL,
                headers=self._auth_headers(),
                query={"action": "getPolicy", "model": model_id},
            )
            require_long_success(response)
            return response

        # The policy fetch has no media side effect, so it is safe to apply the
        # frozen bounded retry rule here. The multipart transfer below remains
        # strictly one-shot because its success can be transport-ambiguous.
        policy_response = await retry_long_operation(request_upload_policy)
        policy_data = _json_object(policy_response).get("data")
        if not isinstance(policy_data, dict):
            raise LongProviderContractError("qwen_policy_schema_error")
        try:
            max_file_size_mb = float(policy_data["max_file_size_mb"])
            upload_dir = str(policy_data["upload_dir"])
            upload_host = str(policy_data["upload_host"])
            suffix = path.suffix.lower() if path.suffix else ".bin"
            object_key = f"{upload_dir}/{uuid4().hex}{suffix}"
            fields = {
                "OSSAccessKeyId": str(policy_data["oss_access_key_id"]),
                "Signature": str(policy_data["signature"]),
                "policy": str(policy_data["policy"]),
                "key": object_key,
                "x-oss-object-acl": str(policy_data["x_oss_object_acl"]),
                "x-oss-forbid-overwrite": str(policy_data["x_oss_forbid_overwrite"]),
                "success_action_status": "200",
                "x-oss-content-type": mimetypes.guess_type(path)[0] or "application/octet-stream",
            }
        except (KeyError, TypeError, ValueError) as error:
            raise LongProviderContractError("qwen_policy_schema_error") from error
        if path.stat().st_size / 1024 / 1024 > max_file_size_mb:
            raise LongProviderContractError("qwen_file_too_large")
        expires_at = (datetime.now(UTC) + timedelta(hours=48)).isoformat()
        try:
            upload = await self._transport.multipart_request(
                upload_host,
                headers={"Accept": "application/json"},
                fields=fields,
                file_path=path,
                mime_type=fields["x-oss-content-type"],
            )
            require_long_success(upload)
        except asyncio.CancelledError as error:
            # Cancellation is transport-ambiguous too: curl is terminated, but
            # OSS may already have committed the object. Attach only the safe
            # provider-managed expiry boundary so the caller can journal it
            # synchronously before preserving cancellation semantics.
            error.__dict__["expires_at"] = expires_at
            raise
        except LongBenchmarkTransportError as error:
            # The transfer is deliberately not replayed. Preserve only the
            # provider-managed expiry boundary; never expose the object key.
            raise LongQwenUploadAmbiguousError(
                f"qwen_upload_{error.code}",
                expires_at=expires_at,
            ) from error
        return MediaHandle(
            provider="qwen",
            model_id=model_id,
            media_kind=media_kind,
            handle_id=f"oss://{object_key}",
            cleanup_policy=CleanupPolicy.EXPIRE_AUTOMATICALLY,
            expires_at=expires_at,
        )

    async def analyze(self, handle: MediaHandle, prompt: str) -> ProviderInference:
        self._validate_handle(handle)
        response = await self._transport.json_request(
            "POST",
            _QWEN_NATIVE_URL,
            headers={
                **self._auth_headers(),
                "Content-Type": "application/json",
                "X-DashScope-OssResourceResolve": "enable",
                "X-DashScope-SSE": "enable",
            },
            payload={
                "model": handle.model_id,
                "input": {
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"video": handle.handle_id, "fps": 1},
                                {"text": prompt + _RAW_JSON_SUFFIX},
                            ],
                        }
                    ]
                },
                "parameters": {
                    "result_format": "message",
                    "incremental_output": True,
                    "temperature": 0.1,
                    "max_tokens": 4096,
                },
            },
        )
        require_long_success(response)
        text, request_id, input_tokens, output_tokens = _parse_qwen_native_sse(response.body)
        return ProviderInference(
            envelope=_validate_envelope(text),
            request_id=request_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            first_response_seconds=response.first_byte_seconds,
            final_response_seconds=response.total_seconds,
        )

    async def cleanup(self, handle: MediaHandle) -> None:
        self._validate_handle(handle)
        if handle.expires_at is None:
            raise LongProviderContractError("qwen_expiry_missing")

    async def verify_cleanup(self, handle: MediaHandle) -> CleanupOutcome:
        try:
            self._validate_handle(handle)
            if handle.expires_at is None:
                return CleanupOutcome.FAILED
            remaining = datetime.fromisoformat(handle.expires_at) - datetime.now(UTC)
        except (LongProviderContractError, ValueError):
            return CleanupOutcome.FAILED
        return (
            CleanupOutcome.EXPIRY_RECORDED
            if timedelta(0) < remaining <= timedelta(hours=48)
            else CleanupOutcome.FAILED
        )

    def _validate_handle(self, handle: MediaHandle) -> None:
        self._require_model(handle.model_id)
        if (
            handle.provider != "qwen"
            or handle.media_kind != "video"
            or not handle.handle_id.startswith("oss://")
            or handle.cleanup_policy != CleanupPolicy.EXPIRE_AUTOMATICALLY
        ):
            raise LongProviderContractError("long_qwen_handle_invalid")

    def _require_model(self, model_id: str) -> None:
        if model_id != EXPECTED_LONG_EXPERIMENT_MODELS["qwen_visual_model"]:
            raise LongProviderContractError("long_qwen_model_not_frozen")

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}


class LongAsrClient(Protocol):
    async def check_configuration(self) -> None: ...

    async def recognize(
        self,
        *,
        audio_path: Path,
        window_start_seconds: float,
        request_id: str,
    ) -> Transcript: ...


class LongProxyVolcAsrClient(VolcAsrClient):
    """The long-study-only ASR client with a mandatory explicit SOCKS proxy.

    The base client intentionally uses a random connect ID after a retry.  The
    study instead derives a deterministic bounded suffix from the run ID so
    concurrent AB/BA arms can never share a socket identity and retries remain
    auditable without revealing source paths or media content.
    """

    def __init__(
        self,
        *,
        api_key: str,
        resource_id: str,
        url: str,
        proxy_url: str,
        chunk_duration_ms: int = 4000,
        pace_audio: bool = True,
        retry_delays: tuple[float, ...] = (1.0, 2.0),
    ) -> None:
        if not proxy_url.startswith("socks5h://"):
            raise ValueError("long ASR proxy must use explicit socks5h")
        super().__init__(
            api_key=api_key,
            resource_id=resource_id,
            url=url,
            chunk_duration_ms=chunk_duration_ms,
            pace_audio=pace_audio,
            retry_delays=retry_delays,
        )
        self._long_proxy_url = proxy_url

    async def check_configuration(self) -> None:
        if not self._api_key.strip():
            raise GateFailure(
                PreflightStage.CONFIGURATION,
                "missing_volc_asr_api_key",
                retryable=False,
            )
        if self._resource_id != EXPECTED_LONG_EXPERIMENT_MODELS["asr_resource"]:
            raise LongProviderContractError("long_asr_model_not_frozen")

    async def recognize(
        self,
        *,
        audio_path: Path,
        window_start_seconds: float,
        request_id: str,
    ) -> Transcript:
        if not _CONNECT_ID.fullmatch(request_id):
            raise ValueError("long ASR request ID must be an opaque 1-56 character token")
        chunks = await asyncio.to_thread(
            _read_pcm_chunks,
            audio_path,
            self._chunk_duration_ms,
        )
        if not chunks:
            return Transcript(text="", utterances=[], provider_request_id=None)

        attempts = len(self._retry_delays) + 1
        for attempt in range(attempts):
            try:
                return await self._recognize_once(
                    chunks=chunks,
                    window_start_seconds=window_start_seconds,
                    connect_id=long_asr_attempt_request_id(request_id, attempt),
                )
            except ProviderError as error:
                if not error.retryable or attempt == attempts - 1:
                    raise
            except InvalidStatus as error:
                mapped = _handshake_error(error)
                if not mapped.retryable or attempt == attempts - 1:
                    raise mapped from error
            except (ConnectionClosed, OSError, TimeoutError) as error:
                if attempt == attempts - 1:
                    raise ProviderError(
                        "provider_error",
                        "语音识别服务暂时不可用",
                        retryable=True,
                    ) from error
            await asyncio.sleep(self._retry_delays[attempt])
        raise AssertionError("retry loop exhausted")

    async def _recognize_once(
        self,
        *,
        chunks: list[bytes],
        window_start_seconds: float,
        connect_id: str,
    ) -> Transcript:
        async with connect(
            self._url,
            additional_headers={
                "X-Api-Key": self._api_key,
                "X-Api-Resource-Id": self._resource_id,
                "X-Api-Connect-Id": connect_id,
            },
            open_timeout=15,
            close_timeout=5,
            ping_interval=None,
            proxy=self._long_proxy_url,
        ) as websocket:
            response = websocket.response
            provider_request_id = (
                response.headers.get("X-Tt-Logid") if response is not None else None
            )
            await websocket.send(_config_frame())
            await _receive_frame(websocket, provider_request_id)

            receive_task = asyncio.create_task(
                _receive_final_result(websocket, provider_request_id)
            )
            try:
                for index, chunk in enumerate(chunks):
                    final = index == len(chunks) - 1
                    await websocket.send(_audio_frame(chunk, final=final))
                    if self._pace_audio and not final:
                        await asyncio.sleep(self._chunk_duration_ms / 1000)
                result = await receive_task
            finally:
                if not receive_task.done():
                    receive_task.cancel()
                await asyncio.gather(receive_task, return_exceptions=True)

        return _to_transcript(
            result,
            window_start_seconds=window_start_seconds,
            provider_request_id=provider_request_id,
        )


class LongAsrMediaProvider:
    """Long-study ASR media adapter with an explicit caller-supplied run ID."""

    def __init__(self, client: LongAsrClient) -> None:
        self._client = client

    async def check_configuration(self) -> None:
        await self._client.check_configuration()

    async def minimal_call(self, model_id: str) -> None:
        self._require_model(model_id)

    async def upload(self, model_id: str, path: Path, media_kind: str) -> MediaHandle:
        self._require_model(model_id)
        if media_kind != "audio" or not path.is_file():
            raise LongProviderContractError("long_asr_contract_mismatch")
        return MediaHandle(
            provider="asr",
            model_id=model_id,
            media_kind="audio",
            handle_id=str(path.resolve()),
            cleanup_policy=CleanupPolicy.LOCAL_RELEASE,
        )

    async def transcribe(self, handle: MediaHandle, *, request_id: str) -> Transcript:
        self._validate_handle(handle)
        if not _CONNECT_ID.fullmatch(request_id):
            raise LongProviderContractError("long_asr_request_id_invalid")
        return await self._client.recognize(
            audio_path=Path(handle.handle_id),
            window_start_seconds=0,
            request_id=request_id,
        )

    async def cleanup(self, handle: MediaHandle) -> None:
        self._validate_handle(handle)

    async def verify_cleanup(self, handle: MediaHandle) -> CleanupOutcome:
        try:
            self._validate_handle(handle)
        except LongProviderContractError:
            return CleanupOutcome.FAILED
        return CleanupOutcome.LOCAL_RELEASED

    def _require_model(self, model_id: str) -> None:
        if model_id != EXPECTED_LONG_EXPERIMENT_MODELS["asr_resource"]:
            raise LongProviderContractError("long_asr_model_not_frozen")

    def _validate_handle(self, handle: MediaHandle) -> None:
        self._require_model(handle.model_id)
        if (
            handle.provider != "asr"
            or handle.media_kind != "audio"
            or handle.cleanup_policy != CleanupPolicy.LOCAL_RELEASE
        ):
            raise LongProviderContractError("long_asr_handle_invalid")


def long_asr_attempt_request_id(request_id: str, attempt: int) -> str:
    """Return a stable, provider-safe connect ID for a bounded retry attempt."""
    if attempt < 0:
        raise ValueError("long ASR attempt must not be negative")
    if not _CONNECT_ID.fullmatch(request_id):
        raise ValueError("long ASR request ID must be an opaque 1-56 character token")
    if attempt == 0:
        return request_id
    digest = hashlib.sha256(f"{request_id}:{attempt}".encode()).hexdigest()[:32]
    return f"long-retry-{digest}"


def _audio_frame(audio: bytes, *, final: bool) -> bytes:
    # Importing the private helper would make the proxy client sensitive to
    # future product-ASR implementation changes.  The frame format is part of
    # the provider protocol, so this tiny encoder stays with the long adapter.
    import gzip
    import struct

    compressed = gzip.compress(audio)
    flags = 0b0010 if final else 0
    header = bytes([0x11, (0b0010 << 4) | flags, 0x01, 0x00])
    return header + struct.pack(">I", len(compressed)) + compressed


def _json_object(response: LongCurlResponse) -> dict[str, Any]:
    try:
        payload = json.loads(response.body)
    except (TypeError, ValueError) as error:
        raise LongProviderContractError("provider_json_error") from error
    if not isinstance(payload, dict):
        raise LongProviderContractError("provider_json_error")
    return payload


def _validate_envelope(text: str) -> CandidateEnvelope:
    try:
        return CandidateEnvelope.model_validate_json(text)
    except ValidationError as error:
        first = error.errors(include_input=False)[0]
        location = ".".join(str(item) for item in first.get("loc", ())) or "root"
        diagnostic = f"{first.get('type', 'validation_error')}:{location}"
        raise LongProviderContractError(
            "candidate_schema_error",
            diagnostic=diagnostic,
        ) from error


def _ark_output_text(payload: dict[str, Any]) -> str:
    top_level = payload.get("output_text")
    if isinstance(top_level, str):
        return top_level
    outputs = payload.get("output", [])
    if isinstance(outputs, list):
        for output in outputs:
            if not isinstance(output, dict):
                continue
            content = output.get("content", [])
            if not isinstance(content, list):
                continue
            for item in content:
                if isinstance(item, dict) and item.get("type") == "output_text":
                    text = item.get("text")
                    if isinstance(text, str):
                        return text
    raise LongProviderContractError("ark_output_text_missing")


def _parse_ark_envelope(
    payload: dict[str, Any],
) -> tuple[CandidateEnvelope, str | None, int | None, int | None]:
    usage_value = payload.get("usage")
    usage = usage_value if isinstance(usage_value, dict) else {}
    return (
        _validate_envelope(_ark_output_text(payload)),
        str(payload["id"]) if payload.get("id") else None,
        _optional_int(usage.get("input_tokens")),
        _optional_int(usage.get("output_tokens")),
    )


def _parse_qwen_native_sse(
    body: str,
) -> tuple[str, str | None, int | None, int | None]:
    chunks: list[str] = []
    request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if data == "[DONE]":
            break
        try:
            event = json.loads(data)
        except ValueError as error:
            raise LongProviderContractError("qwen_sse_schema_error") from error
        if not isinstance(event, dict):
            raise LongProviderContractError("qwen_sse_schema_error")
        if event.get("request_id"):
            request_id = str(event["request_id"])
        output = event.get("output")
        choices = output.get("choices", []) if isinstance(output, dict) else []
        if isinstance(choices, list) and choices:
            first_choice = choices[0]
            message = first_choice.get("message") if isinstance(first_choice, dict) else None
            for text in _qwen_message_text_parts(message):
                # DashScope streaming variants can emit deltas or a cumulative
                # message.  Normalize only the transport representation: never
                # edit model text or attempt a second schema repair.
                if chunks and text.startswith("".join(chunks)):
                    chunks = [text]
                else:
                    chunks.append(text)
        usage = event.get("usage")
        if isinstance(usage, dict):
            input_tokens = _optional_int(usage.get("input_tokens"))
            output_tokens = _optional_int(usage.get("output_tokens"))
    if not chunks:
        raise LongProviderContractError("qwen_sse_text_missing")
    return "".join(chunks), request_id, input_tokens, output_tokens


def _qwen_message_text_parts(message: object) -> list[str]:
    if not isinstance(message, dict):
        return []
    content = message.get("content", [])
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    return [
        text
        for part in content
        if isinstance(part, dict) and isinstance((text := part.get("text")), str)
    ]


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)


# Short aliases make the dependency injection seam read naturally in callers.
LongSeedProvider = LongSeedMediaProvider
LongQwenProvider = LongQwenVlProvider
