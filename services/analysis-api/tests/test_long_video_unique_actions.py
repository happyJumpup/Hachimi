import pytest

from hakimi_analysis.benchmark.long_execution import (
    consolidate_unique_action_candidates,
)
from hakimi_analysis.benchmark.long_models import LongExperimentSource
from hakimi_analysis.benchmark.long_prompts import long_video_prompt
from hakimi_analysis.benchmark.long_scoring import (
    LongVideoSourceGold,
    validate_gold_against_source_contract,
)
from hakimi_analysis.benchmark.models import (
    BenchmarkCandidate,
    EventKind,
    EvidenceChannel,
    GoldEvent,
)


def _candidate(
    name: str,
    start: float,
    end: float,
    *,
    channels: list[EvidenceChannel] | None = None,
    sets: int | None = None,
    reps: int | None = None,
) -> BenchmarkCandidate:
    return BenchmarkCandidate(
        action_name=name,
        start_seconds=start,
        end_seconds=end,
        kind=EventKind.ACTION,
        sets=sets,
        reps=reps,
        evidence_channels=channels or [EvidenceChannel.VISUAL],
    )


def _gold(
    name: str,
    start: float,
    end: float,
    *,
    aliases: list[str] | None = None,
) -> GoldEvent:
    return GoldEvent(
        canonical_name=name,
        accepted_aliases=aliases or [name],
        start_seconds=start,
        end_seconds=end,
        kind=EventKind.ACTION,
        evidence_channels=[EvidenceChannel.AUDIO, EvidenceChannel.VISUAL],
    )


def test_v2_prompt_requests_one_candidate_per_unique_trainable_action() -> None:
    prompt = long_video_prompt("long-video-ab-v2")

    assert "每个不同的可训练动作只返回一个候选" in prompt
    assert "最清晰、完整的主演示片段" in prompt
    assert "不得超过120秒" in prompt
    assert "同一动作的讲解、纠错和重复示范" in prompt
    assert "同一个规范中文名" in prompt


def test_unique_action_consolidation_keeps_one_primary_demo_and_shared_parameters() -> None:
    candidates = [
        _candidate("罗马尼亚硬拉", 44, 58, sets=3, reps=10),
        _candidate(
            "罗马尼亚硬拉",
            394,
            420,
            channels=[EvidenceChannel.AUDIO, EvidenceChannel.VISUAL],
            sets=3,
            reps=10,
        ),
        _candidate("平板杠铃卧推", 344, 440, sets=5, reps=10),
    ]

    consolidated = consolidate_unique_action_candidates(candidates)

    assert [item.action_name for item in consolidated] == [
        "平板杠铃卧推",
        "罗马尼亚硬拉",
    ]
    rdl = consolidated[1]
    assert (rdl.start_seconds, rdl.end_seconds) == (394, 420)
    assert rdl.sets == 3
    assert rdl.reps == 10
    assert rdl.evidence_channels == [EvidenceChannel.AUDIO, EvidenceChannel.VISUAL]


def test_unique_action_consolidation_does_not_select_a_full_tutorial_as_the_demo() -> None:
    consolidated = consolidate_unique_action_candidates(
        [
            _candidate(
                "罗马尼亚硬拉",
                7,
                384,
                channels=[EvidenceChannel.AUDIO, EvidenceChannel.VISUAL],
            ),
            _candidate(
                "罗马尼亚硬拉",
                394,
                420,
                channels=[EvidenceChannel.AUDIO, EvidenceChannel.VISUAL],
            ),
        ]
    )

    assert len(consolidated) == 1
    assert (consolidated[0].start_seconds, consolidated[0].end_seconds) == (394, 420)


def test_unique_action_consolidation_rejects_only_overlong_tutorial_segments() -> None:
    consolidated = consolidate_unique_action_candidates(
        [
            _candidate(
                "罗马尼亚硬拉",
                7,
                384,
                channels=[EvidenceChannel.AUDIO, EvidenceChannel.VISUAL],
            )
        ]
    )

    assert consolidated == []


