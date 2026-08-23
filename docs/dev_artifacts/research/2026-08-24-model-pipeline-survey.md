# 2026-08-24 model and pipeline survey

Status: research input for the technical lead and TPM/PM  
Scope: on-premise smoking-behaviour detection for approximately 20 RTSP cameras, with low false alarms, on RTX 5090 or NVIDIA L40S.  
Research date: 2026-08-24 (Asia/Taipei)

This report does not certify legal compliance or model-data provenance. “Non-Chinese-origin” is treated as a procurement requirement and is separated from the weaker claim “published by a non-Chinese company”. Every candidate still needs a component and weight manifest before delivery.

## Executive recommendation

Build a cascade, not a single smoking classifier:

1. Decode and batch all streams with DeepStream; run a person detector and tracker continuously.
2. Run pose/hand-to-mouth geometry and a small object classifier on person tracks. Classes must include named hard negatives: phone, cup, food, pen/toothpick, and betel-quid/chewing.
3. Aggregate evidence over a track window and apply a deterministic smoking-cycle state machine. Require persistence and at least two independent evidence channels before creating an alert candidate.
4. Send only alert candidates to a VLM reviewer. The VLM returns a constrained `smoking | not_smoking | unclear` decision; `unclear` is a rejection, never an alarm.
5. Keep the VLM behind an API/model slot so the deployment can switch models without changing the CV or alert-policy contract.

For a strict clean-lineage first bakeoff, use `PeopleNet Transformer` or `RF-DETR-L` for person detection, MediaPipe Pose/Hand or RF-DETR Keypoint for geometry, SigLIP 2 for a calibrated crop classifier, and compare Microsoft Phi-4-multimodal, Google Gemma 4 12B, Mistral Pixtral 12B, and Liquid LFM2-VL as reviewers. For a NVIDIA-optimized conditional path, benchmark TAO RT-DETR Warehouse plus Cosmos Reason2, but record that Cosmos Reason2 is post-trained from Qwen3-VL.

L40S is the safer production target. It has 48 GB ECC memory and NVIDIA publishes RT-DETR Warehouse throughput of 29 streams at 30 FPS (FP16, DeepStream) on one L40S. RTX 5090 has 32 GB GDDR7; it is a good CV/prototype card, but it is at or below the official minimum memory published for some 8B VLM deployments and has no comparable multi-stream result in the sources reviewed. Use two GPUs (CV on GPU 0, VLM on GPU 1) if the budget allows; otherwise reserve explicit memory and queue limits and measure the complete workload.

## Candidate inventory

