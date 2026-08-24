# Mandatory GPU factory certification

Date: 2026-08-24  
Branch: `feature/gpu-mandatory-release`  
Code certification commit: `7c444a4`

## Verdict

The planned GPU software-factory implementation and bounded code review are
complete. Sol's final bounded review passed with no remaining P0, P1, or P2 in
the reviewed trust scope. This is **not** a physical GPU qualification receipt
and must not be represented as a lab-qualified or production-qualified GPU
release.

## Implemented factory

- Additive, pixels-free GPU evidence and revision contracts.
- DeepStream 7.0 RTX 3090 media profile with guarded native integration.
- Immutable model-repository, provenance, license, SBOM, engine-hash, and
  rollback gates.
- MQTT candidate processing through deterministic decision, audit, and safe
  audio orchestration.
- Offline GPU deployment, readiness, monitoring, bounded metrics, and alerts.
- Dataset, hard-negative, sealed evaluation, ablation, and 20-stream replay
  workload contracts.
- Host, one-stream, capacity, fault-injection, and signed qualification tools.
- Code-pinned qualification manifest, allowlisted child environment, separate
  Ed25519 executor/readiness trust domains, and semantic evidence replay.
- Coding-agent takeover instructions, tickets, progress records, and durable
  solved-problem memories.

## Verified code gates

- Full implementation suite at remediation: 220 passed, one expected hardware
  opt-in skip.
- Final focused trust suite: 21 passed.
- Ruff format/check: passed.
- Canonical MyPy: passed.
- Compose CPU/GPU rendering and preflight behavior: passed.
- Native Debug/Release and sanitizer checks passed during integration.
- Reference Docker build passed during remediation.

## External activation blockers

GPU-107 remains fail-closed until all of the following are supplied and the
qualification harness produces a passing signed receipt:

1. The pinned DeepStream/Triton image is fully imported and its SBOM recorded.
2. Approved PeopleNet, pose/hand, SigLIP classifier, and optional reviewer
   artifacts have immutable hashes, licenses, lineage, and target-built engines.
3. Approved local smoking/confounder media and the sealed 20-stream replay pack
   contain real clip hashes under the configured media root.
4. Executor and readiness Ed25519 keys are provisioned under the documented
   separate trust scopes.
5. The RTX 3090 one-stream and two-hour 20-stream gates pass with measured
   FPS, sample coverage, queues, latency, GPU, NVDEC, memory, and fault evidence.

Only after these gates pass may the profile be labelled `gpu-lab-qualified`.
The L40S-class 24-hour gate remains required for a production claim.
