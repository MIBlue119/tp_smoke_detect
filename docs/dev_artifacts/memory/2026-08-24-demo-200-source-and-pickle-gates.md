# DEMO-200 source and checkpoint gates

## context

The Shibuya demonstration needed a real source clip and pinned public model
inputs while keeping private media and weights outside Git.

## symptom

The repository had only metadata-only GPU model releases.  The old local
`yt-dlp 2023.12.30` exposed storyboard formats and did not expose playable
video formats.

## discarded hypotheses

- A storyboard image is not a valid source video and cannot support inference.
- A model-card MIT label alone cannot settle the parent YOLO11 terms.
- Loading `.pt` bytes during acquisition is not an acceptable integrity check.

## root cause

The acquisition tool was stale for the current YouTube player path, and both
candidate checkpoints are Torch zip archives containing pickle payloads.

## fix

Pinned `yt-dlp==2026.8.19`, selected exact YouTube format 135, recorded ffprobe
properties and content hash, and added explicit SHA/size gates.  Added static
zip inspection that records the pickle warning without deserialization.  The
HEIher model receipt records MIT model-card terms, YOLO11 parent terms, and
unverified Roboflow dataset ancestry.  The source receipt is private evaluation
only because no reusable YouTube license was exposed.

## verification

The sealed source is 4,203,599 bytes with SHA-256
`32d3ffd7456d0a2f11e3b62a0330328e0541b7bb8e98ce733da5b3c1523facc5`; the
HEIher and YOLO11 bytes match the expected hashes and sizes; ffprobe reports
one 640×480 H.264 stream with no audio; unit and contract tests pass.

## prevention

Keep `local_artifacts/model-demo/` ignored.  Require immutable revisions,
expected sizes, hashes, source rights disposition, and static inspection before
any model adapter can load an artifact.  Never fetch at service startup.

## related commit

Recorded by the DEMO-200 implementation commit on
`feature/model-baseline-shibuya`.