| Candidate | Role | Provenance and licence evidence | Feasibility and caveat | Decision |
|---|---|---|---|---|
| NVIDIA PeopleNet / PeopleNet Transformer | Person detection | NVIDIA NGC model cards state commercial use. The transformer card describes a 3-class person/bag/face detector. | DeepStream/TAO/TensorRT integration is mature. Published PeopleNet INT8 throughput is 465 FPS on T4 for the older ResNet34 model; this is inference-only and not an end-to-end guarantee. | Preferred clean deployment baseline for people; verify exact transformer weight manifest and licence notice.
| RF-DETR-N/S/M/L | Person and optional small-object detection | Roboflow’s official repository says core Nano–Large code/models are Apache 2.0; XLarge/2XLarge require PML 1.0. | Official docs report T4 TensorRT FP16 latency of 2.3 ms (N), 3.5 ms (S), 4.4 ms (M), and 6.8 ms (L). Export targets include ONNX/TensorRT. Validate TensorRT export and small cigarette recall on the target cameras. | Strong clean-lineage detector/keypoint candidate; choose Large or Medium after benchmark, not by COCO score alone.
| MediaPipe Pose/Hand/Holistic | Pose and hand landmarks | Google AI Edge repository code is Apache 2.0; the pose pipeline documents 33 landmarks and video tracking. | Lightweight and track-aware; the legacy pose API focuses on the most prominent person, so run it on tracked person crops or use a multi-person wrapper. It does not identify cigarettes. | Good geometry stage; do not use as the person detector for a crowded full frame.
| SigLIP 2 | Crop embedding / calibrated named-negative classifier | Google model card on Hugging Face lists Apache 2.0. | A frozen encoder plus a supervised head is cheaper and easier to calibrate than using VLM text generation on every frame. Exact latency must be measured at the chosen resolution and batch size. | Preferred S2 object/evidence head after local fine-tuning.
| Microsoft Phi-4-multimodal-instruct | VLM reviewer | Microsoft model card lists MIT; model is approximately 6B parameters and BF16 model files are listed at about 12.9 GB. | Fits an L40S and likely leaves room on RTX 5090 for a reviewer-only process, but `trust_remote_code` is required in the documented Transformers path. Vision language support is English; prompts can be Chinese, but this needs validation. | Clean-lineage reviewer candidate; benchmark with short constrained output.
| Google Gemma 4 12B Unified | VLM reviewer | Google DeepMind model card lists Apache 2.0 and 12B BF16 weights; the card describes native image/video input and 256K context. Gemma terms and prohibited-use terms must be shipped with any redistributed derivative. | Approximately 24 GB for BF16 parameters before KV/cache/runtime overhead; comfortable on L40S, tight on RTX 5090 when co-resident with CV. Disable thinking and cap output for this use. | High-quality clean-lineage reviewer candidate; L40S preferred.
| Mistral Pixtral 12B | VLM reviewer | Mistral model card lists Apache 2.0 and 12B decoder plus 400M vision encoder. | The official safetensors file is about 25.4 GB, so co-resident RTX 5090 deployment is not a safe assumption; L40S or a separate VLM GPU is preferred. Published model support is strongest through Mistral/vLLM tooling; test the exact pinned versions. | Clean-lineage fallback; include only if its local hard-negative accuracy justifies the memory cost.
| Liquid LFM2-VL 1.6B / 450M | VLM reviewer or edge fallback | Liquid AI model card lists LFM Open License v1.0, with 1.2B language + 400M vision components for the 1.6B checkpoint. | Designed for low latency and variable resolution; the vendor explicitly says narrow-use-case fine-tuning is recommended and the models are not intended for safety-critical decisions. Very small footprint makes it easy to isolate, but capability on tiny CCTV evidence is unproven. | Include as a fast/low-memory challenger, not as the sole safety gate.
| Meta V-JEPA 2 | Temporal feature encoder / experiment | Official Meta repository describes video representation learning and the V-JEPA 2 repository is predominantly MIT, with some files under Apache 2.0. | It is not a drop-in VLM or detector. A frozen backbone plus a temporal probe could be useful for smoking-cycle classification after enough local video, but requires a separate training/inference path and a licence audit of every checkpoint. | Research track only; do not block the first service release.
| NVIDIA TAO RT-DETR Warehouse | Person/scene detector | NVIDIA documentation identifies the model and supplies DeepStream throughput. The architecture is RT-DETR; the model card/weight publisher is NVIDIA, not the original architecture author. | On L40S: 29 streams at 30 FPS, 59 at 15 FPS in the cited DeepStream/VSS test. Fine-tuning/export path is documented. Warehouse domain shift must be measured on government cameras. | Conditional accelerator path if the procurement rule accepts NVIDIA-retrained weights with non-Chinese architecture ancestry; disclose that ancestry.
| NVIDIA Cosmos Reason2 2B/8B | VLM reviewer | NVIDIA Open Model License says commercial use and derivative models are allowed, subject to notice/terms. NVIDIA’s model card explicitly says Reason2-2B follows Qwen3-VL-2B and Reason2-8B follows Qwen3-VL-8B. | NVIDIA prerequisites list minimum GPU memory of 24 GB (2B) and 32 GB (8B); official docs say BF16 was tested on Hopper/Blackwell. DeepStream provides a vLLM plugin and NVIDIA publishes alert-verification results, but those results are on RTX PRO 6000, not L40S/5090. | Technically attractive and operationally integrated, but conditional on the government’s ancestry policy; never describe it as clean non-Chinese lineage.

### Important provenance distinction

The words “American vendor”, “fine-tuned in the US”, “rebranded”, “quantized”, and “trained from scratch” are not interchangeable. A procurement manifest should contain, for each artifact:

- publisher and training organization;
- base checkpoint and every known parent checkpoint;
- architecture source and weight source;
- training/fine-tuning/quantization operation performed by the publisher;
- data sources and their licences, where disclosed;
- exact revision, SHA-256, container digest, runtime, and licence files.

