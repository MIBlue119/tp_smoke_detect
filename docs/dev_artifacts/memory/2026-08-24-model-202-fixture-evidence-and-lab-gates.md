# MODEL-202 memory — fixture evidence must not become site accuracy

## Context

U10 needs a reproducible baseline and a VLM ablation before approved weights,
GPU runtime qualification, or site-labelled data are available.

## Symptom

A deterministic provider and metadata-only event rows can produce complete
precision/recall-looking reports even though they say nothing about actual
camera conditions. A naive report would be easy to mistake for acceptance
evidence.

## Root cause

The execution environment intentionally has no approved site corpus, checkpoint
licence package, or target GPU. Model output contracts are test fixtures, not
validated detector outputs.

## Fix

Keep calibration and evaluation as separate calls, require unchanged sealed
event IDs for every ablation profile, fail closed on VLM timeout/malformed/
`unclear` status, and emit Wilson intervals plus `evidence_level=fixture_only`
and `qualification_state=lab_required` in model-card/provenance reports.

## Verification

The MODEL-202 proof tests cover deterministic threshold selection, VLM
with/without comparison, crop variants, confidence interval bounds, checksum
tamper protection, and export/load output equality. No media, weights, or
external model calls are used.

## Prevention

Do not promote the generated baseline or use its metrics for procurement until
the site sealed set, provenance/licence review, GPU qualification, and silent
period gates are attached. Keep this note with future benchmark artifacts.

## Related commit

Filled after the feature commit is created; branch: `feature/model-202-baseline-ablation`.
