# Learning — Audio safety must be enforced twice

## Context

U8 converts a verified smoking decision into a neutral local announcement while
keeping public audio fail-closed during simulation, replay, and shadow runs.

## Symptom / risk

Policy-only suppression is insufficient: retries, stale queued commands, or a
misconfigured adapter could replay an old command or accept arbitrary operator
text after policy approval.

## Root cause

The domain policy and the audio worker have different failure and retry
boundaries. A decision can be correct while delivery is duplicated, delayed,
or directed at an unavailable message asset.

## Fix

Use explicit timestamps and a short expiry on every typed command; derive a
stable command UUID from the decision; validate the fixed message catalogue and
expiry in both fake and HTTP adapters; send the command UUID as the HTTP
idempotency key; persist one immutable receipt per decision with suppression
reason and playback status.

## Verification / prevention

Unit and contract tests prove every suppression path creates no command,
shadow mode never calls the adapter, duplicate delivery produces one accepted
playback, and unknown/expired commands are rejected. Future adapters must
implement `AudioController.send` and preserve the same checks before adding a
device-specific translation.

## Related ticket

ALERT-501 (U8), branch `feature/alert-501-audio-policy`.
