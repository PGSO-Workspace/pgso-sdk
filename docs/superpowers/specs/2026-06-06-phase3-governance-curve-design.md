# Phase 3 — Signal-Quality × Governance-Correctness Curve (Design Spec)

**Project:** PGSO — Prosody-Gated State Orchestrator
**Phase:** 3 (final benchmark — measurement, not demo)
**Date:** 6 June 2026
**Author:** Lucio (PI) · design by pairing
**Status:** DESIGN — pre-implementation. Reuses real `pgso-core` unchanged.

---

## 0. One-paragraph framing

PGSO is a neuro-symbolic system: **probabilistic perception** (a paralinguistic
arousal signal) feeding **deterministic symbolic action** (tool-catalog
governance). Phase 1 established the real eGeMAPS arousal signal is *weak* on
acted speech (Spearman ρ = 0.206, inherent). The open question is **not** "does
one signal pass a threshold" — it is: **across a gradient of signal quality,
where does the deterministic mechanism stop governing correctly, and does PGSO's
non-punitive design (friction, not blocking) make weak signals tolerable?** This
experiment answers that with two separate measurement layers and reports a
*curve*, never a pass/fail.

This is a **measurement experiment** living in `experiments/phase3-governance-curve/`,
alongside phase0 / phase1-arousal-mustard / phase2-arousal-benchmark. It is NOT
the live MVP/harness demo and builds no live agent. It reuses the real SDK and
the datasets already in `data/`.

Three published methodologies frame it (each to be verified before citing):
- **ParaLBench** (IEEE TAC 2024): evaluate multiple signal models under one
  framework; the honest metric is *cross-corpus* generalization, never single-corpus.
- **RoTBench** (EMNLP 2024): evaluate robustness as a *curve across noise levels*,
  not a binary pass/fail.
- **Neuro-symbolic V&V surveys:** separate the probabilistic-perception evaluation
  from the deterministic-action evaluation; the guarantee lives in the verifiable action.

---

## 1. Two layers — kept strictly separate

| | Layer A — Perception | Layer B — Action (**headline**) |
|---|---|---|
| Style | ParaLBench | RoTBench |
| Question | How well does each arousal signal track human arousal? | At each *controlled* signal quality, what fraction of governance decisions is correct, and at what cost? |
| Input | Real signal candidates vs human labels | Signals of **known, injected** quality (no extractor noise) |
| Output | per-candidate × per-corpus ρ + CI; Δρ (learned vs DSP); field-range position | the degradation **curve**; FP/FN; non-punitive cost curve; G2-holds-at-all-levels |
| Data need | Real audio / pre-extracted features (some gated) | **None gated** — injects noise into labels |
| Priority | Secondary ("as data allows") | **First** |

Layer B is built and run first: it needs no gated data and produces the headline
result. Layer A runs after and reports exactly which candidates loaded.

---

## 2. Real components reused unchanged (no reimplemented governance)

From `crates/pgso-core` (verified this session):
- `SignalReading { value: f32 (~[0,1]), axis: Axis, confidence: f32, timestamp_ms: u64 }`.
- `DecisionEngine::process(&reading) -> Option<EngineOutput>`: per-axis 3-layer
  adaptive baseline (population prior → cumulative-mean warm-up → EMA), measure-
  **then**-update, `deviation = |value − baseline|`, **hysteresis** (N consecutive
  above-threshold windows), G4 abstain below confidence threshold. **Level-triggered.**
- `RuleEngine::evaluate(&output) -> Vec<ScopeDecision>`: matches `RulePredicate`
  (axis + min_deviation + min_confidence); **G2+G3**: a `Prune` of a protected
  tool is downgraded to `RequireStepUp`, never emitted as `Prune`, never dropped.
- `Action`: `Allow | RequireStepUp(ToolId) | Prune(ToolId) | InjectDirective(String)`.
- `pgso-actuator-local::LocalActuator`: independent G2 backstop (never prunes
  protected), restores to nominal on `Allow`, idempotent apply.

**Critical mechanism fact driving Layer B:** the engine governs on *deviation
from an adaptive per-speaker baseline*, sustained over a hysteresis window — **not**
on absolute arousal. The Layer B construction and the correctness definition are
therefore expressed at the *sequence/deviation* level, not as "is this scalar high".

The experiment adds only: (a) Python for signal construction / stats / plots, and
(b) a thin Rust **replay/driver** that builds reading sequences and feeds them to
the real `DecisionEngine`+`RuleEngine`+`LocalActuator`. **No governance logic is
reimplemented.**

---

## 3. Layer B — construction (the headline)

