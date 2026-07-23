from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence, TypedDict, cast
from urllib.parse import urlsplit

import httpx


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ANALYSIS_API_SRC = REPOSITORY_ROOT / "services" / "analysis-api" / "src"
if str(ANALYSIS_API_SRC) not in sys.path:
    sys.path.insert(0, str(ANALYSIS_API_SRC))

from hakimi_analysis.gymti import (  # noqa: E402
    GymtiContextError,
    GymtiContract,
    GymtiContractError,
    GymtiRankedResult,
    GymtiService,
    load_gymti_contract,
)


CONTRACT_PATH = REPOSITORY_ROOT / "contracts" / "gymti-questionnaire.v1.json"
PARALLEL_REQUESTS = 4
EXPECTED_LLM_RESPONSES = 3
EXPECTED_FALLBACK_RESPONSES = 1
SERVICE_NAME = "trainpal-demo"
SAFE_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class CanaryError(RuntimeError):
    """A content-redacted public GYMTI canary failure."""


def normalize_public_origin(value: str) -> str:
    if value != value.strip():
        raise CanaryError("public GYMTI canary origin must not contain whitespace")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise CanaryError("public GYMTI canary origin must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise CanaryError("public GYMTI canary origin must not contain credentials")
    if parsed.path or parsed.query or parsed.fragment:
        raise CanaryError(
            "public GYMTI canary origin must not contain a path, query, or fragment"
        )
    try:
        port = parsed.port
    except ValueError as error:
        raise CanaryError("public GYMTI canary origin contains an invalid port") from error
    if port not in {None, 443}:
        raise CanaryError("public GYMTI canary origin must use the default HTTPS port")
    return f"https://{parsed.hostname.lower()}"


class AnswerPayload(TypedDict):
    question_id: str
    option_id: str


class NextQuestionPayload(TypedDict):
    questionnaire_version: str
    scoring_version: str
    answered: list[AnswerPayload]
    candidate_question_ids: list[str]


class NarrativePayload(TypedDict):
    questionnaire_version: str
    scoring_version: str
    answered: list[AnswerPayload]
    formal_result_id: str
    secondary_result_id: str | None
    coach_style_id: str
    reason_codes: list[str]


@dataclass(frozen=True, slots=True)
class ProbePayloads:
    contract_version: str
    next_question: NextQuestionPayload
    narrative: NarrativePayload


@dataclass(frozen=True, slots=True)
class NextQuestionResult:
    source: str
    model: str | None


def _find_formal_answers(
    service: GymtiService,
    contract: GymtiContract,
    answers: tuple[tuple[str, str], ...] = (),
) -> tuple[tuple[str, str], ...] | None:
    evaluation = service.evaluate_answers(answers)
    if evaluation.formal_result is not None:
        return answers
    if len(answers) >= contract.maximum_answers:
        return None
    for question_id in evaluation.terminal_candidate_ids:
        for option_id in sorted(contract.question_options[question_id]):
            candidate_answers = (*answers, (question_id, option_id))
            try:
                result = _find_formal_answers(service, contract, candidate_answers)
            except GymtiContextError:
                continue
            if result is not None:
                return result
    return None


def _positive_reason_codes(
    contract: GymtiContract,
    answers: tuple[tuple[str, str], ...],
    result: GymtiRankedResult,
) -> tuple[str, ...]:
    weights: dict[str, tuple[int, int]] = {}
    for answer_index, key in enumerate(answers):
        weight = max(
            contract.option_gymti_scores[key].get(result.gymti_type, 0),
            contract.option_coach_style_scores[key].get(
                result.recommended_coach_style_id, 0
            ),
        )
        if weight <= 0:
            continue
        for reason_code in contract.option_reason_codes[key]:
            current = weights.get(reason_code)
            if current is None or weight > current[0]:
                weights[reason_code] = (weight, answer_index)
    return tuple(
        reason_code
        for reason_code, _ in sorted(
            weights.items(), key=lambda item: (-item[1][0], item[1][1], item[0])
        )[:3]
    )


def _answers_payload(answers: tuple[tuple[str, str], ...]) -> list[AnswerPayload]:
    return [
        AnswerPayload(question_id=question_id, option_id=option_id)
        for question_id, option_id in answers
    ]


def build_probe_payloads(contract_path: Path = CONTRACT_PATH) -> ProbePayloads:
    try:
        contract = load_gymti_contract(contract_path)
    except GymtiContractError as error:
        raise CanaryError("local GYMTI contract is unavailable") from error
    service = GymtiService(contract=contract, model=None, model_name=None)
    initial_evaluation = service.evaluate_answers(())
    candidate_question_ids = list(initial_evaluation.terminal_candidate_ids)
    if not candidate_question_ids:
        raise CanaryError("GYMTI contract did not provide a legal next question")
    next_question = NextQuestionPayload(
        questionnaire_version=contract.questionnaire_version,
        scoring_version=contract.scoring_version,
        answered=[],
        candidate_question_ids=candidate_question_ids,
    )
    formal_answers = _find_formal_answers(service, contract)
    if formal_answers is None:
        raise CanaryError("GYMTI contract did not produce a formal result")
    formal_evaluation = service.evaluate_answers(formal_answers)
    formal_result = formal_evaluation.formal_result
    if formal_result is None:
        raise CanaryError("GYMTI contract did not produce a formal result")
    reason_codes = _positive_reason_codes(contract, formal_answers, formal_result)
    narrative = NarrativePayload(
        questionnaire_version=contract.questionnaire_version,
        scoring_version=contract.scoring_version,
        answered=_answers_payload(formal_answers),
        formal_result_id=formal_result.gymti_type,
        secondary_result_id=formal_result.secondary_gymti_type,
        coach_style_id=formal_result.recommended_coach_style_id,
        reason_codes=list(reason_codes),
    )
    service.validate_next_question_context(
        questionnaire_version=next_question["questionnaire_version"],
        scoring_version=next_question["scoring_version"],
        answered_question_option_ids=(),
        candidate_question_ids=next_question["candidate_question_ids"],
    )
    service.validate_result_narrative_context(
        questionnaire_version=narrative["questionnaire_version"],
        scoring_version=narrative["scoring_version"],
        answered_question_option_ids=formal_answers,
        formal_result_id=narrative["formal_result_id"],
        secondary_result_id=narrative["secondary_result_id"],
        coach_style_id=narrative["coach_style_id"],
        reason_codes=narrative["reason_codes"],
    )
    return ProbePayloads(
        contract_version=contract.version,
        next_question=next_question,
        narrative=narrative,
    )


class GymtiCanaryClient:
    def __init__(self, *, origin: str, timeout_seconds: float) -> None:
        self._client = httpx.Client(
            base_url=origin,
            follow_redirects=False,
            headers={"Origin": origin},
            timeout=httpx.Timeout(
                connect=min(timeout_seconds, 30),
                read=timeout_seconds,
                write=timeout_seconds,
                pool=min(timeout_seconds, 30),
            ),
        )

    def close(self) -> None:
        self._client.close()

    def post_json(
        self, path: str, payload: dict[str, object], *, label: str
    ) -> dict[str, object]:
        try:
            response = self._client.post(path, json=payload)
        except httpx.HTTPError as error:
            raise CanaryError(f"{label} request failed") from error
        if response.status_code != 200:
            raise CanaryError(f"{label} failed with HTTP {response.status_code}")
        try:
            decoded = response.json()
        except ValueError as error:
            raise CanaryError(f"{label} returned invalid JSON") from error
        if not isinstance(decoded, dict):
            raise CanaryError(f"{label} returned an invalid JSON shape")
        return cast(dict[str, object], decoded)


def _model_name(payload: dict[str, object], *, label: str) -> str:
    model = payload.get("model")
    if not isinstance(model, str) or SAFE_MODEL_NAME.fullmatch(model) is None:
        raise CanaryError(f"{label} returned an invalid provider model")
    return model


def _validate_next_question(
    response: dict[str, object],
    payloads: ProbePayloads,
    *,
    label: str,
    expected_source: str | None = None,
) -> NextQuestionResult:
    source = response.get("source")
    if source not in {"llm", "local_fallback"}:
        raise CanaryError(f"{label} returned an invalid source")
    if expected_source is not None and source != expected_source:
        raise CanaryError(f"{label} did not use the required source")
    question_id = response.get("question_id")
    if (
        not isinstance(question_id, str)
        or question_id not in payloads.next_question["candidate_question_ids"]
    ):
        raise CanaryError(f"{label} returned an illegal question id")
    if response.get("version") != payloads.contract_version:
        raise CanaryError(f"{label} returned the wrong contract version")
    if source == "llm":
        model: str | None = _model_name(response, label=label)
    else:
        if response.get("model") is not None:
            raise CanaryError(f"{label} returned an invalid fallback model")
        model = None
    return NextQuestionResult(source=source, model=model)


def _validate_narrative(
    response: dict[str, object], payloads: ProbePayloads
) -> str:
    if response.get("source") != "llm":
        raise CanaryError("result narrative did not use the required source")
    if response.get("version") != payloads.contract_version:
        raise CanaryError("result narrative returned the wrong contract version")
    text = response.get("text")
    if not isinstance(text, str) or not text.strip():
        raise CanaryError("result narrative was empty")
    generated_at = response.get("generated_at")
    if not isinstance(generated_at, str) or not generated_at:
        raise CanaryError("result narrative omitted its timestamp")
    try:
        parsed_generated_at = datetime.fromisoformat(generated_at)
    except ValueError as error:
        raise CanaryError("result narrative returned an invalid timestamp") from error
    if parsed_generated_at.tzinfo is None:
        raise CanaryError("result narrative returned an invalid timestamp")
    return _model_name(response, label="result narrative")


def _parallel_next_question(
    *,
    origin: str,
    timeout_seconds: float,
    barrier: threading.Barrier,
    payloads: ProbePayloads,
) -> NextQuestionResult:
    client = GymtiCanaryClient(origin=origin, timeout_seconds=timeout_seconds)
    try:
        try:
            barrier.wait(timeout=min(timeout_seconds, 30))
        except threading.BrokenBarrierError as error:
            raise CanaryError("parallel next-question workers did not start together") from error
        response = client.post_json(
            "/api/v1/gymti/next-question",
            cast(dict[str, object], payloads.next_question),
            label="parallel next question",
        )
        return _validate_next_question(
            response, payloads, label="parallel next question"
        )
    finally:
        client.close()


def run_canary(args: argparse.Namespace) -> dict[str, object]:
    origin = normalize_public_origin(args.public_base_url)
    timeout_seconds = float(args.timeout_seconds)
    if timeout_seconds <= 0:
        raise CanaryError("public GYMTI canary timeout must be positive")
    payloads = build_probe_payloads()

    client = GymtiCanaryClient(origin=origin, timeout_seconds=timeout_seconds)
    try:
        initial_next = client.post_json(
            "/api/v1/gymti/next-question",
            cast(dict[str, object], payloads.next_question),
            label="initial next question",
        )
        initial_result = _validate_next_question(
            initial_next,
            payloads,
            label="initial next question",
            expected_source="llm",
        )
        narrative = client.post_json(
            "/api/v1/gymti/result-narrative",
            cast(dict[str, object], payloads.narrative),
            label="result narrative",
        )
        narrative_model = _validate_narrative(narrative, payloads)
    finally:
        client.close()

    provider_model = cast(str, initial_result.model)
    if narrative_model != provider_model:
        raise CanaryError("GYMTI probes returned inconsistent provider models")

    barrier = threading.Barrier(PARALLEL_REQUESTS)
    with ThreadPoolExecutor(max_workers=PARALLEL_REQUESTS) as executor:
        futures = [
            executor.submit(
                _parallel_next_question,
                origin=origin,
                timeout_seconds=timeout_seconds,
                barrier=barrier,
                payloads=payloads,
            )
            for _ in range(PARALLEL_REQUESTS)
        ]
        parallel_results = [future.result() for future in futures]
    source_counts = Counter(result.source for result in parallel_results)
    expected_counts = Counter(
        {"llm": EXPECTED_LLM_RESPONSES, "local_fallback": EXPECTED_FALLBACK_RESPONSES}
    )
    if source_counts != expected_counts:
        raise CanaryError("parallel next-question source distribution was invalid")
    if any(
        result.model != provider_model
        for result in parallel_results
        if result.source == "llm"
    ):
        raise CanaryError("GYMTI probes returned inconsistent provider models")

    return {
        "schema_version": 1,
        "service": SERVICE_NAME,
        "next_question_passed": True,
        "narrative_passed": True,
        "concurrency": {
            "requests": PARALLEL_REQUESTS,
            "llm": source_counts["llm"],
            "local_fallback": source_counts["local_fallback"],
        },
        "provider_model": provider_model,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a content-redacted public CloudBase GYMTI canary."
    )
    parser.add_argument("--public-base-url", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=30)
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        receipt = run_canary(args)
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
        output.write_text(encoded, encoding="utf-8")
        print(encoded, end="")
    except CanaryError as error:
        print(f"public GYMTI canary failed: {error}", file=sys.stderr)
        return 1
    except OSError:
        print("public GYMTI canary failed: receipt output could not be written", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
