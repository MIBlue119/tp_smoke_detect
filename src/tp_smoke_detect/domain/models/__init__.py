"""Provider-neutral domain data structures.

These structures contain structured inference output only.  Raw pixels and
free-form provider explanations are deliberately not part of the domain API.
"""

from .decisions import CascadeDecision, EvidenceSignal, ReasonCode, StageTransition, VetoSignal
from .observations import DomainObservation, OptionalVlmResult

__all__ = [
    "CascadeDecision",
    "DomainObservation",
    "EvidenceSignal",
    "OptionalVlmResult",
    "ReasonCode",
    "StageTransition",
    "VetoSignal",
]
