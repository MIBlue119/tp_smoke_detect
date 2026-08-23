from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from tp_smoke_detect.ports.inference import (
    InferenceReceipt,
    InferenceRequest,
    InferenceRole,
    InferenceStatus,
    ObjectInference,
    PoseInference,
)


def test_object_result_is_typed_and_rejects_nan_scores() -> None:
    result = ObjectInference(label="cigarette", confidence=0.91)
    assert result.label == "cigarette"

    with pytest.raises(ValidationError):
        ObjectInference(label="cigarette", confidence=float("nan"))


def test_requests_are_metadata_only_and_extra_fields_are_rejected() -> None:
    request = InferenceRequest(
        request_id=uuid4(),
        role=InferenceRole.OBJECT,
        track_id="track-1",
        feature_revision="features-v1",
        features={"crop_artifact_id": "artifact-1"},
    )
    assert request.features["crop_artifact_id"] == "artifact-1"

    with pytest.raises(ValidationError):
        InferenceRequest(
            request_id=uuid4(),
            role=InferenceRole.OBJECT,
            track_id="track-1",
            feature_revision="features-v1",
            features={"pixels": b"private-media"},  # type: ignore[dict-item]
        )


def test_receipt_cannot_pair_a_result_with_the_wrong_role() -> None:
    with pytest.raises(ValidationError):
        InferenceReceipt(
            request_id=uuid4(),
            role=InferenceRole.OBJECT,
            provider="fake",
            model_revision="fake-v1",
            status=InferenceStatus.OK,
            result=PoseInference(confidence=0.8),
            latency_ms=1,
        )
