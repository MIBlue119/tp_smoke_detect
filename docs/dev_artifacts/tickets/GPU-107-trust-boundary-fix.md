# GPU-107 trust-boundary remediation

## Scope

Close the two P1 qualification gaps: arbitrary runtime/fault commands could
receive signing material, and readiness could be derived from caller-authored
qualification dictionaries.

## Implemented

- Qualifying execution now resolves only a digest-pinned, shell-free command
  from `configs/gpu-qualification-manifest.yaml`; `--runtime-command` and
  `--fault-runtime-command` are explicitly development-only and cannot qualify.
- Qualification and fault children receive no signing key, key path, or secret
  challenge. The executor independently captures GPU samples, process identity,
  runtime identity, timestamps, and service metrics before signing evidence.
- Readiness requires a detached executor attestation with a separate key ID and
  then uses a separate readiness key. Compose mounts executor verification and
  readiness keys separately; executor signing material is not mounted.
- Added probes for malicious children, forged dictionaries, approved-manifest
  enforcement, and development-command non-qualification.

## Verification

```text
uv run pytest tests/contract/test_gpu_recheck.py tests/gpu/test_one_stream.py -q  # 14 passed
uv run pytest -q                                                                  # 217 passed, 1 skipped
python -m py_compile scripts/qualify_gpu.py scripts/gpu_receipts.py scripts/gpu_readiness_server.py scripts/validate_gpu_receipt.py
```

## Remaining external gate

No physical GPU qualification was claimed. The approved manifest command still
requires the target DeepStream/Triton image, local sealed media, models, and
executor/readiness secret provisioning on the qualification host.
