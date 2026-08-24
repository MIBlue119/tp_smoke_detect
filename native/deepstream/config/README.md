# DeepStream 7.0 media profile

These files are configuration *shapes*, not model artifacts. The runtime
requires the exact model and engine files from the immutable model repository
before it will start. Missing files, missing DeepStream elements, unresolved
revisions, or missing MediaPipe GPU plugins produce an `unqualified` result;
they never become a GPU-ready claim.

The native graph is:

```text
nvurisrcbin (RTSP/file, NVDEC, reconnect)
  -> nvstreammux (bounded batch)
  -> nvinfer (PeopleNet Transformer)
  -> nvtracker (NvDCF)
  -> tpmediapipepose (MediaPipe Tasks C++ GPU/EGL)
  -> tpmediapipehands (MediaPipe Tasks C++ GPU/EGL)
  -> nvinferserver (SigLIP 2 crop model, GPU-local)
```

`tpmediapipepose` and `tpmediapipehands` are intentionally explicit plugin
boundaries. An `identity` element or a CPU landmark implementation must not be
substituted for either role. The plugin must attach typed, correlated receipts
to the DeepStream buffer metadata before a candidate can become complete.

The container profile must provide the elements and the configuration paths
through a checked-in bundle. This checkout does not contain weights, TensorRT
plans, private media, or proprietary SDK files.
