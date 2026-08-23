from __future__ import annotations

from uuid import uuid4

from tp_smoke_detect.adapters.inference.fake import DeterministicFakeProvider
from tp_smoke_detect.application.model_router import ModelRouter
from tp_smoke_detect.ports.inference import InferenceRequest, InferenceRole, InferenceStatus


def request() -> InferenceRequest:
    return InferenceRequest(
        request_id=uuid4(),
        role=InferenceRole.VLM,
        track_id="track-1",
        feature_revision="features-v1",
        features={"crop_artifact_id": "artifact-1"},
    )


def test_router_readiness_and_circuit_breaker_are_separate_from_liveness() -> None:
    provider = DeterministicFakeProvider(
        role=InferenceRole.VLM,
        revision="vlm-v1",
        simulated_latency_ms=100,
    )
    router = ModelRouter({InferenceRole.VLM: provider}, timeout_ms=10, failure_threshold=2)

    assert router.liveness() is True
    assert router.readiness()[InferenceRole.VLM] is True
    assert router.infer(request()).status is InferenceStatus.TIMEOUT
    assert router.infer(request()).status is InferenceStatus.TIMEOUT
    assert router.infer(request()).status is InferenceStatus.UNAVAILABLE
    assert router.readiness()[InferenceRole.VLM] is False


def test_router_rejects_revision_mismatch_without_positive_evidence() -> None:
    provider = DeterministicFakeProvider(role=InferenceRole.OBJECT, revision="old-v1")
    router = ModelRouter(
        {InferenceRole.OBJECT: provider},
        expected_revisions={InferenceRole.OBJECT: "champion-v2"},
    )

    receipt = router.infer(
        InferenceRequest(
            request_id=uuid4(),
            role=InferenceRole.OBJECT,
            track_id="track-1",
            feature_revision="features-v1",
            features={"crop_artifact_id": "artifact-1"},
        )
    )
    assert receipt.status is InferenceStatus.REVISION_MISMATCH
    assert receipt.result is None
