"""Stateless, privacy-bounded GYMTI question selection and result narration."""

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import httpx


class GymtiContractError(ValueError):
    """The versioned questionnaire contract cannot safely be used."""


class GymtiContextError(ValueError):
    """A client context does not match the loaded questionnaire contract."""


def _stable_id(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise GymtiContractError("GYMTI contract contains an invalid stable id")
    if any(character.isspace() for character in value):
        raise GymtiContractError("GYMTI contract contains an invalid stable id")
    return value


def _as_mapping(value: object, *, error: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise GymtiContractError(error)
    return value


def _stable_id_list(value: object, *, error: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise GymtiContractError(error)
    try:
        result = tuple(_stable_id(item) for item in value)
    except GymtiContractError as cause:
        raise GymtiContractError(error) from cause
    if len(set(result)) != len(result):
        raise GymtiContractError(error)
    return result


def _object_catalog_id_list(
    value: object,
    *,
    error: str,
    require_label: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise GymtiContractError(error)
    result: list[str] = []
    for raw_entry in value:
        entry = _as_mapping(raw_entry, error=error)
        result.append(_stable_id(entry.get("id")))
        if require_label:
            label = entry.get("label")
            if not isinstance(label, str) or not label.strip():
                raise GymtiContractError(error)
    if len(set(result)) != len(result):
        raise GymtiContractError(error)
    return tuple(result)


def _object_catalog_ids(
    value: object,
    *,
    error: str,
    require_label: bool = False,
) -> frozenset[str]:
    return frozenset(
        _object_catalog_id_list(value, error=error, require_label=require_label)
    )


def _string_catalog_ids(value: object, *, error: str) -> frozenset[str]:
    values = _stable_id_list(value, error=error)
    if not values:
        raise GymtiContractError(error)
    return frozenset(values)


def _integer(value: object, *, error: str, minimum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise GymtiContractError(error)
    if minimum is not None and value < minimum:
        raise GymtiContractError(error)
    return value


def _validate_score_map(
    value: object,
    *,
    legal_ids: frozenset[str],
    error: str,
) -> dict[str, int]:
    mapping = _as_mapping(value, error=error)
    scores: dict[str, int] = {}
    for raw_id, score in mapping.items():
        stable = _stable_id(raw_id)
        if stable not in legal_ids or not isinstance(score, int) or isinstance(score, bool):
            raise GymtiContractError(error)
        scores[stable] = score
    return scores


@dataclass(frozen=True, slots=True)
class _OptionDefinition:
    id: str
    semantic_tags: tuple[str, ...]
    gymti_scores: Mapping[str, int]
    coach_style_scores: Mapping[str, int]
    hard_bans: tuple[str, ...]
    reason_codes: tuple[str, ...]
    is_no_match: bool


@dataclass(frozen=True, slots=True)
class _QuestionDefinition:
    id: str
    phase: str
    gymti_type_targets: tuple[str, ...]
    coach_style_targets: tuple[str, ...]
    options: Mapping[str, _OptionDefinition]


@dataclass(frozen=True, slots=True)
class _RulesDefinition:
    minimum_answers: int
    maximum_answers: int
    foundation_question_ids: tuple[str, ...]
    adaptive_question_ids: tuple[str, ...]
    early_completion_after_answer_counts: tuple[int, ...]
    early_completion_minimum_winner_score: int
    early_completion_minimum_lead: int
    early_completion_maximum_no_match_ratio_exclusive: float
    terminal_answer_count: int
    terminal_score_boost: int


def _validate_question(
    raw_question: object,
    *,
    gymti_type_ids: frozenset[str],
    coach_style_ids: frozenset[str],
    known_reason_codes: frozenset[str],
) -> _QuestionDefinition:
    question = _as_mapping(raw_question, error="GYMTI contract question is invalid")
    question_id = _stable_id(question.get("id"))
    phase = _stable_id(question.get("phase"))
    if phase not in {"foundation", "adaptive", "terminal"}:
        raise GymtiContractError("GYMTI contract question phase is invalid")
    prompt = question.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise GymtiContractError("GYMTI contract question is invalid")
    semantic_tags = _stable_id_list(
        question.get("semanticTags"), error="GYMTI contract question is invalid"
    )
    if not semantic_tags:
        raise GymtiContractError("GYMTI contract question is invalid")
    probe_targets = _as_mapping(
        question.get("probeTargets"),
        error="GYMTI contract question is invalid",
    )
    target_types = _stable_id_list(
        probe_targets.get("gymtiTypeIds"),
        error="GYMTI contract question is invalid",
    )
    target_styles = _stable_id_list(
        probe_targets.get("coachStyleIds"),
        error="GYMTI contract question is invalid",
    )
    if (
        not target_types
        or not target_styles
        or not set(target_types).issubset(gymti_type_ids)
        or not set(target_styles).issubset(coach_style_ids)
    ):
        raise GymtiContractError("GYMTI contract question probe targets are invalid")
    raw_options = question.get("options")
    if not isinstance(raw_options, list) or not raw_options:
        raise GymtiContractError("GYMTI contract question options are invalid")
    options: dict[str, _OptionDefinition] = {}
    for raw_option in raw_options:
        option = _as_mapping(raw_option, error="GYMTI contract option is invalid")
        option_id = _stable_id(option.get("id"))
        if option_id in options:
            raise GymtiContractError("GYMTI contract option ids must be unique")
        label = option.get("label")
        if not isinstance(label, str) or not label.strip():
            raise GymtiContractError("GYMTI contract option is invalid")
        option_tags = _stable_id_list(
            option.get("semanticTags"), error="GYMTI contract option is invalid"
        )
        if not option_tags:
            raise GymtiContractError("GYMTI contract option is invalid")
        gymti_scores = _validate_score_map(
            option.get("gymtiScores"),
            legal_ids=gymti_type_ids,
            error="GYMTI contract option scores are invalid",
        )
        coach_style_scores = _validate_score_map(
            option.get("coachStyleScores"),
            legal_ids=coach_style_ids,
            error="GYMTI contract option scores are invalid",
        )
        hard_bans = _stable_id_list(
            option.get("hardBans"),
            error="GYMTI contract option is invalid",
        )
        if not set(hard_bans).issubset(coach_style_ids):
            raise GymtiContractError("GYMTI contract option hard bans are invalid")
        reason_codes = _stable_id_list(
            option.get("reasonCodes"),
            error="GYMTI contract option is invalid",
        )
        if not set(reason_codes).issubset(known_reason_codes):
            raise GymtiContractError("GYMTI contract option reason codes are invalid")
        is_no_match = option.get("isNoMatch")
        if not isinstance(is_no_match, bool):
            raise GymtiContractError("GYMTI contract option is invalid")
        if is_no_match and (gymti_scores or coach_style_scores or hard_bans or reason_codes):
            raise GymtiContractError("GYMTI contract no-match option is invalid")
        options[option_id] = _OptionDefinition(
            id=option_id,
            semantic_tags=option_tags,
            gymti_scores=gymti_scores,
            coach_style_scores=coach_style_scores,
            hard_bans=hard_bans,
            reason_codes=reason_codes,
            is_no_match=is_no_match,
        )
    return _QuestionDefinition(
        id=question_id,
        phase=phase,
        gymti_type_targets=target_types,
        coach_style_targets=target_styles,
        options=options,
    )


def _validate_rules(value: object) -> _RulesDefinition:
    rules = _as_mapping(value, error="GYMTI contract rules are missing")
    question_count = _as_mapping(
        rules.get("questionCount"), error="GYMTI contract question count rules are invalid"
    )
    minimum_answers = _integer(
        question_count.get("minimum"),
        error="GYMTI contract question count rules are invalid",
        minimum=1,
    )
    maximum_answers = _integer(
        question_count.get("maximum"),
        error="GYMTI contract question count rules are invalid",
        minimum=minimum_answers,
    )
    if maximum_answers > 8:
        raise GymtiContractError("GYMTI contract question count rules are invalid")
    foundation_question_ids = _stable_id_list(
        rules.get("foundationQuestionIds"),
        error="GYMTI contract foundation question rules are invalid",
    )
    adaptive_question_ids = _stable_id_list(
        rules.get("adaptiveQuestionIds"),
        error="GYMTI contract adaptive question rules are invalid",
    )
    if (
        not foundation_question_ids
        or not adaptive_question_ids
        or set(foundation_question_ids).intersection(adaptive_question_ids)
    ):
        raise GymtiContractError("GYMTI contract question phase rules are invalid")
    early_completion = _as_mapping(
        rules.get("earlyCompletion"), error="GYMTI contract early completion rules are invalid"
    )
    after_answer_counts = _stable_integer_list(
        early_completion.get("afterAnswerCounts"),
        error="GYMTI contract early completion rules are invalid",
    )
    minimum_winner_score = _integer(
        early_completion.get("minimumWinnerScore"),
        error="GYMTI contract early completion rules are invalid",
        minimum=1,
    )
    minimum_lead = _integer(
        early_completion.get("minimumLead"),
        error="GYMTI contract early completion rules are invalid",
        minimum=1,
    )
    if after_answer_counts != tuple(range(minimum_answers, maximum_answers)):
        raise GymtiContractError("GYMTI contract early completion rules are invalid")
    maximum_no_match_ratio = early_completion.get("maximumNoMatchRatioExclusive")
    if (
        not isinstance(maximum_no_match_ratio, (int, float))
        or isinstance(maximum_no_match_ratio, bool)
        or not 0 < maximum_no_match_ratio < 1
    ):
        raise GymtiContractError("GYMTI contract early completion rules are invalid")
    terminal = _as_mapping(rules.get("terminal"), error="GYMTI contract terminal rules are invalid")
    terminal_answer_count = _integer(
        terminal.get("answerCount"),
        error="GYMTI contract terminal rules are invalid",
        minimum=minimum_answers,
    )
    terminal_score_boost = _integer(
        terminal.get("scoreBoost"),
        error="GYMTI contract terminal rules are invalid",
        minimum=1,
    )
    if terminal_answer_count != maximum_answers:
        raise GymtiContractError("GYMTI contract terminal rules are invalid")
    return _RulesDefinition(
        minimum_answers=minimum_answers,
        maximum_answers=maximum_answers,
        foundation_question_ids=foundation_question_ids,
        adaptive_question_ids=adaptive_question_ids,
        early_completion_after_answer_counts=after_answer_counts,
        early_completion_minimum_winner_score=minimum_winner_score,
        early_completion_minimum_lead=minimum_lead,
        early_completion_maximum_no_match_ratio_exclusive=float(maximum_no_match_ratio),
        terminal_answer_count=terminal_answer_count,
        terminal_score_boost=terminal_score_boost,
    )


def _stable_integer_list(value: object, *, error: str) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise GymtiContractError(error)
    result = tuple(_integer(item, error=error, minimum=1) for item in value)
    if len(set(result)) != len(result):
        raise GymtiContractError(error)
    return result


def _maximum_nonterminal_score_gap(
    questions: Mapping[str, _QuestionDefinition],
    score_ids: frozenset[str],
    *,
    score_kind: str,
) -> int:
    """Return a conservative all-core-question bound for a terminal score race."""

    minimum_totals = {score_id: 0 for score_id in score_ids}
    maximum_totals = {score_id: 0 for score_id in score_ids}
    for question in questions.values():
        for score_id in score_ids:
            if score_kind == "gymti":
                values = [
                    option.gymti_scores.get(score_id, 0) for option in question.options.values()
                ]
            else:
                values = [
                    option.coach_style_scores.get(score_id, 0)
                    for option in question.options.values()
                ]
            minimum_totals[score_id] += min(values)
            maximum_totals[score_id] += max(values)
    return max(
        (
            maximum_totals[other_id] - minimum_totals[target_id]
            for target_id in score_ids
            for other_id in score_ids
            if target_id != other_id
        ),
        default=0,
    )


def _validate_question_groups(
    *,
    rules: _RulesDefinition,
    core_questions: Mapping[str, _QuestionDefinition],
    terminal_questions: Sequence[_QuestionDefinition],
    gymti_type_ids: frozenset[str],
    coach_style_ids: frozenset[str],
) -> None:
    configured_core_ids = set(rules.foundation_question_ids).union(rules.adaptive_question_ids)
    if configured_core_ids != set(core_questions):
        raise GymtiContractError("GYMTI contract question phase rules are invalid")
    if any(
        core_questions[question_id].phase != "foundation"
        for question_id in rules.foundation_question_ids
    ) or any(
        core_questions[question_id].phase != "adaptive"
        for question_id in rules.adaptive_question_ids
    ):
        raise GymtiContractError("GYMTI contract question phase rules are invalid")
    maximum_nonterminal_gap = max(
        _maximum_nonterminal_score_gap(
            core_questions,
            gymti_type_ids,
            score_kind="gymti",
        ),
        _maximum_nonterminal_score_gap(
            core_questions,
            coach_style_ids,
            score_kind="coach_style",
        ),
    )
    if rules.terminal_score_boost <= maximum_nonterminal_gap:
        raise GymtiContractError("GYMTI contract terminal score boost is invalid")
    nonterminal_hard_bans = {
        style_id
        for question in core_questions.values()
        for option in question.options.values()
        for style_id in option.hard_bans
    }
    if coach_style_ids.issubset(nonterminal_hard_bans):
        raise GymtiContractError("GYMTI contract terminal candidates can all be hard-banned")
    if len(terminal_questions) != len(coach_style_ids):
        raise GymtiContractError("GYMTI contract terminal bank is invalid")
    terminal_styles: list[str] = []
    for question in terminal_questions:
        if (
            question.phase != "terminal"
            or len(question.coach_style_targets) != 1
            or set(question.gymti_type_targets) != gymti_type_ids
            or len(question.options) != len(gymti_type_ids)
        ):
            raise GymtiContractError("GYMTI contract terminal bank is invalid")
        target_style = question.coach_style_targets[0]
        terminal_styles.append(target_style)
        terminal_result_ids: set[str] = set()
        for option in question.options.values():
            if (
                option.is_no_match
                or option.hard_bans
                or len(option.gymti_scores) != 1
                or set(option.coach_style_scores) != {target_style}
                or option.coach_style_scores[target_style] != rules.terminal_score_boost
            ):
                raise GymtiContractError("GYMTI contract terminal bank is invalid")
            result_id, result_score = next(iter(option.gymti_scores.items()))
            if result_score != rules.terminal_score_boost:
                raise GymtiContractError("GYMTI contract terminal bank is invalid")
            terminal_result_ids.add(result_id)
        if terminal_result_ids != gymti_type_ids:
            raise GymtiContractError("GYMTI contract terminal bank is invalid")
    if set(terminal_styles) != coach_style_ids or len(set(terminal_styles)) != len(terminal_styles):
        raise GymtiContractError("GYMTI contract terminal bank is invalid")


@dataclass(frozen=True, slots=True)
class GymtiContract:
    version: str
    questionnaire_version: str
    scoring_version: str
    question_options: Mapping[str, frozenset[str]]
    question_phases: Mapping[str, str]
    option_semantic_tags: Mapping[tuple[str, str], tuple[str, ...]]
    option_gymti_scores: Mapping[tuple[str, str], Mapping[str, int]]
    option_coach_style_scores: Mapping[tuple[str, str], Mapping[str, int]]
    option_hard_bans: Mapping[tuple[str, str], tuple[str, ...]]
    option_reason_codes: Mapping[tuple[str, str], tuple[str, ...]]
    option_is_no_match: Mapping[tuple[str, str], bool]
    question_gymti_type_targets: Mapping[str, tuple[str, ...]]
    question_coach_style_targets: Mapping[str, tuple[str, ...]]
    result_ids: frozenset[str]
    result_type_order: tuple[str, ...]
    coach_style_ids: frozenset[str]
    coach_style_order: tuple[str, ...]
    reason_codes: frozenset[str]
    foundation_question_ids: tuple[str, ...]
    adaptive_question_ids: tuple[str, ...]
    terminal_question_ids: tuple[str, ...]
    early_completion_answer_counts: tuple[int, ...]
    early_completion_minimum_winner_score: int
    early_completion_minimum_lead: int
    early_completion_maximum_no_match_ratio_exclusive: float
    minimum_answers: int
    maximum_answers: int
    terminal_answer_count: int
    terminal_score_boost: int

    @property
    def question_ids(self) -> frozenset[str]:
        return frozenset(self.question_options)


def load_gymti_contract(path: Path) -> GymtiContract:
    """Load and fail-closed validate the shared v1 questionnaire and scoring contract."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GymtiContractError("GYMTI contract is invalid") from error
    payload = _as_mapping(raw, error="GYMTI contract root is invalid")
    version = _stable_id(payload.get("version"))
    result_type_order = _object_catalog_id_list(
        payload.get("gymtiTypes"),
        error="GYMTI contract GYMTI type catalog is invalid",
        require_label=True,
    )
    gymti_type_ids = frozenset(result_type_order)
    coach_style_order = _stable_id_list(
        payload.get("coachStyleIds"), error="GYMTI contract coach style catalog is invalid"
    )
    if not coach_style_order:
        raise GymtiContractError("GYMTI contract coach style catalog is invalid")
    coach_style_ids = frozenset(coach_style_order)
    reason_codes = _object_catalog_ids(
        payload.get("reasonCodes"), error="GYMTI contract reason code catalog is invalid"
    )
    rules = _validate_rules(payload.get("rules"))
    raw_questions = payload.get("questions")
    raw_terminal_bank = payload.get("terminalBank")
    if not isinstance(raw_questions, list) or not raw_questions:
        raise GymtiContractError("GYMTI contract questions are missing")
    if not isinstance(raw_terminal_bank, list) or not raw_terminal_bank:
        raise GymtiContractError("GYMTI contract terminal bank is missing")
    definitions: dict[str, _QuestionDefinition] = {}
    core_questions: dict[str, _QuestionDefinition] = {}
    terminal_questions: list[_QuestionDefinition] = []
    for raw_question, expected_group in (
        *((question, "core") for question in raw_questions),
        *((question, "terminal") for question in raw_terminal_bank),
    ):
        definition = _validate_question(
            raw_question,
            gymti_type_ids=gymti_type_ids,
            coach_style_ids=coach_style_ids,
            known_reason_codes=reason_codes,
        )
        if definition.id in definitions:
            raise GymtiContractError("GYMTI contract question ids must be unique")
        if expected_group == "core" and definition.phase == "terminal":
            raise GymtiContractError("GYMTI contract question phase is invalid")
        if expected_group == "terminal" and definition.phase != "terminal":
            raise GymtiContractError("GYMTI contract question phase is invalid")
        definitions[definition.id] = definition
        if expected_group == "core":
            core_questions[definition.id] = definition
        else:
            terminal_questions.append(definition)
    _validate_question_groups(
        rules=rules,
        core_questions=core_questions,
        terminal_questions=terminal_questions,
        gymti_type_ids=gymti_type_ids,
        coach_style_ids=coach_style_ids,
    )
    return GymtiContract(
        version=version,
        questionnaire_version=version,
        scoring_version=version,
        question_options={
            question_id: frozenset(definition.options)
            for question_id, definition in definitions.items()
        },
        question_phases={
            question_id: definition.phase for question_id, definition in definitions.items()
        },
        option_semantic_tags={
            (question_id, option_id): option.semantic_tags
            for question_id, definition in definitions.items()
            for option_id, option in definition.options.items()
        },
        option_gymti_scores={
            (question_id, option_id): option.gymti_scores
            for question_id, definition in definitions.items()
            for option_id, option in definition.options.items()
        },
        option_coach_style_scores={
            (question_id, option_id): option.coach_style_scores
            for question_id, definition in definitions.items()
            for option_id, option in definition.options.items()
        },
        option_hard_bans={
            (question_id, option_id): option.hard_bans
            for question_id, definition in definitions.items()
            for option_id, option in definition.options.items()
        },
        option_reason_codes={
            (question_id, option_id): option.reason_codes
            for question_id, definition in definitions.items()
            for option_id, option in definition.options.items()
        },
        option_is_no_match={
            (question_id, option_id): option.is_no_match
            for question_id, definition in definitions.items()
            for option_id, option in definition.options.items()
        },
        question_gymti_type_targets={
            question_id: definition.gymti_type_targets
            for question_id, definition in definitions.items()
        },
        question_coach_style_targets={
            question_id: definition.coach_style_targets
            for question_id, definition in definitions.items()
        },
        result_ids=gymti_type_ids,
        result_type_order=result_type_order,
        coach_style_ids=coach_style_ids,
        coach_style_order=coach_style_order,
        reason_codes=reason_codes,
        foundation_question_ids=rules.foundation_question_ids,
        adaptive_question_ids=rules.adaptive_question_ids,
        terminal_question_ids=tuple(question.id for question in terminal_questions),
        early_completion_answer_counts=rules.early_completion_after_answer_counts,
        early_completion_minimum_winner_score=rules.early_completion_minimum_winner_score,
        early_completion_minimum_lead=rules.early_completion_minimum_lead,
        early_completion_maximum_no_match_ratio_exclusive=(
            rules.early_completion_maximum_no_match_ratio_exclusive
        ),
        minimum_answers=rules.minimum_answers,
        maximum_answers=rules.maximum_answers,
        terminal_answer_count=rules.terminal_answer_count,
        terminal_score_boost=rules.terminal_score_boost,
    )


@dataclass(frozen=True, slots=True)
class GymtiQuestionChoiceInput:
    contract_version: str
    questionnaire_version: str
    scoring_version: str
    answered_question_option_ids: tuple[tuple[str, str], ...]
    candidate_question_ids: tuple[str, ...]
    semantic_tags: tuple[str, ...]
    gymti_scores: Mapping[str, int]
    coach_style_scores: Mapping[str, int]
    excluded_coach_style_ids: tuple[str, ...]
    no_match_count: int
    missing_gymti_type_ids: tuple[str, ...]
    missing_coach_style_ids: tuple[str, ...]
    candidate_probe_targets: tuple["GymtiCandidateProbeTargets", ...]


@dataclass(frozen=True, slots=True)
class GymtiCandidateProbeTargets:
    question_id: str
    gymti_type_ids: tuple[str, ...]
    coach_style_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GymtiNarrativeInput:
    contract_version: str
    questionnaire_version: str
    scoring_version: str
    formal_result_id: str
    secondary_result_id: str | None
    coach_style_id: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GymtiNextQuestion:
    question_id: str
    source: str
    model: str | None
    version: str


@dataclass(frozen=True, slots=True)
class GymtiNarrativeSnapshot:
    source: str
    model: str | None
    version: str
    generated_at: datetime
    text: str


class GymtiModel(Protocol):
    async def choose_question(self, payload: GymtiQuestionChoiceInput) -> str | None: ...

    async def narrate_result(self, payload: GymtiNarrativeInput) -> str | None: ...


class OpenAiStyleGymtiModel:
    """A tiny OpenAI-compatible client; it never logs prompts or provider text."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        http_client: httpx.AsyncClient,
        timeout_seconds: float,
        temperature: float,
    ) -> None:
        if not 0 <= temperature <= 2:
            raise ValueError("GYMTI model temperature must be between 0 and 2")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._http_client = http_client
        self._timeout_seconds = timeout_seconds
        self._temperature = temperature

    async def choose_question(self, payload: GymtiQuestionChoiceInput) -> str | None:
        content = await self._completion(
            system=(
                "Choose exactly one candidate_question_id. Return JSON only with the key "
                "question_id. You must not generate, paraphrase, or modify any question, option, "
                "answer, result, or style."
            ),
            payload={
                "contract_version": payload.contract_version,
                "questionnaire_version": payload.questionnaire_version,
                "scoring_version": payload.scoring_version,
                "answered_question_option_ids": payload.answered_question_option_ids,
                "candidate_question_ids": payload.candidate_question_ids,
                "semantic_tags": payload.semantic_tags,
                "gymti_scores": payload.gymti_scores,
                "coach_style_scores": payload.coach_style_scores,
                "excluded_coach_style_ids": payload.excluded_coach_style_ids,
                "no_match_count": payload.no_match_count,
                "missing_gymti_type_ids": payload.missing_gymti_type_ids,
                "missing_coach_style_ids": payload.missing_coach_style_ids,
                "candidate_probe_targets": tuple(
                    {
                        "question_id": target.question_id,
                        "gymti_type_ids": target.gymti_type_ids,
                        "coach_style_ids": target.coach_style_ids,
                    }
                    for target in payload.candidate_probe_targets
                ),
            },
        )
        if content is None:
            return None
        try:
            decoded = json.loads(content)
        except json.JSONDecodeError:
            return None
        if not isinstance(decoded, dict) or set(decoded) != {"question_id"}:
            return None
        return decoded["question_id"] if isinstance(decoded["question_id"], str) else None

    async def narrate_result(self, payload: GymtiNarrativeInput) -> str | None:
        content = await self._completion(
            system=(
                "Write a brief, warm GYMTI result narrative from the fixed structured facts. "
                "Return JSON only with the key text. Do not change, infer, rank, or add results, "
                "coach styles, reason codes, training advice, health claims, or user facts."
            ),
            payload={
                "contract_version": payload.contract_version,
                "questionnaire_version": payload.questionnaire_version,
                "scoring_version": payload.scoring_version,
                "formal_result_id": payload.formal_result_id,
                "secondary_result_id": payload.secondary_result_id,
                "coach_style_id": payload.coach_style_id,
                "reason_codes": payload.reason_codes,
            },
        )
        if content is None:
            return None
        try:
            decoded = json.loads(content)
        except json.JSONDecodeError:
            return None
        if not isinstance(decoded, dict) or set(decoded) != {"text"}:
            return None
        return decoded["text"] if isinstance(decoded["text"], str) else None

    async def _completion(self, *, system: str, payload: Mapping[str, object]) -> str | None:
        try:
            response = await self._http_client.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "temperature": self._temperature,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system},
                        {
                            "role": "user",
                            "content": json.dumps(
                                payload,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        },
                    ],
                },
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            decoded = response.json()
            choices = decoded.get("choices") if isinstance(decoded, dict) else None
            if not isinstance(choices, list) or not choices:
                return None
            first = choices[0]
            if not isinstance(first, dict):
                return None
            message = first.get("message")
            if not isinstance(message, dict):
                return None
            content = message.get("content")
            return content if isinstance(content, str) else None
        except (httpx.HTTPError, ValueError, TypeError):
            return None


def _normalize_narrative(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text or len(text) > 600:
        return None
    if text.startswith("{"):
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(decoded, dict) or set(decoded) != {"text"}:
            return None
        candidate = decoded["text"]
        if not isinstance(candidate, str):
            return None
        return _normalize_narrative(candidate)
    return text


@dataclass(frozen=True, slots=True)
class _ScoreState:
    gymti_scores: Mapping[str, int]
    coach_style_scores: Mapping[str, int]
    excluded_coach_style_ids: frozenset[str]
    no_match_count: int
    semantic_tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _UniqueWinner:
    id: str
    score: int
    runner_up_score: float
    lead: float


@dataclass(frozen=True, slots=True)
class GymtiRankedResult:
    gymti_type: str
    secondary_gymti_type: str | None
    recommended_coach_style_id: str


@dataclass(frozen=True, slots=True)
class GymtiEvaluation:
    gymti_scores: Mapping[str, int]
    coach_style_scores: Mapping[str, int]
    excluded_coach_style_ids: tuple[str, ...]
    early_completion: bool
    ranked_result: GymtiRankedResult | None
    formal_result: GymtiRankedResult | None
    formal_result_available: bool
    terminal_candidate_ids: tuple[str, ...]


class GymtiService:
    """Stateless service boundary; request content never enters server persistence or logs."""

    def __init__(
        self,
        *,
        contract: GymtiContract,
        model: GymtiModel | None,
        model_name: str | None,
        model_attempts: int = 2,
    ) -> None:
        if model_attempts < 1 or model_attempts > 3:
            raise ValueError("GYMTI model attempts must be between 1 and 3")
        self._contract = contract
        self._model = model
        self._model_name = model_name if model is not None else None
        self._model_attempts = model_attempts

    @property
    def model_available(self) -> bool:
        return self._model is not None

    def evaluate_answers(
        self,
        answered_question_option_ids: Sequence[tuple[str, str]],
    ) -> GymtiEvaluation:
        """Pure contract evaluation shared by next-question validation and golden tests."""

        answers = tuple(answered_question_option_ids)
        score_state = self._validate_answer_path(answers)
        return self._evaluate_valid_answers(answers, score_state)

    async def next_question(
        self,
        *,
        questionnaire_version: str,
        scoring_version: str,
        answered_question_option_ids: Sequence[tuple[str, str]],
        candidate_question_ids: Sequence[str],
        allow_model: bool = False,
    ) -> GymtiNextQuestion:
        payload = self._next_question_payload(
            questionnaire_version=questionnaire_version,
            scoring_version=scoring_version,
            answered_question_option_ids=answered_question_option_ids,
            candidate_question_ids=candidate_question_ids,
        )
        if allow_model and self._model is not None:
            for _ in range(self._model_attempts):
                try:
                    choice = await self._model.choose_question(payload)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    continue
                if choice in payload.candidate_question_ids:
                    return GymtiNextQuestion(
                        question_id=choice,
                        source="llm",
                        model=self._model_name,
                        version=self._contract.version,
                    )
        return GymtiNextQuestion(
            question_id=payload.candidate_question_ids[0],
            source="local_fallback",
            model=None,
            version=self._contract.version,
        )

    async def result_narrative(
        self,
        *,
        questionnaire_version: str,
        scoring_version: str,
        answered_question_option_ids: Sequence[tuple[str, str]],
        formal_result_id: str,
        secondary_result_id: str | None,
        coach_style_id: str,
        reason_codes: Sequence[str],
        allow_model: bool = False,
    ) -> GymtiNarrativeSnapshot:
        payload = self._narrative_payload(
            questionnaire_version=questionnaire_version,
            scoring_version=scoring_version,
            answered_question_option_ids=answered_question_option_ids,
            formal_result_id=formal_result_id,
            secondary_result_id=secondary_result_id,
            coach_style_id=coach_style_id,
            reason_codes=reason_codes,
        )
        if allow_model and self._model is not None:
            for _ in range(self._model_attempts):
                try:
                    narrative = _normalize_narrative(await self._model.narrate_result(payload))
                except asyncio.CancelledError:
                    raise
                except Exception:
                    continue
                if narrative is not None:
                    return GymtiNarrativeSnapshot(
                        source="llm",
                        model=self._model_name,
                        version=self._contract.version,
                        generated_at=datetime.now(UTC),
                        text=narrative,
                    )
        return GymtiNarrativeSnapshot(
            source="template",
            model=None,
            version=self._contract.version,
            generated_at=datetime.now(UTC),
            text=(
                "你的 GYMTI 结果已准备好。TrainPal 会围绕你当前的目标提供陪伴建议，"
                "你可以查看推荐后自行确认或修改。"
            ),
        )

    def validate_next_question_context(
        self,
        *,
        questionnaire_version: str,
        scoring_version: str,
        answered_question_option_ids: Sequence[tuple[str, str]],
        candidate_question_ids: Sequence[str],
    ) -> None:
        self._next_question_payload(
            questionnaire_version=questionnaire_version,
            scoring_version=scoring_version,
            answered_question_option_ids=answered_question_option_ids,
            candidate_question_ids=candidate_question_ids,
        )

    def validate_result_narrative_context(
        self,
        *,
        questionnaire_version: str,
        scoring_version: str,
        answered_question_option_ids: Sequence[tuple[str, str]],
        formal_result_id: str,
        secondary_result_id: str | None,
        coach_style_id: str,
        reason_codes: Sequence[str],
    ) -> None:
        self._narrative_payload(
            questionnaire_version=questionnaire_version,
            scoring_version=scoring_version,
            answered_question_option_ids=answered_question_option_ids,
            formal_result_id=formal_result_id,
            secondary_result_id=secondary_result_id,
            coach_style_id=coach_style_id,
            reason_codes=reason_codes,
        )

    def _evaluate_valid_answers(
        self,
        answers: tuple[tuple[str, str], ...],
        score_state: _ScoreState,
    ) -> GymtiEvaluation:
        early_completion = self._meets_early_completion(answers, score_state)
        ranked_result = self._rank_result(score_state)
        terminal_completion = (
            len(answers) == self._contract.terminal_answer_count
            and bool(answers)
            and self._contract.question_phases.get(answers[-1][0]) == "terminal"
        )
        formal_result_available = (
            (early_completion or terminal_completion) and ranked_result is not None
        )
        return GymtiEvaluation(
            gymti_scores=score_state.gymti_scores,
            coach_style_scores=score_state.coach_style_scores,
            excluded_coach_style_ids=tuple(
                style_id
                for style_id in self._contract.coach_style_order
                if style_id in score_state.excluded_coach_style_ids
            ),
            early_completion=early_completion,
            ranked_result=ranked_result,
            formal_result=ranked_result if formal_result_available else None,
            formal_result_available=formal_result_available,
            terminal_candidate_ids=self._legal_next_question_ids(answers),
        )

    def _rank_result(self, score_state: _ScoreState) -> GymtiRankedResult | None:
        gymti_winner = self._unique_winner(
            self._contract.result_type_order,
            score_state.gymti_scores,
        )
        coach_style_winner = self._unique_winner(
            self._contract.coach_style_order,
            score_state.coach_style_scores,
            score_state.excluded_coach_style_ids,
        )
        if gymti_winner is None or coach_style_winner is None:
            return None
        runner_up_ids = tuple(
            result_id
            for result_id in self._contract.result_type_order
            if result_id != gymti_winner.id
            and score_state.gymti_scores[result_id] == gymti_winner.runner_up_score
        )
        secondary_gymti_type = (
            runner_up_ids[0]
            if len(runner_up_ids) == 1
            and gymti_winner.runner_up_score > 0
            and gymti_winner.lead <= 1
            else None
        )
        return GymtiRankedResult(
            gymti_type=gymti_winner.id,
            secondary_gymti_type=secondary_gymti_type,
            recommended_coach_style_id=coach_style_winner.id,
        )

    def _next_question_payload(
        self,
        *,
        questionnaire_version: str,
        scoring_version: str,
        answered_question_option_ids: Sequence[tuple[str, str]],
        candidate_question_ids: Sequence[str],
    ) -> GymtiQuestionChoiceInput:
        self._validate_versions(questionnaire_version, scoring_version)
        answers = tuple(answered_question_option_ids)
        score_state = self._validate_answer_path(answers)
        candidates = self._validate_candidates(candidate_question_ids, answers)
        excluded_style_ids = tuple(
            style_id
            for style_id in self._contract.coach_style_order
            if style_id in score_state.excluded_coach_style_ids
        )
        missing_gymti_type_ids, missing_coach_style_ids = self._missing_signal_ids(score_state)
        return GymtiQuestionChoiceInput(
            contract_version=self._contract.version,
            questionnaire_version=questionnaire_version,
            scoring_version=scoring_version,
            answered_question_option_ids=answers,
            candidate_question_ids=candidates,
            semantic_tags=score_state.semantic_tags,
            gymti_scores=score_state.gymti_scores,
            coach_style_scores=score_state.coach_style_scores,
            excluded_coach_style_ids=excluded_style_ids,
            no_match_count=score_state.no_match_count,
            missing_gymti_type_ids=missing_gymti_type_ids,
            missing_coach_style_ids=missing_coach_style_ids,
            candidate_probe_targets=tuple(
                GymtiCandidateProbeTargets(
                    question_id=question_id,
                    gymti_type_ids=self._contract.question_gymti_type_targets[question_id],
                    coach_style_ids=self._contract.question_coach_style_targets[question_id],
                )
                for question_id in candidates
            ),
        )

    def _narrative_payload(
        self,
        *,
        questionnaire_version: str,
        scoring_version: str,
        answered_question_option_ids: Sequence[tuple[str, str]],
        formal_result_id: str,
        secondary_result_id: str | None,
        coach_style_id: str,
        reason_codes: Sequence[str],
    ) -> GymtiNarrativeInput:
        self._validate_versions(questionnaire_version, scoring_version)
        answers = tuple(answered_question_option_ids)
        score_state = self._validate_answer_path(answers)
        evaluation = self._evaluate_valid_answers(answers, score_state)
        result = evaluation.formal_result
        if result is None:
            raise GymtiContextError("GYMTI answers do not produce a formal result")
        expected_reason_codes = self._positive_reason_codes(answers, result)
        if (
            formal_result_id != result.gymti_type
            or secondary_result_id != result.secondary_gymti_type
            or coach_style_id != result.recommended_coach_style_id
            or tuple(reason_codes) != expected_reason_codes
        ):
            raise GymtiContextError("GYMTI result snapshot does not match the answers")
        return GymtiNarrativeInput(
            contract_version=self._contract.version,
            questionnaire_version=questionnaire_version,
            scoring_version=scoring_version,
            formal_result_id=result.gymti_type,
            secondary_result_id=result.secondary_gymti_type,
            coach_style_id=result.recommended_coach_style_id,
            reason_codes=expected_reason_codes,
        )

    def _positive_reason_codes(
        self,
        answers: Sequence[tuple[str, str]],
        result: GymtiRankedResult,
    ) -> tuple[str, ...]:
        weights: dict[str, tuple[int, int]] = {}
        for answer_index, key in enumerate(answers):
            weight = max(
                self._contract.option_gymti_scores[key].get(result.gymti_type, 0),
                self._contract.option_coach_style_scores[key].get(
                    result.recommended_coach_style_id,
                    0,
                ),
            )
            if weight <= 0:
                continue
            for reason_code in self._contract.option_reason_codes[key]:
                current = weights.get(reason_code)
                if current is None or weight > current[0]:
                    weights[reason_code] = (weight, answer_index)
        return tuple(
            reason_code
            for reason_code, _ in sorted(
                weights.items(),
                key=lambda item: (-item[1][0], item[1][1], item[0]),
            )[:3]
        )

    def _validate_versions(self, questionnaire_version: str, scoring_version: str) -> None:
        if (
            questionnaire_version != self._contract.questionnaire_version
            or scoring_version != self._contract.scoring_version
        ):
            raise GymtiContextError("GYMTI context does not match the current contract")

    def _validate_answer_path(self, answers: tuple[tuple[str, str], ...]) -> _ScoreState:
        if len(answers) > self._contract.maximum_answers:
            raise GymtiContextError("GYMTI answers exceed the question limit")
        seen_question_ids: set[str] = set()
        for question_id, option_id in answers:
            options = self._contract.question_options.get(question_id)
            if options is None or option_id not in options:
                raise GymtiContextError("GYMTI answer does not match the contract")
            if question_id in seen_question_ids:
                raise GymtiContextError("GYMTI answers include duplicate questions")
            seen_question_ids.add(question_id)

        prefix: list[tuple[str, str]] = []
        for answer in answers:
            if answer[0] not in self._legal_next_question_ids(tuple(prefix)):
                raise GymtiContextError("GYMTI answer sequence is invalid")
            prefix.append(answer)
        return self._score_answers(answers)

    def _score_answers(self, answers: Sequence[tuple[str, str]]) -> _ScoreState:
        gymti_scores = {result_id: 0 for result_id in self._contract.result_type_order}
        coach_style_scores = {style_id: 0 for style_id in self._contract.coach_style_order}
        excluded_coach_style_ids: set[str] = set()
        semantic_tags: list[str] = []
        no_match_count = 0
        for question_id, option_id in answers:
            key = (question_id, option_id)
            for result_id, increment in self._contract.option_gymti_scores[key].items():
                gymti_scores[result_id] += increment
            for style_id, increment in self._contract.option_coach_style_scores[key].items():
                coach_style_scores[style_id] += increment
            excluded_coach_style_ids.update(self._contract.option_hard_bans[key])
            if self._contract.option_is_no_match[key]:
                no_match_count += 1
            for tag in self._contract.option_semantic_tags[key]:
                if tag not in semantic_tags:
                    semantic_tags.append(tag)
        return _ScoreState(
            gymti_scores=gymti_scores,
            coach_style_scores=coach_style_scores,
            excluded_coach_style_ids=frozenset(excluded_coach_style_ids),
            no_match_count=no_match_count,
            semantic_tags=tuple(semantic_tags),
        )

    def _legal_next_question_ids(self, answers: tuple[tuple[str, str], ...]) -> tuple[str, ...]:
        score_state = self._score_answers(answers)
        if (
            len(answers) >= self._contract.maximum_answers
            or self._contains_terminal_answer(answers)
        ):
            return ()
        answered_question_ids = frozenset(question_id for question_id, _ in answers)
        if len(answers) < len(self._contract.foundation_question_ids):
            next_question_id = self._contract.foundation_question_ids[len(answers)]
            return () if next_question_id in answered_question_ids else (next_question_id,)
        if len(answers) >= self._contract.minimum_answers and self._meets_early_completion(
            answers, score_state
        ):
            return ()
        if len(answers) < self._contract.terminal_answer_count - 1:
            adaptive_ids = tuple(
                question_id
                for question_id in self._contract.adaptive_question_ids
                if question_id not in answered_question_ids
            )
            return self._canonical_adaptive_question_ids(adaptive_ids, score_state)
        if len(answers) == self._contract.terminal_answer_count - 1:
            return tuple(
                question_id
                for question_id in self._contract.terminal_question_ids
                if self._terminal_question_is_legal(answers, score_state, question_id)
            )
        return ()

    def _canonical_adaptive_question_ids(
        self,
        question_ids: tuple[str, ...],
        score_state: _ScoreState,
    ) -> tuple[str, ...]:
        if not question_ids:
            return ()
        tied_gymti_type_ids = self._top_tie_ids(
            self._contract.result_type_order,
            score_state.gymti_scores,
        )
        tied_coach_style_ids = self._top_tie_ids(
            self._contract.coach_style_order,
            score_state.coach_style_scores,
            score_state.excluded_coach_style_ids,
        )
        missing_gymti_type_ids, missing_coach_style_ids = self._missing_signal_ids(score_state)

        def coverage(
            question_id: str,
            gymti_type_ids: Sequence[str],
            coach_style_ids: Sequence[str],
        ) -> int:
            return len(
                set(self._contract.question_gymti_type_targets[question_id]).intersection(
                    gymti_type_ids
                )
            ) + len(
                set(self._contract.question_coach_style_targets[question_id]).intersection(
                    coach_style_ids
                )
            )

        tie_coverage = {
            question_id: coverage(
                question_id,
                tied_gymti_type_ids,
                tied_coach_style_ids,
            )
            for question_id in question_ids
        }
        maximum_tie_coverage = max(tie_coverage.values())
        tie_layer = (
            tuple(
                question_id
                for question_id in question_ids
                if tie_coverage[question_id] == maximum_tie_coverage
            )
            if maximum_tie_coverage > 0
            else question_ids
        )
        missing_coverage = {
            question_id: coverage(
                question_id,
                missing_gymti_type_ids,
                missing_coach_style_ids,
            )
            for question_id in tie_layer
        }
        maximum_missing_coverage = max(missing_coverage.values())
        if maximum_missing_coverage <= 0:
            return tie_layer
        return tuple(
            question_id
            for question_id in tie_layer
            if missing_coverage[question_id] == maximum_missing_coverage
        )

    def _missing_signal_ids(
        self,
        score_state: _ScoreState,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return (
            tuple(
                result_id
                for result_id in self._contract.result_type_order
                if score_state.gymti_scores[result_id] <= 0
            ),
            tuple(
                style_id
                for style_id in self._contract.coach_style_order
                if style_id not in score_state.excluded_coach_style_ids
                and score_state.coach_style_scores[style_id] <= 0
            ),
        )

    @staticmethod
    def _top_tie_ids(
        ids: Sequence[str],
        scores: Mapping[str, int],
        excluded_ids: frozenset[str] = frozenset(),
    ) -> tuple[str, ...]:
        eligible_ids = tuple(identifier for identifier in ids if identifier not in excluded_ids)
        if not eligible_ids:
            return ()
        maximum_score = max(scores[identifier] for identifier in eligible_ids)
        top_ids = tuple(
            identifier for identifier in eligible_ids if scores[identifier] == maximum_score
        )
        return top_ids if len(top_ids) > 1 else ()

    def _meets_early_completion(
        self,
        answers: Sequence[tuple[str, str]],
        score_state: _ScoreState,
    ) -> bool:
        if len(answers) not in self._contract.early_completion_answer_counts:
            return False
        if self._contains_terminal_answer(answers):
            return False
        if (
            score_state.no_match_count
            >= len(answers) * self._contract.early_completion_maximum_no_match_ratio_exclusive
        ):
            return False
        gymti_winner = self._unique_winner(
            self._contract.result_type_order,
            score_state.gymti_scores,
        )
        coach_style_winner = self._unique_winner(
            self._contract.coach_style_order,
            score_state.coach_style_scores,
            score_state.excluded_coach_style_ids,
        )
        if gymti_winner is None or coach_style_winner is None:
            return False
        return (
            gymti_winner.score >= self._contract.early_completion_minimum_winner_score
            and gymti_winner.lead >= self._contract.early_completion_minimum_lead
            and coach_style_winner.score >= self._contract.early_completion_minimum_winner_score
            and coach_style_winner.lead >= self._contract.early_completion_minimum_lead
        )

    def _terminal_question_is_legal(
        self,
        answers: tuple[tuple[str, str], ...],
        score_state: _ScoreState,
        question_id: str,
    ) -> bool:
        if (
            self._contract.question_phases.get(question_id) != "terminal"
            or len(answers) != self._contract.terminal_answer_count - 1
            or self._contains_terminal_answer(answers)
        ):
            return False
        target_styles = {
            style_id
            for option_id in self._contract.question_options[question_id]
            for style_id, score in self._contract.option_coach_style_scores[
                (question_id, option_id)
            ].items()
            if score >= self._contract.terminal_score_boost
        }
        if target_styles.intersection(score_state.excluded_coach_style_ids):
            return False
        for option_id in self._contract.question_options[question_id]:
            terminal_state = self._score_answers((*answers, (question_id, option_id)))
            ranked_result = self._rank_result(terminal_state)
            if (
                ranked_result is None
                or ranked_result.recommended_coach_style_id
                in score_state.excluded_coach_style_ids
            ):
                return False
        return True

    def _contains_terminal_answer(self, answers: Sequence[tuple[str, str]]) -> bool:
        return any(
            self._contract.question_phases.get(question_id) == "terminal"
            for question_id, _ in answers
        )

    @staticmethod
    def _unique_winner(
        ids: Sequence[str],
        scores: Mapping[str, int],
        excluded_ids: frozenset[str] = frozenset(),
    ) -> _UniqueWinner | None:
        eligible_ids = tuple(identifier for identifier in ids if identifier not in excluded_ids)
        if not eligible_ids:
            return None
        winner_score = max(scores[identifier] for identifier in eligible_ids)
        winner_ids = tuple(
            identifier for identifier in eligible_ids if scores[identifier] == winner_score
        )
        if len(winner_ids) != 1:
            return None
        winner_id = winner_ids[0]
        runner_up_score = max(
            (scores[identifier] for identifier in eligible_ids if identifier != winner_id),
            default=float("-inf"),
        )
        return _UniqueWinner(
            id=winner_id,
            score=winner_score,
            runner_up_score=runner_up_score,
            lead=winner_score - runner_up_score,
        )

    def _validate_candidates(
        self,
        candidates: Sequence[str],
        answers: tuple[tuple[str, str], ...],
    ) -> tuple[str, ...]:
        candidate_ids = tuple(candidates)
        legal_candidate_ids = self._legal_next_question_ids(answers)
        if not legal_candidate_ids or candidate_ids != legal_candidate_ids:
            raise GymtiContextError("GYMTI candidate questions are invalid")
        return legal_candidate_ids

    @staticmethod
    def _validate_catalog_id(value: str, catalog: frozenset[str], label: str) -> str:
        try:
            stable = _stable_id(value)
        except GymtiContractError as error:
            raise GymtiContextError(f"GYMTI {label} is invalid") from error
        if stable not in catalog:
            raise GymtiContextError(f"GYMTI {label} is invalid")
        return stable
