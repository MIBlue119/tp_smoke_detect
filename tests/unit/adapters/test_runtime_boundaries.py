from __future__ import annotations

from collections.abc import Mapping
from uuid import uuid4

from tp_smoke_detect.adapters.inference.onnx import OnnxInferenceProvider
from tp_smoke_detect.adapters.inference.openai_compatible import OpenAICompatibleProvider
from tp_smoke_detect.ports.inference import InferenceRequest, InferenceRole, InferenceStatus


def request(role: InferenceRole) -> InferenceRequest:
    return InferenceRequest(
        request_id=uuid4(),
        role=role,
        track_id="track-1",
        feature_revision="features-v1",
        features={"crop_artifact_id": "artifact-1"},
    )


def test_onnx_adapter_is_dependency_optional_and_fail_closed() -> None:
    provider = OnnxInferenceProvider(role=InferenceRole.OBJECT, revision="onnx-v1")
    receipt = provider.infer(request(InferenceRole.OBJECT))
    assert receipt.status is InferenceStatus.UNAVAILABLE
    assert receipt.result is None


def test_openai_compatible_adapter_rejects_revision_mismatch() -> None:
    def transport(payload: Mapping[str, object], *, timeout_ms: int | None = None) -> object:
        del payload, timeout_ms
        return {
            "model_revision": "wrong-v1",
            "result": {"label": "cigarette", "confidence": 0.9},
        }

    provider = OpenAICompatibleProvider(
        role=InferenceRole.OBJECT,
        revision="champion-v1",
        transport=transport,
    )
    receipt = provider.infer(request(InferenceRole.OBJECT))
    assert receipt.status is InferenceStatus.REVISION_MISMATCH
    assert receipt.result is None
