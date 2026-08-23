"""Dependency-optional OpenAI-compatible local VLM adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from pydantic import ValidationError

from ...ports.inference import (
    InferenceReceipt,
    InferenceRequest,
    InferenceRole,
    InferenceStatus,
    failure_receipt,
    result_for_role,
)


class CompatibleTransport(Protocol):
    def __call__(
        self, payload: Mapping[str, object], *, timeout_ms: int | None = None
    ) -> object: ...


class OpenAICompatibleProvider:
    """Call a site-local compatible endpoint and retain only constrained output."""

    provider_name = "openai-compatible"

    def __init__(
        self,
        *,
        role: InferenceRole,
        revision: str,
        transport: CompatibleTransport | None = None,
        endpoint: str = "http://127.0.0.1",
    ) -> None:
        self.role = role
        self.revision = revision
        self._transport = transport
        self.endpoint = endpoint

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
        if self._transport is None:
            return failure_receipt(
                request,
                provider=self.provider_name,
                revision=self.revision,
                status=InferenceStatus.UNAVAILABLE,
                reason="compatible_transport_not_configured",
            )
        payload: dict[str, object] = {
            "model": self.revision,
            "role": self.role.value,
            "request_id": str(request.request_id),
            "track_id": request.track_id,
            "feature_revision": request.feature_revision,
            "features": request.features,
        }
        try:
            response = self._transport(payload, timeout_ms=timeout_ms)
            if not isinstance(response, Mapping):
                raise TypeError("response must be an object")
            response_revision = response.get("model_revision", self.revision)
            if not isinstance(response_revision, str) or response_revision != self.revision:
                return failure_receipt(
                    request,
                    provider=self.provider_name,
                    revision=str(response_revision),
                    status=InferenceStatus.REVISION_MISMATCH,
                    reason="provider_revision_mismatch",
                )
            raw = response.get("result", response)
            result = result_for_role(self.role, raw)
        except TimeoutError:
            return failure_receipt(
                request,
                provider=self.provider_name,
                revision=self.revision,
                status=InferenceStatus.TIMEOUT,
                reason="compatible_provider_timeout",
                latency_ms=float(timeout_ms or 0),
            )
        except (TypeError, ValueError, ValidationError):
            return failure_receipt(
                request,
                provider=self.provider_name,
                revision=self.revision,
                status=InferenceStatus.MALFORMED,
                reason="compatible_result_malformed",
            )
        except Exception:
            return failure_receipt(
                request,
                provider=self.provider_name,
                revision=self.revision,
                status=InferenceStatus.ERROR,
                reason="compatible_provider_error",
            )
        return InferenceReceipt(
            request_id=request.request_id,
            role=request.role,
            provider=self.provider_name,
            model_revision=self.revision,
            status=InferenceStatus.OK,
            result=result,
            latency_ms=0,
        )


OpenAICompatibleInferenceProvider = OpenAICompatibleProvider

__all__ = ["CompatibleTransport", "OpenAICompatibleInferenceProvider", "OpenAICompatibleProvider"]
