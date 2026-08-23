# REL-801 bounded release hardening closure

Status: implemented and pushed on `release/0.1.0`

## Scope

Closed the bounded Sol review at `cedc23306eb466f779304d618fa82010278431d1`:

- persisted controller `duplicate` playback is a terminal receipt and cannot
  invoke the controller again; it remains cooldown/cap safety history;
- HTTP audio destinations allow only explicit loopback, RFC1918, or IPv6 ULA
  ranges and reject link-local, unspecified, multicast, documentation,
  benchmark, reserved, and public addresses consistently;
- deployment backup, runtime archive output, restore input, hashing, and
  extraction use bounded chunks while preserving staging and atomic publish;
- legacy replay manifests derive a stable recording identity from normalized
  manifest metadata plus content digests of referenced artifacts;
- the native contract test fails loudly when neither built serializer is
  available, making the documented build-before-contract gate authoritative.

## Proof-first regressions

Added focused tests for persisted duplicate recovery, special-purpose network
addresses, content-distinct legacy recordings with reused artifact names,
streamed deployment output, and a non-skippable native contract check. The
focused tests were red before the implementation and green afterward.

## Verification

```text
uv run ruff format --check .                                      PASS
uv run ruff check .                                               PASS
uv run mypy src tests                                              PASS
uv run pytest -q                                                   154 passed, 1 skipped
uv run smoke-detect schema --output <temporary-dir>; diff ...      PASS
uv run smoke-detect validate-config configs/camera.example.yaml    PASS
uv run smoke-detect demo --fixture synthetic                       PASS
docker compose -f deploy/compose.yaml config --quiet               PASS
docker build -f deploy/Dockerfile.core -t tp-smoke-detect:release-bounded . PASS
native Debug CTest                                               1/1 PASS
native Release CTest                                              1/1 PASS
native RelWithDebInfo ASAN/UBSAN CTest                            1/1 PASS
```

The target-hardware qualification remains intentionally skipped on CPU-only
hosts. GPU/DeepStream capacity, model/site quality, provenance/SBOM, legal and
physical-audio approvals remain external release gates.

Related commit: `d629854` (`fix(release): close bounded safety findings`).
