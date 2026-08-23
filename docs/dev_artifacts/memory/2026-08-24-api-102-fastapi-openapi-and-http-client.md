# API-102: FastAPI dependency annotations and local HTTP testing

## Context

API-102 adds a FastAPI edge with repository injection and generated OpenAPI.

## Symptom

Using a function-local `Annotated` dependency alias while the module enabled
postponed annotations caused OpenAPI generation to retain a `ForwardRef("Repo")`
and fail with a Pydantic `TypeAdapter` not-fully-defined error.

## Discarded hypotheses

- The repository protocol was not the cause; replacing the protocol did not
  address the unresolved local alias.
- The route models were valid; the failure happened while resolving dependency
  annotations for OpenAPI, before request validation.

## Root cause

FastAPI/Pydantic could not resolve a nested alias after `from __future__ import
annotations` stringized the local name.

## Fix

Keep the explicit local alias but evaluate annotations in `api/app.py` (remove
postponed annotations there), and cast `app.state.repository` at the dependency
boundary. OpenAPI now generates deterministically.

## Verification

`create_app(SQLiteAuditRepository()).openapi()` succeeds and
`tests/contract/test_openapi.py` checks the complete v1 path set. All 21 tests,
Ruff, and mypy pass.

## Prevention

When adding nested FastAPI dependency aliases, generate OpenAPI as part of the
contract test. Avoid unresolved local aliases under postponed annotations.

## Related commit

Recorded with the API-102 feature commit.
