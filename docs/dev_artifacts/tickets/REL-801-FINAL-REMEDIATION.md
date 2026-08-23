# REL-801 final remediation — Sol findings #1–#8

Status: implemented and locally verified on `release/0.1.0`

## Scope

- Removed caller-authored `DecisionCompleted` from the operational evaluation
  request. Evaluation decisions are produced by the local service seam; direct
  deterministic/replay tests continue to construct typed decisions internally.
- Added transactional evaluation-owner fencing. A reclaimed idempotency lease
  cannot insert a decision or complete the old evaluation.
- Made post-commit metrics best-effort so telemetry failure cannot turn a
  committed request into an unkeyed retry.
- Added explicit operator/worker audio-reservation reconciliation. Expired
  pending reservations become a terminal `expired` safety fence through CAS;
  reconciliation never resends audio.
- Rejected naive and late HTTP playback receipts.
- Preserved controller exceptions when receipt finalization also fails, while
  logging the finalization degradation separately.
- Corrected the README's default Compose profile and added canonical Debug and
  Release native CTest gates to `AGENTS.md`.

## Verification

```text
uv run ruff format --check .                         PASS
uv run ruff check .                                  PASS
uv run mypy src tests                                 PASS
uv run pytest -q                                      129 passed, 1 skipped
uv run pytest tests/integration/test_final_release_hardening.py \
  tests/integration/test_decision_to_audio.py -q     17 passed
uv run smoke-detect schema --output <temporary-dir>  PASS (diff clean)
uv run smoke-detect validate-config configs/camera.example.yaml PASS
uv run smoke-detect demo --fixture synthetic        PASS
docker compose -f deploy/compose.yaml config --quiet PASS
cmake ... -DCMAKE_BUILD_TYPE=Debug; ctest         1/1 passed
cmake ... -DCMAKE_BUILD_TYPE=Release; ctest       1/1 passed
```

The focused adversarial suite is 18 passed. The one skipped test is the deliberately disabled target-hardware
qualification. GPU/DeepStream/Triton capacity, model/site quality, image
digest/SBOM, legal/agency, retention, and physical-audio approvals remain
external release gates.

## Sol findings #1–#6 follow-up

The second-order release review identified six additional blockers. This
follow-up closes them with bounded regressions:

- `backup-deployment` snapshots the SQLite database from the Compose named
  volume with SQLite's online backup API, includes both read-only mounted
  configs, publishes atomically on the host, and uses `python3`/runtime
  `python` commands rather than `uv` in the image. `restore-runtime` checks
  integrity and replaces the stopped-volume database atomically.
- Controller `duplicate` playback is terminal safety history for caps,
  cooldown, and receipt preservation.
- Native build directories are ignored, and the native producer emits UUID
  delivery identity plus `producer`/`occurred_at`; a contract test validates
  actual built serializer output with Pydantic.
- Replay identity includes explicit recording/source identity or a stable
  manifest content identity, frame, and artifact identity.
- HTTP audio resolves immediately before send, rejects public/mixed results,
  disables proxies and redirects, and connects to the validated address while
  retaining configured Host/TLS-SNI identity.

Focused follow-up verification: 44 Python tests passed, native Debug and
Release CTest passed, and the built native serializer contract test passed.
