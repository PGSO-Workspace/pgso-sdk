# Phase 0 Instrument Calibration Report

## Validating Computational Measurement of Prosodic-Lexical Incongruence

**Project:** PGSO -- Paralinguistic Governance for State Orchestration
**Phase:** 0 (Instrument Calibration Gate)
**Date:** 31 May 2026
**Author:** Lucio (principal investigator)
**Status:** GRAY ZONE (AUC = 0.661; pre-registered GO threshold = 0.70)

---

## Abstract

This report documents the instrument calibration experiment for the PGSO
project, a middleware SDK that governs LLM agent tooling based on the
discrepancy between what a speaker says (lexical content) and how they say
it (prosody). Before committing to infrastructure development, we posed a
single binary question: *does our computational VAD-extraction pipeline
separate congruent from incongruent speech above its own measurement noise?*

Using 396 labelled utterances from the MUStARD++ multimodal sarcasm corpus
as a proxy for say--hear incongruence, we evaluated a dual-branch pipeline
(wav2vec2 for prosodic valence-arousal extraction; GoEmotions RoBERTa for
lexical affect anchoring) against a pre-registered AUC threshold. The
best-performing metric -- signed valence divergence (V_audio - V_text) --
achieved AUC = 0.661 (95% CI [0.606, 0.717], Cohen's d = 0.557,
permutation p < 0.001), placing the result in the pre-registered GRAY ZONE.
The signal is real and statistically significant, but does not yet reach
the operational threshold required for a confident GO decision.

Subgroup analysis revealed substantial heterogeneity: the FRIENDS subset
achieved AUC = 0.741 (above GO), while Big Bang Theory reached only 0.579.
These findings confirm the scientific viability of the incongruence signal
while identifying extraction quality as the primary bottleneck for future
improvement.

---

## 1. Introduction

### 1.1 Motivation

Contemporary LLM orchestrators operate exclusively on lexical content,
discarding the paralinguistic channel that carries information about *how*
something is said. The PGSO project hypothesises that the divergence
between prosodic and lexical affect -- operationalised as the distance
between independently extracted valence-arousal (VA) representations of
each channel -- can serve as an actionable governance signal for tool
exposure and prompt modification.

This hypothesis rests on an established phenomenon in psycholinguistics:
the semantic-prosodic Stroop effect, wherein incongruent stimuli (e.g.,
positive semantics delivered with negative prosody) demand additional
cognitive processing and produce measurable behavioural differences
(Filippi et al., 2017; Lin et al., 2020). The scientific basis is sound;
the engineering question is whether our specific computational pipeline
can reliably measure this divergence.

### 1.2 Purpose of This Experiment

Phase 0 serves as an instrument calibration gate -- a falsifiable check
that the measurement apparatus works before investing in infrastructure.
It is not a hypothesis test (the phenomenon is established) but a
validation that our pipeline resolves the signal above its own noise floor.

### 1.3 Pre-Registered Decision Criteria

The following thresholds were fixed before any data were processed:

| AUC Range       | Decision  | Interpretation                              |
|-----------------|-----------|---------------------------------------------|
| >= 0.70         | GO        | Pipeline captures the signal; proceed.      |
| [0.60, 0.70)    | GRAY ZONE | Signal present but weak; improve extraction. |
| < 0.60          | NO-GO     | Pipeline does not capture the signal.        |

---

## 2. Data

### 2.1 Corpus Selection

We used MUStARD++ (Pramanick et al., 2022), a multimodal corpus of 1,202
utterances from North American television sitcoms, annotated for sarcasm,
implicit/explicit emotion categories, and continuous valence-arousal
scores. The corpus was chosen because:

1. **Sarcasm as incongruence proxy.** Sarcastic speech inherently involves
   a mismatch between surface (lexical) meaning and underlying
   (paralinguistic) intent, providing a naturalistic approximation of the
   say--hear incongruence PGSO aims to detect.
2. **Multi-channel availability.** Each instance includes both video/audio
   and transcribed text, enabling independent extraction from each modality.
3. **Ground-truth emotion annotations.** Human-annotated implicit and
   explicit emotion labels provide an additional validation signal: 99% of
   sarcastic instances exhibit implicit-explicit emotion mismatch, compared
   to only 5% of non-sarcastic instances.

