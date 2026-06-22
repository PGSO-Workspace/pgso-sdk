# Phase 3 — Governance Curve

This experiment has two layers:

- **Layer A — signal benchmark:** how well each candidate arousal extractor's
  reading correlates with human arousal labels (Spearman ρ).
- **Layer B — governance curve:** how decision correctness degrades as the
  signal quality (target ρ) is dialed down, with the inviolable-allowlist
  guarantee (G2) held throughout.

Source artifacts live under `out/`. This README records one methodological
decision about Layer A; the headline Layer B curve is in `out/layerb_summary.json`.

## Layer A — extractor comparison: no paired significance test (decision R3-B)

The two extractors are reported **independently**, each with its own 95% CI. A
paired-bootstrap Δρ (learned − heuristic) is **not** computed, and **no claim of
a significant difference between the extractors is made.**

| Candidate | What it is | n | Spearman ρ | 95% CI | permutation p | Source |
|---|---|---|---|---|---|---|
| `egemaps2` | Heuristic 2-feature pure-DSP eGeMAPS reading | 377 (19 abstained) | **0.20577** | [0.10093, 0.31103] | 1.9996e-4 | `out/layera_egemaps2.json` |
| `wav2small` | Learned distilled transformer (non-commercial weights) | 396 | **0.27055** | [0.17612, 0.36460] | 1.9996e-4 | `out/layera_transformer.json` |

A leakage-free cross-corpus floor for the full eGeMAPS+head is in
`out/layera_egemaps_full.json` (single-feature floor: emovome 0.34376, mustard
0.29228; cross-corpus rho −0.02886 / 0.18830).

### Why no paired comparison

A paired bootstrap requires the two extractors' **per-utterance predictions
aligned by utterance id**, resampled jointly. Those per-utterance predictions
are **not persisted** for both candidates:

1. Only the aggregate ρ (and its CI) is stored for each candidate — there is no
   on-disk per-utterance prediction file for `wav2small`.
2. The two runs are not on the same utterance set: `egemaps2` abstains on 19
   utterances (n = 377) while `wav2small` scores all 396 (n = 396), so the rows
   are not paired even in principle without re-deriving an aligned subset.
3. Re-running the transformer extractor to regenerate aligned per-utterance
   predictions is signal work, which is **out of scope** for the SDK-integrity
   closure that introduced this note.

Because alignment is impossible from the stored artifacts, the comparison falls
back to reporting both ρ separately (decision gate R3-B).

### Do not cite the naive subtraction

The point estimates differ by `0.27055 − 0.20577 = +0.06478`. This raw
subtraction is **not** a result: it has no confidence interval, no paired
significance test, and the two CIs overlap substantially ([0.10, 0.31] vs
[0.18, 0.36]). It MUST NOT be reported as a Δρ or used to claim the learned
extractor is significantly better. The defensible statement is: *both extractors
show a weak but significant positive correlation with human arousal; a paired
comparison was not performed, so no difference between them is claimed.*

If aligned per-utterance predictions for both extractors are regenerated later,
a paired-bootstrap Δρ (≥2000 resamples, seed 42) can be computed and persisted to
`out/layera_compare.json`, at which point that CI — not the subtraction above —
becomes the figure of record.
