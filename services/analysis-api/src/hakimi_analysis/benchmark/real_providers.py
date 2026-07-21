import asyncio
import json
import mimetypes
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from hakimi_analysis.benchmark.manifest import EXPECTED_MODELS
from hakimi_analysis.benchmark.models import (
    CandidateEnvelope,
    CleanupOutcome,
    CleanupPolicy,
    MediaHandle,
    ProviderInference,
    RouteExecution,
)
from hakimi_analysis.benchmark.prompts import (
    AUDIO_ONLY_SUFFIX,
    TASK_INSTRUCTIONS,
    VISUAL_ONLY_SUFFIX,
    fusion_prompt,
)
from hakimi_analysis.benchmark.runner import GateFailure, PreflightStage, _cleanup_handle
from hakimi_analysis.benchmark.transport import (
    BenchmarkTransportError,
    CurlResponse,
    CurlTransport,
    require_success,
)
from hakimi_analysis.models import Transcript
from hakimi_analysis.providers.asr import VolcAsrClient

_QWEN_POLICY_URL = "https://dashscope.aliyuncs.com/api/v1/uploads"
_QWEN_NATIVE_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/"
    "multimodal-generation/generation"
)


class ProviderContractError(RuntimeError):
    def __init__(self, code: str, *, diagnostic: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.diagnostic = diagnostic


class SeedMediaProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        transport: CurlTransport,
        poll_interval_seconds: float = 1,
        poll_limit: int = 90,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._poll_interval_seconds = poll_interval_seconds
        self._poll_limit = poll_limit
        self._deleted: set[str] = set()

    async def check_configuration(self) -> None:
        if not self._api_key.strip():
            raise GateFailure(
                PreflightStage.CONFIGURATION,
                "missing_ark_api_key",
                retryable=False,
            )

    async def minimal_call(self, model_id: str) -> None:
        if model_id not in {
            EXPECTED_MODELS["seed_lite"],
            EXPECTED_MODELS["seed_mini"],
        }:
            raise ProviderContractError("seed_model_not_frozen")
        await self._text_response(model_id, "只返回 {\"actions\": []}")

    async def upload(self, model_id: str, path: Path, media_kind: str) -> MediaHandle:
        if model_id not in {EXPECTED_MODELS["seed_lite"]}:
            raise ProviderContractError("seed_media_model_not_frozen_lite")
        fields = {"purpose": "user_data"}
        if media_kind == "video":
            fields["preprocess_configs[video][fps]"] = "1"
        response = await self._transport.multipart_request(
            f"{self._base_url}/files",
            headers=self._auth_headers(),
            fields=fields,
            file_path=path,
            mime_type=mimetypes.guess_type(path)[0] or "application/octet-stream",
        )
        require_success(response)
        payload = _json_object(response)
        try:
            file_id = str(payload["id"])
            status = str(payload.get("status", ""))
        except (KeyError, TypeError, ValueError) as error:
            raise ProviderContractError("ark_upload_schema_error") from error
        handle = MediaHandle(
            provider="seed",
            model_id=model_id,
            media_kind=media_kind,
            handle_id=file_id,
            cleanup_policy=CleanupPolicy.DELETE_AND_VERIFY,
        )
        if status not in {"processed", "succeeded", "completed", "active"}:
            try:
                await self._wait_until_ready(file_id)
            except BaseException as primary_error:
                cleanup_outcome, _ = await _cleanup_handle(self, handle)
                if cleanup_outcome == CleanupOutcome.FAILED:
                    primary_error.add_note("ark_cleanup_failed")
                raise
        return handle

    async def probe(self, handle: MediaHandle) -> None:
        await self.analyze(handle, TASK_INSTRUCTIONS)

    async def analyze(
        self,
        handle: MediaHandle,
        prompt: str,
    ) -> ProviderInference:
        content_type = "input_video" if handle.media_kind == "video" else "input_audio"
        payload, response = await self._response(
            model_id=handle.model_id,
            instructions=prompt,
            content=[{"type": content_type, "file_id": handle.handle_id}],
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

    async def complete_text(
        self,
        prompt: str,
    ) -> ProviderInference:
        payload, response = await self._response(
            model_id=EXPECTED_MODELS["seed_mini"],
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

    async def cleanup(self, handle: MediaHandle) -> None:
        response = await self._transport.json_request(
            "DELETE",
            f"{self._base_url}/files/{handle.handle_id}",
            headers=self._auth_headers(),
        )
        require_success(response)
        try:
            payload = _json_object(response)
        except ProviderContractError:
            payload = {}
        if payload.get("deleted") is False:
            raise ProviderContractError("ark_delete_not_confirmed")
        verification = await self._transport.json_request(
            "GET",
            f"{self._base_url}/files/{handle.handle_id}",
            headers=self._auth_headers(),
        )
        if verification.status_code not in {404, 410}:
            raise ProviderContractError("ark_delete_verification_failed")
        self._deleted.add(handle.handle_id)

    async def verify_cleanup(self, handle: MediaHandle) -> CleanupOutcome:
        return (
            CleanupOutcome.DELETED_VERIFIED
            if handle.handle_id in self._deleted
            else CleanupOutcome.FAILED
        )

    async def _text_response(self, model_id: str, prompt: str) -> None:
        payload, _ = await self._response(
            model_id=model_id,
            instructions=prompt,
            content=[{"type": "input_text", "text": prompt}],
        )
        _ark_output_text(payload)

    async def _response(
        self,
        *,
        model_id: str,
        instructions: str,
        content: list[dict[str, str]],
    ) -> tuple[dict[str, Any], CurlResponse]:
        response = await self._transport.json_request(
            "POST",
            f"{self._base_url}/responses",
            headers={**self._auth_headers(), "Content-Type": "application/json"},
            payload={
                "model": model_id,
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
        require_success(response)
        return _json_object(response), response

    async def _wait_until_ready(self, file_id: str) -> None:
        for _ in range(self._poll_limit):
            response = await self._transport.json_request(
                "GET",
                f"{self._base_url}/files/{file_id}",
                headers=self._auth_headers(),
            )
            require_success(response)
            status = str(_json_object(response).get("status", ""))
            if status in {"processed", "succeeded", "completed", "active"}:
                return
            if status in {"failed", "error", "cancelled"}:
                raise ProviderContractError("ark_preprocessing_failed")
            await asyncio.sleep(self._poll_interval_seconds)
        raise ProviderContractError("ark_preprocessing_timeout")

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}


class QwenMediaProvider:
    def __init__(
        self,
        *,
        api_key: str,
        compatible_base_url: str,
        transport: CurlTransport,
    ) -> None:
        self._api_key = api_key
        self._compatible_base_url = compatible_base_url.rstrip("/")
        self._transport = transport

    async def check_configuration(self) -> None:
        if not self._api_key.strip():
            raise GateFailure(
                PreflightStage.CONFIGURATION,
                "missing_dashscope_api_key",
                retryable=False,
            )

    async def minimal_call(self, model_id: str) -> None:
        if model_id not in {
            EXPECTED_MODELS["qwen_omni"],
            EXPECTED_MODELS["qwen_vl"],
        }:
            raise ProviderContractError("qwen_model_not_frozen")
        await self._text_probe(model_id)

    async def upload(self, model_id: str, path: Path, media_kind: str) -> MediaHandle:
        if model_id not in {EXPECTED_MODELS["qwen_omni"], EXPECTED_MODELS["qwen_vl"]}:
            raise ProviderContractError("qwen_model_not_frozen")
        policy_response = await self._transport.json_request(
            "GET",
            _QWEN_POLICY_URL,
            headers=self._auth_headers(),
            query={"action": "getPolicy", "model": model_id},
        )
        require_success(policy_response)
        policy = _json_object(policy_response).get("data")
        if not isinstance(policy, dict):
            raise ProviderContractError("qwen_policy_schema_error")
        size_mb = path.stat().st_size / 1024 / 1024
        if size_mb > float(policy["max_file_size_mb"]):
            raise ProviderContractError("qwen_file_too_large")
        object_key = f"{policy['upload_dir']}/{path.name}"
        fields = {
            "OSSAccessKeyId": str(policy["oss_access_key_id"]),
            "Signature": str(policy["signature"]),
            "policy": str(policy["policy"]),
            "key": object_key,
            "x-oss-object-acl": str(policy["x_oss_object_acl"]),
            "x-oss-forbid-overwrite": str(policy["x_oss_forbid_overwrite"]),
            "success_action_status": "200",
            "x-oss-content-type": mimetypes.guess_type(path)[0]
            or "application/octet-stream",
        }
        upload = await self._transport.multipart_request(
            str(policy["upload_host"]),
            headers={"Accept": "application/json"},
            fields=fields,
            file_path=path,
            mime_type=fields["x-oss-content-type"],
        )
        require_success(upload)
        return MediaHandle(
            provider="qwen",
            model_id=model_id,
            media_kind=media_kind,
            handle_id=f"oss://{object_key}",
            cleanup_policy=CleanupPolicy.EXPIRE_AUTOMATICALLY,
            expires_at=(datetime.now(UTC) + timedelta(hours=48)).isoformat(),
        )

    async def probe(self, handle: MediaHandle) -> None:
        await self.analyze(handle, TASK_INSTRUCTIONS)

    async def analyze(
        self,
        handle: MediaHandle,
        prompt: str,
    ) -> ProviderInference:
        if handle.model_id == EXPECTED_MODELS["qwen_omni"]:
            return await self._omni_analyze(handle, prompt)
        if handle.model_id == EXPECTED_MODELS["qwen_vl"]:
            return await self._vl_analyze(handle, prompt)
        raise ProviderContractError("qwen_model_not_frozen")

    async def cleanup(self, handle: MediaHandle) -> None:
        if handle.cleanup_policy != CleanupPolicy.EXPIRE_AUTOMATICALLY:
            raise ProviderContractError("qwen_cleanup_policy_mismatch")
        if handle.expires_at is None:
            raise ProviderContractError("qwen_expiry_missing")

    async def verify_cleanup(self, handle: MediaHandle) -> CleanupOutcome:
        if (
            handle.cleanup_policy != CleanupPolicy.EXPIRE_AUTOMATICALLY
            or handle.expires_at is None
        ):
            return CleanupOutcome.FAILED
        expires_at = datetime.fromisoformat(handle.expires_at)
        remaining = expires_at - datetime.now(UTC)
        return (
            CleanupOutcome.EXPIRY_RECORDED
            if timedelta(0) < remaining <= timedelta(hours=48)
            else CleanupOutcome.FAILED
        )

    async def _text_probe(self, model_id: str) -> None:
        if model_id == EXPECTED_MODELS["qwen_vl"]:
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
                                "content": [{"text": "只回答 ok"}],
                            }
                        ]
                    },
                    "parameters": {
                        "result_format": "message",
                        "incremental_output": True,
                    },
                },
            )
            require_success(response)
            _parse_qwen_native_sse(response.body)
            return
        response = await self._transport.json_request(
            "POST",
            f"{self._compatible_base_url}/chat/completions",
            headers={**self._auth_headers(), "Content-Type": "application/json"},
            payload={
                "model": model_id,
                "messages": [{"role": "user", "content": "只回答 ok"}],
                "stream": True,
                "stream_options": {"include_usage": True},
                "modalities": ["text"],
            },
        )
        require_success(response)
        _parse_openai_sse(response.body)

    async def _omni_analyze(
        self,
        handle: MediaHandle,
        prompt: str,
    ) -> ProviderInference:
        media = (
            {"type": "video_url", "video_url": {"url": handle.handle_id}}
            if handle.media_kind == "video"
            else {
                "type": "input_audio",
                "input_audio": {"data": handle.handle_id, "format": "wav"},
            }
        )
        response = await self._transport.json_request(
            "POST",
            f"{self._compatible_base_url}/chat/completions",
            headers={
                **self._auth_headers(),
                "Content-Type": "application/json",
                "X-DashScope-OssResourceResolve": "enable",
            },
            payload={
                "model": handle.model_id,
                "messages": [
                    {"role": "user", "content": [media, {"type": "text", "text": prompt}]}
                ],
                "stream": True,
                "stream_options": {"include_usage": True},
                "modalities": ["text"],
                "response_format": {"type": "json_object"},
                "temperature": 0.1,
            },
        )
        require_success(response)
        text, request_id, input_tokens, output_tokens = _parse_openai_sse(response.body)
        return ProviderInference(
            envelope=_validate_envelope(text),
            request_id=request_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            first_response_seconds=response.first_byte_seconds,
            final_response_seconds=response.total_seconds,
        )

    async def _vl_analyze(
        self,
        handle: MediaHandle,
        prompt: str,
    ) -> ProviderInference:
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
                                {"text": prompt + VISUAL_ONLY_SUFFIX},
                            ],
                        }
                    ]
                },
                "parameters": {
                    "result_format": "message",
                    "response_format": {"type": "json_object"},
                    "incremental_output": True,
                    "temperature": 0.1,
                    "max_tokens": 4096,
                },
            },
        )
        require_success(response)
        text, request_id, input_tokens, output_tokens = _parse_qwen_native_sse(response.body)
        return ProviderInference(
            envelope=_validate_envelope(text),
            request_id=request_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            first_response_seconds=response.first_byte_seconds,
            final_response_seconds=response.total_seconds,
        )

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}


