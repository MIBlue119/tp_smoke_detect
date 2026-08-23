"""Deterministic provider used by the CPU reference path and contract tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from pydantic import ValidationError

from ...ports.inference import (
    ChewingVetoInference,
    InferenceReceipt,
    InferenceRequest,
    InferenceRole,
    InferenceStatus,
    ObjectInference,
    PoseInference,
    SmokeInference,
    TemporalInference,
    VLMInference,
    failure_receipt,
    result_for_role,
)


class DeterministicFakeProvider:
    """A fixture provider with no clocks, network, GPU, or media dependencies."""

    provider_name = "fake"

    def __init__(
        self,
        *,
        role: InferenceRole,
        revision: str = "fake-v1",
        result: object | None = None,
        raw_result: object | None = None,
        simulated_latency_ms: float = 0,
    ) -> None:
        if result is not None and raw_result is not None:
            raise ValueError("provide result or raw_result, not both")
        if simulated_latency_ms < 0:
            raise ValueError("simulated latency cannot be negative")
        self.role = role
        self.revision = revision
        self._result = result
        self._raw_result = raw_result
        self._simulated_latency_ms = simulated_latency_ms

    def infer(
        self, request: InferenceRequest, *, timeout_ms: int | None = None
    ) -> InferenceReceipt:
        if request.role is not self.role:
            return failure_receipt(
                request,
                provider=self.provider_name,
                revision=self.revision,
                status=InferenceStatus.INVALID,
                reason="provider_role_mismatch",
            )
        if timeout_ms is not None and self._simulated_latency_ms > timeout_ms:
            return failure_receipt(
                request,
                provider=self.provider_name,
                revision=self.revision,
                status=InferenceStatus.TIMEOUT,
                reason="provider_timeout",
                latency_ms=float(timeout_ms),
            )
        raw = self._raw_result if self._raw_result is not None else self._result
        if raw is None:
            raw = self._default_result(request)
        try:
            validated = result_for_role(self.role, raw)
        except (TypeError, ValueError, ValidationError):
            return failure_receipt(
                request,
                provider=self.provider_name,
                revision=self.revision,
                status=InferenceStatus.MALFORMED,
                reason="provider_result_malformed",
                latency_ms=self._simulated_latency_ms,
            )
        return InferenceReceipt(
            request_id=request.request_id,
            role=request.role,
            provider=self.provider_name,
            model_revision=self.revision,
            status=InferenceStatus.OK,
            result=validated,
            latency_ms=self._simulated_latency_ms,
            # The CPU fixture must be byte/repr-stable across repeated runs.
            completed_at=datetime.fromtimestamp(0, tz=UTC),
        )

    def _default_result(self, request: InferenceRequest) -> object:
        digest = hashlib.sha256(
            f"{request.request_id}:{request.track_id}:{request.feature_revision}".encode()
        ).digest()
        score = round((digest[0] / 255) * 0.4 + 0.5, 6)
        defaults: dict[InferenceRole, object] = {
            InferenceRole.POSE: PoseInference(
                hand_to_mouth_distance=0.04, mouth_dwell_ms=500, confidence=score
            ),
            InferenceRole.OBJECT: ObjectInference(label="background", confidence=score),
            InferenceRole.SMOKE: SmokeInference(smoke_score=score, ember_score=score),
            InferenceRole.TEMPORAL: TemporalInference(
                hand_retreat=True, cycle_interval_ms=1200, persistence_ms=2000
            ),
            InferenceRole.CHEWING_VETO: ChewingVetoInference(positive=False, score=0.1),
            InferenceRole.VLM: VLMInference(
                label="unclear", score=score, reason_code="fake_fixture"
            ),
        }
        return defaults[self.role]


FakeInferenceProvider = DeterministicFakeProvider

__all__ = ["DeterministicFakeProvider", "FakeInferenceProvider"]
