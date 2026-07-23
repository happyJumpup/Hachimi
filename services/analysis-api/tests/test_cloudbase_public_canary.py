import argparse
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from runpy import run_path
from typing import Any

import imageio_ffmpeg
import pytest


def load_public_canary() -> dict[str, Any]:
    return run_path(str(Path(__file__).parents[3] / "deploy" / "cloudbase" / "public-canary.py"))


def write_synthetic_video(path: Path, *, duration_seconds: float) -> None:
    generated = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=16x16:r=1",
            "-t",
            str(duration_seconds),
            "-c:v",
            "mpeg4",
            "-pix_fmt",
            "yuv420p",
            "-an",
            "-y",
            str(path),
        ],
        capture_output=True,
        check=False,
    )
    assert generated.returncode == 0, generated.stderr.decode(
        "utf-8", errors="replace"
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


def test_public_canary_selects_the_shortest_of_exactly_five_controlled_sources() -> None:
    namespace = load_public_canary()
    select_shortest = namespace["select_shortest_controlled_source"]
    canary_error = namespace["CanaryError"]
    sources = [
        {
            "id": f"neutral-{index}",
            "title": f"private-title-{index}",
            "duration_seconds": duration,
        }
        for index, duration in enumerate((108.72, 54.87, 207.77, 67.83, 100.43))
    ]

    assert select_shortest(sources) == ("neutral-1", 54.87)
    with pytest.raises(canary_error, match="exactly five"):
        select_shortest(sources[:4])
    with pytest.raises(canary_error, match="invalid source catalog"):
        select_shortest([*sources[:4], {"id": "bad", "duration_seconds": True}])


def test_public_canary_requires_one_public_accept_and_one_capacity_rejection() -> None:
    namespace = load_public_canary()
    worker_result = namespace["WorkerResult"]
    summarize = namespace["summarize_parallel_results"]

    results = [
        worker_result(
            upload_status=202,
            terminal_event_observed=True,
            terminal_status="completed",
            coverage_status="partial",
            retry_after_present=False,
            reliable_candidate_count=1,
            coverage_gap_count=1,
            same_session_restart_accepted=True,
            same_session_restart_cancelled=True,
            same_session_cleanup_proven=True,
        ),
        worker_result(
            upload_status=429,
            terminal_event_observed=False,
            terminal_status=None,
            coverage_status=None,
            retry_after_present=True,
            admission_reason="capacity",
        ),
    ]

    assert summarize(results) == {
        "parallel_requests": 2,
        "upload_status_counts": {"202": 1, "429": 1},
        "accepted_runs": 1,
        "capacity_rejections": 1,
        "terminal_event_count": 1,
        "terminal_status_counts": {"completed": 1},
        "coverage_status_counts": {"partial": 1},
        "reliable_candidate_count": 1,
        "coverage_gap_count": 1,
        "same_session_restart_accepted": True,
        "same_session_restart_cancelled": True,
        "same_session_cleanup_proven": True,
        "retry_after_present_count": 1,
        "capacity_reason_count": 1,
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
        reliable_candidate_count=0,
        coverage_gap_count=0,
        same_session_restart_accepted=True,
        same_session_restart_cancelled=True,
        same_session_cleanup_proven=True,
    )

    with pytest.raises(canary_error, match="one accepted"):
        summarize([accepted, accepted])

    failed = worker_result(
        upload_status=202,
        terminal_event_observed=True,
        terminal_status="failed",
        coverage_status="insufficient",
        retry_after_present=False,
        reliable_candidate_count=0,
        coverage_gap_count=1,
        same_session_restart_accepted=True,
        same_session_restart_cancelled=True,
        same_session_cleanup_proven=True,
    )
    rejected = worker_result(
        upload_status=429,
        terminal_event_observed=False,
        terminal_status=None,
        coverage_status=None,
        retry_after_present=True,
        admission_reason="capacity",
    )
    with pytest.raises(canary_error, match="terminal status"):
        summarize([failed, rejected])

    missing_retry_after = worker_result(
        upload_status=429,
        terminal_event_observed=False,
        terminal_status=None,
        coverage_status=None,
        retry_after_present=False,
        admission_reason="capacity",
    )
    with pytest.raises(canary_error, match="Retry-After"):
        summarize([accepted, missing_retry_after])

    wrong_reason_rejection = worker_result(
        upload_status=429,
        terminal_event_observed=False,
        terminal_status=None,
        coverage_status=None,
        retry_after_present=True,
        admission_reason="rate_limit",
    )
    with pytest.raises(canary_error, match="capacity reason"):
        summarize([accepted, wrong_reason_rejection])


@pytest.mark.parametrize(
    ("coverage_status", "reliable_candidate_count", "coverage_gap_count"),
    [
        ("insufficient", 0, 1),
        ("partial", 0, 1),
        ("partial", 1, 0),
        ("complete", 1, 1),
    ],
)
def test_public_canary_rejects_non_promotable_completed_results(
    coverage_status: str,
    reliable_candidate_count: int,
    coverage_gap_count: int,
) -> None:
    namespace = load_public_canary()
    worker_result = namespace["WorkerResult"]
    summarize = namespace["summarize_parallel_results"]
    canary_error = namespace["CanaryError"]
    accepted = worker_result(
        upload_status=202,
        terminal_event_observed=True,
        terminal_status="completed",
        coverage_status=coverage_status,
        retry_after_present=False,
        reliable_candidate_count=reliable_candidate_count,
        coverage_gap_count=coverage_gap_count,
        same_session_restart_accepted=True,
        same_session_restart_cancelled=True,
        same_session_cleanup_proven=True,
    )
    rejected = worker_result(
        upload_status=429,
        terminal_event_observed=False,
        terminal_status=None,
        coverage_status=None,
        retry_after_present=True,
        admission_reason="capacity",
    )

    with pytest.raises(canary_error, match="coverage contract"):
        summarize([accepted, rejected])


def test_public_canary_redacts_terminal_content_from_the_receipt() -> None:
    namespace = load_public_canary()
    summarize_terminal = namespace["summarize_terminal"]
    summarize = namespace["summarize_parallel_results"]
    worker_result = namespace["WorkerResult"]

    accepted = summarize_terminal(
        {
            "status": "completed",
            "coverage_status": "partial",
            "candidates": [
                {
                    "name": "private source action name",
                    "source_id": "private-source-name",
                    "needs_confirmation": False,
                }
            ],
            "coverage_gaps": [{"reason": "provider_error", "transcript": "private transcript"}],
        },
        terminal_event_observed=True,
        same_session_restart_accepted=True,
        same_session_restart_cancelled=True,
        same_session_cleanup_proven=True,
    )
    rejected = worker_result(
        upload_status=429,
        terminal_event_observed=False,
        terminal_status=None,
        coverage_status=None,
        retry_after_present=True,
        admission_reason="capacity",
    )

    encoded = json.dumps(summarize([accepted, rejected]))
    assert "private source action name" not in encoded
    assert "private-source-name" not in encoded
    assert "private transcript" not in encoded
    assert '"reliable_candidate_count": 1' in encoded
    assert '"coverage_gap_count": 1' in encoded
    assert '"same_session_restart_accepted": true' in encoded
    assert '"same_session_restart_cancelled": true' in encoded
    assert '"same_session_cleanup_proven": true' in encoded
    assert "cookie" not in encoded.lower()
    assert "ip" not in encoded.lower()


@pytest.mark.parametrize(
    "source_duration_seconds",
    [None, True, 294.49, 295.51],
)
def test_local_public_canary_rejects_terminal_duration_outside_295_second_window(
    source_duration_seconds: object,
) -> None:
    namespace = load_public_canary()
    summarize_terminal = namespace["summarize_terminal"]
    canary_error = namespace["CanaryError"]
    payload: dict[str, object] = {
        "status": "completed",
        "coverage_status": "complete",
        "candidates": [],
        "coverage_gaps": [],
    }
    if source_duration_seconds is not None:
        payload["source_duration_seconds"] = source_duration_seconds

    with pytest.raises(canary_error, match="295"):
        summarize_terminal(
            payload,
            terminal_event_observed=True,
            require_local_295_seconds=True,
        )


@pytest.mark.parametrize("source_duration_seconds", [294.5, 295, 295.5])
def test_local_public_canary_accepts_terminal_duration_within_295_second_window(
    source_duration_seconds: float,
) -> None:
    namespace = load_public_canary()
    summarize_terminal = namespace["summarize_terminal"]

    result = summarize_terminal(
        {
            "status": "completed",
            "coverage_status": "complete",
            "candidates": [],
            "coverage_gaps": [],
            "source_duration_seconds": source_duration_seconds,
        },
        terminal_event_observed=True,
        require_local_295_seconds=True,
    )

    assert result.terminal_status == "completed"


@pytest.mark.parametrize(
    "missing_field",
    [
        "same_session_restart_accepted",
        "same_session_restart_cancelled",
        "same_session_cleanup_proven",
    ],
)
def test_public_canary_rejects_missing_same_session_restart_evidence(
    missing_field: str,
) -> None:
    namespace = load_public_canary()
    worker_result = namespace["WorkerResult"]
    summarize = namespace["summarize_parallel_results"]
    canary_error = namespace["CanaryError"]
    restart_evidence = {
        "same_session_restart_accepted": True,
        "same_session_restart_cancelled": True,
        "same_session_cleanup_proven": True,
    }
    restart_evidence[missing_field] = False
    accepted_without_restart = worker_result(
        upload_status=202,
        terminal_event_observed=True,
        terminal_status="completed",
        coverage_status="complete",
        retry_after_present=False,
        **restart_evidence,
    )
    rejected = worker_result(
        upload_status=429,
        terminal_event_observed=False,
        terminal_status=None,
        coverage_status=None,
        retry_after_present=True,
        admission_reason="capacity",
    )

    with pytest.raises(canary_error, match="same-session restart"):
        summarize([accepted_without_restart, rejected])


def test_accepted_worker_reuses_the_same_session_after_terminal_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    namespace = load_public_canary()
    run_worker = namespace["_run_worker"]
    calls: list[str] = []

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            self.upload_count = 0
            self.cleanup_count = 0

        def prepare_public_session(self) -> None:
            calls.append("prepare")

        def upload_video(self, _media_path: Path) -> object:
            self.upload_count += 1
            calls.append(f"upload-{self.upload_count}")
            return namespace["httpx"].Response(
                202,
                json={"id": f"run-{self.upload_count}"},
            )

        def stream_events(self, _run_id: str) -> tuple[int, bool]:
            calls.append("events")
            return 200, True

        def wait_for_terminal(self, _run_id: str, *, timeout_seconds: float) -> dict[str, object]:
            calls.append(f"terminal-{_run_id}")
            if _run_id == "run-2":
                return {
                    "status": "cancelled",
                    "coverage_status": None,
                    "candidates": [],
                    "coverage_gaps": [],
                }
            return {
                "status": "completed",
                "coverage_status": "complete",
                "candidates": [],
                "coverage_gaps": [],
                "source_duration_seconds": 295,
            }

        def wait_for_runtime_cleanup(self, *, timeout_seconds: float) -> dict[str, object]:
            assert timeout_seconds == 30
            self.cleanup_count += 1
            calls.append(f"cleanup-{self.cleanup_count}")
            return {
                "clean": True,
                "residue_count": 0,
                "ffmpeg_process_count": 0,
            }

        def cancel(self, run_id: str) -> None:
            assert run_id == "run-2"
            calls.append("cancel-run-2")

        def close(self) -> None:
            calls.append("close")

    class Barrier:
        def wait(self, *, timeout: int) -> None:
            assert timeout == 30

    monkeypatch.setitem(run_worker.__globals__, "PublicCanaryClient", FakeClient)
    result = run_worker(
        origin="https://demo.example.com",
        media_path=tmp_path / "canary.mp4",
        barrier=Barrier(),
        timeout_seconds=180,
    )

    assert result.same_session_restart_accepted is True
    assert result.same_session_restart_cancelled is True
    assert result.same_session_cleanup_proven is True
    assert calls == [
        "prepare",
        "upload-1",
        "events",
        "terminal-run-1",
        "cleanup-1",
        "upload-2",
        "cancel-run-2",
        "terminal-run-2",
        "cleanup-2",
        "close",
    ]


def test_worker_exception_cancels_and_rechecks_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    namespace = load_public_canary()
    run_worker = namespace["_run_worker"]
    canary_error = namespace["CanaryError"]
    calls: list[str] = []

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def prepare_public_session(self) -> None:
            calls.append("prepare")

        def upload_video(self, _media_path: Path) -> object:
            calls.append("upload")
            return namespace["httpx"].Response(202, json={"id": "run-1"})

        def stream_events(self, _run_id: str) -> tuple[int, bool]:
            calls.append("events")
            raise canary_error("stream failed")

        def cancel(self, run_id: str) -> None:
            assert run_id == "run-1"
            calls.append("cancel")

        def wait_for_terminal(
            self, run_id: str, *, timeout_seconds: float
        ) -> dict[str, object]:
            assert run_id == "run-1"
            assert timeout_seconds == 30
            calls.append("cancelled")
            return {"status": "cancelled"}

        def wait_for_runtime_cleanup(
            self, *, timeout_seconds: float
        ) -> dict[str, object]:
            assert timeout_seconds == 30
            calls.append("cleanup")
            return {
                "clean": True,
                "residue_count": 0,
                "ffmpeg_process_count": 0,
            }

        def close(self) -> None:
            calls.append("close")

    class Barrier:
        def wait(self, *, timeout: int) -> None:
            assert timeout == 30

    monkeypatch.setitem(run_worker.__globals__, "PublicCanaryClient", FakeClient)

    with pytest.raises(canary_error, match="stream failed"):
        run_worker(
            origin="https://demo.example.com",
            media_path=tmp_path / "canary.mp4",
            barrier=Barrier(),
            timeout_seconds=180,
        )

    assert calls == [
        "prepare",
        "upload",
        "events",
        "cancel",
        "cancelled",
        "cleanup",
        "close",
    ]


def test_rejected_worker_requires_the_capacity_admission_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = load_public_canary()
    run_worker = namespace["_run_worker"]

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def prepare_public_session(self) -> None:
            pass

        def start_controlled_analysis(self, _source_id: str) -> object:
            return namespace["httpx"].Response(
                429,
                headers={
                    "Retry-After": "1",
                    "X-TrainPal-Admission-Reason": "rate_limit",
                },
            )

        def close(self) -> None:
            pass

    class Barrier:
        def wait(self, *, timeout: int) -> None:
            assert timeout == 30

    monkeypatch.setitem(run_worker.__globals__, "PublicCanaryClient", FakeClient)

    result = run_worker(
        origin="https://demo.example.com",
        source_id="controlled-source",
        barrier=Barrier(),
        timeout_seconds=180,
    )

    assert result.retry_after_present is True
    assert result.admission_reason == "rate_limit"


def test_public_canary_polls_until_runtime_cleanup_is_proven(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = load_public_canary()
    client_type = namespace["PublicCanaryClient"]
    client = object.__new__(client_type)
    payload_values: list[dict[str, object]] = [
        {"clean": False, "residue_count": 2, "ffmpeg_process_count": 1},
        {"clean": True, "residue_count": 0, "ffmpeg_process_count": 0},
    ]
    payloads: Iterator[dict[str, object]] = iter(payload_values)
    paths: list[str] = []

    def get_json(path: str, *, label: str) -> dict[str, object]:
        assert label == "runtime cleanup"
        paths.append(path)
        return next(payloads)

    client.get_json = get_json
    ticks = iter((0.0, 0.1, 0.2))
    monkeypatch.setattr(namespace["time"], "monotonic", lambda: next(ticks))
    monkeypatch.setattr(namespace["time"], "sleep", lambda _seconds: None)

    assert client.wait_for_runtime_cleanup(timeout_seconds=1) == {
        "clean": True,
        "residue_count": 0,
        "ffmpeg_process_count": 0,
    }
    assert paths == [
        "/api/v1/runtime-cleanup",
        "/api/v1/runtime-cleanup",
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"clean": True, "residue_count": 1, "ffmpeg_process_count": 0},
        {"clean": True, "residue_count": 0, "ffmpeg_process_count": True},
        {"clean": "yes", "residue_count": 0, "ffmpeg_process_count": 0},
    ],
)
def test_public_canary_rejects_invalid_runtime_cleanup_evidence(
    payload: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    namespace = load_public_canary()
    client_type = namespace["PublicCanaryClient"]
    canary_error = namespace["CanaryError"]
    client = object.__new__(client_type)
    client.get_json = lambda *_args, **_kwargs: payload
    ticks = iter((0.0, 0.1))
    monkeypatch.setattr(namespace["time"], "monotonic", lambda: next(ticks))

    with pytest.raises(canary_error, match="cleanup evidence"):
        client.wait_for_runtime_cleanup(timeout_seconds=1)


def test_run_canary_reports_cleanup_without_an_ip_cooldown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    namespace = load_public_canary()
    run_canary = namespace["run_canary"]
    worker_result = namespace["WorkerResult"]

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            self.closed = False
            instances.append(self)

        def get_json(self, path: str, *, label: str) -> dict[str, object]:
            if path == "/api/v1/capabilities":
                return {
                    "local_analysis_max_seconds": 300,
                    "local_upload_max_bytes": 19_000_000,
                }
            assert path == "/api/v1/access/session"
            assert label == "anonymous session"
            return {"tier": "public", "can_analyze": True}

        def wait_for_runtime_cleanup(self, *, timeout_seconds: float) -> dict[str, object]:
            assert timeout_seconds == 30
            return {
                "clean": True,
                "residue_count": 0,
                "ffmpeg_process_count": 0,
            }

        def close(self) -> None:
            self.closed = True

    instances: list[FakeClient] = []

    accepted = worker_result(
        upload_status=202,
        terminal_event_observed=True,
        terminal_status="completed",
        coverage_status="complete",
        retry_after_present=False,
        same_session_restart_accepted=True,
        same_session_restart_cancelled=True,
        same_session_cleanup_proven=True,
    )
    rejected = worker_result(
        upload_status=429,
        terminal_event_observed=False,
        terminal_status=None,
        coverage_status=None,
        retry_after_present=True,
        admission_reason="capacity",
    )
    worker_results = iter((accepted, rejected))

    monkeypatch.setitem(run_canary.__globals__, "PublicCanaryClient", FakeClient)
    monkeypatch.setitem(
        run_canary.__globals__, "_run_worker", lambda **_kwargs: next(worker_results)
    )
    media = tmp_path / "canary.mp4"
    write_synthetic_video(media, duration_seconds=295)

    receipt = run_canary(
        argparse.Namespace(
            public_base_url="https://demo.example.com",
            media=str(media),
            timeout_seconds=180,
        )
    )

    assert len(instances) == 2
    assert all(instance.closed for instance in instances)
    assert "ip_cooldown" not in receipt
    assert receipt["runtime_cleanup"] == {
        "clean": True,
        "residue_count": 0,
        "ffmpeg_process_count": 0,
    }
    assert receipt["input"] == {
        "kind": "local_upload",
        "media_bytes": media.stat().st_size,
        "duration_seconds": pytest.approx(295, abs=0.5),
    }
    encoded = json.dumps(receipt)
    assert "cookie" not in encoded.lower()
    assert "address" not in encoded.lower()


def test_run_canary_rejects_a_short_local_video_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    namespace = load_public_canary()
    run_canary = namespace["run_canary"]
    canary_error = namespace["CanaryError"]
    media = tmp_path / "short.mp4"
    write_synthetic_video(media, duration_seconds=1)

    class UnexpectedClient:
        def __init__(self, **_kwargs: object) -> None:
            raise AssertionError("short local canary must fail before any network client")

    monkeypatch.setitem(run_canary.__globals__, "PublicCanaryClient", UnexpectedClient)

    with pytest.raises(canary_error, match="295"):
        run_canary(
            argparse.Namespace(
                public_base_url="https://demo.example.com",
                media=str(media),
                timeout_seconds=180,
            )
        )


def test_controlled_source_canary_receipt_redacts_source_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = load_public_canary()
    run_canary = namespace["run_canary"]
    worker_result = namespace["WorkerResult"]
    private_id = "private-controlled-id"
    private_title = "private controlled title"

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def get_json(self, path: str, *, label: str) -> object:
            if path == "/api/v1/capabilities":
                return {
                    "local_analysis_max_seconds": 300,
                    "local_upload_max_bytes": 19_000_000,
                }
            assert path == "/api/v1/access/session"
            return {"tier": "public", "can_analyze": True}

        def list_controlled_sources(self) -> object:
            return [
                {
                    "id": private_id if index == 0 else f"neutral-{index}",
                    "title": private_title if index == 0 else "neutral",
                    "duration_seconds": float(55 + index),
                }
                for index in range(5)
            ]

        def wait_for_runtime_cleanup(self, *, timeout_seconds: float) -> dict[str, object]:
            assert timeout_seconds == 30
            return {
                "clean": True,
                "residue_count": 0,
                "ffmpeg_process_count": 0,
            }

        def close(self) -> None:
            pass

    accepted = worker_result(
        upload_status=202,
        terminal_event_observed=True,
        terminal_status="completed",
        coverage_status="complete",
        retry_after_present=False,
        same_session_restart_accepted=True,
        same_session_restart_cancelled=True,
        same_session_cleanup_proven=True,
    )
    rejected = worker_result(
        upload_status=429,
        terminal_event_observed=False,
        terminal_status=None,
        coverage_status=None,
        retry_after_present=True,
        admission_reason="capacity",
    )
    worker_results = iter((accepted, rejected))
    selected_source_ids: list[str | None] = []

    def fake_worker(**kwargs: object) -> object:
        selected_source_id = kwargs.get("source_id")
        assert selected_source_id is None or isinstance(selected_source_id, str)
        selected_source_ids.append(selected_source_id)
        return next(worker_results)

    monkeypatch.setitem(run_canary.__globals__, "PublicCanaryClient", FakeClient)
    monkeypatch.setitem(run_canary.__globals__, "_run_worker", fake_worker)

    receipt = run_canary(
        argparse.Namespace(
            public_base_url="https://demo.example.com",
            controlled_shortest=True,
            media=None,
            timeout_seconds=180,
        )
    )

    assert selected_source_ids == [private_id, private_id]
    assert receipt["input"] == {
        "kind": "controlled_source",
        "catalog_count": 5,
        "duration_seconds": 55.0,
    }
    encoded = json.dumps(receipt)
    assert private_id not in encoded
    assert private_title not in encoded


def test_public_canary_timeout_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    namespace = load_public_canary()
    client_type = namespace["PublicCanaryClient"]
    canary_error = namespace["CanaryError"]
    client = object.__new__(client_type)
    ticks = iter((0.0, 2.0))
    monkeypatch.setattr(namespace["time"], "monotonic", lambda: next(ticks))

    with pytest.raises(canary_error, match="did not reach a terminal state"):
        client.wait_for_terminal("run-timeout", timeout_seconds=1)


def test_public_canary_has_no_judge_credential_dependency() -> None:
    namespace = load_public_canary()
    source = (Path(__file__).parents[3] / "deploy" / "cloudbase" / "public-canary.py").read_text(
        encoding="utf-8"
    )

    assert namespace["PARALLEL_REQUESTS"] == 2
    assert namespace["EXPECTED_ACCEPTED_RUNS"] == 1
    assert "judge_credential_target" not in source
    assert "prepare_judge_session" not in source
