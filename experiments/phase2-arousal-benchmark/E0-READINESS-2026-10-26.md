# E0 acoustic-signal readiness: protocol supplement (2026-10-26)

**Status:** proposal only; NOT an approved preregistration, a PhDude record, an experimental result, a license grant, or a replacement of the existing Charter protocol.

## Research question

How well do transcription-independent arousal estimators track human vocal-arousal annotations on held-out naturalistic speech, and how does performance and coverage transfer across speakers and corpora? This study is independent of whether restricting tools is appropriate. Acoustic arousal does not establish distress, intent or consent.

## Existing candidates, not newly trained or executed

1. **Rust eGeMAPS-style heuristic**, only a limited acoustic feature mapping, not full eGeMAPS or calibrated emotion confidence.
2. **audeering/wav2small**, benchmark adapter exists; recorded CC BY-NC-SA 4.0 weights, academic evaluation only unless licensing independently permits more.
3. **audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim**, benchmark adapter exists; recorded CC BY-NC-SA 4.0 weights. Investigate training overlap, especially with MSP data.

The historical `benchmark.py` mixes whole-utterance learned estimates with windowed heuristic estimates. Do not use it as a comparable streaming benchmark. The existing `evaluate_predictions.py` is a development evaluator and must not be described as an accepted preregistration.

## Dataset acquisition candidates (not downloaded or approved)

| Candidate | Role | Blocking constraint |
|---|---|---|
| [EMOVOME](https://zenodo.org/records/10694370) | Naturalistic Spanish voice messages, proposed language-transfer set | Request source audio and written authorization; confirm annotations and sampling |
| [MSP-Conversation](https://www.lab-msp.com/MSP/MSP-Conversation.html) | Naturalistic dialogue and temporal validation | Institutional academic agreement and speaker/conversation split; study related-MSP overlap |
| [MSP-Podcast](https://lab-msp.com/MSP/MSP-Podcast.html) | Naturalistic reference | Academic access terms; check whether pretrained MSP estimators saw evaluation recordings |
| [IEMOCAP](https://sail.usc.edu/iemocap/iemocap_release.htm) | Secondary acted/improvised development control | Access conditions; do not call it spontaneous natural conversation |
| CREMA-D and MUStARD++ | Retrospective diagnostics only | Acted speech; cannot substitute independent naturalistic evidence |

## Required dataset/provenance manifest

Each utterance must have corpus, version, stable scene ID, speaker and session ID, split (train/development/test), path and SHA256 of audio, annotation scale and annotation type, annotator provenance, permissions and license reference. Reject duplicate IDs; disallow speaker overlap across splits within corpus, and detect duplicate audio across splits. Independently inspect cross-corpus overlap, training exposure and source permissions before declaring readiness.

Run `preflight_manifest.py` for structural checks; it cannot certify permission or label validity.

## Protocol gates: not yet researcher-approved

1. Freeze eligible extractor revisions, weights/hashes, dependencies, licenses, acoustic window length, hop, timestamp handling, hardware, preprocessor, abstention reasons and aggregation using development data only.
2. Compare all candidates on the **same observations**; record errors and abstentions, never impute them or silently exclude them. Retain individual coverage and paired common-coverage counts.
3. Report per-corpus and split: Spearman with independently annotated arousal, number of utterances/speakers, coverage, confidence intervals resampling by speaker (or dialogue/session if dependent), and paired delta on shared emitted observations. Optional CCC/MAE only with justified common scale.
4. Transfer to a held-out corpus without selection/calibration on its test partition. Document potential training overlap; do not claim zero-shot when unverified.
5. Run a separate **causal, streaming-only** evaluation where all candidates receive only audio available through each timestamp. Report end-to-end latency and acoustic look-ahead. Never equate full-utterance pooling with incremental windows.
6. Preserve negative findings. Historical Spearman thresholds labeled “governance-grade” are not permission-to-intervene validity thresholds.
7. Apply approved PhDude methods and authorization before model/corpus execution. Do not edit Vault research records directly.

## Source of evidence and limitations

- SDK: `docs/validation/charter-validation-protocol.md`, `experiments/phase2-arousal-benchmark/README.md`.
- Vault: `manuscript/methods.md` and `manuscript/results.md` (recorded candidate engineering and CREMA-D findings).
- Earlier 21-session public-voice pilot: seven arms, zero task predicate success in each arm; this does not establish an effect of PGSO.
- No new human acoustic benchmark, new model evaluation or live user study is claimed here.

**Publication separation:** E0 estimates signal validity; E1 establishes mechanism conformance; later independent judgments and controls examine intervention appropriateness and task utility.
