from __future__ import annotations

from uuid import UUID, uuid4

from tp_smoke_detect.adapters.inference.fake import DeterministicFakeProvider
from tp_smoke_detect.ports.inference import (
    InferenceRequest,
    InferenceRole,
    InferenceStatus,
    ObjectInference,
)


def request(
    role: InferenceRole = InferenceRole.OBJECT, request_id: UUID | None = None
) -> InferenceRequest:
    return InferenceRequest(
        request_id=request_id or uuid4(),
        role=role,
        track_id="track-1",
        feature_revision="features-v1",
        features={"crop_artifact_id": "artifact-1"},
    )


def test_fake_provider_is_reproducible_and_structured() -> None:
    provider = DeterministicFakeProvider(
        role=InferenceRole.OBJECT,
        revision="fake-object-v1",
        result=ObjectInference(label="cigarette", confidence=0.95),
    )

    first = provider.infer(request())
    second = provider.infer(request(request_id=first.request_id))

    assert first == second
    assert first.status is InferenceStatus.OK
    assert first.model_revision == "fake-object-v1"
    assert first.result == ObjectInference(label="cigarette", confidence=0.95)


def test_fake_timeout_and_malformed_fixture_fail_closed() -> None:
    slow = DeterministicFakeProvider(
        role=InferenceRole.OBJECT,
        simulated_latency_ms=100,
        revision="fake-object-v1",
    )
    malformed = DeterministicFakeProvider(
        role=InferenceRole.OBJECT,
        raw_result={"label": "not-a-role", "confidence": 1.0},
        revision="fake-object-v1",
    )

    assert slow.infer(request(), timeout_ms=10).status is InferenceStatus.TIMEOUT
    assert malformed.infer(request()).status is InferenceStatus.MALFORMED
