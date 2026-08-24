from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from tp_smoke_detect.contracts import (
    BoundingBox,
    CandidateEnvelope,
    CandidateInferenceReason,
    CandidateInferenceReceipt,
    CandidateInferenceRole,
    CandidateInferenceStatus,
    DeadlineOutcome,
    Geometry,
    InferenceReviewerEvidence,
    ObjectObservation,
    Observations,
    Quality,
    ReviewerLabel,
    ReviewerReasonCode,
    Stage,
)


def candidate(**overrides: object) -> CandidateEnvelope:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    values: dict[str, object] = {
        "event_id": uuid4(),
        "correlation_id": uuid4(),
        "producer": "gpu-fixture",
        "occurred_at": now,
        "camera_id": "cam-01",
        "track_id": "track-1",
        "camera_config_revision": "cam-01-r1",
        "source_pts_ns": 1,
        "capture_ts_ns": 2,
        "received_ts_ns": 3,
        "first_seen_at": now,
        "last_seen_at": now,
        "stage": Stage.DETECTED,
        "geometry": Geometry(
            person_box=BoundingBox(x=0.1, y=0.1, width=0.2, height=0.4, confidence=0.9)
        ),
        "quality": Quality(
            source_width=1920,
            source_height=1080,
            face_pixels=80,
            crop_pixels=200,
            illumination_profile="day",
            eligible=True,
            eligibility_reason="within_camera_limits",
        ),
        "observations": Observations(),
    }
    values.update(overrides)
    return CandidateEnvelope.model_validate(values)


def receipt(
    *,
    role: CandidateInferenceRole = CandidateInferenceRole.OBJECT,
    status: CandidateInferenceStatus = CandidateInferenceStatus.OK,
    model_revision: str = "siglip2-head-r1",
    **overrides: object,
) -> CandidateInferenceReceipt:
    values: dict[str, object] = {
        "role": role,
        "status": status,
        "reason_code": CandidateInferenceReason.NONE
        if status is CandidateInferenceStatus.OK
        else CandidateInferenceReason.TIMEOUT,
        "request_id": uuid4(),
        "correlation_id": uuid4(),
        "model_revision": model_revision,
        "artifact_revision": "artifact-r1",
        "output_schema": "object.v1" if role is CandidateInferenceRole.OBJECT else "none",
        "deadline_outcome": DeadlineOutcome.MET
        if status is CandidateInferenceStatus.OK
        else DeadlineOutcome.EXPIRED,
        "score": 0.91 if status is CandidateInferenceStatus.OK else None,
    }
    values.update(overrides)
    return CandidateInferenceReceipt.model_validate(values)


def test_legacy_candidate_defaults_gpu_fields_to_unavailable() -> None:
    payload = candidate().model_dump(mode="json")
    assert payload["inference_receipts"] == []
    assert payload["model_revisions"] == {}
    parsed = CandidateEnvelope.model_validate(payload)
    assert parsed.inference_receipts == []
    assert parsed.model_revisions == {}


def test_complete_reviewer_receipt_maps_to_domain_without_pixels() -> None:
    reviewer = receipt(
        role=CandidateInferenceRole.REVIEWER,
        model_revision="reviewer-r7",
        output_schema="reviewer.v1",
        score=0.88,
        reviewer=InferenceReviewerEvidence(
            label=ReviewerLabel.SMOKING,
            score=0.88,
            reason_code=ReviewerReasonCode.SMOKING_BEHAVIOR,
        ),
    )
    item = candidate(
        model_revisions={CandidateInferenceRole.REVIEWER: "reviewer-r7"},
        inference_receipts=[reviewer],
        observations=Observations(
            reviewer=InferenceReviewerEvidence(
                label=ReviewerLabel.SMOKING,
                score=0.88,
                reason_code=ReviewerReasonCode.SMOKING_BEHAVIOR,
            )
        ),
    )

    observation = item.to_domain_observation()

    assert observation.vlm is not None
    assert observation.vlm.status == "positive"
    assert observation.vlm.revision == "reviewer-r7"
    assert "raw_pixels" not in json.dumps(item.model_dump(mode="json"))


def test_failed_receipt_cannot_carry_positive_result() -> None:
    with pytest.raises(ValidationError):
        receipt(
            status=CandidateInferenceStatus.TIMEOUT,
            score=0.91,
            deadline_outcome=DeadlineOutcome.EXPIRED,
        )


def test_failed_role_cannot_populate_positive_observation() -> None:
    failed_object = receipt(status=CandidateInferenceStatus.TIMEOUT)
    with pytest.raises(ValidationError):
        candidate(
            inference_receipts=[failed_object],
            observations=Observations(
                objects=[ObjectObservation(label="cigarette", confidence=0.9)]
            ),
        )


def test_revision_mismatch_and_free_form_reviewer_output_are_rejected() -> None:
    object_receipt = receipt()
    with pytest.raises(ValidationError):
        candidate(
            model_revisions={CandidateInferenceRole.OBJECT: "different-r1"},
            inference_receipts=[object_receipt],
        )

    with pytest.raises(ValidationError):
        receipt(
            role=CandidateInferenceRole.REVIEWER,
            output_schema="reviewer.v1",
            reviewer={
                "label": "smoking",
                "score": 0.9,
                "reason_code": "the model said this is smoking",
            },
        )


def test_reviewer_observation_requires_a_matching_success_receipt() -> None:
    with pytest.raises(ValidationError):
        candidate(
            observations=Observations(
                reviewer=InferenceReviewerEvidence(
                    label=ReviewerLabel.SMOKING,
                    score=0.9,
                    reason_code=ReviewerReasonCode.SMOKING_BEHAVIOR,
                )
            )
        )
