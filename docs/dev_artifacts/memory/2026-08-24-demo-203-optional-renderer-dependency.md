# DEMO-203: optional renderer dependency must be visible to fixture gates

## Context

U4 adds an evidence-only renderer that converts decoded frames into an
annotated H.264 MP4. The repository's first draft put Pillow in the optional
`demo` dependency group because model assets are optional.

## Symptom

The plan's normal fixture command, `uv run pytest tests/integration/demo`,
failed during test collection with `ModuleNotFoundError: No module named PIL`.
The renderer is a checked-in contract path, so a clean development install
must be able to import and exercise it without model weights or GPU packages.

## Root cause

The image codec helper is CPU-only but is imported by the checked-in demo
module. The `demo` group is not installed by the base fixture/test command.

## Fix and verification

Pillow was moved to the base runtime dependencies and the lockfile was
regenerated. This keeps model download/inference optional while making the
CPU-safe renderer executable by the standard fixture gate. Ruff, mypy, and
all six renderer/media integration tests pass.

## Prevention

When a checked-in module is exercised by a standard gate, classify its
dependency separately from optional weights/runtimes. Keep heavyweight model
packages in optional groups, but do not hide a small deterministic codec
dependency behind a group that the gate does not install.

## Related files

- `pyproject.toml`
- `uv.lock`
- `ml/demo/annotate.py`
- `tests/integration/demo/`
