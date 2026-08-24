"""Versioned, provider-neutral envelopes shared by every service lane.

The contracts intentionally contain features and references, never raw pixels or
free-form model prose.  Additive changes to v1 must keep existing fields and enum
values valid; breaking changes require a new versioned module and schema directory.
Delivery metadata is emitted by all current producers.  The v1 ingestion models
keep these fields optional so older JSON fixtures and replay records remain
readable; producers must continue to populate them on newly emitted envelopes.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from .domain.models.observations import DomainObservation


class ContractModel(BaseModel):
    """Base model with stable wire formatting and rejection of accidental fields."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)


class Stage(StrEnum):
    DETECTED = "detected"
    QUALITY = "quality"
    POSE = "pose"
    OBJECT = "object"
    TEMPORAL = "temporal"
    REVIEW = "review"
    COMPLETED = "completed"


class CandidateInferenceRole(StrEnum):
    """Stable evidence roles emitted by the GPU media plane."""

    DETECTOR = "detector"
    POSE = "pose"
    OBJECT = "object"
    SMOKE = "smoke"
    TEMPORAL = "temporal"
    REVIEWER = "reviewer"


class CandidateInferenceStatus(StrEnum):
    """Bounded receipt states; only ``ok`` can contribute evidence."""

    OK = "ok"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    MALFORMED = "malformed"
    INVALID = "invalid"
    REVISION_MISMATCH = "revision_mismatch"
    ERROR = "error"
    UNCLEAR = "unclear"


class CandidateInferenceReason(StrEnum):
    """Operator-safe reason codes for a role receipt."""

    NONE = "none"
    NOT_READY = "not_ready"
    TIMEOUT = "timeout"
    QUEUE_EXPIRED = "queue_expired"
    UNAVAILABLE = "unavailable"
    MALFORMED_OUTPUT = "malformed_output"
    REVISION_MISMATCH = "revision_mismatch"
    DEADLINE_EXPIRED = "deadline_expired"
    GPU_OUT_OF_MEMORY = "gpu_out_of_memory"
    PROVIDER_ERROR = "provider_error"


class DeadlineOutcome(StrEnum):
    MET = "met"
    EXPIRED = "expired"
    NOT_APPLICABLE = "not_applicable"


class CandidateOutputSchema(StrEnum):
    NONE = "none"
    DETECTOR_V1 = "detector.v1"
    POSE_V1 = "pose.v1"
    OBJECT_V1 = "object.v1"
    SMOKE_V1 = "smoke.v1"
    TEMPORAL_V1 = "temporal.v1"
    REVIEWER_V1 = "reviewer.v1"


class ReviewerLabel(StrEnum):
    SMOKING = "smoking"
    NOT_SMOKING = "not_smoking"
    UNCLEAR = "unclear"


class ReviewerReasonCode(StrEnum):
    SMOKING_BEHAVIOR = "smoking_behavior"
    NON_SMOKING_BEHAVIOR = "non_smoking_behavior"
    UNCLEAR_VIEW = "unclear_view"


class InferenceReviewerEvidence(ContractModel):
    """Constrained reviewer output; model prose never crosses the boundary."""

    label: ReviewerLabel
    score: Annotated[float, Field(ge=0, le=1)]
    reason_code: ReviewerReasonCode


class DecisionOutcome(StrEnum):
    VERIFIED = "verified"
    REJECTED = "rejected"
    UNCLEAR = "unclear"
    ERROR = "error"


class RunMode(StrEnum):
    SIMULATION = "simulation"
    REPLAY = "replay"
    SHADOW = "shadow"
    HUMAN_CONFIRMED = "human_confirmed"
    AUTOMATIC = "automatic"


class EventMetadata(ContractModel):
    """Delivery identity shared by every broker-visible v1 envelope."""

    event_id: UUID | None = None
    correlation_id: UUID | None = None
    producer: str = Field(default="unknown", min_length=1, max_length=128)
    occurred_at: datetime | None = None


class Point(ContractModel):
    x: Annotated[float, Field(ge=0, le=1)]
    y: Annotated[float, Field(ge=0, le=1)]


class BoundingBox(ContractModel):
    x: Annotated[float, Field(ge=0, le=1)]
    y: Annotated[float, Field(ge=0, le=1)]
    width: Annotated[float, Field(gt=0, le=1)]
    height: Annotated[float, Field(gt=0, le=1)]
    confidence: Annotated[float, Field(ge=0, le=1)]


