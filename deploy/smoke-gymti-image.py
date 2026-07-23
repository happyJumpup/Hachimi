#!/usr/bin/env python3

from __future__ import annotations

import asyncio

import httpx

from hakimi_analysis.access import AccessManager
from hakimi_analysis.app import create_app
from hakimi_analysis.bootstrap import build_gymti_service
from hakimi_analysis.gymti import GymtiService, load_gymti_contract
from hakimi_analysis.readiness import StaticReadiness
from hakimi_analysis.settings import Settings


async def post(
    client: httpx.AsyncClient,
    path: str,
    payload: dict[str, object],
) -> dict[str, object]:
    response = await client.post(path, json=payload)
    if response.status_code != 200:
        raise RuntimeError(f"GYMTI image smoke returned HTTP {response.status_code}")
    if response.headers.get("Cache-Control") != "no-store":
        raise RuntimeError("GYMTI image smoke response must not be cached")
    decoded = response.json()
    if not isinstance(decoded, dict):
        raise RuntimeError("GYMTI image smoke returned an invalid response")
    return decoded


async def run_smoke() -> None:
    settings = Settings(_env_file=None)
    provider_client = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.gymti_llm_timeout_seconds, connect=2),
        follow_redirects=False,
    )
    gymti_service = build_gymti_service(settings, provider_client)
    if gymti_service is None or not gymti_service.model_available:
        await provider_client.aclose()
        raise RuntimeError("GYMTI image smoke provider is unavailable")
    app = create_app(
        access=AccessManager(
            cookie_secret="image-audit-cookie-secret-at-least-32-bytes",
            judge_access_code="image-audit-judge-code",
            public_concurrency=1,
            public_attempt_limit=100,
        ),
        app_env="test",
        readiness=StaticReadiness(None),
        local_upload_enabled=False,
        gymti_service=gymti_service,
        gymti_llm_enabled=True,
        gymti_llm_concurrency=3,
    )
    transport = httpx.ASGITransport(app=app)
    async with provider_client, httpx.AsyncClient(
        transport=transport,
        base_url="http://image-audit.invalid",
        headers={"Origin": "http://image-audit.invalid"},
    ) as client:
        contract = load_gymti_contract(settings.gymti_contract_path)
        next_question = await post(
            client,
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
            "source": "llm",
            "model": "image-audit-trap-model",
            "version": contract.version,
        }:
            raise RuntimeError("public GYMTI next-question did not use the provider")

        answers = [
            ("q01_energy_after_work", "q01_c_music"),
            ("q02_gym_attraction", "q02_b_cardio"),
            ("q03_data_report", "q03_b_skip_math"),
            ("q04_desktop_cat", "q04_a_flag"),
            ("q05_reminder_aversion", "q05_a_vague"),
        ]
        local_service = GymtiService(contract=contract, model=None, model_name=None)
        evaluation = local_service.evaluate_answers(answers)
        result = evaluation.formal_result
        if result is None:
            raise RuntimeError("GYMTI smoke vector did not produce a formal result")
        reason_codes = local_service._positive_reason_codes(answers, result)
        narrative = await post(
            client,
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
        if (
            narrative.get("source") != "llm"
            or narrative.get("model") != "image-audit-trap-model"
            or not narrative.get("text")
        ):
            raise RuntimeError("public GYMTI narrative did not use the provider")

        for failure_kind in ("invalid", "exception"):
            fallback = await post(
                client,
                "/api/v1/gymti/next-question",
                {
                    "questionnaire_version": contract.questionnaire_version,
                    "scoring_version": contract.scoring_version,
                    "answered": [],
                    "candidate_question_ids": ["q01_energy_after_work"],
                },
            )
            if fallback != {
                "question_id": "q01_energy_after_work",
                "source": "local_fallback",
                "model": None,
                "version": contract.version,
            }:
                raise RuntimeError(f"GYMTI {failure_kind} response did not fall back")


def main() -> int:
    asyncio.run(run_smoke())
    print("public GYMTI image smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
