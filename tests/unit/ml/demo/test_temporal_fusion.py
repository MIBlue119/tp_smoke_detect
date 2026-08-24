from ml.demo.contracts import EventState
from ml.demo.fusion import (
    CigaretteDetection,
    DeterministicFusion,
    FrameDetections,
    FusionConfig,
    PersonDetection,
    baseline_miss_event,
)


def _person(track_id: str = "person-a", cigarette_visible: bool = True) -> FrameDetections:
    points = [(0.0, 0.0, 0.0)] * 17
    points[0] = (0.30, 0.25, 0.95)
    points[9] = (0.30, 0.40, 0.80)
    cigarette = (CigaretteDetection((0.28, 0.23, 0.32, 0.28), 0.95),) if cigarette_visible else ()
    return FrameDetections(
        0,
        0,
        (PersonDetection((0.1, 0.1, 0.5, 0.9), tuple(points), 0.95, track_id),),
        cigarette,
    )


def _frames(count: int, visible: bool = True) -> tuple[FrameDetections, ...]:
    return tuple(
        FrameDetections(
            index,
            index * 33_333_333,
            _person().persons,
            _person(cigarette_visible=visible).cigarettes,
        )
        for index in range(count)
    )


def test_candidate_requires_persistence_and_has_one_interval() -> None:
    result = DeterministicFusion().process(_frames(4))
    assert result.frames[0].state is EventState.INSUFFICIENT_EVIDENCE
    assert result.frames[-1].state is EventState.CANDIDATE
    assert len(result.events) == 1
    assert result.events[0].start_pts_ns == 3 * 33_333_333
    assert result.events[0].state is EventState.CANDIDATE


def test_unmatched_evidence_is_unclear_after_active_candidate() -> None:
    frames = _frames(4) + _frames(1, visible=False)
    # Rebase the appended fixture's frame/time so it is source ordered.
    frames = frames[:4] + (FrameDetections(4, 4 * 33_333_333, frames[4].persons, ()),)
    result = DeterministicFusion().process(frames)
    assert result.frames[-1].state is EventState.UNCLEAR
    assert "occluded" in result.frames[-1].reason_codes
    assert len(result.events) == 1


def test_missing_person_frames_close_candidate_after_gap() -> None:
    visible = _frames(4)
    absent = tuple(FrameDetections(index, index * 33_333_333, ()) for index in range(4, 11))
    result = DeterministicFusion().process(visible + absent)
    assert len(result.events) == 1
    assert result.events[0].end_pts_ns == 3 * 33_333_333
    assert "track_gap" in result.events[0].reason_codes


def test_out_of_order_pts_is_not_counted() -> None:
    result = DeterministicFusion().process(
        (
            FrameDetections(0, 33_333_333, _frames(1)[0].persons, _frames(1)[0].cigarettes),
            FrameDetections(1, 0, _frames(1)[0].persons, _frames(1)[0].cigarettes),
        )
    )
    assert result.frames[-1].state is EventState.UNCLEAR
    assert result.frames[-1].reason_codes == ("out_of_order_pts",)
    assert not result.events


def test_same_input_is_byte_stable_and_manual_miss_is_separate() -> None:
    first = DeterministicFusion().process(_frames(5))
    second = DeterministicFusion().process(_frames(5))
    assert first == second
    assert baseline_miss_event("track-1", 10, 20).state is EventState.BASELINE_MISS


def test_thresholds_are_validated() -> None:
    try:
        FusionConfig(entry_confidence=0.2, exit_confidence=0.3)
    except ValueError as exc:
        assert "exit_confidence" in str(exc)
    else:
        raise AssertionError("invalid hysteresis thresholds were accepted")
