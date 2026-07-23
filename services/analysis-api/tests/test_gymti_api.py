import asyncio
import json
from pathlib import Path

import httpx
import pytest

from hakimi_analysis.access import ACCESS_COOKIE_NAME, AccessManager
from hakimi_analysis.app import create_app
from hakimi_analysis.bootstrap import build_gymti_service
from hakimi_analysis.gymti import (
    GymtiContextError,
    GymtiContractError,
    GymtiNarrativeInput,
    GymtiQuestionChoiceInput,
    GymtiService,
    load_gymti_contract,
)
from hakimi_analysis.settings import PROJECT_ROOT, Settings

_STYLES = [
    "hotblood",
    "gentle",
    "snarky",
    "analyst",
    "comedian",
    "challenger",
    "zen",
]
_FOUNDATION_IDS = ["goal-focus", "foundation-two", "foundation-three", "foundation-four"]
_ADAPTIVE_IDS = [
    "routine-preference",
    "adaptive-two",
    "adaptive-three",
    "adaptive-four",
    "adaptive-five",
]


def _write_contract(path: Path) -> Path:
    def question(question_id: str, option_id: str, *, phase: str) -> dict[str, object]:
        return {
            "id": question_id,
            "phase": phase,
            "prompt": "fixture prompt",
            "semanticTags": ["goal_strength"],
            "probeTargets": {
                "gymtiTypeIds": ["strength"],
                "coachStyleIds": ["hotblood"],
            },
            "options": [
                {
                    "id": option_id,
                    "label": "fixture option",
                    "semanticTags": ["goal_strength"],
                    "gymtiScores": {"strength": 1},
                    "coachStyleScores": {"hotblood": 1},
                    "hardBans": [],
                    "reasonCodes": ["goal_strength"],
                    "isNoMatch": False,
                }
            ],
        }

    core_questions = [
        question(question_id, f"{question_id}.option", phase="foundation")
        for question_id in _FOUNDATION_IDS
    ] + [
        question(question_id, f"{question_id}.option", phase="adaptive")
        for question_id in _ADAPTIVE_IDS
    ]
    terminal_bank: list[dict[str, object]] = []
    for style in _STYLES:
        terminal_question = question(
            f"terminal-{style}", f"terminal-{style}.strength", phase="terminal"
        )
        terminal_question["probeTargets"] = {
            "gymtiTypeIds": ["strength"],
            "coachStyleIds": [style],
        }
        terminal_question["options"] = [
            {
                "id": f"terminal-{style}.strength",
                "label": "terminal option",
                "semanticTags": ["goal_strength"],
                "gymtiScores": {"strength": 100},
                "coachStyleScores": {style: 100},
                "hardBans": [],
                "reasonCodes": ["goal_strength"],
                "isNoMatch": False,
            }
        ]
        terminal_bank.append(terminal_question)

    path.write_text(
        json.dumps(
            {
                "version": "gymti-v1",
                "gymtiTypes": [{"id": "strength", "label": "strength"}],
                "coachStyleIds": _STYLES,
                "reasonCodes": [{"id": "goal_strength"}],
                "rules": {
                    "questionCount": {"minimum": 5, "maximum": 8},
                    "foundationQuestionIds": _FOUNDATION_IDS,
                    "adaptiveQuestionIds": _ADAPTIVE_IDS,
                    "earlyCompletion": {
                        "afterAnswerCounts": [5, 6, 7],
                        "minimumWinnerScore": 4,
                        "minimumLead": 3,
                        "maximumNoMatchRatioExclusive": 0.5,
                    },
                    "terminal": {"answerCount": 8, "scoreBoost": 100},
                },
                "questions": core_questions,
                "terminalBank": terminal_bank,
            },
        ),
        encoding="utf-8",
    )
    return path


class _Model:
    def __init__(self, *, question_ids: list[str | None], narratives: list[str | None]) -> None:
        self._question_ids = question_ids
        self._narratives = narratives
        self.question_inputs: list[GymtiQuestionChoiceInput] = []
        self.narrative_inputs: list[GymtiNarrativeInput] = []

    async def choose_question(self, payload: GymtiQuestionChoiceInput) -> str | None:
        self.question_inputs.append(payload)
        return self._question_ids.pop(0)

    async def narrate_result(self, payload: GymtiNarrativeInput) -> str | None:
        self.narrative_inputs.append(payload)
        return self._narratives.pop(0)


