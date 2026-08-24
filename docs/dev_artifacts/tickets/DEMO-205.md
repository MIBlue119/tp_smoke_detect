# DEMO-205 — close Shibuya baseline review findings

Status: implemented on `feature/DEMO-205-review-fixes`; final Sol re-review pending.

## Closed in this slice

- Untrusted `.pt` files are loaded in a reproducible disposable Docker boundary:
  UID `65532`, root filesystem and model input read-only, dedicated output mount,
  `--network none`, dropped capabilities, no inherited environment or credentials.
  Both sealed checkpoints were actually loaded; receipt image ID is
  `sha256:3eb2c8dcbbffe59cb969690c6b48af7e72dde6d0a8a484995c841764902cf2b9`.
- Rendering now validates the checked-in Draft 2020-12 JSON Schema, including
  required and `additionalProperties: false` tests, before publication.
- Completed manual review is embedded in the annotation and copied as an
  immutable companion. The final manifest links annotation, review, contact
  sheet, video, boundary receipt, and runtime receipt by SHA-256.
- Overlay contains source decoder PTS timestamp and shortened exact model
  revisions, has one bounded summary panel, and draws at most four prioritized
  entries to prevent clutter.
- Decoder uses PyAV frame PTS/time-base when available; fallback is explicit.
  The actual run processed 1,248 frames on the RTX 3090 with `fake_provider=false`.
- Entry/exit hysteresis is enforced and covered by a low-score active-candidate test.
- Runtime receipt records Python/package versions; `requirements-demo-gpu.lock`
  supplies exact demo pins. No sibling-worktree virtualenv is used by the run.

## Actual output

Run root (ignored local media):
`/mnt/HDD2/proj_walnutek/tp_smoke_detect/.local-demo-inputs/runs/shibuya-GByZa0qbA8A-r1/`

| artifact | SHA-256 | result |
|---|---|---|
| `annotated.mp4` | `b6ce94c632bb0e4eecda2bb5e2e3aa41b686d719c73d563d3d22a94e163db7cd` | H.264/yuv420p, 640x480, 41.6416s, 1,248 frames, 0 audio, 10,899,217 bytes |
| `annotation.json` | `fac3892cdde56b1eee8d8fe3a0a8b5a045622df8801ca5f2412a2390bc6d6631` | 1,248 PTS-bound evidence frames, 0 candidate events |
| `manual-review.json` | `bd1ce9faca78707483d7878c24b038cd14d80c23ffd982dae147cb1d73f7ffd0` | 21 completed `unclear` anchors; no pending judgments |
| `contact-sheet.jpg` | `50345ddf0c8fecdbe818e42b8b1b7fde7215f8f4a641276f9c1435fb0a74e1dc` | regenerated from final MP4 |

The model remains an uncalibrated baseline, not a production qualification or
accuracy claim. RT-DETR fine-tuning is follow-up only if a broader labelled
evaluation establishes visible misses; this single test clip must not be used
for training.

## Verification

- Boundary script: passed; both pinned checkpoint hashes matched.
- CUDA smoke: passed on one frame; full run: passed on RTX 3090.
- Targeted demo/model tests: 17 passed.
- Ruff targeted checks and mypy for `ml/demo` plus runner: passed.
- Full repository collection was attempted with `PYTHONPATH=src:.`; unrelated
  environment blockers remain (FastAPI/Starlette mismatch in this conda env and
  the pre-existing namespace import issue when `src` is omitted).
