# Local MLOps contracts

The modules in this directory are CPU-only and metadata-only. They are intended
to run against an approved local artifact store; camera media and model weights
must never be copied into this repository.

```python
from ml.datasets import build_manifest, split_manifest
from ml.evaluation import evaluate_events
from ml.registry import validate_promotion
```

Use `DatasetItem.source_hash` for the content digest and `source` for the
catalogue decision (not a private filesystem path). `split_manifest` groups by
camera, which also keeps each track together. `evaluate_events` emits `None`
and `not_applicable` for classes with no observations; these values must not be
rendered as a measured zero. A release can be promoted only when all artifact
hashes, approved provenance, a calibration report, SBOM, and rollback target
are present.
