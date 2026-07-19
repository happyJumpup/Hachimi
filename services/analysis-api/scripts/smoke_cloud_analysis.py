import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx

from hakimi_analysis.bootstrap import build_catalog, build_pipeline
from hakimi_analysis.models import EvidenceType, RunStage
from hakimi_analysis.pipeline import PipelineFailure
from hakimi_analysis.providers.base import ProviderError
from hakimi_analysis.settings import PROJECT_ROOT, Settings


class SmokeFailure(RuntimeError):
    def __init__(self, code: str, provider_calls: list[dict[str, object]]) -> None:
        super().__init__(code)
        self.code = code
        self.provider_calls = provider_calls


def _temporary_entries(root: Path) -> set[str]:
    if not root.exists():
        return set()
    return {entry.name for entry in root.iterdir()}


def _is_expected_action(name: str) -> bool:
    normalized = "".join(name.lower().split())
    return ("drag" in normalized and "curl" in normalized) or (
        "拖" in normalized and "弯举" in normalized
    )


def _provider_call(response: httpx.Response) -> dict[str, object]:
    payload: dict[str, Any] = {}
    try:
        parsed = response.json()
        if isinstance(parsed, dict):
            payload = parsed
    except ValueError:
        pass
    error_payload = payload.get("error")
    error = error_payload if isinstance(error_payload, dict) else {}
    return {
        "provider": "asr" if "openspeech" in response.request.url.host else "ark",
        "provider_status": response.headers.get("X-Api-Status-Code"),
        "provider_request_id": response.headers.get("X-Tt-Logid")
        or response.headers.get("X-Request-Id"),
        "provider_error_code": error.get("code") or payload.get("code"),
    }


async def run_smoke() -> dict[str, Any]:
    settings = Settings()
    if settings.analysis_provider != "cloud":
        raise RuntimeError("cloud_provider_required")
    if settings.ark_api_key is None or settings.volc_asr_api_key is None:
        raise RuntimeError("cloud_credentials_missing")

    catalog = build_catalog(settings)
    source = catalog.get("legacy-arm-workout")
    temp_root = PROJECT_ROOT / "tmp" / "analysis-runs"
    before_entries = _temporary_entries(temp_root)
    events: list[tuple[str, dict[str, object]]] = []
    provider_calls: list[dict[str, object]] = []

    async def emit(
        _stage: RunStage,
        event_type: str,
        data: dict[str, object],
    ) -> None:
        events.append((event_type, data))

    async def observe_response(response: httpx.Response) -> None:
        await response.aread()
        provider_calls.append(_provider_call(response))

    started = time.perf_counter()
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(settings.run_timeout_seconds, connect=15),
        follow_redirects=False,
        event_hooks={"response": [observe_response]},
    ) as http_client:
        pipeline = build_pipeline(settings, http_client)
        try:
            async with asyncio.timeout(settings.run_timeout_seconds):
                result = await pipeline.analyze(source, 45, emit)
        except (PipelineFailure, ProviderError) as error:
            raise SmokeFailure(error.code, provider_calls) from error
    elapsed_seconds = round(time.perf_counter() - started, 2)

    after_entries = _temporary_entries(temp_root)
    if after_entries != before_entries:
        raise SmokeFailure("local_temp_cleanup_failed", provider_calls)

    completed_branches = {
        str(data.get("branch"))
        for event_type, data in events
        if event_type == "branch.completed"
    }
    if completed_branches != {"speech", "visual"}:
        raise SmokeFailure("both_cloud_branches_did_not_complete", provider_calls)

    matching = [
        candidate
        for candidate in result.candidates
        if _is_expected_action(candidate.name)
        and candidate.segment is not None
        and candidate.segment.start_seconds < 51
        and candidate.segment.end_seconds > 41
    ]
    if not matching:
        raise SmokeFailure("expected_action_or_time_intersection_missing", provider_calls)
    if not any(
        {evidence.type for evidence in candidate.evidence}
        >= {EvidenceType.SPEECH, EvidenceType.VISUAL}
        for candidate in matching
    ):
        raise SmokeFailure("fused_dual_evidence_missing", provider_calls)
    if any(
        "weight" in json.dumps(candidate.model_dump(mode="json"), ensure_ascii=False).lower()
        for candidate in result.candidates
    ):
        raise SmokeFailure("weight_leaked_into_candidate", provider_calls)

    return {
        "status": "PASS",
        "elapsed_seconds": elapsed_seconds,
    }


async def main() -> int:
    try:
        result = await run_smoke()
    except SmokeFailure as error:
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "error_code": error.code,
                    "provider_diagnostics": error.provider_calls,
                },
                ensure_ascii=False,
            )
        )
        return 1
    except Exception as error:
        runtime_code = str(error) if isinstance(error, RuntimeError) else ""
        safe_code = (
            runtime_code
            if runtime_code in {"cloud_provider_required", "cloud_credentials_missing"}
            else "unexpected_error"
        )
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "error_code": safe_code,
                },
                ensure_ascii=False,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
