"""Contracts and acquisition gates for the private Shibuya baseline demo.

The demo package is deliberately metadata-first.  It never downloads or loads
an artifact merely because a service starts; acquisition is an explicit,
operator-invoked staging action.
"""

from .contracts import (
    DEMO_SCHEMA_VERSION,
    DemoRunReceipt,
    EventState,
    FrameEvidence,
    ModelReceipt,
    SourceReceipt,
    validate_video_annotation,
)
from .fusion import (
    CigaretteDetection,
    DeterministicFusion,
    DeterministicPersonTracker,
    FrameDetections,
    FusionConfig,
    FusionResult,
    PersonDetection,
    baseline_miss_event,
)

__all__ = [
    "DEMO_SCHEMA_VERSION",
    "DemoRunReceipt",
    "EventState",
    "FrameEvidence",
    "ModelReceipt",
    "SourceReceipt",
    "validate_video_annotation",
    "CigaretteDetection",
    "DeterministicFusion",
    "DeterministicPersonTracker",
    "FrameDetections",
    "FusionConfig",
    "FusionResult",
    "PersonDetection",
    "baseline_miss_event",
]
