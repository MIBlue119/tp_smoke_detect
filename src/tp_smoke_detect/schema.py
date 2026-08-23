"""Deterministic JSON Schema export for the versioned wire contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .contracts import AudioCommand, CandidateEnvelope, DecisionCompleted

CONTRACTS: dict[str, type[BaseModel]] = {
    "track.candidate.v1": CandidateEnvelope,
    "decision.completed.v1": DecisionCompleted,
    "audio.command.v1": AudioCommand,
}


def schema_documents() -> dict[str, dict[str, Any]]:
    return {
        name: model.model_json_schema(mode="validation", by_alias=True)
        for name, model in CONTRACTS.items()
    }


def write_schemas(output_dir: Path | str = "schemas/smoke/v1") -> list[Path]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, document in schema_documents().items():
        path = destination / f"{name}.json"
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        written.append(path)
    return written
