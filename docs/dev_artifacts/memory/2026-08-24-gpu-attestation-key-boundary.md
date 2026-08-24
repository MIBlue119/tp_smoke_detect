# GPU qualification key and evidence boundary

## context

GPU-107 readiness was being assembled from runtime output while telemetry and
fault children could be given signing-key paths and challenge secrets.

## symptom

An arbitrary command could impersonate the runtime signer or return a convincing
qualification dictionary; a readiness consumer had no independent executor
attestation to verify.

## discarded hypotheses

Adding another child HMAC or a stronger random challenge would not establish
trust because the arbitrary child still received the secret or could fabricate
the signed structure.

## root cause

The executor delegated evidence authentication to the process it was supposed
to measure and accepted a caller-selected command as the qualifying runtime.

## fix

Qualifying commands are selected only from a digest-pinned approved manifest.
Children receive no key, key path, or secret challenge. The executor samples
host GPU/process/container identity and timestamps independently, cross-checks
runtime service metrics, signs the raw evidence, and readiness verifies that
detached attestation with a separate executor key before signing readiness with
its own key.

## verification

Malicious-child environment probe, forged-dictionary readiness probe, approved
manifest tests, and full pytest pass. Physical GPU execution remains an external
qualification gate.

## prevention

Keep executor signing keys outside all runtime/fault containers; require a
manifest digest and separate executor/readiness key IDs in every receipt.

## related commit

Pending worker commit.
