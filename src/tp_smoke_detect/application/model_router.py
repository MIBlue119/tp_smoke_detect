"""Role routing, readiness, and circuit isolation for inference providers."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from ..ports.inference import (
    InferenceProvider,
    InferenceReceipt,
    InferenceRequest,
    InferenceRole,
    InferenceStatus,
    ProviderState,
    failure_receipt,
)


@dataclass
class _Circuit:
    state: ProviderState = ProviderState.CLOSED
    failures: int = 0
    opened_at: float = 0.0


class ModelRouter:
    """Keep provider failures local to a role while preserving core liveness."""

    def __init__(
        self,
        providers: Mapping[InferenceRole, InferenceProvider],
        *,
        timeout_ms: int = 500,
        failure_threshold: int = 3,
        recovery_timeout_s: float = 30.0,
        expected_revisions: Mapping[InferenceRole, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if timeout_ms < 1:
            raise ValueError("timeout_ms must be positive")
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be positive")
        if recovery_timeout_s <= 0:
            raise ValueError("recovery_timeout_s must be positive")
        self._providers = dict(providers)
        self._timeout_ms = timeout_ms
        self._failure_threshold = failure_threshold
        self._recovery_timeout_s = recovery_timeout_s
        self._expected_revisions = dict(expected_revisions or {})
        self._clock = clock
        self._circuits = {role: _Circuit() for role in self._providers}

    def liveness(self) -> bool:
        """Liveness is process health; a degraded model role is not a crash."""

        return True

    def readiness(self) -> dict[InferenceRole, bool]:
        """Return role-level readiness without making a provider inference call."""

        return {
            role: circuit.state is not ProviderState.OPEN
            for role, circuit in self._circuits.items()
        }

    def route(self, role: InferenceRole) -> InferenceProvider | None:
        """Expose the selected provider for diagnostics, never for domain policy."""

        return self._providers.get(role)

    def infer(self, request: InferenceRequest) -> InferenceReceipt:
        provider = self._providers.get(request.role)
        if provider is None:
            return failure_receipt(
                request,
                provider="router",
                revision="unavailable",
                status=InferenceStatus.UNAVAILABLE,
                reason="role_provider_not_configured",
            )
        circuit = self._circuits[request.role]
        now = self._clock()
        if circuit.state is ProviderState.OPEN:
            if now - circuit.opened_at < self._recovery_timeout_s:
                return failure_receipt(
                    request,
                    provider=getattr(provider, "provider_name", "provider"),
                    revision=provider.revision,
                    status=InferenceStatus.UNAVAILABLE,
                    reason="provider_circuit_open",
                )
            circuit.state = ProviderState.HALF_OPEN

        try:
            receipt = provider.infer(request, timeout_ms=self._timeout_ms)
        except Exception:
            # A runtime must not be able to take down the AI core process.
            receipt = failure_receipt(
                request,
                provider=getattr(provider, "provider_name", "provider"),
                revision=provider.revision,
                status=InferenceStatus.ERROR,
                reason="provider_exception",
            )

        expected = self._expected_revisions.get(request.role)
        if (
            receipt.status is InferenceStatus.OK
            and expected is not None
            and receipt.model_revision != expected
        ):
            receipt = failure_receipt(
                request,
                provider=receipt.provider,
                revision=receipt.model_revision,
                status=InferenceStatus.REVISION_MISMATCH,
                reason="router_revision_mismatch",
                latency_ms=receipt.latency_ms,
            )
        self._record(circuit, receipt.status, now)
        return receipt

    def _record(self, circuit: _Circuit, status: InferenceStatus, now: float) -> None:
        if status is InferenceStatus.OK:
            circuit.state = ProviderState.CLOSED
            circuit.failures = 0
            return
        if status is InferenceStatus.UNCLEAR:
            return
        circuit.failures += 1
        if circuit.failures >= self._failure_threshold:
            circuit.state = ProviderState.OPEN
            circuit.opened_at = now


__all__ = ["ModelRouter"]
