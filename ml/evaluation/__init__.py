"""Leakage-safe event metrics and calibration reports."""

from .metrics import (
    CalibrationBin,
    EvaluationEvent,
    EventMetricReport,
    aggregate_events,
    evaluate_events,
)

__all__ = [
    "CalibrationBin",
    "EventMetricReport",
    "EvaluationEvent",
    "aggregate_events",
    "evaluate_events",
]