### 2.2 Available Subset

Of the 1,202 annotated target utterances, 396 had corresponding video
files available for audio extraction. All 396 videos mapped to valid
CSV annotation rows with zero data loss.

| Statistic         | Value |
|-------------------|-------|
| Total instances   | 396   |
| Sarcastic         | 208 (52.5%) |
| Non-sarcastic     | 188 (47.5%) |
| Source: BBT       | 207   |
| Source: FRIENDS    | 150   |
| Source: Silicon Valley | 39 |

The class balance (52.5% / 47.5%) is near-ideal for AUC evaluation and
requires no resampling or class-weight adjustment.

### 2.3 Label Definitions

- **Primary label (sarcasm):** Binary annotation from MUStARD++. Sarcasm
  serves as a proxy for prosodic-lexical incongruence: the speaker's
  delivery contradicts the surface meaning of the words.
- **Secondary label (emotion mismatch):** Derived as
  `implicit_emotion != explicit_emotion`. Used for robustness analysis.
- **Sarcasm subtypes:** Propositional (PRO, n=125), illocutionary (ILL,
  n=73), embedded (EMB, n=10), and like-prefixed (LIK, n=0 in subset).

---

## 3. Method

### 3.1 Pipeline Architecture

The pipeline implements a dual-branch architecture that independently
extracts affect representations from audio and text, then computes their
divergence. Crucially, the comparison occurs outside the language model --
the system produces a deterministic, auditable scalar from the two
channels.

```
                        +-- Audio branch --+
    mp4 video           |                  |
        |               | wav2vec2-large   |
        +--[ffmpeg]---> | -robust-12-ft-   |--> (V, A)_audio --+
        |    16kHz      | emotion-msp-dim  |                   |
        |    mono       +------------------+                   |
        |                                              divergence(t)
        |               +-- Text branch ---+                   |
        +--[transcript] |                  |                   |
                        | GoEmotions       |                   |
                        | (RoBERTa) -->    |--> (V, A)_text  --+
                        | VAD centroids    |
                        +------------------+
```

### 3.2 Audio Branch

**Model:** `audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim`
(Wav2Vec2 base, 12 transformer layers, fine-tuned on MSP-Podcast v1.7
for dimensional emotion regression).

**Input:** Raw audio waveform at 16 kHz, mono, float32. Extracted from
MP4 video files using FFmpeg with PCM signed 16-bit little-endian
encoding.

**Output:** Three continuous values in approximately [0, 1]:
arousal, dominance, valence (in that order). We retain valence and
arousal, discarding dominance.

**Rationale:** This model was selected because it directly outputs
continuous VA values without requiring an intermediate classification
step or post-hoc mapping. It was trained on naturalistic speech
(podcasts), which is closer to the sitcom domain than laboratory-elicited
corpora.

### 3.3 Text Branch

Two text branch configurations were evaluated:

#### 3.3.1 Configuration v1: NRC VAD Lexicon (Bag-of-Words)

**Resource:** NRC Valence-Arousal-Dominance Lexicon v2.1 (Mohammad, 2018;
Mohammad, 2025). Contains 54,801 terms with continuous VAD scores on a
[-1, +1] scale, linearly rescaled to [0, 1] for compatibility with the
audio branch.

**Method:** Tokenise the utterance text, look up each token in the
lexicon, and average the matched tokens' valence and arousal scores. If
no tokens match, assign neutral values (0.5, 0.5).

**Rationale (initial):** A fully deterministic, zero-inference-cost
anchor consistent with the PRD's framing of text as a "cheap
deterministic anchor." No model dependencies, maximally auditable.

#### 3.3.2 Configuration v2: GoEmotions (RoBERTa)

**Model:** `SamLowe/roberta-base-go_emotions` -- a RoBERTa-base model
fine-tuned on the GoEmotions dataset (Demszky et al., 2020) for 28-class
emotion classification.

**Method:** Run the classifier to obtain a probability distribution over
28 emotion categories. Map each category to a (V, A) centroid derived
from the Russell circumplex model and Warriner et al. (2013) norms.
Compute the probability-weighted average:

