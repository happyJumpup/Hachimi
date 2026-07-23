import argparse
import json
import threading
from pathlib import Path
from runpy import run_path
from typing import Any

import httpx
import pytest

from hakimi_analysis.gymti import GymtiService, load_gymti_contract


def load_public_gymti_canary() -> dict[str, Any]:
    return run_path(
        str(Path(__file__).parents[3] / "deploy" / "cloudbase" / "public-gymti-canary.py")
    )


def test_public_gymti_canary_accepts_only_a_strict_https_origin() -> None:
    namespace = load_public_gymti_canary()
    normalize_public_origin = namespace["normalize_public_origin"]
    canary_error = namespace["CanaryError"]

    assert normalize_public_origin("https://demo.example.com") == "https://demo.example.com"
    for invalid in (
        "http://demo.example.com",
        "https://demo.example.com/",
        "https://demo.example.com/path",
        "https://user:secret@demo.example.com",
        "https://demo.example.com?query=yes",
        "https://demo.example.com#fragment",
        "https://demo.example.com:8443",
        " https://demo.example.com",
        "https://demo.example.com ",
    ):
        with pytest.raises(canary_error):
            normalize_public_origin(invalid)


def test_public_gymti_canary_builds_valid_stable_id_payloads_from_the_contract() -> None:
    namespace = load_public_gymti_canary()
    payloads = namespace["build_probe_payloads"]()
    contract_path = Path(__file__).parents[3] / "contracts" / "gymti-questionnaire.v1.json"
    contract = load_gymti_contract(contract_path)
    service = GymtiService(contract=contract, model=None, model_name=None)

    next_payload = payloads.next_question
    service.validate_next_question_context(
        questionnaire_version=next_payload["questionnaire_version"],
        scoring_version=next_payload["scoring_version"],
        answered_question_option_ids=tuple(
            (answer["question_id"], answer["option_id"])
            for answer in next_payload["answered"]
        ),
        candidate_question_ids=next_payload["candidate_question_ids"],
    )
    narrative_payload = payloads.narrative
    service.validate_result_narrative_context(
        questionnaire_version=narrative_payload["questionnaire_version"],
        scoring_version=narrative_payload["scoring_version"],
        answered_question_option_ids=tuple(
            (answer["question_id"], answer["option_id"])
            for answer in narrative_payload["answered"]
        ),
        formal_result_id=narrative_payload["formal_result_id"],
        secondary_result_id=narrative_payload["secondary_result_id"],
        coach_style_id=narrative_payload["coach_style_id"],
        reason_codes=narrative_payload["reason_codes"],
    )

    encoded = json.dumps(
        {"next": next_payload, "narrative": narrative_payload}, ensure_ascii=False
    )
    raw_contract = json.loads(contract_path.read_text(encoding="utf-8"))
    display_text = [item["label"] for item in raw_contract["gymtiTypes"]]
    display_text.extend(
        question["prompt"]
        for question in [*raw_contract["questions"], *raw_contract["terminalBank"]]
    )
    assert all(text not in encoded for text in display_text)


def test_public_gymti_canary_fails_closed_when_the_local_contract_is_invalid(
    tmp_path: Path,
) -> None:
    namespace = load_public_gymti_canary()
    build_probe_payloads = namespace["build_probe_payloads"]
    canary_error = namespace["CanaryError"]
    invalid_contract = tmp_path / "contract.json"
    invalid_contract.write_text("PRIVATE invalid contract", encoding="utf-8")

    with pytest.raises(canary_error, match="local GYMTI contract") as captured:
        build_probe_payloads(invalid_contract)
    assert "PRIVATE" not in str(captured.value)


def test_public_gymti_canary_proves_llm_narrative_and_three_plus_one_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    namespace = load_public_gymti_canary()
    run_canary = namespace["run_canary"]
    lock = threading.Lock()
    parallel_call_count = 0
    calls: list[str] = []
    instances: list[Any] = []

    class FakeClient:
        def __init__(self, *, origin: str, timeout_seconds: float) -> None:
            assert origin == "https://demo.example.com"
            assert timeout_seconds == 30
            self.closed = False
            instances.append(self)

        def post_json(
            self, path: str, payload: dict[str, Any], *, label: str
        ) -> dict[str, object]:
            nonlocal parallel_call_count
            with lock:
                calls.append(label)
                if label == "initial next question":
                    return {
                        "question_id": payload["candidate_question_ids"][0],
                        "source": "llm",
                        "model": "ark-model-v1",
                        "version": payload["questionnaire_version"],
                    }
                if label == "result narrative":
                    return {
                        "source": "llm",
                        "model": "ark-model-v1",
                        "version": payload["questionnaire_version"],
                        "generated_at": "2026-07-23T00:00:00Z",
                        "text": "PRIVATE result narrative body",
                    }
                assert label == "parallel next question"
                parallel_call_count += 1
                source = "local_fallback" if parallel_call_count == 4 else "llm"
                return {
                    "question_id": payload["candidate_question_ids"][0],
                    "source": source,
                    "model": None if source == "local_fallback" else "ark-model-v1",
                    "version": payload["questionnaire_version"],
                }

        def close(self) -> None:
            self.closed = True

    monkeypatch.setitem(run_canary.__globals__, "GymtiCanaryClient", FakeClient)
    receipt = run_canary(
        argparse.Namespace(
            public_base_url="https://demo.example.com",
            timeout_seconds=30,
            output=str(tmp_path / "receipt.json"),
        )
    )

    assert calls[:2] == ["initial next question", "result narrative"]
    assert calls.count("parallel next question") == 4
    assert len(instances) == 5
    assert all(instance.closed for instance in instances)
    assert receipt == {
        "schema_version": 1,
        "service": "trainpal-demo",
        "next_question_passed": True,
        "narrative_passed": True,
        "concurrency": {"requests": 4, "llm": 3, "local_fallback": 1},
        "provider_model": "ark-model-v1",
    }
    encoded = json.dumps(receipt)
    assert "demo.example.com" not in encoded
    assert "PRIVATE result narrative body" not in encoded
    assert "cookie" not in encoded.lower()
    assert "answer" not in encoded.lower()
    assert "question_id" not in encoded.lower()
    assert "reason" not in encoded.lower()


