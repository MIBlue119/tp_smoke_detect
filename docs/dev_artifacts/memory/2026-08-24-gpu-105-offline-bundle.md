# GPU-105 memory: offline bundle readiness must be a hard gate

## Context

The deployment needs to run on an air-gapped RTX 3090 host while keeping
DeepStream, Triton, the candidate bus, and the Python policy core separate.

## Symptom

CUDA visibility or a valid Compose file can look healthy even when the model
repository is metadata-only, an engine was built for another runtime, or the
one-stream receipt is absent.

## Root cause

Container orchestration proves process startup, not model provenance, engine
identity, or target-hardware behavior. A tag also does not prove an image
digest or SBOM.

## Fix

`gpu-preflight` validates digest pins, SBOM markers, model/engine hashes, and
the one-stream receipt offline. Triton runs with model control disabled against
a read-only repository; media and candidate services have independent health
checks. The optional VLM is a separate profile that exits until qualified.

## Verification and prevention

Compose profiles are syntax-checked, all runtime networks are internal, MQTT
and Triton have no host ports, and only GPU services receive device
reservations. `export_gpu_bundle.sh` and `import_gpu_bundle.sh` carry archive
hashes and SBOMs. Keep GPU-107's real inference and capacity receipts as a
release gate; never infer them from this preflight.