class AsrMediaProvider:
    def __init__(self, client: VolcAsrClient) -> None:
        self._client = client

    async def check_configuration(self) -> None:
        return None

    async def minimal_call(self, model_id: str) -> None:
        if model_id != EXPECTED_MODELS["asr_resource"]:
            raise ProviderContractError("asr_model_not_frozen")
        return None

    async def upload(self, model_id: str, path: Path, media_kind: str) -> MediaHandle:
        if model_id != EXPECTED_MODELS["asr_resource"] or media_kind != "audio":
            raise ProviderContractError("asr_contract_mismatch")
        return MediaHandle(
            provider="asr",
            model_id=model_id,
            media_kind="audio",
            handle_id=str(path.resolve()),
            cleanup_policy=CleanupPolicy.LOCAL_RELEASE,
        )

    async def probe(self, handle: MediaHandle) -> None:
        await self.transcribe(handle, run_index=0)

    async def transcribe(self, handle: MediaHandle, *, run_index: int) -> Transcript:
        return await self._client.recognize(
            audio_path=Path(handle.handle_id),
            window_start_seconds=0,
            request_id=f"benchmark-{run_index}",
        )

    async def cleanup(self, handle: MediaHandle) -> None:
        if handle.cleanup_policy != CleanupPolicy.LOCAL_RELEASE:
            raise ProviderContractError("asr_cleanup_policy_mismatch")

    async def verify_cleanup(self, handle: MediaHandle) -> CleanupOutcome:
        return (
            CleanupOutcome.LOCAL_RELEASED
            if handle.cleanup_policy == CleanupPolicy.LOCAL_RELEASE
            else CleanupOutcome.FAILED
        )


