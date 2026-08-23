# REL-801 — Repository-relative takeover and honest release gates

## Context

U12 had to make the CPU reference service usable by a new coding agent while
preserving GitFlow and preventing a documentation-only release from implying
GPU, model, site, legal, or automatic-audio qualification.

## Symptom

README.md was only a placeholder, AGENTS.md listed a short command set without
runbook ownership, and one qualification receipt contained a host-specific
virtualenv path. Operators lacked a single documented path for safe mute,
incident response, model rollback, and media-free restore.

## Discarded hypotheses

- A Compose config pass is enough to call the release production-ready.
- A CPU replay pass proves 20-camera throughput or model quality.
- A host-local absolute command is reproducible for another agent.
- Automatic mode can be enabled as a convenience during installation.

## Root cause

The project has separate trust boundaries for deterministic CPU behavior,
deployment packaging, target hardware, model provenance, site policy, and
public audio. The takeover docs did not make those boundaries executable.

## Fix

Added a complete agent guide, release-aware README, four runbooks, and an
explicit release receipt. Commands use repository-relative entry points;
external gates remain named blockers; examples preserve shadow/muted defaults;
media, secrets, and weights remain outside Git.

## Verification

Run the commands listed in docs/dev_artifacts/tickets/REL-801.md and update the
release receipt with their actual output. Sol review is required before the
release branch is considered ready.

## Prevention

Keep the runbooks and AGENTS.md in the same review as deployment changes.
Require a release receipt to distinguish local checks from target-host,
provenance, policy, and legal evidence. Do not record host-specific absolute
paths in durable artifacts.

## Related commit

To be filled after REL-801 commit and push.