Cosmos Reason2 is the clearest example: NVIDIA publishes the weights and licence, but its own card names Qwen3-VL as the base. If the contract excludes Chinese-origin model ancestry, it is not a clean candidate even though NVIDIA is the publisher.

## Hardware and latency sizing

### Facts from primary sources

- NVIDIA lists RTX 5090 with 32 GB GDDR7 and Blackwell architecture.
- NVIDIA lists L40S with 48 GB GDDR6 ECC, 864 GB/s bandwidth, and FP8 Tensor Core support.
- NVIDIA’s RT-DETR Warehouse/VSS page reports one L40S at 29 streams at 30 FPS or 59 streams at 15 FPS, using the FP16 ResNet-50 model in DeepStream. This is a detector throughput measurement, not a 20-camera end-to-end service guarantee.
- NVIDIA’s DeepStream VLM plugin performs segmented, asynchronous, batched processing and supports one shared model across multiple streams. Its current reference requirements specify at least 40 GB GPU memory. The plugin exposes selection FPS, segment length, queue size, maximum output tokens, and GPU memory fraction; pin all of these rather than inheriting defaults.
- NVIDIA’s alert-verification benchmark reports 20-stream average 0.63 s and P90 0.95 s on an RTX PRO 6000 Workstation Edition, with RT-DETR and Cosmos Reason2. This validates the cascade pattern, but it must not be copied as a L40S/5090 guarantee.

### Practical sizing table

| Deployment | CV stage | VLM stage | Expected engineering position |
|---|---|---|---|
| One L40S, strict lineage | PeopleNet/RF-DETR + tracker + pose/classifier | Phi 4 or Liquid, event-gated | Most likely to fit; profile decoder, CV buffers, and VLM queue together.
| One L40S, higher reviewer quality | Same | Gemma 4 12B or Pixtral 12B, event-gated | Fits by parameter arithmetic, but reserve headroom for KV cache and image tokens; do not run 20 continuous VLM streams.
| One RTX 5090 | CV detector/tracker/pose/classifier | Liquid or Phi reviewer only | Feasible prototype; 32 GB is tight for 8B reviewers and co-residency. Build separate process and fail-open-to-no-audio on OOM.
| Two GPUs | CV on GPU 0 | Any selected reviewer on GPU 1 | Recommended production topology; isolates reviewer faults and keeps RTSP/CV latency stable.

Parameter-memory estimates are only lower bounds: BF16 parameters cost roughly two bytes each, then add vision activations, KV cache, CUDA/TensorRT buffers, allocator fragmentation, and batching. The service must publish a benchmark with p50/p95/p99 latency, GPU memory high-water mark, dropped frames, queue age, and alert-to-audio latency at 20 streams.

### VLM scheduling rule

At 20 cameras × 10 FPS, the CV path sees roughly 200 frames/s. A VLM should see candidate clips only after track-level gating. Use a bounded per-camera queue, one active review per track, a global concurrency limit, and a deadline. If the queue is full or the deadline expires, emit `review_unavailable` and do not play audio. This prevents a burst of false candidates from starving all cameras.

## False-positive mitigation for this use case

The draft correctly identifies nose picking, betel chewing, phones, eating/drinking, distance, and occlusion as the acceptance risk. The model choice alone cannot solve them.

### Recommended evidence contract

For each person track, retain only short-lived feature evidence by default:

- person box, track ID, camera ID, and quality/coverage score;
- hand and face/pose landmarks with confidence;
- crop-classifier probabilities for `cigarette`, `vape`, `phone`, `cup`, `food`, `pen_toothpick`, `betel_quid`, and `background`;
- temporal features: hand-to-mouth distance normalized by eye distance, mouth dwell, hand retreat, inter-cycle interval, and optional smoke/ember evidence;
- VLM decision, model revision, prompt revision, and evidence-frame hashes.

Use a state machine rather than a frame threshold. A candidate should require a valid hand-to-mouth dwell, hand retreat, a plausible inter-cycle interval, persistence over a track window, and two independent evidence channels. Phone-at-ear, cup/food approach, pen/toothpick, and continuous jaw motion should be explicit negative paths. The exact thresholds belong in per-camera configuration and must be learned from a silent-period evaluation set.

### Betel chewing

