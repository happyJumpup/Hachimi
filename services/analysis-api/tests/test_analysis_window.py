from hakimi_analysis.media import AnalysisWindow, calculate_analysis_window


def test_default_window_is_clamped_to_the_source_duration() -> None:
    assert calculate_analysis_window(trigger_seconds=5, duration_seconds=54) == AnalysisWindow(
        start_seconds=0,
        end_seconds=25,
        expanded=False,
    )


def test_default_window_uses_fifteen_seconds_before_and_twenty_after() -> None:
    assert calculate_analysis_window(trigger_seconds=30, duration_seconds=90) == AnalysisWindow(
        start_seconds=15,
        end_seconds=50,
        expanded=False,
    )


def test_expanded_window_is_clamped_and_marked_expanded() -> None:
    assert calculate_analysis_window(
        trigger_seconds=45,
        duration_seconds=54,
        expanded=True,
    ) == AnalysisWindow(start_seconds=15, end_seconds=54, expanded=True)


def test_trigger_outside_the_video_is_rejected() -> None:
    try:
        calculate_analysis_window(trigger_seconds=55, duration_seconds=54)
    except ValueError as error:
        assert str(error) == "trigger_seconds must be inside the source video"
    else:
        raise AssertionError("expected invalid trigger to be rejected")