class _BlockingModel(_Model):
    def __init__(self) -> None:
        super().__init__(question_ids=[], narratives=[])
        self.three_started = asyncio.Event()
        self.release = asyncio.Event()

    async def choose_question(self, payload: GymtiQuestionChoiceInput) -> str | None:
        self.question_inputs.append(payload)
        if len(self.question_inputs) == 3:
            self.three_started.set()
        await self.release.wait()
        return "foundation-two"


def _service(contract_path: Path, model: _Model | None = None) -> GymtiService:
    return GymtiService(
        contract=load_gymti_contract(contract_path),
        model=model,
        model_name="doubao-seed-2-0-mini-260428" if model else None,
    )


def _access_manager(*, judge_concurrency: int = 2) -> AccessManager:
    return AccessManager(
        cookie_secret="test-cookie-secret-with-at-least-32-bytes",
        judge_access_code="test-judge-code",
        judge_concurrency=judge_concurrency,
        public_concurrency=0,
    )


async def _upgrade_to_judge(client: httpx.AsyncClient) -> None:
    session = await client.get("/api/v1/access/session")
    assert session.status_code == 200
    upgraded = await client.post(
        "/api/v1/access/session",
        json={"access_code": "test-judge-code"},
        headers={"Origin": "https://test"},
    )
    assert upgraded.status_code == 200
    assert upgraded.json()["tier"] == "judge"


def _next_question_payload() -> dict[str, object]:
    return {
        "questionnaire_version": "gymti-v1",
        "scoring_version": "gymti-v1",
        "answered": [{"question_id": "goal-focus", "option_id": "goal-focus.option"}],
        "candidate_question_ids": ["foundation-two"],
    }


def _foundation_answers() -> list[tuple[str, str]]:
    return [(question_id, f"{question_id}.option") for question_id in _FOUNDATION_IDS]


def _narrative_payload() -> dict[str, object]:
    answered = [
        {"question_id": question_id, "option_id": f"{question_id}.option"}
        for question_id in _FOUNDATION_IDS
    ]
    answered.append(
        {"question_id": "routine-preference", "option_id": "routine-preference.option"}
    )
    return {
        "questionnaire_version": "gymti-v1",
        "scoring_version": "gymti-v1",
        "answered": answered,
        "formal_result_id": "strength",
        "secondary_result_id": None,
        "coach_style_id": "hotblood",
        "reason_codes": ["goal_strength"],
    }


