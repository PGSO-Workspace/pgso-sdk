# Phase 1 Signal Report

## Does the eGeMAPS Arousal Signal Track Human Arousal? (Acted Precondition + Label-Free Calibration)

**Project:** PGSO -- Paralinguistic Governance for State Orchestration
**Phase:** 1 (signal generalization -- precondition)
**Date:** 3 June 2026
**Author:** Lucio (principal investigator)
**Status:** WEAK, and **inherent** (Spearman rho = 0.206 on acted speech; label-free recalibration does not improve it)

---

## Abstract

The pure-Rust `pgso-signal-egemaps` extractor derives a paralinguistic arousal
scalar from speech. It had never been graded against human affect labels. We
asked a single question: **does the eGeMAPS arousal signal correlate with
human-annotated arousal?** Using 377 utterances from MUStARD++ with usable audio
and dimensional labels, the confidence-weighted-mean arousal correlated with
human arousal at Spearman rho = 0.206 (95% CI [0.10, 0.31], permutation
p = 0.0002), placing it in the pre-registered **WEAK** band. A pre-registered,
label-free calibration study then tested whether the weakness was an artifact of
the mapping's calibration: re-scaling the energy and F0-variability references
from feature percentiles changed rho by +0.008 (CI [-0.026, +0.044], i.e. zero),
and within-speaker normalization *lowered* rho to 0.146 (DOES NOT GENERALIZE
band). **The weak correlation is inherent to the two-feature prosodic mapping on
this corpus, not a calibration defect.** This is the signal's first grading
against ground truth and a clean, if negative, finding.

---

## 1. Scope and Premise Corrections

This run was specified as a test of whether the eGeMAPS signal generalizes from
acted to spontaneous speech, graded on dimensional valence/arousal. Three
premises of that specification did not hold against the repository and the scope
was narrowed accordingly (with explicit approval before any code):

1. **The signal produces arousal only.** `AxisMapping` (crate
   `pgso-signal-egemaps`, `features.rs`) exposes a single mapping,
   `arousal = 0.6·clamp(energy/0.3) + 0.4·clamp(f0_std/50)`, and every
   `SignalReading` is `Axis::Arousal`. There is no valence mapping, so the
   originally pre-registered *valence-primary* criterion is uncomputable.
   **Arousal is the tested axis.**
2. **Phase 0's AUC (0.651/0.661) was the wav2vec2 model, not this signal.** The
   Phase 0 audio branch was `wav2vec2-large-robust-12-ft-emotion-msp-dim`
   (Phase 0 report, S3.2/S4.3). eGeMAPS appears there as the *recommended
   replacement*. This eval is therefore the eGeMAPS signal's first contact with
   human affect labels.
3. **No spontaneous corpus is available locally.** Only MUStARD++ (acted sitcom)
   is on disk; IEMOCAP and MSP-Podcast are absent. This run is consequently the
   **acted precondition** -- a necessary, not sufficient, condition for
   generalization -- and does **not** answer the acted->spontaneous question.

### Pre-registered decision bands (fixed before any run, applied to arousal)

| Spearman rho (predicted vs human) | Verdict |
|---|---|
| rho >= 0.35 **and** permutation p < 0.001 | GENERALIZES |
| 0.20 <= rho < 0.35 | WEAK (signal present, degraded) |
| rho < 0.20 | DOES NOT GENERALIZE |

---

## 2. Data

MUStARD++ (Pramanick et al., 2022): 1,201 annotated target utterances, of which
**396** had a video file on disk (33%, the same availability ceiling as Phase 0).
All 396 carry single consensus **Valence** (1-8 observed) and **Arousal** (3-9
observed) integer Likert labels; arousal is the ground truth here. Audio was
extracted from mp4 to 16 kHz mono PCM-16 via ffmpeg.

Human arousal is **range-restricted**: mean 6.52, sd 1.14 on a 1-9 scale,
clustered high -- acted sitcom speech is uniformly animated. Restricted label
range mechanically attenuates any correlation.

