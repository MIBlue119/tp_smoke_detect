"""Typed, provider-neutral inference ports.

Providers exchange metadata and structured results only.  A provider may be
backed by ONNX Runtime, Triton, an OpenAI-compatible local endpoint, or the
deterministic fixture adapter; the domain layer never depends on those runtimes.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Protocol, TypeAlias
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)


class InferenceRole(StrEnum):
    """Roles are stable integration seams, not implementation names."""

    POSE = "pose"
    OBJECT = "object"
    SMOKE = "smoke"
    TEMPORAL = "temporal"
    CHEWING_VETO = "chewing_veto"
    VLM = "vlm"


# Kept as an alias for adapters that call this concept a model role.
ModelRole = InferenceRole


class InferenceStatus(StrEnum):
    OK = "ok"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    MALFORMED = "malformed"
    INVALID = "invalid"
    REVISION_MISMATCH = "revision_mismatch"
    ERROR = "error"
    UNCLEAR = "unclear"


class ProviderState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class InferenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def _finite_score(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("score must be finite")
    return value


class PoseInference(InferenceModel):
    hand_to_mouth_distance: float | None = Field(default=None, ge=0)
    mouth_dwell_ms: int | None = Field(default=None, ge=0)
    confidence: float = Field(ge=0, le=1)

    _finite_confidence = field_validator("confidence")(_finite_score)


ObjectLabel = Literal[
    "cigarette",
    "vape",
    "phone",
    "cup",
    "food",
    "pen_toothpick",
    "betel_quid",
    "background",
    "unknown",
]


class ObjectInference(InferenceModel):
    label: ObjectLabel
    confidence: float = Field(ge=0, le=1)

    _finite_confidence = field_validator("confidence")(_finite_score)


class SmokeInference(InferenceModel):
    smoke_score: float = Field(ge=0, le=1)
    ember_score: float = Field(ge=0, le=1)

    _finite_smoke = field_validator("smoke_score")(_finite_score)
    _finite_ember = field_validator("ember_score")(_finite_score)


class TemporalInference(InferenceModel):
    hand_retreat: bool
    cycle_interval_ms: int | None = Field(default=None, ge=0)
    persistence_ms: int = Field(ge=0)


class ChewingVetoInference(InferenceModel):
    positive: bool
    score: float = Field(ge=0, le=1)

    _finite_score = field_validator("score")(_finite_score)


class VLMInference(InferenceModel):
    label: Literal["smoking", "not_smoking", "unclear"]
    score: float | None = Field(default=None, ge=0, le=1)
    reason_code: str = Field(min_length=1, max_length=64)

    _finite_score = field_validator("score")(_finite_score)


RoleResult: TypeAlias = (
    PoseInference
    | ObjectInference
    | SmokeInference
    | TemporalInference
    | ChewingVetoInference
    | VLMInference
)


class InferenceRequest(InferenceModel):
    """A metadata-only request; media is referenced by an artifact identifier."""

    schema_version: Literal["inference.request.v1"] = "inference.request.v1"
    request_id: UUID
    role: InferenceRole
    track_id: str = Field(min_length=1, max_length=128)
    feature_revision: str = Field(min_length=1, max_length=128)
    features: dict[str, StrictStr | StrictInt | StrictFloat | StrictBool | None] = Field(
        default_factory=dict
    )

    @field_validator("features")
    @classmethod
    def reject_non_finite_features(
        cls, value: dict[str, StrictStr | StrictInt | StrictFloat | StrictBool | None]
    ) -> dict[str, StrictStr | StrictInt | StrictFloat | StrictBool | None]:
        for feature_name, feature_value in value.items():
            if isinstance(feature_value, float) and not math.isfinite(feature_value):
                raise ValueError(f"feature {feature_name!r} must be finite")
        return value


class InferenceReceipt(InferenceModel):
    """The only model evidence that may cross into the domain layer."""

    schema_version: Literal["inference.receipt.v1"] = "inference.receipt.v1"
    request_id: UUID
    role: InferenceRole
    provider: str = Field(min_length=1, max_length=128)
    model_revision: str = Field(min_length=1, max_length=256)
    status: InferenceStatus
    result: RoleResult | None = None
    reason_codes: list[str] = Field(default_factory=list)
    latency_ms: float = Field(ge=0)
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    _finite_latency = field_validator("latency_ms")(_finite_score)

    @model_validator(mode="after")
    def enforce_fail_closed_result(self) -> InferenceReceipt:
        if self.status is InferenceStatus.OK and self.result is None:
            raise ValueError("successful receipts require a typed result")
        if self.status is InferenceStatus.OK and self.result is not None:
            expected = {
                InferenceRole.POSE: PoseInference,
                InferenceRole.OBJECT: ObjectInference,
                InferenceRole.SMOKE: SmokeInference,
                InferenceRole.TEMPORAL: TemporalInference,
                InferenceRole.CHEWING_VETO: ChewingVetoInference,
                InferenceRole.VLM: VLMInference,
            }[self.role]
            if not isinstance(self.result, expected):
                raise ValueError("receipt result does not match its model role")
        if self.status is not InferenceStatus.OK and self.result is not None:
            raise ValueError("failed receipts cannot carry positive evidence")
        if self.status is not InferenceStatus.OK and not self.reason_codes:
            raise ValueError("failed receipts require a reason code")
        return self


# Explicit name for callers that use the word envelope in their port API.
InferenceReceiptEnvelope = InferenceReceipt


class InferenceProvider(Protocol):
    """Typed provider port implemented by every runtime adapter."""

    role: InferenceRole
    revision: str
    provider_name: str

    def infer(
        self, request: InferenceRequest, *, timeout_ms: int | None = None
    ) -> InferenceReceipt: ...


ProviderPort = InferenceProvider


def result_for_role(role: InferenceRole, raw: object) -> RoleResult:
    """Validate an adapter payload against the role's constrained result type."""

    result_type: type[RoleResult]
    if role is InferenceRole.POSE:
        result_type = PoseInference
    elif role is InferenceRole.OBJECT:
        result_type = ObjectInference
    elif role is InferenceRole.SMOKE:
        result_type = SmokeInference
    elif role is InferenceRole.TEMPORAL:
        result_type = TemporalInference
    elif role is InferenceRole.CHEWING_VETO:
        result_type = ChewingVetoInference
    else:
        result_type = VLMInference
    return result_type.model_validate(raw)


def failure_receipt(
    request: InferenceRequest,
    *,
    provider: str,
    revision: str,
    status: InferenceStatus,
    reason: str,
    latency_ms: float = 0,
) -> InferenceReceipt:
    """Build a receipt that cannot accidentally be interpreted as evidence."""

    return InferenceReceipt(
        request_id=request.request_id,
        role=request.role,
        provider=provider,
        model_revision=revision,
        status=status,
        reason_codes=[reason],
        latency_ms=max(0, latency_ms),
    )


__all__ = [
    "ChewingVetoInference",
    "InferenceModel",
    "InferenceProvider",
    "InferenceReceipt",
    "InferenceReceiptEnvelope",
    "InferenceRequest",
    "InferenceRole",
    "InferenceStatus",
    "ModelRole",
    "ObjectInference",
    "ObjectLabel",
    "PoseInference",
    "ProviderPort",
    "ProviderState",
    "RoleResult",
    "SmokeInference",
    "TemporalInference",
    "VLMInference",
    "failure_receipt",
    "result_for_role",
]