class Geometry(ContractModel):
    person_box: BoundingBox
    roi_id: str | None = None
    coverage_score: Annotated[float, Field(ge=0, le=1)] = 1.0


class Quality(ContractModel):
    source_width: Annotated[int, Field(gt=0)]
    source_height: Annotated[int, Field(gt=0)]
    face_pixels: Annotated[float, Field(ge=0)]
    crop_pixels: Annotated[float, Field(ge=0)]
    illumination_profile: Literal["day", "night", "mixed", "unknown"]
    eligible: bool
    eligibility_reason: str


class PoseObservation(ContractModel):
    hand_to_mouth_distance: Annotated[float, Field(ge=0)] | None = None
    mouth_dwell_ms: Annotated[int, Field(ge=0)] | None = None
    confidence: Annotated[float, Field(ge=0, le=1)]


class ObjectObservation(ContractModel):
    label: Literal[
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
    confidence: Annotated[float, Field(ge=0, le=1)]


class SmokeObservation(ContractModel):
    smoke_score: Annotated[float, Field(ge=0, le=1)]
    ember_score: Annotated[float, Field(ge=0, le=1)]


class TemporalObservation(ContractModel):
    hand_retreat: bool
    cycle_interval_ms: Annotated[int, Field(ge=0)] | None = None
    persistence_ms: Annotated[int, Field(ge=0)]


class Observations(ContractModel):
    pose: PoseObservation | None = None
    objects: list[ObjectObservation] = Field(default_factory=list)
    smoke: SmokeObservation | None = None
    temporal: TemporalObservation | None = None
    reviewer: InferenceReviewerEvidence | None = None
    independent_channels: list[str] = Field(default_factory=list)


class CandidateInferenceReceipt(ContractModel):
    """Pixels-free receipt for one GPU inference role."""

    role: CandidateInferenceRole
    status: CandidateInferenceStatus
    reason_code: CandidateInferenceReason = CandidateInferenceReason.NONE
    request_id: UUID | None = None
    correlation_id: UUID | None = None
    model_revision: str = Field(min_length=1, max_length=256)
    artifact_revision: str = Field(default="unknown", min_length=1, max_length=256)
    output_schema: CandidateOutputSchema = CandidateOutputSchema.NONE
    deadline_outcome: DeadlineOutcome = DeadlineOutcome.NOT_APPLICABLE
    score: Annotated[float, Field(ge=0, le=1)] | None = None
    reviewer: InferenceReviewerEvidence | None = None

    @model_validator(mode="after")
    def enforce_fail_closed_result(self) -> CandidateInferenceReceipt:
        if self.status is CandidateInferenceStatus.OK:
            if self.reason_code is not CandidateInferenceReason.NONE:
                raise ValueError("successful receipts must use reason_code=none")
            if self.deadline_outcome is DeadlineOutcome.EXPIRED:
                raise ValueError("successful receipts cannot have an expired deadline")
            if self.model_revision == "unknown" or self.artifact_revision == "unknown":
                raise ValueError("successful receipts require immutable revisions")
            if self.request_id is None or self.correlation_id is None:
                raise ValueError("successful receipts require request correlation")
            expected_schema = {
                CandidateInferenceRole.DETECTOR: CandidateOutputSchema.DETECTOR_V1,
                CandidateInferenceRole.POSE: CandidateOutputSchema.POSE_V1,
                CandidateInferenceRole.OBJECT: CandidateOutputSchema.OBJECT_V1,
                CandidateInferenceRole.SMOKE: CandidateOutputSchema.SMOKE_V1,
                CandidateInferenceRole.TEMPORAL: CandidateOutputSchema.TEMPORAL_V1,
                CandidateInferenceRole.REVIEWER: CandidateOutputSchema.REVIEWER_V1,
            }[self.role]
            if self.output_schema is not expected_schema:
                raise ValueError(f"{self.role.value} receipt has the wrong output schema")
            if self.role is CandidateInferenceRole.REVIEWER:
                if self.reviewer is None:
                    raise ValueError("successful reviewer receipts require typed evidence")
            elif self.reviewer is not None:
                raise ValueError("only reviewer receipts may carry reviewer evidence")
        else:
            if self.score is not None or self.reviewer is not None:
                raise ValueError("failed receipts cannot carry positive evidence")
        return self

    @property
    def result(self) -> InferenceReviewerEvidence | None:
        """Compatibility spelling for adapters that call the typed result ``result``."""

        return self.reviewer


# Short aliases keep adapter vocabulary interoperable without changing the
# versioned wire names.  The ports module remains the owner of request/receipt
# transport types for individual providers.
InferenceReceipt = CandidateInferenceReceipt
InferenceRole = CandidateInferenceRole
InferenceStatus = CandidateInferenceStatus
ReviewerEvidence = InferenceReviewerEvidence


class CandidateEnvelope(EventMetadata):
    """The ``track.candidate.v1`` message represented as JSON."""

    schema_version: Literal["track.candidate.v1"] = "track.candidate.v1"
    camera_id: str = Field(min_length=1, max_length=128)
    track_id: str = Field(min_length=1, max_length=128)
    camera_config_revision: str = Field(min_length=1, max_length=128)
    source_pts_ns: Annotated[int, Field(ge=0)]
    capture_ts_ns: Annotated[int, Field(ge=0)]
    received_ts_ns: Annotated[int, Field(ge=0)]
    first_seen_at: datetime
    last_seen_at: datetime
    stage: Stage
    artifact_ids: list[str] = Field(default_factory=list)
    geometry: Geometry
    quality: Quality
    observations: Observations
    inference_receipts: list[CandidateInferenceReceipt] = Field(default_factory=list)
    model_revisions: dict[CandidateInferenceRole, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def enforce_receipt_integrity(self) -> CandidateEnvelope:
        receipts = {receipt.role: receipt for receipt in self.inference_receipts}
        if len(receipts) != len(self.inference_receipts):
            raise ValueError("candidate cannot contain duplicate inference roles")
        for role, revision in self.model_revisions.items():
            if not revision or len(revision) > 256:
                raise ValueError("model revisions must be bounded and non-empty")
            receipt = receipts.get(role)
            if receipt is not None and receipt.model_revision != revision:
                raise ValueError(f"model revision mismatch for role {role.value}")

        positive_by_role: dict[CandidateInferenceRole, bool] = {
            CandidateInferenceRole.DETECTOR: self.geometry.person_box.confidence > 0,
            CandidateInferenceRole.POSE: self.observations.pose is not None
            and self.observations.pose.confidence > 0,
            CandidateInferenceRole.OBJECT: any(
                item.label not in {"background", "unknown"} and item.confidence > 0
                for item in self.observations.objects
            ),
            CandidateInferenceRole.SMOKE: self.observations.smoke is not None
            and (
                self.observations.smoke.smoke_score > 0 or self.observations.smoke.ember_score > 0
            ),
            CandidateInferenceRole.TEMPORAL: self.observations.temporal is not None
            and (
                self.observations.temporal.hand_retreat
                or self.observations.temporal.persistence_ms > 0
            ),
            CandidateInferenceRole.REVIEWER: self.observations.reviewer is not None,
        }
        for role, positive in positive_by_role.items():
            receipt = receipts.get(role)
            if (
                positive
                and receipt is not None
                and receipt.status is not CandidateInferenceStatus.OK
            ):
                raise ValueError(f"failed {role.value} receipt cannot populate evidence")

        reviewer = self.observations.reviewer
        if reviewer is not None:
            receipt = receipts.get(CandidateInferenceRole.REVIEWER)
            if receipt is None or receipt.status is not CandidateInferenceStatus.OK:
                raise ValueError("reviewer observation requires a successful reviewer receipt")
            if receipt.reviewer != reviewer:
                raise ValueError("reviewer observation does not match its receipt")
        return self

    def to_domain_observation(self) -> DomainObservation:
        """Map metadata-only candidate evidence into the deterministic domain."""

        from .domain.models.observations import DomainObservation, OptionalVlmResult

        pose = self.observations.pose
        temporal = self.observations.temporal
        object_observation = self.observations.objects[0] if self.observations.objects else None
        smoke = self.observations.smoke
        reviewer = self.observations.reviewer
        reviewer_receipt = next(
            (
                item
                for item in self.inference_receipts
                if item.role is CandidateInferenceRole.REVIEWER
                and item.status is CandidateInferenceStatus.OK
            ),
            None,
        )
        vlm = None
        if reviewer is not None and reviewer_receipt is not None:
            status_map: dict[ReviewerLabel, Literal["positive", "negative", "unclear"]] = {
                ReviewerLabel.SMOKING: "positive",
                ReviewerLabel.NOT_SMOKING: "negative",
                ReviewerLabel.UNCLEAR: "unclear",
            }
            status = status_map[reviewer.label]
            vlm = OptionalVlmResult(
                status=status,
                score=reviewer.score,
                revision=reviewer_receipt.model_revision,
            )
        # ``independent_channels`` is retained for v1 wire compatibility, but
        # it is never trusted.  Derive the safety channels only from positive
        # observations whose corresponding receipt is successful and bound to
        # this candidate.  A producer cannot smuggle a second channel by
        # writing an arbitrary string into the compatibility field.
        receipt_by_role = {item.role: item for item in self.inference_receipts}
        positive_channels: set[str] = set()
        object_positive = any(
            item.label in {"cigarette", "vape", "heated_tobacco"} and item.confidence > 0
            for item in self.observations.objects
        )
        object_receipt = receipt_by_role.get(CandidateInferenceRole.OBJECT)
        if (
            object_positive
            and object_receipt is not None
            and object_receipt.status is CandidateInferenceStatus.OK
        ):
            positive_channels.add("object")
        smoke_positive = self.observations.smoke is not None and (
            self.observations.smoke.smoke_score > 0 or self.observations.smoke.ember_score > 0
        )
        smoke_receipt = receipt_by_role.get(CandidateInferenceRole.SMOKE)
        if (
            smoke_positive
            and smoke_receipt is not None
            and smoke_receipt.status is CandidateInferenceStatus.OK
        ):
            positive_channels.add("smoke")

        revisions_by_role = dict(
            (role.value, revision) for role, revision in self.model_revisions.items()
        )
        revisions_by_role.update(
            {
                item.role.value: item.model_revision
                for item in self.inference_receipts
                if item.status is CandidateInferenceStatus.OK
            }
        )
        revisions = tuple(sorted(revisions_by_role.items()))
        return DomainObservation(
            timestamp_ns=self.capture_ts_ns,
            event_id=str(self.event_id) if self.event_id is not None else None,
            quality_eligible=self.quality.eligible,
            pose_eligible=pose is not None and pose.confidence > 0,
            hand_to_mouth=pose is not None
            and pose.hand_to_mouth_distance is not None
            and pose.hand_to_mouth_distance > 0,
            contact_target=(
                "mouth"
                if pose is not None
                and pose.hand_to_mouth_distance is not None
                and pose.hand_to_mouth_distance > 0
                else "unknown"
            ),
            mouth_dwell_ms=pose.mouth_dwell_ms or 0 if pose is not None else 0,
            hand_retreat=temporal.hand_retreat if temporal is not None else False,
            object_label=object_observation.label if object_observation is not None else None,
            object_score=object_observation.confidence if object_observation is not None else 0,
            smoke_score=smoke.smoke_score if smoke is not None else 0,
            ember_score=smoke.ember_score if smoke is not None else 0,
            persistence_ms=temporal.persistence_ms if temporal is not None else 0,
            positive_channels=frozenset(positive_channels),
            vlm=vlm,
            model_revisions=revisions,
        )


class EvidenceChannel(ContractModel):
    name: str = Field(min_length=1, max_length=64)
    score: Annotated[float, Field(ge=0, le=1)]
    positive: bool


class DecisionCompleted(ContractModel):
    """The ``decision.completed.v1`` message represented as JSON."""

    schema_version: Literal["decision.completed.v1"] = "decision.completed.v1"
    event_id: UUID | None = None
    correlation_id: UUID | None = None
    producer: str = Field(default="unknown", min_length=1, max_length=128)
    occurred_at: datetime | None = None
    decision_id: UUID
    camera_id: str = Field(min_length=1, max_length=128)
    track_id: str = Field(min_length=1, max_length=128)
    outcome: DecisionOutcome
    reason_codes: list[str] = Field(min_length=1)
    evidence_channels: list[EvidenceChannel] = Field(default_factory=list)
    model_revisions: dict[str, str] = Field(default_factory=dict)
    policy_revision: str = Field(min_length=1, max_length=128)
    latency_ms: Annotated[float, Field(ge=0)]
    mode: RunMode
    audio_eligibility: bool


class AudioCommand(EventMetadata):
    """The ``audio.command.v1`` message represented as JSON."""

    schema_version: Literal["audio.command.v1"] = "audio.command.v1"
    command_id: UUID
    decision_id: UUID
    zone_id: str = Field(min_length=1, max_length=128)
    message_id: str = Field(min_length=1, max_length=128)
    volume_profile: str = Field(min_length=1, max_length=64)
    expires_at: datetime
    policy_revision: str = Field(min_length=1, max_length=128)


SchemaContract = CandidateEnvelope | DecisionCompleted | AudioCommand
