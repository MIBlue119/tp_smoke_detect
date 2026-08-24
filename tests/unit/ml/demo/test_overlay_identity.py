from ml.demo import annotate
from PIL import Image


def test_overlay_emits_full_run_and_model_revisions(monkeypatch) -> None:
    emitted: list[str] = []

    class Draw:
        def rectangle(self, *args, **kwargs):
            return None

        def text(self, _xy, value, **kwargs):
            emitted.append(str(value))

    monkeypatch.setattr(annotate.ImageDraw, "Draw", lambda *_args, **_kwargs: Draw())
    payload = {
        "run_id": "shibuya-GByZa0qbA8A-r1-full-identity",
        "runtime": {"device_name": "RTX3090"},
        "models": [
            {"role": "person_pose", "model_revision": "pose-revision-complete-123456789"},
            {
                "role": "cigarette_detector",
                "model_revision": "cigarette-revision-complete-987654321",
            },
        ],
    }
    annotate._draw_frame(
        Image.new("RGB", (640, 480)), [], payload, {"frame_index": 1, "source_pts_ns": 0}
    )
    header = "\n".join(emitted)
    assert payload["run_id"] in header
    assert payload["models"][0]["model_revision"] in header
    assert payload["models"][1]["model_revision"] in header
