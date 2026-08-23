# Replay media fixtures

`VIDEO-301` uses manifest-driven image sequences.  The checked-in CPU fixture
is generated in memory by `smoke-detect demo --fixture synthetic`; no private
camera media, generated weights, or identifying screenshots belong in this
directory.

For a local replay, place only synthetic or redistributable images below the
configured artifact root and reference them by root-relative `artifact_id` in
a JSON manifest.  Supported image suffixes are PNG, JPEG, BMP, and WebP.  MP4
and other video containers are reserved for the DeepStream worker (VIDEO-302)
and are reported as structured `unsupported_media` errors here.
