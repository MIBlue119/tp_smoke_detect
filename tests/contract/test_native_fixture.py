import json
from pathlib import Path

from tp_smoke_detect.contracts import CandidateEnvelope


def test_native_candidate_fixture_is_v1_contract() -> None:
    path = Path("tests/contract/fixtures/candidate/track.candidate.v1.native.json")
    candidate = CandidateEnvelope.model_validate(json.loads(path.read_text(encoding="utf-8")))
    assert candidate.schema_version == "track.candidate.v1"
    assert candidate.camera_id == "cam-native-01"
    assert "raw_pixels" not in candidate.model_dump()
