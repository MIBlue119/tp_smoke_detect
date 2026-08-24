"""Fail-closed training and calibration receipts for the GPU crop head.

This module intentionally does not import a deep-learning framework.  It
creates a reproducible calibration receipt from an approved non-sealed event
view and records a blocked state when weights or dataset evidence are absent.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ml.evaluation.ablation import CalibrationResult, calibrate_threshold
from ml.evaluation.metrics import EvaluationEvent


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class CalibrationAccessError(ValueError):
    """Raised when training or calibration is given sealed acceptance labels."""


@dataclass(frozen=True, slots=True)
class TrainingReceipt:
    model_id: str
    dataset_manifest_sha256: str
    calibration: CalibrationResult
    seed: int
    training_config_sha256: str
    model_sha256: str | None
    state: str
    reason: str | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": "ml.training-receipt.v1",
            "model_id": self.model_id,
            "dataset_manifest_sha256": self.dataset_manifest_sha256,
            "calibration": self.calibration.to_dict(),
            "seed": self.seed,
            "training_config_sha256": self.training_config_sha256,
            "model_sha256": self.model_sha256,
            "state": self.state,
            "reason": self.reason,
        }

    @property
    def receipt_sha256(self) -> str:
        return hashlib.sha256(_canonical(self.payload()).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        result = self.payload()
        result["receipt_sha256"] = self.receipt_sha256
        return result


def _reject_sealed(split_name: str) -> None:
    if split_name.lower() in {"sealed", "sealed_test", "acceptance", "test_sealed"}:
        raise CalibrationAccessError("sealed acceptance labels are unavailable to calibration")


def calibrate_training_split(
    events: Iterable[EvaluationEvent],
    *,
    split_name: str,
    dataset_manifest_sha256: str,
    model_id: str = "siglip2-crop-head",
    seed: int = 0,
    training_config: dict[str, Any] | None = None,
    target_precision: float | None = None,
) -> TrainingReceipt:
    """Calibrate only a named train/validation split and emit an auditable receipt."""

    _reject_sealed(split_name)
    if not dataset_manifest_sha256 or len(dataset_manifest_sha256) != 64:
        raise ValueError("dataset_manifest_sha256 must be a manifest digest")
    rows = tuple(events)
    config_digest = hashlib.sha256(
        _canonical(training_config or {"method": "threshold", "split": split_name}).encode()
    ).hexdigest()
    calibration = calibrate_threshold(rows, target_precision=target_precision)
    state = "calibrated" if calibration.calibration_state == "ok" else "blocked"
    reason = None if state == "calibrated" else f"calibration_state:{calibration.calibration_state}"
    return TrainingReceipt(
        model_id,
        dataset_manifest_sha256,
        calibration,
        seed,
        config_digest,
        None,
        state,
        reason,
    )


def block_without_weights(
    *,
    model_id: str,
    dataset_manifest_sha256: str,
    calibration: CalibrationResult,
    seed: int,
    training_config: dict[str, Any],
    reason: str = "approved model weights are unavailable in the local bundle",
) -> TrainingReceipt:
    """Record missing weights without inventing a model hash or metric."""

    config_digest = hashlib.sha256(_canonical(training_config).encode("utf-8")).hexdigest()
    return TrainingReceipt(
        model_id,
        dataset_manifest_sha256,
        calibration,
        seed,
        config_digest,
        None,
        "blocked",
        reason,
    )


__all__ = [
    "CalibrationAccessError",
    "TrainingReceipt",
    "block_without_weights",
    "calibrate_training_split",
]
