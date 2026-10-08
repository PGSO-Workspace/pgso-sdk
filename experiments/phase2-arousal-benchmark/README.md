# Phase 2 — Arousal-signal benchmark (spontaneous speech)

## Validation revision — 2026-10-08

The historical design and bands below are retained for provenance. They are **not
evidence that any correlation is governance-grade**, and the legacy
`benchmark.py` does not implement the revised validation protocol. Its utterance
bootstrap, unpinned model loading, aggregate failure reporting and whole-utterance
timing are insufficient for a confirmatory streaming comparison. Do not use its
"REAL" or "ceiling" output as a scientific conclusion.

The revised [protocol](../../docs/validation/charter-validation-protocol.md) is a
development draft, not an approved preregistration. No new corpus/model evaluation
has been executed under it. `evaluate_predictions.py` evaluates **already recorded**
predictions without loading models or calling providers. It preserves coverage,
separates errors from abstentions, validates aligned utterance identities and
speaker-disjoint splits, and uses paired speaker-cluster bootstrap intervals.
Every candidate must supply a row for every utterance, including failures.

Required CSV columns:

```text
corpus,split,scene,speaker,candidate,human_arousal,prediction,status
```

`split` is `train`, `development`, or `test`. `status` is `ok`, `abstained`, or
`error`; only `ok` contains a prediction. IDs must be stable, labels and speaker
identities identical across candidates. A speaker must belong to only one split
within a corpus. This validator does not detect identity overlap between corpora,
audio duplicates or overlap with model pretraining: those require the protocol's
dataset/model provenance review. Prediction files must come with the pinned
extraction manifest described there. Correlations are conditional on emitted
predictions; paired differences are conditional on common coverage.

Example for a future authorized recorded-prediction evaluation:

```sh
python evaluate_predictions.py predictions.csv --output results.json
```

The command refuses to overwrite results, records input/evaluator/statistics
hashes and emits strict JSON. Undefined statistics are explicit, not NaN or a
successful verdict. If any bootstrap replicate is undefined, the interval is
withheld and its count reported. Small/degenerate samples need methodological
review, not silent filtering. Results are labeled development and do not select a
winner automatically. Vault datasets/analyses must still run only through a
declared PhDude analysis; this command is not a bypass.

Run software checks only (synthetic records, no audio or providers):

```sh
python3 -m unittest discover -s experiments/phase2-arousal-benchmark -p 'test_evaluate_predictions.py' -v
```

The test command is run from the repository root. The evaluator needs NumPy; it
does not need torch, librosa, matplotlib or downloaded model weights.

## Historical design (not the revised protocol)

**Question:** how well does each of several prosodic arousal signals track *human*
arousal on the same spontaneous corpus, same axis, same pre-registered bands? A
**benchmark + root-cause read**, not a tuned win.

## Status: PRE-STAGED — awaiting a spontaneous corpus on disk

No spontaneous **audio** is available yet (IEMOCAP/MSP-Podcast absent; EMOVOME ships
labels/features only, audio gated). Per the rule, the verdict run is blocked. The
harness + all three candidates are built and **validated on synthetic audio**; only
the corpus→manifest loader remains (written when audio lands). The Rust SDK is **not**
touched; the eGeMAPS extractor is reused as-is via the Phase-1 binary.

## Candidates (validated: load + run + size + latency)

| id | model | params | latency | license | commercial |
|---|---|---|---|---|---|
| `egemaps` | eGeMAPS 2-feature (Phase 1, Rust) | ~2 features | n/a | project-owned | **YES** |
| `wav2small` | audeering/wav2small A/D/V | 15K / 68 KB | ~19 ms/utt | CC BY-NC-SA | **NO** |
| `wav2vec2_large_msp` | audeering wav2vec2-large msp-dim A/D/V | ~165M / 1.3 GB | ~399 ms/utt | CC BY-NC-SA | **NO** |

**Finding already locked:** both learned candidates are **research-only (CC BY-NC-SA)**.
A STRONG learned result is an *academic ceiling*, not a shippable SDK signal, absent a
commercial license or an own-trained head on a permissive encoder.

## Pre-registered bands (fixed before any run — `config.py`)

Spearman ρ of predicted vs human arousal, per candidate, per corpus:

| ρ | band |
|---|---|
| ≥ 0.50 **and** perm p < 0.001 | STRONG (governance-grade) |
| 0.35 – 0.50 | MODERATE (usable, document limits) |
| 0.20 – 0.35 | WEAK (= Phase-1 heuristic band; baseline ρ=0.206) |
| < 0.20 | DOES NOT TRACK |

No pooling across corpora, no tuning to cross a band, no cherry-picking a corpus.

## Design

- **Native per-utterance** aggregation (user-approved): eGeMAPS keeps its Phase-1 800 ms
  window + confidence-weighted mean; the learned models use their own whole-utterance
  pooling. One predicted arousal per utterance per candidate; compared per utterance.
- Per candidate × corpus: Spearman ρ + permutation test (5000) + bootstrap 95% CI (2000)
  + Pearson (secondary). Per-channel standardization for the scatter (ρ is rank-invariant).
- **Pairwise Δρ vs the eGeMAPS baseline** with a paired-bootstrap CI — the decision-relevant
  number: does a learned signal beat the cheap heuristic *for real* (CI excludes 0)?
- Honesty controls: per-speaker breakdown; abstentions reported, never imputed; eGeMAPS
  F0/extraction-failure decomposition (too-short / low-voicing).

## Files

- `config.py` — pre-registration, candidate registry, bands, `print_header`.
- `wav2small_model.py`, `audeering_model.py` — learned-model adapters (ported from model cards; NC license).
- `eval_stats.py` — rank-based Spearman / permutation / bootstrap / paired-Δρ (self-checked).
- `validate_models.py` — synthetic-audio candidate readiness (load + run + size + latency).
- `benchmark.py` — corpus-agnostic orchestrator: manifest → verdict table + pairwise + scatters + root-cause.
- **TODO when audio lands:** `load_<corpus>.py` → standard manifest.

## Standard manifest (what the loader must emit)

CSV with columns: `scene, wav_path, human_arousal, speaker` (+ optional `human_valence`).
`human_arousal` numeric; `wav_path` decodable by librosa (wav/ogg/flac).

## Run (when a corpus is on disk)

```sh
cd experiments/phase2-arousal-benchmark
python validate_models.py                       # candidate readiness (synthetic)
python load_<corpus>.py                         # corpus -> out/manifest.csv  (to be written)
python benchmark.py out/manifest.csv iemocap     # verdict table + scatters + root-cause
```
