from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from runpy import run_path
from typing import Any, Callable, cast
from urllib.parse import urlsplit
from uuid import uuid4

import httpx


_PRIVATE_SUPPORT = run_path(str(Path(__file__).with_name("private-canary.py")))
CanaryError = cast(type[RuntimeError], _PRIVATE_SUPPORT["CanaryError"])
read_windows_credential = cast(
    Callable[[str], str],
    _PRIVATE_SUPPORT["read_windows_credential"],
)

PUBLIC_UPLOAD_MAX_BYTES = 19_000_000
PARALLEL_REQUESTS = 4
EXPECTED_ACCEPTED_RUNS = 3
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
VALID_COVERAGE_STATUSES = {"complete", "partial", "insufficient"}


@dataclass(frozen=True, slots=True)
class WorkerResult:
    upload_status: int
    terminal_event_observed: bool
    terminal_status: str | None
    coverage_status: str | None
    retry_after_present: bool


def normalize_public_origin(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise CanaryError("public canary origin must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise CanaryError("public canary origin must not contain credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise CanaryError("public canary origin must not contain a path, query, or fragment")
    try:
        port = parsed.port
    except ValueError as error:
        raise CanaryError("public canary origin contains an invalid port") from error
    if port not in {None, 443}:
        raise CanaryError("public canary origin must use the default HTTPS port")
    return f"https://{parsed.hostname.lower()}"


def _expect_json(response: httpx.Response, expected_status: int, label: str) -> dict[str, Any]:
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

    def prepare_judge_session(self, judge_code: str) -> None:
        public_session = self.get_json(
            "/api/v1/access/session",
            label="public session",
        )
        if public_session.get("tier") != "public" or public_session.get("can_analyze"):
            raise CanaryError("anonymous analysis is not fail-closed")
        judge_session = _expect_json(
            self._client.post(
                "/api/v1/access/session",
                json={"access_code": judge_code},
            ),
            200,
            "judge session",
        )
        if judge_session.get("tier") != "judge" or not judge_session.get("can_analyze"):
            raise CanaryError("judge session was not granted analysis capacity")

    def upload_video(self, media_path: Path) -> httpx.Response:
        with media_path.open("rb") as media:
            return self._client.post(
                "/api/v1/analysis-runs/local",
                data={"local_source_id": f"local:{uuid4()}"},
                files={"media": ("canary.mp4", media, "video/mp4")},
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

    def wait_for_terminal(self, run_id: str, *, timeout_seconds: float) -> dict[str, Any]:
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

    def cancel(self, run_id: str) -> None:
        self._client.delete(f"/api/v1/analysis-runs/{run_id}")


def summarize_parallel_results(results: list[WorkerResult]) -> dict[str, Any]:
    status_counts = Counter(str(result.upload_status) for result in results)
    if (
        len(results) != PARALLEL_REQUESTS
        or status_counts != Counter({"202": EXPECTED_ACCEPTED_RUNS, "429": 1})
    ):
        raise CanaryError("public concurrency gate requires three accepted runs and one 429")

    accepted = [result for result in results if result.upload_status == 202]
    rejected = [result for result in results if result.upload_status == 429]
    if any(result.terminal_status != "completed" for result in accepted):
        raise CanaryError("an accepted public canary run returned a non-completed terminal status")
    if any(not result.terminal_event_observed for result in accepted):
        raise CanaryError("an accepted public canary run did not emit a terminal SSE event")
    if any(result.coverage_status not in VALID_COVERAGE_STATUSES for result in accepted):
        raise CanaryError("an accepted public canary run returned invalid coverage status")
    if any(not result.retry_after_present for result in rejected):
        raise CanaryError("the capacity rejection omitted Retry-After")

    terminal_counts = Counter(
        result.terminal_status for result in accepted if result.terminal_status is not None
    )
    coverage_counts = Counter(
        result.coverage_status for result in accepted if result.coverage_status is not None
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
        "retry_after_present_count": sum(
            result.retry_after_present for result in rejected
        ),
    }


def _run_worker(
    *,
    origin: str,
    media_path: Path,
    judge_code: str,
    barrier: threading.Barrier,
    timeout_seconds: float,
) -> WorkerResult:
    client = PublicCanaryClient(origin=origin, timeout_seconds=timeout_seconds)
    run_id: str | None = None
    try:
        client.prepare_judge_session(judge_code)
        barrier.wait(timeout=30)
        response = client.upload_video(media_path)
        if response.status_code == 429:
            return WorkerResult(
                upload_status=429,
                terminal_event_observed=False,
                terminal_status=None,
                coverage_status=None,
                retry_after_present="Retry-After" in response.headers,
            )
        created = _expect_json(response, 202, "parallel local analysis upload")
        run_id_value = created.get("id")
        if not isinstance(run_id_value, str) or not run_id_value:
            raise CanaryError("analysis upload did not return a run id")
        run_id = run_id_value
        sse_status, terminal_observed = client.stream_events(run_id)
        if sse_status != 200:
            raise CanaryError(f"public SSE failed with HTTP {sse_status}")
        terminal = client.wait_for_terminal(run_id, timeout_seconds=timeout_seconds)
        return WorkerResult(
            upload_status=202,
            terminal_event_observed=terminal_observed,
            terminal_status=(
                str(terminal.get("status")) if terminal.get("status") is not None else None
            ),
            coverage_status=(
                str(terminal.get("coverage_status"))
                if terminal.get("coverage_status") is not None
                else None
            ),
            retry_after_present=False,
        )
    except BaseException:
        if run_id is not None:
            try:
                client.cancel(run_id)
            except Exception:
                pass
        raise
    finally:
        client.close()


def run_canary(args: argparse.Namespace) -> dict[str, Any]:
    origin = normalize_public_origin(args.public_base_url)
    media_path = Path(args.media).expanduser().resolve()
    if not media_path.is_file():
        raise CanaryError("canary media is unavailable")
    media_bytes = media_path.stat().st_size
    if media_bytes <= 0 or media_bytes > PUBLIC_UPLOAD_MAX_BYTES:
        raise CanaryError("public canary media is outside the CloudBase upload envelope")

    judge_code = read_windows_credential(args.judge_credential_target)
    probe = PublicCanaryClient(origin=origin, timeout_seconds=args.timeout_seconds)
    try:
        capabilities = probe.get_json("/api/v1/capabilities", label="capabilities")
        anonymous = probe.get_json("/api/v1/access/session", label="anonymous session")
    finally:
        probe.close()
    if capabilities.get("local_analysis_max_seconds") != 300:
        raise CanaryError("deployed local analysis duration boundary is not 300 seconds")
    if anonymous.get("tier") != "public" or anonymous.get("can_analyze"):
        raise CanaryError("anonymous analysis is not fail-closed")

    barrier = threading.Barrier(PARALLEL_REQUESTS)
    with ThreadPoolExecutor(max_workers=PARALLEL_REQUESTS) as executor:
        futures = [
            executor.submit(
                _run_worker,
                origin=origin,
                media_path=media_path,
                judge_code=judge_code,
                barrier=barrier,
                timeout_seconds=args.timeout_seconds,
            )
            for _ in range(PARALLEL_REQUESTS)
        ]
        results = [future.result() for future in futures]

    return {
        "schema_version": 1,
        "service": "trainpal-demo",
        "access": "public-http-route",
        "media_bytes": media_bytes,
        "capabilities": {
            "local_analysis_max_seconds": capabilities.get(
                "local_analysis_max_seconds"
            ),
            "local_upload_max_bytes": capabilities.get("local_upload_max_bytes"),
        },
        "access_gate": {"anonymous_can_analyze": False, "judge_sessions": 4},
        "concurrency": summarize_parallel_results(results),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a content-redacted four-request CloudBase public canary."
    )
    parser.add_argument("--public-base-url", required=True)
    parser.add_argument("--media", required=True)
    parser.add_argument(
        "--judge-credential-target",
        default="HakimiFitness.CloudBase.JudgeCode",
    )
    parser.add_argument("--timeout-seconds", type=float, default=180)
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
