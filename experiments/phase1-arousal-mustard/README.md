# Phase 1 — Signal Generalization Eval (arousal, MUStARD++)

**Question (this run):** does the pure-Rust eGeMAPS **arousal** signal correlate
with human-annotated arousal?

## Scope / honesty caveat (read first)

This is the **acted-baseline precondition**, NOT the headline acted→spontaneous
generalization test:

- The eGeMAPS signal produces **arousal only** — there is no valence mapping in
  the crate, so the pre-registered *valence* axis cannot be computed against this
  signal.
- No spontaneous corpus (IEMOCAP improvised / MSP-Podcast) is available locally;
  the only corpus on disk is **MUStARD++ (acted sitcom)** — the very domain we
  wanted to generalize *from*.
- So we grade eGeMAPS arousal vs MUStARD++ human arousal on **acted** speech. If
  arousal does not track even here, the spontaneous question is moot. A pass here
  is **necessary, not sufficient** for generalization.

Note also: Phase 0's AUC 0.651/0.661 was the **wav2vec2** model's valence, not this
extractor. eGeMAPS appears in Phase 0's "next steps" as the prosodic replacement —
so this is the eGeMAPS signal's **first** grading against human affect labels.

## Pre-registered bands (fixed before any run — see `config.py`)

Applied to arousal (the testable axis):

| ρ (Spearman, predicted vs human) | verdict |
|---|---|
| ρ ≥ 0.35 **and** permutation p < 0.001 | GENERALIZES |
| 0.20 ≤ ρ < 0.35 | WEAK (signal present, degraded) |
| ρ < 0.20 | DOES NOT GENERALIZE |

## Pipeline

1. **`step1_build_manifest.py`** — parse MUStARD++, keep `_u` rows with human V+A
   and a video on disk, decode each to 16 kHz mono wav (ffmpeg via imageio-ffmpeg),
   write `out/manifest.csv`. Reports counts, label ranges, and the duration
   distribution vs the 800 ms analysis window.
2. **`extract/`** (Rust) — runs `pgso-signal-egemaps` over each utterance →
   per-utterance arousal + confidence + voiced-fraction + abstention →
   `out/predicted.csv`. *(step 2 — current file is a one-utterance seam check.)*
3. **`stats.py`** — standardize, Spearman ρ + permutation + bootstrap CI,
   per-show / per-speaker breakdown, F0-failure diagnostic, scatter chart, verdict
   vs bands. *(steps 3–6)*

The extractor is **reused as-is** (`crates/pgso-signal-egemaps`); this harness adds
no DSP.

## Run

```sh
cd experiments/phase1-arousal-mustard
python step1_build_manifest.py                                                    # step 1: manifest + decode
cargo run --release --manifest-path extract/Cargo.toml -- out/manifest.csv out/predicted.csv  # step 2: extract
python stats.py                                                                   # steps 3-6: verdict + chart
```

## Results (2026-06-03)

N = 377 correlated (19 abstained: 11 too-short, 8 low-voicing).

**Spearman ρ (conf-weighted mean vs human arousal) = +0.206, 95% CI [+0.10, +0.31],
permutation p = 0.0002 (1-sided, 5000) → WEAK** (signal present, degraded). Robust to
aggregation (plain mean 0.215, median 0.211; Pearson 0.199).

Read: the eGeMAPS arousal signal tracks human arousal on acted speech **significantly but
weakly** — even the CI upper bound (0.31) is below the 0.35 GENERALIZES bar. This is the
acted **precondition**, not the acted→spontaneous test.

Why weak (diagnosed — **not** F0 failure: abstention 4.8%, low-confidence 10%):
1. shipped-mapping mis-calibration — energy term muted (mean 25% of range; `energy_ref=0.3`
   ≫ normal-speech RMS ≈0.08) and f0_std term saturated 43% of the time (clipped >50 Hz);
2. range restriction in acted labels (human arousal sd 1.14);
3. no speaker normalization — GOLDENGIRLS predicts uniformly ≈0.9 (high-pitch voices peg
   the f0_std term) → that show's ρ ≈ 0.

Chart: `out/results/scatter_arousal_wmean.png`. Record: `out/results/verdict.txt`.

### Calibration follow-up (label-free) — `calibrate.py`

Tests whether the WEAK result is calibration-limited or inherent, with references drawn
from feature **distributions** (P95), never the labels. Recompute validated vs the Rust
extractor to 3e-8.

- references: `energy_ref` 0.3 → **0.312** (≈ no change); `f0_std_ref` 50 → **130** (un-clamps the 43% saturation).
- **absolute:** ρ 0.206 → **0.214**; Δ = +0.008, 95% CI **[−0.026, +0.044]** → indistinguishable from 0.
- **within-speaker (PRIMARY, N=375):** ρ V0 0.144 → V3 **0.146**; Δ = +0.003, CI [−0.037, +0.042]; p=0.003 → **DOES NOT GENERALIZE**. Speaker control *lowers* ρ (0.201 → 0.144): the weak signal is partly between-speaker.

**Conclusion: the WEAK arousal result is inherent, not a calibration artifact.** Un-clamping
pitch variability adds no discriminative power; the prosody-only features (energy + F0
variability) are weakly related to human arousal on this corpus, and within a speaker the
relationship is in the DOES-NOT-GENERALIZE band. Chart: `out/results/calib_rho_comparison.png`.
