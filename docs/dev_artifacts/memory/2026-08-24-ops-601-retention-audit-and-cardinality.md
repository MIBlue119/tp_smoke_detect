# Durable learning: retention retries need a transaction boundary

## Context

U9 needed to expire raw media, event clips, and metadata independently while
keeping the decision audit available after a clip is removed.

## Symptom

Filesystem deletion and database bookkeeping are separate systems. A process
crash after unlinking a clip can leave the database showing it as live; a retry
must not fail merely because the file is already gone. Conversely, marking an
artifact deleted before writing its audit can lose the audit on a later crash.

## Discarded hypotheses

- A best-effort `deleted` update followed by a separate audit insert is not
  sufficient: the two writes can diverge.
- Putting artifact IDs or correlation IDs into metrics would make dashboards
  useful for one incident but creates unbounded time-series cardinality.

## Root cause

External filesystem state cannot be made transactional with the audit store;
the durable boundary must therefore be the database transition after a safe,
idempotent unlink.

## Fix

`RetentionManager` treats a missing file as successful deletion and calls the
SQLite adapter's `finalize_artifact_deletion`, which marks the artifact deleted
and inserts its successful audit row in one transaction with a uniqueness
constraint. Metrics use only bounded dimensions (`camera_id`, component,
stage, outcome, reason, and other enumerated categories).

## Verification

Focused retention tests verify separate clip and metadata clocks, retained
decision metadata after clip deletion, durable audit presence, and a second
retry after a missing file. Metrics tests reject `track_id` labels and inspect
the Prometheus exposition.

## Prevention

Future PostgreSQL adapters must keep the same transaction and unique-key
semantics. New metric labels require an explicit bounded-cardinality review.

## Related commit

Filled after the implementation commit is created for OPS-601.