class NativeMediaAdapter:
    def __init__(
        self,
        provider: SeedMediaProvider | QwenMediaProvider,
        *,
        handle_role: str,
        prompt_suffix: str = "",
    ) -> None:
        self._provider = provider
        self._handle_role = handle_role
        self._prompt_suffix = prompt_suffix

    async def execute(
        self,
        handles: Mapping[str, MediaHandle],
        *,
        run_index: int,
    ) -> RouteExecution:
        del run_index
        started = time.perf_counter()
        result = await self._provider.analyze(
            handles[self._handle_role], TASK_INSTRUCTIONS + self._prompt_suffix
        )
        elapsed = time.perf_counter() - started
        return RouteExecution(
            candidates=result.envelope.actions,
            provider_request_id=result.request_id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            first_response_seconds=result.first_response_seconds,
            first_parseable_candidate_seconds=elapsed,
            final_result_seconds=elapsed,
        )


class AsrTextAdapter:
    def __init__(self, asr: AsrMediaProvider, seed: SeedMediaProvider) -> None:
        self._asr = asr
        self._seed = seed

    async def execute(
        self,
        handles: Mapping[str, MediaHandle],
        *,
        run_index: int,
    ) -> RouteExecution:
        started = time.perf_counter()
        transcript = await self._asr.transcribe(handles["audio"], run_index=run_index)
        preprocessing_seconds = time.perf_counter() - started
        fusion_started = time.perf_counter()
        result = await self._seed.complete_text(
            fusion_prompt(transcript.model_dump(mode="json"), None) + AUDIO_ONLY_SUFFIX
        )
        fusion_seconds = time.perf_counter() - fusion_started
        elapsed = time.perf_counter() - started
        return RouteExecution(
            candidates=result.envelope.actions,
            provider_request_id=result.request_id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            preprocessing_seconds=preprocessing_seconds,
            first_response_seconds=(
                preprocessing_seconds + result.first_response_seconds
                if result.first_response_seconds is not None
                else None
            ),
            first_parseable_candidate_seconds=elapsed,
            final_result_seconds=elapsed,
            fusion_seconds=fusion_seconds,
        )


