"""Optional ONNX Runtime boundary.

The base package deliberately does not import ``onnxruntime``.  Production
bootstrap code supplies a runner from the GPU extra; CPU installations can use
the same port and receive a structured unavailable receipt.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import ValidationError

from ...ports.inference import (
    InferenceReceipt,
    InferenceRequest,
    InferenceRole,
    InferenceStatus,
    failure_receipt,
    result_for_role,
)


class OnnxInferenceProvider:
    provider_name = "onnxruntime"

    def __init__(
        self,
        *,
        role: InferenceRole,
        revision: str,
        runner: Callable[[InferenceRequest], object] | None = None,
    ) -> None:
        self.role = role
        self.revision = revision
        self._runner = runner

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
        if self._runner is None:
            return failure_receipt(
                request,
                provider=self.provider_name,
                revision=self.revision,
                status=InferenceStatus.UNAVAILABLE,
                reason="onnx_runtime_not_installed",
            )
        try:
            raw = self._runner(request)
            result = result_for_role(self.role, raw)
        except TimeoutError:
            return failure_receipt(
                request,
                provider=self.provider_name,
                revision=self.revision,
                status=InferenceStatus.TIMEOUT,
                reason="onnx_inference_timeout",
                latency_ms=float(timeout_ms or 0),
            )
        except MemoryError:
            return failure_receipt(
                request,
                provider=self.provider_name,
                revision=self.revision,
                status=InferenceStatus.ERROR,
                reason="onnx_out_of_memory",
            )
        except (TypeError, ValueError, ValidationError):
            return failure_receipt(
                request,
                provider=self.provider_name,
                revision=self.revision,
                status=InferenceStatus.MALFORMED,
                reason="onnx_result_malformed",
            )
        except Exception:
            # Do not leak runtime details into an operator-visible receipt.
            return failure_receipt(
                request,
                provider=self.provider_name,
                revision=self.revision,
                status=InferenceStatus.ERROR,
                reason="onnx_provider_error",
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


__all__ = ["OnnxInferenceProvider"]