The public sources reviewed did not produce a ready-to-deploy CCTV dataset for Taiwanese betel-quid chewing. Existing results found in searches are clinical/oral-image or questionnaire datasets, not the fixed-camera behaviour labels required here. Therefore, collect a local hard-negative set with consent and label jaw motion, hand-to-mouth episodes, red-stain/quid visibility only when visible, and spitting/hand-retreat patterns. Do not claim that a generic “eating” class covers betel chewing.

### VLM reviewer guardrails

- use 3–8 evidence frames or a short clip, not a full stream;
- include camera distance/quality metadata and the named confounders in the prompt;
- require machine-parseable output with a schema and reject extra text;
- set `temperature=0` or the lowest supported deterministic setting and cap output to a few tokens;
- log the prompt/model revision and raw response for offline audit, subject to the retention policy;
- never use a VLM explanation as proof; retain the actual image evidence and CV features.

## Fine-tuning data and MLOps

### Public data is seed material, not acceptance evidence

Useful starting points include:

- Roboflow’s `eating-drinking-mobile-smoking` dataset: the publisher page lists the four relevant classes and CC BY 4.0. Verify the image-level rights and contributor provenance before shipping any derivative.
- SCAU-Smoker-Detection (SCAU-SD), introduced with the HOLT-Net paper as a human–cigarette interaction benchmark. Treat it as research evaluation material until the dataset licence and redistribution terms are obtained from the authors.
- The 2025 CCTV fire-exit study reports 8,124 images across 20 scenarios and 2,708 low-light samples, with a best reported recall of 78.90% and mAP@50 of 83.70%. These are paper results on that paper’s data, not evidence of performance on this project’s cameras.
- Action-Net provides MIT-labelled action images including eating, drinking, calling, and using-phone. It is useful for augmentation/sanity checks, but it is not a substitute for fixed-camera, multi-person, low-light, occluded footage.

Build the project dataset around the actual cameras:

1. Record a consented, access-controlled silent-period corpus across day/night, camera angles, distances, occlusions, clothing, and crowd levels.
2. Sample by track and event, not by adjacent frames. Split by camera/site/person/time so near-duplicate frames cannot leak across train and test.
3. Label positive smoking episodes and named hard negatives; include “unknown/insufficient evidence” and out-of-coverage cases.
4. Keep an immutable, versioned acceptance set with a per-confounder confusion matrix. Never train on the acceptance set.
5. Measure event precision/recall, false audio alerts per camera-day, time-to-alert, missed-event duration, calibration error, and coverage/quality rejection rate. Frame mAP alone is not an acceptance metric.

### Suggested offline loop

```text
capture -> privacy filter -> track/event sampler -> annotation review
       -> dataset manifest + licence/provenance checks
       -> detector/pose/classifier/VLM experiments
       -> camera-held-out evaluation -> calibration/policy simulation
       -> signed model package -> staging shadow run -> silent production
       -> champion/rollback decision
```

Use an on-premise registry such as MLflow with a database-backed backend and object store. MLflow’s current registry guidance recommends aliases/tags instead of deprecated fixed stages; use `candidate`, `champion`, and `rollback` aliases. Tag every version with dataset digest, camera split, code commit, base-model revision, prompt revision, licence manifest, and measured p95/p99 latency. Keep model files and reports in the controlled artifact mount; do not send government video to a hosted registry.

## Proposed bakeoff tickets

These are research/engineering tickets for the TPM to allocate; they are deliberately independent where possible.

| ID | Work | Exit evidence | Dependencies |
|---|---|---|---|
| R-01 | Build provenance/licence manifest for all candidate code, weights, datasets, containers | Signed table with parent checkpoints, hashes, licence files, and unresolved items | None
| R-02 | DeepStream 20-stream CV harness | Reproducible p50/p95/p99 latency, drops, memory, and tracker stability on representative clips | None
| R-03 | Detector bakeoff: PeopleNet Transformer vs RF-DETR M/L vs conditional TAO RT-DETR | Camera-held-out person recall, small-object recall, TensorRT throughput, and artefact manifest | R-01, R-02
| R-04 | Pose/hand geometry bakeoff | Landmark stability and hand-mouth feature error by distance/occlusion | R-02
| R-05 | Named-negative crop classifier | Per-class confusion matrix for cigarette/vape/phone/cup/food/pen/betel/background; calibrated thresholds | R-04, initial labelled set
| R-06 | Temporal policy simulator | Replayable state machine with per-camera thresholds, cooldown, and false-audio budget | R-05
| R-07 | VLM reviewer bakeoff: Phi, Gemma 4, Pixtral, Liquid; optional Cosmos | Same evidence clips, schema compliance, reviewer precision/recall, p95 latency, VRAM high-water mark | R-01, R-06
| R-08 | Betel/nose-picking/phone/eating hard-negative collection | Consent and retention record, annotation guide, held-out confusion pack | None
| R-09 | On-prem MLflow/artifact registry and model manifest | Offline registry, aliases, rollback, checksums, retention/access controls | R-01
| R-10 | Shadow deployment and audio safety circuit | 2–8 week silent report, per-camera metrics, manual review samples, global stop button test | R-02, R-06, R-07, R-09