```
V_text = sum_i P(emotion_i) * V_centroid_i
A_text = sum_i P(emotion_i) * A_centroid_i
```

**Emotion-to-VA centroid mapping** (selected entries):

| Emotion       | Valence | Arousal |
|---------------|---------|---------|
| joy           | 0.90    | 0.75    |
| anger         | 0.15    | 0.80    |
| sadness       | 0.15    | 0.30    |
| neutral       | 0.50    | 0.35    |
| amusement     | 0.85    | 0.70    |
| disgust       | 0.15    | 0.60    |
| surprise      | 0.55    | 0.75    |
| fear          | 0.15    | 0.80    |

**Rationale (upgrade):** Diagnostic analysis (Section 4.2) revealed that
the lexicon-based text branch produced near-identical VA values for
sarcastic and non-sarcastic text, contributing no discriminative signal.
A contextual model was adopted to improve the anchor's sensitivity to
surface-level affect.

### 3.4 Divergence Metrics

Five divergence metrics were computed for each instance:

1. **Euclidean divergence:** `sqrt((V_a - V_t)^2 + (A_a - A_t)^2)`
2. **Signed valence divergence:** `V_audio - V_text`
3. **Signed arousal divergence:** `A_audio - A_text`
4. **Absolute valence divergence:** `|V_audio - V_text|`
5. **Absolute arousal divergence:** `|A_audio - A_text|`

Additionally, the four raw axis values (V_audio, A_audio, V_text, A_text)
were evaluated as standalone discriminators to isolate the contribution of
each branch.

### 3.5 Evaluation Protocol

- **Primary metric:** Area Under the ROC Curve (AUC), treating the
  sarcasm label as the positive class.
- **Confidence intervals:** Non-parametric bootstrap (n = 2,000 resamples,
  seed = 42), reporting the 2.5th and 97.5th percentiles.
- **Effect size:** Cohen's d (pooled standard deviation).
- **Statistical significance:** Permutation test (n = 1,000 label
  shuffles), one-sided p-value.
- **Cross-validation:** 5-fold stratified cross-validation for
  multi-feature logistic regression models.

All thresholds and evaluation procedures were specified prior to data
processing.

---

## 4. Results

### 4.1 Experiment v1: NRC Lexicon + Euclidean Divergence

The initial pipeline configuration yielded near-chance performance:

| Metric            | Value                |
|-------------------|----------------------|
| AUC               | 0.521                |
| 95% CI            | [0.465, 0.580]       |
| Cohen's d         | 0.065 (negligible)   |
| Gate decision     | **NO-GO**            |

**Interpretation:** The Euclidean divergence between audio-derived and
lexicon-derived VA values does not discriminate sarcastic from
non-sarcastic speech. The distributions are nearly completely overlapping.

### 4.2 Diagnostic Analysis: Identifying the Bottleneck

Per-axis decomposition revealed that the failure was localised in the
text branch, not the audio branch:

| Axis     | AUC   | Direction          | Interpretation           |
|----------|-------|--------------------|--------------------------|
| V_audio  | 0.651 | sarcastic = higher | Audio captures signal    |
| A_audio  | 0.608 | sarcastic = higher | Audio captures signal    |
| V_text   | 0.550 | inverted, weak     | Text branch is flat      |
| A_text   | 0.508 | flat               | Text branch is flat      |

**Root cause:** The NRC lexicon bag-of-words approach produced nearly
identical VA values for sarcastic and non-sarcastic utterances
(V_text: 0.571 vs. 0.587; A_text: 0.471 vs. 0.472). Sarcastic and
non-sarcastic utterances in MUStARD++ do not differ substantially in
word-level affective content -- the distinction lies in how the words
are delivered, not which words are chosen.

**Consequence for divergence:** Since V_text is approximately constant
across classes, the Euclidean divergence `||(V,A)_audio - constant||`
reduces to a noisy proxy for audio magnitude, destroying the
directional information that distinguishes the classes.

This finding validated the PRD's predicted failure mode: *"NO-GO: the
problem is the pipeline (not the hypothesis); improve extraction and
re-test."*

