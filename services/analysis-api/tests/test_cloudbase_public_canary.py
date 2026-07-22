from pathlib import Path
from runpy import run_path
from typing import Any

import pytest


def load_public_canary() -> dict[str, Any]:
    return run_path(
        str(Path(__file__).parents[3] / "deploy" / "cloudbase" / "public-canary.py")
    )


def test_public_canary_normalizes_only_https_origins() -> None:
    namespace = load_public_canary()
    normalize_public_origin = namespace["normalize_public_origin"]
    canary_error = namespace["CanaryError"]

    assert normalize_public_origin("https://demo.example.com/") == "https://demo.example.com"
    with pytest.raises(canary_error):
        normalize_public_origin("http://demo.example.com")
    with pytest.raises(canary_error):
        normalize_public_origin("https://demo.example.com/path")
    with pytest.raises(canary_error):
        normalize_public_origin("https://user:secret@demo.example.com")


def test_public_canary_requires_three_accepts_and_one_capacity_rejection() -> None:
    namespace = load_public_canary()
    worker_result = namespace["WorkerResult"]
    summarize = namespace["summarize_parallel_results"]

    results = [
        worker_result(
            upload_status=202,
            terminal_event_observed=True,
            terminal_status="completed",
            coverage_status=coverage,
            retry_after_present=False,
        )
        for coverage in ("complete", "partial", "insufficient")
    ]
    results.append(
        worker_result(
            upload_status=429,
            terminal_event_observed=False,
            terminal_status=None,
            coverage_status=None,
            retry_after_present=True,
        )
    )

    assert summarize(results) == {
        "parallel_requests": 4,
        "upload_status_counts": {"202": 3, "429": 1},
        "accepted_runs": 3,
        "capacity_rejections": 1,
        "terminal_event_count": 3,
        "terminal_status_counts": {"completed": 3},
        "coverage_status_counts": {
            "complete": 1,
            "partial": 1,
            "insufficient": 1,
        },
        "retry_after_present_count": 1,
    }


def test_public_canary_rejects_a_false_concurrency_pass() -> None:
    namespace = load_public_canary()
    worker_result = namespace["WorkerResult"]
    summarize = namespace["summarize_parallel_results"]
    canary_error = namespace["CanaryError"]
    accepted = worker_result(
        upload_status=202,
        terminal_event_observed=True,
        terminal_status="completed",
        coverage_status="complete",
        retry_after_present=False,
    )

    with pytest.raises(canary_error, match="three accepted"):
        summarize([accepted, accepted, accepted, accepted])

    failed = worker_result(
        upload_status=202,
        terminal_event_observed=True,
        terminal_status="failed",
        coverage_status="insufficient",
        retry_after_present=False,
    )
    rejected = worker_result(
        upload_status=429,
        terminal_event_observed=False,
        terminal_status=None,
        coverage_status=None,
        retry_after_present=True,
    )
    with pytest.raises(canary_error, match="terminal status"):
        summarize([accepted, accepted, failed, rejected])
