import pytest
from scripts.run_shibuya_demo import _validate_review_hashes


def test_stale_annotation_hash_rejected() -> None:
    review = {"pre_final_annotation_sha256": "a" * 64, "rendered_video_sha256": "b" * 64}
    with pytest.raises(RuntimeError, match="annotation"):
        _validate_review_hashes(review, "c" * 64, "b" * 64)


def test_stale_video_hash_rejected() -> None:
    review = {"pre_final_annotation_sha256": "a" * 64, "rendered_video_sha256": "b" * 64}
    with pytest.raises(RuntimeError, match="video"):
        _validate_review_hashes(review, "a" * 64, "c" * 64)
