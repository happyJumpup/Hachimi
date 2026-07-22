from hakimi_analysis.fusion import fuse_candidates
from hakimi_analysis.models import (
    EvidenceType,
    Segment,
    SegmentRole,
    SpeechSignal,
    VisualSegment,
)


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
                segment_role=SegmentRole.FOLLOW_ALONG,
            )
        ],
        visual_segments=[
            VisualSegment(
                action_name="Drag Curl",
                start_seconds=41,
                end_seconds=51,
                visual_cue="双臂沿躯干向后拖动哑铃",
                segment_role=SegmentRole.FOLLOW_ALONG,
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


def test_same_action_within_one_sampling_step_is_fused() -> None:
    candidates = fuse_candidates(
        source_id="source-a",
        speech_signals=[
            SpeechSignal(
                action_name="Drag Curl",
                sets=None,
                reps=10,
                duration_seconds=None,
                rest_seconds=None,
                start_seconds=42,
                end_seconds=46,
                evidence_text="drag curl",
            )
        ],
        visual_segments=[
            VisualSegment(
                action_name="Drag Curl",
                start_seconds=48,
                end_seconds=54,
                visual_cue="dumbbells move behind the torso",
            )
        ],
    )

    assert len(candidates) == 1
    assert [item.type for item in candidates[0].evidence] == ["speech", "visual"]
    assert candidates[0].segment is not None
    assert candidates[0].segment.start_seconds == 42
    assert candidates[0].segment.end_seconds == 54


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


def test_overlapping_different_actions_form_one_candidate_needing_confirmation() -> None:
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

    assert len(candidates) == 1
    assert candidates[0].name == "锤式弯举"
    assert candidates[0].needs_confirmation is True
    assert len(candidates[0].evidence) == 2


def test_no_evidence_produces_no_candidates() -> None:
    assert fuse_candidates(source_id="source-a", speech_signals=[], visual_segments=[]) == []


def test_overlapping_duplicate_speech_signals_become_one_deterministic_candidate() -> None:
    sparse = SpeechSignal(
        action_name="Squat",
        sets=None,
        reps=None,
        duration_seconds=None,
        rest_seconds=None,
        start_seconds=10,
        end_seconds=18,
        evidence_text="squat",
    )
    parameterized = SpeechSignal(
        action_name="squat",
        sets=3,
        reps=10,
        duration_seconds=None,
        rest_seconds=30,
        start_seconds=12,
        end_seconds=20,
        evidence_text="three sets of ten squats",
    )

    forward = fuse_candidates(
        source_id="source-a",
        speech_signals=[sparse, parameterized],
        visual_segments=[],
    )
    reversed_order = fuse_candidates(
        source_id="source-a",
        speech_signals=[parameterized, sparse],
        visual_segments=[],
    )

    assert len(forward) == 1
    assert forward == reversed_order
    assert forward[0].segment == Segment(start_seconds=10, end_seconds=20)
    assert forward[0].parameters.sets == 3
    assert forward[0].parameters.reps == 10


def test_conflicting_internal_segment_roles_require_confirmation_without_being_exposed() -> None:
    candidates = fuse_candidates(
        source_id="source-a",
        speech_signals=[
            SpeechSignal(
                action_name="深蹲",
                sets=None,
                reps=None,
                duration_seconds=None,
                rest_seconds=None,
                start_seconds=5,
                end_seconds=10,
                evidence_text="跟我做深蹲",
                segment_role=SegmentRole.FOLLOW_ALONG,
            )
        ],
        visual_segments=[
            VisualSegment(
                action_name="深蹲",
                start_seconds=5,
                end_seconds=10,
                visual_cue="讲解站姿并示范一次",
                segment_role=SegmentRole.TEACHING_DEMO,
            )
        ],
    )

    assert len(candidates) == 1
    assert candidates[0].needs_confirmation is True
    assert "segment_role" not in candidates[0].model_dump()


def test_teaching_demo_length_does_not_become_a_training_duration() -> None:
    candidates = fuse_candidates(
        source_id="source-a",
        speech_signals=[
            SpeechSignal(
                action_name="深蹲",
                sets=None,
                reps=None,
                duration_seconds=None,
                rest_seconds=None,
                start_seconds=5,
                end_seconds=25,
                evidence_text="下面示范深蹲动作",
                segment_role=SegmentRole.TEACHING_DEMO,
            )
        ],
        visual_segments=[
            VisualSegment(
                action_name="深蹲",
                start_seconds=5,
                end_seconds=25,
                visual_cue="连续示范深蹲",
                segment_role=SegmentRole.TEACHING_DEMO,
            )
        ],
    )

    assert len(candidates) == 1
    assert candidates[0].parameters.mode is None
    assert candidates[0].parameters.duration_seconds is None
    assert candidates[0].needs_confirmation is False


def test_speech_only_internal_segment_role_still_needs_confirmation() -> None:
    candidates = fuse_candidates(
        source_id="source-a",
        speech_signals=[
            SpeechSignal(
                action_name="深蹲",
                sets=3,
                reps=10,
                duration_seconds=None,
                rest_seconds=None,
                start_seconds=5,
                end_seconds=10,
                evidence_text="跟我一起做深蹲",
                segment_role=SegmentRole.FOLLOW_ALONG,
            )
        ],
        visual_segments=[],
    )

    assert candidates[0].needs_confirmation is True
    assert "segment_role" not in candidates[0].model_dump()


def test_visual_only_internal_segment_role_still_needs_confirmation() -> None:
    candidates = fuse_candidates(
        source_id="source-a",
        speech_signals=[],
        visual_segments=[
            VisualSegment(
                action_name="深蹲",
                start_seconds=5,
                end_seconds=10,
                visual_cue="面对镜头逐步讲解动作",
                segment_role=SegmentRole.TEACHING_DEMO,
            )
        ],
    )

    assert candidates[0].needs_confirmation is True
    assert "segment_role" not in candidates[0].model_dump()


def test_overlapping_conflicting_names_form_one_candidate_for_confirmation() -> None:
    candidates = fuse_candidates(
        source_id="source-a",
        speech_signals=[
            SpeechSignal(
                action_name="罗马尼亚硬拉",
                start_seconds=10,
                end_seconds=20,
                evidence_text="下面做罗马尼亚硬拉",
            )
        ],
        visual_segments=[
            VisualSegment(
                action_name="直腿硬拉",
                start_seconds=11,
                end_seconds=19,
                visual_cue="髋部后移并下放哑铃",
            )
        ],
    )

    assert len(candidates) == 1
    assert candidates[0].name == "罗马尼亚硬拉"
    assert candidates[0].needs_confirmation is True
    assert {evidence.type for evidence in candidates[0].evidence} == {
        EvidenceType.SPEECH,
        EvidenceType.VISUAL,
    }


def test_separate_teaching_demos_of_same_action_keep_one_best_reference() -> None:
    candidates = fuse_candidates(
        source_id="source-a",
        speech_signals=[
            SpeechSignal(
                action_name="深蹲",
                start_seconds=5,
                end_seconds=10,
                evidence_text="示范深蹲",
                segment_role=SegmentRole.TEACHING_DEMO,
            ),
            SpeechSignal(
                action_name="深蹲",
                sets=4,
                reps=12,
                start_seconds=30,
                end_seconds=40,
                evidence_text="深蹲四组每组十二次",
                segment_role=SegmentRole.TEACHING_DEMO,
            ),
        ],
        visual_segments=[],
    )

    assert len(candidates) == 1
    assert candidates[0].segment == Segment(start_seconds=30, end_seconds=40)
    assert candidates[0].parameters.sets == 4
    assert candidates[0].parameters.reps == 12


def test_separate_teaching_speech_merges_complementary_parameters_and_evidence() -> None:
    candidates = fuse_candidates(
        source_id="source-a",
        speech_signals=[
            SpeechSignal(
                action_name="杠铃卧推",
                sets=4,
                start_seconds=10,
                end_seconds=18,
                evidence_text="杠铃卧推做四组",
                segment_role=SegmentRole.TEACHING_DEMO,
            ),
            SpeechSignal(
                action_name="杠铃卧推",
                reps=10,
                start_seconds=40,
                end_seconds=48,
                evidence_text="每组做十次",
                segment_role=SegmentRole.TEACHING_DEMO,
            ),
        ],
        visual_segments=[],
    )

    assert len(candidates) == 1
    assert candidates[0].parameters.sets == 4
    assert candidates[0].parameters.reps == 10
    assert {
        (evidence.start_seconds, evidence.end_seconds)
        for evidence in candidates[0].evidence
    } == {(10, 18), (40, 48)}


def test_generic_and_specific_compatible_names_form_one_specific_candidate() -> None:
    candidates = fuse_candidates(
        source_id="source-a",
        speech_signals=[
            SpeechSignal(
                action_name="卧推",
                start_seconds=10,
                end_seconds=20,
                evidence_text="开始卧推",
                segment_role=SegmentRole.TEACHING_DEMO,
            )
        ],
        visual_segments=[
            VisualSegment(
                action_name="杠铃卧推",
                start_seconds=11,
                end_seconds=19,
                visual_cue="使用杠铃完成卧推",
                segment_role=SegmentRole.TEACHING_DEMO,
            )
        ],
    )

    assert len(candidates) == 1
    assert candidates[0].name == "杠铃卧推"
    assert candidates[0].needs_confirmation is False


def test_explicit_equipment_conflict_is_not_merged_by_time_overlap() -> None:
    candidates = fuse_candidates(
        source_id="source-a",
        speech_signals=[
            SpeechSignal(
                action_name="杠铃卧推",
                start_seconds=10,
                end_seconds=20,
                evidence_text="杠铃卧推",
                segment_role=SegmentRole.TEACHING_DEMO,
            )
        ],
        visual_segments=[
            VisualSegment(
                action_name="哑铃卧推",
                start_seconds=11,
                end_seconds=19,
                visual_cue="双手持哑铃卧推",
                segment_role=SegmentRole.TEACHING_DEMO,
            )
        ],
    )

    assert [candidate.name for candidate in candidates] == ["杠铃卧推", "哑铃卧推"]


def test_generic_name_cannot_bridge_conflicting_equipment_groups() -> None:
    candidates = fuse_candidates(
        source_id="source-a",
        speech_signals=[
            SpeechSignal(
                action_name="卧推",
                start_seconds=10,
                end_seconds=20,
                evidence_text="卧推示范",
                segment_role=SegmentRole.TEACHING_DEMO,
            ),
            SpeechSignal(
                action_name="杠铃卧推",
                sets=4,
                start_seconds=30,
                end_seconds=40,
                evidence_text="杠铃卧推四组",
                segment_role=SegmentRole.TEACHING_DEMO,
            ),
            SpeechSignal(
                action_name="哑铃卧推",
                reps=10,
                start_seconds=50,
                end_seconds=60,
                evidence_text="哑铃卧推十次",
                segment_role=SegmentRole.TEACHING_DEMO,
            ),
        ],
        visual_segments=[],
    )

    assert [candidate.name for candidate in candidates] == ["杠铃卧推", "哑铃卧推"]
    assert candidates[0].parameters.sets == 4
    assert candidates[1].parameters.reps == 10


def test_overlapping_different_speech_actions_do_not_share_parameters() -> None:
    candidates = fuse_candidates(
        source_id="source-a",
        speech_signals=[
            SpeechSignal(
                action_name="深蹲",
                sets=4,
                start_seconds=10,
                end_seconds=20,
                evidence_text="深蹲做四组",
            ),
            SpeechSignal(
                action_name="硬拉",
                reps=10,
                start_seconds=12,
                end_seconds=19,
                evidence_text="硬拉做十次",
            ),
        ],
        visual_segments=[],
    )

    assert [candidate.name for candidate in candidates] == ["深蹲", "硬拉"]
    assert candidates[0].parameters.sets == 4
    assert candidates[0].parameters.reps is None
    assert candidates[1].parameters.sets is None
    assert candidates[1].parameters.reps == 10
