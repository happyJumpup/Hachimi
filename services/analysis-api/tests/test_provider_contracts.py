import base64
import json
from pathlib import Path

import httpx
import pytest
import respx

from hakimi_analysis.models import Segment
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.providers.asr import VolcAsrClient
from hakimi_analysis.providers.base import ProviderSchemaError


@pytest.mark.asyncio
@respx.mock
async def test_asr_maps_word_timestamps_to_the_source_video_clock(tmp_path: Path) -> None:
    audio = tmp_path / "window.wav"
    audio.write_bytes(b"wav-bytes")
    route = respx.post("https://speech.example/recognize").mock(
        return_value=httpx.Response(
            200,
            headers={
                "X-Api-Status-Code": "20000000",
                "X-Tt-Logid": "speech-log-id",
            },
            json={
                "result": {
                    "text": "做拖拽弯举",
                    "utterances": [
                        {
                            "text": "做拖拽弯举",
                            "start_time": 500,
                            "end_time": 1700,
                            "words": [
                                {"text": "拖拽弯举", "start_time": 700, "end_time": 1600}
                            ],
                        }
                    ],
                }
            },
        )
    )
    async with httpx.AsyncClient() as http_client:
        client = VolcAsrClient(
            api_key="test-asr-key",
            resource_id="volc.bigasr.auc_turbo",
            url="https://speech.example/recognize",
            http_client=http_client,
        )
        transcript = await client.recognize(
            audio_path=audio,
            window_start_seconds=15,
            request_id="run-123",
        )

    assert transcript.provider_request_id == "speech-log-id"
    assert transcript.utterances[0].start_seconds == 15.5
    assert transcript.utterances[0].words[0].end_seconds == 16.6
    request = route.calls[0].request
    assert request.headers["X-Api-Key"] == "test-asr-key"
    payload = json.loads(request.content)
    assert payload["user"]["uid"] == "hachimi-run-123"
    assert payload["audio"]["data"] == base64.b64encode(b"wav-bytes").decode("ascii")


@pytest.mark.asyncio
@respx.mock
async def test_ark_video_file_is_deleted_after_structured_visual_result(tmp_path: Path) -> None:
    video = tmp_path / "window.mp4"
    video.write_bytes(b"video-bytes")
    upload = respx.post("https://ark.example/api/v3/files").mock(
        return_value=httpx.Response(200, json={"id": "file-123", "status": "processed"})
    )
    response = respx.post("https://ark.example/api/v3/responses").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "resp-123",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "segments": [
                                            {
                                                "action_name": "Drag Curl",
                                                "start_seconds": 41,
                                                "end_seconds": 51,
                                                "visual_cue": "哑铃沿躯干后拉",
                                            }
                                        ]
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        ],
                    }
                ],
            },
        )
    )
    delete = respx.delete("https://ark.example/api/v3/files/file-123").mock(
        return_value=httpx.Response(200, json={"id": "file-123", "deleted": True})
    )

    async with httpx.AsyncClient() as http_client:
        client = ArkResponsesClient(
            api_key="test-ark-key",
            model_id="doubao-test",
            base_url="https://ark.example/api/v3",
            http_client=http_client,
        )
        result = await client.locate_visual(
            video_path=video,
            window=Segment(start_seconds=15, end_seconds=54),
            trigger_seconds=45,
            instructions="visual skill contract",
        )

    assert result.segments[0].action_name == "Drag Curl"
    assert upload.called
    assert response.called
    assert delete.called
    response_payload = json.loads(response.calls[0].request.content)
    assert response_payload["store"] is False
    assert response_payload["input"][0]["content"][0]["type"] == "input_video"
    assert response_payload["input"][0]["content"][0]["file_id"] == "file-123"


@pytest.mark.asyncio
@respx.mock
async def test_ark_video_file_is_deleted_when_structured_result_is_invalid(tmp_path: Path) -> None:
    video = tmp_path / "window.mp4"
    video.write_bytes(b"video-bytes")
    respx.post("https://ark.example/api/v3/files").mock(
        return_value=httpx.Response(200, json={"id": "file-invalid", "status": "processed"})
    )
    respx.post("https://ark.example/api/v3/responses").mock(
        return_value=httpx.Response(
            200,
            json={"id": "resp-invalid", "output_text": '{"segments": [{"action_name": 7}]}'},
        )
    )
    delete = respx.delete("https://ark.example/api/v3/files/file-invalid").mock(
        return_value=httpx.Response(200, json={"id": "file-invalid", "deleted": True})
    )

    async with httpx.AsyncClient() as http_client:
        client = ArkResponsesClient(
            api_key="test-ark-key",
            model_id="doubao-test",
            base_url="https://ark.example/api/v3",
            http_client=http_client,
        )
        with pytest.raises(ProviderSchemaError):
            await client.locate_visual(
                video_path=video,
                window=Segment(start_seconds=15, end_seconds=54),
                trigger_seconds=45,
                instructions="visual skill contract",
            )

    assert delete.called


@pytest.mark.asyncio
@respx.mock
async def test_ark_speech_result_is_validated_as_json() -> None:
    respx.post("https://ark.example/api/v3/responses").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "resp-speech",
                "output_text": json.dumps(
                    {
                        "signals": [
                            {
                                "action_name": "交叉弯举",
                                "sets": 3,
                                "reps": 10,
                                "duration_seconds": None,
                                "rest_seconds": 60,
                                "start_seconds": 20,
                                "end_seconds": 28,
                                "evidence_text": "做三组交叉弯举",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
            },
        )
    )
    async with httpx.AsyncClient() as http_client:
        client = ArkResponsesClient(
            api_key="test-ark-key",
            model_id="doubao-test",
            base_url="https://ark.example/api/v3",
            http_client=http_client,
        )
        result = await client.understand_speech(
            transcript={
                "text": "做三组交叉弯举",
                "utterances": [
                    {"text": "做三组交叉弯举", "start_seconds": 20, "end_seconds": 28}
                ],
            },
            trigger_seconds=24,
            window=Segment(start_seconds=15, end_seconds=35),
            instructions="speech skill contract",
        )

    assert result.signals[0].sets == 3
    assert result.signals[0].reps == 10
