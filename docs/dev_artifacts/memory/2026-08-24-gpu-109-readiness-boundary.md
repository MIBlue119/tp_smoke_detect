# GPU-109 readiness boundary memory

context: The GPU Compose profile had a candidate consumer readiness file in a
private container tmpfs while the aggregate readiness service only checked the
core and Triton endpoints.

symptom: The aggregate GPU endpoint could remain ready after candidate
processing had failed or before the broker consumer had created its ready
file; the vertical slice also had no durable proof of role-revision propagation.

discarded hypotheses: A Docker healthcheck on PID 1 was sufficient; a Compose
`depends_on` health condition would continuously represent runtime readiness;
CUDA visibility was a substitute for candidate-service readiness.

root cause: `depends_on` only gates startup and the candidate ready file was
not shared with the readiness process.

fix: Added the named `gpu_runtime_state` volume, moved the candidate ready
file to `/run/gpu-state/candidate-ready`, and made `gpu_readiness_server.py`
require that file. Added a CPU-runnable end-to-end test for typed receipts,
revision propagation, audit persistence, and shadow audio.

verification: Compose config passed; GPU vertical slice passed 4 tests;
focused GPU/degraded suite passed 13 tests; Ruff checks passed.

prevention: Treat readiness as an explicit contract across service boundaries;
every new GPU component needs an observable readiness signal and a test that
proves missing evidence remains degraded/unqualified.

related commit: GPU-109 integration commit (recorded by the caller).
