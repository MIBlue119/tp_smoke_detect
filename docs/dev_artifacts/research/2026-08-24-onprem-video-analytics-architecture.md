# On-premise 20-camera video analytics architecture research

**Research date:** 2026-08-24  
**Scope:** 20 or more RTSP cameras, Python/Docker application layer, NVIDIA DeepStream/Triton where useful, RTX 5090 or L40S class GPU, local audio warning output.  
**Source policy:** external claims in this report are based on first-party NVIDIA, Triton, CUDA, GStreamer, and NVIDIA product documentation. Capacity numbers are explicitly labelled as vendor benchmark evidence or engineering targets; they are not acceptance results for this project.

## Executive recommendation

Use a two-rate pipeline:

1. DeepStream owns RTSP ingest, NVDEC decode, batching, detector inference, tracking, and metadata extraction.
2. A bounded Python decision service owns temporal rules, per-camera calibration, evidence aggregation, and alert policy.
3. Triton serves only the gated crop/verification models (and optionally a VLM). Never send every frame to a VLM.
4. Audio playback is an independent, fail-closed service connected to a fixed local sound device. A failed audio device must not stop video analytics.

For production, select L40S as the default over RTX 5090 when the system is expected to run continuously. NVIDIA describes L40S as designed and supported for 24/7 enterprise data-center operations; it has 48 GB ECC memory, three NVENC and three NVDEC engines, and a 350 W passive design ([L40S product page](https://www.nvidia.com/en-au/data-center/l40s/)). NVIDIA’s DeepStream installation guide also states that enterprise GPUs are highly recommended for 24x7 deployments and that gaming GPUs are not designed for that environment ([DeepStream installation](https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_Installation.html)). RTX 5090 is useful for development, pilot, or a cost-sensitive deployment with explicit hardware burn-in and support-risk acceptance; its official product page lists 32 GB GDDR7 and positions it as a GeForce gaming/creator product ([RTX 5090 product page](https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5090/)).

DeepStream 9.1’s dGPU compatibility table lists Blackwell, Ada, Hopper, Ampere, and Turing platforms, Ubuntu 24.04, CUDA 13.2, TensorRT 10.16.0.72, and driver R595.58.03 for the 9.1 row ([platform compatibility](https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_Installation.html)). Pin this stack in a tested image; do not let a host driver or CUDA update silently invalidate TensorRT engines.

## Proposed deployment topology

```text
camera VLAN (RTSP H.264/H.265)
        |
        v
DeepStream source bins (nvurisrcbin / nvmultiurisrcbin)
        |
        v
NVDEC -> nvstreammux (live-source=1, bounded batch timeout)
        |
        +--> detector (TensorRT/nvinfer or nvinferserver) -> nvtracker
        |                                      |
        |                                      +--> per-track metadata
        |                                                    |
        |                                                    v
        |                                 Python decision service (rules + state)
        |                                     |              |
        |                                     |              +--> event store/metrics
        |                                     v
        |                         bounded verifier queue (crops + 5-frame evidence)
        |                                     |
        |                                     v
        |                              Triton (GPU1 preferred)
        |                                     |
        |                                     v
        |                         smoking / not_smoking / unclear
        |                                     |
        |                                     v
        |                    alert policy -> local audio service -> ALSA -> amplifier
        |
        +--> optional event clip recorder / evidence store (separate retention policy)
```

DeepStream’s `nvstreammux` is the correct first batching boundary: it collects frames from multiple sources, pushes when the batch is full or when `batched-push-timeout` expires, and uses round-robin collection for live streams ([nvstreammux](https://docs.nvidia.com/metropolis/deepstream/9.0/text/DS_plugin_gst-nvstreammux.html)). Set `live-source=1` for RTSP; NVIDIA’s FAQ specifically recommends this for live streams and recommends disabling sink synchronization/QOS when avoiding jitter and unnecessary drops ([DeepStream FAQ](https://docs.nvidia.com/metropolis/deepstream/9.0/text/DS_FAQ.html)).

DeepStream Service Maker now provides both a Python Flow API and lower-level Python Pipeline API ([Service Maker for Python](https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_service_maker_python.html)). Use Python for configuration, control-plane, metadata contracts, and decision logic. Keep the high-rate hot path inside DeepStream plugins/TensorRT; do not copy every decoded frame through Python/OpenCV. A custom C++ plugin may be warranted for expensive crop/feature work, but it should communicate through stable metadata rather than becoming the business-rule owner.

## Capacity and throughput reasoning

### What the official benchmark says

NVIDIA’s DeepStream 9.0 performance page reports end-to-end application measurements that include capture/decode, preprocessing, batching, inference, and postprocessing, with output rendering disabled. In its dGPU table, the L40S result is **615 FPS** for RT-DETR at 640x640 with TensorRT FP16 and NvDCF, and **665 FPS** for TrafficCamNet Transformer Lite with NvDCF ([DeepStream performance](https://docs.nvidia.com/metropolis/deepstream/9.0/text/DS_Performance.html)). The same page warns that rendering, OSD, and tiling consume resources and are disabled for peak measurements.

At 20 cameras x 10 FPS, the primary detector input is 200 FPS; at 15 FPS it is 300 FPS. The published 615 FPS L40S figure is therefore encouraging headroom for a similar detector/tracker workload, but it is not evidence that a custom smoking detector, pose model, crops, storage, RTSP jitter, and VLM all fit simultaneously. The acceptance benchmark must use the project’s actual models and recorded camera streams. Treat 50% or less sustained detector/GPU utilization as an initial engineering target, with p99 queue latency and frame age as the controlling measures, not average FPS alone.

### Recommended capacity envelope

| Stage | Initial design | Backpressure behavior |
|---|---|---|
| RTSP decode | 20 streams, camera-native H.264/H.265, target 10 FPS analytics cadence | Reconnect per source; drop stale decoded frames rather than blocking all sources |
| Primary detector | Batch up to 20; 5–10 FPS per source depending on scene and camera quality | Use tracker between detector frames where validated; keep latest frame per source |
| Pose/object crop stage | Only person tracks and hand-to-mouth candidates | Bounded per-camera queue; discard duplicate/stale crops first |
| Temporal rules | CPU/Python state keyed by camera + track ID | Never block video ingestion on an alert or VLM response |
| Verification/VLM | Sparse, gated requests; 5-frame montage plus selected crops | Hard queue limit, timeout, cancellation; `unclear`/timeout is no-alert |
| Audio | One local playback worker with dedupe/cooldown | Coalesce alerts; persist event even if playback fails |

Use a single L40S first for a proof-of-capacity if the CV models are small. For production, two L40S cards are preferable: GPU0 for DeepStream CV and GPU1 for Triton verification. This is process and failure isolation, not a claim that one 48 GB card cannot run the models. If only one GPU is purchased, reserve explicit memory for the detector before starting Triton; model loading must be measured with the actual engines.

## Batching, scheduling, and backpressure

### DeepStream batching

Set `nvstreammux.batch-size` to the maximum active camera count (20 initially) and use a finite `batched-push-timeout`. A full-batch-only policy makes a slow/dead camera hold healthy cameras; a timeout-only policy lowers batch fill. Start with a timeout around one analytics frame period and tune from measured camera-to-detector latency. The muxer supports runtime source addition/removal and carries source IDs, original dimensions, PTS, and NTP timestamp metadata, which should be retained in the application event contract ([nvstreammux](https://docs.nvidia.com/metropolis/deepstream/9.0/text/DS_plugin_gst-nvstreammux.html)).

Do not put an unbounded queue between decode, inference, and decision stages. Every queue should have an owner, a maximum length, and a drop policy. For live analytics, frame age is more important than processing every frame. Recommended policy:

- decode queue: latest-frame or short bounded queue per camera;
- detector queue: bounded batch queue; reject/drop stale frames while preserving one newest frame per source;
- candidate queue: preserve candidate transitions and evidence windows, but deduplicate by `(camera_id, track_id, event_window)`;
- VLM queue: small global limit plus per-camera fairness; reject new verification work when it would exceed the audio/event latency budget.

### Triton batching

Triton’s dynamic batcher combines stateless requests and lets the operator set preferred batch sizes, queue delay, queue limits, priorities, and timeout actions ([Triton batchers](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/user_guide/batcher.html)). Tune it with the Performance Analyzer and Model Analyzer, which measure throughput, latency, GPU memory, and utilization for different batch and instance configurations ([Model Analyzer](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/user_guide/model_analyzer.html)).

For the crop classifier, use dynamic batching with a small delay budget (start at 0–5 ms and measure) and a bounded queue. For a stateful temporal model, either keep state in the Python decision service or use Triton sequence batching; Triton documents sequence batching as the option for stateful models whose requests must remain on one model instance. Do not use a sequence scheduler merely because the application has track IDs—application state is often easier to test and restart.

For a generative VLM, use a separate Triton/vLLM or TensorRT-LLM service and a strict request budget. Triton’s TensorRT-LLM backend supports inflight batching and paged attention; its multimodal workflow documents supported model families including LLaVA, VILA, LLaVA OneVision, MLLAMA, and Qwen2-VL ([TensorRT-LLM backend](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/tensorrtllm_backend/README.html), [multimodal workflow](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/tensorrtllm_backend/docs/multimodal.html)). Model provenance, license, and government procurement suitability remain project decisions; Triton support does not establish those properties.

The official Triton vLLM deployment guide warns that vLLM greedily consumes up to 90% of GPU memory by default and demonstrates setting `gpu_memory_utilization` to 50% ([vLLM backend deployment](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/tutorials/Quick_Deploy/vLLM/README.html)). Pin this setting explicitly and start the CV pipeline before loading the verifier. A verifier OOM must not take down the detector.

## Model and engine lifecycle

For a detector or pose model that the team trains, use an exportable ONNX -> device-specific TensorRT engine path. NVIDIA TAO’s deployment documentation states that models export to ONNX and that the TensorRT engine must be generated for each target hardware/configuration; changing TensorRT or CUDA versions requires regeneration, and running an engine built against a different version is unsupported ([TAO DetectNet_v2 deployment](https://docs.nvidia.com/tao/tao-toolkit/latest/text/ds_tao/detectnet_v2_ds.html)).

Recommended artifact identity:

```text
model_name
model_version
training_data_manifest_hash
weights_hash
onnx_hash
engine_hash
cuda_version
tensorrt_version
gpu_compute_capability
calibration_manifest_hash
```

Store those fields beside every deployed engine. Use immutable model directories and an explicit promotion step. Triton’s default `NONE` model-control mode loads models at startup; its `POLL` mode can observe partial repository updates and is explicitly not recommended for production. Triton also documents that a failed reload leaves the already-loaded model unchanged, which is useful for a controlled canary/rollback process ([Triton model management](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/user_guide/model_management.html)). Prefer a new version directory, pre-load/health-check, traffic switch, and rollback over editing a live model file.

## Audio alert integration

Audio should be an output sidecar, not part of the batched camera pipeline. The event service emits an `AlertCommand` only after all temporal, evidence, cooldown, and area-policy checks pass. The audio worker:

1. validates the command schema and expiry timestamp;
2. applies deduplication and per-zone cooldown;
3. plays a pre-installed, hashed WAV file;
4. records `accepted`, `started`, `completed`, or `failed` status;
5. exposes a physical/global mute path independent of the AI process.

GStreamer `playbin` can play a URI and allows the application to choose an explicit audio sink; GStreamer documents `alsasink` as the sink for output to a sound card ([playbin](https://gstreamer.freedesktop.org/documentation/playback/playbin.html), [ALSA plugin](https://gstreamer.freedesktop.org/documentation/alsa/index.html)). A minimal audio container can use `playbin uri=file:///... audio-sink=alsasink` or a small explicit pipeline. Use fixed PCM WAV assets to minimize codec dependencies.

DeepStream 9.1 containers do not package libraries needed for some audio parsing and CPU decode/encode operations. The Docker documentation shows mapping `/dev/snd` and warns that those extra packages may need installation ([DeepStream containers](https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_docker_containers.html)). Keep audio in a separate small container with `/dev/snd` access and an explicit device name. This makes audio package updates and sound-device failures independent of GPU analytics.

Fail closed: if evidence is `unclear`, the verifier times out, a camera timestamp is too stale, or the audio device is unhealthy, record the event and suppress the broadcast. This matches the draft’s requirement to minimize false alarms and keeps “no audio” observable rather than silently losing an event.

## Observability and operations

### Required metrics

Expose one Prometheus endpoint for the Python service and scrape Triton’s metrics endpoint. Triton exposes Prometheus metrics at port 8002 by default, including request success/failure, inference and execution counts, pending request count, request/queue/compute latencies, GPU utilization, GPU memory, CPU, and pinned-memory metrics ([Triton metrics](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/user_guide/metrics.html)).

Application metrics should include:

- `camera_up`, reconnect count, last frame time, source PTS age, decode errors;
- input FPS, detector FPS, dropped-frame count and drop reason per camera;
- mux batch size/fill ratio and batch wait time;
- detector/pose/crop/VLM queue depth and age, with p50/p95/p99 latency;
- track count, candidate count, verifier accept/reject/unclear count;
- alert commands accepted/suppressed by policy, cooldown, stale evidence, and audio failure;
- per-camera false-positive review labels and model version;
- GPU temperature, power, memory, utilization, NVDEC utilization, and process restarts.

Use structured JSON logs with a correlation ID spanning `frame_id -> track_id -> candidate_id -> verification_id -> alert_id`. Do not put raw frames, faces, or full crops in metrics or logs. Event evidence should use a separate retention-controlled store with access logging.

For sampled deep traces, Triton provides a tracing subsystem and current documentation is available at [Triton tracing](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/user_guide/trace.html). Keep tracing sampled and disabled or tightly rate-limited by default in a government deployment; it can expose image-derived request payload metadata.

## Failure handling and degradation matrix

| Failure | Detection | Safe response | Recovery |
|---|---|---|---|
| One RTSP source stalls | no frame/PTS progress, source bus error | mark camera unavailable; do not block mux; suppress alerts for that camera | per-source reconnect; operator notification |
| Packet loss/jitter | decode warnings, PTS age, frame drops | prefer RTSP TCP for unstable links; drop stale frames | reconnect with bounded backoff; inspect camera/network |
| Detector/TensorRT error | DeepStream bus/error, process health | restart pipeline/container; no audio from incomplete evidence | reload known-good engine; quarantine bad version |
| Triton unavailable/OOM | readiness, request timeout/failure, GPU memory | continue CV/track path; treat verifier as `unclear`; no alert | restart verifier; revert model version |
| VLM queue saturated | queue age/size and timeout metric | reject low-priority duplicate candidates; preserve event metadata | tune gate, batch, or add verifier GPU |
| Audio device absent | playback open/error status | persist confirmed event and show operator fault; no repeated broadcast retries | device/systemd/container restart; manual test |
| Host/GPU reset | process exit, DCGM/nvidia-smi fault | mark entire node degraded; alert operator; restart services | reboot/failover node if available |
| Model update fails | Triton model status/readiness | retain previous loaded version; abort promotion | fix artifact and repeat canary |

DeepStream’s `nvmultiurisrcbin` can add/remove sensors through a REST API, integrates source bins and streammux, and recommends `live-source=1`, `drop-pipeline-eos=1`, and a maximum batch size equal to the configured stream capacity ([nvmultiurisrcbin](https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_plugin_gst-nvmultiurisrcbin.html)). It is useful for controlled camera lifecycle operations, but secure the API on the camera VLAN and put it behind the project’s own authenticated control plane; do not expose the raw REST port to general networks.

## Interfaces for the Python service

The following logical interfaces keep DeepStream-specific metadata separate from policy logic. Pydantic models can implement these contracts; serialization should be versioned and tested.

```python
class FrameObservation:
    camera_id: str
    frame_id: int
    source_pts_ns: int
    capture_ts_ns: int | None
    received_ts_ns: int
    width: int
    height: int
    tracks: list[TrackObservation]

class TrackObservation:
    track_id: int
    bbox_xyxy: tuple[float, float, float, float]
    keypoints: list[Keypoint] | None
    object_scores: dict[str, float]
    source_frame_ids: list[int]

class VerificationRequest:
    verification_id: str
    camera_id: str
    track_id: int
    event_window_start_ns: int
    event_window_end_ns: int
    evidence_refs: list[EvidenceRef]
    deadline_ns: int
    model_slot: str

class VerificationResult:
    verification_id: str
    model_name: str
    model_version: str
    verdict: Literal["smoking", "not_smoking", "unclear"]
    confidence: float | None
    evidence_channels: list[str]
    latency_ms: float

class AlertCommand:
    alert_id: str
    camera_id: str
    zone_id: str
    created_ts_ns: int
    expires_ts_ns: int
    reason_codes: list[str]
    audio_asset_id: str
    cooldown_key: str
```

The contract must carry source timestamps and model versions. Without those fields, offline replay cannot distinguish camera delay, queue delay, model delay, and policy delay, and an audit cannot explain which model produced an alert.

## Alternatives and risks

### DeepStream + Triton (recommended)

Best fit for 20 RTSP streams and NVIDIA GPUs. It provides hardware decode, multi-source batching, native trackers, TensorRT integration, runtime source management, Triton scheduling, and operational metrics. Main risks are NVIDIA stack pinning, proprietary DeepStream components/licensing review, and the need for a custom plugin when Python metadata access becomes a bottleneck.

### DeepStream + direct TensorRT, no Triton

Use `nvinfer` for all compact CV models and run the verifier as a separate Python/TensorRT process. This reduces operational components and can be easier on one GPU, but loses Triton’s standard model repository, batching controls, readiness, and Prometheus metrics. It is a reasonable pilot fallback, not the preferred production model-slot architecture.

### Pure GStreamer/FFmpeg + ONNX Runtime/TensorRT

Useful if the organization cannot accept DeepStream licensing or needs a broader CPU/GPU portability story. It shifts batching, decode surface handling, tracking, and failure recovery into project code. Expect more engineering and less vendor-tested multi-camera behavior; use only after a pilot demonstrates that DeepStream is blocked by procurement or platform constraints.

### Single RTX 5090

Potentially excellent throughput per dollar for a lab or pilot and likely enough VRAM for compact CV plus a small verifier. Risks are 32 GB rather than L40S’s 48 GB, no ECC, consumer-board thermals/support, and NVIDIA’s explicit 24x7 enterprise-GPU recommendation. Require a 72-hour soak, power/thermal logging, full camera replay, driver/container pinning, spare-card plan, and written acceptance of the support posture before production.

### Single L40S versus two L40S

One L40S may fit the workload, but two cards are operationally safer: isolate CV from VLM, prevent verifier OOM from starving decode, and leave a degraded-mode path. L40S does not provide MIG or NVLink according to NVIDIA’s product specifications, so isolation should be process/GPU assignment rather than assumed hardware partitioning.

## Verification plan before committing to hardware

1. Build the exact DeepStream 9.1 dGPU container with CUDA 13.2/TensorRT 10.16 and verify driver R595.58.03 on both a L40S and the intended RTX 5090 board.
2. Replay 20 synchronized H.264/H.265 camera recordings at 5, 10, and 15 FPS. Include resolution changes, source stalls, RTSP reconnects, packet loss, long-GOP streams, night/IR scenes, and multiple simultaneous people.
3. Measure detector throughput with OSD/tiler disabled and then repeat with required evidence clips. Record per-camera frame age, dropped frames, batch fill, GPU/NVDEC utilization, and p99 end-to-end alert latency.
4. Inject verifier bursts at the worst expected candidate rate. Verify queue limits, timeout behavior, GPU memory ceilings, and that detector latency remains within budget when Triton is unavailable.
5. Exercise audio independently: device unplug/replug, process restart, duplicate alert, global mute, invalid asset, and amplifier failure. Confirm every failed playback is visible in the event log.
6. Replay the frozen validation set for smoking, not-smoking, phone, drinking, eating, betel chewing, nose picking, pen/toothpick, vape, low-resolution, occlusion, and night scenes. Report per-camera and per-behavior precision/recall and the number of broadcasts, not only aggregate model metrics.
7. Freeze the resulting engine hashes, container digest, host driver, camera profiles, thresholds, and benchmark report into the deployment artifact.

## Open decisions for the TPM/PM

- Confirm whether 5090 is allowed for continuous public-sector production or only pilot use.
- Confirm camera codec, frame rate, GOP, bitrate, lens/FOV, and maximum distance per monitored zone; the GPU cannot recover missing pixels.
- Select the verifier model only after an on-site confusion-set benchmark and provenance/license review. Triton’s multimodal support list is an integration starting point, not a procurement approval.
- Decide whether runtime camera add/remove is needed. If yes, expose a project-authenticated API around `nvmultiurisrcbin`, with audit logs and a fixed maximum of 20/24/32 sources.
- Define audio policy for overlapping events, quiet hours, per-zone cooldown, global mute, and what “system unavailable” means to operators.
- Define retention and access control for event clips and crops separately from metrics and model artifacts.

## Source index

All external technical claims in this report link to first-party documentation inline. The principal references are:

- [DeepStream installation and platform compatibility](https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_Installation.html)
- [DeepStream Docker containers](https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_docker_containers.html)
- [DeepStream `nvstreammux`](https://docs.nvidia.com/metropolis/deepstream/9.0/text/DS_plugin_gst-nvstreammux.html)
- [DeepStream `nvmultiurisrcbin`](https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_plugin_gst-nvmultiurisrcbin.html)
- [DeepStream performance](https://docs.nvidia.com/metropolis/deepstream/9.0/text/DS_Performance.html)
- [DeepStream Service Maker Python](https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_service_maker_python.html)
- [Triton overview](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/index.html)
- [Triton batchers](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/user_guide/batcher.html)
- [Triton metrics](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/user_guide/metrics.html)
- [Triton model management](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/user_guide/model_management.html)
- [Triton TensorRT-LLM multimodal workflow](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/tensorrtllm_backend/docs/multimodal.html)
- [CUDA minor-version compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html)
- [NVIDIA L40S](https://www.nvidia.com/en-au/data-center/l40s/)
- [NVIDIA GeForce RTX 5090](https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5090/)
- [GStreamer `playbin`](https://gstreamer.freedesktop.org/documentation/playback/playbin.html)
- [GStreamer ALSA plugin](https://gstreamer.freedesktop.org/documentation/alsa/index.html)

