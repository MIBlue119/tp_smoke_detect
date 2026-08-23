# REL-801 second-order release remediation qualification

Date: 2026-08-24
Branch: `release/0.1.0`

## Findings closed

1. Compose named-volume SQLite backup and mounted policy/camera configuration
   now use `backup-deployment`/`backup-runtime`, SQLite online backup, atomic
   host publication, and integrity-checked stopped-volume restore.
2. `duplicate` controller playback is included in audio cap/cooldown history
   and terminal receipt preservation.
3. Native Debug/Release build paths are ignored and qualification leaves a
   clean Git status.
4. Replay event and correlation IDs include recording/source, frame, and
   artifact identity while remaining deterministic.
5. The native producer emits delivery identity and actual built serializer
   output is parsed by the Python v1 contract.
6. HTTP audio disables redirects/proxies, validates DNS immediately before
   send, rejects unsafe results, and connects to the validated IP while
   retaining Host/TLS-SNI identity.

## Verification receipt

| Gate | Result |
| --- | --- |
| `uv run ruff format --check .` | PASS |
| `uv run ruff check .` | PASS |
| `uv run mypy src tests` | PASS |
| `uv run pytest -q` | PASS — 143 passed, 1 target-hardware skip |
| schema regeneration + diff | PASS |
| camera config validation | PASS |
| synthetic demo | PASS — 2 candidates, 0 errors |
| `docker compose ... config --quiet` | PASS |
| `docker compose ... build smoke-detect` | PASS |
| native Debug CTest | PASS — 1/1 |
| native Release CTest | PASS — 1/1 |
| native RelWithDebInfo ASAN/UBSAN CTest | PASS — 1/1 |
| `git diff --check` and clean qualification paths | PASS |

External GPU/DeepStream SDK, target-camera capacity, 24-hour soak, model
quality/provenance, site/legal approvals, and physical audio remain outside
this CPU/reference receipt.
