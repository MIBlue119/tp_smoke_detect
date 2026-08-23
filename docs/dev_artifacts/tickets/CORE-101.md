# CORE-101 — Deterministic domain cascade

## Scope

Implemented U2 in the domain-owned paths. The cascade is provider-neutral: an
adapter supplies timestamped, structured `DomainObservation` values and the
domain owns track aggregation, staged transitions, smoking-cycle detection,
veto precedence, evidence-family de-duplication, optional-VLM fail-closed
handling, and final reason codes.

## Delivered files

- `src/tp_smoke_detect/domain/models/`: immutable observations, constrained
  optional VLM results, structured evidence, decisions, transitions, vetoes,
  and stable reason codes.
- `src/tp_smoke_detect/domain/cascade/`: virtual-time approach/dwell/retreat
  cycle detector, ordered track state, retry de-duplication, out-of-order and
  track-gap handling, and the provider-neutral facade.
- `src/tp_smoke_detect/domain/policy/evidence.py`: dominant veto evaluation,
  persistence/cycle gates, independent channel canonicalization, and
  reject-on-unclear VLM behavior.
- `tests/unit/domain/`: proof-first table and virtual-time coverage for U2 and
  the acceptance examples.

## Verification evidence

- `uv run ruff format --check .` — passed.
- `uv run ruff check .` — passed.
- `uv run mypy src tests` — passed (`22` files checked).
- `uv run pytest tests/unit` — passed (`21` tests).
- `uv run pytest tests/contract` — passed (`5` tests).

Coverage includes nose-touch rejection, chewing/phone/drink/food/pen veto
precedence, one-cycle/one-channel rejection, two independent channels,
optional VLM timeout rejection, duplicate retry idempotence, out-of-order
ignoring, gap reset, and deterministic timestamp-driven stage transitions.

## Research and design notes

The implementation follows KTD4/KTD7/KTD9 and the U2 contract in the system
plan. The domain stores only constrained scores, labels, revisions, and reason
codes. It does not import a provider SDK, inspect pixels, consult wall-clock
time, or emit audio commands. Model provenance and provider ports remain U4
work.

## Unresolved blockers

None for U2. U8 owns audio command integration and U3 owns persistence/audit
serialization. Their implementation must preserve the domain's
`audio_eligibility=False` result for rejected and unclear decisions.