### 3.1 Ground truth
MUStARD++ `human_arousal` (1–9 Likert) + `speaker`, read from the existing
`experiments/phase1-arousal-mustard/out/manifest.csv` (N ≈ 396; label-only — no
audio or extractor-abstention dependency). Normalize `a_true = (arousal − 1)/8 ∈ [0,1]`.
Per-speaker baseline `base[spk]` = mean `a_true` for that speaker when the speaker
has ≥ 5 utterances, else the corpus mean. `population_prior` = corpus mean `a_true`.

### 3.2 Per-utterance drive — "calm prior + sustained test segment"
For each utterance, build a reading sequence:
1. **K = `warmup_readings` (5) calm warm-up readings** at value `base[spk]`,
   confidence 0.9 — the engine's adaptive baseline converges to `base[spk]`.
2. **H = `hysteresis_window` (3) test readings** at the utterance's controlled-
   arousal value `s_i`, confidence 0.9.

All readings are `Axis::Arousal`. A **fresh** real `DecisionEngine` +
`RuleEngine` + `LocalActuator` per utterance (no cross-utterance contamination,
mirroring Phase-1's fresh-extractor-per-utterance). Record per utterance:
`intervened ∈ {0,1}` (any rule fired and mutated the served catalog), the multiset
of emitted `Action`s, the served catalog, and the G2 assertion outcome.

### 3.3 Frozen config + pre-registered operating point
Use the shipped governance-demo `EngineConfig` **unchanged**:
`confidence_threshold = 0.5`, `hysteresis_window = 3`, `warmup_readings = 5`,
`ema_alpha = 0.1`, `population_prior = corpus mean a_true`.

The **one** operating-point choice: MUStARD arousal is range-restricted
(sd ≈ 0.14 in normalized space), so the shipped `deviation_threshold = 0.3` would
make almost nothing trigger (degenerate curve). We **pre-register**
`deviation_threshold` = the **70th percentile of `|a_true − base[spk]|`** over the
corpus → oracle intervene-rate ≈ **30%** (the top-tertile most-deviated
utterances). This is a *frozen operating point*, identical across all five levels
and both rule configs. It is **not** correctness tuning: it sets the base rate
before any decision is scored and is never revisited.

Confidence is held fixed at 0.9 — this experiment isolates the **signal-value**
quality axis. Confidence/abstention degradation is a *separate* axis, declared
out of scope (a noted follow-up).

### 3.4 Controlled-quality signal — hit target ρ, then verify
Normal-scores Gaussian copula, holding the marginal distribution fixed across
levels so only the *coupling to truth* changes:
1. Inverse-normal scores `z_a = Φ⁻¹((avg_rank(a_true) − 0.5)/n)` (tie-aware ranks).
2. `z_s = λ·z_a + √(1 − λ²)·ε`, with `ε ~ N(0,1)` independent (seeded).
3. Back-map `z_s` through the **empirical quantile function of `a_true`** → `s_i`
   has the same marginal as `a_true`; only ρ to truth varies.

Calibration: analytic start `λ = 2·sin(πρ/6)` (bivariate-normal Spearman⇄Pearson),
then **seeded bisection on λ** until the achieved *tie-aware* Spearman (computed by
the same `eval_stats.spearman` used for reporting) matches the target within 0.01.

**Hard gate (per the task's "verify achieved ρ before using"):** assert and print
the achieved ρ (mean ± sd over seeds) for every level before any decision is
driven. A level whose achieved ρ is off-target by > 0.01 halts the run.

**Seeds:** 20 per level (seeds 0–19, fixed). RNG lives only in Python signal
construction (allowed); the Rust core is deterministic given its input readings.

### 3.5 Pre-registered levels (FIXED — no post-hoc change)
| Level | Target Spearman ρ to truth | Role |
|---|---|---|
| **L0** | 1.00 | Oracle — sanity (must be 100% correct) |
| **L1** | 0.50 | Strong |
| **L2** | 0.35 | Moderate |
| **L3** | 0.20 | **Weak — where the real DSP signal sits** |
| **L4** | 0.00 | Noise — sanity (must collapse to chance) |

### 3.6 Governance-correctness definition — oracle defines intent
At **L0** the signal equals `a_true` exactly, so the engine's own decision *is* the
ground-truth intent. L0 correctness = 100% **by construction** (a wiring sanity
check). For L1–L4, a decision is **correct** iff it matches that utterance's L0
(oracle) decision. Per level we report:
- **governance-correctness rate** = fraction matching oracle;
- **false-positive rate** = intervened when oracle said don't;
- **false-negative rate** = allowed when oracle said intervene;
each with bootstrap 95% CI over utterances, and mean ± sd over the 20 seeds.

**Chance baseline (printed, makes "collapse to chance" verifiable):** with oracle
intervene-rate `p` and level-`L` intervene-rate `q`, expected agreement under
independence ≈ `p·q + (1−p)·(1−q)`. With the fixed marginal, `q ≈ p ≈ 0.30`, so
L4 chance ≈ `p² + (1−p)²` ≈ 0.58. We print `p`, `q`, and the chance value per level;
L4 correctness should land near it, not at an asserted 0.5.

### 3.7 G2 holds at every level
The Rust driver **asserts** at runtime, across all levels (incl. L4), both rule
configs, and all seeds: no emitted `Action` is `Prune(protected)` and the
protected tool is always present in the served catalog. To demonstrate this
*empirically under noise*, the punitive config (§3.8) includes an **adversarial
rule that explicitly targets the protected tool** — and we observe the G2/G3
downgrade hold. The formal proof remains `prop_allowlist_never_pruned`
(unchanged); Layer B's assertion is the empirical confirmation across the actual run.

### 3.8 Non-punitive cost — two configs on identical signals
Two **pre-registered** rule sets, driven on the *same* signals/levels:
- **Non-punitive (primary):** `RequireStepUp(close_sale)` + `InjectDirective`.
  Cost of a false positive = **one extra confirmation**.
- **Punitive counterfactual:** `Prune(close_sale)` (+ the adversarial
  protected-targeting rule for the G2 demo). Cost of a false positive = **one
  blocked capability**.

Both configs use an **identical `RulePredicate`** (same axis, `min_deviation`,
`min_confidence`) and differ **only in the emitted `Action`s** → triggering is
provably identical, so the **correctness curve is config-independent**; only the
*cost per error* differs. We report, per level:
1. **non-punitive cost** = E[step-up confirmations / interaction] (counts direct
   `RequireStepUp` and any G2-downgraded prunes);
2. **punitive counterfactual** = E[blocked capabilities / interaction] (prunes of
   unprotected tools);
3. the **gap** = the non-punitive dividend, with emphasis on the FP-driven cost
   (extra confirmations on utterances where the oracle said "don't intervene").

**Honest read:** identify the ρ-level at which the FP-driven extra-confirmation
cost stays modest even where raw correctness is only moderate, and locate L3
(real-signal quality) on it. One pre-registered **reference comfort line**
(≤ 0.5 extra confirmations / interaction) is shown purely as a discussion aid —
**explicitly NOT a verdict gate** (no invented pass/fail).

---

## 4. Layer A — perception benchmark (secondary; graceful-degrade)

Three candidates; **per-corpus, never pooled**; report exactly which loaded.

1. **eGeMAPS 2-feature (reference floor):** reuse `out/predicted.csv`
   `pred_arousal_wmean` vs `human_arousal` on MUStARD++. Recompute ρ + permutation
   (5000) + bootstrap CI + per-speaker breakdown + F0/abstention diagnostics with
   the shared `eval_stats`. Commercial-clean.
2. **eGeMAPS FULL (88-param):** openSMILE `eGeMAPSv02` over MUStARD++ wavs
   (`out/wav/*.wav`) **and** EMOVOME's pre-extracted
   `data/emovome/Audios/features_eGeMAPSv02.csv` → the **cross-corpus** candidate
   (acted + spontaneous — the first time the long-blocked spontaneous point is
   reachable). Skips gracefully and reports "did not run" if `opensmile` is absent.
   **Arousal head (pre-registered default — the §C sub-decision, accepted):**
   - *Primary:* a **transparent linear head trained cross-corpus, leakage-free** —
     fit eGeMAPS-88 → arousal on EMOVOME, test on MUStARD++, and vice-versa;
     coefficients reported (auditable). This is the ParaLBench cross-corpus-honest
     setup and answers "does the richer auditable DSP set generalize across corpora".
   - *Sensitivity:* a single a-priori canonical arousal feature
     (`loudness_sma3_amean`) correlated directly — a fully label-free auditable floor.
3. **Distilled transformer (lightweight learned):** `audeering/wav2small`
   (reuse the Phase-2 adapter `wav2small_model.py`) on MUStARD++ wavs → arousal
   directly. MUStARD-only (EMOVOME has no on-disk audio). Skips gracefully if the
   model/torch won't load. **CC BY-NC-SA → research-only reference ceiling, NOT
   shippable** (carried from Phase-2).

**Decision-relevant Δρ:** paired-bootstrap `ρ(transformer) − ρ(eGeMAPS-2feat)` and
`ρ(transformer) − ρ(eGeMAPS-FULL)` on MUStARD++ (where all run), with 95% CI on the
difference — "does the learned signal beat the heuristic enough to justify it?"
(Cross-corpus Δρ is not possible — the transformer is MUStARD-only — and that is
reported as such.)

**EMOVOME human-arousal mapping:** primary = mean of the numeric SAM arousal
ratings (`arousal_E_SAM`, `arousal_NE1/2/3_SAM`); sensitivity = `arousal_C`
categorical {low, neutral, high} → {0, 1, 2}. Exact SAM column ranges verified at
build time; aggregation rule frozen here.

**Field-range position — NOT a pass/fail cutoff** (per the task): place each
candidate on a **literature-verified** SER arousal range (indicative: heuristic
DSP ≈ 0.1–0.3 · full eGeMAPS+head ≈ 0.3–0.5 · large learned ≈ 0.5–0.75; ParaLBench
+ MSP-Podcast SOTA — verified before citing). Phase-1/2 bands (≥0.35 / 0.20–0.35 /
<0.20) shown only for continuity, not as verdicts.

If a needed corpus or dependency is absent → **STOP and report** for that candidate;
never fabricate or substitute. Layer A being single-corpus for a candidate is
reported as a precondition, not cross-corpus generalization (ParaLBench caveat).

---

## 5. Architecture & file layout

New `experiments/phase3-governance-curve/`, mirroring phase1/phase2 conventions
(Python orchestration + a Rust crate depending on the real path crates):

```
experiments/phase3-governance-curve/
  config.py              # ALL pre-registration; print_header() at top of every run
  eval_stats.py          # reused verbatim from phase2 (validated Spearman/perm/bootstrap/paired-Δ)
  layerb_build_signals.py# copula construction + achieved-ρ verify gate -> layerb_signals.csv
  drive/                 # Rust crate `phase3-drive`
    Cargo.toml           #   deps: pgso-core, pgso-actuator-local (path), anyhow, csv, serde
    src/main.rs          #   reads layerb_signals.csv; builds calm-prior+test sequences;
                         #   drives REAL DecisionEngine+RuleEngine+LocalActuator for both
                         #   rule configs; asserts G2; writes layerb_decisions.csv
  layerb_analyze.py      # oracle=L0 intent; correctness/FP/FN + CI; chance baseline;
                         #   non-punitive vs punitive cost curves; G2 confirmation; figures
  layera_egemaps2.py     # reuse predicted.csv -> ρ/CI/per-speaker/diagnostics (MUStARD)
  layera_egemaps_full.py # openSMILE (MUStARD wavs) + EMOVOME features; head; cross-corpus
  layera_transformer.py  # wav2small on MUStARD wavs (graceful skip)
  layera_compare.py      # Δρ paired bootstrap; candidate×corpus ρ table; which-ran report
  run_all.py             # prints pre-registration, runs Layer B first, then Layer A
  out/                   # signals.csv, decisions.csv, results/*.png, tables
docs/phase3-governance-curve-report.md   # final report (Phase-1 report style) + honest paragraph
```

`config.py` holds and prints: the five levels + target ρ; the λ map; seeds;
permutation/bootstrap params (5000 / 2000 / seed 42, matching Phase 1/2); the
frozen `EngineConfig` + the 30% operating point; the correctness definition; both
rule configs; the Layer A candidate registry + field-range bands; the EMOVOME
aggregation rule; all paths.

The Rust `drive/` crate mirrors phase1 `extract/`: a workspace-external binary
depending on the published path crates, doing only CSV I/O + sequence replay; all
governance comes from `pgso-core`/`pgso-actuator-local`.

---

## 6. Deliverables

- **Layer B:** the governance-correctness-vs-signal-quality **curve** (headline
  figure); FP/FN breakdown per level; the non-punitive-cost curve + punitive
  counterfactual; the G2-holds-at-all-levels confirmation.
- **Layer A:** per-candidate × per-corpus ρ table with CIs; the pairwise Δρ
  (learned vs DSP) with CI; each candidate's literature-verified field-range position.
- **One honest paragraph:** at what signal quality PGSO governs acceptably given
  the non-punitive reaction, and where each real signal candidate falls relative to
  that — i.e., is the library ready, and with which signal.

---

## 7. Integrity (carried from Phase 1, enforced)

- Pre-register & print L0–L4, bands, and the operating point **before** any run.
- Achieved ρ **verified** (asserted + printed) before any decision is driven.
- **Corpora never pooled.**
- Abstentions / skipped candidates **dropped and reported**, never imputed.
- **No post-hoc level/threshold changes; no tuning to make a curve look better**,
  either direction. Report whatever the curves show (graceful degradation is a
  strong result; collapse is an honest finding).
- Real `pgso-core` driven **unchanged** — no reimplemented governance.
- **STOP and report** if a corpus or dependency is absent.

---

## 8. Working-rule compliance (pair-programming feedback)

- TDD ping-pong on the Rust `drive` crate: tests/signatures first, sign-off, then
  logic. No `.unwrap()`/`.expect()` in non-test code; `?` with typed errors
  (`anyhow` at the binary boundary, as phase1 `extract` does). `clippy -D warnings`
  clean. Incremental, ≤ 2 files/change without sign-off. The SDK crates are not
  modified.
- 3-failed-attempt hard stop on any check loop → escalate.

---

*End of design spec.*