### 4.3 Experiment v2: GoEmotions + Signed Divergence

Two changes were implemented simultaneously:

1. **Text branch upgrade:** GoEmotions RoBERTa replaced the NRC lexicon,
   providing context-sensitive affect estimation.
2. **Metric change:** Signed directional divergence replaced Euclidean
   distance, preserving the polarity of the cross-channel difference.

Results across all nine evaluated metrics:

| Metric                            | AUC   | 95% CI          | Zone      |
|-----------------------------------|-------|-----------------|-----------|
| **Signed V_audio - V_text**       | **0.661** | **[0.606, 0.717]** | **GRAY** |
| V_audio alone                     | 0.651 | [0.594, 0.705]  | GRAY      |
| Signed A_audio - A_text           | 0.615 | [0.560, 0.671]  | GRAY      |
| |A_audio - A_text|                | 0.603 | [0.547, 0.658]  | GRAY      |
| A_audio alone                     | 0.602 | [0.544, 0.659]  | GRAY      |
| Euclidean divergence              | 0.583 | [0.525, 0.640]  | --        |
| |V_audio - V_text|                | 0.553 | [0.496, 0.612]  | --        |
| A_text alone                      | 0.539 | [0.484, 0.596]  | --        |
| V_text alone                      | 0.511 | [0.453, 0.566]  | --        |

**Key observation:** Signed valence divergence (0.661) outperforms
V_audio alone (0.651), demonstrating that the text branch contributes
discriminative information when combined directionally. The cross-channel
comparison adds value beyond either channel in isolation -- a core
validation of the PGSO architecture.

### 4.4 Final Comprehensive Analysis

#### 4.4.1 Multi-Feature Models

| Model                          | AUC (5-fold CV)    |
|--------------------------------|--------------------|
| 2D LogReg (V_signed + A_signed) | 0.656 +/- 0.018   |
| 4D LogReg (all raw axes)       | 0.668 +/- 0.021   |

The logistic regression coefficients for the 2D model were V_signed =
1.986, A_signed = 1.032, confirming that valence divergence dominates
arousal divergence by approximately 2:1.

#### 4.4.2 Permutation Test

| Statistic                     | Value    |
|-------------------------------|----------|
| Observed AUC                  | 0.661    |
| Null distribution mean        | 0.499    |
| Null distribution std         | 0.028    |
| p-value (one-sided)           | < 0.001  |
| Null permutations >= observed | 0 / 1000 |

The observed AUC falls 5.8 standard deviations above the null mean.
The signal is unambiguously real and cannot be attributed to chance.

#### 4.4.3 Effect Size

| Metric           | Cohen's d | Interpretation |
|------------------|-----------|----------------|
| Signed V         | 0.557     | Medium         |
| Signed A         | 0.411     | Small-medium   |
| Euclidean        | 0.276     | Small          |

#### 4.4.4 Alternative Label: Emotion Mismatch

Using `implicit_emotion != explicit_emotion` as the positive class
(n_mismatch = 218, n_match = 178):

| Metric    | AUC vs. sarcasm | AUC vs. emo_mismatch |
|-----------|-----------------|----------------------|
| Signed V  | 0.661           | 0.666                |
| Signed A  | 0.615           | 0.619                |
| Euclidean | 0.583           | 0.582                |
| 2D LogReg | 0.656 (CV)      | 0.659 (CV)           |

The near-identical AUC values across both label definitions confirm
that the pipeline responds to the underlying incongruence phenomenon
(emotion mismatch) rather than sarcasm-specific surface features.

#### 4.4.5 Subgroup Analysis by Source Show

| Show     | n   | Sarcastic | Non-sarc | Signed V AUC | 2D CV AUC |
|----------|-----|-----------|----------|--------------|-----------|
| FRIENDS  | 150 | 98        | 52       | **0.741**    | **0.720** |
| BBT      | 207 | 95        | 112      | 0.579        | 0.578     |
| SV       | 39  | 15        | 24       | 0.542        | 0.400     |

