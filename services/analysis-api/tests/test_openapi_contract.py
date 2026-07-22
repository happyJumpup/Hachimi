import pytest
from pydantic import ValidationError

from hakimi_analysis.app import create_app
from hakimi_analysis.bootstrap import UnconfiguredPipeline
from hakimi_analysis.models import AnalysisCandidate


def test_analysis_candidate_requires_an_absolute_segment() -> None:
    with pytest.raises(ValidationError):
        AnalysisCandidate.model_validate(
            {
                "id": "candidate-1",
                "name": "拖拽弯举",
                "source_id": "source-1",
                "segment": None,
                "parameters": {},
                "evidence": [],
                "segment_role": "unknown",
                "needs_confirmation": True,
            }
        )


def test_analysis_candidate_rejects_the_legacy_public_segment_role() -> None:
    with pytest.raises(ValidationError):
        AnalysisCandidate.model_validate(
            {
                "id": "candidate-1",
                "name": "拖拽弯举",
                "source_id": "source-1",
                "segment": {"start_seconds": 1, "end_seconds": 2},
                "parameters": {},
                "evidence": [],
                "segment_role": "unknown",
                "needs_confirmation": True,
            }
        )


def test_openapi_documents_actual_readiness_and_analysis_errors() -> None:
    schema = create_app(pipeline=UnconfiguredPipeline()).openapi()
    paths = schema["paths"]

    ready_responses = paths["/api/v1/ready"]["get"]["responses"]
    assert ready_responses["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ReadyResponse"
    }
    assert ready_responses["503"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/NotReadyResponse"
    }

    create_responses = paths["/api/v1/analysis-runs"]["post"]["responses"]
    assert {"400", "403", "404", "429", "503"} <= set(create_responses)
    for status_code in ("400", "403", "404", "429", "503"):
        assert create_responses[status_code]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ApiErrorResponse"
        }
    assert create_responses["429"]["headers"]["Retry-After"]["schema"] == {
        "type": "string"
    }

    get_responses = paths["/api/v1/analysis-runs/{run_id}"]["get"]["responses"]
    delete_responses = paths["/api/v1/analysis-runs/{run_id}"]["delete"]["responses"]
    event_responses = paths["/api/v1/analysis-runs/{run_id}/events"]["get"]["responses"]
    assert get_responses["404"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ApiErrorResponse"
    }
    for status_code in ("403", "404"):
        assert delete_responses[status_code]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ApiErrorResponse"
        }
    assert event_responses["404"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ApiErrorResponse"
    }
    assert event_responses["200"]["content"] == {
        "text/event-stream": {"schema": {"type": "string"}}
    }

    capabilities_response = paths["/api/v1/capabilities"]["get"]["responses"]["200"]
    assert capabilities_response["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/CapabilitiesView"
    }

    local_create = paths["/api/v1/analysis-runs/local"]["post"]
    assert set(local_create["requestBody"]["content"]) == {"multipart/form-data"}
    assert local_create["responses"]["202"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/AnalysisRunView"
    }
    for status_code in ("400", "403", "404", "413", "415", "422", "429", "503"):
        assert local_create["responses"][status_code]["content"]["application/json"][
            "schema"
        ] == {"$ref": "#/components/schemas/ApiErrorResponse"}

    candidate_schema = schema["components"]["schemas"]["AnalysisCandidate"]
    assert "segment_role" not in candidate_schema["properties"]
    assert "SegmentRole" not in schema["components"]["schemas"]
    run_schema = schema["components"]["schemas"]["AnalysisRunView"]
    progress_description = run_schema["properties"]["discovered_candidate_count"][
        "description"
    ]
    assert "completed evidence" in progress_description
    assert "exact fused candidate count" in progress_description