After extraction, **19 utterances abstained** (no reading): 11 shorter than the
800 ms analysis window, 8 with voiced fraction below the VAD floor. The
correlation set is **N = 377**.

---

## 3. Method

The shipped extractor is reused unmodified; the harness adds no DSP.

- **Decode:** ffmpeg mp4 -> 16 kHz mono PCM-16 (`step1_build_manifest.py`).
- **Extract:** a small Rust binary (`extract/`) depending on
  `pgso-signal-egemaps` runs the extractor at its default config (800 ms window,
  400 ms hop) over each utterance, emitting per-window arousal + confidence and
  per-window features.
- **Aggregate (pre-registered primary):** per-utterance arousal =
  confidence-weighted mean of window arousals (confidence =
  mean_voicing × voiced_fraction). Plain mean and median are reported as
  sensitivity only. Abstaining utterances are dropped and reported, never
  imputed.
- **Correlate:** Spearman rho (rank-based, tie-aware), one-sided permutation test
  (5,000 shuffles), bootstrap 95% CI (2,000 resamples, seed 42). Per-channel
  z-scoring is applied for the scatter only (Spearman is rank/affine invariant).

---

## 4. Results

### 4.1 Primary correlation

| Quantity | Value |
|---|---|
| **Spearman rho (conf-weighted mean)** | **+0.206** |
| 95% CI (bootstrap) | [+0.10, +0.31] |
| Permutation p (1-sided, 5000) | 0.0002 (0/5000 >= observed) |
| Pearson r (secondary) | +0.199 |
| Sensitivity: rho(plain mean) / rho(median) | +0.215 / +0.211 |
| **Verdict** | **WEAK (signal present, degraded)** |

The correlation is statistically unambiguous (p = 0.0002) but weak. It is not a
near-miss: the CI's upper bound (0.31) is below the 0.35 GENERALIZES bar. The
result is robust to the aggregation choice.

### 4.2 F0 / extraction diagnostic (extraction-failure vs signal-reality)

The weak result is **not** an F0-extraction failure:

| Diagnostic | Value |
|---|---|
| Utterance abstention | 19/396 (4.8%) -- 11 too-short, 8 low-voicing |
| Low-confidence readings (mean conf < 0.30) | 38/377 (10.1%) |
| Confidence percentiles (p10/p50/p90) | 0.30 / 0.46 / 0.61 |
| Energy-term utilization | mean 0.25 of range; 5.0% saturated |
| F0_std-term utilization | mean 0.77 of range; **43.0% saturated** |

Pitch tracking succeeds on this audio. The mapping, however, is range-squeezed:
the energy term sits low (normal-speech RMS is far below `energy_ref = 0.3`) while
the F0-variability term saturates 43% of the time (`f0_std_ref = 50` Hz is
exceeded often). Whether this squeeze *causes* the weak correlation is tested in
Section 4.4.

### 4.3 Heterogeneity

Per-show Spearman rho: BBT 0.152 (N=202), FRIENDS 0.183 (N=91), SV 0.205 (N=39),
GoldenGirls -0.054 (N=34), Sarcasmoholics 0.462 (N=11, small). Per-speaker rho
ranges from -0.63 (OTHER, a catch-all label) to +0.39 (Gilfoyle), with several
near or below zero. This is far flatter than Phase 0's wav2vec2 (FRIENDS 0.74) --
expected, a four-constant heuristic versus a large learned model. The scatter
(`scatter_arousal_wmean.png`) shows GoldenGirls predicted uniformly near 0.9
regardless of human rating: high-pitch voices peg the F0-variability term, the
proximate motivation for the speaker-control analysis below.

### 4.4 Label-free calibration study

To test whether the WEAK result is calibration-limited or inherent, references
were re-derived from feature **distributions** (P95), never the labels; the
shipped crate was untouched and arousal recomputed in Python (validated against
the extractor to 3e-8).

- **References:** `energy_ref` 0.3 -> 0.312 (essentially unchanged -- the default
  is fine at the loud tail); `f0_std_ref` 50 -> 130 (un-clamps the 43% saturation).
