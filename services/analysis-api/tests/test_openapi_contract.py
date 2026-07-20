from hakimi_analysis.app import create_app
from hakimi_analysis.bootstrap import UnconfiguredPipeline


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
