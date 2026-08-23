# ALERT-501 — Audio policy and adapter integration

## Scope

Implemented U8: a deterministic, provider-neutral audio policy choke point and
safe adapter boundary. Verified decisions can produce only a fixed catalogue
message, and only automatic mode can submit a bounded command. Shadow mode
records `would_announce` without invoking the controller.

## Delivered

- `domain/policy/audio.py`: explicit-time policy with precedence for site/zone/
  camera mute and camera suspension, then safe mute/mode, evidence eligibility,
  quiet hours, hourly and daily caps, and per-zone cooldown. Commands use a
  deterministic UUID5 derived from the decision and expire after a short TTL.
- `ports/audio.py`: typed controller and immutable structured playback receipt.
- `adapters/audio/fake.py`: CPU-safe catalogue-only, expiry-aware, idempotent
  fake controller.
- `adapters/audio/http.py`: site-local HTTP(S) adapter with bounded timeout,
  catalogue validation, expiry validation, JSON-only command payload, and an
  idempotency header.
- `application/request_audio.py`: single application integration point that
  calls the adapter only after policy approval and records one immutable audio
  receipt per decision.
- SQLite audio receipt persistence and API `POST /v1/audio/requests`; mutes,
  caps/cooldown history, and confirmed false-positive reviews feed policy
  context. Existing v1 broker contracts and generated schemas are unchanged.

## Verification evidence

- `uv run ruff format --check` on all changed files — passed.
- `uv run ruff check` on all changed source/tests — passed.
- `uv run mypy src tests` — passed (63 files).
- `uv run pytest -q` — passed (69 tests).
- Proof-first coverage includes insufficient evidence, shadow bypass, each
  mute/suspension/cap/quiet/cooldown suppression, overnight quiet hours,
  deterministic command IDs, adapter expiry/catalogue rejection, duplicate
  delivery, and immutable receipt persistence.

## Notes

The repository's full-tree Ruff check still reports four pre-existing import
ordering findings in U2 domain tests; those files are outside ALERT-501 and
were not changed. The scoped changed-file gate is clean.

## Unresolved blockers

Automatic public audio remains deployment/site-policy gated. The default
profile remains simulation with audio muted, and no raw media or arbitrary
audio text is accepted.
