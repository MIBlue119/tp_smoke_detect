import json
import subprocess
from pathlib import Path

from tp_smoke_detect.contracts import CandidateEnvelope


def test_native_candidate_fixture_is_v1_contract() -> None:
    path = Path("tests/contract/fixtures/candidate/track.candidate.v1.native.json")
    candidate = CandidateEnvelope.model_validate(json.loads(path.read_text(encoding="utf-8")))
    assert candidate.schema_version == "track.candidate.v1"
    assert candidate.camera_id == "cam-native-01"
    assert "raw_pixels" not in candidate.model_dump()


def test_built_native_producer_output_is_v1_contract() -> None:
    binaries = (
        Path("native/deepstream/build-debug/media_worker_tests"),
        Path("native/deepstream/build-release/media_worker_tests"),
    )
    binary = next((path for path in binaries if path.is_file()), None)
    if binary is None:
        raise AssertionError(
            "native qualification binary is unavailable; build native/deepstream before "
            "running the contract gate"
        )
    completed = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    parsed = CandidateEnvelope.model_validate(payload)
    assert parsed.producer == "tp-smoke-detect.native-reference"
    assert parsed.event_id is not None
    assert parsed.correlation_id is not None
    assert parsed.occurred_at is not None
