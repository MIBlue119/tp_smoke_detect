# Software factory status — 2026-08-24

## Integrated on `develop`

- FND-001: repository foundation and v1 contracts
- CORE-101: deterministic domain cascade
- API-102: API, persistence, and audit
- MODEL-201: inference provider framework
- VIDEO-301: deterministic replay worker
- MLOPS-401: dataset, evaluation, and release contracts
- ALERT-501: guarded audio policy and adapters
- OPS-601: observability, retention, and degraded modes
- MODEL-202: baseline calibration and VLM ablation tooling
- RQ2: supported 2026 runtime stack research

## Active

- VIDEO-302: native/DeepStream media-worker boundary and reference tests

## Evidence at this checkpoint

- Ruff format: pass
- Ruff lint: pass
- MyPy over `src`, `ml`, and `tests`: pass
- Pytest: 82 passed
- CPU replay demo: 2 deterministic candidates, 0 errors
- Remote: `origin/develop` contains commit `6018e2d`

## External gates

- Target-hardware DeepStream and 20-camera qualification require the accepted NVIDIA host and camera streams.
- Site precision/recall and confusion-class acceptance require consented local footage and a sealed evaluation set.
- Automatic public audio requires the silent-period acceptance report and written agency policy approval.
