# GPU release review remediation

Status: implemented on `feature/gpu-mandatory-release`; canonical integration is
owned by the parent agent.

The Sol review findings were closed in one bounded slice:

- the custom `media_publisher` executable is built into the GPU image and is the
  Compose entrypoint; native output uses the v1 geometry and enum vocabulary;
- paho-mqtt is lockfile-backed, the candidate health path is shared, retry
  attempts survive MQTT redelivery, and durable decisions are recovered before
  cascade mutation;
- active release manifest revisions are injected into the candidate consumer,
  while independent evidence channels are recomputed from successful receipts;
- qualification evaluates measured sample ratios, latency, queue, FPS, and
  camera coverage values;
- readiness receipts bind schema, manifest/image identity, host/runtime identity,
  source qualification, and freshness;
- TensorRT plan bytes, replay clip bytes, promotion provenance/latency, and all
  offline bundle members are hash/provenance checked;
- native contract tests build the documented path when needed and exercise the
  custom publisher entrypoint.

Verification evidence is recorded by the parent integration run. External GPU
image import, approved model bytes, sealed clips, and physical RTX 3090 runtime
receipts remain deployment prerequisites and intentionally stay blocked here.