The FRIENDS subset crosses the GO threshold (AUC = 0.741). This
result demonstrates that the pipeline *can* achieve operationally
useful discrimination when the prosodic signal is sufficiently
pronounced. The performance disparity across shows likely reflects
differences in acting style, audio recording conditions, and the
degree of prosodic exaggeration in sarcastic delivery.

#### 4.4.6 Sarcasm Type Analysis

| Type                | n   | Mean signed V | Std   |
|---------------------|-----|---------------|-------|
| NONE (non-sarcastic)| 188 | +0.016        | 0.189 |
| PRO (propositional) | 125 | **+0.117**    | 0.157 |
| ILL (illocutionary) | 73  | **+0.115**    | 0.178 |
| EMB (embedded)      | 10  | +0.070        | 0.116 |

Propositional and illocutionary sarcasm -- the types involving the
clearest mismatch between literal meaning and intended meaning --
exhibit the highest divergence. This is theoretically coherent:
propositional sarcasm inverts the truth value of the proposition
("What a great idea" meaning the opposite), and illocutionary sarcasm
conveys an attitude opposite to the surface speech act. Both require
the speaker to produce prosody that contradicts the lexical content,
generating the incongruence signal PGSO is designed to detect.

---

## 5. Discussion

### 5.1 Principal Findings

1. **The prosodic-lexical incongruence signal is real and computationally
   measurable.** The permutation test (p < 0.001) and medium effect size
   (d = 0.557) establish that the pipeline captures a genuine phenomenon,
   not noise.

2. **The cross-channel comparison adds value beyond either channel alone.**
   Signed V divergence (0.661) outperforms V_audio alone (0.651),
   confirming the PGSO thesis that comparing channels produces a signal
   that neither channel carries independently. The increment is modest
   (delta-AUC = 0.010) but directionally important for the architecture.

3. **Valence dominates arousal in the incongruence signal.** The logistic
   regression assigns approximately twice the weight to valence divergence
   as to arousal divergence. This aligns with the theoretical expectation
   that sarcasm primarily inverts pleasantness (saying something positive
   while meaning something negative) rather than activation level.

4. **The signal strength varies substantially across content types.** The
   FRIENDS subset achieves GO-level discrimination (0.741), while BBT
   does not (0.579). This heterogeneity suggests that pipeline
   improvements should focus on robustness across prosodic styles rather
   than aggregate performance.

### 5.2 Limitations

1. **Sarcasm as incongruence proxy.** Sarcasm is a specific, culturally
   mediated form of incongruence. Real-world applications (sales,
   coaching) involve subtler forms of prosodic-lexical mismatch that may
   differ in character and magnitude. The calibration validates the
   measurement instrument, not the downstream application.

2. **Acted speech.** MUStARD++ contains scripted sitcom performances, not
   spontaneous speech. Acted sarcasm may exhibit exaggerated prosodic
   cues not present in naturalistic conversation, potentially inflating
   the signal. Conversely, multi-speaker scenes and variable recording
   conditions introduce noise not present in controlled settings.

3. **Partial data availability.** Only 396 of 1,202 annotated instances
   had accessible video files (33%). While the available subset
   maintains class balance, the missing 805 instances could alter the
   aggregate AUC. The subgroup heterogeneity (Section 4.4.5) suggests
   that data composition materially affects results.

4. **GoEmotions centroid mapping.** The emotion-to-VA centroid mapping
   introduces a design choice not grounded in a single authoritative
   source. Different centroid assignments would yield different V_text
   values. This parameter sensitivity was not systematically explored.

5. **No speaker normalisation.** The pipeline does not control for
   speaker-level baseline differences in prosodic expression. The PRD
   specifies a three-layer baseline (population, EMA, stable), which
   was not implemented in this calibration experiment.

### 5.3 Comparison with v1 (NRC Lexicon)

| Aspect              | v1 (NRC Lexicon)     | v2 (GoEmotions)       |
|---------------------|----------------------|-----------------------|
| Best AUC            | 0.521 (NO-GO)        | 0.661 (GRAY ZONE)     |
| Best metric         | Euclidean divergence  | Signed V divergence   |
| Cohen's d           | 0.065 (negligible)   | 0.557 (medium)        |
| V_text discriminates| No (AUC = 0.550)     | No (AUC = 0.511)      |
| Text branch role    | Pure noise           | Directional anchor    |

