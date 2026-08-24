"""GPU-profile vertical-slice contract tests.

These tests exercise the pixels-free seam that is runnable in the CPU
checkout: a DeepStream-shaped candidate with immutable role receipts enters
the candidate consumer, deterministic policy, audit persistence, metrics, and
shadow audio boundary.  They intentionally do not claim NVIDIA execution;
GPU-107 owns the real-runtime qualification gates.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from tp_smoke_detect.adapters.audio.fake import FakeAudioController
from tp_smoke_detect.adapters.messaging.in_memory import InMemoryMessageBus
from tp_smoke_detect.adapters.persistence.sqlite import SQLiteAuditRepository
from tp_smoke_detect.application.candidate_processing import CandidateProcessingService
from tp_smoke_detect.application.request_audio import AudioRequestService
from tp_smoke_detect.contracts import (
    BoundingBox,
    CandidateEnvelope,
    CandidateInferenceReceipt,
    CandidateInferenceRole,
    CandidateInferenceStatus,
    CandidateOutputSchema,
    DeadlineOutcome,
    Geometry,
    ObjectObservation,
    Observations,
    PoseObservation,
    Quality,
    RunMode,
    SmokeObservation,
    Stage,
    TemporalObservation,
)
from tp_smoke_detect.domain.policy.audio import AudioPolicy, AudioPolicyConfig
from tp_smoke_detect.observability.health import HealthRegistry
from tp_smoke_detect.observability.metrics import OperationalMetrics

ROOT = Path(__file__).parents[2]


def _candidate(
    *,
    timestamp_ns: int,
    retreat: bool,
    object_label: Literal["cigarette", "phone"] = "cigarette",
) -> CandidateEnvelope:
    now = datetime(2026, 8, 24, tzinfo=UTC) + timedelta(seconds=timestamp_ns // 1_000_000_000)
    correlation_id = UUID("1f17f74b-ec66-4a6d-9b4f-6cf8498c56b0")
    roles = (
        (CandidateInferenceRole.DETECTOR, CandidateOutputSchema.DETECTOR_V1, "people-r1"),
        (CandidateInferenceRole.POSE, CandidateOutputSchema.POSE_V1, "pose-r1"),
        (CandidateInferenceRole.OBJECT, CandidateOutputSchema.OBJECT_V1, "crop-r1"),
        (CandidateInferenceRole.SMOKE, CandidateOutputSchema.SMOKE_V1, "smoke-r1"),
        (CandidateInferenceRole.TEMPORAL, CandidateOutputSchema.TEMPORAL_V1, "temporal-r1"),
    )
    receipts = [
        CandidateInferenceReceipt(
            role=role,
            status=CandidateInferenceStatus.OK,
            request_id=uuid4(),
            correlation_id=correlation_id,
            model_revision=revision,
            artifact_revision="gpu-bundle-r1",
            output_schema=schema,
            deadline_outcome=DeadlineOutcome.MET,
            score=0.95,
        )
        for role, schema, revision in roles
    ]
    revisions = {role: revision for role, _schema, revision in roles}
    return CandidateEnvelope(
        event_id=uuid4(),
        correlation_id=correlation_id,
        producer="deepstream-gpu-fixture",
        occurred_at=now,
        camera_id="gpu-camera-01",
        track_id="track-vertical-slice",
        camera_config_revision="camera-gpu-r1",
        source_pts_ns=timestamp_ns,
        capture_ts_ns=timestamp_ns,
        received_ts_ns=timestamp_ns + 8_000_000,
        first_seen_at=now,
        last_seen_at=now + timedelta(milliseconds=100),
        stage=Stage.COMPLETED,
        geometry=Geometry(
            person_box=BoundingBox(x=0.1, y=0.1, width=0.3, height=0.6, confidence=0.99),
            roi_id="gpu-zone-01",
        ),
        quality=Quality(
            source_width=1920,
            source_height=1080,
            face_pixels=120,
            crop_pixels=640,
            illumination_profile="day",
            eligible=True,
            eligibility_reason="gpu-fixture-eligible",
        ),
        observations=Observations(
            pose=PoseObservation(
                hand_to_mouth_distance=0.03,
                mouth_dwell_ms=0 if retreat else 400,
                confidence=0.98,
            ),
            objects=[ObjectObservation(label=object_label, confidence=0.96)],
            smoke=SmokeObservation(smoke_score=0.9, ember_score=0.8),
            temporal=TemporalObservation(
                hand_retreat=retreat,
                cycle_interval_ms=1000 if retreat else None,
                persistence_ms=2500,
            ),
            independent_channels=["object", "smoke"],
        ),
        inference_receipts=receipts,
        model_revisions=revisions,
    )


def _service(
    repository: SQLiteAuditRepository,
    audio: FakeAudioController,
    metrics: OperationalMetrics,
) -> CandidateProcessingService:
    return CandidateProcessingService(
        repository,
        AudioRequestService(
            AudioPolicy(
                AudioPolicyConfig(
                    mode=RunMode.SHADOW,
                    audio_muted=False,
                    policy_revision="gpu-shadow-r1",
                )
            ),
            audio,
            repository,
        ),
        zone_by_camera={"gpu-camera-01": "gpu-zone-01"},
        metrics=metrics,
        health=HealthRegistry(metrics),
    )


def test_gpu_candidate_reaches_audit_and_shadow_audio_without_raw_pixels() -> None:
    repository = SQLiteAuditRepository()
    audio = FakeAudioController()
    metrics = OperationalMetrics()
    service = _service(repository, audio, metrics)
    bus = InMemoryMessageBus()
    results = []
    bus.subscribe(
        lambda candidate: results.append(service.process_payload(candidate.model_dump_json()))
    )

    bus.publish(_candidate(timestamp_ns=1_000_000_000, retreat=False))
    bus.publish(_candidate(timestamp_ns=2_000_000_000, retreat=True))

    assert len(results) == 2
    assert results[-1].decision is not None
    decision = results[-1].decision
    assert decision is not None
    assert decision.outcome.value == "verified"
    assert decision.audio_eligibility is True
    assert decision.model_revisions[CandidateInferenceRole.OBJECT] == "crop-r1"
    assert results[-1].audio_outcome == "would_announce"
    assert audio.commands == []
    saved = repository.get_decision(str(decision.decision_id))
    assert saved is not None
    assert saved["model_revisions"]["detector"] == "people-r1"
    receipt = repository.get_audio_receipt(f"audio:{decision.decision_id}")
    assert receipt is not None
    assert receipt["outcome"] == "would_announce"
    assert "raw_pixels" not in json.dumps(saved)
    assert "gpu-bundle-r1" not in json.dumps(saved)
    rendered = metrics.render()
    assert "smoke_candidate_messages_total" in rendered
    assert "gpu-camera-01" not in rendered


def test_gpu_incomplete_receipt_stays_auditable_and_suppresses_audio() -> None:
    repository = SQLiteAuditRepository()
    audio = FakeAudioController()
    service = _service(repository, audio, OperationalMetrics())
    payload = _candidate(timestamp_ns=1_000_000_000, retreat=True).model_dump(mode="json")
    payload["inference_receipts"] = [
        receipt
        for receipt in payload["inference_receipts"]
        if receipt["role"] != CandidateInferenceRole.OBJECT.value
    ]

    result = service.process_payload(json.dumps(payload))

    assert result.decision is not None
    assert result.decision.outcome.value == "rejected"
    assert result.decision.audio_eligibility is False
    assert repository.list_audio_receipts() == []
    assert audio.commands == []


def test_gpu_profile_runtime_config_is_consumable_by_candidate_service() -> None:
    from tp_smoke_detect.settings import load_settings

    settings = load_settings(ROOT / "configs/runtime.gpu-rtx3090.yaml")

    assert settings.environment == "production"
    assert settings.candidate.require_gpu_receipts is True
    assert settings.policy.mode is RunMode.SHADOW
    assert settings.policy.audio_muted is True


def test_gpu_bundle_and_qualification_receipts_remain_fail_closed() -> None:
    bundle = json.loads(
        (
            ROOT / "docs/dev_artifacts/qualification/model-artifact-receipts/gpu-103-baseline.json"
        ).read_text(encoding="utf-8")
    )
    qualification = json.loads(
        (ROOT / "docs/dev_artifacts/qualification/2026-08-24-gpu-107.json").read_text(
            encoding="utf-8"
        )
    )

    assert bundle["status"] == "blocked"
    assert qualification["status"] in {"blocked", "unqualified"}
    assert qualification.get("release_claim") in {None, "unqualified"}
