# Release final remediation: trust boundaries and in-doubt side effects

## context

Sol's release review found that the CPU/reference service had closed most race
conditions but still allowed unsafe authority and ambiguous side-effect states.

## symptom

An operational caller could submit a typed final decision, a reclaimed
evaluation owner could insert a decision after replacement, metric failure could
make a committed request look failed, and expired audio reservations had no
safe operator transition. HTTP receipts could also claim acceptance after the
command lifetime.

## discarded hypotheses

- Retaining the request decision but forcing `audio_eligibility=False` was not
  sufficient: the caller still controlled the audit decision itself.
- Automatically releasing an expired audio lease was rejected because the
  controller request may still be in flight and a retry could duplicate audio.
- Retrying telemetry synchronously was rejected because telemetry is not part
  of the durable decision transaction.

## root cause

The API request model crossed the trusted decision boundary, evaluation
completion used row identity without a transactional claim check, and the
audio reservation protocol treated expiry as ownership loss even though the
external controller has no cancellation guarantee.

## fix

The API now rejects caller-authored decision fields. SQLite completion checks
the still-running evaluation owner and idempotency key inside `BEGIN IMMEDIATE`
before inserting the decision. Metrics are wrapped as best-effort post-commit
observations. Audio reconciliation is explicit, actor/reason-audited, and CAS
transitions pending to terminal `expired`, which remains a resend fence. The
HTTP adapter validates aware receipt time and command expiry, and controller
errors remain the primary exception if finalization fails.

## verification

The focused adversarial tests cover all of the above, with 17 passing. The full
suite has 128 passing and one expected target-hardware skip. Ruff, MyPy, schema
generation, config validation, synthetic demo, Compose config, and native Debug
and Release CTest all pass.

## prevention

Keep trust-boundary request models separate from internal contracts. Every
external side effect needs an owner token, a bounded lease, an explicit
in-doubt state, and a CAS finalizer. Keep post-commit telemetry out of request
success/failure semantics. The coding-agent gates now run both native
optimization profiles.

## related commit

Recorded with the release hardening commit that closes Sol findings #1–#8.
