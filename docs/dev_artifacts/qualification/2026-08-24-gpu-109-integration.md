# GPU-109 integration qualification

Status: **gpu-capable-scaffold-unqualified**.

The CPU-runnable vertical slice passes typed candidate receipts from a
DeepStream-shaped producer through deterministic cascade, audit persistence,
bounded metrics, and muted shadow audio. Missing role receipts produce a
durable rejected decision and no audio request.

The Compose graph parses with a shared candidate readiness boundary. The
profile remains unqualified: the pinned DeepStream image is not imported, the
metadata-only model manifest and synthetic replay pack are blocked, and no
real NVDEC, TensorRT, Triton, one-stream, or 20-stream evidence was produced.

## Commands

| Gate | Result |
|---|---|
| `uv run pytest tests/e2e/test_gpu_vertical_slice.py -q` | 4 passed |
| `uv run pytest tests/e2e tests/gpu tests/load/test_gpu_qualification.py tests/integration/test_gpu_model_failures.py tests/integration/test_gpu_degraded_modes.py -q` | 13 passed |
| `docker compose -f deploy/compose.yaml config --quiet` | passed |
| `uv run ruff format --check tests/e2e/test_gpu_vertical_slice.py scripts/gpu_readiness_server.py` | passed |
| `uv run ruff check tests/e2e/test_gpu_vertical_slice.py scripts/gpu_readiness_server.py` | passed |

See the existing GPU-107 receipt for host/image/model/replay blockers. Do not
infer an R11/R12 pass from this artifact.
