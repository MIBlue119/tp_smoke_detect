from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from ml.demo.acquisition import (
    AcquisitionBlockedError,
    download_verified,
    inspect_pickle_checkpoint,
    sha256_file,
)


def test_pickle_inspection_never_deserializes(tmp_path: Path) -> None:
    checkpoint = tmp_path / "model.pt"
    with zipfile.ZipFile(checkpoint, "w") as archive:
        archive.writestr("model/data.pkl", b"arbitrary pickle bytes")
    report = inspect_pickle_checkpoint(checkpoint)
    assert report["unsafe_deserialization_warning"] is True
    assert "never" in report["pickle_load"]
    assert "model/data.pkl" in report["static_zip_members"]


def test_download_is_disabled_without_explicit_network_ack(tmp_path: Path) -> None:
    with pytest.raises(AcquisitionBlockedError, match="network disabled"):
        download_verified(
            "https://example.invalid/model.pt",
            tmp_path / "model.pt",
            expected_sha256="0" * 64,
            expected_size=1,
            allow_network=False,
        )


def test_hash_is_exact_and_not_a_path_receipt(tmp_path: Path) -> None:
    asset = tmp_path / "asset.bin"
    asset.write_bytes(b"demo")
    assert sha256_file(asset) == "2a97516c354b68848cdbd8f54a226a0a55b21ed138e207ad6c5cbb9c00aa5aea"
    assert json.dumps({"sha256": sha256_file(asset), "local_artifact_id": "models/x.pt"})
