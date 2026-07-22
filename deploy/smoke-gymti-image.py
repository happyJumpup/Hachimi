#!/usr/bin/env python3

from __future__ import annotations

import json
import urllib.request

from hakimi_analysis.gymti import GymtiService, load_gymti_contract
from hakimi_analysis.settings import Settings


def post(path: str, payload: dict[str, object]) -> dict[str, object]:
    request = urllib.request.Request(
        f"http://127.0.0.1:8000{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Origin": "http://127.0.0.1:8000",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(f"GYMTI image smoke returned HTTP {response.status}")
        if response.headers.get("Cache-Control") != "no-store":
            raise RuntimeError("GYMTI image smoke response must not be cached")
        decoded = json.load(response)
    if not isinstance(decoded, dict):
        raise RuntimeError("GYMTI image smoke returned an invalid response")
    return decoded


def main() -> int:
    settings = Settings(_env_file=None)
    contract = load_gymti_contract(settings.gymti_contract_path)
    next_question = post(
        "/api/v1/gymti/next-question",
        {
            "questionnaire_version": contract.questionnaire_version,
            "scoring_version": contract.scoring_version,
            "answered": [],
            "candidate_question_ids": ["q01_energy_after_work"],
        },
    )
    if next_question != {
        "question_id": "q01_energy_after_work",
        "source": "local_fallback",
        "model": None,
        "version": contract.version,
    }:
        raise RuntimeError("anonymous GYMTI next-question did not use the local fallback")

    answers = [
        ("q01_energy_after_work", "q01_c_music"),
        ("q02_gym_attraction", "q02_b_cardio"),
        ("q03_data_report", "q03_b_skip_math"),
        ("q04_desktop_cat", "q04_a_flag"),
        ("q05_reminder_aversion", "q05_a_vague"),
    ]
    service = GymtiService(contract=contract, model=None, model_name=None)
    evaluation = service.evaluate_answers(answers)
    result = evaluation.formal_result
    if result is None:
        raise RuntimeError("GYMTI smoke vector did not produce a formal result")
    reason_codes = service._positive_reason_codes(answers, result)
    narrative = post(
        "/api/v1/gymti/result-narrative",
        {
            "questionnaire_version": contract.questionnaire_version,
            "scoring_version": contract.scoring_version,
            "answered": [
                {"question_id": question_id, "option_id": option_id}
                for question_id, option_id in answers
            ],
            "formal_result_id": result.gymti_type,
            "secondary_result_id": result.secondary_gymti_type,
            "coach_style_id": result.recommended_coach_style_id,
            "reason_codes": list(reason_codes),
        },
    )
    if narrative.get("source") != "template" or narrative.get("model") is not None:
        raise RuntimeError("anonymous GYMTI narrative did not use the local template")
    if not narrative.get("text"):
        raise RuntimeError("anonymous GYMTI narrative was empty")
    print("anonymous GYMTI image smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