## Primary sources

- [NVIDIA RTX 5090 specifications](https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5090/)
- [NVIDIA L40S specifications](https://www.nvidia.com/en-gb/data-center/l40s/)
- [NVIDIA RT-DETR Warehouse throughput and deployment guidance](https://docs.nvidia.com/vss/3.2.0/models/rt-detr.html)
- [NVIDIA DeepStream vLLM/VLM plugin](https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_ref_app_vllm_plugin.html)
- [NVIDIA VSS alert-verification benchmark](https://docs.nvidia.com/vss/3.2.0/performance-alert-verification.html)
- [NVIDIA Cosmos Reason2 prerequisites](https://docs.nvidia.com/cosmos/latest/prerequisites.html)
- [NVIDIA Cosmos Reason2 reference and vLLM deployment](https://docs.nvidia.com/cosmos/latest/reason2/reference.html)
- [NVIDIA Cosmos Reason2 8B model card](https://huggingface.co/nvidia/Cosmos-Reason2-8B)
- [NVIDIA Cosmos Reason2 2B model card](https://huggingface.co/nvidia/Cosmos-Reason2-2B)
- [NVIDIA TAO model-zoo overview](https://docs.nvidia.com/tao/tao-toolkit/latest/text/model_zoo/overview.html)
- [NVIDIA PeopleNet model card](https://catalog.ngc.nvidia.com/orgs/nvidia/tao/models/peoplenet/-?_lr=1)
- [Roboflow RF-DETR repository and licence/latency notes](https://github.com/roboflow/rf-detr)
- [Google SigLIP 2 model card](https://huggingface.co/google/siglip2-base-patch16-256)
- [Google MediaPipe pose documentation](https://github.com/google-ai-edge/mediapipe/blob/master/docs/solutions/pose.md)
- [Microsoft Phi-4 multimodal model card](https://huggingface.co/microsoft/Phi-4-multimodal-instruct)
- [Mistral Pixtral 12B model card](https://huggingface.co/mistralai/Pixtral-12B-2409)
- [Google Gemma 4 12B model card](https://huggingface.co/google/gemma-4-12B-it)
- [Google Gemma terms of use](https://ai.google.dev/gemma/terms)
- [Liquid AI LFM2-VL model card](https://huggingface.co/LiquidAI/LFM2-VL-1.6B)
- [Meta V-JEPA 2 repository/licence](https://github.com/facebookresearch/vjepa2)
- [Roboflow smoking/eating/drinking/mobile dataset](https://universe.roboflow.com/truong-dqwgd/eating-drinking-mobile-smoking-pfy54)
- [SCAU-Smoker-Detection paper record](https://www.sciencedirect.com/science/article/pii/S095219762301103X)
- [CCTV fire-exit smoking detection study](https://arxiv.org/abs/2508.11696)
- [Action-Net dataset and MIT licence](https://github.com/OlafenwaMoses/Action-Net)
- [MLflow model registry workflows](https://mlflow.org/docs/latest/ml/model-registry/workflow)

## Evidence gaps to keep visible

- No source reviewed supplies validated smoking-event precision/recall for this project’s cameras.
- No source reviewed supplies an on-target RTX 5090 or L40S end-to-end result for the selected VLM reviewers.
- Cosmos Reason2’s published base is Qwen3-VL; procurement must decide whether this ancestry is allowed.
- Public smoking datasets are not enough to cover Taiwanese betel chewing, nose picking, or camera-specific occlusions; local hard-negative data is mandatory.
- The VLM reviewers are general-purpose or physical-AI models, not certified smoking detectors. Treat every model score as a hypothesis to be evaluated, never an acceptance guarantee.
