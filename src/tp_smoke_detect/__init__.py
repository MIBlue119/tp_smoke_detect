"""CPU-safe foundation contracts for the smoke-detection service."""

__version__ = "0.1.0"

from .contracts import AudioCommand, CandidateEnvelope, DecisionCompleted

__all__ = ["AudioCommand", "CandidateEnvelope", "DecisionCompleted", "__version__"]