def test_contract_loader_rejects_duplicate_question_and_wrong_camel_case(tmp_path: Path) -> None:
    path = _write_contract(tmp_path / "gymti.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["questions"].append(payload["questions"][0])
    payload["questions"][0]["options"][0]["hard_bans"] = []
    payload["questions"][0]["options"][0].pop("hardBans")
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(GymtiContractError):
        load_gymti_contract(path)


def test_contract_loader_rejects_terminal_boost_that_cannot_break_a_score_tie(
    tmp_path: Path,
) -> None:
    path = _write_contract(tmp_path / "gymti.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["rules"]["terminal"]["scoreBoost"] = 1
    for terminal in payload["terminalBank"]:
        style = terminal["probeTargets"]["coachStyleIds"][0]
        for option in terminal["options"]:
            option["gymtiScores"]["strength"] = 1
            option["coachStyleScores"][style] = 1
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(GymtiContractError):
        load_gymti_contract(path)


def test_contract_loader_rejects_a_scored_no_match_option(tmp_path: Path) -> None:
    path = _write_contract(tmp_path / "gymti.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["questions"][0]["options"][0]["isNoMatch"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(GymtiContractError):
        load_gymti_contract(path)


def test_contract_loader_rejects_core_hard_bans_that_eliminate_every_terminal_style(
    tmp_path: Path,
) -> None:
    path = _write_contract(tmp_path / "gymti.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["questions"][0]["options"][0]["hardBans"] = _STYLES
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(GymtiContractError):
        load_gymti_contract(path)


def test_project_questionnaire_contract_loads_with_all_terminal_candidates() -> None:
    contract = load_gymti_contract(PROJECT_ROOT / "contracts" / "gymti-questionnaire.v1.json")

    assert contract.version == "gymti-questionnaire.v1"
    assert len(contract.question_ids) == 16
    assert "terminal_zen_goal" in contract.question_ids
    assert contract.maximum_answers == 8


def test_shared_golden_vectors_match_python_contract_evaluation() -> None:
    contract = load_gymti_contract(PROJECT_ROOT / "contracts" / "gymti-questionnaire.v1.json")
    service = GymtiService(contract=contract, model=None, model_name=None)
    vectors = json.loads(
        (PROJECT_ROOT / "contracts" / "gymti-golden-vectors.v1.json").read_text(encoding="utf-8")
    )

    assert vectors["questionnaireVersion"] == contract.version
    for vector in vectors["vectors"]:
        answers = [(answer["questionId"], answer["optionId"]) for answer in vector["answers"]]
        for answer in vector["answers"]:
            assert answer["optionId"] in contract.question_options[answer["questionId"]]
        expected = vector["expected"]
        evaluation = service.evaluate_answers(answers)
        assert evaluation.gymti_scores == expected["gymtiScores"]
        assert evaluation.coach_style_scores == expected["coachStyleScores"]
        assert list(evaluation.excluded_coach_style_ids) == expected["excludedCoachStyleIds"]
        assert evaluation.early_completion is expected["earlyCompletion"]
        assert evaluation.ranked_result is not None
        assert evaluation.ranked_result.gymti_type == expected["result"]["gymtiType"]
        assert (
            evaluation.ranked_result.secondary_gymti_type
            == expected["result"]["secondaryGymtiType"]
        )
        assert (
            evaluation.ranked_result.recommended_coach_style_id
            == expected["result"]["recommendedCoachStyleId"]
        )
        terminal_candidates = expected.get("terminalCandidateIds")
        if terminal_candidates is not None:
            assert evaluation.terminal_candidate_ids == tuple(terminal_candidates)
        is_terminal_completion = contract.question_phases[answers[-1][0]] == "terminal"
        formal_result_expected = expected["earlyCompletion"] or is_terminal_completion
        assert evaluation.formal_result_available is formal_result_expected
        assert (evaluation.formal_result is not None) is formal_result_expected
        if formal_result_expected:
            assert evaluation.formal_result == evaluation.ranked_result


@pytest.mark.asyncio
async def test_next_question_requires_the_exact_contract_candidate_sequence(tmp_path: Path) -> None:
    service = _service(_write_contract(tmp_path / "gymti.json"))

    selected = await service.next_question(
        questionnaire_version="gymti-v1",
        scoring_version="gymti-v1",
        answered_question_option_ids=[("goal-focus", "goal-focus.option")],
        candidate_question_ids=["foundation-two"],
    )

    assert selected.question_id == "foundation-two"
    with pytest.raises(GymtiContextError):
        await service.next_question(
            questionnaire_version="gymti-v1",
            scoring_version="gymti-v1",
            answered_question_option_ids=[],
            candidate_question_ids=["terminal-hotblood"],
        )
    with pytest.raises(GymtiContextError):
        await service.next_question(
            questionnaire_version="gymti-v1",
            scoring_version="gymti-v1",
            answered_question_option_ids=[("goal-focus", "goal-focus.option")],
            candidate_question_ids=[],
        )
    with pytest.raises(GymtiContextError):
        await service.next_question(
            questionnaire_version="gymti-v1",
            scoring_version="gymti-v1",
            answered_question_option_ids=[("goal-focus", "goal-focus.option")],
            candidate_question_ids=["routine-preference"],
        )
    with pytest.raises(GymtiContextError):
        await service.next_question(
            questionnaire_version="gymti-v1",
            scoring_version="gymti-v1",
            answered_question_option_ids=_foundation_answers(),
            candidate_question_ids=list(reversed(_ADAPTIVE_IDS)),
        )


@pytest.mark.asyncio
async def test_adaptive_candidates_filter_to_the_highest_tie_resolution_layer(
    tmp_path: Path,
) -> None:
    path = _write_contract(tmp_path / "gymti.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    for question in payload["questions"][:4]:
        question["options"][0]["coachStyleScores"] = {"hotblood": 1, "gentle": 1}
    adaptive = {question["id"]: question for question in payload["questions"][4:]}
    adaptive["routine-preference"]["probeTargets"]["coachStyleIds"] = [
        "hotblood",
        "gentle",
    ]
    adaptive["adaptive-two"]["probeTargets"]["coachStyleIds"] = [
        "snarky",
        "analyst",
        "comedian",
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")
    model = _Model(question_ids=["routine-preference"], narratives=[])
    service = _service(path, model)

    selected = await service.next_question(
        questionnaire_version="gymti-v1",
        scoring_version="gymti-v1",
        answered_question_option_ids=_foundation_answers(),
        candidate_question_ids=["routine-preference"],
        allow_model=True,
    )

    assert selected.question_id == "routine-preference"
    assert model.question_inputs[0].candidate_question_ids == ("routine-preference",)
    with pytest.raises(GymtiContextError):
        await service.next_question(
            questionnaire_version="gymti-v1",
            scoring_version="gymti-v1",
            answered_question_option_ids=_foundation_answers(),
            candidate_question_ids=_ADAPTIVE_IDS,
        )


@pytest.mark.asyncio
async def test_adaptive_candidates_use_missing_signal_coverage_then_contract_order(
    tmp_path: Path,
) -> None:
    path = _write_contract(tmp_path / "gymti.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    adaptive = {question["id"]: question for question in payload["questions"][4:]}
    adaptive["routine-preference"]["probeTargets"]["coachStyleIds"] = ["gentle"]
    adaptive["adaptive-two"]["probeTargets"]["coachStyleIds"] = [
        "snarky",
        "analyst",
    ]
    adaptive["adaptive-three"]["probeTargets"]["coachStyleIds"] = [
        "comedian",
        "challenger",
    ]
    adaptive["adaptive-four"]["probeTargets"]["coachStyleIds"] = ["zen"]
    adaptive["adaptive-five"]["probeTargets"]["coachStyleIds"] = ["hotblood"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    model = _Model(question_ids=["adaptive-three"], narratives=[])
    service = _service(path, model)

    selected = await service.next_question(
        questionnaire_version="gymti-v1",
        scoring_version="gymti-v1",
        answered_question_option_ids=_foundation_answers(),
        candidate_question_ids=["adaptive-two", "adaptive-three"],
        allow_model=True,
    )

    assert selected.question_id == "adaptive-three"
    prompt = model.question_inputs[0]
    assert prompt.candidate_question_ids == ("adaptive-two", "adaptive-three")
    assert tuple(target.question_id for target in prompt.candidate_probe_targets) == (
        "adaptive-two",
        "adaptive-three",
    )
    assert prompt.missing_coach_style_ids == (
        "gentle",
        "snarky",
        "analyst",
        "comedian",
        "challenger",
        "zen",
    )
    fallback = await service.next_question(
        questionnaire_version="gymti-v1",
        scoring_version="gymti-v1",
        answered_question_option_ids=_foundation_answers(),
        candidate_question_ids=["adaptive-two", "adaptive-three"],
    )
    assert fallback.question_id == "adaptive-two"
    assert fallback.source == "local_fallback"


@pytest.mark.asyncio
async def test_next_question_rejects_answers_after_early_completion(tmp_path: Path) -> None:
    service = _service(_write_contract(tmp_path / "gymti.json"))
    early_answers = [*_foundation_answers(), ("routine-preference", "routine-preference.option")]

    with pytest.raises(GymtiContextError):
        await service.next_question(
            questionnaire_version="gymti-v1",
            scoring_version="gymti-v1",
            answered_question_option_ids=early_answers,
            candidate_question_ids=["adaptive-two"],
        )


@pytest.mark.asyncio
async def test_next_question_filters_hard_banned_terminal_candidates_from_golden_vector() -> None:
    contract = load_gymti_contract(PROJECT_ROOT / "contracts" / "gymti-questionnaire.v1.json")
    service = GymtiService(contract=contract, model=None, model_name=None)
    vectors = json.loads(
        (PROJECT_ROOT / "contracts" / "gymti-golden-vectors.v1.json").read_text(encoding="utf-8")
    )
    vector = next(item for item in vectors["vectors"] if item["id"] == "hard-ban-terminal-filter")
    answers = [(answer["questionId"], answer["optionId"]) for answer in vector["answers"]]
    candidates = vector["expected"]["terminalCandidateIds"]

    selected = await service.next_question(
        questionnaire_version=contract.questionnaire_version,
        scoring_version=contract.scoring_version,
        answered_question_option_ids=answers,
        candidate_question_ids=candidates,
    )

    assert selected.question_id == "terminal_hotblood_goal"
    with pytest.raises(GymtiContextError):
        await service.next_question(
            questionnaire_version=contract.questionnaire_version,
            scoring_version=contract.scoring_version,
            answered_question_option_ids=answers,
            candidate_question_ids=[*candidates, "terminal_snarky_goal"],
        )


@pytest.mark.asyncio
async def test_next_question_retries_invalid_model_choice_then_accepts_legal_candidate(
    tmp_path: Path,
) -> None:
    model = _Model(question_ids=["not-a-contract-question", "foundation-two"], narratives=[])
    service = _service(_write_contract(tmp_path / "gymti.json"), model)

    selected = await service.next_question(
        questionnaire_version="gymti-v1",
        scoring_version="gymti-v1",
        answered_question_option_ids=[("goal-focus", "goal-focus.option")],
        candidate_question_ids=["foundation-two"],
        allow_model=True,
    )

    assert selected.question_id == "foundation-two"
    assert selected.source == "llm"
    assert len(model.question_inputs) == 2
    assert model.question_inputs[0].answered_question_option_ids == (
        ("goal-focus", "goal-focus.option"),
    )
    assert model.question_inputs[0].semantic_tags == ("goal_strength",)
    assert model.question_inputs[0].gymti_scores == {"strength": 1}
    assert model.question_inputs[0].coach_style_scores == {
        "hotblood": 1,
        "gentle": 0,
        "snarky": 0,
        "analyst": 0,
        "comedian": 0,
        "challenger": 0,
        "zen": 0,
    }
    assert model.question_inputs[0].excluded_coach_style_ids == ()
    assert model.question_inputs[0].no_match_count == 0
    assert model.question_inputs[0].missing_gymti_type_ids == ()
    assert model.question_inputs[0].missing_coach_style_ids == (
        "gentle",
        "snarky",
        "analyst",
        "comedian",
        "challenger",
        "zen",
    )
    assert model.question_inputs[0].candidate_probe_targets[0].question_id == "foundation-two"
    assert model.question_inputs[0].candidate_probe_targets[0].gymti_type_ids == ("strength",)
    assert model.question_inputs[0].candidate_probe_targets[0].coach_style_ids == ("hotblood",)


@pytest.mark.asyncio
async def test_next_question_uses_first_legal_local_fallback_when_model_is_missing_or_invalid(
    tmp_path: Path,
) -> None:
    service = _service(_write_contract(tmp_path / "gymti.json"))

    selected = await service.next_question(
        questionnaire_version="gymti-v1",
        scoring_version="gymti-v1",
        answered_question_option_ids=_foundation_answers(),
        candidate_question_ids=_ADAPTIVE_IDS,
    )

    assert selected.question_id == "routine-preference"
    assert selected.source == "local_fallback"
    assert selected.model is None


@pytest.mark.asyncio
async def test_next_question_uses_first_legal_fallback_after_bounded_invalid_retries(
    tmp_path: Path,
) -> None:
    model = _Model(question_ids=["not-legal", "still-not-legal"], narratives=[])
    service = _service(_write_contract(tmp_path / "gymti.json"), model)

    selected = await service.next_question(
        questionnaire_version="gymti-v1",
        scoring_version="gymti-v1",
        answered_question_option_ids=_foundation_answers(),
        candidate_question_ids=_ADAPTIVE_IDS,
        allow_model=True,
    )

    assert selected.question_id == "routine-preference"
    assert selected.source == "local_fallback"
    assert len(model.question_inputs) == 2


@pytest.mark.asyncio
async def test_result_narrative_never_accepts_model_result_fields_and_uses_template(
    tmp_path: Path,
) -> None:
    model = _Model(
        question_ids=[],
        narratives=['{"formal_result_id":"other","text":"cannot use"}', None],
    )
    service = _service(_write_contract(tmp_path / "gymti.json"), model)

    snapshot = await service.result_narrative(
        questionnaire_version="gymti-v1",
        scoring_version="gymti-v1",
        answered_question_option_ids=[
            *_foundation_answers(),
            ("routine-preference", "routine-preference.option"),
        ],
        formal_result_id="strength",
        secondary_result_id=None,
        coach_style_id="hotblood",
        reason_codes=["goal_strength"],
        allow_model=True,
    )

    assert snapshot.source == "template"
    assert snapshot.model is None
    assert snapshot.version == "gymti-v1"
    assert snapshot.text
    assert len(model.narrative_inputs) == 2
    assert model.narrative_inputs[0].formal_result_id == "strength"
    assert model.narrative_inputs[0].secondary_result_id is None
    assert model.narrative_inputs[0].coach_style_id == "hotblood"
    assert model.narrative_inputs[0].reason_codes == ("goal_strength",)


@pytest.mark.asyncio
async def test_result_narrative_rejects_any_snapshot_not_rebuilt_from_answers_before_model(
    tmp_path: Path,
) -> None:
    model = _Model(question_ids=[], narratives=[])
    app = create_app(
        gymti_service=_service(_write_contract(tmp_path / "gymti.json"), model),
        gymti_llm_enabled=True,
        access=_access_manager(),
        app_env="test",
    )
    invalid_payloads: list[dict[str, object]] = []

    incomplete = _narrative_payload()
    incomplete["answered"] = [
        {"question_id": question_id, "option_id": f"{question_id}.option"}
        for question_id in _FOUNDATION_IDS
    ]
    invalid_payloads.append(incomplete)

    wrong_formal = _narrative_payload()
    wrong_formal["formal_result_id"] = "not-the-derived-result"
    invalid_payloads.append(wrong_formal)

    wrong_style = _narrative_payload()
    wrong_style["coach_style_id"] = "gentle"
    invalid_payloads.append(wrong_style)

    wrong_secondary = _narrative_payload()
    wrong_secondary["secondary_result_id"] = "strength"
    invalid_payloads.append(wrong_secondary)

    wrong_reasons = _narrative_payload()
    wrong_reasons["reason_codes"] = []
    invalid_payloads.append(wrong_reasons)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        await _upgrade_to_judge(client)
        responses = [
            await client.post("/api/v1/gymti/result-narrative", json=payload)
            for payload in invalid_payloads
        ]

    assert all(response.status_code == 422 for response in responses)
    assert model.narrative_inputs == []


@pytest.mark.asyncio
async def test_gymti_endpoints_are_stateless_and_return_no_store_responses(tmp_path: Path) -> None:
    app = create_app(
        gymti_service=_service(_write_contract(tmp_path / "gymti.json")),
        app_env="test",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        next_response = await client.post(
            "/api/v1/gymti/next-question", json=_next_question_payload()
        )
        narrative_response = await client.post(
            "/api/v1/gymti/result-narrative", json=_narrative_payload()
        )

    assert next_response.status_code == 200
    assert next_response.json()["question_id"] == "foundation-two"
    assert next_response.headers["cache-control"] == "no-store"
    assert narrative_response.status_code == 200
    assert narrative_response.json()["source"] == "template"
    assert narrative_response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_endpoint_rejects_context_that_does_not_match_the_contract(tmp_path: Path) -> None:
    app = create_app(
        gymti_service=_service(_write_contract(tmp_path / "gymti.json")),
        app_env="test",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        payload = _next_question_payload()
        payload["questionnaire_version"] = "gymti-v0"
        response = await client.post("/api/v1/gymti/next-question", json=payload)

    assert response.status_code == 422
    assert "goal-focus" not in response.text


@pytest.mark.asyncio
async def test_endpoint_rejects_an_injected_candidate_before_any_model_call(tmp_path: Path) -> None:
    model = _Model(question_ids=["terminal-hotblood"], narratives=[])
    app = create_app(
        gymti_service=_service(_write_contract(tmp_path / "gymti.json"), model),
        gymti_llm_enabled=True,
        access=_access_manager(),
        app_env="test",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        await _upgrade_to_judge(client)
        response = await client.post(
            "/api/v1/gymti/next-question",
            json={
                "questionnaire_version": "gymti-v1",
                "scoring_version": "gymti-v1",
                "answered": [],
                "candidate_question_ids": ["terminal-hotblood"],
            },
        )

    assert response.status_code == 422
    assert model.question_inputs == []


@pytest.mark.asyncio
async def test_disabled_llm_does_not_construct_a_configured_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnexpectedModel:
        def __init__(self, **_: object) -> None:
            raise AssertionError("disabled GYMTI LLM must not be constructed")

    monkeypatch.setattr("hakimi_analysis.bootstrap.OpenAiStyleGymtiModel", UnexpectedModel)
    settings = Settings(
        _env_file=None,
        gymti_contract_path=_write_contract(tmp_path / "gymti.json"),
        gymti_llm_api_key="ark-secret-value",
        gymti_llm_enabled=False,
    )
    async with httpx.AsyncClient() as http_client:
        service = build_gymti_service(settings, http_client)

    assert service is not None
    assert service.model_available is False


@pytest.mark.asyncio
async def test_unconfirmed_retention_does_not_construct_a_configured_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnexpectedModel:
        def __init__(self, **_: object) -> None:
            raise AssertionError("unconfirmed GYMTI retention must not construct a model")

    monkeypatch.setattr("hakimi_analysis.bootstrap.OpenAiStyleGymtiModel", UnexpectedModel)
    settings = Settings(
        _env_file=None,
        gymti_contract_path=_write_contract(tmp_path / "gymti.json"),
        gymti_llm_api_key="ark-secret-value",
        gymti_llm_enabled=True,
        gymti_llm_retention_confirmed=False,
    )
    async with httpx.AsyncClient() as http_client:
        service = build_gymti_service(settings, http_client)

    assert service is not None
    assert service.model_available is False


@pytest.mark.asyncio
async def test_enabled_llm_calls_model_for_public_requests_without_access_tier_dependency(
    tmp_path: Path,
) -> None:
    model = _Model(question_ids=["foundation-two"], narratives=['{"text":"public narrative"}'])
    app = create_app(
        gymti_service=_service(_write_contract(tmp_path / "gymti.json"), model),
        gymti_llm_enabled=True,
        access=_access_manager(judge_concurrency=0),
        app_env="test",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        next_response = await client.post(
            "/api/v1/gymti/next-question", json=_next_question_payload()
        )
        narrative_response = await client.post(
            "/api/v1/gymti/result-narrative", json=_narrative_payload()
        )

    assert next_response.json()["source"] == "llm"
    assert narrative_response.json()["source"] == "llm"
    assert len(model.question_inputs) == 1
    assert len(model.narrative_inputs) == 1


@pytest.mark.asyncio
async def test_enabled_llm_does_not_consume_analysis_access_quota(tmp_path: Path) -> None:
    access = _access_manager(judge_concurrency=1)
    model = _Model(question_ids=["foundation-two"], narratives=['{"text":"judge narrative"}'])
    app = create_app(
        gymti_service=_service(_write_contract(tmp_path / "gymti.json"), model),
        gymti_llm_enabled=True,
        access=access,
        app_env="test",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        await _upgrade_to_judge(client)
        next_response = await client.post(
            "/api/v1/gymti/next-question", json=_next_question_payload()
        )
        narrative_response = await client.post(
            "/api/v1/gymti/result-narrative", json=_narrative_payload()
        )
        session = access.resolve(client.cookies.get(ACCESS_COOKIE_NAME))

    assert next_response.json()["source"] == "llm"
    assert narrative_response.json()["source"] == "llm"
    assert len(model.question_inputs) == 1
    assert len(model.narrative_inputs) == 1
    lease = await access.reserve(session, source_id="post-gymti-check", client_ip="127.0.0.1")
    await lease.release()


@pytest.mark.asyncio
async def test_fourth_concurrent_gymti_model_request_falls_back_without_waiting(
    tmp_path: Path,
) -> None:
    access = _access_manager(judge_concurrency=0)
    model = _BlockingModel()
    app = create_app(
        gymti_service=_service(_write_contract(tmp_path / "gymti.json"), model),
        gymti_llm_enabled=True,
        gymti_llm_concurrency=3,
        access=access,
        app_env="test",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        admitted = [
            asyncio.create_task(
                client.post("/api/v1/gymti/next-question", json=_next_question_payload())
            )
            for _ in range(3)
        ]
        await asyncio.wait_for(model.three_started.wait(), timeout=1)
        fourth = await asyncio.wait_for(
            client.post("/api/v1/gymti/next-question", json=_next_question_payload()),
            timeout=1,
        )
        model.release.set()
        admitted_responses = await asyncio.gather(*admitted)

    assert [response.json()["source"] for response in admitted_responses] == ["llm"] * 3
    assert fourth.status_code == 200
    assert fourth.json()["source"] == "local_fallback"
    assert len(model.question_inputs) == 3


@pytest.mark.asyncio
async def test_missing_ark_configuration_builds_an_explicit_local_fallback(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        gymti_contract_path=_write_contract(tmp_path / "gymti.json"),
        gymti_llm_enabled=True,
    )
    async with httpx.AsyncClient() as http_client:
        service = build_gymti_service(settings, http_client)
        assert service is not None
        selected = await service.next_question(
            questionnaire_version="gymti-v1",
            scoring_version="gymti-v1",
            answered_question_option_ids=[],
            candidate_question_ids=["goal-focus"],
        )

    assert selected.source == "local_fallback"
    assert selected.model is None
