from ml.demo.fusion import (
    CigaretteDetection,
    DeterministicPersonTracker,
    FrameDetections,
    PersonDetection,
)


def _person(x: float = 0.1, track_id: str | None = None) -> PersonDetection:
    points = [(0.0, 0.0, 0.0)] * 17
    points[0] = (x + 0.2, 0.25, 0.95)
    points[9] = (x + 0.2, 0.40, 0.80)
    return PersonDetection((x, 0.1, x + 0.4, 0.9), tuple(points), 0.95, track_id)


def test_tracker_assigns_stable_ids_by_iou() -> None:
    tracker = DeterministicPersonTracker()
    first = tracker.update(FrameDetections(0, 0, (_person(),)))
    second = tracker.update(FrameDetections(1, 1_000_000, (_person(0.01),)))
    assert first[0].track_id == "track-000001"
    assert second[0].track_id == first[0].track_id


def test_tracker_tie_breaks_by_existing_track_id() -> None:
    tracker = DeterministicPersonTracker()
    tracker.update(FrameDetections(0, 0, (_person(0.1), _person(0.55))))
    output = tracker.update(FrameDetections(1, 1_000_000, (_person(0.1), _person(0.55))))
    assert [person.track_id for person in output] == ["track-000001", "track-000002"]


def test_detection_types_reject_unnormalized_geometry() -> None:
    try:
        CigaretteDetection((0.0, 0.0, 1.1, 0.2), 0.8)
    except ValueError as exc:
        assert "normalized" in str(exc)
    else:
        raise AssertionError("out-of-range cigarette box was accepted")
