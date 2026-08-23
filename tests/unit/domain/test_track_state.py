from tp_smoke_detect.contracts import Stage
from tp_smoke_detect.domain.cascade.track import TrackState
from tp_smoke_detect.domain.models.decisions import ReasonCode
from tp_smoke_detect.domain.models.observations import DomainObservation


def test_track_transitions_are_source_timestamp_driven() -> None:
    state = TrackState("cam-a", "track-1")
    state.ingest(DomainObservation(timestamp_ns=10, object_label="cigarette"))
    state.ingest(DomainObservation(timestamp_ns=20, smoke_score=0.8))

    assert [transition.from_stage for transition in state.transitions] == [
        Stage.DETECTED,
        Stage.QUALITY,
        Stage.POSE,
        Stage.OBJECT,
    ]
    assert state.stage == Stage.TEMPORAL


def test_duplicate_and_gap_are_explicit_and_do_not_join_cycles() -> None:
    state = TrackState("cam-a", "track-1", gap_tolerance_ms=100)
    first = DomainObservation(timestamp_ns=0, event_id="a")
    assert state.ingest(first) == ()
    assert state.ingest(first) == (ReasonCode.DUPLICATE,)

    reasons = state.ingest(DomainObservation(timestamp_ns=200_000_001))
    assert reasons == (ReasonCode.TRACK_GAP,)
    assert state.persistence_ms == 0
