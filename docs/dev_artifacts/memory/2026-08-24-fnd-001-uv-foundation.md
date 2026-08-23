# FND-001 — CPU foundation delivery notes

## Context

The repository began as documentation only. U1 had to establish a reproducible
CPU install and shared v1 contracts for later service lanes.

## Symptom

The first `uv lock` failed because TOML basic strings interpret `\\.` as an
invalid escape. Once the package built, the repository-wide formatter also
parsed a code sample embedded in an existing research document.

## Discarded hypotheses

- The lock failure was not a missing package or an unavailable Python runtime.
- The research document was not changed to satisfy a code formatter; it is source
  material, not executable Python.

## Root cause

The mypy exclude regex used a TOML basic string with an unescaped backslash, and
Ruff's default discovery included documentation containing illustrative code.

## Fix

Use a TOML literal string for the regex and exclude `docs` from Ruff discovery.
The package itself remains fully linted, formatted, and type checked.

## Verification

`uv lock`, `uv sync --group dev`, Ruff format/check, mypy, unit tests, contract
tests, schema generation/cmp, config validation, and the synthetic CPU command
all pass on the FND-001 branch.

## Prevention

Keep CI entry points in `AGENTS.md` and keep non-executable research artifacts
outside source-tool discovery. Run the lockfile and schema reproducibility proof
before adding behavior to a new foundation.

## Related commit

Filled after the conventional FND-001 commit is created.
