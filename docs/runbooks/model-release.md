# Model-release runbook

Model artifacts are optional for the CPU profile and are never downloaded at
runtime. This runbook defines the evidence required before a candidate can be
used on a site. An unresolved provenance, license, ancestry, evaluation,
runtime, SBOM, or rollback field blocks promotion.

## Release record

Create an immutable release record in the approved on-premise registry. It
must include:

- model role, version, content hash, and export/engine hash;
- parent checkpoints, publisher/origin, deployable license, and terms;
- dataset manifest hash, camera-grouped sealed split, and label policy;
- event-level precision/recall, calibration, confusion-class rates, and
  rejection/unclear behavior;
- CUDA, TensorRT, driver, GPU, Python/native runtime, and dependency hashes;
- SBOM and vulnerability dispositions;
- configuration/prompt revision where applicable;
- immutable rollback target and reviewer/approval record.

The model list and release manifest contain identifiers and hashes only, not
private frames or free-form model prose. Keep weights and media in the local
registry/artifact store, not Git.

## Candidate checks

Run deterministic metadata-only checks first:

    uv run ruff check ml tests/unit/ml tests/integration/ml
    uv run mypy ml tests/unit/ml
    uv run pytest tests/unit/ml tests/integration/ml -q
    uv run smoke-detect demo --fixture synthetic

For a real candidate, independently verify source/license documents, hashes,
dataset splits, evaluation report, calibration, and runtime compatibility.
The ml/registry contracts reject missing or invalid provenance; a passing
contract test does not certify commercial rights or agency policy.

Before import, verify exact runtime and image inputs against
deploy/image-pins.yaml, attach SBOMs, and run target-host one-stream and
20-camera gates. Record p95 latency, queue depth, dropped frames, GPU memory,
model timeout rate, and failure isolation. A CPU or synthetic result is not a
capacity receipt.

## Promotion

Promotion is a controlled registry operation:

1. Register an immutable candidate; never overwrite a version directory.
2. Attach the complete manifest and evaluation artifacts.
3. Have model and policy owners review the sealed split and confusion classes,
   including nose touch, phone, drinking, eating, pen/toothpick, steam, vape,
   and heated tobacco where policy requires them.
4. Load the candidate in an isolated runtime and exercise health, replay,
   timeout, malformed-output, and rollback paths.
5. Point candidate to the immutable version only after the receipt is signed.
6. Canary in shadow mode. Compare event-level metrics and operational signals
   before any mode change.
7. A separate site owner may approve champion; automatic audio remains
   unavailable until silent-period and agency gates are complete.

Registry aliases (candidate, champion, rollback) are convenience pointers; the
immutable version and audit record are authoritative. Record alias change,
actor, UTC time, previous version, and new version.

## Rollback

Rollback is preferred to in-place edits:

1. Mute the site and set shadow mode.
2. Confirm previous immutable release and runtime hashes from the manifest.
3. Load and health-check the rollback version in isolation.
4. Point the registry alias to the rollback version and restart only the model
   stage if the deployment supports it.
5. Replay the sealed smoke fixture and verify revision IDs in new decisions.
6. Record old/new hashes, reason, operator, health, metrics, and review.

If the previous release is unavailable or its provenance is incomplete, keep
the system shadow/muted and escalate; do not substitute an unreviewed model.

## Release blockers

The following are explicit blockers for production or automatic audio:

- unresolved REQUIRED-BEFORE-RELEASE image digest or SBOM;
- unknown model parent checkpoint, origin, license, or generated-engine hash;
- missing sealed event-level evaluation or confusion report;
- failed target-hardware, 24-hour, restart, or failure-isolation receipt;
- missing retention/privacy, agency, legal/procurement, or silent-period
  approval;
- inability to reproduce the CPU/replay and rollback evidence.

Record each blocker in the release qualification receipt. Do not remove the
blocker merely because the service starts or a unit test passes.
