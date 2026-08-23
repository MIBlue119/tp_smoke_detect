import json
from pathlib import Path

from tp_smoke_detect.schema import schema_documents, write_schemas


def test_checked_in_v1_schemas_match_generator(tmp_path: Path) -> None:
    generated = write_schemas(tmp_path)

    for path in generated:
        checked_in = Path("schemas/smoke/v1") / path.name
        assert json.loads(path.read_text(encoding="utf-8")) == json.loads(
            checked_in.read_text(encoding="utf-8")
        )


def test_all_v1_contracts_are_named_and_versioned() -> None:
    assert set(schema_documents()) == {
        "track.candidate.v1",
        "decision.completed.v1",
        "audio.command.v1",
    }
    for name, schema in schema_documents().items():
        assert schema["title"]
        assert name.split(".")[-1] == "v1"