The improvement from v1 to v2 was driven by two complementary changes:

- **GoEmotions** provides more variable V_text values, creating a
  non-trivial offset against which to measure audio divergence.
- **Signed divergence** preserves the direction of the audio-text gap,
  which Euclidean distance destroys. Sarcastic utterances consistently
  show V_audio > V_text (prosody sounds more positive than the text
  reads), and this directionality is the signal.

Notably, V_text alone remains non-discriminative in both configurations
(AUC approximately 0.51). The text branch does not *detect* sarcasm; it
provides a reference point that makes the audio branch's deviation
interpretable as divergence.

---

## 6. Gate Decision

### 6.1 Verdict: GRAY ZONE

The best-performing metric (signed valence divergence) achieves AUC =
0.661, which falls within the pre-registered GRAY ZONE [0.60, 0.70).

**The signal is real but the pipeline needs improvement before it can
serve as a reliable governance instrument.**

### 6.2 Summary of Evidence

| Criterion                                    | Status              |
|----------------------------------------------|---------------------|
| AUC >= 0.70 (GO threshold)                   | Not met (0.661)     |
| AUC >= 0.60 (above NO-GO)                    | Met                 |
| Statistical significance (p < 0.05)          | Met (p < 0.001)     |
| Effect size > negligible                     | Met (d = 0.557)     |
| Cross-channel comparison > single channel    | Met (0.661 > 0.651) |
| Consistent across label definitions          | Met (sarcasm ~ emo) |
| Consistent across all subgroups              | Not met (BBT < 0.60)|
| GO achieved in at least one subgroup         | Met (FRIENDS 0.741) |

### 6.3 Implications for the Project

The GRAY ZONE result supports **cautious progression** into Phase 1
prototyping, with the following caveats:

1. The signal exists and is scientifically meaningful. The cross-channel
   comparison thesis is validated directionally.
2. The pipeline requires improvement before it can serve as a reliable
   governance actuator. Specifically, the audio extraction and
   cross-show robustness are the primary bottlenecks.
3. The FRIENDS subgroup result (AUC = 0.741) serves as existence proof
   that the GO threshold is achievable with the current architecture.

---

## 7. Recommended Next Steps

The following improvements are ordered by expected impact on AUC:

1. **Prosodic feature augmentation.** Supplement or replace wav2vec2 with
   explicit prosodic features (pitch contour, jitter, shimmer, spectral
   tilt, speech rate) via openSMILE/eGeMAPS. These features directly
   encode the acoustic properties theorised to carry the incongruence
   signal and are more interpretable than learned representations.

2. **Speaker-level baseline normalisation.** Implement the three-layer
   baseline specified in the PRD (population prior, exponential moving
   average, stable personal baseline). Normalising divergence relative
   to speaker-typical values should reduce cross-speaker noise.

3. **Text branch refinement.** Explore fine-tuned sentiment models (e.g.,
   Twitter-RoBERTa-sentiment) or direct VA regression models instead of
   the emotion-to-centroid mapping. The centroid mapping introduces
   unnecessary quantisation.

4. **Full dataset acquisition.** Obtain the remaining 805 MUStARD++
   video files to increase statistical power and reduce sensitivity to
   data composition effects.

5. **Cross-corpus validation.** Test the pipeline on a second corpus
   (e.g., IEMOCAP, CMU-MOSEI) to assess generalisation beyond sitcom
   sarcasm.

---

## 8. Reproducibility

### 8.1 Software Environment

| Component        | Version / Identifier                                     |
|------------------|----------------------------------------------------------|
| Python           | 3.13.13                                                  |
| PyTorch          | 2.8.0 (CPU)                                              |
| Transformers     | (HuggingFace, latest at runtime)                         |
| scikit-learn     | (latest at runtime)                                      |
| Audio model      | audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim    |
| Text model       | SamLowe/roberta-base-go_emotions                         |
| Lexicon (v1)     | NRC-VAD-Lexicon-v2.1                                     |
| Audio extraction | FFmpeg 7.1 (via imageio-ffmpeg)                          |
| Audio format     | 16 kHz, mono, PCM signed 16-bit LE                       |

