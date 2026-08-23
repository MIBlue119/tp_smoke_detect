# Release second-order safety findings

## context

Sol's release review of `release/0.1.0` found six residual defects across
deployment backup, audio history, replay identity, native delivery contracts,
HTTP egress, and clean-tree qualification.

## symptom

The documented backup path addressed a host directory that was not the
Compose named volume and copied live SQLite files without an online snapshot.
Duplicate controller acknowledgements were not counted as audio history.
Replay IDs could collide between zero-based recordings. The native serializer
omitted delivery identity. HTTP validation happened at construction and did
not pin the later connection or disable redirects. Native qualification
created untracked in-tree build directories.

## discarded hypotheses

- A normal file copy was not treated as a valid WAL-safe backup; SQLite's
  online backup API is required for a consistent deployed snapshot.
- An allowlist alone was not treated as DNS rebinding protection; the socket
  must connect to the just-validated address and the adapter must reject
  redirects and proxy routing.
- A hand-authored native JSON fixture was not treated as proof of the native
  producer; the built serializer output must pass the Python contract.

## root cause

The runbook described the pre-Compose filesystem rather than the deployed
mounts, controller terminal states were not normalized into one safety set,
identity derivation omitted source scope, and the native/network boundaries
were tested through surrogates rather than the actual output/connect path.

## fix

Added `backup-deployment`, `backup-runtime`, and `restore-runtime` flows with
an atomic host publication, SQLite online backup, mounted policy/camera
capture, in-volume integrity-checked restore, and no runtime `uv` dependency.
Counted and preserved `duplicate` playback. Added recording/content identity
to replay UUID inputs. Added UUID delivery fields and deterministic IDs to the
native envelope plus a built-output contract test. Added pinned-address HTTP(S)
connections with no proxy/redirect support and immediate safe DNS validation.
Ignored the documented native build directories.

## verification

- 44 focused Python tests passed, including backup, duplicate, replay, HTTP,
  and release-hardening regressions.
- `uv run mypy src tests` passed.
- Native Debug and Release builds/CTest passed.
- Built native output passed `CandidateEnvelope` validation.
- A real local HTTP server accepted a pinned loopback request.

## prevention

Keep deployment runbooks executable against actual Compose mounts, make every
external side effect a durable terminal safety fact, include source identity
in deterministic IDs, validate real producer output against versioned
contracts, and pin network validation to the connection path. Keep native
qualification output outside Git status or ignore the exact canonical paths.

## related commit

The containing release branch commit is recorded after the full gate matrix.