def test_public_gymti_canary_requires_an_output_path() -> None:
    namespace = load_public_gymti_canary()
    parse_args = namespace["parse_args"]

    with pytest.raises(SystemExit):
        parse_args(["--public-base-url", "https://demo.example.com"])
    parsed = parse_args(
        [
            "--public-base-url",
            "https://demo.example.com",
            "--output",
            "private-receipt.json",
        ]
    )
    assert parsed.output == "private-receipt.json"


def test_public_gymti_canary_rejects_an_incomplete_narrative_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = load_public_gymti_canary()
    run_canary = namespace["run_canary"]
    canary_error = namespace["CanaryError"]

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def post_json(
            self, _path: str, payload: dict[str, Any], *, label: str
        ) -> dict[str, object]:
            if label == "initial next question":
                return {
                    "question_id": payload["candidate_question_ids"][0],
                    "source": "llm",
                    "model": "ark-model-v1",
                    "version": payload["questionnaire_version"],
                }
            assert label == "result narrative"
            return {
                "source": "llm",
                "model": "ark-model-v1",
                "version": payload["questionnaire_version"],
                "text": "private narrative",
            }

        def close(self) -> None:
            pass

    monkeypatch.setitem(run_canary.__globals__, "GymtiCanaryClient", FakeClient)
    with pytest.raises(canary_error, match="timestamp"):
        run_canary(
            argparse.Namespace(
                public_base_url="https://demo.example.com",
                timeout_seconds=30,
                output="unused.json",
            )
        )


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(503, json={"detail": "PRIVATE provider response"}), "HTTP 503"),
        (httpx.Response(200, text="PRIVATE invalid JSON"), "invalid JSON"),
        (httpx.Response(200, json=["not", "an", "object"]), "JSON shape"),
    ],
)
def test_public_gymti_canary_fails_closed_on_http_or_json_errors(
    response: httpx.Response, message: str
) -> None:
    namespace = load_public_gymti_canary()
    client_type = namespace["GymtiCanaryClient"]
    canary_error = namespace["CanaryError"]
    client = object.__new__(client_type)
    client._client = httpx.Client(
        base_url="https://demo.example.com",
        transport=httpx.MockTransport(lambda _request: response),
    )

    try:
        with pytest.raises(canary_error, match=message) as captured:
            client.post_json("/api/v1/gymti/next-question", {}, label="next question")
    finally:
        client.close()
    assert "PRIVATE" not in str(captured.value)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"question_id": "not-in-the-contract"}, "illegal question id"),
        ({"model": "   "}, "provider model"),
        ({"model": "https://private.example/model"}, "provider model"),
    ],
)
def test_public_gymti_canary_rejects_illegal_ids_and_unsafe_models(
    override: dict[str, object], message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    namespace = load_public_gymti_canary()
    run_canary = namespace["run_canary"]
    canary_error = namespace["CanaryError"]

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def post_json(
            self, _path: str, payload: dict[str, Any], *, label: str
        ) -> dict[str, object]:
            assert label == "initial next question"
            valid: dict[str, object] = {
                "question_id": payload["candidate_question_ids"][0],
                "source": "llm",
                "model": "ark-model-v1",
                "version": payload["questionnaire_version"],
            }
            return {**valid, **override}

        def close(self) -> None:
            pass

    monkeypatch.setitem(run_canary.__globals__, "GymtiCanaryClient", FakeClient)
    with pytest.raises(canary_error, match=message):
        run_canary(
            argparse.Namespace(
                public_base_url="https://demo.example.com",
                timeout_seconds=30,
                output="unused.json",
            )
        )


def test_public_gymti_canary_rejects_any_other_parallel_distribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = load_public_gymti_canary()
    run_canary = namespace["run_canary"]
    canary_error = namespace["CanaryError"]

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def post_json(
            self, _path: str, payload: dict[str, Any], *, label: str
        ) -> dict[str, object]:
            if label == "result narrative":
                return {
                    "source": "llm",
                    "model": "ark-model-v1",
                    "version": payload["questionnaire_version"],
                    "generated_at": "2026-07-23T00:00:00Z",
                    "text": "private narrative",
                }
            assert label in {"initial next question", "parallel next question"}
            return {
                "question_id": payload["candidate_question_ids"][0],
                "source": "llm",
                "model": "ark-model-v1",
                "version": payload["questionnaire_version"],
            }

        def close(self) -> None:
            pass

    monkeypatch.setitem(run_canary.__globals__, "GymtiCanaryClient", FakeClient)
    with pytest.raises(canary_error, match="distribution"):
        run_canary(
            argparse.Namespace(
                public_base_url="https://demo.example.com",
                timeout_seconds=30,
                output="unused.json",
            )
        )
