from __future__ import annotations

import json
from pathlib import Path

import pytest
from ml.demo.annotate import AnnotationError, load_annotation
from tests.integration.demo.test_annotation_pipeline import _annotation, _source


def test_event_outside_source_duration_is_rejected(tmp_path: Path) -> None:
    source = _source(tmp_path)
    annotation = _annotation(
        source,
        events=[
            {
                "event_id": "late",
                "track_id": "t1",
                "start_pts_ns": 0,
                "end_pts_ns": 3_000_000_000,
                "state": "candidate",
                "reason_codes": ["associated_cigarette"],
            }
        ],
    )
    with pytest.raises(AnnotationError, match="exceeds source duration"):
        load_annotation(annotation)


def test_partial_annotation_is_rejected_before_media_work(tmp_path: Path) -> None:
    source = _source(tmp_path)
    annotation = _annotation(source)
    payload = json.loads(annotation.read_text(encoding="utf-8"))
    payload["runtime"]["fake_provider"] = True
    annotation.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AnnotationError, match="real CUDA"):
        load_annotation(annotation)


def test_checked_in_schema_rejects_missing_required_field(tmp_path: Path) -> None:
    source = _source(tmp_path)
    annotation = _annotation(source)
    payload = json.loads(annotation.read_text(encoding="utf-8"))
    del payload["models"]
    annotation.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AnnotationError, match="schema:  'models' is a required property"):
        load_annotation(annotation)


def test_checked_in_schema_rejects_additional_property(tmp_path: Path) -> None:
    source = _source(tmp_path)
    annotation = _annotation(source)
    payload = json.loads(annotation.read_text(encoding="utf-8"))
    payload["unexpected"] = True
    annotation.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AnnotationError, match="schema:  Additional properties are not allowed"):
        load_annotation(annotation)