- **Absolute regime:** rho 0.206 -> **0.214**; Δ = +0.008, 95% CI
  [-0.026, +0.044] -> indistinguishable from 0. Un-clamping pitch variability adds
  no discriminative power.
- **Within-speaker regime (PRIMARY, N=375):** rho 0.144 -> **0.146**; Δ = +0.003,
  CI [-0.037, +0.042]; permutation p = 0.003 -> **DOES NOT GENERALIZE**. Removing
  per-speaker baselines *lowered* rho (0.201 -> 0.144): part of the weak absolute
  signal is between-speaker, and within a speaker the tracking is weaker still.

Chart: `out/results/calib_rho_comparison.png`.

---

## 5. Verdict and Discussion

**Arousal verdict (acted precondition): WEAK (rho = 0.206).** The eGeMAPS arousal
signal tracks human arousal significantly but weakly on acted speech.

**The weakness is inherent, not a calibration artifact.** Label-free correction of
every diagnosed issue -- energy reference, pitch-variability saturation, and
per-speaker baseline -- moved rho by approximately zero (all CIs straddle 0). The
two prosodic features (mean energy, F0 standard deviation) are simply weakly
related to human arousal ratings on this corpus. Within a speaker -- the regime
relevant to a live, per-conversation governance signal -- the relationship sits
in the DOES NOT GENERALIZE band (rho ~ 0.15, though statistically real at
p = 0.003).

**Bearing on the original generalization question.** The outlook is poor: if
prosody->arousal is inherently weak on *exaggerated acted* speech, subtler
spontaneous speech is unlikely to be stronger. This remains formally unproven
without IEMOCAP / MSP-Podcast.

**Implications for PGSO.** The two-feature eGeMAPS arousal mapping is real but
weak as a standalone affect signal, and recalibration will not close the gap. The
Phase 0 report's recommended next steps -- richer prosodic descriptors (spectral
tilt, HNR, MFCC, speech rate) or the learned wav2vec2 model -- look necessary
rather than optional for a governance-grade signal.

---

## 6. Limitations

1. **Acted speech only.** MUStARD++ is scripted sitcom performance; the spontaneous
   target corpora were unavailable. This is a precondition, not the generalization
   test.
2. **Range restriction.** Human arousal sd 1.14 attenuates the achievable rho.
3. **Arousal only.** The signal has no valence axis; valence was not testable and
   the original valence-primary criterion could not be evaluated.
4. **Within-speaker bootstrap.** The paired bootstrap resamples utterances and
   re-centers within speaker; a speaker-cluster bootstrap (27 speakers) would be
   coarser but more conservative.
5. **Single corpus.** No second corpus to assess corpus heterogeneity of the
   arousal correlation itself.

---

## 7. Reproducibility

| Item | Value |
|---|---|
| Harness | `experiments/phase1-arousal-mustard/` |
| Pipeline | `step1_build_manifest.py` -> `extract/` (Rust) -> `stats.py`, `calibrate.py` |
| Extractor | `crates/pgso-signal-egemaps` (unmodified; default 16 kHz config) |
| Data | `data/mustard_repo/mustard++_text.csv`, `data/videos/final_utterance_videos/*.mp4` |
| Audio | ffmpeg (imageio-ffmpeg 7.1), 16 kHz mono PCM-16 |
| Aggregation (primary) | confidence-weighted mean |
| Permutations / bootstrap / seed | 5,000 / 2,000 / 42 |
| Calibration references | P95 of per-window energy / f0_std (label-free) |
| Within-speaker minimum | 2 utterances/speaker |
| Artifacts | `out/manifest.csv`, `out/predicted.csv`, `out/windows.csv`, `out/results/*` |

**Integrity.** Decision bands pre-registered and printed at the top of every run;
no threshold derived from results; no tuning to cross a band; abstentions dropped
and reported (never imputed); corpora never pooled; calibration references taken
from feature distributions only, never from the labels; the shipped crate was not
modified.

---

*End of report.*
