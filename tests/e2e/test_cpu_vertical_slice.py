"""CPU qualification: replay -> cascade -> audit -> policy -> review.

This intentionally uses the in-memory metadata bus and SQLite repository.  It
is the deterministic reference seam for an air-gapped host; broker/PostgreSQL
and GPU qualification are separate gates and are not implied by this test.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from tp_smoke_detect.adapters.artifacts.local import LocalArtifactStore
from tp_smoke_detect.adapters.audio.fake import FakeAudioController
from tp_smoke_detect.adapters.messaging.in_memory import InMemoryMessageBus
from tp_smoke_detect.adapters.persistence.sqlite import SQLiteAuditRepository
from tp_smoke_detect.application.replay import ReplayWorker, synthetic_camera
from tp_smoke_detect.application.request_audio import AudioRequestService
from tp_smoke_detect.contracts import CandidateEnvelope, DecisionCompleted, DecisionOutcome, RunMode
from tp_smoke_detect.domain.cascade.engine import CascadeEngine
from tp_smoke_detect.domain.models.observations import DomainObservation
from tp_smoke_detect.domain.policy.audio import AudioPolicy, AudioPolicyConfig

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (b"\x00" * 17)


def _manifest() -> dict[str, object]:
    box = {"x": 0.2, "y": 0.2, "width": 0.3, "height": 0.5, "confidence": 0.99}
    approach = {
        "pose": {"hand_to_mouth_distance": 0.03, "mouth_dwell_ms": 300, "confidence": 0.99},
        "objects": [{"label": "cigarette", "confidence": 0.96}],
        "smoke": {"smoke_score": 0.9, "ember_score": 0.8},
        "temporal": {"hand_retreat": False, "persistence_ms": 2500},
        "independent_channels": ["object", "smoke"],
    }
    retreat = {
        **approach,
        "pose": {"confidence": 0.99},
        "temporal": {"hand_retreat": True, "persistence_ms": 2500, "cycle_interval_ms": 1000},
    }
    return {
        "camera_id": "e2e-camera",
        "camera_config_revision": "e2e-camera-r1",
        "source_fps": 1,
        "source_width": 640,
        "source_height": 480,
        "frames": [
            {
                "frame_id": "approach",
                "artifact_id": "approach.png",
                "pts_ns": 0,
                "track_id": "track-e2e",
                "person_box": box,
                "observations": approach,
            },
            {
                "frame_id": "retreat",
                "artifact_id": "retreat.png",
                "pts_ns": 1_000_000_000,
                "track_id": "track-e2e",
                "person_box": box,
                "observations": retreat,
            },
        ],
    }


def _domain_observation(candidate: CandidateEnvelope) -> DomainObservation:
    observations = candidate.observations
    pose = observations.pose
    temporal = observations.temporal
    object_observation = observations.objects[0] if observations.objects else None
    smoke = observations.smoke
    return DomainObservation(
        timestamp_ns=candidate.capture_ts_ns,
        event_id=candidate.track_id + ":" + str(candidate.source_pts_ns),
        quality_eligible=candidate.quality.eligible,
        pose_eligible=pose is None or pose.confidence >= 0.5,
        hand_to_mouth=pose is not None and pose.hand_to_mouth_distance is not None,
        contact_target="mouth",
        mouth_dwell_ms=pose.mouth_dwell_ms if pose and pose.mouth_dwell_ms else 0,
        hand_retreat=temporal.hand_retreat if temporal else False,
        object_label=object_observation.label if object_observation else None,
        object_score=object_observation.confidence if object_observation else 0,
        smoke_score=smoke.smoke_score if smoke else 0,
        ember_score=smoke.ember_score if smoke else 0,
        persistence_ms=temporal.persistence_ms if temporal else 0,
        positive_channels=frozenset(observations.independent_channels),
        model_revisions=(("fake", "fixture-e2e-r1"),),
    )


def test_replay_decision_audit_shadow_and_review(tmp_path: Path) -> None:
    (tmp_path / "approach.png").write_bytes(PNG)
    (tmp_path / "retreat.png").write_bytes(PNG)
    repository = SQLiteAuditRepository()
    bus = InMemoryMessageBus()
    audio = FakeAudioController()
    service = AudioRequestService(
        AudioPolicy(AudioPolicyConfig(mode=RunMode.SHADOW, audio_muted=False)), audio, repository
    )
    engine = CascadeEngine("e2e-camera", "track-e2e")
    decisions: list[DecisionCompleted] = []

    def consume(candidate: CandidateEnvelope) -> None:
        result = engine.ingest(_domain_observation(candidate))
        if result.outcome is not DecisionOutcome.VERIFIED or decisions:
            return
        decision = DecisionCompleted(
            event_id=uuid4(),
            correlation_id=uuid4(),
            producer="test.e2e",
            occurred_at=datetime(2026, 8, 24, tzinfo=UTC),
            decision_id=uuid4(),
            camera_id="e2e-camera",
            track_id="track-e2e",
            outcome=result.outcome,
            reason_codes=[reason.value for reason in result.reason_codes],
            evidence_channels=[item.to_contract() for item in result.evidence_channels],
            model_revisions=dict(result.model_revisions),
            policy_revision="e2e-policy-r1",
            latency_ms=1.0,
            mode=RunMode.SHADOW,
            audio_eligibility=result.audio_eligibility,
        )
        repository.put_decision(decision.model_dump(mode="json"))
        policy_result = service.request(
            decision,
            camera_id=decision.camera_id,
            zone_id="e2e-zone",
            now=datetime(2026, 8, 24, tzinfo=UTC),
        )
        assert policy_result.outcome == "would_announce"
        decisions.append(decision)
        repository.append_review(
            {
                "review_id": str(uuid4()),
                "decision_id": str(decision.decision_id),
                "label": "true_positive",
                "actor": "e2e-operator",
                "reason": "deterministic qualification fixture",
            }
        )

    bus.subscribe(consume)
    result = ReplayWorker(
        synthetic_camera().model_copy(update={"camera_id": "e2e-camera"}),
        LocalArtifactStore(tmp_path),
        bus,
        sampling_fps=1,
    ).replay(_manifest())

    assert result.ok
    assert len(result.candidates) == 2
    assert len(bus.messages) == 2
    assert len(decisions) == 1
    saved = repository.get_decision(str(decisions[0].decision_id))
    assert saved is not None
    assert saved["outcome"] == "verified"
    receipt = repository.get_audio_receipt(f"audio:{decisions[0].decision_id}")
    assert receipt is not None
    assert receipt["outcome"] == "would_announce"
    assert audio.commands == []
    assert len(repository.connection.execute("SELECT * FROM reviews").fetchall()) == 1


def test_automatic_profile_uses_fake_audio_only_after_policy(tmp_path: Path) -> None:
    del tmp_path
    repository = SQLiteAuditRepository()
    audio = FakeAudioController(now=datetime(2026, 8, 24, tzinfo=UTC))
    service = AudioRequestService(
        AudioPolicy(AudioPolicyConfig(mode=RunMode.AUTOMATIC, audio_muted=False)), audio, repository
    )
    decision = DecisionCompleted(
        event_id=uuid4(),
        correlation_id=uuid4(),
        producer="test.e2e",
        occurred_at=datetime(2026, 8, 24, tzinfo=UTC),
        decision_id=uuid4(),
        camera_id="e2e-camera",
        track_id="track-e2e",
        outcome=DecisionOutcome.VERIFIED,
        reason_codes=["verified"],
        evidence_channels=[],
        model_revisions={"fake": "fixture-e2e-r1"},
        policy_revision="e2e-policy-r1",
        latency_ms=1,
        mode=RunMode.AUTOMATIC,
        audio_eligibility=True,
    )
    result = service.request(
        decision,
        camera_id=decision.camera_id,
        zone_id="e2e-zone",
        now=datetime(2026, 8, 24, tzinfo=UTC),
    )
    assert result.allowed
    assert len(audio.commands) == 1
    assert repository.get_audio_receipt(f"audio:{decision.decision_id}") is not None