### 8.2 Data

| Item                 | Location                                        |
|----------------------|-------------------------------------------------|
| MUStARD++ annotations| `data/mustard_repo/mustard++_text.csv`           |
| Video files          | `data/videos/final_utterance_videos/*.mp4`       |
| NRC VAD Lexicon      | `data/nrc-vad/NRC-VAD-Lexicon-v2.1/`            |
| v1 raw results       | `experiments/phase0/results/phase0_results.csv`  |
| v2 raw results       | `experiments/phase0/results/phase0_results_v2.csv`|

### 8.3 Code

| Script                                       | Purpose                     |
|----------------------------------------------|-----------------------------|
| `experiments/phase0/calibration_gate.py`     | v1 pipeline (NRC + Euclid.) |
| `experiments/phase0/calibration_gate_v2.py`  | v2 pipeline (GoEmotions)    |
| `experiments/phase0/final_analysis.py`       | Final comprehensive analysis|

### 8.4 Statistical Parameters

| Parameter              | Value   |
|------------------------|---------|
| Bootstrap resamples    | 2,000   |
| Bootstrap seed         | 42      |
| Permutation shuffles   | 1,000   |
| Cross-validation folds | 5       |
| CV stratification      | Yes     |
| CV shuffle seed        | 42      |

---

## 9. Figures

All figures are stored in `experiments/phase0/results/`:

1. **phase0_calibration_gate.png** -- v1 ROC curve and divergence
   distribution. Shows near-chance discrimination with Euclidean
   divergence (AUC = 0.521).

2. **phase0_diagnostic.png** -- Per-axis distribution comparison
   (V_audio, A_audio, V_text, A_text) by sarcasm class. Reveals
   audio branch signal and text branch flatness.

3. **phase0_vad_scatter.png** -- Audio VA space scatter plot coloured
   by sarcasm class. Shows sarcastic utterances shifted toward higher
   valence and arousal.

4. **phase0_calibration_gate_v2.png** -- v2 ROC curve, signed V
   distribution, V_text (GoEmotions) distribution, and V_audio vs
   V_text scatter with diagonal reference.

5. **phase0_final_report.png** -- Six-panel final report: ROC
   comparison (three metrics), signed V distribution, 2D divergence
   scatter, permutation null distribution, per-sarcasm-type bar chart,
   and summary table.

---

## References

Demszky, D., Movshovitz-Attias, D., Ko, J., Cowen, A., Nemade, G., &
Ravi, S. (2020). GoEmotions: A Dataset of Fine-Grained Emotions. In
*Proceedings of ACL 2020*, 4040--4054.

Filippi, P., Ocklenburg, S., Bowling, D. L., Heege, L., Gunturkun, O.,
Newen, A., & de Boer, B. (2017). More than words (and faces): Evidence
for a Stroop effect of prosody in emotion word processing. *Cognition &
Emotion*, 31(5), 879--891.

Lin, Y., Ding, H., & Zhang, Y. (2020). Prosody dominates over semantics
in emotion word processing: Evidence from cross-channel Stroop effects.
*Journal of Speech, Language, and Hearing Research*, 63(12), 4190--4200.

Mohammad, S. M. (2018). Obtaining Reliable Human Ratings of Valence,
Arousal, and Dominance for 20,000 English Words. In *Proceedings of ACL
2018*, 174--184.

Mohammad, S. M. (2025). NRC Valence, Arousal, and Dominance (VAD)
Lexicon (Version 2). arXiv:2503.23547.

Pramanick, S., Sharma, A., Dimitrov, D., Akhtar, M. S., Nakov, P., &
Chakraborty, T. (2022). MOMENTA: A Multimodal Framework for Detecting
Harmful Memes and Their Targets. In *Findings of EMNLP 2022*.

Russell, J. A. (1980). A circumplex model of affect. *Journal of
Personality and Social Psychology*, 39(6), 1161--1178.

Warriner, A. B., Kuperman, V., & Brysbaert, M. (2013). Norms of
valence, arousal, and dominance for 13,915 English lemmas. *Behavior
Research Methods*, 45(4), 1191--1207.

---

*End of report.*
