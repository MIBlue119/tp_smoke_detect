from ml.datasets import DatasetItem, build_manifest, split_manifest

HASH_A = "a" * 64


def item(item_id: str, camera: str, track: str, label: str = "smoking") -> DatasetItem:
    return DatasetItem(
        item_id=item_id,
        event_id=f"event-{item_id}",
        camera_id=camera,
        track_id=track,
        label=label,
        source_hash=HASH_A,
        license="CC-BY-4.0",
        source="fixture:public",
    )


def test_split_is_deterministic_and_keeps_camera_and_track_together() -> None:
    manifest = build_manifest(
        "fixture", "1", [item("1", "cam-a", "track-a"), item("2", "cam-a", "track-a")]
    )
    first = split_manifest(manifest, seed=4)
    second = split_manifest(manifest, seed=4)
    assert first.to_json() == second.to_json()
    non_empty = [split for split in first.splits if split.item_ids]
    assert len(non_empty) == 1
    assert non_empty[0].camera_ids == ("cam-a",)


def test_missing_or_invalid_provenance_hash_is_rejected() -> None:
    try:
        item("1", "cam-a", "track-a", "phone").__class__(
            item_id="1",
            event_id="event-1",
            camera_id="cam-a",
            track_id="track-a",
            label="phone",
            source_hash="missing",
            license="CC-BY-4.0",
            source="fixture:public",
        )
    except ValueError as error:
        assert "source_hash" in str(error)
    else:
        raise AssertionError("invalid provenance hash was accepted")