def test_v2_reviewed_gold_rejects_duplicate_action_identities() -> None:
    with pytest.raises(ValueError, match="one event per unique trainable action"):
        LongVideoSourceGold(
            source_id="romanian-deadlift-7m",
            duration_seconds=424.31,
            gold_version="long-video-gold-v2",
            reviewed=True,
            events=[
                _gold("罗马尼亚硬拉", 44, 58),
                _gold("罗马尼亚硬拉", 394, 420),
            ],
        )


def test_v2_gold_accepts_120_seconds_and_rejects_a_longer_primary_demo() -> None:
    accepted = LongVideoSourceGold(
        source_id="romanian-deadlift-7m",
        duration_seconds=424.31,
        gold_version="long-video-gold-v2",
        reviewed=True,
        events=[_gold("罗马尼亚硬拉", 0, 120)],
    )

    assert accepted.events[0].end_seconds == 120
    with pytest.raises(ValueError, match="cannot exceed 120 seconds"):
        LongVideoSourceGold(
            source_id="romanian-deadlift-7m",
            duration_seconds=424.31,
            gold_version="long-video-gold-v2",
            reviewed=True,
            events=[_gold("罗马尼亚硬拉", 0, 120.001)],
        )


def test_v2_gold_is_non_empty_and_matches_the_offline_source_contract() -> None:
    source = LongExperimentSource(
        source_id="romanian-deadlift-7m",
        source_path="source.mp4",
        sha256="a" * 64,
        duration_seconds=424.31,
        gold_path="gold.json",
        gold_version="long-video-gold-v2",
        gold_contract={
            "version": "long-video-unique-actions-v1",
            "actions": [
                {
                    "canonical_name": "罗马尼亚硬拉",
                    "accepted_aliases": ["RDL", "罗马尼亚式硬拉"],
                }
            ],
        },
    )

    with pytest.raises(ValueError, match="at least one trainable action"):
        LongVideoSourceGold(
            source_id=source.source_id,
            duration_seconds=source.duration_seconds,
            gold_version="long-video-gold-v2",
            reviewed=True,
            events=[],
        )

    wrong = LongVideoSourceGold(
        source_id=source.source_id,
        duration_seconds=source.duration_seconds,
        gold_version="long-video-gold-v2",
        reviewed=True,
        events=[_gold("传统硬拉", 394, 420)],
    )
    with pytest.raises(ValueError, match="source contract"):
        validate_gold_against_source_contract(source, wrong)

    valid = LongVideoSourceGold(
        source_id=source.source_id,
        duration_seconds=source.duration_seconds,
        gold_version="long-video-gold-v2",
        reviewed=True,
        events=[
            _gold(
                "罗马尼亚硬拉",
                394,
                420,
                aliases=["RDL", "罗马尼亚式硬拉"],
            )
        ],
    )
    assert validate_gold_against_source_contract(source, valid) is valid


def test_v2_gold_contract_locks_the_nineteen_minute_source_to_seven_actions() -> None:
    names = [
        "弹力带肩活动",
        "推撑类激活",
        "低位绳索夹胸",
        "平板杠铃卧推",
        "上斜器械卧推",
        "坐姿杠铃实力推",
        "颈后绳索臂屈伸",
    ]
    source = LongExperimentSource(
        source_id="beginner-full-workout-19m",
        source_path="source.mp4",
        sha256="b" * 64,
        duration_seconds=1157.46,
        gold_path="gold.json",
        gold_version="long-video-gold-v2",
        gold_contract={
            "version": "long-video-unique-actions-v1",
            "actions": [
                {"canonical_name": name, "accepted_aliases": [name]} for name in names
            ],
        },
    )
    incomplete = LongVideoSourceGold(
        source_id=source.source_id,
        duration_seconds=source.duration_seconds,
        gold_version="long-video-gold-v2",
        reviewed=True,
        events=[
            _gold(name, index * 100, index * 100 + 20)
            for index, name in enumerate(names[:-1])
        ],
    )

    with pytest.raises(ValueError, match="source contract"):
        validate_gold_against_source_contract(source, incomplete)
