"""Optional ONNX Runtime boundary.

The base package deliberately does not import ``onnxruntime``.  Production
bootstrap code supplies a runner from the GPU extra; CPU installations can use
the same port and receive a structured unavailable receipt.
"""

from __future__ import annotations

import inspect
import time
from collections.abc import Callable, Mapping

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
        runner: Callable[..., object] | None = None,
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
        started = time.monotonic()
        try:
            # A synchronous arbitrary callable cannot be cancelled safely in
            # this process.  When a deadline is requested, require the ONNX
            # runtime boundary to opt into the cooperative ``deadline`` (or
            # legacy ``timeout_ms``) keyword rather than pretending that a
            # local argument enforces a hard timeout.
            if timeout_ms is not None:
                parameters: Mapping[str, inspect.Parameter]
                try:
                    parameters = inspect.signature(self._runner).parameters
                except (TypeError, ValueError):
                    parameters = {}
                accepts_kwargs = any(
                    parameter.kind is inspect.Parameter.VAR_KEYWORD
                    for parameter in parameters.values()
                )
                deadline = time.monotonic() + timeout_ms / 1000
                if "deadline" in parameters or accepts_kwargs:
                    raw = self._runner(request, deadline=deadline)
                elif "timeout_ms" in parameters:
                    raw = self._runner(request, timeout_ms=timeout_ms)
                else:
                    return failure_receipt(
                        request,
                        provider=self.provider_name,
                        revision=self.revision,
                        status=InferenceStatus.TIMEOUT,
                        reason="onnx_runner_not_cooperative",
                        latency_ms=0,
                    )
                if time.monotonic() > deadline:
                    raise TimeoutError
            else:
                raw = self._runner(request)
            result = result_for_role(self.role, raw)
        except TimeoutError:
            return failure_receipt(
                request,
                provider=self.provider_name,
                revision=self.revision,
                status=InferenceStatus.TIMEOUT,
                reason="onnx_inference_timeout",
                latency_ms=(time.monotonic() - started) * 1000,
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
            latency_ms=(time.monotonic() - started) * 1000,
        )


__all__ = ["OnnxInferenceProvider"]
