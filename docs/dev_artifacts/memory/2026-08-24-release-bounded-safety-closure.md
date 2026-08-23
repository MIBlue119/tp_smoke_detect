# Release bounded safety closure

## Context

The bounded release review checked the second-order fixes on the release branch
and found five remaining P1/P2 defects at the audio, deployment, replay, and
native contract boundaries.

## Symptom

An already-persisted controller `duplicate` could be sent again when cooldown
was disabled. Python's broad `is_private` accepted special-purpose addresses.
Deployment backup and restore paths materialized complete archives or files in
memory. Legacy manifests reused artifact names and collided on event identity.
The native contract test silently skipped when no binary had been built.

## Discarded hypotheses

- Treating every `is_private` result as an approved internal destination is not
  a sufficient security policy; link-local, unspecified, multicast, and
  documentation ranges need explicit treatment.
- A manifest hash alone is not a recording identity when the manifest names
  files in a store whose contents can change.
- A hand-authored native fixture is not proof that the built serializer emits
  the same contract.

## Root cause

The persistence reservation fast path recognized only `accepted`; the network
helper delegated policy to a runtime classification broader than the product
boundary; archive helpers used byte-oriented convenience APIs at process and
member boundaries; and legacy replay parsing had no access to artifact content.
The contract gate encoded binary availability as an optional local convenience.

## Fix

`duplicate` now follows the immutable terminal receipt path and is retained in
history. Address approval is an explicit network allowlist for loopback,
RFC1918, and ULA. File hashing, subprocess stdout, stdin restore, archive
members, and runtime output are streamed in 1 MiB chunks before atomic rename.
Legacy replay IDs are content-scoped with bounded artifact hashing. The native
test raises a clear failure when a built producer is unavailable.

## Verification

Proof-first focused regressions failed on the old implementation and passed
after the change. Full verification passed with 154 Python tests and one
documented target-hardware skip, Ruff, MyPy, schema/config/demo, Compose,
Docker, native Debug/Release, and ASAN/UBSAN CTest.

## Prevention

Keep duplicate and uncertain playback states in safety-history tests, test every
special-purpose address class at network seams, require chunked I/O for backup
and restore APIs, derive replay identity from durable source/content identity,
and run native contract validation only after (and as part of) the native build
gate. Re-run the release review after each boundary change.

## Related commit

`d629854` — `fix(release): close bounded safety findings`.