class ModularAdapter:
    def __init__(
        self,
        visual: SeedMediaProvider | QwenMediaProvider,
        asr: AsrMediaProvider,
        seed: SeedMediaProvider,
    ) -> None:
        self._visual = visual
        self._asr = asr
        self._seed = seed

    async def execute(
        self,
        handles: Mapping[str, MediaHandle],
        *,
        run_index: int,
    ) -> RouteExecution:
        started = time.perf_counter()
        visual_task = asyncio.create_task(
            self._visual.analyze(
                handles["visual"], TASK_INSTRUCTIONS + VISUAL_ONLY_SUFFIX
            )
        )
        asr_task = asyncio.create_task(
            self._asr.transcribe(handles["audio"], run_index=run_index)
        )
        try:
            visual_result, transcript = await asyncio.gather(visual_task, asr_task)
        except BaseException:
            visual_task.cancel()
            asr_task.cancel()
            await asyncio.gather(visual_task, asr_task, return_exceptions=True)
            raise
        preprocessing_seconds = time.perf_counter() - started
        visual_envelope = visual_result.envelope
        fusion_started = time.perf_counter()
        fused = await self._seed.complete_text(
            fusion_prompt(transcript.model_dump(mode="json"), visual_envelope)
        )
        fusion_seconds = time.perf_counter() - fusion_started
        elapsed = time.perf_counter() - started
        return RouteExecution(
            candidates=fused.envelope.actions,
            visual_control=visual_envelope.actions,
            provider_request_id=fused.request_id or visual_result.request_id,
            input_tokens=(fused.input_tokens or 0) + (visual_result.input_tokens or 0),
            output_tokens=(fused.output_tokens or 0) + (visual_result.output_tokens or 0),
            preprocessing_seconds=preprocessing_seconds,
            first_response_seconds=(
                preprocessing_seconds + fused.first_response_seconds
                if fused.first_response_seconds is not None
                else None
            ),
            first_parseable_candidate_seconds=elapsed,
            final_result_seconds=elapsed,
            fusion_seconds=fusion_seconds,
        )


