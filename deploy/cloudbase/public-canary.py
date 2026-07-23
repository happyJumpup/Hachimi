from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from runpy import run_path
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import imageio_ffmpeg

_PRIVATE_SUPPORT = run_path(str(Path(__file__).with_name("private-canary.py")))
CanaryError = cast(type[RuntimeError], _PRIVATE_SUPPORT["CanaryError"])

PUBLIC_UPLOAD_MAX_BYTES = 19_000_000
LOCAL_CANARY_DURATION_SECONDS = 295.0
LOCAL_CANARY_DURATION_TOLERANCE_SECONDS = 0.5
PARALLEL_REQUESTS = 2
EXPECTED_ACCEPTED_RUNS = 1
MIN_COOLDOWN_SECONDS = 1
MAX_COOLDOWN_SECONDS = 600
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
PASSING_COVERAGE_STATUSES = {"complete", "partial"}
ADMISSION_REASON_HEADER = "X-TrainPal-Admission-Reason"
CAPACITY_ADMISSION_REASON = "capacity"
RUNTIME_CLEANUP_TIMEOUT_SECONDS = 30.0


def _is_295_second_duration(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    duration_seconds = float(value)
    return math.isfinite(duration_seconds) and (
        abs(duration_seconds - LOCAL_CANARY_DURATION_SECONDS)
        <= LOCAL_CANARY_DURATION_TOLERANCE_SECONDS
    )


def probe_local_canary_duration(media_path: Path) -> float:
    try:
        reader: Any = imageio_ffmpeg.read_frames(str(media_path), pix_fmt="rgb24")
        try:
            metadata = next(reader)
        finally:
            reader.close()
    except (OSError, RuntimeError, StopIteration, TypeError, ValueError) as error:
        raise CanaryError(
            "local canary media duration could not be verified"
        ) from error
    duration_seconds = metadata.get("duration") if isinstance(metadata, dict) else None
    if not _is_295_second_duration(duration_seconds):
        raise CanaryError(
            "local canary media must be 295 seconds within a 0.5 second tolerance"
        )
    return float(cast(int | float, duration_seconds))


@dataclass(frozen=True, slots=True)
class WorkerResult:
    upload_status: int
    terminal_event_observed: bool
    terminal_status: str | None
    coverage_status: str | None
    retry_after_present: bool
    admission_reason: str | None = None
    reliable_candidate_count: int = 0
    coverage_gap_count: int = 0
    session_cooldown_enforced: bool = False
    session_retry_after_seconds: int | None = None


def summarize_terminal(
    payload: dict[str, Any],
    *,
    terminal_event_observed: bool,
    session_retry_after_seconds: int,
    require_local_295_seconds: bool = False,
) -> WorkerResult:
    if require_local_295_seconds and not _is_295_second_duration(
        payload.get("source_duration_seconds")
    ):
        raise CanaryError(
            "local canary terminal duration must prove 295 seconds within tolerance"
        )
    candidates = payload.get("candidates")
    coverage_gaps = payload.get("coverage_gaps")
    if not isinstance(candidates, list) or not isinstance(coverage_gaps, list):
        raise CanaryError("analysis terminal payload omitted coverage evidence")
    if any(not isinstance(gap, dict) for gap in coverage_gaps):
        raise CanaryError("analysis terminal payload returned invalid coverage gaps")
    reliable_candidate_count = 0
    for candidate in candidates:
        if (
            not isinstance(candidate, dict)
            or type(candidate.get("needs_confirmation")) is not bool
        ):
            raise CanaryError("analysis terminal payload returned invalid candidates")
        if candidate["needs_confirmation"] is False:
            reliable_candidate_count += 1
    terminal_status = payload.get("status")
    coverage_status = payload.get("coverage_status")
    return WorkerResult(
        upload_status=202,
        terminal_event_observed=terminal_event_observed,
        terminal_status=terminal_status if isinstance(terminal_status, str) else None,
        coverage_status=coverage_status if isinstance(coverage_status, str) else None,
        retry_after_present=False,
        reliable_candidate_count=reliable_candidate_count,
        coverage_gap_count=len(coverage_gaps),
        session_cooldown_enforced=True,
        session_retry_after_seconds=session_retry_after_seconds,
    )


def normalize_public_origin(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise CanaryError("public canary origin must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise CanaryError("public canary origin must not contain credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise CanaryError(
            "public canary origin must not contain a path, query, or fragment"
        )
    try:
        port = parsed.port
    except ValueError as error:
        raise CanaryError("public canary origin contains an invalid port") from error
    if port not in {None, 443}:
        raise CanaryError("public canary origin must use the default HTTPS port")
    return f"https://{parsed.hostname.lower()}"


def select_shortest_controlled_source(
    payload: object,
) -> tuple[str, float]:
    if not isinstance(payload, list) or len(payload) != 5:
        raise CanaryError("controlled source canary requires exactly five sources")
    sources: list[tuple[str, float]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise CanaryError("controlled source canary received an invalid source catalog")
        source_id = item.get("id")
        duration = item.get("duration_seconds")
        if (
            not isinstance(source_id, str)
            or not source_id
            or isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not 0 < float(duration) <= 300
        ):
            raise CanaryError("controlled source canary received an invalid source catalog")
        sources.append((source_id, float(duration)))
    if len({source_id for source_id, _ in sources}) != len(sources):
        raise CanaryError("controlled source canary received an invalid source catalog")
    return min(sources, key=lambda source: (source[1], source[0]))


def _expect_json(
    response: httpx.Response, expected_status: int, label: str
) -> dict[str, Any]:
    if response.status_code != expected_status:
        raise CanaryError(f"{label} failed with HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as error:
        raise CanaryError(f"{label} returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise CanaryError(f"{label} returned an invalid JSON shape")
    return payload


class PublicCanaryClient:
    def __init__(self, *, origin: str, timeout_seconds: float) -> None:
        self._origin = origin
        self._client = httpx.Client(
            base_url=origin,
            follow_redirects=False,
            headers={"Origin": origin},
            timeout=httpx.Timeout(
                connect=30,
                read=timeout_seconds,
                write=timeout_seconds,
                pool=30,
            ),
        )

    def close(self) -> None:
        self._client.close()

    def get_json(self, path: str, *, label: str) -> dict[str, Any]:
        return _expect_json(self._client.get(path), 200, label)

    def prepare_public_session(self) -> None:
        public_session = self.get_json(
            "/api/v1/access/session",
            label="public session",
        )
        if public_session.get("tier") != "public" or not public_session.get(
            "can_analyze"
        ):
            raise CanaryError("public analysis is not available")

    def list_controlled_sources(self) -> object:
        response = self._client.get("/api/v1/sources")
        if response.status_code != 200:
            raise CanaryError(
                f"controlled source catalog failed with HTTP {response.status_code}"
            )
        try:
            return response.json()
        except ValueError as error:
            raise CanaryError("controlled source catalog returned invalid JSON") from error

    def require_analysis_cooldown(self, *, label: str) -> int:
        session = self.get_json("/api/v1/access/session", label=label)
        retry_after_seconds = session.get("retry_after_seconds")
        if (
            session.get("can_analyze") is not False
            or type(retry_after_seconds) is not int
            or not MIN_COOLDOWN_SECONDS <= retry_after_seconds <= MAX_COOLDOWN_SECONDS
        ):
            raise CanaryError(f"{label} did not prove the analysis cooldown")
        return retry_after_seconds

    def upload_video(self, media_path: Path) -> httpx.Response:
        with media_path.open("rb") as media:
            return self._client.post(
                "/api/v1/analysis-runs/local",
                data={"local_source_id": f"local:{uuid4()}"},
                files={"media": ("canary.mp4", media, "video/mp4")},
            )

    def start_controlled_analysis(self, source_id: str) -> httpx.Response:
        return self._client.post(
            "/api/v1/analysis-runs",
            json={"source_id": source_id},
        )

    def stream_events(self, run_id: str) -> tuple[int, bool]:
        terminal_observed = False
        with self._client.stream(
            "GET",
            f"/api/v1/analysis-runs/{run_id}/events",
            headers={"Accept": "text/event-stream", "Origin": self._origin},
        ) as response:
            if response.status_code != 200:
                return response.status_code, terminal_observed
            for line in response.iter_lines():
                if not line.startswith("event: "):
                    continue
                if line.removeprefix("event: ").strip() in {
                    "run.completed",
                    "run.failed",
                    "run.cancelled",
                }:
                    terminal_observed = True
                    break
        return 200, terminal_observed

    def wait_for_terminal(
        self, run_id: str, *, timeout_seconds: float
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            payload = self.get_json(
                f"/api/v1/analysis-runs/{run_id}",
                label="run status",
            )
            if payload.get("status") in TERMINAL_STATUSES:
                return payload
            time.sleep(1)
        raise CanaryError("analysis run did not reach a terminal state")

    def wait_for_runtime_cleanup(
        self, *, timeout_seconds: float
    ) -> dict[str, object]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            payload = self.get_json(
                "/api/v1/runtime-cleanup",
                label="runtime cleanup",
            )
            clean = payload.get("clean")
            residue_count = payload.get("residue_count")
            ffmpeg_process_count = payload.get("ffmpeg_process_count")
            if (
                type(clean) is not bool
                or type(residue_count) is not int
                or residue_count < 0
                or type(ffmpeg_process_count) is not int
                or ffmpeg_process_count < 0
                or clean != (residue_count == 0 and ffmpeg_process_count == 0)
            ):
                raise CanaryError("runtime cleanup returned invalid cleanup evidence")
            if clean:
                return {
                    "clean": True,
                    "residue_count": residue_count,
                    "ffmpeg_process_count": ffmpeg_process_count,
                }
            time.sleep(0.5)
        raise CanaryError("runtime cleanup was not proven before timeout")

    def cancel(self, run_id: str) -> None:
        self._client.delete(f"/api/v1/analysis-runs/{run_id}")


def summarize_parallel_results(results: list[WorkerResult]) -> dict[str, Any]:
    status_counts = Counter(str(result.upload_status) for result in results)
    if len(results) != PARALLEL_REQUESTS or status_counts != Counter(
        {"202": EXPECTED_ACCEPTED_RUNS, "429": 1}
    ):
        raise CanaryError(
            "public concurrency gate requires one accepted run and one 429"
        )

    accepted = [result for result in results if result.upload_status == 202]
    rejected = [result for result in results if result.upload_status == 429]
    if any(result.terminal_status != "completed" for result in accepted):
        raise CanaryError(
            "an accepted public canary run returned a non-completed terminal status"
        )
    if any(not result.terminal_event_observed for result in accepted):
        raise CanaryError(
            "an accepted public canary run did not emit a terminal SSE event"
        )
    for result in accepted:
        if result.coverage_status not in PASSING_COVERAGE_STATUSES:
            raise CanaryError(
                "an accepted public canary run failed the coverage contract"
            )
        if (
            result.session_cooldown_enforced is not True
            or type(result.session_retry_after_seconds) is not int
            or not MIN_COOLDOWN_SECONDS
            <= result.session_retry_after_seconds
            <= MAX_COOLDOWN_SECONDS
        ):
            raise CanaryError(
                "an accepted public canary run failed the session cooldown contract"
            )
        if result.coverage_status == "complete" and result.coverage_gap_count != 0:
            raise CanaryError(
                "an accepted public canary run failed the coverage contract"
            )
        if result.coverage_status == "partial" and (
            result.reliable_candidate_count < 1 or result.coverage_gap_count < 1
        ):
            raise CanaryError(
                "an accepted public canary run failed the coverage contract"
            )
    if any(not result.retry_after_present for result in rejected):
        raise CanaryError("the capacity rejection omitted Retry-After")
    if any(result.admission_reason != CAPACITY_ADMISSION_REASON for result in rejected):
        raise CanaryError("the concurrency rejection omitted the capacity reason")

    terminal_counts = Counter(
        result.terminal_status
        for result in accepted
        if result.terminal_status is not None
    )
    coverage_counts = Counter(
        result.coverage_status
        for result in accepted
        if result.coverage_status is not None
    )
    return {
        "parallel_requests": len(results),
        "upload_status_counts": dict(status_counts),
        "accepted_runs": len(accepted),
        "capacity_rejections": len(rejected),
        "terminal_event_count": sum(
            result.terminal_event_observed for result in accepted
        ),
        "terminal_status_counts": dict(terminal_counts),
        "coverage_status_counts": dict(coverage_counts),
        "reliable_candidate_count": sum(
            result.reliable_candidate_count for result in accepted
        ),
        "coverage_gap_count": sum(result.coverage_gap_count for result in accepted),
        "session_cooldown_enforced": all(
            result.session_cooldown_enforced for result in accepted
        ),
        "session_retry_after_seconds": accepted[0].session_retry_after_seconds,
        "retry_after_present_count": sum(
            result.retry_after_present for result in rejected
        ),
        "capacity_reason_count": sum(
            result.admission_reason == CAPACITY_ADMISSION_REASON for result in rejected
        ),
    }


def _run_worker(
    *,
    origin: str,
    barrier: threading.Barrier,
    timeout_seconds: float,
    media_path: Path | None = None,
    source_id: str | None = None,
) -> WorkerResult:
    if (media_path is None) == (source_id is None):
        raise CanaryError("public canary worker requires exactly one analysis input")
    client = PublicCanaryClient(origin=origin, timeout_seconds=timeout_seconds)
    run_id: str | None = None
    try:
        client.prepare_public_session()
        barrier.wait(timeout=30)
        response = (
            client.upload_video(media_path)
            if media_path is not None
            else client.start_controlled_analysis(cast(str, source_id))
        )
        if response.status_code == 429:
            return WorkerResult(
                upload_status=429,
                terminal_event_observed=False,
                terminal_status=None,
                coverage_status=None,
                retry_after_present="Retry-After" in response.headers,
                admission_reason=response.headers.get(ADMISSION_REASON_HEADER),
            )
        created = _expect_json(response, 202, "parallel public analysis start")
        run_id_value = created.get("id")
        if not isinstance(run_id_value, str) or not run_id_value:
            raise CanaryError("analysis upload did not return a run id")
        run_id = run_id_value
        sse_status, terminal_observed = client.stream_events(run_id)
        if sse_status != 200:
            raise CanaryError(f"public SSE failed with HTTP {sse_status}")
        terminal = client.wait_for_terminal(run_id, timeout_seconds=timeout_seconds)
        session_retry_after_seconds = client.require_analysis_cooldown(
            label="accepted session cooldown"
        )
        return summarize_terminal(
            terminal,
            terminal_event_observed=terminal_observed,
            session_retry_after_seconds=session_retry_after_seconds,
            require_local_295_seconds=media_path is not None,
        )
    except BaseException:
        if run_id is not None:
            with suppress(Exception):
                client.cancel(run_id)
        raise
    finally:
        client.close()


def probe_post_run_guards(
    *, origin: str, timeout_seconds: float
) -> tuple[dict[str, object], dict[str, object]]:
    client = PublicCanaryClient(origin=origin, timeout_seconds=timeout_seconds)
    try:
        runtime_cleanup = client.wait_for_runtime_cleanup(
            timeout_seconds=min(timeout_seconds, RUNTIME_CLEANUP_TIMEOUT_SECONDS)
        )
        retry_after_seconds = client.require_analysis_cooldown(
            label="fresh IP cooldown"
        )
    finally:
        client.close()
    return (
        runtime_cleanup,
        {
            "enforced": True,
            "retry_after_seconds": retry_after_seconds,
        },
    )


def run_canary(args: argparse.Namespace) -> dict[str, Any]:
    origin = normalize_public_origin(args.public_base_url)
    controlled_shortest = bool(getattr(args, "controlled_shortest", False))
    media_path: Path | None = None
    media_bytes: int | None = None
    media_duration_seconds: float | None = None
    source_id: str | None = None
    selected_duration: float | None = None
    if not controlled_shortest:
        media_value = getattr(args, "media", None)
        if not isinstance(media_value, str) or not media_value:
            raise CanaryError("local upload canary requires a media path")
        media_path = Path(media_value).expanduser().resolve()
        if not media_path.is_file():
            raise CanaryError("canary media is unavailable")
        media_bytes = media_path.stat().st_size
        if media_bytes <= 0 or media_bytes > PUBLIC_UPLOAD_MAX_BYTES:
            raise CanaryError(
                "public canary media is outside the CloudBase upload envelope"
            )
        media_duration_seconds = probe_local_canary_duration(media_path)

    probe = PublicCanaryClient(origin=origin, timeout_seconds=args.timeout_seconds)
    try:
        capabilities = probe.get_json("/api/v1/capabilities", label="capabilities")
        anonymous = probe.get_json("/api/v1/access/session", label="anonymous session")
        if controlled_shortest:
            source_id, selected_duration = select_shortest_controlled_source(
                probe.list_controlled_sources()
            )
    finally:
        probe.close()
    if capabilities.get("local_analysis_max_seconds") != 300:
        raise CanaryError(
            "deployed local analysis duration boundary is not 300 seconds"
        )
    if anonymous.get("tier") != "public" or not anonymous.get("can_analyze"):
        raise CanaryError("public analysis is not available")

    barrier = threading.Barrier(PARALLEL_REQUESTS)
    with ThreadPoolExecutor(max_workers=PARALLEL_REQUESTS) as executor:
        futures = [
            executor.submit(
                _run_worker,
                origin=origin,
                media_path=media_path,
                source_id=source_id,
                barrier=barrier,
                timeout_seconds=args.timeout_seconds,
            )
            for _ in range(PARALLEL_REQUESTS)
        ]
        results = [future.result() for future in futures]

    concurrency = summarize_parallel_results(results)
    runtime_cleanup, ip_cooldown = probe_post_run_guards(
        origin=origin,
        timeout_seconds=args.timeout_seconds,
    )

    input_receipt: dict[str, object]
    if controlled_shortest:
        input_receipt = {
            "kind": "controlled_source",
            "catalog_count": 5,
            "duration_seconds": selected_duration,
        }
    else:
        input_receipt = {
            "kind": "local_upload",
            "media_bytes": media_bytes,
            "duration_seconds": media_duration_seconds,
        }

    return {
        "schema_version": 1,
        "service": "trainpal-demo",
        "access": "public-http-route",
        "input": input_receipt,
        "capabilities": {
            "local_analysis_max_seconds": capabilities.get(
                "local_analysis_max_seconds"
            ),
            "local_upload_max_bytes": capabilities.get("local_upload_max_bytes"),
        },
        "access_gate": {"public_can_analyze": True, "public_sessions": 2},
        "concurrency": concurrency,
        "runtime_cleanup": runtime_cleanup,
        "ip_cooldown": ip_cooldown,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a content-redacted two-request CloudBase public canary."
    )
    parser.add_argument("--public-base-url", required=True)
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--media")
    input_group.add_argument("--controlled-shortest", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=240)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        receipt = run_canary(args)
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
    except CanaryError as error:
        print(f"public canary failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
