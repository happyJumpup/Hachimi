from hakimi_analysis.fusion import fuse_candidates
from hakimi_analysis.models import SpeechSignal, VisualSegment


def test_overlapping_speech_and_visual_evidence_become_one_candidate() -> None:
    candidates = fuse_candidates(
        source_id="legacy-arm-workout",
        speech_signals=[
            SpeechSignal(
                action_name="拖拽弯举",
                sets=3,
                reps=10,
                duration_seconds=None,
                rest_seconds=None,
                start_seconds=42,
                end_seconds=49,
                evidence_text="接下来做 drag curl",
            )
        ],
        visual_segments=[
            VisualSegment(
                action_name="Drag Curl",
                start_seconds=41,
                end_seconds=51,
                visual_cue="双臂沿躯干向后拖动哑铃",
            )
        ],
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.name == "拖拽弯举"
    assert candidate.segment is not None
    assert candidate.segment.start_seconds == 41
    assert candidate.segment.end_seconds == 51
    assert candidate.parameters.sets == 3
    assert candidate.parameters.reps == 10
    assert [item.type for item in candidate.evidence] == ["speech", "visual"]
    assert candidate.needs_confirmation is False


def test_single_branch_evidence_is_returned_as_needing_confirmation() -> None:
    candidates = fuse_candidates(
        source_id="source-a",
        speech_signals=[
            SpeechSignal(
                action_name="交叉弯举",
                sets=None,
                reps=None,
                duration_seconds=None,
                rest_seconds=None,
                start_seconds=8,
                end_seconds=14,
                evidence_text="交叉弯举",
            )
        ],
        visual_segments=[],
    )

    assert len(candidates) == 1
    assert candidates[0].name == "交叉弯举"
    assert candidates[0].needs_confirmation is True
    assert [item.type for item in candidates[0].evidence] == ["speech"]


def test_overlapping_different_actions_remain_separate_candidates() -> None:
    candidates = fuse_candidates(
        source_id="source-a",
        speech_signals=[
            SpeechSignal(
                action_name="锤式弯举",
                sets=3,
                reps=10,
                duration_seconds=None,
                rest_seconds=60,
                start_seconds=42,
                end_seconds=49,
                evidence_text="做锤式弯举",
            )
        ],
        visual_segments=[
            VisualSegment(
                action_name="Drag Curl",
                start_seconds=41,
                end_seconds=51,
                visual_cue="哑铃沿躯干向后拖动",
            )
        ],
    )

    assert len(candidates) == 2
    assert [candidate.name for candidate in candidates] == ["Drag Curl", "锤式弯举"]
    assert all(candidate.needs_confirmation for candidate in candidates)
    assert all(len(candidate.evidence) == 1 for candidate in candidates)


def test_no_evidence_produces_no_candidates() -> None:
    assert fuse_candidates(source_id="source-a", speech_signals=[], visual_segments=[]) == []