def _json_object(response: CurlResponse) -> dict[str, Any]:
    try:
        payload = json.loads(response.body)
    except (TypeError, ValueError) as error:
        raise ProviderContractError("provider_json_error") from error
    if not isinstance(payload, dict):
        raise ProviderContractError("provider_json_error")
    return payload


def _validate_envelope(text: str) -> CandidateEnvelope:
    try:
        return CandidateEnvelope.model_validate_json(text)
    except ValidationError as error:
        first = error.errors(include_input=False)[0]
        location = ".".join(str(item) for item in first.get("loc", ())) or "root"
        diagnostic = f"{first.get('type', 'validation_error')}:{location}"
        raise ProviderContractError(
            "candidate_schema_error",
            diagnostic=diagnostic,
        ) from error


def _ark_output_text(payload: dict[str, Any]) -> str:
    top_level = payload.get("output_text")
    if isinstance(top_level, str):
        return top_level
    for output in payload.get("output", []):
        if not isinstance(output, dict):
            continue
        for content in output.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str):
                    return text
    raise ProviderContractError("ark_output_text_missing")


def _parse_ark_envelope(
    payload: dict[str, Any],
) -> tuple[CandidateEnvelope, str | None, int | None, int | None]:
    usage_value = payload.get("usage")
    usage: dict[str, Any] = usage_value if isinstance(usage_value, dict) else {}
    return (
        _validate_envelope(_ark_output_text(payload)),
        str(payload["id"]) if payload.get("id") else None,
        _optional_int(usage.get("input_tokens")),
        _optional_int(usage.get("output_tokens")),
    )


def _parse_openai_sse(body: str) -> tuple[str, str | None, int | None, int | None]:
    chunks: list[str] = []
    request_id = None
    input_tokens = output_tokens = None
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if data == "[DONE]":
            break
        try:
            event = json.loads(data)
        except ValueError as error:
            raise ProviderContractError("qwen_sse_schema_error") from error
        if event.get("id"):
            request_id = str(event["id"])
        choices = event.get("choices", [])
        if choices:
            content = choices[0].get("delta", {}).get("content")
            if isinstance(content, str):
                chunks.append(content)
        usage = event.get("usage")
        if isinstance(usage, dict):
            input_tokens = _optional_int(usage.get("prompt_tokens"))
            output_tokens = _optional_int(usage.get("completion_tokens"))
    if not chunks:
        raise ProviderContractError("qwen_sse_text_missing")
    return "".join(chunks), request_id, input_tokens, output_tokens


def _parse_qwen_native_sse(
    body: str,
) -> tuple[str, str | None, int | None, int | None]:
    chunks: list[str] = []
    request_id = None
    input_tokens = output_tokens = None
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if data == "[DONE]":
            break
        try:
            event = json.loads(data)
        except ValueError as error:
            raise ProviderContractError("qwen_sse_schema_error") from error
        if event.get("request_id"):
            request_id = str(event["request_id"])
        choices = event.get("output", {}).get("choices", [])
        if choices:
            for part in choices[0].get("message", {}).get("content", []):
                text = part.get("text") if isinstance(part, dict) else None
                if isinstance(text, str):
                    chunks.append(text)
        usage = event.get("usage")
        if isinstance(usage, dict):
            input_tokens = _optional_int(usage.get("input_tokens"))
            output_tokens = _optional_int(usage.get("output_tokens"))
    if not chunks:
        raise ProviderContractError("qwen_sse_text_missing")
    return "".join(chunks), request_id, input_tokens, output_tokens


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, int | float) else None


def map_transport_error(error: BenchmarkTransportError, stage: PreflightStage) -> GateFailure:
    return GateFailure(stage, error.code, retryable=error.retryable)
