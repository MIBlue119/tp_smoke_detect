# DEMO-201 CUDA adapter memory

## context

The one-video demo had sealed YOLO11 pose and HEIher cigarette checkpoints but
no real CUDA adapter.  The base project environment must stay CPU-safe.

## symptom

The first implementation attempt used a normal process environment, which
contained unrelated API-key variables, and could not safely load pickle-bearing
checkpoints.  The optional export lane also had no ONNX/TensorRT conversion
toolchain.

## discarded hypotheses

- A CUDA-visible PyTorch process alone is sufficient trust evidence.
- A metadata-only provider or Compose CUDA check can prove model execution.
- An ONNX/TensorRT filename is evidence that parity passed.

## root cause

CUDA visibility, model provenance, process isolation, and export parity are
separate gates.  The base `uv` environment intentionally has no PyTorch or
Ultralytics dependency, while the host Python had optional packages but no
complete exporter stack.

## fix

Added lazy adapters with exact hash/size checks, explicit isolated-runtime and
no-network environment gates, sensitive-variable scrubbing, typed normalized
outputs, runtime/VRAM receipts, conservative parity comparison, and explicit
unavailable export receipts.

## verification

CPU-safe tests and static checks pass.  A sanitized subprocess loaded both
sealed checkpoints and ran one real frame on RTX 3090 `cuda:0`; no fake provider
was used.  Pose returned 10 detections and cigarette returned 0 for that frame.
ONNX/TensorRT were honestly recorded unavailable due to missing `onnxscript`
and TensorRT, with no generated artifact accepted.

## prevention

Keep GPU imports lazy, require explicit isolation flags for pickle loads, keep
model/engine bytes under ignored local artifact storage, and require fixed-frame
parity before an export can be referenced by an integration run.

## related commit

DEMO-201 implementation commit (see ticket and branch history).
